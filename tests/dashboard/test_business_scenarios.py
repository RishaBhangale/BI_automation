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

from utils.db_utils import fetch_scalar
from utils.validation_utils import compare_single_value

log = logging.getLogger("dashboard_methods")

# ---------------------------------------------------------------------------
# Load test cases from Excel at collection time
# ---------------------------------------------------------------------------
EXCEL_PATH = os.path.join(os.path.dirname(__file__), "../../test_data/business_scenarios.xlsx")
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
    return str(tc.get("Test ID", "unknown"))


# ---------------------------------------------------------------------------
# Generic data-driven test
# ---------------------------------------------------------------------------

@pytest.mark.dashboard
@pytest.mark.parametrize("tc", test_cases, ids=_id_func)
def test_business_scenario(dashboard_page, db_engine, dashboard_config, tc):
    """
    Data-Driven Business Scenario Test.

    Each row in business_scenarios.xlsx becomes one test case.  The test:
      1. Navigates to the Summary page.
      2. Optionally resets + applies Slicer 1.
      3. Optionally resets + applies Slicer 2.
      4. Reads the target KPI card value from the dashboard.
      5. Fetches the expected value from the source database via the SQL file.
      6. Compares dashboard vs database (within tolerance).
      7. Resets all applied slicers in teardown (always runs, even on failure).
    """
    test_id       = _clean(tc.get("Test ID")) or "UNKNOWN"
    scenario_name = _clean(tc.get("Scenario Name")) or "Unnamed"
    slicer1_name  = _clean(tc.get("Slicer 1 Name"))
    slicer1_value = _clean(tc.get("Slicer 1 Value"))
    slicer2_name  = _clean(tc.get("Slicer 2 Name"))
    slicer2_value = _clean(tc.get("Slicer 2 Value"))
    kpi_to_read   = _clean(tc.get("KPI to Read"))
    sql_file      = _clean(tc.get("SQL File Name"))

    log.info(f"--- Starting {test_id}: {scenario_name} ---")

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
        # ── Step 2 ─────────────────────────────────────────────────────────────
        if slicer1_name and slicer1_value:
            log.info(f"STEP2_START|Apply Slicer: {slicer1_name} = {slicer1_value}")
            try:
                dashboard_page.reset_slicer(slicer1_name)
            except Exception:
                pass
            dashboard_page.set_slicer(slicer1_name, slicer1_value)
            applied_slicers.append(slicer1_name)
            log.info("STEP2_END")

        # ── Step 3 ─────────────────────────────────────────────────────────────
        if slicer2_name and slicer2_value:
            log.info(f"STEP3_START|Apply Slicer: {slicer2_name} = {slicer2_value}")
            try:
                dashboard_page.reset_slicer(slicer2_name)
            except Exception:
                pass
            dashboard_page.set_slicer(slicer2_name, slicer2_value)
            applied_slicers.append(slicer2_name)
            log.info("STEP3_END")

        # ── Step 4 ─────────────────────────────────────────────────────────────
        log.info(f"STEP4_START|Read KPI card: {kpi_to_read}")
        dashboard_raw = dashboard_page.extract_card_value(kpi_to_read)
        log.info(f"Dashboard KPI value: '{dashboard_raw}'")
        log.info("STEP4_END")

        # ── Step 5 ─────────────────────────────────────────────────────────────
        log.info("STEP5_START|Fetch expected value from source database")

        if not sql_file:
            pytest.fail(f"{test_id}: 'SQL File Name' is empty in business_scenarios.xlsx")

        sql_path = os.path.join(SQL_DIR, sql_file)
        if not os.path.exists(sql_path):
            pytest.fail(f"{test_id}: SQL file not found — {sql_path}")

        with open(sql_path, "r") as fh:
            sql_query = fh.read()

        if db_engine is None:
            log.info("No DB engine available — skipping DB comparison (STEP5)")
            log.info("STEP5_END")
            pytest.skip(
                "No source database configured or reachable. "
                "Ensure DB credentials are set and VPN is active."
            )

        source_value = fetch_scalar(db_engine, sql_query)
        log.info(f"Database result: {source_value}")
        log.info("STEP5_END")

        # ── Step 6 ─────────────────────────────────────────────────────────────
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
