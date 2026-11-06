"""
test_functional_parity.py — Scenario 1: Qlik ↔ PBI Functional Parity Tests.

All 7 test categories driven directly from test_data/functional_parity_scenarios.xlsx:
  A - Sheet/Page Discovery and Existence Parity
  B - Visual Existence per page
  C - Filter/Slicer Existence
  D - Filter Impact Propagation (CORE TEST: Qlik and PBI visuals react identically to filters)
  E - Navigation & Button Functionality (Measure toggle: Revenue vs. Quantity)
  F - Cross-Filter Behavior (chart click to filter other visuals)
  G - Filter Clear & State Isolation (reset integrity)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.validation_utils import detect_changed_visuals, compare_visual_impact
from config.settings import (
    QLIK_APP_ID_TRACK1,
    QLIK_SHEET_ID_MAIN,
    PBI_REPORT_URL_TRACK1,
)

log = logging.getLogger("test_functional_parity")

# ─────────────────────────────────────────────────────────────────────────────
# Load Excel test cases
# ─────────────────────────────────────────────────────────────────────────────

_default_excel = Path(__file__).parent.parent.parent / "test_data" / "functional_parity_scenarios.xlsx"
EXCEL_PATH = Path(os.environ.get("BI_TEST_EXCEL_PATH", _default_excel))
if not EXCEL_PATH.exists():
    EXCEL_PATH = _default_excel


def _load_test_cases(category: str) -> list[dict[str, Any]]:
    """Load test rows for a given category from the Excel scenario definition file."""
    if not EXCEL_PATH.exists():
        log.warning(f"Scenario Excel file not found at: {EXCEL_PATH}")
        return []
    try:
        df = pd.read_excel(EXCEL_PATH)
        if "Category" not in df.columns:
            return []
        filtered_df = df[df["Category"].astype(str).str.upper() == category.upper()]
        return filtered_df.where(pd.notnull(filtered_df), None).to_dict("records")
    except Exception as e:
        log.error(f"Error reading {EXCEL_PATH}: {e}")
        return []


def _tc_id(tc: dict) -> str:
    tid = tc.get("Test ID", "UNKNOWN")
    name = tc.get("Scenario Name") or tc.get("Test Name") or ""
    return f"{tid} - {name}".strip(" -")


def _norm_name(name: str) -> str:
    return " ".join(str(name).lower().split())


def _out_of_scope(config: dict) -> set[str]:
    """Sheets the migration team has confirmed are intentionally not in PBI report."""
    oos = {_norm_name(x) for x in (config.get("out_of_scope_sheets") or [])}
    if oos:
        log.warning(f"Excluding {len(oos)} out-of-scope Qlik sheet(s) from Category A checks: {sorted(oos)}")
    return oos


def _sheets_without_pbi_page(
    qlik_titles: list[str],
    pbi_pages: list[str],
    mapping: dict[str, str],
    out_of_scope: set[str],
) -> list[tuple[str, str]]:
    """Return [(qlik_sheet, expected_pbi_page)] for in-scope sheets missing in PBI."""
    pbi_set = {_norm_name(p) for p in pbi_pages}
    norm_map = {_norm_name(k): v for k, v in mapping.items()}
    missing = []
    for title in qlik_titles:
        if _norm_name(title) in out_of_scope:
            continue
        expected = norm_map.get(_norm_name(title), title)
        if _norm_name(expected) not in pbi_set:
            missing.append((title, expected))
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY A — Sheet / Page Discovery & Parity
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryA:
    """Category A: Sheet & Page Existence Parity."""

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_qlik_sheet_list_is_discoverable(self, qlik_dashboard, config):
        """FP-A-000: Enumerate all published Qlik sheets dynamically."""
        app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
        sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)

        log.info("FP-A-000: Navigating to Qlik app to verify session and enumerate sheets")
        qlik_dashboard.open(app_id, sheet_id)

        sheets = qlik_dashboard.get_sheet_list_via_api()
        assert len(sheets) > 0, (
            "Qlik REST API returned 0 sheets. "
            "Verify that Qlik session is valid and app has published sheets."
        )
        log.info(f"[PASS] Successfully enumerated {len(sheets)} Qlik sheets: {[s['title'] for s in sheets]}")

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_pbi_page_list_is_discoverable(self, pbi_dashboard, config):
        """FP-A-001: Enumerate all published PBI report pages dynamically."""
        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
        log.info("FP-A-001: Opening PBI report and enumerating page tabs")
        pbi_dashboard.open(report_url)

        pages = pbi_dashboard.get_page_list()
        assert len(pages) > 0, (
            "PBI returned 0 pages. Check report embed status or navigation tabs selector."
        )
        log.info(f"[PASS] Successfully enumerated {len(pages)} PBI pages: {pages}")

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_sheet_count_parity(self, qlik_dashboard, pbi_dashboard, config):
        """FP-A-002: Every in-scope Qlik sheet must have a corresponding PBI page."""
        app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
        sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

        qlik_dashboard.open(app_id, sheet_id)
        pbi_dashboard.open(report_url)

        qlik_sheets = qlik_dashboard.get_sheet_list_via_api()
        pbi_pages = pbi_dashboard.get_page_list()
        qlik_titles = [s["title"] for s in qlik_sheets]

        log.info(f"Qlik sheets count: {len(qlik_titles)} | PBI pages count: {len(pbi_pages)}")

        missing = _sheets_without_pbi_page(
            qlik_titles, pbi_pages, config.get("sheet_page_mapping", {}), _out_of_scope(config)
        )
        assert not missing, (
            f"FAIL — {len(missing)} in-scope Qlik sheets have no PBI page "
            f"(PBI has {len(pbi_pages)} pages):\n"
            + "\n".join(f"  - Qlik '{q}' -> expected PBI '{e}'" for q, e in missing)
            + f"\nPBI pages: {pbi_pages}"
        )
        log.info(f"[PASS] All in-scope Qlik sheets have a PBI page ({len(qlik_titles)} Qlik / {len(pbi_pages)} PBI)")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY B — Visual Existence per Page
# ─────────────────────────────────────────────────────────────────────────────

cat_b_cases = _load_test_cases("B")


@pytest.mark.cat_b
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_b_cases, ids=[_tc_id(tc) for tc in cat_b_cases] if cat_b_cases else [])
def test_visual_existence(tc, pbi_dashboard, config):
    """FP-B-*: Validates that key visuals specified for a sheet/page exist on PBI."""
    test_id = tc.get("Test ID", "UNKNOWN")
    pbi_page_name = tc.get("PBI Page Name")

    if not pbi_page_name:
        pytest.skip(f"{test_id}: No PBI Page Name defined.")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    visual_titles = pbi_dashboard.get_visual_titles()
    assert len(visual_titles) >= 3, (
        f"{test_id} FAIL — Expected at least 3 data visuals on PBI page '{pbi_page_name}', "
        f"but only found {len(visual_titles)}: {visual_titles}. "
        "Page may have failed to fully render or visual container selectors need updating."
    )

    expected_visual = tc.get("Field / Element") or tc.get("Visual Name")
    if expected_visual and str(expected_visual).strip().lower() not in ("all", "visuals", "none", "nan"):
        exp_clean = str(expected_visual).strip().lower()
        assert any(exp_clean in v.lower() for v in visual_titles), (
            f"{test_id} FAIL — Expected visual '{expected_visual}' not found in page visuals: {visual_titles}"
        )

    log.info(f"[PASS] {test_id}: Visuals verified on '{pbi_page_name}' ({len(visual_titles)} visuals found: {visual_titles})")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY C — Filter / Slicer Existence
# ─────────────────────────────────────────────────────────────────────────────

cat_c_cases = _load_test_cases("C")


if cat_c_cases:
    @pytest.mark.cat_c
    @pytest.mark.scenario1
    @pytest.mark.parametrize("tc", cat_c_cases, ids=[_tc_id(tc) for tc in cat_c_cases])
    def test_filter_existence(tc, pbi_dashboard, config):
        """FP-C-*: Validates that filter dimensions present in Qlik exist as slicers on PBI."""
        test_id = tc.get("Test ID", "UNKNOWN")
        pbi_page_name = tc.get("PBI Page Name")
        filter_field = tc.get("Field / Element") or tc.get("Filter Field")

        if not filter_field:
            pytest.skip(f"{test_id}: No Filter Field defined.")

        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
        pbi_dashboard.open(report_url)
        pbi_dashboard.switch_to_page(pbi_page_name)

        fields = [f.strip() for f in str(filter_field).split(";") if f.strip()]
        missing_slicers = []
        for fld in fields:
            if not pbi_dashboard.has_slicer(fld):
                missing_slicers.append(fld)

        assert not missing_slicers, (
            f"{test_id} FAIL — Filter/Slicer(s) {missing_slicers} not found on page '{pbi_page_name}'."
        )
        log.info(f"[PASS] {test_id}: Slicers {fields} confirmed present on '{pbi_page_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY D — Filter Impact Propagation (CORE FUNCTIONALITY TEST)
# ─────────────────────────────────────────────────────────────────────────────

cat_d_cases = _load_test_cases("D")


@pytest.mark.cat_d
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_d_cases, ids=[_tc_id(tc) for tc in cat_d_cases] if cat_d_cases else [])
def test_filter_impact_propagation(tc, qlik_dashboard, pbi_dashboard, config):
    """
    FP-D-*: Apply a filter on BOTH Qlik and PBI.
    Verify that visuals react and propagate filter changes on both platforms with parity.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    filter_field = tc.get("Field / Element") or tc.get("Filter Field")
    filter_val = tc.get("Value / Action") or tc.get("Filter Value")
    qlik_sheet_name = tc.get("Qlik Sheet Name")
    pbi_page_name = tc.get("PBI Page Name")

    if not filter_field or not filter_val:
        pytest.skip(f"{test_id}: Incomplete filter configuration.")

    log.info(f"--- {test_id}: Starting Filter Impact Propagation ({filter_field} = {filter_val}) ---")

    app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
    sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    fields = [f.strip() for f in str(filter_field).split(";") if f.strip()]
    values = [v.strip() for v in str(filter_val).split(";") if v.strip()]

    # ── STEP 1: Qlik Baseline & Filter ─────────────────────────────────────────
    log.info(f"STEP 1: Qlik — Open sheet '{qlik_sheet_name or 'primary'}' and capture baseline")
    qlik_dashboard.open(app_id, sheet_id)
    if qlik_sheet_name:
        qlik_dashboard.navigate_to_sheet(qlik_sheet_name)

    qlik_baseline = qlik_dashboard.capture_baseline()
    assert len(qlik_baseline) > 0, f"{test_id}: Qlik baseline is empty — cannot detect visual changes"

    log.info(f"STEP 2: Qlik — Apply filter(s): {list(zip(fields, values))}")
    for fld, val in zip(fields, values):
        try:
            qlik_dashboard.apply_filter(fld, val)
        except Exception as e:
            pytest.fail(f"{test_id} FAIL — Could not apply Qlik filter '{fld}'='{val}': {e}")

    log.info("STEP 3: Qlik — Capture post-filter state")
    qlik_filtered = qlik_dashboard.capture_baseline()
    assert len(qlik_filtered) > 0, f"{test_id} FAIL — Post-filter Qlik capture returned 0 visuals on '{qlik_sheet_name}'"
    changed_qlik = detect_changed_visuals(qlik_baseline, qlik_filtered)
    log.info(f"Qlik visuals changed: {sorted(changed_qlik)}")

    # ── STEP 2: Qlik Teardown ──────────────────────────────────────────────────
    qlik_dashboard.clear_all_filters()

    # ── STEP 3: PBI Baseline & Slicer ──────────────────────────────────────────
    log.info("STEP 4: PBI — Open report and capture baseline")
    pbi_dashboard.open(report_url)
    if pbi_page_name:
        pbi_dashboard.switch_to_page(pbi_page_name)

    pbi_baseline = pbi_dashboard.capture_page_baseline()
    assert len(pbi_baseline) > 0, f"{test_id}: PBI baseline is empty — cannot detect visual changes"

    log.info(f"STEP 5: PBI — Apply slicer(s): {list(zip(fields, values))}")
    for fld, val in zip(fields, values):
        try:
            applied = pbi_dashboard.apply_slicer(fld, val, raise_on_error=True)
            assert applied, (
                f"{test_id} FAIL — Slicer/filter '{fld}'='{val}' could not be selected on PBI page '{pbi_page_name}'"
            )
        except Exception as e:
            pytest.fail(f"{test_id} FAIL — Failed to apply PBI slicer '{fld}'='{val}' on page '{pbi_page_name}': {e}")

    log.info("STEP 6: PBI — Capture post-filter state")
    pbi_filtered = pbi_dashboard.capture_page_baseline()
    assert len(pbi_filtered) > 0, f"{test_id} FAIL — Post-filter PBI capture returned 0 visuals on '{pbi_page_name}'"
    changed_pbi = detect_changed_visuals(pbi_baseline, pbi_filtered)
    log.info(f"PBI visuals changed: {sorted(changed_pbi)}")

    # ── STEP 4: PBI Teardown ───────────────────────────────────────────────────
    pbi_dashboard.clear_all_slicers()

    # ── STEP 5: Parity Verification ────────────────────────────────────────────
    assert len(changed_qlik) > 0, (
        f"{test_id} FAIL — Filter {list(zip(fields, values))} had NO effect on Qlik sheet '{qlik_sheet_name}'. "
        f"0 visuals changed state."
    )
    assert len(changed_pbi) > 0, (
        f"{test_id} FAIL — Filter {list(zip(fields, values))} had NO effect on PBI page '{pbi_page_name}'. "
        f"PBI visuals changed: 0 (while Qlik had {len(changed_qlik)} changes: {sorted(changed_qlik)}). "
        f"Filter propagation failed to update visuals."
    )

    parity_passed, parity_detail = compare_visual_impact(changed_qlik, changed_pbi, test_id=test_id)
    assert parity_passed, parity_detail
    log.info(f"[PASS] {parity_detail}")


