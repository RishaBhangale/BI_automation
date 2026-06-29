"""
test_kpi_validation.py — KPI card data validation tests.

Tests in this file extract values from Power BI KPI card visuals and
compare them against the source database or Excel.

How it works:
  1. The dashboard_config fixture reads your YAML config file.
  2. For each entry in kpi_validations[], validate_all_kpis() is called.
  3. It extracts the KPI value from the dashboard via Playwright.
  4. It fetches the expected value from the DB (SQL) or Excel.
  5. It compares the two using compare_single_value() with tolerance.

To run:
    pytest tests/dashboard/test_kpi_validation.py \
        --dashboard-config=dashboard_configs/public_sales_dashboard.yaml

To discover visuals first (do this for any new dashboard):
    pytest tests/dashboard/test_kpi_validation.py::test_kpi_discover_visuals \
        --dashboard-config=dashboard_configs/_template.yaml -s
"""

import logging

import pytest

from methods.dashboard_methods import validate_all_kpis
from utils.config_loader import get_kpi_validations

# Use the same logger name as dashboard_methods — captured by the report hook.
log = logging.getLogger("dashboard_methods")


# ── KPI Validation ─────────────────────────────────────────────────────────────

@pytest.mark.dashboard
def test_all_kpis(dashboard_page, db_engine, dashboard_config):
    """
    Validate ALL KPI card visuals defined in the dashboard YAML config.

    Emits STEP markers so the HTML report shows step-by-step detail:
      Step 1 — Open dashboard + confirm render
      Step 2 — Discover testable visuals on the page
      Step 3 — Extract + validate KPI values against source
    """
    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")

    # ── Step 1 ─────────────────────────────────────────────────────────────────
    log.info(f"STEP1_START|Open dashboard: {dash_name}")
    url = dashboard_config["dashboard"].get("url", "")
    log.info(f"Dashboard URL: {url}")
    log.info(f"Embed mode: {dashboard_page._embed_mode}")
    log.info("Report canvas ready — visuals loaded")
    log.info("STEP1_END")

    # ── Step 2 ─────────────────────────────────────────────────────────────────
    log.info("STEP2_START|Discover testable visuals on current page")
    titles = dashboard_page.get_all_visual_titles()
    kpis_defined = get_kpi_validations(dashboard_config)
    log.info(f"Testable visuals found ({len(titles)}): {titles}")
    log.info(f"KPI validations configured: {len(kpis_defined)}")
    if not kpis_defined:
        log.info("No KPI validations defined in config — test will be skipped")
    log.info("STEP2_END")

    if not kpis_defined:
        pytest.skip("No KPI validations defined in the dashboard config")

    # ── Step 3 ─────────────────────────────────────────────────────────────────
    log.info("STEP3_START|Extract KPI values and validate against source")
    results = validate_all_kpis(
        dashboard_page=dashboard_page,
        config=dashboard_config,
        db_engine=db_engine,
    )

    failures = [r for r in results if not r["passed"]]
    total    = len(results)
    passed   = total - len(failures)

    for r in results:
        status     = "PASS" if r["passed"] else "FAIL"
        page_label = f"[{r['page']}] " if r["page"] else ""
        log.info(f"[{status}] {page_label}{r['visual_title']}: {r['detail']}")

    if failures:
        for r in failures:
            log.error(f"FAIL — {r['visual_title']}: {r['detail']}")

    log.info(f"KPI Summary: {passed}/{total} passed")
    log.info("STEP3_END")

    failure_summary = "\n".join(
        f"  \u2717 [{r['page']}] {r['visual_title']}: {r['detail']}"
        for r in failures
    )
    assert not failures, (
        f"KPI validation: {passed}/{total} passed.\n"
        f"Failures:\n{failure_summary}"
    )


# ── Visual Discovery (Diagnostic) ─────────────────────────────────────────────

@pytest.mark.dashboard
def test_kpi_discover_visuals(dashboard_page, dashboard_config):
    """
    Diagnostic test: print all TESTABLE visual titles found on each report page.

    Testable visuals = aria-roledescription in PTW_TESTABLE_TYPES
    (Card, Charts, Table, Matrix) — slicers and textboxes are excluded.

    This test always passes. Run it first for any new dashboard to populate
    the visual_title fields in your YAML config.
    """
    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")
    pages     = dashboard_config["dashboard"].get("pages", [])

    log.info(f"STEP1_START|Discover testable visuals: {dash_name}")
    log.info(f"Pages configured in YAML: {pages or ['(none — current page only)']}")
    log.info("STEP1_END")

    log.info("STEP2_START|Scan page(s) for testable visuals")

    if not pages:
        titles = dashboard_page.get_all_visual_titles()
        log.info(f"Testable visuals found ({len(titles)}): {titles}")
        print(f"\n=== Testable visuals on current page ===")
        for t in titles:
            print(f"  \u2022 {t}")
        log.info("STEP2_END")
        assert True
        return

    print(f"\n=== Discovering visual titles across {len(pages)} page(s) ===")
    for page_name in pages:
        if not page_name:
            continue
        dashboard_page.switch_to_page(page_name)
        titles = dashboard_page.get_all_visual_titles()
        log.info(f"Page '{page_name}' — {len(titles)} testable visuals: {titles}")
        print(f"\n  Page: '{page_name}'")
        for t in titles:
            print(f"    \u2022 {t}")

    log.info("STEP2_END")
    assert True
