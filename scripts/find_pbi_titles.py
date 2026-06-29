"""
find_pbi_titles.py  — Evaluate all 3 public dashboards for KPI testability.

Waits for loading spinners to disappear before analyzing DOM.
Reports on visual types, titles, page navigation style, and KPI card presence.

Run:
    python scripts/find_pbi_titles.py
"""

import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

REPORTS = [
    {
        "label": "Sales Analytics Dashboard",
        "url": (
            "https://app.powerbi.com/view?r=eyJrIjoiMDc3MTM4Y2UtMTJhMi00YWYxLTgwMjEt"
            "NjJkNGExYzZlZGMwIiwidCI6IjY3ZDJkYjA2LTQ0YmQtNGMzMi1iN2Q5LTFhMjAyZjk4Zm"
            "M1YyIsImMiOjl9&pageName=ReportSection"
        ),
    },
    {
        "label": "Sales Performance Report",
        "url": (
            "https://app.powerbi.com/view?r=eyJrIjoiOTlkMzc3YzEtZmIyMS00MmRjLWIxYzgt"
            "MzVhNmM2MmViNTE1IiwidCI6ImI4YTczMWUzLTE2NjAtNDNiZS1hNzY3LTdiNGQ5NzBhODM0MCJ9"
        ),
    },
    {
        "label": "Super Store Sales Dashboard",
        "url": (
            "https://app.powerbi.com/view?r=eyJrIjoiYjIwOWFhZmItNjExOS00OTVkLTk0MjUt"
            "ZmZkZDk0ODNmYjlmIiwidCI6IjVhZGIyZTRjLTcxYWQtNDMxYS04MWZhLWQ0OGRhNmZlNmI0MCJ9"
        ),
    },
]

RENDER_TIMEOUT = 90_000


def wait_for_spinners(page, timeout_ms=60_000):
    """Wait for all Power BI loading spinners to disappear."""
    print("  ⏳ Waiting for spinners to finish loading...")
    try:
        # Wait for spinner to appear
        page.wait_for_selector("[data-testid='spinner']", state="visible", timeout=10_000)
        print("  ⏳ Spinner appeared — waiting for it to disappear...")
        # Wait for ALL spinners to be gone
        page.wait_for_selector("[data-testid='spinner']", state="hidden", timeout=timeout_ms)
        print("  ✅ Spinners gone — visuals should be rendered")
    except PwTimeout:
        print("  ℹ️  No spinner detected (fast render or already done)")
    # Extra buffer
    time.sleep(3)


