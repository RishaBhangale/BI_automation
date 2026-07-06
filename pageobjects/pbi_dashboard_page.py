"""
pbi_dashboard_page.py — Power BI Published Dashboard Page Object Model.

Supports TWO embed modes (auto-detected from URL):

  MODE A — "Publish to Web"  (view?r=... URL)
    • No iframe — report is in the outer page DOM
    • No authentication required
    • Use PTW_* locators from pbi_locators.py

  MODE B — "Org / Secure Report"  (groups/.../reports/... URL)
    • Report is inside an <iframe>
    • Requires Microsoft SSO (Azure AD) login
    • Use ORG_* locators from pbi_locators.py

The embed mode is detected automatically from the URL via _detect_embed_mode().
All public methods work transparently for both modes.
"""

from __future__ import annotations

import time
from typing import Optional, Union

from playwright.sync_api import Page, FrameLocator, Locator, TimeoutError as PwTimeoutError

from pageobjects.base_page import BasePage
from pageobjects.sso_login_page import SSOLoginPage
from locators.pbi_locators import PBILocators, PageNotFoundError, DashboardLoadError
from config.settings import PBI_RENDER_TIMEOUT, PBI_PAGE_SWITCH_WAIT
from utils.logger import get_logger

log = get_logger("pbi_dashboard_page")

EMBED_MODE_PUBLISH_TO_WEB = "publish_to_web"
EMBED_MODE_ORG_REPORT     = "org_report"


def _detect_embed_mode(url: str) -> str:
    """
    Detect embed mode from the Power BI URL.

    Returns:
        "publish_to_web" — if URL contains /view?r= (public "Publish to web" report)
        "org_report"     — all other app.powerbi.com URLs (require SSO + iframe)
    """
    if "app.powerbi.com/view" in url and "?r=" in url:
        return EMBED_MODE_PUBLISH_TO_WEB
    return EMBED_MODE_ORG_REPORT


