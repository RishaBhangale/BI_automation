"""
dashboard_methods.py — High-level orchestration for BI dashboard validation.

These methods sit above the POM and utilities. They combine Playwright
extraction + data source fetching + validation into single reusable calls.

A test function calls one method here and gets back a result dict.
No test-level logic, no assertions — assertions live in the test files.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.engine import Engine

from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.validation_utils import compare_single_value, compare_datasets
from utils.db_utils import fetch_scalar, fetch_db_data
from utils.excel_data_utils import load_source_excel, load_source_csv, aggregate_column
from utils.logger import get_logger

log = get_logger("dashboard_methods")


def _make_result(visual_title: str, page: str, passed: bool, detail: str) -> dict:
    """Build a standardised result dict."""
    status = "PASS" if passed else "FAIL"
    log.info(f"[{status}] '{visual_title}' on page '{page}': {detail}")
    return {
        "visual_title": visual_title,
        "page":         page,
        "passed":       passed,
        "detail":       detail,
    }


# ── KPI Validation ─────────────────────────────────────────────────────────

def validate_kpi(
    dashboard_page: PBIDashboardPage,
    kpi_config: dict,
    db_engine: Optional[Engine] = None,
    excel_filepath: str = "",
    excel_sheet: str = "",
) -> dict:
    """
    Validate a single KPI card visual against its source data.

    Pulls the KPI value from the dashboard using Playwright, then compares
    it against the scalar result of a SQL query (or Excel aggregation).

    Args:
        dashboard_page: PBIDashboardPage POM instance (already opened and authenticated).
        kpi_config:     A single KPI entry dict from the dashboard YAML config.
                        Must contain at minimum: visual_title, page, tolerance.
                        Must contain either sql_query (for DB) or
                        excel_column + excel_agg (for Excel).
        db_engine:      SQLAlchemy engine. Required if sql_query is provided.
        excel_filepath: Path to the source Excel file. Required if excel_column is provided.
        excel_sheet:    Sheet name for the Excel file.

    Returns:
        Result dict with keys: visual_title, page, passed, detail.
    """
    visual_title = kpi_config.get("visual_title", "Unknown")
    page_name    = kpi_config.get("page", "")
    tolerance    = float(kpi_config.get("tolerance", 0.01))
    sql_query    = kpi_config.get("sql_query", "").strip()
    excel_column = kpi_config.get("excel_column", "").strip()
    excel_agg    = kpi_config.get("excel_agg", "sum").strip()

    try:
        # Step 1: Switch to the correct page if specified
        if page_name:
            dashboard_page.switch_to_page(page_name)

        # Step 2: Extract the KPI value from the dashboard
        dashboard_raw = dashboard_page.extract_card_value(visual_title)

        # Step 3: Fetch the source value
        source_value: Optional[float] = None

        if sql_query and db_engine:
            source_value = fetch_scalar(db_engine, sql_query)

        elif excel_column and excel_filepath:
            df = load_source_excel(excel_filepath, excel_sheet or "Sheet1")
            source_value = aggregate_column(df, excel_column, excel_agg)

        else:
            # No source DB or Excel configured.
            # extraction_only mode: just verify the value can be extracted and parsed.
            # This is useful for smoke tests on public dashboards where no source is available.
            from utils.validation_utils import parse_pbi_number
            try:
                parsed = parse_pbi_number(dashboard_raw)
                return _make_result(
                    visual_title, page_name, True,
                    f"EXTRACTED (no source comparison): dashboard='{dashboard_raw}' parsed={parsed:.4f}"
                )
            except Exception as parse_err:
                return _make_result(
                    visual_title, page_name, False,
                    f"EXTRACTED but could not parse value '{dashboard_raw}': {parse_err}"
                )

        # Step 4: Compare
        passed, detail = compare_single_value(
            dashboard_raw, source_value, tolerance, label=visual_title
        )
        return _make_result(visual_title, page_name, passed, detail)

    except Exception as e:
        log.error(f"validate_kpi failed for '{visual_title}': {e}")
        return _make_result(visual_title, page_name, False, f"Exception: {e}")


def validate_all_kpis(
    dashboard_page: PBIDashboardPage,
    config: dict,
    db_engine: Optional[Engine] = None,
) -> list[dict]:
    """
    Validate all KPI entries defined in the dashboard YAML config.

    Args:
        dashboard_page: PBIDashboardPage POM instance.
        config:         Parsed dashboard YAML config dict.
        db_engine:      SQLAlchemy engine (pass None if using Excel).

    Returns:
        List of result dicts, one per KPI entry.
    """
    from utils.config_loader import get_kpi_validations, get_excel_source
    kpis = get_kpi_validations(config)
    excel_path, excel_sheet = get_excel_source(config)

    if not kpis:
        log.warning("No KPI validations defined in config")
        return []

    results = []
    for kpi in kpis:
        result = validate_kpi(
            dashboard_page=dashboard_page,
            kpi_config=kpi,
            db_engine=db_engine,
            excel_filepath=excel_path,
            excel_sheet=excel_sheet,
        )
        results.append(result)

    total  = len(results)
    passed = sum(1 for r in results if r["passed"])
    log.info(f"KPI validation summary: {passed}/{total} passed")
    return results


# ── Table / Chart Validation ───────────────────────────────────────────────

def validate_table(
    dashboard_page: PBIDashboardPage,
    table_config: dict,
    db_engine: Optional[Engine] = None,
    excel_filepath: str = "",
    excel_sheet: str = "",
) -> dict:
    """
    Validate a table or chart visual's underlying data against the source.

    Extracts data from the visual using "Show as a table", then runs the
    equivalent SQL query and compares the two datasets.

    Args:
        dashboard_page: PBIDashboardPage POM instance.
        table_config:   A single table validation entry dict from the YAML config.
                        Must contain: visual_title, page, join_keys, compare_cols, tolerance.
                        Must contain either sql_query (DB) or excel source details.
        db_engine:      SQLAlchemy engine.
        excel_filepath: Path to the source Excel file (fallback).
        excel_sheet:    Sheet name.

    Returns:
        Result dict with keys: visual_title, page, passed, detail.
    """
    import pandas as pd

    visual_title = table_config.get("visual_title", "Unknown")
    page_name    = table_config.get("page", "")
    join_keys    = table_config.get("join_keys", [])
    compare_cols = table_config.get("compare_cols", [])
    tolerance    = float(table_config.get("tolerance", 0.01))
    sql_query    = table_config.get("sql_query", "").strip()

    try:
        # Step 1: Switch to the correct page
        if page_name:
            dashboard_page.switch_to_page(page_name)

        # Step 2: Extract table data from dashboard
        dashboard_data = dashboard_page.extract_table_data(visual_title)

        # Step 3: Fetch source dataset
        source_df = None

        if sql_query and db_engine:
            source_df = fetch_db_data(db_engine, sql_query)

        elif excel_filepath:
            source_df = load_source_excel(excel_filepath, excel_sheet or "Sheet1")

        else:
            return _make_result(
                visual_title, page_name, False,
                "No source configured: provide sql_query+db_engine or excel_filepath"
            )

        # Step 4: Compare datasets
        passed, detail = compare_datasets(
            dashboard_data, source_df, join_keys, compare_cols, tolerance
        )
        return _make_result(visual_title, page_name, passed, detail)

    except Exception as e:
        log.error(f"validate_table failed for '{visual_title}': {e}")
        return _make_result(visual_title, page_name, False, f"Exception: {e}")


def validate_all_tables(
    dashboard_page: PBIDashboardPage,
    config: dict,
    db_engine: Optional[Engine] = None,
) -> list[dict]:
    """
    Validate all table/chart entries defined in the dashboard YAML config.

    Args:
        dashboard_page: PBIDashboardPage POM instance.
        config:         Parsed dashboard YAML config dict.
        db_engine:      SQLAlchemy engine (pass None if using Excel).

    Returns:
        List of result dicts, one per table validation entry.
    """
    from utils.config_loader import get_table_validations, get_excel_source
    tables = get_table_validations(config)
    excel_path, excel_sheet = get_excel_source(config)

    if not tables:
        log.warning("No table validations defined in config")
        return []

    results = []
    for tbl in tables:
        result = validate_table(
            dashboard_page=dashboard_page,
            table_config=tbl,
            db_engine=db_engine,
            excel_filepath=excel_path,
            excel_sheet=excel_sheet,
        )
        results.append(result)

    total  = len(results)
    passed = sum(1 for r in results if r["passed"])
    log.info(f"Table validation summary: {passed}/{total} passed")
    return results