if not cat_d_cases:
    del test_filter_impact_propagation


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY E — Navigation & Button Functionality
# ─────────────────────────────────────────────────────────────────────────────

cat_e_cases = _load_test_cases("E")


@pytest.mark.cat_e
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_e_cases, ids=[_tc_id(tc) for tc in cat_e_cases] if cat_e_cases else [])
def test_navigation_and_buttons(tc, qlik_dashboard, pbi_dashboard, config):
    """FP-E-*: Tests interactive bookmark buttons, navigation links, and toggle actions on Qlik & PBI."""
    test_id = tc.get("Test ID", "UNKNOWN")
    button_label = tc.get("Field / Element") or tc.get("Button / Action Label") or "Revenue"
    qlik_sheet_name = tc.get("Qlik Sheet Name") or "Overdue Opportunities"
    pbi_page_name = tc.get("PBI Page Name") or "Overdue Opportunities"

    log.info(f"--- {test_id}: Testing interactive bookmark button '{button_label}' on '{qlik_sheet_name}' ↔ '{pbi_page_name}' ---")

    app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
    sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    alt_label = "Quantity" if "rev" in str(button_label).lower() else "Revenue"

    # ── STEP 1: Qlik — Open sheet and test bookmark button functionality ───────
    log.info(f"STEP 1: Qlik — Open sheet '{qlik_sheet_name}' and capture baseline")
    qlik_dashboard.open(app_id, sheet_id)
    if qlik_sheet_name:
        qlik_dashboard.navigate_to_sheet(qlik_sheet_name)

    qlik_baseline = qlik_dashboard.capture_baseline()
    assert len(qlik_baseline) > 0, f"{test_id}: Qlik baseline is empty on '{qlik_sheet_name}'"

    log.info(f"STEP 2: Qlik — Click alternate button '{alt_label}' to transition state")
    qlik_clicked_alt = qlik_dashboard.click_button(alt_label)
    assert qlik_clicked_alt, f"{test_id} FAIL — Could not click button '{alt_label}' on Qlik sheet '{qlik_sheet_name}'"
    qlik_dashboard.page.wait_for_timeout(3_000)

    qlik_mid = qlik_dashboard.capture_baseline()
    changed_qlik_alt = detect_changed_visuals(qlik_baseline, qlik_mid)
    log.info(f"Qlik visuals changed after clicking '{alt_label}': {sorted(changed_qlik_alt)}")

    log.info(f"STEP 3: Qlik — Click button '{button_label}' to transition back and observe value changes")
    qlik_clicked_target = qlik_dashboard.click_button(button_label)
    assert qlik_clicked_target, f"{test_id} FAIL — Could not click button '{button_label}' on Qlik sheet '{qlik_sheet_name}'"
    qlik_dashboard.page.wait_for_timeout(3_000)

    qlik_post = qlik_dashboard.capture_baseline()
    changed_qlik_target = detect_changed_visuals(qlik_mid, qlik_post)
    log.info(f"Qlik visuals changed after clicking '{button_label}': {sorted(changed_qlik_target)}")

    all_changed_qlik = changed_qlik_alt | changed_qlik_target
    assert len(all_changed_qlik) > 0, (
        f"{test_id} FAIL — Clicking '{alt_label}' / '{button_label}' buttons had no effect on Qlik sheet '{qlik_sheet_name}'."
    )

    # ── STEP 2: PBI — Open report and test bookmark button functionality ───────
    log.info(f"STEP 4: PBI — Open report and switch to corresponding page '{pbi_page_name}'")
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    pbi_baseline = pbi_dashboard.capture_page_baseline()
    assert len(pbi_baseline) > 0, f"{test_id}: PBI baseline is empty on '{pbi_page_name}'"

    log.info(f"STEP 5: PBI — Click alternate button '{alt_label}' to transition state")
    pbi_clicked_alt = pbi_dashboard.set_metric_toggle(alt_label)
    assert pbi_clicked_alt, f"{test_id} FAIL — Could not click button '{alt_label}' on PBI page '{pbi_page_name}'"
    pbi_dashboard.page.wait_for_timeout(3_000)

    pbi_mid = pbi_dashboard.capture_page_baseline()
    changed_pbi_alt = detect_changed_visuals(pbi_baseline, pbi_mid)
    log.info(f"PBI visuals changed after clicking '{alt_label}': {sorted(changed_pbi_alt)}")

    log.info(f"STEP 6: PBI — Click button '{button_label}' to transition back and observe value changes")
    pbi_clicked_target = pbi_dashboard.set_metric_toggle(button_label)
    assert pbi_clicked_target, f"{test_id} FAIL — Could not click button '{button_label}' on PBI page '{pbi_page_name}'"
    pbi_dashboard.page.wait_for_timeout(3_000)

    pbi_post = pbi_dashboard.capture_page_baseline()
    changed_pbi_target = detect_changed_visuals(pbi_mid, pbi_post)
    log.info(f"PBI visuals changed after clicking '{button_label}': {sorted(changed_pbi_target)}")

    all_changed_pbi = changed_pbi_alt | changed_pbi_target
    assert len(all_changed_pbi) > 0, (
        f"{test_id} FAIL — Clicking '{alt_label}' / '{button_label}' buttons had no effect on PBI page '{pbi_page_name}'."
    )

    log.info(
        f"[PASS] {test_id}: Bookmark filter button functionality verified across Qlik "
        f"({len(all_changed_qlik)} visual(s) changed: {sorted(all_changed_qlik)}) and Power BI "
        f"({len(all_changed_pbi)} visual(s) changed: {sorted(all_changed_pbi)})."
    )


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY F — Cross-Filter Behavior
# ─────────────────────────────────────────────────────────────────────────────