def analyze_report(page, label):
    """Analyze a loaded Power BI report page."""
    print(f"\n{'─'*70}")
    print(f"  ANALYZING: {label}")
    print(f"{'─'*70}")

    # ── 1. Count visual types ──────────────────────────────────────────────
    visual_summary = page.evaluate("""() => {
        const vcs = document.querySelectorAll('visual-container');
        const summary = {
            total: vcs.length,
            withTitle: 0,
            noTitle: 0,
            types: {},
            allText: [],
            roleDescriptions: [],
            spinnerCount: 0,
        };
        for (const vc of vcs) {
            const cls = vc.className || '';
            const inner = vc.querySelector('.visualContainer');
            const innerCls = inner ? (inner.className || '') : '';

            if (innerCls.includes('noVisualTitle')) {
                summary.noTitle++;
            } else {
                summary.withTitle++;
            }

            // Determine visual type from inner classes
            let vcType = 'unknown';
            if (innerCls.includes('visual-image'))    vcType = 'image';
            if (innerCls.includes('visual-textbox'))  vcType = 'textbox';
            if (innerCls.includes('visual-card'))     vcType = 'card';
            if (innerCls.includes('visual-kpi'))      vcType = 'kpi';
            if (innerCls.includes('visual-lineChart')) vcType = 'lineChart';
            if (innerCls.includes('visual-barChart')) vcType = 'barChart';
            if (innerCls.includes('visual-columnChart')) vcType = 'columnChart';
            if (innerCls.includes('visual-pieChart')) vcType = 'pieChart';
            if (innerCls.includes('visual-table'))   vcType = 'table';
            if (innerCls.includes('visual-matrix'))  vcType = 'matrix';
            if (innerCls.includes('visual-slicer'))  vcType = 'slicer';

            summary.types[vcType] = (summary.types[vcType] || 0) + 1;

            // Collect non-empty text
            const txt = vc.innerText ? vc.innerText.trim() : '';
            if (txt && txt.length > 0 && txt.length < 200) {
                summary.allText.push({type: vcType, text: txt.substring(0, 100)});
            }

            // Collect aria-roledescription
            const innerDiv = vc.querySelector('[aria-roledescription]');
            const roleDesc = innerDiv ? innerDiv.getAttribute('aria-roledescription') : null;
            if (roleDesc && !summary.roleDescriptions.includes(roleDesc)) {
                summary.roleDescriptions.push(roleDesc);
            }
            
            // Count spinners
            if (vc.querySelector('[data-testid="spinner"]')) {
                summary.spinnerCount++;
            }
        }
        return summary;
    }""")

    print(f"\n  Visual containers: {visual_summary['total']}")
    print(f"  With title: {visual_summary['withTitle']} | No title: {visual_summary['noTitle']}")
    print(f"  Still loading (spinner): {visual_summary['spinnerCount']}")
    print(f"  Visual types: {visual_summary['types']}")
    print(f"  Role descriptions: {visual_summary['roleDescriptions']}")
    print(f"\n  Text found in visuals:")
    for item in visual_summary['allText'][:20]:
        print(f"    [{item['type']}] {item['text']!r}")

    # ── 2. Detailed look at ALL class combinations inside visual-container ──
    print(f"\n  All inner CSS classes (visual-type related):")
    classes_data = page.evaluate("""() => {
        const vcs = document.querySelectorAll('visual-container');
        const classSets = new Set();
        for (const vc of vcs) {
            const inner = vc.querySelector('.visualContainer');
            if (!inner) continue;
            const relevant = Array.from(inner.classList)
                .filter(c => c.startsWith('visual-') || c === 'noVisualTitle')
                .join(' | ');
            if (relevant) classSets.add(relevant);
        }
        return Array.from(classSets);
    }""")
    for cs in classes_data:
        print(f"    • {cs}")

    # ── 3. Page navigation style ───────────────────────────────────────────
    print(f"\n  Page navigation:")
    nav_data = page.evaluate("""() => {
        const info = {
            tabCount: document.querySelectorAll("[role='tab']").length,
            logoNavHTML: '',
            statusBarLeftPane: '',
            pageIndicator: '',
        };
        const logoNav = document.querySelector('logo-bar-navigation');
        if (logoNav) info.logoNavHTML = logoNav.innerText || '';
        const leftPane = document.querySelector('pbi-status-bar .leftPane');
        if (leftPane) info.statusBarLeftPane = leftPane.innerHTML.substring(0, 300);
        // Look for page indicator text like "1 / 3"
        const pageIndicators = document.querySelectorAll('[class*="pageNumber"], [class*="page-number"]');
        if (pageIndicators.length > 0) {
            info.pageIndicator = pageIndicators[0].innerText || '';
        }
        return info;
    }""")
    print(f"    [role='tab'] count: {nav_data['tabCount']}")
    print(f"    logo-bar-navigation text: {nav_data['logoNavHTML']!r}")
    print(f"    status-bar left pane: {nav_data['statusBarLeftPane'][:200]!r}")
    print(f"    page indicator: {nav_data['pageIndicator']!r}")

    # ── 4. Verdict ─────────────────────────────────────────────────────────
    types = visual_summary['types']
    has_charts  = any(t in types for t in ['lineChart', 'barChart', 'columnChart', 'pieChart', 'card', 'kpi', 'table', 'matrix'])
    has_spinner = visual_summary['spinnerCount'] > 0
    has_text    = bool(visual_summary['allText'])

    print(f"\n  VERDICT:")
    if has_charts:
        print(f"    ✅ Has chart/KPI/table visuals — GOOD for testing!")
    elif has_text and not visual_summary['types'].get('image', 0):
        print(f"    ⚠️  Has text content but no identified chart types — maybe generic visuals")
    elif visual_summary['types'].get('image', 0) == visual_summary['total']:
        print(f"    ❌ All visuals are images — not suitable for data extraction")
    else:
        print(f"    ⚠️  Mixed or unknown visual types — may have some extractable data")

    if has_spinner:
        print(f"    ⚠️  {visual_summary['spinnerCount']} visuals still loading — data may be incomplete")
    else:
        print(f"    ✅ No loading spinners — full render complete")

    return visual_summary


def inspect_report(url, label, p):
    """Open and analyze one Power BI report."""
    print(f"\n{'='*70}")
    print(f"  Opening: {label}")
    print(f"  URL: {url[:80]}...")
    print(f"{'='*70}")

    browser = p.chromium.launch(headless=True)  # headless for speed
    page    = browser.new_page(viewport={"width": 1600, "height": 900})

    try:
        page.goto(url, timeout=RENDER_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=RENDER_TIMEOUT)

        # Check for login redirect
        if "login.microsoftonline.com" in page.url or "login.live.com" in page.url:
            print("  ❌ Login required — not a public report")
            browser.close()
            return None
        print(f"  ✅ Publicly accessible!")

        # Wait for spinners
        wait_for_spinners(page)

        # Analyze
        result = analyze_report(page, label)
        browser.close()
        return result

    except Exception as exc:
        import traceback
        traceback.print_exc()
        browser.close()
        return None


def main():
    print("Evaluating all 3 public Power BI dashboards for testability...")
    print("Running in headless mode for speed.\n")

    with sync_playwright() as p:
        for report in REPORTS:
            inspect_report(report["url"], report["label"], p)

    print(f"\n{'='*70}")
    print("  DONE — see verdicts above to pick the best dashboard to test with.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
