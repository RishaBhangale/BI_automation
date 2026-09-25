"""
test_business_scenarios.py — Business-logic scenario tests for Dashboard validation.

These tests simulate realistic end-user business workflows on the Power BI report.
Each test applies the default filter state (no custom slicer interactions),
validates one business rule against the database, then restores the report state.

PERFORMANCE NOTE:
    The browser session is shared across the entire test run (session-scoped).
    SSO login happens only ONCE. All 15 test cases reuse the same open tab.

To run:
    pytest tests/dashboard/test_business_scenarios.py \\
        --dashboard-config=dashboard_configs/demo_detection.yaml -v
"""

import logging
import math
import os

import pandas as pd
import pytest

from utils.db_utils import fetch_scalar, fetch_db_data
from utils.validation_utils import (
    compare_single_value,
    compare_datasets,
    parse_pbi_number,
)
from utils.sql_template_engine import (
    build_query,
    build_table_query,
    build_dax_kpi_query,
    build_dax_table_query,
)

log = logging.getLogger("dashboard_methods")


class SlicerInteractionError(Exception):
    """Raised when a slicer cannot be applied during test setup.

    Distinguishes UI/functional failures from data-mismatch failures
    so the HTML report gives a clear, actionable failure reason.
    """
    pass

# ---------------------------------------------------------------------------
# Load test cases from Excel at collection time
# ---------------------------------------------------------------------------
_default_excel = os.path.join(os.path.dirname(__file__), "../../test_data/business_scenarios.xlsx")
EXCEL_PATH = os.environ.get("BI_TEST_EXCEL_PATH", _default_excel)
if not os.path.exists(EXCEL_PATH):
    EXCEL_PATH = _default_excel
SQL_DIR    = os.path.join(os.path.dirname(__file__), "../../test_data/sql_queries")

try:
    _df        = pd.read_excel(EXCEL_PATH)
    # Replace pandas NaN with Python None so downstream guards work correctly
    test_cases = _df.where(pd.notnull(_df), None).to_dict("records")
except Exception as _exc:
    log.error(f"Failed to load test cases from Excel: {_exc}")
    test_cases = []


def _clean(val) -> str | None:
    """Return val as a stripped string, or None if it is empty / NaN / 'nan'.

    Guards against three different representations of 'empty' that can appear
    when reading Excel files with pandas:
      - Python  None        (from our pd.notnull replacement)
      - float   nan         (if pd.notnull replacement was skipped)
      - string  'nan'       (if the value was coerced to a string elsewhere)
    """
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if s.lower() == "nan" or s == "":
        return None
    return s


def _id_func(tc):
    t_id = str(tc.get("Test ID", "unknown"))
    s_name = str(tc.get("Scenario Name", "Unnamed"))
    return f"{t_id} - {s_name}"


# ---------------------------------------------------------------------------
# Generic data-driven test
# ---------------------------------------------------------------------------

