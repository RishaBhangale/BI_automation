"""
test_stress_suite.py — Comprehensive stress test suite for the BI Validation Framework.

Exercises all KPI types, all major slicers, multi-slicer combinations, reset isolation,
grand-total checks, and error-handling paths. All rows driven from:
    test_data/stress_test_scenarios.xlsx

Error-handling rows (Group E) use the 'Expected Error' column:
    - "ValueError"            → build_query must raise ValueError (unknown KPI/slicer)
    - "SlicerInteractionError" → set_slicer must raise SlicerInteractionError (bad value)
    If the expected exception fires → test PASSES.
    If no exception fires       → test FAILS with a clear message.

To run:
    pytest tests/dashboard/test_stress_suite.py \
        --dashboard-config=dashboard_configs/demo_detection.yaml -v
"""

import logging
import math
import os

import pandas as pd
import pytest

from utils.db_utils import fetch_scalar
from utils.validation_utils import compare_single_value
from utils.sql_template_engine import build_query

log = logging.getLogger("dashboard_methods")


class SlicerInteractionError(Exception):
    """Raised when a slicer cannot be applied during test setup."""
    pass


# ---------------------------------------------------------------------------
# Load stress test cases from Excel at collection time
# ---------------------------------------------------------------------------
EXCEL_PATH = os.path.join(os.path.dirname(__file__), "../../test_data/stress_test_scenarios.xlsx")
SQL_DIR    = os.path.join(os.path.dirname(__file__), "../../test_data/sql_queries")

try:
    _df        = pd.read_excel(EXCEL_PATH)
    stress_cases = _df.where(pd.notnull(_df), None).to_dict("records")
except Exception as _exc:
    log.error(f"Failed to load stress test cases from Excel: {_exc}")
    stress_cases = []


def _clean(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if s.lower() == "nan" or s == "":
        return None
    return s


def _id_func(tc):
    t_id   = str(tc.get("Test ID", "unknown"))
    s_name = str(tc.get("Scenario Name", "Unnamed"))
    return f"{t_id} - {s_name}"


# ---------------------------------------------------------------------------
# Generic stress test
# ---------------------------------------------------------------------------

@pytest.mark.dashboard
@pytest.mark.parametrize("tc", stress_cases, ids=_id_func)
def test_stress_scenario(dashboard_page, db_engine, dashboard_config, tc):
    """
    Stress test scenario. Covers KPI coverage, slicer matrix, reset isolation,
    grand-total checks, and error-handling paths.
    """
    test_id        = _clean(tc.get("Test ID")) or "UNKNOWN"
    scenario_name  = _clean(tc.get("Scenario Name")) or "Unnamed"
    expected_error = _clean(tc.get("Expected Error"))  # e.g. "ValueError" or "SlicerInteractionError"

    slicers = []
    for i in range(1, 7):
        s_name  = _clean(tc.get(f"Slicer {i} Name"))
        s_value = _clean(tc.get(f"Slicer {i} Value"))
        if s_name and s_value:
            slicers.append((s_name, s_value))

    kpi_to_read = _clean(tc.get("KPI to Read"))
    sql_file    = _clean(tc.get("SQL File Name"))

    log.info(f"--- Starting {test_id}: {scenario_name} ---")

    # ── Step 1 ─────────────────────────────────────────────────────────────────
    log.info("STEP1_START|Navigate to Summary page and confirm dashboard is loaded")
    dashboard_page.switch_to_page("Summary")
    log.info("STEP1_END")

    applied_slicers: list[str] = []
    _test_passed  = True
    _fail_message = ""

    try:
        # ── Step 1.5: Clear ALL existing filters ─────────────────────────────
        log.info("STEP1.5_START|Clear all default slicers to start from a clean state")
        dashboard_page.clear_all_slicers()
        log.info("STEP1.5_END")

        # ── Error path: if expected_error is set, test the error condition ─────
        if expected_error:
            log.info(f"STEP2_START|Testing error condition — expecting {expected_error}")
            error_raised = None

            # Test ValueError from build_query (unknown KPI or unknown slicer)
            if expected_error == "ValueError":
                try:
                    build_query(kpi_to_read or "", slicers)
                    # If we get here, no error was raised — fail the test
                    _test_passed  = False
                    _fail_message = (
                        f"{test_id}: Expected ValueError from build_query but none was raised. "
                        f"KPI='{kpi_to_read}', slicers={slicers}"
                    )
                except ValueError as ve:
                    log.info(f"[PASS] Correct ValueError raised: {ve}")
                    _test_passed = True
                log.info("STEP2_END")

            elif expected_error == "SlicerInteractionError":
                # Apply slicers — expect the bad one to raise SlicerInteractionError
                caught = False
                for idx, (s_name, s_value) in enumerate(slicers, start=1):
                    try:
                        dashboard_page.set_slicer(s_name, s_value)
                        applied_slicers.append(s_name)
                    except Exception as se:
                        log.info(f"[PASS] Correct slicer error raised: {se}")
                        caught = True
                        break
                if not caught:
                    _test_passed  = False
                    _fail_message = (
                        f"{test_id}: Expected SlicerInteractionError but all slicers applied successfully. "
                        f"Slicer values may now be valid in the dashboard."
                    )
                else:
                    _test_passed = True
                log.info("STEP2_END")

            else:
                _test_passed  = False
                _fail_message = f"{test_id}: Unknown Expected Error type: {expected_error}"

        else:
            # ── Normal path ───────────────────────────────────────────────────
            # ── Step 2: Apply slicers ─────────────────────────────────────────
            for idx, (s_name, s_value) in enumerate(slicers, start=1):
                log.info(f"STEP2.{idx}_START|Apply Slicer: {s_name} = {s_value}")
                try:
                    dashboard_page.reset_slicer(s_name)
                except Exception:
                    pass
                try:
                    dashboard_page.set_slicer(s_name, s_value)
                except Exception as se:
                    raise SlicerInteractionError(
                        f"Could not apply slicer '{s_name}' with value '{s_value}': {se}"
                    ) from se
                applied_slicers.append(s_name)
                log.info(f"STEP2.{idx}_END")

            # ── Step 4: Read KPI ──────────────────────────────────────────────
            log.info(f"STEP4_START|Read KPI card: {kpi_to_read}")
            dashboard_raw = dashboard_page.extract_card_value(kpi_to_read)
            log.info(f"Dashboard KPI value: '{dashboard_raw}'")
            log.info("STEP4_END")

            # ── Step 5: Fetch DB value ────────────────────────────────────────
            log.info("STEP5_START|Fetch expected value from source database")

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

            # ── Step 6: Compare ───────────────────────────────────────────────
            log.info("STEP6_START|Compare dashboard KPI value against database result")
            passed, detail = compare_single_value(
                dashboard_raw=dashboard_raw,
                source_value=source_value,
                tolerance=0.01,
                label=f"{kpi_to_read} ({scenario_name})",
            )
            status = "PASS" if passed else "FAIL"
            log.info(f"[{status}] {detail}")
            if not passed:
                log.error(f"FAIL — {detail}")

            _test_passed  = passed
            _fail_message = f"{test_id} FAILED: {detail}"
            log.info("STEP6_END")

    except SlicerInteractionError as e:
        _test_passed  = False
        _fail_message = (
            f"{test_id} FAILED — Functional check: slicer could not be applied. "
            f"This is a UI interaction issue, not a data mismatch. Detail: {e}"
        )
        log.error(_fail_message)
    except Exception as e:
        _test_passed  = False
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

    assert _test_passed, _fail_message
