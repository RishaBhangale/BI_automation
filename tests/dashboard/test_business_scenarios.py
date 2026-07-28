"""
test_business_scenarios.py — Business-logic scenario tests for Dashboard validation.

These tests simulate realistic end-user business workflows on the Power BI report.
Each test applies the default filter state (no custom slicer interactions initially),
validates one business rule, and then restores the report to its default state.

To run:
    pytest tests/dashboard/test_business_scenarios.py \
        --dashboard-config=dashboard_configs/demo_detection.yaml -v
"""

import logging
import os
import pandas as pd
import pytest

from utils.db_utils import fetch_scalar
from utils.validation_utils import compare_single_value

log = logging.getLogger("dashboard_methods")

# Load test cases from Excel
EXCEL_PATH = os.path.join(os.path.dirname(__file__), "../../test_data/business_scenarios.xlsx")
SQL_DIR = os.path.join(os.path.dirname(__file__), "../../test_data/sql_queries")

try:
    df_tests = pd.read_excel(EXCEL_PATH)
    # Convert dataframe to list of dicts for parametrize, replacing NaNs with None
    test_cases = df_tests.where(pd.notnull(df_tests), None).to_dict('records')
except Exception as e:
    log.error(f"Failed to load test cases from Excel: {e}")
    test_cases = []

def id_func(tc):
    return tc["Test ID"]

@pytest.mark.dashboard
@pytest.mark.parametrize("tc", test_cases, ids=id_func)
def test_business_scenario(dashboard_page, db_engine, dashboard_config, tc):
    """
    Generic Data-Driven Test for Business Scenarios.
    """
    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")
    tolerance = 0.01

    test_id = tc["Test ID"]
    scenario_name = tc["Scenario Name"]
    slicer1_name = tc.get("Slicer 1 Name")
    slicer1_value = tc.get("Slicer 1 Value")
    slicer2_name = tc.get("Slicer 2 Name")
    slicer2_value = tc.get("Slicer 2 Value")
    kpi_to_read = tc.get("KPI to Read")
    sql_file = tc.get("SQL File Name")

    log.info(f"--- Starting {test_id}: {scenario_name} ---")

    # Step 1: Open Summary page
    log.info("STEP1_START|Navigate to Summary page and confirm dashboard is loaded")
    dashboard_page.switch_to_page("Summary")
    log.info("STEP1_END")

    # Keep track of slicers applied so we can reset them later
    applied_slicers = []

    try:
        # Step 2: Apply Slicer 1
        if slicer1_name and slicer1_value:
            log.info(f"STEP2_START|Apply Slicer: {slicer1_name} = {slicer1_value}")
            # Ensure it is at default first
            try:
                dashboard_page.reset_slicer(slicer1_name)
            except Exception:
                pass
            dashboard_page.set_slicer(slicer1_name, slicer1_value)
            applied_slicers.append(slicer1_name)
            log.info("STEP2_END")

        # Step 3: Apply Slicer 2
        if slicer2_name and slicer2_value:
            log.info(f"STEP3_START|Apply Slicer: {slicer2_name} = {slicer2_value}")
            try:
                dashboard_page.reset_slicer(slicer2_name)
            except Exception:
                pass
            dashboard_page.set_slicer(slicer2_name, slicer2_value)
            applied_slicers.append(slicer2_name)
            log.info("STEP3_END")

        # Step 4: Read KPI card
        log.info(f"STEP4_START|Read KPI card: {kpi_to_read}")
        dashboard_raw = dashboard_page.extract_card_value(kpi_to_read)
        log.info(f"Dashboard Value: '{dashboard_raw}'")
        log.info("STEP4_END")

        # Step 5: Fetch expected value from source database
        log.info("STEP5_START|Fetch expected value from source database")
        
        sql_path = os.path.join(SQL_DIR, sql_file)
        if not os.path.exists(sql_path):
            pytest.fail(f"SQL file missing: {sql_path}")
            
        with open(sql_path, "r") as f:
            sql_query = f.read()
            
        if db_engine is None:
            log.info("No DB engine configured — skipping database comparison (STEP5)")
            log.info("STEP5_END")
            pytest.skip("No source database configured.")

        source_value = fetch_scalar(db_engine, sql_query)
        log.info(f"Database query result: {source_value}")
        log.info("STEP5_END")

        # Step 6: Compare
        log.info("STEP6_START|Compare dashboard KPI value against database result")
        passed, detail = compare_single_value(
            dashboard_raw=dashboard_raw,
            source_value=source_value,
            tolerance=tolerance,
            label=f"{kpi_to_read} ({scenario_name})",
        )
        status = "PASS" if passed else "FAIL"
        log.info(f"[{status}] {detail}")
        if not passed:
            log.error(f"FAIL — {detail}")
        log.info("STEP6_END")
        
        assert passed, f"{test_id} FAILED: {detail}"

    finally:
        # Step 7: Teardown - Reset applied slicers
        log.info("STEP7_START|Teardown - Reset applied slicers")
        for slicer in applied_slicers:
            try:
                dashboard_page.reset_slicer(slicer)
                log.info(f"Reset {slicer}")
            except Exception as e:
                log.warning(f"Could not reset {slicer}: {e}")
        log.info("STEP7_END")