@pytest.mark.dashboard
@pytest.mark.parametrize("tc", test_cases, ids=_id_func)
def test_business_scenario(dashboard_page, db_engine, dashboard_config, pbi_api_client, tc):
    """
    Data-Driven Business Scenario Test (KPI Scalar + Multi-Row Table Validation + Tier 2 DAX Fallback).

    Each row in business_scenarios.xlsx becomes one test case. The test:
      1. Navigates to the Summary page and clears default slicers.
      2. Applies Slicer 1..6 filters.
      3. Branches by 'Visual Type':
         • 'KPI' (default): Reads scalar KPI card via DOM (Tier 1) with automatic
           DAX REST API fallback (Tier 2 — Item 12) if DOM scraping fails/obfuscated,
           then compares against SQL scalar result.
         • 'TABLE' (Item 11): Extracts full table/matrix rows via DOM grid virtual-scrolling
           (Tier 1) or PBI REST API DAX SUMMARIZECOLUMNS (Tier 2), then compares
           row-by-row against the source database DataFrame using compare_datasets().
      4. Resets all applied slicers in teardown (always runs, even on failure).
    """
    test_id       = _clean(tc.get("Test ID")) or "UNKNOWN"
    scenario_name = _clean(tc.get("Scenario Name")) or "Unnamed"
    slicers = []
    for i in range(1, 7):
        s_name = _clean(tc.get(f"Slicer {i} Name"))
        s_value = _clean(tc.get(f"Slicer {i} Value"))
        if s_name and s_value:
            slicers.append((s_name, s_value))

    kpi_to_read       = _clean(tc.get("KPI to Read"))
    sql_file          = _clean(tc.get("SQL File Name"))
    visual_type       = (_clean(tc.get("Visual Type")) or "KPI").upper()
    table_visual_name = _clean(tc.get("Table Visual Name")) or kpi_to_read
    join_keys_raw     = _clean(tc.get("Join Keys")) or ""
    compare_cols_raw  = _clean(tc.get("Compare Columns")) or ""
    custom_dax        = _clean(tc.get("DAX Query"))

    log.info(f"--- Starting {test_id}: {scenario_name} [Visual Type: {visual_type}] ---")

    # ── Step 1 ─────────────────────────────────────────────────────────────────
    log.info("STEP1_START|Navigate to Summary page and confirm dashboard is loaded")
    dashboard_page.switch_to_page("Summary")
    log.info("STEP1_END")

    # Track slicers so we can reset them in teardown
    applied_slicers: list[str] = []

    # We separate the result of the comparison so the assert runs AFTER
    # teardown (Step 7) has already completed cleanly.
    _test_passed  = True
    _fail_message = ""

    try:
        # ── Step 1.5: Clear ALL existing filters ─────────────────────────────
        log.info("STEP1.5_START|Clear all default slicers to start from a clean state")
        dashboard_page.clear_all_slicers()
        log.info("STEP1.5_END")

        # ── Step 2 ─────────────────────────────────────────────────────────────
        for idx, (s_name, s_value) in enumerate(slicers, start=1):
            log.info(f"STEP2.{idx}_START|Apply Slicer: {s_name} = {s_value}")
            try:
                dashboard_page.reset_slicer(s_name)
            except Exception:
                pass
            try:
                dashboard_page.set_slicer(s_name, s_value)
            except Exception as se:
                raise SlicerInteractionError(f"Could not apply slicer '{s_name}' with value '{s_value}': {se}") from se
            applied_slicers.append(s_name)
            log.info(f"STEP2.{idx}_END")

        # ═══════════════════════════════════════════════════════════════════════
        # BRANCH A — ITEM 11: MULTI-ROW TABLE / MATRIX VISUAL VALIDATION
        # ═══════════════════════════════════════════════════════════════════════
        if visual_type in ("TABLE", "MATRIX"):
            if not table_visual_name:
                pytest.fail(f"{test_id}: 'Table Visual Name' (or 'KPI to Read') is required when Visual Type is TABLE")

            join_keys    = [k.strip() for k in join_keys_raw.split(",") if k.strip()]
            compare_cols = [c.strip() for c in compare_cols_raw.split(",") if c.strip()]

            # ── Step 4 (TABLE): Extract rows via Tier 2 DAX (if active) or Tier 1 DOM Scroll ──
            log.info(f"STEP4_START|Extract table rows from visual: {table_visual_name}")
            dashboard_data: list[dict] = []

            if pbi_api_client is not None:
                # Tier 2 DAX extraction is faster and captures 100% of rows without virtual scroll limits
                try:
                    dax_q = custom_dax or build_dax_table_query(
                        table_visual_name, slicers, join_keys, compare_cols
                    )
                    log.info(f"[Tier 2 DAX] Querying semantic model directly for table '{table_visual_name}'")
                    dax_df = pbi_api_client.execute_dax(dax_q)
                    if not dax_df.empty:
                        dashboard_data = dax_df.to_dict("records")
                        log.info(f"[Tier 2 DAX] Retrieved {len(dashboard_data)} rows from semantic model")
                except Exception as dax_exc:
                    log.warning(f"[Tier 2 DAX] Table query failed ({dax_exc}) — falling back to Tier 1 DOM scroll")

            if not dashboard_data:
                # Tier 1 DOM extraction with virtualized scroll loop
                try:
                    dashboard_data = dashboard_page.extract_table_data(table_visual_name)
                    log.info(f"[Tier 1 DOM + Scroll] Extracted {len(dashboard_data)} rows from '{table_visual_name}'")
                except Exception as dom_exc:
                    log.warning(f"[Tier 1 DOM] Table extraction failed for '{table_visual_name}': {dom_exc}")
                    if pbi_api_client is None:
                        fallback_dax = custom_dax or build_dax_table_query(
                            table_visual_name, slicers, join_keys, compare_cols
                        )
                        log.info(
                            f"[Tier 2 Fallback Standby] Prepared DAX query (awaiting PBI_CLIENT_SECRET): {fallback_dax}"
                        )
                    raise

            log.info("STEP4_END")

            # ── Step 5 (TABLE): Fetch expected dataset from source DB ──────────
            log.info("STEP5_START|Fetch expected table dataset from source database")
            if db_engine is None:
                log.info("No DB engine available — skipping DB comparison (STEP5)")
                log.info("STEP5_END")
                pytest.skip(
                    "No source database configured or reachable. "
                    "Ensure DB credentials are set and VPN is active."
                )

            if sql_file:
                sql_path = os.path.join(SQL_DIR, sql_file)
                if not os.path.exists(sql_path):
                    pytest.fail(f"{test_id}: SQL file not found — {sql_path}")
                with open(sql_path, "r") as fh:
                    sql_query = fh.read().strip()
                log.info(f"Using SQL file: {sql_file}")
            else:
                try:
                    sql_query = build_table_query(
                        table_visual_name, slicers, join_keys, compare_cols
                    )
                except ValueError as ve:
                    pytest.fail(f"{test_id}: Table SQL auto-generation failed — {ve}")
                log.info(f"Auto-generated Table SQL: {sql_query}")

            source_df = fetch_db_data(db_engine, sql_query)
            log.info(f"Database returned {len(source_df)} rows, columns={list(source_df.columns)}")

            # If join_keys / compare_cols were omitted in Excel (using TABLE_COLUMN_MAP),
            # infer them automatically from source_df columns (first col = join key, rest = metrics)
            if not join_keys and len(source_df.columns) >= 1:
                join_keys = [str(source_df.columns[0])]
            if not compare_cols and len(source_df.columns) >= 2:
                compare_cols = [str(c) for c in source_df.columns[1:]]

            log.info("STEP5_END")

            # ── Step 6 (TABLE): Row-by-row dataset comparison ──────────────────
            log.info("STEP6_START|Compare dashboard table rows against database result set")
            passed, detail = compare_datasets(
                dashboard_data = dashboard_data,
                source_df      = source_df,
                join_keys      = join_keys,
                compare_cols   = compare_cols,
                tolerance      = 0.01,
            )
            status = "PASS" if passed else "FAIL"
            log.info(f"[{status}] {detail}")
            if not passed:
                log.error(f"FAIL — {detail}")

            _test_passed  = passed
            _fail_message = f"{test_id} FAILED: {detail}"
            log.info("STEP6_END")

        # ═══════════════════════════════════════════════════════════════════════
        # BRANCH B — SCALAR KPI VALIDATION + ITEM 12 TIER 2 DAX FALLBACK
        # ═══════════════════════════════════════════════════════════════════════
        else:
            # ── Step 4 (KPI): Read KPI via Tier 1 DOM with Tier 2 DAX Fallback ──
            log.info(f"STEP4_START|Read KPI card: {kpi_to_read}")
            dashboard_raw = ""
            dom_error: Exception | None = None

            try:
                dashboard_raw = dashboard_page.extract_card_value(kpi_to_read)
            except Exception as dom_exc:
                dom_error = dom_exc
                log.warning(
                    f"[Tier 1 DOM] Could not scrape KPI '{kpi_to_read}' from DOM: {dom_exc}"
                )

            # Item 12: Trigger Tier 2 DAX Fallback if DOM returned empty or unparseable text
            if parse_pbi_number(dashboard_raw) is None:
                dax_query = custom_dax or build_dax_kpi_query(kpi_to_read or "", slicers)
                if pbi_api_client is not None:
                    log.warning(
                        f"DOM scrape returned '{dashboard_raw}' for '{kpi_to_read}' "
                        f"— triggering Tier 2 DAX fallback via Power BI REST API"
                    )
                    try:
                        dax_df = pbi_api_client.execute_dax(dax_query)
                        if not dax_df.empty:
                            dashboard_raw = str(dax_df.iloc[0, 0])
                            log.info(f"[Tier 2 DAX Fallback] Retrieved KPI value: '{dashboard_raw}'")
                            dom_error = None
                        else:
                            log.warning("[Tier 2 DAX Fallback] Query returned 0 rows")
                    except Exception as dax_exc:
                        log.warning(f"[Tier 2 DAX Fallback] Execution failed: {dax_exc}")
                else:
                    log.info(
                        f"[Tier 2 DAX Fallback Standby] DOM returned '{dashboard_raw}'. "
                        f"Prepared fallback DAX query (awaiting Azure AD credentials): {dax_query}"
                    )
                    if dom_error is not None:
                        raise dom_error

            log.info(f"Dashboard KPI value: '{dashboard_raw}'")
            log.info("STEP4_END")

            # ── Step 5 (KPI): Fetch expected scalar from source database ───────
            log.info("STEP5_START|Fetch expected value from source database")

            if db_engine is None:
                log.info("No DB engine available — skipping DB comparison (STEP5)")
                log.info("STEP5_END")
                pytest.skip(
                    "No source database configured or reachable. "
                    "Ensure DB credentials are set and VPN is active."
                )

            # ── SQL resolution: file override OR auto-generate ─────────────────
            if sql_file:
                sql_path = os.path.join(SQL_DIR, sql_file)
                if not os.path.exists(sql_path):
                    pytest.fail(f"{test_id}: SQL file not found — {sql_path}")
                with open(sql_path, "r") as fh:
                    sql_query = fh.read().strip()
                log.info(f"Using SQL file: {sql_file}")
            else:
                if not kpi_to_read:
                    pytest.fail(f"{test_id}: 'KPI to Read' is empty — cannot auto-generate SQL")
                try:
                    sql_query = build_query(kpi_to_read, slicers)
                except ValueError as ve:
                    pytest.fail(f"{test_id}: SQL auto-generation failed — {ve}")
                log.info(f"Auto-generated SQL: {sql_query}")

            source_value = fetch_scalar(db_engine, sql_query)
            log.info(f"Database result: {source_value}")
            log.info("STEP5_END")

            # ── Step 6 (KPI): Compare scalar values ────────────────────────────
            log.info("STEP6_START|Compare dashboard KPI value against database result")
            passed, detail = compare_single_value(
                dashboard_raw = dashboard_raw,
                source_value  = source_value,
                tolerance     = 0.01,
                label         = f"{kpi_to_read} ({scenario_name})",
            )
            status = "PASS" if passed else "FAIL"
            log.info(f"[{status}] {detail}")
            if not passed:
                log.error(f"FAIL — {detail}")

            # Store result — DO NOT assert here so that teardown (Step 7) is
            # always green and never incorrectly shown as the point of failure.
            _test_passed  = passed
            _fail_message = f"{test_id} FAILED: {detail}"
            log.info("STEP6_END")

    except SlicerInteractionError as e:
        _test_passed = False
        _fail_message = (
            f"{test_id} FAILED — Functional check: slicer could not be applied. "
            f"This is a UI interaction issue, not a data mismatch. Detail: {e}"
        )
        log.error(_fail_message)
    except Exception as e:
        _test_passed = False
        _fail_message = f"{test_id} FAILED during execution: {str(e)}"
        log.error(_fail_message)
    finally:
        # ── Step 7 (always runs) ───────────────────────────────────────────────
        log.info("STEP7_START|Teardown — reset applied slicers to restore default state")
        for slicer in applied_slicers:
            try:
                dashboard_page.reset_slicer(slicer)
                log.info(f"Reset slicer: {slicer}")
            except Exception as exc:
                log.warning(f"Could not reset slicer '{slicer}': {exc}")
        log.info("STEP7_END")

    # ── Final assertion (OUTSIDE finally block) ────────────────────────────────
    # By placing the assert here, Step 7 teardown always completes cleanly.
    # The test is only marked as FAILED after teardown has already finished.
    assert _test_passed, _fail_message
