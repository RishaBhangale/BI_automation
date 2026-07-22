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

        Reads the page indicator text (e.g. "1of3" or "1 of 19") from
        the logo-bar-navigation element. Works for arrow-nav reports.

        ── Edge Case: Section listings concatenated into indicator ───────────
        Some PBI reports render the indicator and section nav labels as one
        continuous text node with no separator:

            "1of31- Ticket Volume & Classification2- Efficiency & Resolution3-..."

        This is "1 of 3" (3 pages) + "1- Section Name" + "2- ..." concatenated.
        A greedy `of(\d+)` would incorrectly match "of31" → 31 pages.

        Fix: use a non-greedy match with a lookahead that detects when a
        section-listing digit-dash pattern follows immediately:

            of(\d+?)(?=\d+\s*-)   →  "of3" (then lookahead sees "1-") ✓

        If no section listings are present (clean "1of19"), the lookahead
        won't match and we fall back to the standard greedy match → 19 ✓

        Returns:
            Total page count as int, or 1 if indicator not found.
        """
        try:
            nav = self.page.locator(PBILocators.PTW_NAV_CONTAINER)
            nav.wait_for(state="attached", timeout=5_000)

            import re

            # Poll for a few seconds if it says "0" (PBI loads it asynchronously)
            for _ in range(15):
                raw_text = nav.text_content().strip()

                # ── Step 1: Non-greedy + lookahead (for section-list concat) ──
                # Handles "1of31- Section Name" → 3
                # of(\d+?)  — match minimum digits
                # (?=\d+\s*-) — only if immediately followed by digit(s)+dash
                match = re.search(r'of(\d+?)(?=\d+\s*-)', raw_text, re.IGNORECASE)

                if not match:
                    # ── Step 2: Standard greedy fallback ──────────────────────
                    # Handles clean indicators like "1of19", "1 of 3"
                    match = re.search(r'of\s*(\d+)', raw_text, re.IGNORECASE)

                if match:
                    count = int(match.group(1))
                    if count > 0:
                        log.debug(
                            f"Page count from indicator '{raw_text}': {count}"
                        )
                        return count

                self.page.wait_for_timeout(1000)

            log.warning("Page count remained 0 or unmatched after waiting.")
        except PwTimeoutError:
            log.debug("logo-bar-navigation not found — report may use tab navigation")
        return 1


    def go_to_next_page(self) -> None:
        """
        Click the Next Page arrow button. For arrow-nav reports.

        The button can be temporarily disabled (aria-disabled="true") while the
        current page is still rendering. This method waits for it to become
        enabled before clicking, avoiding the TimeoutError seen on slow/large
        dashboards.
        """
        self._click_nav_button(direction="next")
        self.page.wait_for_timeout(2_000)  # wait for page transition to start
        log.debug(f"Navigated to next page (now: {self._get_page_indicator_text()})")

    def go_to_previous_page(self) -> None:
        """
        Click the Previous Page arrow button. For arrow-nav reports.

        Waits for the button to become enabled before clicking.
        """
        self._click_nav_button(direction="prev")
        self.page.wait_for_timeout(2_000)
        log.debug(f"Navigated to previous page (now: {self._get_page_indicator_text()})")

    def _click_nav_button(self, direction: str, max_wait_ms: int = 10_000) -> None:
        """
        Locate and click a navigation button (next or prev), waiting until it
        is enabled (not aria-disabled) before clicking.

        Power BI temporarily sets aria-disabled="true" on navigation buttons
        while a page transition is in progress. Trying to click during this
        window raises a TimeoutError. This helper polls until enabled.

        Args:
            direction:   "next" or "prev"
            max_wait_ms: Maximum ms to wait for the button to become enabled.
        """
        if direction == "next":
            primary_sel  = PBILocators.PTW_NAV_NEXT_PAGE
            fallback_sel = PBILocators.PTW_NAV_NEXT_FALLBACK
        else:
            primary_sel  = PBILocators.PTW_NAV_PREV_PAGE
            fallback_sel = PBILocators.PTW_NAV_PREV_FALLBACK

        # Resolve the button element — try primary selector first
        try:
            btn = self.page.locator(primary_sel)
            btn.wait_for(state="visible", timeout=3_000)
        except PwTimeoutError:
            btn = self.page.locator(fallback_sel).first

        # Wait for the button to be enabled (not aria-disabled)
        poll_ms  = 500
        elapsed  = 0
        while elapsed < max_wait_ms:
            is_disabled = btn.evaluate(
                "el => el.disabled || el.getAttribute('aria-disabled') === 'true'"
            )
            if not is_disabled:
                break
            log.debug(
                f"Nav button ({direction}) is disabled — waiting {poll_ms}ms "
                f"({elapsed}/{max_wait_ms}ms elapsed)"
            )
            self.page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        else:
            log.warning(
                f"Nav button ({direction}) was still disabled after {max_wait_ms}ms — "
                "attempting click anyway"
            )

        btn.click()



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

        For Multi-row card visuals, decomposes each visual into individual
        sub-KPI entries (one dict per sub-value) so every KPI is discoverable.
        The decomposition is fully generalised via JS DOM crawl — no selectors
        are hardcoded for a specific dashboard.

        Returns raw data — callers filter by type.
        """
        raw = self.page.evaluate("""
            () => {
                const results = [];
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );

                // Noise patterns for titles that are NOT real visual names:
                function isNoisyTitle(title) {
                    if (!title || title.length === 0) return true;
                    if (title.length <= 2 && /^[⇗⇘⇒⇑⇓→↑↓▲▼]/.test(title)) return true;
                    if (/^[\\$\\(]/.test(title)) return true;
                    if (/^[\\d,\\.\\s%]+$/.test(title)) return true;
                    if (/^[\\$\\(]?[\\d,\\.]+[KMBkmbTt%]?\\)?$/.test(title)) return true;
                    return false;
                }

                // ── Detect whether a Card visual is actually a multi-value card ──
                // A Card with N KPI pairs has the pattern:
                //   line0: KPI_label_1  (text, not a pure number)
                //   line1: KPI_value_1  (numeric or short)
                //   line2: KPI_label_2
                //   line3: KPI_value_2  ...
                // We identify this by checking that lines alternate between
                // "looks like a label" (contains letters, length > 2) and
                // "looks like a value" (short, may be numeric or text).
                // Returns [] for single-KPI cards.
                function decomposeMultiKpiCard(vc, roleDesc) {
                    const allText = vc ? vc.innerText.trim() : '';
                    const lines = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                    const rect = vc ? vc.getBoundingClientRect() : {x:0,y:0,width:0,height:0};

                    // ── Strategy 1: CSS sub-item selectors (Multi-row card DOM) ──
                    const containerSelectors = [
                        "[class*='cardItemContainer']",
                        "[class*='cardItem']",
                        "[class*='multiRowCard'] [class*='cell']",
                        "[class*='row'] [class*='data']",
                    ];
                    const labelSelectors = ["[class*='caption']","[class*='label']","[class*='category']","[class*='title']"];
                    const valueSelectors = ["[class*='value']","[class*='callout']","[class*='data']"];

                    let bestItems = [];
                    if (vc) {
                        for (const sel of containerSelectors) {
                            const items = Array.from(vc.querySelectorAll(sel));
                            if (items.length > bestItems.length) bestItems = items;
                        }
                    }

                    if (bestItems.length >= 2) {
                        // DOM sub-items found — extract label+value from each
                        const subEntries = [];
                        bestItems.forEach((item, idx) => {
                            let label = '', value = '';
                            for (const s of labelSelectors) { const el = item.querySelector(s); if (el && el.innerText.trim()) { label = el.innerText.trim(); break; } }
                            for (const s of valueSelectors) { const el = item.querySelector(s); if (el && el.innerText.trim()) { value = el.innerText.trim(); break; } }
                            if (!label) { const il = item.innerText.trim().split('\\n').map(l=>l.trim()).filter(Boolean); label=il[0]||''; value=il[1]||''; }
                            if (label) subEntries.push({ title: label, value, type: roleDesc, parent_type: roleDesc, sub_index: idx, fullText: `${label}\\n${value}`, is_noisy_title: isNoisyTitle(label), x: rect.x, y: rect.y, width: rect.width, height: rect.height });
                        });
                        if (subEntries.length >= 2) return subEntries;
                    }

                    // ── Strategy 2: innerText alternating-line parsing ──
                    // PBI Card visuals containing N KPIs render as:
                    //   label1 \\n value1 \\n label2 \\n value2 ...
                    // where labels contain letters and values are short.
                    // We need at least 2 pairs (4 lines) to call it multi-value.
                    const isLabel = l => /[a-zA-Z_]/.test(l) && l.length > 2;
                    const isValue = l => l.length <= 50;  // values are short

                    // Check alternating pattern starting at line 0
                    if (lines.length >= 4) {
                        const pairs = [];
                        let i = 0;
                        while (i < lines.length - 1) {
                            const lbl = lines[i];
                            const val = lines[i + 1];
                            if (isLabel(lbl) && isValue(val) && !isNoisyTitle(lbl)) {
                                pairs.push([lbl, val]);
                                i += 2;
                            } else {
                                break;
                            }
                        }
                        if (pairs.length >= 2) {
                            return pairs.map(([lbl, val], idx) => ({
                                title: lbl,
                                value: val,
                                type: roleDesc,
                                parent_type: roleDesc,
                                sub_index: idx,
                                fullText: `${lbl}\\n${val}`,
                                is_noisy_title: isNoisyTitle(lbl),
                                x: rect.x, y: rect.y,
                                width: rect.width, height: rect.height,
                            }));
                        }
                    }

                    return [];  // Not a multi-value card
                }

                // Card types that may contain multiple KPI values
                const MULTI_KPI_TYPES = new Set(['Card', 'Multi-row card']);

                for (const div of innerDivs) {
                    const roleDesc = div.getAttribute('aria-roledescription') || '';
                    const vc = div.closest('visual-container');
                    const allText  = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines    = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                    const title    = lines.length > 0 ? lines[0] : '';

                    let x = 0, y = 0, width = 0, height = 0;
                    if (vc) {
                        const rect = vc.getBoundingClientRect();
                        x = rect.x; y = rect.y; width = rect.width; height = rect.height;
                    } else {
                        const rect = div.getBoundingClientRect();
                        x = rect.x; y = rect.y; width = rect.width; height = rect.height;
                    }

                    // ── Attempt multi-KPI decomposition for Card / Multi-row card ──
                    if (MULTI_KPI_TYPES.has(roleDesc) && vc) {
                        const subKpis = decomposeMultiKpiCard(vc, roleDesc);
                        if (subKpis.length >= 2) {
                            // Push each sub-KPI as its own discoverable entry
                            for (const sub of subKpis) results.push(sub);
                            continue;  // skip the single parent-card push
                        }
                    }

                    // Single-value visual (or non-card type) — push as-is
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

        Used by KPI card extraction — returns the outer visual-container, which
        is sufficient for reading innerText/aria-labels on its children.

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
                        // Tag both the outer shell AND the inner div so callers can
                        // use whichever has real pixel dimensions.
                        if (vc) {{
                            vc.setAttribute('data-pw-title', '{safe_title}');
                        }}
                        // Tag the inner div — this one always has real pixel dimensions
                        div.setAttribute('data-pw-inner-title', '{safe_title}');
                        return true;
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

    def _find_visual_inner_div_ptw(self, visual_title: str) -> "Locator":
        """
        Return the INNER [aria-roledescription] div for a named PTW visual.

        Unlike _find_visual_container_ptw (which returns the outer
        visual-container custom element), this returns the child div that
        Power BI renders with real pixel dimensions. This is the correct
        element to hover or click for showing the '...' More Options button,
        because the outer visual-container has zero width/height in PTW embeds.

        Args:
            visual_title: Exact first-line title of the visual.

        Returns:
            Playwright Locator for the inner [aria-roledescription] div.

        Raises:
            ValueError: If no visual with the given title is found.
        """
        # _find_visual_container_ptw already tags the inner div with data-pw-inner-title
        # as a side-effect — call it to ensure tagging is done, then return the inner div.
        self._find_visual_container_ptw(visual_title)  # ensures tagging
        safe_title = visual_title.replace("'", "\\'")
        inner = self.page.locator(f"[data-pw-inner-title='{safe_title}']")
        if inner.count() == 0:
            raise ValueError(
                f"Inner div for visual '{visual_title}' not found after tagging. "
                f"This is unexpected — check DOM structure."
            )
        return inner.first

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
                }}
                // Always tag the inner div — it has real pixel dimensions
                div.setAttribute('data-pw-inner-type-idx', '{tag_value}');
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

        Handles both standalone Card visuals AND sub-KPIs inside Multi-row card
        visuals. For Multi-row cards the method searches ALL text lines across
        ALL multi-row visual containers until a line matching ``visual_title``
        is found, then returns the value paired with that label.

        KEY FINDING: For single Card visuals, innerText structure is:
            Line 0: Visual title (e.g. 'Sales')
            Line 1: (empty)
            Line 2: KPI value (e.g. '$1.7M')

        For Multi-row card visuals, pairs alternate as label then value.
        """
        safe_title = visual_title.replace("'", "\\'")
        raw_value = self.page.evaluate(f"""
            () => {{
                const innerDivs = document.querySelectorAll(
                    "[aria-roledescription]"
                );

                // ── Pass 1: Exact first-line match (standalone Card) ──
                // A standalone Card's innerText starts with its title on line 0.
                // We only accept this match if the title is truly line[0] — i.e.
                // this visual "owns" the title, not just contains it in the middle.
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    const allText = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines   = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length === 0 || lines[0] !== '{safe_title}') continue;

                    // Return the first non-title line that looks like a KPI value.
                    // Skip lines that look like dates (contain a weekday/month name)
                    // or sub-KPI labels (contain 'of ' or end with '_name'/'_%').
                    const datePattern = /\\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December)\\b/i;
                    const labelPattern = / of |_name|_%|^Sum |^First |^Count |^Average |^Max |^Min /i;
                    for (let i = 1; i < lines.length; i++) {{
                        const line = lines[i];
                        if (datePattern.test(line)) continue;   // skip dates
                        if (labelPattern.test(line)) continue;  // skip sub-KPI labels
                        // Accept: numeric value, percentage, short text
                        if (line.length < 40 && line.length > 0 && !/^YoY/.test(line)) {{
                            return line;
                        }}
                    }}
                    // Fallback: second non-empty line
                    return lines.length > 1 ? lines[1] : allText;
                }}

                // ── Pass 2: Sub-KPI search across ALL card-type visuals ──
                // Power BI renders multi-value cards as 'Card' OR 'Multi-row card'.
                // We search every visual's full text for a line equal to the requested
                // title, then return the NEXT non-empty line as the value.
                // This handles sub-KPIs like 'Sum of danceability_%' sitting inside
                // a 'Sum of acousticness_%' Card visual.
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    const allText = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines   = allText.split('\\n').map(l => l.trim()).filter(Boolean);

                    // Skip if title is line[0] — already handled by Pass 1
                    if (lines.length > 0 && lines[0] === '{safe_title}') continue;

                    for (let i = 0; i < lines.length; i++) {{
                        if (lines[i] === '{safe_title}') {{
                            // Value is the next non-empty line
                            for (let j = i + 1; j < lines.length; j++) {{
                                if (lines[j]) return lines[j];
                            }}
                        }}
                    }}

                    // Also try tab-separated pairs on the same line
                    for (const line of lines) {{
                        const parts = line.split('\\t').map(p => p.trim()).filter(Boolean);
                        if (parts.length === 2 && parts[0] === '{safe_title}') {{
                            return parts[1];
                        }}
                    }}
                }}
                return null;
            }}
        """)

        if not raw_value:
            raise ValueError(
                f"Could not extract value for card '{visual_title}'. "
                f"Verify the visual title matches exactly and it is a Card or Multi-row card visual."
            )
        log.info(f"Card '{visual_title}' → raw value: '{raw_value}'")
        return raw_value


        if not raw_value:
            raise ValueError(
                f"Could not extract value for card '{visual_title}'. "
                f"Verify the visual title matches exactly and it is a Card or Multi-row card visual."
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

    def _extract_chart_data_ptw_aria(self, visual_title: str) -> list[dict]:
        """
        Extract chart data from Power BI's accessibility aria-label attributes.

        Power BI renders aria-labels on every chart data point (bar, line point,
        pie slice, etc.) for screen reader accessibility. These look like:
          "The Weeknd. Sum of streams. 4,655,506,388."
          "2023. Revenue. $1.2M."

        This method scrapes those aria-labels directly — NO clicks, NO context
        menu, works perfectly in headless mode.

        Args:
            visual_title: Exact first-line title of the chart visual.

        Returns:
            List of dicts with keys 'category', 'measure', 'value'.
            Empty list if no aria-label data points found.
        """
        safe_title = visual_title.replace("'", "\\'")
        rows = self.page.evaluate(f"""
            () => {{
                const innerDivs = document.querySelectorAll('[aria-roledescription]');
                let targetVc = null;
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    if (!vc) continue;
                    const firstLine = (vc.innerText || '').trim().split('\\n')[0].trim();
                    if (firstLine === '{safe_title}') {{ targetVc = vc; break; }}
                }}
                if (!targetVc) return [];

                // ── Pass 1: Standard "Category. Measure. Value." aria-label format ──
                const candidates = targetVc.querySelectorAll('[aria-label]');
                const rows = [];
                const seen = new Set();
                for (const el of candidates) {{
                    const label = (el.getAttribute('aria-label') || '').trim();
                    if (!label || label.length < 3) continue;
                    if (seen.has(label)) continue;
                    seen.add(label);
                    const clean = label.replace(/\\.$/, '');
                    const parts = clean.split('. ');
                    if (parts.length === 3) {{
                        rows.push({{ category: parts[0].trim(), measure: parts[1].trim(), value: parts[2].trim() }});
                    }} else if (parts.length === 2) {{
                        rows.push({{ category: parts[0].trim(), value: parts[1].trim() }});
                    }} else if (parts.length > 3) {{
                        rows.push({{ category: parts[0].trim(), measure: parts.slice(1,-1).join(', '), value: parts[parts.length-1].trim() }});
                    }}
                    // Single-part labels are axis ticks — skip
                }}
                if (rows.length > 0) return rows;

                // ── Pass 2: column-chart-rect + axis-tick-text pairing ─────────────
                // Large bar/column charts: PBI puts raw numeric values as aria-label
                // on [data-automation-type="column-chart-rect"] rects, and category
                // names in [data-automation-type="axis-tick-text"] text elements.
                // Match N-th bar rect → N-th axis tick label (mod category count).
                const barRects = Array.from(targetVc.querySelectorAll(
                    '[data-automation-type="column-chart-rect"],[data-automation-type="bar-chart-rect"]'
                ));
                const axisTickEls = Array.from(targetVc.querySelectorAll(
                    '[data-automation-type="axis-tick-text"]'
                ));
                // PBI renders every text string TWICE (visual + accessibility shadow),
                // producing "Blinding LightsBlinding Lights" from textContent.
                // dedup() detects and removes the repeat.
                const dedup = s => {{
                    if (s.length % 2 !== 0) return s;
                    const half = s.slice(0, s.length / 2);
                    return (half + half === s) ? half : s;
                }};
                const axisLabels = [...new Set(
                    axisTickEls
                        .map(el => dedup((el.textContent || '').trim()))
                        .filter(t => t && !/^[\\d.,]+$/.test(t))
                )];

                if (barRects.length > 0 && axisLabels.length > 0) {{
                    const nCats = axisLabels.length;
                    const seenKey = new Set();
                    barRects.forEach((rect, i) => {{
                        const val = (rect.getAttribute('aria-label') || '').trim();
                        const cat = axisLabels[i % nCats];
                        if (!cat || !val) return;
                        const key = cat + '|' + val;
                        if (seenKey.has(key)) return;
                        seenKey.add(key);
                        rows.push({{ category: cat, value: val }});
                    }});
                    if (rows.length > 0) return rows;
                }}

                // ── Pass 3: SVG <text> category + adjacent numeric value ────────────
                // Fallback for any chart where only SVG text is rendered.
                const numericRe = /^[\\d,\\.\\s%$€£¥bn]+$/i;
                const svgTexts = Array.from(targetVc.querySelectorAll('text'))
                    .map(t => (t.textContent || '').trim()).filter(t => t.length > 1);
                const cats = [...new Set(svgTexts.filter(t => !numericRe.test(t)))];
                const vals = [...new Set(svgTexts.filter(t => numericRe.test(t) && !/^[\\s]+$/.test(t)))];
                if (cats.length > 0 && vals.length > 0 && cats.length === vals.length) {{
                    cats.forEach((c, i) => rows.push({{ category: c, value: vals[i] }}));
                }}

                return rows;
            }}
        """)
        return rows or []

    def _extract_table_visual_ptw_dom(self, visual_title: str) -> list[dict]:
        """
        Extract data from a PBI Table or Matrix visual in PTW mode by scraping
        the DOM's grid/table role elements directly — NO clicks, NO context menu.

        Power BI Table and Matrix visuals render their data as:
          <div role="grid" aria-rowcount="N" aria-colcount="M">
            <div role="rowgroup">                    ← header
              <div role="row">
                <div role="columnheader">Col A</div>
                <div role="columnheader">Col B</div>
              </div>
            </div>
            <div role="rowgroup">                    ← body (virtualised!)
              <div role="row">
                <div role="gridcell">val1</div>
                <div role="gridcell">val2</div>
              </div>
              ...
            </div>
          </div>

        Limitations:
          • Virtualised scrolling — only currently rendered rows are in the DOM
            (typically ~20-40 rows depending on row height and visual size).
          • aria-rowcount on the grid tells us the TRUE total row count, which
            we surface as metadata so the caller knows this is a partial extract.

        Args:
            visual_title: Exact first-line title of the visual.

        Returns:
            List of dicts. Keys are column header texts. Each dict is one row.
            The first dict may contain a special key '__meta__' with:
              {'total_rows': N, 'visible_rows': M, 'partial': True/False}
        """
        safe_title = visual_title.replace("'", "\\'")
        result = self.page.evaluate(f"""
            () => {{
                // Find the visual-container whose title matches
                const innerDivs = document.querySelectorAll('[aria-roledescription]');
                let targetVc = null;
                for (const div of innerDivs) {{
                    const vc = div.closest('visual-container');
                    if (!vc) continue;
                    const firstLine = (vc.innerText || '').trim().split('\\n')[0].trim();
                    if (firstLine === '{safe_title}') {{
                        targetVc = vc;
                        break;
                    }}
                }}
                if (!targetVc) return {{ error: 'visual not found', rows: [] }};

                // Find the grid element (PBI table visual uses role='grid')
                const grid = targetVc.querySelector('[role="grid"], [role="treegrid"]');
                if (!grid) return {{ error: 'no grid found', rows: [] }};

                const totalRows  = parseInt(grid.getAttribute('aria-rowcount') || '0', 10);
                const totalCols  = parseInt(grid.getAttribute('aria-colcount')  || '0', 10);

                // Read column headers from the first rowgroup's columnheader cells
                const headers = [];
                const headerCells = grid.querySelectorAll(
                    '[role="columnheader"], [role="rowheader"]:first-child'
                );
                for (const cell of headerCells) {{
                    // PBI puts the text in a span inside the header cell
                    const txt = (cell.innerText || '').trim().replace(/\\n/g, ' ');
                    if (txt) headers.push(txt);
                }}

                // Read data rows from gridcell elements
                const rows = [];
                const dataRows = grid.querySelectorAll('[role="row"]:not([aria-hidden="true"])');
                for (const row of dataRows) {{
                    const cells = row.querySelectorAll('[role="gridcell"], [role="rowheader"]');
                    if (cells.length === 0) continue;
                    const rowData = {{}};
                    cells.forEach((cell, i) => {{
                        const key = headers[i] || `col_${{i}}`;
                        rowData[key] = (cell.innerText || '').trim().replace(/\\n/g, ' ');
                    }});
                    // Skip rows that are all empty (PBI sometimes renders ghost rows)
                    if (Object.values(rowData).every(v => v === '')) continue;
                    rows.push(rowData);
                }};

                return {{
                    total_rows:   totalRows,
                    visible_rows: rows.length,
                    partial:      totalRows > 0 && rows.length < totalRows,
                    headers:      headers,
                    rows:         rows,
                }};
            }}
        """)

        if not result or result.get("error"):
            log.debug(
                f"[dom-table] No grid found in '{visual_title}': "
                f"{result.get('error', 'unknown error')}"
            )
            return []

        total   = result.get("total_rows", 0)
        visible = result.get("visible_rows", 0)
        partial = result.get("partial", False)
        rows    = result.get("rows", [])

        if partial:
            log.warning(
                f"[dom-table] '{visual_title}' — virtualised table: "
                f"{visible} of {total} rows in DOM. "
                f"Only visible rows extracted. "
                f"For full data use SQL direct comparison."
            )
        else:
            log.info(
                f"[dom-table] '{visual_title}' — extracted {visible} rows "
                f"(total per aria-rowcount: {total})"
            )

        # Prepend a metadata sentinel row so callers know it's partial
        if partial and rows:
            meta = {"__meta__": f"PARTIAL: {visible}/{total} rows visible in DOM"}
            rows = [meta] + rows

        return rows

    def extract_table_data(
        self,
        visual_title: str,
        visual_type: str | None = None,
        visual_index: int | None = None,
    ) -> list[dict]:
        """
        Extract underlying data from a chart/table visual.

        For PTW (Publish-to-Web) mode:
          Strategy A — Aria-label scraping (headless-safe, no clicks):
            Reads Power BI's accessibility aria-labels directly from SVG data points.
            Returns {category, measure, value} dicts.
          Strategy A2 — DOM grid/table row scraping (headless-safe, no clicks):
            Reads PBI Table/Matrix visuals directly from the DOM using role='grid'.
          Strategy B — "Show as a table" UI flow (requires headed or accessible chart):
            Right-clicks / hovers to open context menu → Show as a table.

        For Org mode:
          Only Strategy B (right-click context menu) is used.

        Args:
            visual_title: Exact title of the chart or table visual.  Pass an
                          empty string if using type+index strategy.
            visual_type:  ``aria-roledescription`` of the visual (e.g.
                          ``\"Clustered column chart\"``) — used when
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

        # ── Strategy A (PTW only): aria-label scraping — headless-safe, no clicks ──
        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB and visual_title:
            try:
                aria_rows = self._extract_chart_data_ptw_aria(visual_title)
                if aria_rows:
                    log.info(
                        f"[aria] Extracted {len(aria_rows)} data points from "
                        f"'{label}' via accessibility aria-labels"
                    )
                    return aria_rows
                else:
                    log.debug(
                        f"[aria] No aria-label data points found in '{label}' "
                        f"— falling through to Show-as-table UI flow"
                    )
            except Exception as e:
                log.debug(f"[aria] Extraction failed for '{label}': {e} — trying UI flow")

        # ── Strategy A2 (PTW only): DOM grid/table row scraping ──────────────────
        # For Table and Matrix visuals in PTW — PBI renders data as div[role='grid']
        # with div[role='gridcell'] children. No clicks or context menus needed.
        # Note: virtualised scrolling means only visible rows (~20-40) are in DOM.
        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB and visual_title:
            try:
                dom_rows = self._extract_table_visual_ptw_dom(visual_title)
                if dom_rows:
                    data_rows = [r for r in dom_rows if "__meta__" not in r]
                    log.info(
                        f"[dom-table] Extracted {len(data_rows)} visible rows from "
                        f"'{label}' via DOM grid scraping"
                    )
                    return dom_rows  # includes meta row so report shows partial warning
                else:
                    log.debug(
                        f"[dom-table] No grid rows found in '{label}' "
                        f"— falling through to Show-as-table UI flow"
                    )
            except Exception as e:
                log.debug(f"[dom-table] Extraction failed for '{label}': {e} — trying UI flow")

        # ── Strategy B: Show as a table UI flow ──────────────────────────────────
        # PTW: disabled by PBI for most visual types (no More Options button rendered).
        # Org mode: right-click context menu always works.

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

        # ── Open the visual context menu ─────────────────────────────────────
        # PTW mode: the outer visual-container custom element has ZERO pixel
        # dimensions in PTW embeds — can't hover or click it. The inner
        # [aria-roledescription] div has real dimensions (e.g. 450×300) and
        # is what PBI attaches header hover effects to.
        # Org mode: right-click on the outer container works normally.
        context_opened = False

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            # Resolve the inner div with real pixel dimensions
            if visual_title:
                title_el = self._find_visual_inner_div_ptw(visual_title)
            else:
                # type+index: _find_visual_by_title tags inner div as data-pw-inner-type-idx
                self._find_visual_by_title(visual_title, visual_type, visual_index)
                tag_value = f"{visual_type}_{visual_index}"
                title_el = self.page.locator(
                    f"[data-pw-inner-type-idx='{tag_value}']"
                ).first
        else:
            title_el = self._find_visual_by_title(visual_title, visual_type, visual_index)

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            # Strategy 1: Get element coordinates → move mouse → More Options button
            # Now that title_el is the inner div (real pixel dimensions), this works.
            try:
                # Scroll into view first
                title_el.evaluate("el => el.scrollIntoView({block:'center', inline:'center'})")
                self.page.wait_for_timeout(400)

                # Get bounding rect — the inner div has real dimensions unlike the shell
                rect = title_el.evaluate("""el => {
                    const r = el.getBoundingClientRect();
                    return {x: r.left + r.width/2, y: r.top + r.height/2,
                            w: r.width, h: r.height};
                }""")
                cx, cy = rect["x"], rect["y"]

                if cx > 0 and cy > 0 and rect["w"] > 0:
                    log.debug(f"Inner div bounding rect for '{label}': {rect}")
                    self.page.mouse.move(cx, cy)
                    self.page.wait_for_timeout(700)  # let PBI fade-in the header buttons
                else:
                    raise ValueError(f"Inner div bounding rect is zero/off-screen: {rect}")

                # Look for More Options button scoped inside the visual
                more_btn = title_el.locator(
                    "button[aria-label='More options'], "
                    "button[title='More options'], "
                    "[class*='visualHeaderItemsContainer'] button:last-of-type, "
                    "[class*='moreOptions']"
                ).first
                more_btn.wait_for(state="visible", timeout=3_000)
                more_btn.click()

                ctx.locator(PBILocators.PTW_CONTEXT_MENU).wait_for(
                    state="visible", timeout=4_000
                )
                context_opened = True
                log.debug(f"Context menu opened via mouse.move+More Options for '{label}'")
            except Exception as e:
                log.debug(f"mouse.move+More Options failed for '{label}': {e} — trying right-click")

            # Strategy 2: JS dispatch mouseover events + right-click fallback
            if not context_opened:
                try:
                    title_el.evaluate("""el => {
                        el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
                        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true}));
                    }""")
                    self.page.wait_for_timeout(500)

                    title_el.click(button="right", force=True, timeout=5_000)
                    ctx.locator(PBILocators.PTW_CONTEXT_MENU).wait_for(
                        state="visible", timeout=4_000
                    )
                    context_opened = True
                    log.debug(f"Context menu opened via JS events + right-click for '{label}'")
                except Exception as e:
                    log.debug(f"JS events + right-click failed for '{label}': {e}")

        else:
            # Org mode — right-click works on the outer container
            title_el.click(button="right", force=True)
            context_menu = ctx.locator(PBILocators.ORG_CONTEXT_MENU)
            context_menu.wait_for(state="visible", timeout=5_000)
            context_opened = True


        if not context_opened:
            raise ValueError(
                f"Could not open context menu for visual '{label}'. "
                f"The dashboard may not support 'Show as a table' in PTW embed mode, "
                f"or the visual header buttons are not accessible in headless mode. "
                f"Try running with headed=True to debug."
            )

        # Check if \"Show as a table\" is actually available in the context menu
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