cat_f_cases = _load_test_cases("F")


@pytest.mark.cat_f
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_f_cases, ids=[_tc_id(tc) for tc in cat_f_cases] if cat_f_cases else [])
def test_cross_filter_behavior(tc, pbi_dashboard, config):
    """FP-F-*: Click on a chart segment/bar and verify other visual cards update."""
    test_id = tc.get("Test ID", "UNKNOWN")
    chart_name = tc.get("Field / Element") or tc.get("Chart Visual Name") or "Market"
    target_segment = tc.get("Value / Action") or tc.get("Segment Label") or "FRANCE"
    pbi_page_name = tc.get("PBI Page Name") or "Missing PN Opportunities ISG"

    log.info(f"--- {test_id}: Cross-filter test on '{chart_name}' -> '{target_segment}' ---")
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    baseline = pbi_dashboard.capture_page_baseline()
    assert len(baseline) > 0, f"{test_id}: Baseline is empty on '{pbi_page_name}'"
    ctx = pbi_dashboard._ctx()
    vis_container = ctx.locator(f"visual-container:has-text('{chart_name}')").first
    target_scope = vis_container if (vis_container.count() > 0 and vis_container.is_visible(timeout=1_500)) else ctx

    bar = target_scope.locator(
        f"rect[aria-label*='{target_segment}' i], "
        f"[data-automation-type*='chart-rect'][aria-label*='{target_segment}' i], "
        f"path[aria-label*='{target_segment}' i], "
        f"[aria-label*='{target_segment}' i], "
        f"svg text:has-text('{target_segment}'), "
        f"[role='gridcell']:has-text('{target_segment}'), "
        f"span:has-text('{target_segment}')"
    ).first
    assert bar.count() > 0 and bar.is_visible(timeout=4_000), (
        f"{test_id} FAIL — Segment '{target_segment}' in visual '{chart_name}' not found on '{pbi_page_name}'."
    )

    bar.click(timeout=3_000, force=True)
    pbi_dashboard.page.wait_for_timeout(3_000)

    post_click = pbi_dashboard.capture_page_baseline()
    assert len(post_click) > 0, f"{test_id} FAIL — Post-click PBI capture returned 0 visuals on '{pbi_page_name}'"
    changed = detect_changed_visuals(baseline, post_click)
    pbi_dashboard.clear_all_slicers()

    assert len(changed) > 0, (
        f"{test_id} FAIL — Cross-filtering on '{target_segment}' in visual '{chart_name}' did not update any other visuals on '{pbi_page_name}'."
    )

    log.info(f"[PASS] {test_id}: Cross-filter interaction verified on '{pbi_page_name}' ({len(changed)} visual(s) changed: {sorted(changed)})")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY G — Filter Clear & State Isolation