class PBIDashboardPage(BasePage):
    """
    Page Object Model for published Power BI dashboards.

    Supports both "Publish to Web" and org/secure report embed modes.
    The embed mode is detected automatically from the URL.

    All interaction methods (get_all_visual_titles, extract_card_value, etc.)
    work transparently regardless of embed mode.
    """

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._embed_mode: str = EMBED_MODE_ORG_REPORT  # updated after open()
        self._org_frame: Optional[FrameLocator] = None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Context: returns the correct page/frame context for the current embed mode
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_context(self) -> Union[Page, FrameLocator]:
        """
        Returns the correct locator context:
          • publish_to_web → self.page  (outer page, no iframe)
          • org_report     → FrameLocator inside the report iframe
        """
        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            return self.page
        return self._get_org_frame()

    def _get_org_frame(self) -> FrameLocator:
        """
        Return the FrameLocator for org report iframes.
        Tries primary selector, falls back to secondary.
        """
        frame = self.page.frame_locator(PBILocators.ORG_IFRAME)
        try:
            frame.locator("body").wait_for(timeout=5_000)
            return frame
        except PwTimeoutError:
            log.debug("Primary org iframe selector failed — trying fallback")
            return self.page.frame_locator(PBILocators.ORG_IFRAME_FALLBACK)

    # Legacy alias used by older code
    def get_iframe(self) -> FrameLocator:
        """Backward-compatible alias for _get_org_frame()."""
        return self._get_org_frame()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Navigation & Login
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def open(self, url: str) -> None:
        """
        Navigate to the Power BI report URL and wait for it to render.

        Auto-detects embed mode from the URL. For publish-to-web reports,
        waits for the <explore-canvas> element to appear. For org reports,
        waits for networkidle (SSO handled separately via login_via_sso()).

        Args:
            url: Published Power BI report URL.
        """
        self._embed_mode = _detect_embed_mode(url)
        log.info(f"Embed mode detected: {self._embed_mode}")
        log.info(f"Navigating to: {url}")

        self.page.goto(url, timeout=PBI_RENDER_TIMEOUT)
        self.page.wait_for_load_state("networkidle", timeout=PBI_RENDER_TIMEOUT)

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            self._wait_for_ptw_render()
        # For org reports, caller must invoke login_via_sso() next if redirected.

    def _wait_for_ptw_render(self) -> None:
        """
        Wait for the Publish-to-Web report canvas to finish rendering.
        The <explore-canvas> custom element appears once the report is ready.
        """
        log.info("Waiting for Publish-to-Web report canvas to render...")
        try:
            self.page.wait_for_selector(
                PBILocators.PTW_EXPLORE_CANVAS,
                state="attached",
                timeout=PBI_RENDER_TIMEOUT,
            )
            # Extra buffer for visual JS rendering (PBI renders visuals lazily)
            self.page.wait_for_timeout(5_000)
            log.info("Publish-to-Web report canvas ready")
        except PwTimeoutError:
            log.warning(
                "explore-canvas not found — report may still be loading. "
                "Proceeding with extra wait..."
            )
            self.page.wait_for_timeout(10_000)

    def login_via_sso(self, username: str, password: str) -> None:
        """
        Handle Microsoft SSO login if the current page is a login wall.
        Safe to call unconditionally — does nothing if no login is required.

        Only relevant for MODE B (org reports). Publish-to-web reports
        do not require authentication.

        Args:
            username: Microsoft account email.
            password: Account password (decrypt before passing in).
        """
        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            log.info("Publish-to-web report — no SSO login required")
            return

        sso = SSOLoginPage(self.page)
        if sso.is_on_login_page():
            log.info("SSO login page detected — authenticating")
            sso.login(username, password)
            self._wait_for_org_report_render()
        else:
            log.info("No SSO login required — report accessible directly")

    def _wait_for_org_report_render(self) -> None:
        """
        Wait for an org report inside an iframe to finish rendering.
        Used after SSO login redirect.
        """
        log.info("Waiting for org report to finish rendering...")
        try:
            self.page.wait_for_selector(
                PBILocators.ORG_LOADING_SPINNER, state="visible", timeout=10_000
            )
            self.page.wait_for_selector(
                PBILocators.ORG_LOADING_SPINNER, state="hidden", timeout=PBI_RENDER_TIMEOUT
            )
        except PwTimeoutError:
            log.debug("Org report spinner not detected — assuming render complete")
        self.page.wait_for_load_state("networkidle", timeout=PBI_RENDER_TIMEOUT)
        log.info("Org report render complete")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Page Navigation
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_all_page_names(self) -> list[str]:
        """
        Return a list of all visible page tab names in the report.

        Returns:
            List of page name strings as they appear in the tab bar.
        """
        ctx = self._get_context()
        tab_sel = (
            PBILocators.PTW_PAGE_TAB
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_PAGE_TAB
        )
        tabs  = ctx.locator(tab_sel).all()
        names = []
        for tab in tabs:
            title = tab.get_attribute("title") or tab.inner_text()
            if title:
                names.append(title.strip())
        log.info(f"Found {len(names)} pages: {names}")
        return names

    def switch_to_page(self, page_name: str) -> None:
        """
        Navigate to a named page in the report.

        Auto-detects navigation mode:
          - TAB-based: clicks the tab with matching title (most org reports)
          - ARROW-based: clicks Next/Previous until the page indicator matches
            (some Publish-to-Web reports with arrow navigation)

        Args:
            page_name: Exact page name as it appears in the report tab bar,
                       OR a 1-based integer string for arrow-nav reports.
        """
        log.info(f"Switching to page: '{page_name}'")

        # Try tab-based navigation first
        ctx = self._get_context()
        tab_by_name = (
            PBILocators.PTW_PAGE_TAB_BY_NAME
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_PAGE_TAB_BY_NAME
        )
        selector = tab_by_name.format(page_name=page_name)
        try:
            tab = ctx.locator(selector)
            tab.wait_for(state="visible", timeout=3_000)
            tab.click()
            self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
            log.info(f"Tab navigation: now on page '{page_name}'")
            return
        except PwTimeoutError:
            log.debug(f"Tab '{page_name}' not found — trying arrow navigation")

        # Fallback: arrow-based navigation (go to page 1, click Next until we match)
        try:
            self._arrow_navigate_to(page_name)
        except PageNotFoundError:
            # Collect all available pages for a helpful error message
            available = self.get_all_page_names()
            raise PageNotFoundError(
                f"Page '{page_name}' not found in this report. "
                f"Available pages: {available}. "
                f"Check that the page name in your YAML matches exactly (case-sensitive)."
            )

    def _arrow_navigate_to(self, target_page: str) -> None:
        """
        Navigate to a page using the Previous/Next arrow buttons.

        Used for Publish-to-Web reports that display arrows instead of tabs.
        Starts from page 1, clicks Next until the page indicator matches.

        Args:
            target_page: Page name or 1-based page number as a string.
        """
        # First go to page 1
        self._go_to_page_one()
        page_count = self.get_page_count()
        log.info(f"Arrow navigation: target='{target_page}', total pages={page_count}")

        for page_num in range(1, page_count + 1):
            current_indicator = self._get_page_indicator_text()
            if target_page in current_indicator or str(page_num) == target_page:
                log.info(f"Reached target page '{target_page}' (indicator: {current_indicator})")
                self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                return
            self.go_to_next_page()

        log.warning(f"Arrow navigation: could not find page '{target_page}' in {page_count} pages")
        raise PageNotFoundError(
            f"Page '{target_page}' not found in {page_count} pages. "
            f"Check that the page name in your YAML matches exactly what appears in the report."
        )

    def get_page_count(self) -> int:
        """
        Return total number of pages in the report.

        Reads the page indicator text (e.g. "1of19" or "1 of 3") from
        the logo-bar-navigation element. Works for arrow-nav reports.

        Returns:
            Total page count as int, or 1 if indicator not found.
        """
        try:
            nav = self.page.locator(PBILocators.PTW_NAV_CONTAINER)
            nav.wait_for(state="attached", timeout=5_000)
            
            import re
            # Poll for a few seconds if it says "0" (PBI loads it asynchronously)
            for _ in range(15):
                text = nav.text_content().strip()
                match = re.search(r'of\s*(\d+)', text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    if count > 0:
                        log.debug(f"Page count from indicator '{text}': {count}")
                        return count
                self.page.wait_for_timeout(1000)
                
            log.warning("Page count remained 0 or unmatched after waiting.")
        except PwTimeoutError:
            log.debug("logo-bar-navigation not found — report may use tab navigation")
        return 1

    def go_to_next_page(self) -> None:
        """Click the Next Page arrow button. For arrow-nav reports."""
        try:
            btn = self.page.locator(PBILocators.PTW_NAV_NEXT_PAGE)
            btn.wait_for(state="visible", timeout=3_000)
            btn.click()
        except PwTimeoutError:
            # Fallback selectors
            self.page.locator(PBILocators.PTW_NAV_NEXT_FALLBACK).first.click()
        self.page.wait_for_timeout(2_000)  # wait for page transition
        log.debug(f"Navigated to next page (now: {self._get_page_indicator_text()})")

    def go_to_previous_page(self) -> None:
        """Click the Previous Page arrow button. For arrow-nav reports."""
        try:
            btn = self.page.locator(PBILocators.PTW_NAV_PREV_PAGE)
            btn.wait_for(state="visible", timeout=3_000)
            btn.click()
        except PwTimeoutError:
            self.page.locator(PBILocators.PTW_NAV_PREV_FALLBACK).first.click()
        self.page.wait_for_timeout(2_000)
        log.debug(f"Navigated to previous page (now: {self._get_page_indicator_text()})")

    def navigate_all_pages(self) -> dict[int, list[str]]:
        """
        Navigate through every page and collect testable visual titles per page.

        Starts from page 1, clicks Next until all pages are visited.
        Works for BOTH tab-nav and arrow-nav reports.

        Returns:
            Dict mapping 1-based page number → list of testable visual title strings.
        """
        page_count = self.get_page_count()
        log.info(f"Navigating all {page_count} pages to discover visuals")

        if page_count == 1:
            # Tab-nav or single-page — just return current page
            titles = self.get_all_visual_titles()
            return {1: titles}

        # Arrow-nav: go to page 1 first
        self._go_to_page_one()
        result: dict[int, list[str]] = {}

        for page_num in range(1, page_count + 1):
            titles = self.get_all_visual_titles()
            result[page_num] = titles
            log.info(f"Page {page_num}/{page_count}: {len(titles)} testable visuals")
            if page_num < page_count:
                self.go_to_next_page()

        return result

    def _go_to_page_one(self) -> None:
        """Navigate back to the first page (click Previous until page 1)."""
        for _ in range(50):  # safety limit
            text = self._get_page_indicator_text()
            import re
            match = re.search(r'(\d+)\s*of', text, re.IGNORECASE)
            if match and match.group(1) == '1':
                break
            try:
                self.page.locator(PBILocators.PTW_NAV_PREV_PAGE).click()
                self.page.wait_for_timeout(1_500)
            except Exception:
                break

    def _get_page_indicator_text(self) -> str:
        """Return the current page indicator text, e.g. '3of19'."""
        try:
            return self.page.locator(PBILocators.PTW_NAV_CONTAINER).text_content().strip()
        except Exception:
            return ""


    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Visual Discovery
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_all_visual_titles(self) -> list[str]:
        """
        Return the titles of all visible visuals on the current report page.

        For Publish-to-Web reports:
          • Iterates <visual-container> elements
          • Uses JS evaluation to find title text inside each container
          • Falls back through PTW_TITLE_CANDIDATES selector list

        For Org reports:
          • Uses ORG_VISUAL_TITLE_TEXT selector inside the iframe

        Returns:
            List of visual title strings (empty strings for untitled visuals excluded).
        """
        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            return self._get_ptw_visual_titles()
        return self._get_org_visual_titles()

    def _get_ptw_visual_titles(self) -> list[str]:
        """
        Extract visual titles for Publish-to-Web reports via JS evaluation.

        KEY FINDING (confirmed 2026-06-26):
          • Visual type is stored in aria-roledescription on the inner div
            [data-automation-type='visualContainer']
          • ALL visuals have noVisualTitle class — the CSS-based title header
            is always hidden on publish-to-web reports.
          • The visual 'title' is the first non-empty line of the visual's
            innerText content (e.g. a Card titled 'Sales' has innerText starting
            with 'Sales\n\n$1.7M\n...')
          • Types to skip: PTW_SKIP_TYPES — Text box, img, button, Slicer
          • Testable types: PTW_TESTABLE_TYPES — Card, chart, Table, Matrix types only

        Returns:
            List of title strings for all TESTABLE visuals on the current page.
            Testable = aria-roledescription is in PTW_TESTABLE_TYPES.

        See also:
            discover_all_visuals() — returns ALL non-noise visuals (including slicers)
            for diagnostic / discovery runs.
        """
        visuals_data = self._evaluate_ptw_visuals()
        testable_types = PBILocators.PTW_TESTABLE_TYPES
        testable = [v for v in visuals_data if v['type'] in testable_types]
        titles = [v['title'] for v in testable]
        log.info(f"Testable visual titles (PTW): {titles}")
        # Cache for use by _find_visual_container_ptw
        self._ptw_visuals_cache = testable
        return titles

    def discover_all_visuals(self) -> list[dict]:
        """
        Return ALL visuals on the current page (diagnostic / discovery mode).

        Unlike get_all_visual_titles(), this returns slicers, page titles,
        labels, and other non-testable visuals. Use this when first exploring
        a new dashboard to see everything that's on the page.

        Visuals with noisy titles (Y-axis values like "$0K") ARE included
        but are flagged with ``is_noisy_title=True``.

        Returns:
            List of dicts with keys: title, type, fullText, is_noisy_title, x, y, width, height.
        """
        visuals_data = self._evaluate_ptw_visuals()
        skip = PBILocators.PTW_SKIP_TYPES
        all_visuals = [v for v in visuals_data if v['type'] not in skip]
        log.info(f"All visuals discovered ({len(all_visuals)}): {[v['title'] for v in all_visuals]}")
        return all_visuals

    def discover_all_visuals_raw(self) -> list[dict]:
        """
        Return EVERY visual on the current page with no type filtering.

        Intended for the auto-discovery script — includes Text boxes, slicers,
        noisy-titled charts, and all other visual types.

        Each dict contains:
            title         — first text line (may be a Y-axis value for charts)
            type          — aria-roledescription value
            fullText      — up to 400 chars of the visual's innerText
            is_noisy_title — True when the title is a Y-axis/numeric value
            x, y          — top-left corner (viewport coords, pixels)
            width, height — visual bounding box dimensions

        Returns:
            Raw list of all visual dicts.
        """
        raw = self._evaluate_ptw_visuals()
        log.info(f"Raw visuals (all types, {len(raw)} total)")
        return raw

    def discover_all_pages(self) -> list[dict]:
        """
        Crawl every page of the report and collect full visual + slicer data.

        Intended for the auto-discovery script.  Handles both tab-nav and
        arrow-nav reports.  Starts from the first page and visits each page
        in order, collecting raw visual data and slicer values.

        Returns:
            List of page dicts, one per page, with the structure::

                [
                  {
                    "page_name":  "Executive Summary",  # tab name or "Page 1"
                    "page_index": 0,                    # 0-based
                    "visuals":    [ ... ],               # from discover_all_visuals_raw()
                    "slicers":    [                      # auto-detected slicer selections
                      {"title": "Year", "values": ["2022"]}
                    ]
                  },
                  ...
                ]
        """
        log.info("discover_all_pages: starting multi-page crawl")
        results: list[dict] = []

        # --- Tab-nav: use named tabs if any are present ---
        page_names = self.get_all_page_names()

        if page_names:
            for idx, name in enumerate(page_names):
                log.info(f"discover_all_pages: tab-nav to '{name}' ({idx+1}/{len(page_names)})")
                try:
                    self.switch_to_page(name)
                except Exception as e:
                    log.warning(f"Could not switch to page '{name}': {e}")
                    continue
                self.page.wait_for_timeout(2_000)
                results.append(self._snapshot_current_page(name, idx))
            return results

        # --- Arrow-nav: use page count + next-page clicks ---
        page_count = self.get_page_count()
        if page_count > 1:
            self._go_to_page_one()

        for idx in range(page_count):
            page_label = f"Page {idx + 1}"
            log.info(f"discover_all_pages: arrow-nav {page_label}/{page_count}")
            results.append(self._snapshot_current_page(page_label, idx))
            if idx < page_count - 1:
                self.go_to_next_page()


        return results

    def _wait_for_stable_visuals(self, max_wait_ms: int = 12_000, poll_ms: int = 800) -> None:
        """
        Wait until the number of [aria-roledescription] elements on the page
        stops growing (i.e., all lazy-loaded chart visuals have rendered).

        Power BI renders KPI cards first (~2s), then chart visuals later (~4-6s).
        A fixed wait misses charts on slow connections.  This polls every
        ``poll_ms`` ms until the count is stable for two consecutive polls,
        or until ``max_wait_ms`` is exceeded.

        Args:
            max_wait_ms: Maximum total wait time in milliseconds.
            poll_ms:     Interval between polls in milliseconds.
        """
        prev_count  = -1
        stable_hits = 0
        elapsed     = 0

        while elapsed < max_wait_ms:
            count = self.page.evaluate(
                "() => document.querySelectorAll('[aria-roledescription]').length"
            )
            if count == prev_count and count > 0:
                stable_hits += 1
                if stable_hits >= 2:
                    log.debug(f"Visuals stable at {count} elements after {elapsed}ms")
                    return
            else:
                stable_hits = 0
                if count != prev_count:
                    log.debug(f"Visual count changed: {prev_count} → {count} (elapsed {elapsed}ms)")

            prev_count = count
            self.page.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        log.debug(f"_wait_for_stable_visuals: timed out after {max_wait_ms}ms — proceeding")

    def _snapshot_current_page(self, page_name: str, page_index: int) -> dict:
        """
        Collect all visual and slicer data for the current report page.

        Used internally by discover_all_pages().

        Returns:
            Dict with page_name, page_index, visuals, slicers.
        """
        # Wait until chart visuals finish lazy-loading before scraping
        self._wait_for_stable_visuals()
        visuals = self.discover_all_visuals_raw()

        # Auto-discover slicers: any visual of type 'Slicer'
        slicers = []
        for v in visuals:
            if v.get('type') == 'Slicer':
                # Try the scraped title first; fall back to first line of fullText
                slicer_title = v.get('title', '').strip()
                if not slicer_title:
                    full = v.get('fullText', '')
                    slicer_title = full.split('\n')[0].strip() if full else ''
                if not slicer_title:
                    continue
                try:
                    values = self.get_slicer_value(slicer_title)
                    slicers.append({'title': slicer_title, 'values': values})
                except Exception as e:
                    log.debug(f"Could not read slicer '{slicer_title}': {e}")
                    slicers.append({'title': slicer_title, 'values': []})

        return {
            'page_name':  page_name,
            'page_index': page_index,
            'visuals':    visuals,
            'slicers':    slicers,
        }

    def extract_chart_headers(
        self,
        visual_type: str,
        visual_index: int,
        visual_title: str = "",
    ) -> list[str]:
        """
        Extract only the column headers from a chart/table visual's
        "Show as a table" view, WITHOUT reading the row data.

        This is a lightweight version of extract_table_data() used by the
        auto-discovery script to understand what columns a chart exposes,
        so it can perform DB column matching for SQL suggestion.

        Args:
            visual_type:  aria-roledescription of the visual.
            visual_index: 0-based index among visuals of that type.
            visual_title: Optional explicit title (uses type+index if empty).

        Returns:
            List of header strings.  Empty list if the chart does not
            support "Show as a table" or if it cannot be located.
        """
        label = visual_title or f"{visual_type}[{visual_index}]"
        log.info(f"extract_chart_headers: '{label}'")
        ctx = self._get_context()

        show_as_table_sel = (
            PBILocators.PTW_SHOW_AS_TABLE
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SHOW_AS_TABLE
        )
        header_sel = (
            PBILocators.PTW_DATA_TABLE_HEADER
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE_HEADER
        )
        data_table_sel = (
            PBILocators.PTW_DATA_TABLE
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE
        )

        try:
            title_el = self._find_visual_by_title(visual_title, visual_type, visual_index)
            title_el.click(button="right", force=True)

            context_menu = ctx.locator(
                PBILocators.PTW_CONTEXT_MENU
                if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
                else PBILocators.ORG_CONTEXT_MENU
            )
            context_menu.wait_for(state="visible", timeout=5_000)

            show_el = ctx.locator(show_as_table_sel)
            show_el.wait_for(state="visible", timeout=3_000)
            show_el.click()

            ctx.locator(data_table_sel).wait_for(state="visible", timeout=15_000)

            header_els = ctx.locator(header_sel).all()
            headers = [h.inner_text().strip() for h in header_els]
            log.info(f"Chart headers for '{label}': {headers}")

            self._click_back_to_report(ctx)
            return headers

        except Exception as e:
            log.debug(f"extract_chart_headers failed for '{label}': {e}")
            # Gracefully close any open menu
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return []

    def _evaluate_ptw_visuals(self) -> list[dict]:
        """
        Run the JS evaluation to get all visual containers with their
        aria-roledescription and first text line (title).

        Returns raw data — callers filter by type.
        """
        raw = self.page.evaluate("""
            () => {
                const results = [];
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );

                // Noise patterns for titles that are NOT real visual names:
                // - Y-axis tick values: "$0K", "($200K)", "$0.0M", "100%"
                // - Arrow/symbol chars: "⇗", "⇘"
                // - Pure numbers: "0", "100"
                function isNoisyTitle(title) {
                    if (!title || title.length === 0) return true;
                    // Single arrow/symbol char (length ≤ 2)
                    if (title.length <= 2 && /^[⇗⇘⇒⇑⇓→↑↓▲▼]/.test(title)) return true;
                    // Starts with $ or ( — y-axis value
                    if (/^[$\\(]/.test(title)) return true;
                    // Purely numeric
                    if (/^[\\d,\\.\\s%]+$/.test(title)) return true;
                    // Short number-like string e.g. "$0K", "100K"
                    if (/^[$\\(]?[\\d,\\.]+[KMBkmbTt%]?\\)?$/.test(title)) return true;
                    return false;
                }

                for (const div of innerDivs) {
                    const roleDesc = div.getAttribute('aria-roledescription') || '';
                    const vc = div.closest('visual-container');
                    const allText  = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines    = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                    const title    = lines.length > 0 ? lines[0] : '';
                    
                    let x = 0, y = 0, width = 0, height = 0;
                    if (vc) {
                        const rect = vc.getBoundingClientRect();
                        x = rect.x;
                        y = rect.y;
                        width = rect.width;
                        height = rect.height;
                    } else {
                        const rect = div.getBoundingClientRect();
                        x = rect.x;
                        y = rect.y;
                        width = rect.width;
                        height = rect.height;
                    }

                    results.push({
                        title,
                        type:     roleDesc,
                        fullText: allText.substring(0, 300),
                        is_noisy_title: isNoisyTitle(title),
                        x, y, width, height
                    });
                }
                return results;
            }
        """)
        return raw or []


    def _get_org_visual_titles(self) -> list[str]:
        """Extract visual titles for org reports (inside iframe)."""
        frame = self._get_org_frame()
        title_elements = frame.locator(PBILocators.ORG_VISUAL_TITLE_TEXT).all()
        titles = [el.inner_text().strip() for el in title_elements if el.inner_text().strip()]
        log.info(f"Visual titles (ORG): {titles}")
        return titles

    def _find_visual_container_ptw(self, visual_title: str) -> "Locator":
        """
        Find the <visual-container> element for a named visual (Publish-to-Web mode).

        Strategy: uses JS to find the [aria-roledescription] div whose parent visual-container
        first text line matches the visual title, then tags it with a data-pw-title
        attribute so Playwright can locate it.

        Args:
            visual_title: Exact first-line title of the visual.

        Returns:
            Playwright Locator for the visual-container element.

        Raises:
            ValueError: If no visual with the given title is found.
        """
        safe_title = visual_title.replace("'", "\\'")
        found = self.page.evaluate(f"""
            () => {{
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    const allText = (vc ? vc.innerText : div.innerText || '').trim();
                    const firstLine = allText.split('\\n')[0].trim();
                    if (firstLine === '{safe_title}') {{
                        // Tag the outer visual-container so Playwright can find it
                        if (vc) {{
                            vc.setAttribute('data-pw-title', '{safe_title}');
                            return true;
                        }}
                    }}
                }}
                return false;
            }}
        """)

        if not found:
            available = self._get_ptw_visual_titles()
            raise ValueError(
                f"Visual '{visual_title}' not found on current page. "
                f"Available: {available}"
            )
        return self.page.locator(f"visual-container[data-pw-title='{safe_title}']")

    def _find_visual_by_type_index_ptw(
        self, visual_type: str, visual_index: int
    ) -> "Locator":
        """
        Find a visual by its ``aria-roledescription`` type and 0-based index.

        This is the fallback when a Power BI report author places the chart
        title in a separate Text box visual rather than inside the chart
        container — a very common pattern. In that case the chart container
        has no usable title text, so we locate it by type and position.

        Example YAML usage::

            table_validations:
              - visual_title: ""           # leave blank
                visual_type:  "Clustered column chart"
                visual_index: 0            # 0-based; first chart of this type

        Args:
            visual_type:  Exact ``aria-roledescription`` value, e.g.
                          ``"Clustered column chart"``.
            visual_index: 0-based position among all visuals of this type.

        Returns:
            Playwright Locator for the matching visual-container element.

        Raises:
            ValueError: If fewer visuals of ``visual_type`` exist than expected.
        """
        tag_attr = "data-pw-type-idx"
        tag_value = f"{visual_type}_{visual_index}"
        safe_type = visual_type.replace("'", "\\'")

        found = self.page.evaluate(f"""
            () => {{
                const allDivs = document.querySelectorAll("[aria-roledescription]");
                const matches = [];
                for (const div of allDivs) {{
                    if (div.getAttribute('aria-roledescription') === '{safe_type}') {{
                        matches.push(div);
                    }}
                }}
                if (matches.length <= {visual_index}) return false;
                const div = matches[{visual_index}];
                const vc  = div.closest('visual-container');
                if (vc) {{
                    vc.setAttribute('{tag_attr}', '{tag_value}');
                    return true;
                }}
                // No visual-container — tag the div itself
                div.setAttribute('{tag_attr}', '{tag_value}');
                return true;
            }}
        """)

        if not found:
            # Count available visuals of this type for a helpful error
            count = self.page.evaluate(f"""
                () => document.querySelectorAll(
                    "[aria-roledescription='{safe_type}']"
                ).length
            """)
            raise ValueError(
                f"Visual type '{visual_type}' index {visual_index} not found. "
                f"Only {count} visual(s) of this type exist on the current page."
            )

        log.info(
            f"Located visual by type+index: '{visual_type}' [{visual_index}] "
            f"→ tagged as {tag_attr}='{tag_value}'"
        )
        return self.page.locator(f"visual-container[{tag_attr}='{tag_value}'], "
                                  f"[aria-roledescription][{tag_attr}='{tag_value}']")

    def _find_visual_by_title(
        self,
        visual_title: str,
        visual_type: str | None = None,
        visual_index: int | None = None,
    ):
        """
        Locate a visual container. Supports both embed modes and two locating
        strategies:

        1. **By title** (default): finds the visual whose first text line
           matches ``visual_title``.  Works when the report author embeds the
           chart title inside the chart container.

        2. **By type + index** (fallback): used when ``visual_title`` is empty
           or blank and ``visual_type`` / ``visual_index`` are provided.
           Finds the *N-th* visual whose ``aria-roledescription`` equals
           ``visual_type``.  Use this when titles are placed in separate Text
           box visuals (a common PBI pattern).

        Args:
            visual_title: Exact first-line title of the visual.  Pass an empty
                          string (``""``) to use type+index strategy.
            visual_type:  ``aria-roledescription`` value, e.g.
                          ``"Clustered column chart"``.
            visual_index: 0-based index among visuals of ``visual_type``.

        Returns:
            Locator pointing to the visual container element.
        """
        # Choose type+index strategy when title is absent and type is given
        use_type_index = (not visual_title or not visual_title.strip()) and visual_type

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            if use_type_index:
                idx = visual_index if visual_index is not None else 0
                return self._find_visual_by_type_index_ptw(visual_type, idx)
            return self._find_visual_container_ptw(visual_title)

        # Org report — use iframe-based selector (type+index not yet supported)
        frame = self._get_org_frame()
        title_locator = frame.locator(
            f"{PBILocators.ORG_VISUAL_TITLE_TEXT}:has-text('{visual_title}')"
        )
        if title_locator.count() == 0:
            available = self._get_org_visual_titles()
            raise ValueError(
                f"Visual '{visual_title}' not found. Available: {available}"
            )
        return title_locator.first

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # KPI Card Extraction
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def extract_card_value(self, visual_title: str) -> str:
        """
        Extract the displayed value from a KPI Card visual.

        The raw string is returned exactly as Power BI renders it
        (e.g., "$4.2M", "1,234", "87.5%"). Use validation_utils.parse_pbi_number()
        to convert it to a float for comparison.

        Args:
            visual_title: Exact title of the KPI card visual.

        Returns:
            Raw value string as displayed on the card.
        """
        log.info(f"Extracting KPI card value for: '{visual_title}'")

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            return self._extract_card_value_ptw(visual_title)
        return self._extract_card_value_org(visual_title)

    def _extract_card_value_ptw(self, visual_title: str) -> str:
        """
        Extract card value for Publish-to-Web reports via JS evaluation.

        KEY FINDING: For Card visuals, innerText structure is:
            Line 0: Visual title (e.g. 'Sales')
            Line 1: (empty)
            Line 2: KPI value (e.g. '$1.7M')
            Line 3: (empty)
            Line 4: 'YoY' label
            Line 5: comparison value

        We return the FIRST numeric-looking non-empty line after the title.
        """
        safe_title = visual_title.replace("'", "\\'")
        raw_value = self.page.evaluate(f"""
            () => {{
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    const allText = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines   = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length === 0 || lines[0] !== '{safe_title}') continue;

                    // Return the first line after the title that looks like a number
                    for (let i = 1; i < lines.length; i++) {{
                        const line = lines[i];
                        // Must contain at least one digit and be reasonably short
                        if (/[0-9]/.test(line) && line.length < 30 && !/^YoY/.test(line)) {{
                            return line;
                        }}
                    }}
                    // Fallback: return second non-empty line
                    return lines.length > 1 ? lines[1] : allText;
                }}
                return null;
            }}
        """)

        if not raw_value:
            raise ValueError(
                f"Could not extract value for card '{visual_title}'. "
                f"Verify the visual title matches exactly and it is a Card visual."
            )
        log.info(f"Card '{visual_title}' → raw value: '{raw_value}'")
        return raw_value

    def _extract_card_value_org(self, visual_title: str) -> str:
        """Extract card value for org reports (inside iframe)."""
        frame = self._get_org_frame()
        value_locator = frame.locator(
            f"visual-container:has({PBILocators.ORG_VISUAL_TITLE_TEXT}"
            f":has-text('{visual_title}')) {PBILocators.ORG_CARD_VALUE}"
        )
        value_locator.wait_for(timeout=15_000)
        raw_value = value_locator.inner_text().strip()
        log.info(f"Card '{visual_title}' → raw value: '{raw_value}'")
        return raw_value

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Table / Chart Data Extraction ("Show as a table")
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def extract_table_data(
        self,
        visual_title: str,
        visual_type: str | None = None,
        visual_index: int | None = None,
    ) -> list[dict]:
        """
        Extract underlying data from a chart/table visual using
        Power BI's "Show as a table" / "Show data" feature.

        Process:
          1. Right-click the visual to open the context menu.
          2. Click "Show as a table".
          3. Wait for PBI to render the data as an HTML table.
          4. Scrape table headers and rows.
          5. Click "Back to report" to return to normal view.

        Args:
            visual_title: Exact title of the chart or table visual.  Pass an
                          empty string if using type+index strategy.
            visual_type:  ``aria-roledescription`` of the visual (e.g.
                          ``"Clustered column chart"``) — used when
                          ``visual_title`` is blank.
            visual_index: 0-based index among visuals of ``visual_type``.

        Returns:
            List of dicts, one per data row. Keys are column headers.
            Example: [{"region": "North", "sales": "123456"}, ...]
            Note: All values are strings — use validation_utils to parse numerics.
        """
        label = visual_title or f"{visual_type}[{visual_index}]"
        log.info(f"Extracting table data for: '{label}'")
        ctx = self._get_context()

        show_as_table_sel = (
            PBILocators.PTW_SHOW_AS_TABLE
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SHOW_AS_TABLE
        )
        data_table_sel = (
            PBILocators.PTW_DATA_TABLE
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE
        )
        header_sel = (
            PBILocators.PTW_DATA_TABLE_HEADER
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE_HEADER
        )
        row_sel = (
            PBILocators.PTW_DATA_TABLE_ROW
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE_ROW
        )
        cell_sel = (
            PBILocators.PTW_DATA_TABLE_CELL
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_DATA_TABLE_CELL
        )

        # Right-click the visual to open context menu (supports title OR type+index)
        title_el = self._find_visual_by_title(visual_title, visual_type, visual_index)
        title_el.click(button="right", force=True)

        context_menu = ctx.locator(
            PBILocators.PTW_CONTEXT_MENU
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_CONTEXT_MENU
        )
        context_menu.wait_for(state="visible", timeout=5_000)

        # Check if "Show as a table" is actually available in the context menu
        show_as_table_el = ctx.locator(show_as_table_sel)
        try:
            show_as_table_el.wait_for(state="visible", timeout=3_000)
        except PwTimeoutError:
            # Close the context menu gracefully by pressing Escape
            self.page.keyboard.press("Escape")
            raise ValueError(
                f"Visual '{label}' does not support 'Show as a table'. "
                f"This feature must be enabled by the report author in Power BI Desktop "
                f"(Visual → Format → Show as a table). "
                f"Contact the report author to enable it, or remove this visual from table_validations."
            )

        show_as_table_el.click()

        data_table = ctx.locator(data_table_sel)
        data_table.wait_for(state="visible", timeout=20_000)

        # ── Read headers ──────────────────────────────────────────────────────
        header_els = ctx.locator(header_sel).all()
        headers    = [h.inner_text().strip() for h in header_els]
        log.info(f"Table headers: {headers}")

        # ── Scrape rows with scroll-to-load pagination ────────────────────────
        # Power BI "Show as a table" uses a virtual scroll container — not all
        # rows are rendered at once. We scroll down repeatedly to force more rows
        # into the DOM, collecting new ones each pass.
        MAX_SCROLL_ATTEMPTS = 20
        all_rows: list[dict] = []
        seen_first_cells: set[str] = set()  # de-duplicate by first-cell value of each row

        def _scrape_visible_rows() -> int:
            """Scrape currently visible rows, add new ones to all_rows. Returns count added."""
            added = 0
            for row_el in ctx.locator(row_sel).all():
                cell_els = row_el.locator(cell_sel).all()
                cells = [c.inner_text().strip() for c in cell_els]
                if cells and len(cells) == len(headers):
                    row_key = "|".join(cells)  # unique key for de-duplication
                    if row_key not in seen_first_cells:
                        seen_first_cells.add(row_key)
                        all_rows.append(dict(zip(headers, cells)))
                        added += 1
            return added

        # Initial scrape
        _scrape_visible_rows()
        log.debug(f"After initial scrape: {len(all_rows)} rows")

        # Scroll inside the table container and collect more rows
        scroll_attempts = 0
        while scroll_attempts < MAX_SCROLL_ATTEMPTS:
            # Find the scrollable container (the table body / viewport)
            scroll_container = ctx.locator(data_table_sel)
            prev_count = len(all_rows)

            # Scroll the container down by its visible height
            try:
                scroll_container.evaluate("el => el.scrollTop += el.clientHeight")
            except Exception:
                break  # Container may no longer exist — stop scrolling
            self.page.wait_for_timeout(300)  # Allow DOM to re-render

            newly_added = _scrape_visible_rows()
            log.debug(
                f"Scroll attempt {scroll_attempts + 1}: "
                f"+{newly_added} new rows (total {len(all_rows)})"
            )

            if len(all_rows) == prev_count:
                # No new rows — we've hit the bottom
                log.debug("No new rows after scroll — reached end of table")
                break

            scroll_attempts += 1

        scroll_pages = scroll_attempts + 1
        log.info(
            f"Extracted {len(all_rows)} rows from '{visual_title}' "
            f"({'1 scroll page' if scroll_pages == 1 else f'{scroll_pages} scroll pages'})"
        )
        self._click_back_to_report(ctx)
        return all_rows


    def _click_back_to_report(self, ctx) -> None:
        """Click 'Back to report' to exit the 'Show as a table' view."""
        back_sel = (
            PBILocators.PTW_BACK_TO_REPORT
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_BACK_TO_REPORT
        )
        try:
            back_btn = ctx.locator(back_sel)
            back_btn.wait_for(state="visible", timeout=5_000)
            back_btn.click()
            self.page.wait_for_timeout(2_000)
            log.info("Returned to report view")
        except PwTimeoutError:
            log.warning("'Back to report' button not found — already in report view?")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Slicer Interaction
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_slicer(self, slicer_title: str, value: str) -> None:
        """
        Set a slicer to a specific value.

        Args:
            slicer_title: Title of the slicer visual.
            value:        The slicer item to select (e.g., "North", "2024").
        """
        log.info(f"Setting slicer '{slicer_title}' to '{value}'")
        container = self._find_visual_by_title(slicer_title)

        search_sel = (
            PBILocators.PTW_SLICER_SEARCH
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SLICER_SEARCH
        )
        item_sel = (
            PBILocators.PTW_SLICER_ITEM
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SLICER_ITEM
        )

        try:
            search = container.locator(search_sel)
            search.wait_for(timeout=3_000)
            search.fill(value)
            self.page.wait_for_timeout(500)
        except PwTimeoutError:
            log.debug(f"Slicer '{slicer_title}' has no search input — trying direct click")

        item = container.locator(f"{item_sel}:has-text('{value}')")
        item.click()
        self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
        log.info(f"Slicer '{slicer_title}' set to '{value}'")

    def get_slicer_value(self, slicer_title: str) -> list[str]:
        """
        Read the currently selected value(s) from a slicer visual.

        Works by inspecting the innerText of selected slicer items.
        If no items appear selected (e.g., "All" or no selection), returns ["All"].

        Args:
            slicer_title: Title of the slicer visual.

        Returns:
            List of currently selected slicer item labels.
            Returns ["All"] if nothing is explicitly selected.
        """
        log.info(f"Reading slicer value for: '{slicer_title}'")
        selected_texts = self.page.evaluate("""
            (targetTitle) => {
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );
                for (const div of innerDivs) {
                    const vc = div.closest('visual-container');
                    const allText = (vc ? vc.innerText : div.innerText || '').trim();
                    const firstLine = allText.split('\\n')[0].trim();
                    if (firstLine !== targetTitle) continue;

                    const selected = [];

                    // Strategy 1: checked checkboxes inside slicer items
                    const checkedInputs = div.querySelectorAll(
                        "[class*='slicerItemContainer'] input[aria-checked='true'], " +
                        "[class*='slicerItemContainer'] input:checked"
                    );
                    for (const inp of checkedInputs) {
                        const label = inp.closest("[class*='slicerItemContainer']");
                        if (label) selected.push((label.innerText || '').trim());
                    }

                    // Strategy 2: items with 'selected' or 'checked' class
                    if (selected.length === 0) {
                        const selectedItems = div.querySelectorAll(
                            "[class*='slicerItemContainer'][class*='selected'], " +
                            "[class*='slicerItemContainer'][aria-selected='true']"
                        );
                        for (const item of selectedItems) {
                            selected.push((item.innerText || '').trim());
                        }
                    }

                    // Strategy 3: look for a display-value span (dropdown/date slicers)
                    if (selected.length === 0) {
                        const display = div.querySelector(
                            "[class*='displayValue'], [class*='slicerText'], " +
                            "[aria-label*='selected' i] span"
                        );
                        if (display) selected.push((display.innerText || '').trim());
                    }

                    return selected.length > 0 ? selected : ['All'];
                }
                return null;  // slicer not found
            }
        """, slicer_title)

        if selected_texts is None:
            raise ValueError(
                f"Slicer '{slicer_title}' not found on current page. "
                f"Make sure the slicer title matches exactly."
            )

        log.info(f"Slicer '{slicer_title}' current selection: {selected_texts}")
        return selected_texts

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Health Checks
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def check_for_error_banner(self) -> tuple[bool, str]:
        """
        Check whether the dashboard shows an error or permission-denied banner.

        Looks for known Power BI error DOM elements and text patterns.
        Safe to call after open() to verify the report actually loaded.

        Returns:
            Tuple (has_error: bool, message: str).
            If has_error is True, message describes what was found.
        """
        try:
            # Check for CSS error containers
            error_el = self.page.locator(PBILocators.PTW_ERROR_BANNER)
            if error_el.count() > 0:
                msg = error_el.first.inner_text().strip()[:200]
                log.warning(f"Error banner detected: '{msg}'")
                return True, f"Power BI error banner detected: '{msg}'"
        except Exception:
            pass

        # Check for known error text patterns
        deny_patterns = [
            "You do not have permission",
            "This content is not available",
            "Access denied",
            "Something went wrong",
        ]
        for pattern in deny_patterns:
            try:
                if self.page.locator(f"text={pattern}").count() > 0:
                    log.warning(f"Permission/error text detected: '{pattern}'")
                    return True, f"Dashboard shows error: '{pattern}'"
            except Exception:
                pass

        log.info("No error banners detected — dashboard appears accessible")
        return False, ""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Diagnostics
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @property
    def embed_mode(self) -> str:
        """Return the detected embed mode for this report."""
        return self._embed_mode
