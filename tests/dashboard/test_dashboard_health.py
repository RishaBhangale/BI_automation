"""
test_dashboard_health.py — Pre-validation health checks for Power BI dashboards.

These tests act as a SMOKE SUITE that should run BEFORE KPI and table validation.
They catch configuration issues and access problems early, with clear error messages,
instead of letting them surface as cryptic timeouts deep in the validation logic.

Tests in this file:
  • test_dashboard_loads     — Verify the dashboard URL is reachable and shows no error banners.
  • test_pages_exist         — Verify all pages listed in the YAML config actually exist.
  • test_visuals_exist       — Verify all visual_title entries in YAML exist on their declared page.
  • test_filter_state        — (Optional) Verify slicer defaults match expected_filters in YAML.

Run order recommendation:
    pytest tests/dashboard/test_dashboard_health.py  # First — smoke check
    pytest tests/dashboard/test_kpi_validation.py    # Second — data validation
    pytest tests/dashboard/test_table_validation.py  # Third  — table validation

Or run all together (health tests will run first due to alphabetical order):
    pytest tests/dashboard/ --dashboard-config=dashboard_configs/my_dashboard.yaml
"""

import logging

import pytest

from locators.pbi_locators import PageNotFoundError

log = logging.getLogger("dashboard_methods")


# ── Test 1: Dashboard loads without errors ─────────────────────────────────────

@pytest.mark.dashboard
def test_dashboard_loads(dashboard_page, dashboard_config):
    """
    Verify the dashboard URL is reachable and shows no error banners.

    Checks for:
      • Power BI error containers (CSS class-based detection)
      • Known permission-denied / unavailable text patterns

    Fails fast with a clear message instead of letting downstream tests
    produce cryptic TimeoutErrors.
    """
    dash_name = dashboard_config["dashboard"].get("name", "Dashboard")
    url = dashboard_config["dashboard"].get("url", "")

    log.info(f"STEP1_START|Health check: Dashboard loads — {dash_name}")
    log.info(f"URL: {url}")
    log.info(f"Embed mode: {dashboard_page._embed_mode}")
    log.info("STEP1_END")

    log.info("STEP2_START|Check for error banners or permission-denied messages")
    has_error, error_msg = dashboard_page.check_for_error_banner()
    log.info(f"Error banner check: {'ERROR FOUND' if has_error else 'OK — no errors detected'}")
    log.info("STEP2_END")

    assert not has_error, (
        f"Dashboard failed to load correctly.\n"
        f"URL: {url}\n"
        f"Error: {error_msg}\n"
        f"Check that the URL is correct, the report is published, "
        f"and your account has access."
    )


# ── Test 2: All configured pages exist ────────────────────────────────────────

@pytest.mark.dashboard
def test_pages_exist(dashboard_page, dashboard_config):
    """
    Verify that every page listed in dashboard.pages[] exists in the report.

    Attempts switch_to_page() for each configured page. If a page cannot be
    found (PageNotFoundError), the test fails with a clear message indicating
    which page was missing and what pages are available.

    Skips gracefully if no pages are configured in the YAML.
    """
    pages = dashboard_config["dashboard"].get("pages", [])
    pages = [p for p in pages if p and str(p).strip()]

    log.info(f"STEP1_START|Health check: Configured pages exist — {len(pages)} page(s)")
    if not pages:
        log.info("No pages configured in YAML — skipping page existence check")
        log.info("STEP1_END")
        pytest.skip("No pages configured in dashboard.pages[] — nothing to check")

    log.info(f"Pages to verify: {pages}")
    log.info("STEP1_END")

    log.info(f"STEP2_START|Navigate to each configured page")
    missing_pages = []
    for page_name in pages:
        try:
            dashboard_page.switch_to_page(page_name)
            log.info(f"[OK] Page '{page_name}' — found and navigated successfully")
        except PageNotFoundError as e:
            log.error(f"[FAIL] Page '{page_name}' — not found: {e}")
            missing_pages.append(str(e))
        except Exception as e:
            log.error(f"[FAIL] Page '{page_name}' — unexpected error: {e}")
            missing_pages.append(f"Page '{page_name}': {e}")

    log.info(f"Pages verified: {len(pages) - len(missing_pages)}/{len(pages)}")
    log.info("STEP2_END")

    assert not missing_pages, (
        f"The following page(s) from your YAML config were not found in the report:\n"
        + "\n".join(f"  ✗ {m}" for m in missing_pages)
        + "\n\nCheck the page names in your YAML — they must match exactly "
        f"(case-sensitive) what appears in the Power BI report tab bar."
    )


# ── Test 3: All configured visual titles exist ─────────────────────────────────