# ─────────────────────────────────────────────────────────────────────────────

cat_g_cases = _load_test_cases("G")


@pytest.mark.cat_g
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_g_cases, ids=[_tc_id(tc) for tc in cat_g_cases] if cat_g_cases else [])
def test_filter_clear_restores_baseline(tc, pbi_dashboard, config):
    """FP-G-*: Apply filter, clear it, verify return to baseline state."""
    test_id = tc.get("Test ID", "FP-G-001")
    pbi_page_name = tc.get("PBI Page Name") or "Overdue Opportunities ISG"
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    log.info(f"--- {test_id}: State isolation check on PBI '{pbi_page_name}' ---")
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    baseline = pbi_dashboard.capture_page_baseline()
    assert len(baseline) > 0, f"{test_id}: PBI baseline is empty."

    # 1. Apply a test slicer to perturb state away from baseline
    test_slicer = tc.get("Field / Element") or tc.get("Filter Field") or "GEO Level 3"
    test_val = tc.get("Value / Action") or tc.get("Filter Value") or "BENELUX"
    log.info(f"Applying test perturbation filter: {test_slicer} = {test_val}")
    applied = pbi_dashboard.apply_slicer(test_slicer, test_val, raise_on_error=True)
    assert applied, f"{test_id} FAIL — Failed to apply perturbation filter '{test_slicer}'='{test_val}'"
    pbi_dashboard.page.wait_for_timeout(2_000)

    perturbed = pbi_dashboard.capture_page_baseline()
    assert len(perturbed) > 0, f"{test_id} FAIL — Perturbed PBI capture returned 0 visuals on '{pbi_page_name}'"
    changed_perturb = detect_changed_visuals(baseline, perturbed)
    assert len(changed_perturb) > 0, (
        f"{test_id} FAIL — Perturbation slicer '{test_slicer}'='{test_val}' did not alter any visuals "
        f"on '{pbi_page_name}'. Cannot test reset integrity without verified state perturbation."
    )
    log.info(f"Perturbation confirmed: {len(changed_perturb)} visual(s) changed: {sorted(changed_perturb)}")

    # 2. Clear slicers to restore default state
    pbi_dashboard.clear_all_slicers()
    pbi_dashboard.page.wait_for_timeout(2_000)
    post_clear = pbi_dashboard.capture_page_baseline()
    assert len(post_clear) > 0, f"{test_id} FAIL — Post-clear PBI capture returned 0 visuals on '{pbi_page_name}'"

    # 3. Assert baseline is restored (zero residual bleed)
    residual = detect_changed_visuals(baseline, post_clear)
    assert not residual, f"{test_id} FAIL — Residual state after clearing slicers. Changed: {sorted(residual)}"
    log.info(f"[PASS] {test_id}: State isolation confirmed — clean baseline restored")


if not cat_g_cases:
    del test_filter_clear_restores_baseline