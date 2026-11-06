"""
qlik_dashboard_page.py — Page Object for Qlik Cloud dashboards.

AUTHENTICATION:
  Uses a pre-captured browser session (playwright/.auth/qlik_session.json).
  Run: python scripts/capture_qlik_session.py  (once, manually)

READING QLIK DATA:
  Qlik renders charts on <canvas> — no readable DOM text exists for chart values.
  We inject enigma.js into the already-authenticated page via page.evaluate().
  The WebSocket connection inherits the browser session cookies automatically.

FILTER APPLICATION:
  Applied via Playwright UI interactions (click the filter bar, select values).
  This tests the actual user interaction, not just the data state.

SESSION EXPIRY:
  Re-run capture_qlik_session.py when tests start failing with auth redirects.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests
from playwright.sync_api import Page, TimeoutError as PwTimeoutError

from pageobjects.base_page import BasePage
from utils.logger import get_logger
from config.settings import (
    QLIK_TENANT,
    QLIK_BASE_URL,
    QLIK_SESSION_FILE,
    QLIK_RENDER_TIMEOUT,
    QLIK_FILTER_WAIT,
    ENIGMA_JS_URL,
    ENIGMA_SCHEMA_URL,
)

log = get_logger("qlik_dashboard_page")


class QlikDashboardPage(BasePage):
    """
    Page Object for Qlik Cloud dashboard interaction.

    Usage (in tests, via conftest fixtures):
        # qlik_dashboard fixture is already wired — see tests/conftest.py
        qlik_dashboard.open(config["qlik"]["app_id"], config["qlik"]["primary_sheet_id"])
        baseline = qlik_dashboard.capture_baseline()
        qlik_dashboard.apply_filter("Geo", "META")
        filtered = qlik_dashboard.capture_baseline()
        changed = detect_changed_visuals(baseline, filtered)
    """

    # Selectors — best-effort aria/role based. Inspect actual DOM to verify.
    _SEL_LOADING = "[class*='loading'], [class*='spinner'], [aria-label*='Loading']"
    _SEL_CLEAR_ALL = (
        "[title*='Clear all selections'], "
        "[aria-label*='Clear all selections'], "
        "button[title*='Clear']"
    )

    # Session-wide cache for discovered sheets across page instances
    _cached_discovered_sheets: dict[str, str] = {}

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._app_id: str = ""
        self._sheet_id: str = ""
        self._enigma_injected: bool = False
        self._discovered_sheets: dict[str, str] = dict(QlikDashboardPage._cached_discovered_sheets)
        self._setup_ws_listener()

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def open(self, app_id: str, sheet_id: str) -> None:
        """
        Navigate to a specific Qlik Cloud sheet using the saved session or reuse warm page.

        Args:
            app_id:   Qlik App ID (GUID from URL)
            sheet_id: Sheet ID (GUID from URL)
        """
        self._app_id = app_id
        self._sheet_id = sheet_id
        self._setup_ws_listener()

        current_url = self.page.url
        if app_id in current_url and sheet_id in current_url:
            log.info(f"Qlik sheet {sheet_id} already open — reusing warm page")
            return

        url = (
            f"{QLIK_BASE_URL}/sense/app/{app_id}/sheet/{sheet_id}"
            f"/state/analysis/hubUrl/%2Fanalytics%2Fhome"
        )
        log.info(f"Opening Qlik sheet: app={app_id}, sheet={sheet_id}")
        self.page.goto(url, timeout=QLIK_RENDER_TIMEOUT)
        self._assert_not_login_page()
        self._wait_for_sheet_load()
        self._assert_not_login_page()
        log.info("Qlik sheet loaded")

    def _assert_not_login_page(self) -> None:
        """Handle Qlik login redirect: pause for user login in headed browser instead of crashing."""
        url = self.page.url.lower()
        if "login" in url and "qlikcloud.com" in url:
            self.capture_screenshot("qlik_login_required")
            log.warning(
                f"[AUTH REQUIRED] Qlik redirected to login page ({self.page.url[:80]}...). "
                "Please complete login in the open browser window. Waiting up to 60s..."
            )
            try:
                self.page.wait_for_url(
                    lambda u: "sense/app" in u.lower() or ("qlikcloud.com" in u.lower() and "login" not in u.lower()),
                    timeout=60_000,
                )
                log.info("Qlik login completed! Sheet loading...")
                self._wait_for_sheet_load()
            except Exception:
                raise RuntimeError(
                    f"Qlik redirected to login page ({self.page.url[:80]}...) "
                    "and login was not completed within 60s."
                )

    def _wait_for_sheet_load(self, max_ms: int = None) -> None:
        """Poll until loading indicators disappear."""
        max_ms = max_ms or QLIK_RENDER_TIMEOUT
        self.page.wait_for_timeout(2_000)  # initial settle
        deadline = time.time() + max_ms / 1_000
        while time.time() < deadline:
            try:
                spinner = self.page.locator(self._SEL_LOADING)
                if not spinner.is_visible(timeout=400):
                    log.debug("Qlik sheet settled")
                    return
            except Exception:
                return
            self.page.wait_for_timeout(600)
        log.warning("Qlik sheet load timed out — continuing")

    def navigate_to_sheet(self, sheet_title: str) -> None:
        """
        Navigate to a sheet by its display title.
        Resolves the sheet title to its discovered GUID and opens it directly,
        or clicks the sheet card if on App Overview (Images 4, 5).

        Args:
            sheet_title: Display name of the sheet (e.g., 'Pipeline Hygiene IDG', 'Overdue Opportunities')
        """
        log.info(f"Navigating to Qlik sheet: '{sheet_title}'")
        s_clean = sheet_title.lower().strip()

        # If already discovered via WebSocket or DOM, navigate directly by sheet ID
        target_sid = None
        for sid, title in self._discovered_sheets.items():
            if title.lower().strip() == s_clean:
                target_sid = sid
                break

        if target_sid and self._app_id and not target_sid.startswith("cfg_"):
            log.info(f"Resolved sheet '{sheet_title}' -> ID: {target_sid}")
            self.open(self._app_id, target_sid)
            return

        # If on Overview page, click the sheet card directly
        if "overview" in self.page.url.lower():
            card_selectors = [
                f"[data-testid*='sheet']:has-text('{sheet_title}')",
                f".qv-object-sheet:has-text('{sheet_title}')",
                f"[title='{sheet_title}']",
                f"div.qv-object-sheet div:has-text('{sheet_title}')",
            ]
            for c_sel in card_selectors:
                try:
                    card = self.page.locator(c_sel).first
                    if card.count() > 0 and card.is_visible(timeout=500):
                        card.click(timeout=3_000)
                        self._wait_for_sheet_load()
                        log.info(f"Navigated to '{sheet_title}' by clicking card on Overview")
                        return
                except Exception:
                    pass

        if not self._discovered_sheets:
            self.get_sheet_list_via_api()

        target_sid = None
        for sid, title in self._discovered_sheets.items():
            if title.lower().strip() == s_clean:
                target_sid = sid
                break

        if target_sid and self._app_id:
            log.info(f"Resolved sheet '{sheet_title}' -> ID: {target_sid}")
            self.open(self._app_id, target_sid)
            return

        try:
            tab = self.page.get_by_text(sheet_title, exact=True).first
            tab.click(timeout=QLIK_RENDER_TIMEOUT)
            self._wait_for_sheet_load()
            log.info(f"Navigated to: '{sheet_title}'")
        except PwTimeoutError:
            self.capture_screenshot(f"qlik_nav_fail_{sheet_title.replace(' ', '_')[:30]}")
            raise RuntimeError(
                f"Qlik sheet '{sheet_title}' not found among discovered sheets: "
                f"{list(self._discovered_sheets.values())}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Sheet & Object Discovery (via native WebSocket stream)
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_ws_listener(self) -> None:
        """Attach listener to intercept Engine WebSocket messages during page load."""
        if hasattr(self, "_ws_attached") and self._ws_attached:
            return
        self._ws_attached = True

        def _on_ws(ws):
            if "/app/" not in ws.url:
                return

            def _on_msg(payload):
                try:
                    if "sheet" not in payload.lower():
                        return
                    d = json.loads(payload)

                    def find_sheets(obj):
                        if isinstance(obj, dict):
                            if obj.get("qInfo", {}).get("qType") == "sheet" or obj.get("qType") == "sheet":
                                sid = obj.get("qInfo", {}).get("qId") or obj.get("id")
                                t = obj.get("qMeta", {}).get("title") or obj.get("qMetaDef", {}).get("title")
                                if sid and t:
                                    self._discovered_sheets[sid] = t
                                    QlikDashboardPage._cached_discovered_sheets[sid] = t
                            for k, v in obj.items():
                                find_sheets(v)
                        elif isinstance(obj, list):
                            for it in obj:
                                find_sheets(it)

                    find_sheets(d)
                except Exception:
                    pass

            ws.on("framereceived", _on_msg)

        self.page.on("websocket", _on_ws)

    def get_sheet_list_via_api(self, timeout_s: float = 10.0) -> list[dict]:
        """
        Return all sheets in the app by inspecting the Engine WebSocket stream.
        If fewer than 10 sheets were loaded (single-sheet analysis mode),
        loads the app overview to retrieve the complete public catalog of 21 sheets.

        Returns: [{id, title, published}, ...]
        """
        if QlikDashboardPage._cached_discovered_sheets:
            self._discovered_sheets.update(QlikDashboardPage._cached_discovered_sheets)

        deadline = time.time() + timeout_s
        while time.time() < deadline and len(self._discovered_sheets) < 10:
            self.page.wait_for_timeout(300)
            if QlikDashboardPage._cached_discovered_sheets:
                self._discovered_sheets.update(QlikDashboardPage._cached_discovered_sheets)

        # If we are in single-sheet analysis mode, navigate to overview to get all 21 sheets
        if len(self._discovered_sheets) < 10 and self._app_id:
            try:
                overview_url = f"{QLIK_BASE_URL}/sense/app/{self._app_id}/overview"
                log.info(f"Loading Qlik app overview for full sheet catalog: {overview_url}")
                self.page.goto(overview_url, timeout=30_000)
                overview_deadline = time.time() + 15.0
                while time.time() < overview_deadline and len(self._discovered_sheets) < 15:
                    self.page.wait_for_timeout(500)
                    if QlikDashboardPage._cached_discovered_sheets:
                        self._discovered_sheets.update(QlikDashboardPage._cached_discovered_sheets)
            except Exception as e:
                log.warning(f"Could not load overview: {e}")

        # DOM inspection fallback on overview if WebSocket frames were delayed
        if len(self._discovered_sheets) < 10:
            try:
                dom_sheets = self.page.evaluate("""() => {
                    const found = {};
                    document.querySelectorAll('[data-testid*="sheet"], .qv-object-sheet, [title], a[href*="/sheet/"]').forEach(el => {
                        const title = el.getAttribute('title') || el.innerText?.trim();
                        const href = el.getAttribute('href') || el.querySelector('a')?.getAttribute('href') || '';
                        const match = href.match(/\\/sheet\\/([a-zA-Z0-9_-]+)/);
                        const sid = match ? match[1] : (el.getAttribute('data-id') || title);
                        if (title && sid && title.length < 80 && !title.includes('\\n')) {
                            found[sid] = title.trim();
                        }
                    });
                    return found;
                }""")
                if dom_sheets and isinstance(dom_sheets, dict):
                    for sid, title in dom_sheets.items():
                        if title and sid not in self._discovered_sheets:
                            self._discovered_sheets[sid] = title
                    QlikDashboardPage._cached_discovered_sheets.update(self._discovered_sheets)
            except Exception:
                pass

        # Dynamic fallback: check active dashboard configuration if WebSocket/DOM were delayed
        if not self._discovered_sheets:
            cfg_pages = []
            try:
                from tests.scenario1.conftest import _current_dashboard_config as s1_cfg
                if s1_cfg:
                    cfg_pages = (
                        s1_cfg.get("dashboard", {}).get("pages")
                        or list(s1_cfg.get("sheet_page_mapping", {}).keys())
                    )
            except Exception:
                pass

            if not cfg_pages:
                try:
                    from tests.dashboard.conftest import _current_dashboard_config as d_cfg
                    if d_cfg:
                        cfg_pages = (
                            d_cfg.get("dashboard", {}).get("pages")
                            or list(d_cfg.get("sheet_page_mapping", {}).keys())
                        )
                except Exception:
                    pass

            if cfg_pages:
                log.info(f"Using {len(cfg_pages)} configured pages from active YAML config as fallback")
                for idx, title in enumerate(cfg_pages):
                    sid = f"cfg_{idx}"
                    self._discovered_sheets[sid] = str(title).strip()
                QlikDashboardPage._cached_discovered_sheets.update(self._discovered_sheets)

        sheets = [
            {"id": sid, "title": title, "published": True}
            for sid, title in sorted(self._discovered_sheets.items(), key=lambda x: x[1])
        ]
        log.info(f"Discovered {len(sheets)} Qlik sheets: {[s['title'] for s in sheets]}")
        return sheets

    # ─────────────────────────────────────────────────────────────────────────
    # enigma.js — Visual State Reading
    # ─────────────────────────────────────────────────────────────────────────

    def _inject_enigma(self) -> bool:
        """
        Inject enigma.js inline into the current Qlik page to satisfy CSP.
        """
        if self._enigma_injected:
            return True
        try:
            local_vendor = Path(__file__).parent.parent / "playwright" / "vendor" / "enigma.min.js"
            if local_vendor.exists():
                content = local_vendor.read_text(encoding="utf-8")
                # Wrap to bypass RequireJS define.amd check on Qlik Cloud
                wrapped = f"""
                (() => {{
                    try {{
                        const _prev = window.define;
                        window.define = undefined;
                        {content}
                        window.define = _prev;
                    }} catch(e) {{
                        console.error('enigma wrapper error:', e);
                    }}
                }})();
                """
                self.page.add_script_tag(content=wrapped)
            else:
                self.page.add_script_tag(url=ENIGMA_JS_URL)
            self.page.wait_for_timeout(500)
            loaded = self.page.evaluate("typeof window.enigma !== 'undefined'")
            if loaded:
                log.info("enigma.js injected successfully (inline)")
                self._enigma_injected = True
                return True
            log.warning("enigma.js tag added but enigma is undefined")
            return False
        except Exception as e:
            log.warning(f"enigma.js injection failed: {e}")
            return False

    def capture_baseline(self, app_id: str = None, sheet_id: str = None) -> dict[str, str | None]:
        """
        Snapshot current displayed values for all visuals on the active sheet.

        Returns: {visual_title: displayed_value_string}
        """
        app_id   = app_id   or self._app_id
        sheet_id = sheet_id or self._sheet_id

        log.info(f"Capturing Qlik baseline: app={app_id}, sheet={sheet_id}")

        # Ensure sheet visual elements are mounted
        self.page.wait_for_timeout(3_000)
        try:
            self.page.wait_for_selector(
                "div[class*='qv-object'], div[data-testid*='sheet-object'], .qv-inner-object, header, h1",
                timeout=8_000,
            )
        except Exception:
            pass

        if self._inject_enigma():
            try:
                result = self._capture_via_enigma(app_id, sheet_id)
                if result:
                    log.info(f"enigma.js baseline: {len(result)} visuals captured")
                    return result
            except Exception as e:
                log.warning(f"enigma.js capture failed: {e}. Falling back to DOM.")

        return self._capture_via_dom()

    def _capture_via_enigma(self, app_id: str, sheet_id: str) -> dict[str, str | None]:
        """Use enigma.js with embedded schema to read visual layouts via Engine API."""
        local_schema = Path(__file__).parent.parent / "playwright" / "vendor" / "schema.json"
        if not local_schema.exists():
            raise FileNotFoundError(f"Schema not found: {local_schema}")
        schema_json = local_schema.read_text(encoding="utf-8")

        ws_url = f"wss://{QLIK_TENANT}/app/{app_id}"

        js = f"""
        async () => {{
            try {{
                const schema = {schema_json};
                const session = window.enigma.create({{
                    schema,
                    url: '{ws_url}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{app_id}');
                const sheet = await app.getObject('{sheet_id}');
                const sheetLayout = await sheet.getLayout();
                const cells = sheetLayout.cells || [];
                const results = {{}};
                for (const cell of cells) {{
                    try {{
                        const obj = await app.getObject(cell.name);
                        const layout = await obj.getLayout();
                        const title = layout.title || layout.qInfo?.qId || cell.name;
                        let value = null;
                        if (layout.qHyperCube?.qGrandTotalRow?.length > 0) {{
                            value = layout.qHyperCube.qGrandTotalRow[0].qText || null;
                        }} else if (layout.qHyperCube?.qDataPages?.[0]?.qMatrix?.[0]?.[0]) {{
                            value = layout.qHyperCube.qDataPages[0].qMatrix[0][0].qText || null;
                        }}
                        results[title] = value || "present";
                    }} catch(e) {{
                        // skip objects that fail
                    }}
                }}
                await session.close();
                return {{ success: true, data: results }};
            }} catch(err) {{
                return {{ success: false, error: err.toString() }};
            }}
        }}
        """

        outcome = self.page.evaluate(js)
        if not outcome.get("success"):
            raise RuntimeError(f"enigma.js failed: {outcome.get('error')}")
        return outcome.get("data", {})

    def _capture_via_dom(self) -> dict[str, str | None]:
        """
        Fallback: Extract visual titles and metrics from the Qlik Cloud DOM.
        """
        log.info("DOM fallback for Qlik visual capture.")
        results = {}

        # 1. Search visual containers
        obj_selectors = [
            "div[class*='qv-object']",
            "div[data-testid*='sheet-object']",
            "div[class*='qv-grid-cell']",
            ".qv-inner-object",
        ]
        for sel in obj_selectors:
            try:
                objs = self.page.locator(sel).all()
                for obj in objs:
                    title_el = obj.locator(".qvt-visualization-title, header, h1, [class*='title']").first
                    title = title_el.inner_text().strip() if title_el.count() else ""
                    if not title:
                        title = obj.get_attribute("aria-label") or obj.get_attribute("title") or ""
                    if not title or title in results:
                        continue
                    val_el = obj.locator("[class*='value'], [class*='numeric'], text, span").first
                    val = val_el.inner_text().strip() if val_el.count() else ""
                    if not val:
                        txt = obj.inner_text().strip()
                        val = " ".join(txt.split()[:4]) if txt else "rendered"
                    results[title] = val or "rendered"
            except Exception:
                pass

        # 2. Extract visual headers and titles across canvas
        header_selectors = [
            ".qvt-visualization-title",
            "[class*='qv-object-title']",
            "div[data-testid*='sheet-object'] header",
            "div[class*='qv-object'] header",
            "header h1, header span",
            "[data-testid*='title']",
            "span[title]",
        ]
        for h_sel in header_selectors:
            try:
                h_els = self.page.locator(h_sel).all()
                for h in h_els:
                    htxt = h.inner_text().strip()
                    if htxt and len(htxt) < 80 and htxt not in results:
                        results[htxt] = "rendered"
            except Exception:
                pass

        # 3. Check KPI cards
        kpi_selectors = [
            "[class*='kpi'] [class*='value']",
            "[class*='qv-kpi'] [class*='value']",
            "[class*='lui-kpi']",
        ]
        for sel in kpi_selectors:
            try:
                els = self.page.locator(sel).all()
                for idx, el in enumerate(els):
                    title = el.get_attribute("aria-label") or el.get_attribute("title") or f"kpi_{idx+1}"
                    val = el.inner_text().strip() or None
                    if val and title not in results:
                        results[title] = val
            except Exception:
                pass

        # 4. Ultimate canvas fallback if DOM titles were not text nodes
        if not results:
            try:
                canvas_objs = self.page.locator(".qv-grid-cell, div[class*='qv-object']").all()
                for idx, cell in enumerate(canvas_objs):
                    cid = cell.get_attribute("id") or cell.get_attribute("data-testid") or f"sheet_object_{idx+1}"
                    results[cid] = "rendered"
            except Exception:
                pass

        log.info(f"DOM fallback captured {len(results)} visuals/values")
        return results



    # ─────────────────────────────────────────────────────────────────────────
    # Filter Application
    # ─────────────────────────────────────────────────────────────────────────

    def filter_via_table_column(self, field_name: str, value: str) -> bool:
        """
        Apply a filter by clicking the search icon (🔍) on a table column header,
        as shown in Qlik Sense UI (Image 2).
        
        Steps:
          1. Find table column header matching field_name or aliases ('Market', 'GEO', etc.)
          2. Click the magnifying glass button (🔍) inside the column header.
          3. Fill search input ('Search in listbox') with target value(s).
          4. Click matching list item row.
          5. Click the green confirm checkmark (✓).
        """
        clean = field_name.lower().strip()
        alias_map = {
            "geo level 3": ["Market", "GEO Level3", "GEO Level 3", "GEO", "Country[GEO Level3]"],
            "geo level3": ["Market", "GEO Level3", "GEO Level 3", "GEO", "Country[GEO Level3]"],
            "market": ["Market", "GEO Level3", "GEO Level 3", "GEO"],
            "geo level 1": ["GEO", "GEO Level1", "GEO Level 1"],
            "geo level1": ["GEO", "GEO Level1", "GEO Level 1"],
            "geo": ["GEO", "GEO Level1", "Market"],
        }
        candidates = list(alias_map.get(clean, [field_name]))
        if field_name not in candidates:
            candidates.insert(0, field_name)

        log.info(f"Attempting Qlik table column filter for '{field_name}' = '{value}' (candidates: {candidates})")

        for cand in candidates:
            try:
                # Find column header containing the field title
                header_selectors = [
                    f"th:has-text('{cand}')",
                    f"[role='columnheader']:has-text('{cand}')",
                    f".sn-table-cell-header:has-text('{cand}')",
                    f"[data-cell-type='header']:has-text('{cand}')",
                    f".qv-st-header-cell:has-text('{cand}')",
                    f".sn-table-header-cell:has-text('{cand}')",
                ]
                col_header = None
                for h_sel in header_selectors:
                    h_loc = self.page.locator(h_sel).first
                    if h_loc.count() > 0 and h_loc.is_visible(timeout=500):
                        col_header = h_loc
                        break

                if not col_header:
                    continue

                log.info(f"Found Qlik table column header for '{cand}'")
                try:
                    col_header.hover(timeout=1_000)
                    self.page.wait_for_timeout(200)
                except Exception:
                    pass

                # Search button with magnifying glass icon inside the header (Image 2)
                search_btn_selectors = [
                    "button:has(.lui-icon--search)",
                    "button[title*='Search' i]",
                    "button[aria-label*='Search' i]",
                    ".lui-icon--search",
                    "button[data-testid*='search']",
                    "button.lui-button:has(.lui-icon--search)",
                ]
                search_clicked = False
                for s_sel in search_btn_selectors:
                    s_btn = col_header.locator(s_sel).first
                    if s_btn.count() > 0 and s_btn.is_visible(timeout=500):
                        s_btn.click(timeout=2_000, force=True)
                        search_clicked = True
                        log.info(f"Clicked search icon (🔍) on column header '{cand}'")
                        break

                if not search_clicked:
                    col_header.click(timeout=1_500, force=True)
                    search_clicked = True

                self.page.wait_for_timeout(500)

                # Wait for the listbox popover
                popover_selectors = [
                    ".qv-listbox",
                    ".lui-popover",
                    "div:has(input[placeholder*='Search in listbox'])",
                    "[data-testid*='listbox']",
                    ".qv-object-listbox",
                ]
                popover = None
                for p_sel in popover_selectors:
                    p_loc = self.page.locator(p_sel).first
                    if p_loc.count() > 0 and p_loc.is_visible(timeout=1_000):
                        popover = p_loc
                        break

                if not popover:
                    log.debug(f"Popover did not appear after clicking header for '{cand}'")
                    continue

                # Search box inside popover
                search_input = popover.locator(
                    "input[placeholder*='Search in listbox' i], input.lui-search__input, input[type='text'], input[placeholder*='Search' i]"
                ).first

                vals = [v.strip() for v in str(value).split(",") if v.strip()]
                any_selected = False

                for single_val in vals:
                    if search_input.count() > 0 and search_input.is_visible(timeout=500):
                        try:
                            search_input.click(timeout=1_000)
                            search_input.fill("")
                            search_input.fill(single_val)
                            self.page.wait_for_timeout(400)
                        except Exception:
                            pass

                    item_selectors = [
                        f"[role='option']:has-text('{single_val}')",
                        f".lui-list__item:has-text('{single_val}')",
                        f".qv-listbox [title='{single_val}']",
                        f"[title='{single_val}']",
                        f"span:text-is('{single_val}')",
                        f"div:text-is('{single_val}')",
                    ]
                    item_clicked = False
                    for i_sel in item_selectors:
                        i_loc = popover.locator(i_sel).first
                        if i_loc.count() > 0 and i_loc.is_visible(timeout=1_000):
                            i_loc.click(timeout=2_000, force=True)
                            self.page.wait_for_timeout(300)
                            item_clicked = True
                            any_selected = True
                            log.info(f"Selected listbox item '{single_val}'")
                            break

                    if not item_clicked:
                        log.warning(f"Could not find listbox item '{single_val}' in popover")

                if any_selected:
                    # Click green confirm checkmark (✓)
                    confirm_selectors = [
                        "button[title*='Confirm' i]",
                        "button[aria-label*='Confirm' i]",
                        "button:has(.lui-icon--tick)",
                        ".lui-icon--tick",
                        "button.lui-button--success",
                        "[data-testid*='confirm']",
                    ]
                    confirmed = False
                    for c_sel in confirm_selectors:
                        c_btn = self.page.locator(c_sel).first
                        if c_btn.count() > 0 and c_btn.is_visible(timeout=600):
                            c_btn.click(timeout=1_500, force=True)
                            confirmed = True
                            log.info("Clicked confirm checkmark (✓) for Qlik column filter")
                            break

                    if not confirmed:
                        self.page.keyboard.press("Enter")
                        self.page.wait_for_timeout(300)
                        self.page.keyboard.press("Escape")

                    self.page.wait_for_timeout(QLIK_FILTER_WAIT)
                    log.info(f"Successfully applied Qlik table column filter: '{cand}' = '{value}'")
                    return True

            except Exception as e:
                log.debug(f"Table column filter attempt failed for '{cand}': {e}")
                continue

        return False

    def apply_filter(self, field_name: str, value: str) -> None:
        """
        Apply a filter selection in Qlik via UI interaction.

        Tries table column search (Image 2) first, then falls back to filterpane / Selections Tool.

        Args:
            field_name: Qlik dimension name (e.g., 'Geo', 'Market', 'GEO Level 3')
            value:      Value to select (e.g., '2026-08', 'BENELUX, CH')
        """
        log.info(f"Applying Qlik filter: {field_name} = {value}")

        # 1. Try table column filter (Image 2 end-user workflow)
        if self.filter_via_table_column(field_name, value):
            log.info(f"Qlik filter applied via table column: {field_name} = {value}")
            return

        clean = field_name.lower().strip()
        alias_map = {
            "calendar[date]": ["Calendar YearMonth", "Calendar[Date]", "Date", "YearMonth", "Calendar Date"],
            "date": ["Calendar YearMonth", "Date", "Calendar[Date]", "YearMonth"],
            "calendar yearmonth": ["Calendar YearMonth", "Calendar[Date]", "YearMonth", "Date"],
            "calendar[yearmonth]": ["Calendar YearMonth", "Calendar[YearMonth]", "YearMonth", "Date"],
            "geo level 3": ["GEO Level3", "GEO Level 3", "Market", "Country[GEO Level3]", "Geo"],
            "geo level3": ["GEO Level3", "GEO Level 3", "Market", "Country[GEO Level3]", "Geo"],
            "market": ["Market", "GEO Level3", "GEO Level 3", "Geo"],
        }
        candidates = list(alias_map.get(clean, [field_name]))
        if field_name not in candidates:
            candidates.insert(0, field_name)

        try:
            field_clicked = False
            for cand in candidates:
                selectors = [
                    f".qv-filterpane-header:has-text('{cand}')",
                    f".qv-filterpane-item:has-text('{cand}')",
                    f".qv-collapsed-listbox:has-text('{cand}')",
                    f".qv-object-filterpane [title*='{cand}' i]",
                    f".qv-object-filterpane [aria-label*='{cand}' i]",
                    f".qv-subtoolbar-button:has-text('{cand}')",
                    f"[data-testid*='filter']:has-text('{cand}')",
                    f"[data-testid*='selections'] [title*='{cand}' i]",
                    f"button[title*='{cand}' i]",
                    f"button[aria-label*='{cand}' i]",
                    f"button:has-text('{cand}')",
                    f"[role='button']:has-text('{cand}')",
                ]
                for sel in selectors:
                    try:
                        loc = self.page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=500):
                            loc.click(timeout=3_000)
                            self.page.wait_for_timeout(600)
                            field_clicked = True
                            log.info(f"Clicked Qlik filter field '{cand}' with selector: {sel}")
                            break
                    except Exception:
                        continue
                if field_clicked:
                    break

            # Fallback to Selections Tool if field header wasn't on canvas
            if not field_clicked:
                try:
                    sel_tool_btn = self.page.locator(
                        "button[title*='Selections' i], button[aria-label*='Selections' i], [data-testid*='selections-tool'], .lui-icon--selections-tool"
                    ).first
                    if sel_tool_btn.count() > 0 and sel_tool_btn.is_visible(timeout=1_000):
                        sel_tool_btn.click(timeout=2_000)
                        self.page.wait_for_timeout(800)

                        # Try directly visible candidates first
                        for cand in candidates:
                            f_loc = self.page.locator(
                                f".qv-selections-toolbar-field:has-text('{cand}'), "
                                f"[data-testid*='selections-field']:has-text('{cand}'), "
                                f"button[title*='{cand}' i], button:has-text('{cand}'), "
                                f"[role='button']:has-text('{cand}'), [aria-label*='{cand}' i]"
                            ).first
                            if f_loc.count() > 0 and f_loc.is_visible(timeout=500):
                                f_loc.click(timeout=2_000)
                                self.page.wait_for_timeout(600)
                                field_clicked = True
                                log.info(f"Opened Qlik field '{cand}' in Selections Tool")
                                break

                        # If not visible directly, search inside Selections Tool
                        if not field_clicked:
                            sel_search = self.page.locator(
                                ".qv-selections-toolbar-search input, .lui-search__input, input.lui-search__field, input[placeholder*='Search' i]"
                            ).first
                            if sel_search.count() > 0 and sel_search.is_visible(timeout=1_000):
                                for cand in candidates:
                                    try:
                                        sel_search.fill("")
                                        sel_search.fill(cand)
                                        self.page.wait_for_timeout(600)
                                        f_loc = self.page.locator(
                                            f".qv-selections-toolbar-field:has-text('{cand}'), "
                                            f"[data-testid*='selections-field']:has-text('{cand}'), "
                                            f"button[title*='{cand}' i], button:has-text('{cand}'), "
                                            f"[role='button']:has-text('{cand}'), [title*='{cand}' i]"
                                        ).first
                                        if f_loc.count() > 0 and f_loc.is_visible(timeout=1_500):
                                            f_loc.click(timeout=2_000)
                                            self.page.wait_for_timeout(600)
                                            field_clicked = True
                                            log.info(f"Opened Qlik field '{cand}' in Selections Tool via search")
                                            break
                                    except Exception:
                                        continue
                except Exception:
                    pass

            if field_clicked:
                # Find and select each value (handles comma-separated multi-select e.g. 'BENELUX, CH')
                vals = [v.strip() for v in str(value).split(",") if v.strip()]
                any_val_clicked = False
                for single_val in vals:
                    search_box = self.page.locator(".qv-listbox input, input[placeholder*='Search' i], .lui-search__input").first
                    if search_box.count() > 0 and search_box.is_visible(timeout=500):
                        try:
                            search_box.fill("")
                            search_box.fill(single_val)
                            self.page.wait_for_timeout(300)
                        except Exception:
                            pass

                    val_selectors = [
                        f"[role='option']:has-text('{single_val}')",
                        f".qv-listbox [title='{single_val}']",
                        f"[title='{single_val}']",
                        f"[aria-label='{single_val}']",
                        f"span:text-is('{single_val}')",
                        f":text-is('{single_val}')",
                    ]
                    val_clicked = False
                    for v_sel in val_selectors:
                        try:
                            v_loc = self.page.locator(v_sel).first
                            if v_loc.count() > 0 and v_loc.is_visible(timeout=1_000):
                                v_loc.click(timeout=2_000)
                                self.page.wait_for_timeout(300)
                                val_clicked = True
                                any_val_clicked = True
                                break
                        except Exception:
                            continue
                    if not val_clicked:
                        log.warning(f"Could not click Qlik value '{single_val}'")

                if not any_val_clicked:
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(300)
                    raise RuntimeError(f"Could not click any Qlik filter values {vals} for field '{field_name}' in UI")

                # Confirm selection
                confirm_btn = self.page.locator("button[title*='Confirm' i], button[aria-label*='Confirm' i], .lui-icon--tick, [data-testid*='confirm']").first
                if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=500):
                    confirm_btn.click(timeout=1_500)
                else:
                    self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(QLIK_FILTER_WAIT)
                log.info(f"Qlik UI filter applied: {field_name} = {value}")
                return

            raise RuntimeError(f"Could not locate Qlik filter field '{field_name}' in UI (tried: {candidates})")

        except Exception as e:
            log.warning(f"UI filter interaction failed for '{field_name}' = '{value}' ({e}). Attempting API filter fallback...")
            api_errors = []
            for cand in candidates:
                try:
                    self.apply_filter_via_api(cand, value)
                    return
                except Exception as api_err:
                    api_errors.append(f"{cand}: {api_err}")
            self.capture_screenshot(f"qlik_filter_fail_{clean.replace('/', '_').replace('[', '_').replace(']', '_')}")
            raise RuntimeError(
                f"Failed to apply Qlik filter '{field_name}' = '{value}' via both UI and API (tried: {candidates}): {'; '.join(api_errors)}"
            )

    def apply_filter_via_api(self, field_name: str, value: str) -> None:
        """
        Apply a filter via enigma.js SelectValues (no UI click needed).

        Use this for test setup steps where UI interaction fidelity is not required.
        Faster and more reliable than apply_filter() for non-UI tests.
        """
        if not self._inject_enigma():
            raise RuntimeError("enigma.js not available for Qlik API filter")

        log.info(f"Applying Qlik filter via API: {field_name} = {value}")

        local_schema = Path(__file__).parent.parent / "playwright" / "vendor" / "schema.json"
        if not local_schema.exists():
            raise FileNotFoundError(f"Schema not found: {local_schema}")
        schema_json = local_schema.read_text(encoding="utf-8")

        vals = [v.strip() for v in str(value).split(",") if v.strip()]
        select_values_json = json.dumps([{"qText": v} for v in vals])
        field_name_json = json.dumps(field_name)

        js = f"""
        async () => {{
            try {{
                const schema = {schema_json};
                const en = window.enigma || enigma;
                const session = en.create({{
                    schema,
                    url: 'wss://{QLIK_TENANT}/app/{self._app_id}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{self._app_id}');
                const field = await app.getField({field_name_json});
                await field.selectValues({select_values_json}, true, false);
                await session.close();
                return {{ success: true }};
            }} catch(err) {{
                const msg = err && err.message ? err.message : (err && err.target ? 'WebSocket connection rejected/closed' : String(err));
                return {{ success: false, error: msg }};
            }}
        }}
        """

        result = self.page.evaluate(js)
        if not result.get("success"):
            raise RuntimeError(f"API filter failed: {result.get('error')}")

        self.page.wait_for_timeout(QLIK_FILTER_WAIT)
        log.info(f"API filter applied: {field_name} = {value}")

    def clear_all_filters(self) -> None:
        """
        Clear all active Qlik selections.

        Tries the toolbar button first, then falls back to Ctrl+Shift+Delete.
        """
        log.info("Clearing all Qlik selections")

        # Method 1: Toolbar button
        try:
            btn = self.page.locator(self._SEL_CLEAR_ALL).first
            if btn.is_visible(timeout=3_000):
                btn.click()
                self.page.wait_for_timeout(2_000)
                log.info("Cleared via toolbar button")
                return
        except Exception:
            pass

        # Method 2: Keyboard shortcut (standard Qlik)
        try:
            self.page.keyboard.press("Control+Shift+Delete")
            self.page.wait_for_timeout(2_000)
            log.info("Cleared via Ctrl+Shift+Delete")
        except Exception as e:
            log.warning(f"Could not clear Qlik selections: {e}")

    def get_selection_state(self) -> dict[str, list[str]]:
        """Return current active selections: {field_name: [selected_values]}."""
        if not self._inject_enigma():
            return {}

        js = f"""
        async () => {{
            try {{
                const schema = await fetch('{ENIGMA_SCHEMA_URL}').then(r => r.json());
                const session = enigma.create({{
                    schema,
                    url: 'wss://{QLIK_TENANT}/app/{self._app_id}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{self._app_id}');
                const selections = await app.getCurrentSelections();
                await session.close();
                const result = {{}};
                for (const sel of (selections || [])) {{
                    result[sel.fieldName] = sel.selectedValues?.map(v => v.qName) || [];
                }}
                return result;
            }} catch(err) {{
                return {{}};
            }}
        }}
        """
        return self.page.evaluate(js) or {}

    def click_button(self, button_label: str) -> bool:
        """
        Click an interactive button or bookmark toggle on the Qlik sheet.
        Matches by button label (e.g. 'Revenue', 'Quantity').
        """
        log.info(f"Clicking Qlik button: '{button_label}'")
        selectors = [
            f"button.qv-action-button:has-text('{button_label}')",
            f"button:has-text('{button_label}')",
            f"[role='button']:has-text('{button_label}')",
            f".qv-inner-object:has-text('{button_label}') button",
            f".qv-inner-object:has-text('{button_label}')",
            f"button[title*='{button_label}' i]",
            f"button[aria-label*='{button_label}' i]",
        ]
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1_500):
                    loc.scroll_into_view_if_needed(timeout=1_000)
                    loc.click(timeout=2_000, force=True)
                    self.page.wait_for_timeout(2_000)
                    log.info(f"Qlik button '{button_label}' clicked via selector: {sel}")
                    return True
            except Exception:
                continue
        log.warning(f"Qlik button '{button_label}' not found or could not be clicked")
        return False