@pytest.mark.dashboard
def test_visuals_exist(dashboard_page, dashboard_config):
    """
    Verify that every visual_title configured in kpi_validations and
    table_validations actually exists on its declared page.

    This catches renamed or removed visuals immediately, before the full
    KPI/table validation suite runs.

    For each visual:
      1. Navigate to the declared page (if specified).
      2. Call get_all_visual_titles() to get the current visual titles.
      3. Assert the configured title is in the list.

    Skips gracefully if no validations are configured in the YAML.
    """
    from utils.config_loader import get_kpi_validations, get_table_validations

    kpis   = get_kpi_validations(dashboard_config)
    tables = get_table_validations(dashboard_config)

    all_entries = [
        {"visual_title": e.get("visual_title", ""), "page": e.get("page", ""), "type": "KPI"}
        for e in kpis
    ] + [
        {"visual_title": e.get("visual_title", ""), "page": e.get("page", ""), "type": "Table"}
        for e in tables
    ]

    # Filter out empty titles
    all_entries = [e for e in all_entries if e["visual_title"].strip()]

    log.info(f"STEP1_START|Health check: Visual titles exist — {len(all_entries)} visual(s)")
    if not all_entries:
        log.info("No visual_title entries found in config — skipping visual existence check")
        log.info("STEP1_END")
        pytest.skip("No kpi_validations or table_validations defined in config")

    log.info(f"Visuals to verify: {[e['visual_title'] for e in all_entries]}")
    log.info("STEP1_END")

    log.info("STEP2_START|Check each visual title exists on its declared page")
    missing_visuals = []
    current_page    = None

    for entry in all_entries:
        visual_title = entry["visual_title"]
        page_name    = entry["page"]
        entry_type   = entry["type"]

        # Navigate to page if needed
        if page_name and page_name != current_page:
            try:
                dashboard_page.switch_to_page(page_name)
                current_page = page_name
            except PageNotFoundError as e:
                # Page doesn't exist — flag both the page and visual as missing
                log.error(f"[FAIL] Cannot navigate to page '{page_name}': {e}")
                missing_visuals.append(
                    f"[{entry_type}] '{visual_title}' on page '{page_name}': Page not found"
                )
                continue

        available = dashboard_page.get_all_visual_titles()
        if visual_title in available:
            log.info(f"[OK] [{entry_type}] '{visual_title}' on page '{page_name or 'current'}' — found")
        else:
            log.error(
                f"[FAIL] [{entry_type}] '{visual_title}' on page '{page_name or 'current'}' — NOT FOUND. "
                f"Available: {available}"
            )
            missing_visuals.append(
                f"[{entry_type}] '{visual_title}' on page '{page_name or 'current page'}'. "
                f"Available visuals: {available}"
            )

    log.info(f"Visuals verified: {len(all_entries) - len(missing_visuals)}/{len(all_entries)}")
    log.info("STEP2_END")

    assert not missing_visuals, (
        f"The following visual(s) from your YAML config were not found on their declared page:\n"
        + "\n".join(f"  ✗ {m}" for m in missing_visuals)
        + "\n\nThe visual may have been renamed or removed by the report author. "
        f"Re-run test_kpi_discover_visuals to get the current visual titles."
    )


# ── Test 4: Slicer state matches expected filters ──────────────────────────────

@pytest.mark.dashboard
def test_filter_state(dashboard_page, dashboard_config):
    """
    Verify that slicer defaults match the expected_filters declared in the YAML.

    This test prevents a common false-mismatch scenario: if the dashboard's
    default slicer shows "2023" but your SQL queries are filtered on 2024,
    every KPI will mismatch for the wrong reason.

    Configuration in YAML (optional block):
        expected_filters:
          - slicer_title: "Year"
            expected_value: "2024"
          - slicer_title: "Region"
            expected_value: "All"

    Skips gracefully if expected_filters is not configured.
    """
    expected_filters = dashboard_config.get("expected_filters", []) or []
    expected_filters = [f for f in expected_filters if f.get("slicer_title", "").strip()]

    log.info(f"STEP1_START|Health check: Filter state — {len(expected_filters)} slicer(s) to verify")
    if not expected_filters:
        log.info("No expected_filters configured in YAML — skipping slicer state check")
        log.info("STEP1_END")
        pytest.skip("No expected_filters configured in YAML")

    log.info(f"Slicers to verify: {[f['slicer_title'] for f in expected_filters]}")
    log.info("STEP1_END")

    log.info("STEP2_START|Read current slicer values and compare to expected")
    mismatches = []

    for f in expected_filters:
        slicer_title   = f.get("slicer_title", "")
        expected_value = str(f.get("expected_value", "")).strip()
        page_name      = f.get("page", "")

        if page_name:
            try:
                dashboard_page.switch_to_page(page_name)
            except PageNotFoundError as e:
                mismatches.append(f"Slicer '{slicer_title}': cannot navigate to page '{page_name}': {e}")
                continue

        try:
            current_values = dashboard_page.get_slicer_value(slicer_title)
            current_str = ", ".join(current_values)

            # Match: expected_value is in the current selections, or
            # expected is "All" and current is ["All"]
            matched = (
                expected_value in current_values
                or expected_value.lower() == "all" and current_values == ["All"]
                or expected_value == current_str
            )

            if matched:
                log.info(
                    f"[OK] Slicer '{slicer_title}': "
                    f"expected='{expected_value}', actual='{current_str}'"
                )
            else:
                log.error(
                    f"[FAIL] Slicer '{slicer_title}': "
                    f"expected='{expected_value}', actual='{current_str}'"
                )
                mismatches.append(
                    f"Slicer '{slicer_title}': expected='{expected_value}', actual='{current_str}'"
                )
        except ValueError as e:
            log.error(f"[FAIL] Slicer '{slicer_title}': {e}")
            mismatches.append(f"Slicer '{slicer_title}': {e}")

    log.info(f"Filter state: {len(expected_filters) - len(mismatches)}/{len(expected_filters)} matched")
    log.info("STEP2_END")

    assert not mismatches, (
        f"Slicer defaults do not match expected_filters in your YAML config.\n"
        f"This means your SQL queries may be filtering on different data than what the dashboard shows.\n\n"
        + "\n".join(f"  ✗ {m}" for m in mismatches)
        + "\n\nEither update expected_filters in your YAML to match the dashboard defaults, "
        f"or verify that your SQL WHERE clauses align with the slicer defaults."
    )
