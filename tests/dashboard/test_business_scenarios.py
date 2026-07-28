"""
test_business_scenarios.py — Business-logic scenario tests for Dashboard validation.

These tests simulate realistic end-user business workflows on the Power BI report.
Each test applies the default filter state (no custom slicer interactions initially),
validates one business rule, and then restores the report to its default state.

Test Cases Implemented
───────────────────────
TC-BIZ-001: State-Level Sales Validation
    Navigate to the Summary page, apply the "State" slicer to "California",
    read the "Total Sales" KPI card, and verify it matches the sum of all
    Sales records in the database where State = 'California'.

To run:
    pytest tests/dashboard/test_business_scenarios.py \\
        --dashboard-config=dashboard_configs/demo_detection.yaml -v
"""

import logging

import pytest

from utils.db_utils import fetch_scalar
from utils.validation_utils import compare_single_value

log = logging.getLogger("dashboard_methods")


# ── TC-BIZ-001: State-Level Sales Validation ──────────────────────────────────

@pytest.mark.dashboard
def test_biz_tc001_state_level_sales_validation(dashboard_page, db_engine, dashboard_config):
    """
    TC-BIZ-001 — State-Level Sales Validation

    Business Scenario:
        A regional sales manager selects "California" from the State filter to
        review how much revenue that state contributed. The "Total Sales" KPI
        card should update to show only California's figures.

    Steps:
        1. Open the Summary page and confirm the dashboard is loaded.
        2. Confirm the State slicer is currently set to "All" (default state).
        3. Select "California" in the State slicer.
        4. Read the updated "Total Sales" KPI card value.
        5. Fetch the expected Total Sales for California from the source database.
        6. Assert the dashboard value matches the database value within tolerance.
        7. Reset the State slicer back to "All" to restore the default state.
    """
    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")
    tolerance = 0.01  # 1% tolerance for number formatting differences

    # ── Step 1: Open Summary page ──────────────────────────────────────────────
    log.info("STEP1_START|Navigate to Summary page and confirm dashboard is loaded")
    log.info(f"Dashboard: {dash_name}")
    log.info("Switching to page: Summary")
    dashboard_page.switch_to_page("Summary")
    log.info("Summary page loaded successfully")
    log.info("STEP1_END")

    # ── Step 2: Confirm default slicer state ──────────────────────────────────
    log.info("STEP2_START|Confirm State slicer is at default (All)")
    try:
        current_state = dashboard_page.get_slicer_value("State")
        log.info(f"State slicer current value: {current_state}")
        if current_state != ["All"]:
            log.info(
                f"State slicer is not at default 'All' (found: {current_state}). "
                "Resetting before applying California filter."
            )
            dashboard_page.reset_slicer("State")
            log.info("State slicer reset to All")
        else:
            log.info("State slicer confirmed at default: All")
    except Exception as e:
        log.info(f"Could not read current slicer value (non-blocking): {e}")
    log.info("STEP2_END")

    # ── Step 3: Apply California filter ───────────────────────────────────────
    log.info("STEP3_START|Apply State slicer — select California")
    log.info("Clicking State slicer item: California")
    dashboard_page.set_slicer("State", "California")
    log.info("State slicer set to: California — dashboard is now filtered")
    log.info("STEP3_END")

    # ── Step 4: Read Total Sales KPI card ─────────────────────────────────────
    log.info("STEP4_START|Read Total Sales KPI card value (filtered to California)")
    dashboard_raw = dashboard_page.extract_card_value("Total Sales")
    log.info(f"Total Sales card value (California): '{dashboard_raw}'")
    log.info("STEP4_END")

    # ── Step 5: Fetch expected value from source database ─────────────────────
    log.info("STEP5_START|Fetch expected Total Sales for California from source database")
    california_sql = """
        SELECT SUM([Sales]) AS [Total Sales]
        FROM [SALES]
        WHERE [State] = 'California';
    """
    if db_engine is None:
        log.info("No DB engine configured — skipping database comparison (STEP5)")
        log.info("STEP5_END")
        pytest.skip(
            "No source database configured. "
            "Set source_db credentials in the dashboard YAML to enable DB comparison."
        )

    source_value = fetch_scalar(db_engine, california_sql)
    log.info(f"Database query result for California Total Sales: {source_value}")
    log.info(f"SQL used: SELECT SUM([Sales]) FROM [SALES] WHERE [State] = 'California'")
    log.info("STEP5_END")

    # ── Step 6: Compare dashboard value against database ──────────────────────
    log.info("STEP6_START|Compare dashboard KPI value against database result")
    passed, detail = compare_single_value(
        dashboard_raw=dashboard_raw,
        source_value=source_value,
        tolerance=tolerance,
        label="Total Sales (State=California)",
    )
    status = "PASS" if passed else "FAIL"
    log.info(f"[{status}] Total Sales (State=California): {detail}")
    if not passed:
        log.error(f"FAIL — Total Sales (California): {detail}")
    log.info("STEP6_END")

    # ── Step 7: Reset slicer back to All (teardown, always runs) ─────────────
    log.info("STEP7_START|Reset State slicer back to All (restore default state)")
    try:
        dashboard_page.reset_slicer("State")
        log.info("State slicer reset to All — dashboard restored to default state")
    except Exception as e:
        log.info(f"Could not reset slicer (non-blocking): {e}")
    log.info("STEP7_END")

    # ── Final assertion ────────────────────────────────────────────────────────
    assert passed, (
        f"TC-BIZ-001 FAILED — State-Level Sales Validation\n"
        f"Filter: State = California\n"
        f"KPI Card:  '{dashboard_raw}'\n"
        f"Database:  {source_value}\n"
        f"Detail:    {detail}"
    )
