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

    # ── Step 4 ─────────────────────────────────────────────────────────────────
    log.info("STEP4_START|Chart data snapshot (Show as a table)")
    chart_configs = dashboard_config.get("table_validations", [])
    if not chart_configs:
        log.info("No chart validations configured — skipping chart snapshot")
    else:
        from locators.pbi_locators import PBILocators
        snap_pass = 0
        snap_fail = 0
        for chart_cfg in chart_configs:
            ctitle  = chart_cfg.get("visual_title", "")
            ctype   = chart_cfg.get("visual_type", "")
            cidx    = chart_cfg.get("visual_index") or 0
            cpage   = chart_cfg.get("page", "")
            if cpage:
                try:
                    dashboard_page.switch_to_page(cpage)
                except Exception:
                    pass
            try:
                rows = dashboard_page.extract_table_data(
                    visual_title=ctitle,
                    visual_type=ctype or None,
                    visual_index=int(cidx),
                )
                if rows:
                    cols    = list(rows[0].keys()) if rows else []
                    preview = " | ".join(str(v) for v in list(rows[0].values())[:4]) if rows else "(empty)"
                    log.info(
                        f"[CHART SNAP] '{ctitle or ctype}' — "
                        f"{len(rows)} rows, columns: {cols[:6]} — "
                        f"row[0]: {preview}"
                    )
                    snap_pass += 1
                else:
                    log.info(f"[CHART SNAP] '{ctitle or ctype}' — 0 rows extracted (chart may be empty)")
                    snap_fail += 1
            except Exception as e:
                log.warning(f"[CHART SNAP] '{ctitle or ctype}' — could not extract: {e}")
                snap_fail += 1
        log.info(f"Chart snapshot summary: {snap_pass}/{len(chart_configs)} extracted")
    log.info("STEP4_END")

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

    Output now includes:
      • The visual title (for use in visual_title: YAML field)
      • The visual type (for use in visual_type: YAML field as fallback)
      • The 0-based index among visuals of the same type (for visual_index:)
      • A snippet of the visual's text content

    This test ALWAYS PASSES. Run it first for any new dashboard.
    """
    from locators.pbi_locators import PBILocators

    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")
    pages     = dashboard_config["dashboard"].get("pages", [])

    log.info(f"STEP1_START|Discover testable visuals: {dash_name}")
    log.info(f"Pages configured in YAML: {pages or ['(none — current page only)']}")
    log.info("STEP1_END")

    log.info("STEP2_START|Scan page(s) for testable visuals")

    def _print_visuals(visuals_data: list[dict]):
        """Print a rich summary table of discovered visuals."""
        # Track per-type index for visual_index guidance
        type_counts: dict[str, int] = {}
        for v in visuals_data:
            vtype = v.get('type', '')
            idx = type_counts.get(vtype, 0)
            type_counts[vtype] = idx + 1
            title    = v.get('title', '').strip()
            snippet  = v.get('fullText', '').replace('\n', ' | ')[:80]

            print(f"  • title:        '{title}'")
            print(f"    visual_type:  '{vtype}'")
            print(f"    visual_index: {idx}")
            print(f"    text snippet: {snippet}")
            print()

    if not pages:
        visuals_data = dashboard_page.discover_all_visuals()
        # Filter to testable types only
        testable = [v for v in visuals_data
                    if v.get('type') in PBILocators.PTW_TESTABLE_TYPES]
        log.info(f"Testable visuals found ({len(testable)})")
        print(f"\n=== Testable visuals on current page ({len(testable)} found) ===")
        print("    (Use 'visual_title' if title is meaningful, otherwise use 'visual_type' + 'visual_index')\n")
        _print_visuals(testable)
        log.info("STEP2_END")
        assert True
        return

    print(f"\n=== Discovering visual titles across {len(pages)} page(s) ===")
    for page_name in pages:
        if not page_name:
            continue
        dashboard_page.switch_to_page(page_name)
        visuals_data = dashboard_page.discover_all_visuals()
        testable = [v for v in visuals_data
                    if v.get('type') in PBILocators.PTW_TESTABLE_TYPES]
        log.info(f"Page '{page_name}' — {len(testable)} testable visuals")
        print(f"\n  Page: '{page_name}'")
        _print_visuals(testable)

    log.info("STEP2_END")
    assert True
