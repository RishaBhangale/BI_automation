"""
test_business_scenarios.py — Business-logic scenario tests for Dashboard validation.

These tests simulate realistic end-user business workflows on the Power BI report.
Each test navigates to the page specified in the scenario Excel, applies the
configured filter state, validates one business rule against the source database,
then restores the report state.

PERFORMANCE NOTE:
    The browser session is shared across the entire test run (session-scoped).
    SSO login happens only ONCE. All test cases reuse the same open tab.

To run:
    pytest tests/dashboard/test_business_scenarios.py \
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
    """Raised when a slicer cannot be applied during test setup.

    Distinguishes UI/functional failures from data-mismatch failures
    so the HTML report gives a clear, actionable failure reason.
    """
    pass


# ---------------------------------------------------------------------------
# Load test cases from Excel at collection time
# ---------------------------------------------------------------------------
_default_excel = os.path.join(
    os.path.dirname(__file__),
    "../../test_data/business_scenarios.xlsx",
)

EXCEL_PATH = os.environ.get("BI_TEST_EXCEL_PATH", _default_excel)

if not os.path.exists(EXCEL_PATH):
    EXCEL_PATH = _default_excel

SQL_DIR = os.path.join(
    os.path.dirname(__file__),
    "../../test_data/sql_queries",
)

try:
    _df = pd.read_excel(EXCEL_PATH)

    # Replace pandas NaN with Python None so downstream guards work correctly
    test_cases = _df.where(pd.notnull(_df), None).to_dict("records")

except Exception as _exc:
    log.error(f"Failed to load test cases from Excel: {_exc}")
    test_cases = []


def _clean(val) -> str | None:
    """Return val as a stripped string, or None if it is empty / NaN / 'nan'.

    Guards against three different representations of 'empty' that can appear
    when reading Excel files with pandas:
      - Python None
      - float nan
      - string 'nan'
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
def test_business_scenario(
    dashboard_page,
    db_engine,
    dashboard_config,
    tc,
):
    """
    Data-Driven Business Scenario Test.

    Each row in the scenario Excel becomes one test case.

    The test:
      1. Navigates to the Page Name specified in the Excel row.
      2. Clears existing slicers.
      3. Optionally resets + applies configured slicers.
      4. Reads the target KPI card value from the dashboard.
      5. Fetches the expected value from the source database via the SQL file.
      6. Compares dashboard vs database (within tolerance).
      7. Resets all applied slicers in teardown.
    """

    test_id = _clean(tc.get("Test ID")) or "UNKNOWN"
    scenario_name = _clean(tc.get("Scenario Name")) or "Unnamed"

    # Page name is now driven by the Excel scenario.
    # Falls back to Summary for existing/legacy test cases that do not
    # have a Page Name column or have it left blank.
    page_name = _clean(tc.get("Page Name")) or "Summary"

    # Log the exact column headers pandas loaded, so a header typo/mismatch
    # (e.g. "Slicer 1 name" vs "Slicer 1 Name") is visible immediately.
    log.info(f"SCENARIO_CONFIG|Excel columns loaded: {list(tc.keys())}")

    slicers = []

    for i in range(1, 7):
        s_name = _clean(tc.get(f"Slicer {i} Name"))
        s_value = _clean(tc.get(f"Slicer {i} Value"))

        log.info(
            f"SCENARIO_CONFIG|Slicer {i}: "
            f"name='{s_name}', value='{s_value}'"
        )

        if s_name and s_value:
            slicers.append((s_name, s_value))

    kpi_to_read = _clean(tc.get("KPI to Read"))
    sql_file = _clean(tc.get("SQL File Name"))

    log.info(
        f"SCENARIO_CONFIG|Test ID='{test_id}' | "
        f"Page='{page_name}' | "
        f"Configured slicers={slicers} | "
        f"KPI='{kpi_to_read}' | "
        f"SQL='{sql_file}'"
    )

    log.info(f"--- Starting {test_id}: {scenario_name} ---")

    # -----------------------------------------------------------------------
    # Step 1: Navigate to the configured Power BI page
    # -----------------------------------------------------------------------
    log.info(
        f"STEP1_START|Navigate to {page_name} page "
        f"and confirm dashboard is loaded"
    )

    dashboard_page.switch_to_page(page_name)

    log.info("STEP1_END")

    # Track slicers so we can reset them in teardown
    applied_slicers: list[str] = []

    # We separate the result of the comparison so the assert runs AFTER
    # teardown (Step 7) has already completed cleanly.
    _test_passed = True
    _fail_message = ""

    try:
        # -------------------------------------------------------------------
        # Step 1.5: Clear ALL existing filters
        # -------------------------------------------------------------------
        log.info(
            "STEP1.5_START|Clear all default slicers "
            "to start from a clean state"
        )

        dashboard_page.clear_all_slicers()

        log.info("STEP1.5_END")

        # -------------------------------------------------------------------
        # Step 2: Apply configured slicers
        # -------------------------------------------------------------------

        if not slicers:
            log.warning(
                f"SCENARIO_CONFIG|No slicers configured for {test_id}. "
                f"The dashboard will remain in its default filter context."
            )

        for idx, (s_name, s_value) in enumerate(slicers, start=1):

            log.info(
                f"STEP2.{idx}_START|Apply Slicer: "
                f"{s_name} = {s_value}"
            )

            try:
                dashboard_page.reset_slicer(s_name)
            except Exception as reset_exc:
                log.debug(
                    f"Could not reset slicer '{s_name}' before applying: "
                    f"{reset_exc}"
                )

            # Support comma-separated multi-select values.
            slicer_values = [
                value.strip()
                for value in str(s_value).split(",")
                if value.strip()
            ]

            if not slicer_values:
                raise SlicerInteractionError(
                    f"Slicer '{s_name}' has no valid configured value."
                )

            log.info(
                f"Applying '{s_name}' with values: {slicer_values}"
            )

            for selected_value in slicer_values:

                log.info(
                    f"Applying slicer '{s_name}' "
                    f"value '{selected_value}'"
                )

                try:
                    dashboard_page.set_slicer(
                        s_name,
                        selected_value
                    )
                except Exception as se:
                    raise SlicerInteractionError(
                        f"Could not apply slicer '{s_name}' "
                        f"with value '{selected_value}': {se}"
                    ) from se

                log.info(
                    f"Slicer '{s_name}' value "
                    f"'{selected_value}' applied successfully"
                )

            applied_slicers.append(s_name)

            # Allow Power BI to recalculate visuals.
            dashboard_page._wait_for_dashboard_settle()

            log.info(
                f"STEP2.{idx}_END|Slicer '{s_name}' applied successfully"
            )

        # -------------------------------------------------------------------
        # Step 4: Read KPI from dashboard
        # -------------------------------------------------------------------
        log.info(f"STEP4_START|Read KPI card: {kpi_to_read}")

        dashboard_raw = dashboard_page.extract_card_value(kpi_to_read)

        log.info(f"Dashboard KPI value: '{dashboard_raw}'")
        log.info("STEP4_END")

        # -------------------------------------------------------------------
        # Step 5: Fetch expected value from source database
        # -------------------------------------------------------------------
        log.info("STEP5_START|Fetch expected value from source database")

        if db_engine is None:

            log.info(
                "No DB engine available — skipping DB comparison (STEP5)"
            )

            log.info("STEP5_END")

            pytest.skip(
                "No source database configured or reachable. "
                "Ensure DB credentials are set and VPN is active."
            )

        # -------------------------------------------------------------------
        # SQL resolution:
        #   1. Explicit SQL file from Excel
        #   2. Otherwise auto-generate query
        # -------------------------------------------------------------------
        if sql_file:

            # Explicit .sql file takes priority
            sql_path = os.path.join(SQL_DIR, sql_file)

            if not os.path.exists(sql_path):
                pytest.fail(
                    f"{test_id}: SQL file not found — {sql_path}"
                )

            with open(sql_path, "r", encoding="utf-8") as fh:
                sql_query = fh.read().strip()

            log.info(f"Using SQL file: {sql_file}")

        else:

            # Auto-generate from KPI title + slicer state
            if not kpi_to_read:
                pytest.fail(
                    f"{test_id}: 'KPI to Read' is empty — "
                    f"cannot auto-generate SQL"
                )

            try:
                sql_query = build_query(
                    kpi_to_read,
                    slicers,
                )

            except ValueError as ve:
                pytest.fail(
                    f"{test_id}: SQL auto-generation failed — {ve}"
                )

            log.info(f"Auto-generated SQL: {sql_query}")

        source_value = fetch_scalar(
            db_engine,
            sql_query,
        )

        log.info(f"Database result: {source_value}")
        log.info("STEP5_END")

        # -------------------------------------------------------------------
        # Step 6: Compare dashboard vs source
        # -------------------------------------------------------------------
        log.info(
            "STEP6_START|Compare dashboard KPI value "
            "against database result"
        )

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

        # Store result.
        # DO NOT assert here so that teardown (Step 7) always executes.
        _test_passed = passed
        _fail_message = f"{test_id} FAILED: {detail}"

        log.info("STEP6_END")

    except SlicerInteractionError as e:

        _test_passed = False

        _fail_message = (
            f"{test_id} FAILED — Functional check: "
            f"slicer could not be applied. "
            f"This is a UI interaction issue, not a data mismatch. "
            f"Detail: {e}"
        )

        log.error(_fail_message)

    except Exception as e:

        _test_passed = False

        _fail_message = (
            f"{test_id} FAILED during execution: {str(e)}"
        )

        log.error(_fail_message)

    finally:

        # -------------------------------------------------------------------
        # Step 7: Teardown
        # -------------------------------------------------------------------
        log.info(
            "STEP7_START|Teardown — reset applied slicers "
            "to restore default state"
        )

        for slicer in applied_slicers:

            try:
                dashboard_page.reset_slicer(slicer)

                log.info(
                    f"Reset slicer: {slicer}"
                )

            except Exception as exc:

                log.warning(
                    f"Could not reset slicer "
                    f"'{slicer}': {exc}"
                )

        log.info("STEP7_END")

    # -----------------------------------------------------------------------
    # Final assertion
    # -----------------------------------------------------------------------
    # The assertion happens OUTSIDE the finally block so that teardown
    # always completes before the test is marked as failed.
    assert _test_passed, _fail_message