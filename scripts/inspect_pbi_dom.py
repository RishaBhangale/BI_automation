"""
inspect_pbi_dom.py  — PBI DOM inspection (no-iframe version)

Power BI "Publish to web" (view?r=) reports render the entire report
directly in the main page DOM — there is NO iframe.

This script searches the outer page directly for PBI visual elements.

Run:
    python scripts/inspect_pbi_dom.py
"""

import time
from playwright.sync_api import sync_playwright

# Three public "Publish to web" reports
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

TIMEOUT    = 90_000   # ms
WAIT_SECS  = 30       # wait after networkidle — PBI renders JS components lazily


# ── Candidate selectors to probe on the OUTER page (no iframe) ────────────────
TAB_SELECTORS = [
    "li[role='tab']",
    "[role='tab']",
    "div[role='tab']",
    "[class*='pageNavigation'] li",
    "[class*='tab']",
    "[class*='page-tab']",
    "a[class*='tab']",
]

TITLE_SELECTORS = [
    "div[class*='visualTitle'] span",
    "div[class*='titleText']",
    "[class*='visualTitle']",
    "div[class*='title'] span",
    "[data-testid*='title']",
    "visual-container [class*='title']",
    "span[class*='label']",
    "[class*='cardCallout']",
    "[class*='kpi']",
    "h3",
    "h2",
]

VISUAL_CONTAINERS = [
    "visual-container",
    "[class*='visual-container']",
    "[class*='visualContainer']",
    "[class*='canvasVisual']",
    "[class*='pbiEmbed']",
    "[data-testid='visual-container']",
]


def inspect_report(url: str, label: str, p) -> None:
    print(f"\n{'='*70}")
    print(f"  Opening: {label}")
    print(f"{'='*70}")

    browser = p.chromium.launch(headless=False)
    page    = browser.new_page(viewport={"width": 1600, "height": 900})

    try:
        page.goto(url, timeout=TIMEOUT)
        print("  ⏳ Waiting for networkidle...")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT)
        print(f"  ⏳ Extra wait {WAIT_SECS}s for PBI JS render...")
        time.sleep(WAIT_SECS)

        current_url = page.url
        print(f"\n  Current URL: {current_url}")

        if "login.microsoftonline.com" in current_url or "login.live.com" in current_url:
            print("  ❌ Login required — not a public 'Publish to web' report.")
            browser.close()
            return
        print("  ✅ No login redirect!")

        # ── Check if there are any iframes now ───────────────────────────────
        iframe_count = page.locator("iframe").count()
        print(f"\n  <iframe> count in DOM: {iframe_count}")
        if iframe_count > 0:
            for i in range(min(iframe_count, 5)):
                el = page.locator("iframe").nth(i)
                print(f"    iframe[{i}] title='{el.get_attribute('title')}' "
                      f"src='{(el.get_attribute('src') or '')[:80]}'")

        # ── Probe page tabs on outer page ─────────────────────────────────────
        print("\n  --- Page Tabs ---")
        for sel in TAB_SELECTORS:
            count = page.locator(sel).count()
            if count > 0:
                print(f"  ✅ Selector '{sel}' → {count} element(s):")
                for i in range(min(count, 10)):
                    el   = page.locator(sel).nth(i)
                    txt  = (el.get_attribute("title") or el.inner_text()).strip()[:60]
                    cls  = (el.get_attribute("class") or "")[:80]
                    print(f"       text='{txt}'  class='{cls}'")
                break
            else:
                print(f"  ✗  '{sel}' → 0")

        # ── Probe visual containers ───────────────────────────────────────────
        print("\n  --- Visual Containers ---")
        for sel in VISUAL_CONTAINERS:
            count = page.locator(sel).count()
            print(f"  {'✅' if count > 0 else '✗ '} '{sel}' → {count}")
            if count > 0:
                el  = page.locator(sel).first
                cls = (el.get_attribute("class") or "")[:100]
                print(f"       first element class='{cls}'")

        # ── Probe visual titles on outer page ─────────────────────────────────
        print("\n  --- Visual Titles ---")
        found = False
        for sel in TITLE_SELECTORS:
            count = page.locator(sel).count()
            if count > 0:
                print(f"  ✅ Selector '{sel}' → {count} element(s):")
                for i in range(min(count, 15)):
                    el  = page.locator(sel).nth(i)
                    txt = el.inner_text().strip()[:80]
                    if txt:
                        print(f"       • '{txt}'")
                found = True
                break
        if not found:
            print("  (no visual titles found with any selector)")

        # ── Print inner HTML of body (first 5000 chars) ───────────────────────
        print("\n  --- Page body HTML (first 5000 chars) ---")
        body_html = page.locator("body").inner_html()[:5000]
        print(body_html)

        # ── Print all unique class names in the DOM ───────────────────────────
        print("\n  --- All unique class names containing 'visual' or 'pbi' (case-insensitive) ---")
        all_classes = page.evaluate("""() => {
            const classes = new Set();
            document.querySelectorAll('*').forEach(el => {
                el.className && String(el.className).split(' ').forEach(c => {
                    if (c && (c.toLowerCase().includes('visual') ||
                              c.toLowerCase().includes('pbi') ||
                              c.toLowerCase().includes('canvas') ||
                              c.toLowerCase().includes('report') ||
                              c.toLowerCase().includes('tile') ||
                              c.toLowerCase().includes('card'))) {
                        classes.add(c);
                    }
                });
            });
            return Array.from(classes).sort();
        }""")
        for cls in all_classes[:60]:
            print(f"    .{cls}")

        # ── Print all custom element names ────────────────────────────────────
        print("\n  --- Custom elements (tagName with hyphen) ---")
        custom_tags = page.evaluate("""() => {
            const tags = new Set();
            document.querySelectorAll('*').forEach(el => {
                if (el.tagName.includes('-')) tags.add(el.tagName.toLowerCase());
            });
            return Array.from(tags).sort();
        }""")
        for tag in custom_tags[:40]:
            print(f"    <{tag}>")

        print("\n  🔍 Browser staying open for 90 seconds — inspect DOM manually in DevTools.")
        print("  TIP: In DevTools Console, run:")
        print("       document.querySelectorAll('[class*=visual]')")
        print("       to see all visual elements.")
        time.sleep(90)

    except Exception as exc:
        import traceback
        traceback.print_exc()
    finally:
        browser.close()


def main():
    with sync_playwright() as p:
        report = REPORTS[0]
        inspect_report(report["url"], report["label"], p)


if __name__ == "__main__":
    main()
