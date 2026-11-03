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
from config.settings import PBI_RENDER_TIMEOUT, PBI_PAGE_SWITCH_WAIT, PBI_IFRAME_PROBE_TIMEOUT, PBI_PAGELIST_TIMEOUT, QLIK_FILTER_WAIT, LOG_DIR
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



_IFRAME_SEL = (
    "iframe[title*='Power BI'], "
    "iframe[name*='PowerBI'], "
    "iframe[src*='powerbi.com'], "
    "iframe[src*='report-iframe']"
)

_READY_SEL = (
    "visual-container, "
    "[class*='visualContainer'], "
    "[class*='visual-container'], "
    "[role='region'][aria-label]"
)

_PAGE_TAB_SELECTORS = [
    "button[aria-label][class*='paginationButton']",
    "[class*='pageNavigation'] [role='tab']",
    "[class*='pageNavigation'] button",
    "[class*='page-navigation'] [role='tab']",
    "[class*='page-navigation'] button",
    "[aria-label*='page navigation' i] [role='tab']",
    "[aria-label*='page navigation' i] button",
    "[role='tablist'] [role='tab']",
    "li[role='tab']",
    "[role='tab']",
    "[aria-label='Pages' i] [role='listitem']",
    "[aria-label='Pages' i] button",
]

_NOT_PAGE_NAMES = {
    "pages", "page navigation", "previous page", "next page", "show pages", "hide pages",
    "sheets", "bookmarks", "stories",
}

_LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.windows.net")


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
        self._current_page: Optional[str] = None        # tracks last navigated-to page


    def _wait_for_dashboard_settle(self, max_ms: int = 6_000) -> None:
        """
        Poll until the Power BI canvas stops showing any loading indicator,
        or until max_ms milliseconds elapse.

        Replaces flat wait_for_timeout(PBI_PAGE_SWITCH_WAIT) calls after slicer
        interactions. Dashboard is considered settled when NONE of the known
        loading selectors are visible.

        Falls back gracefully — if no loading indicator ever appears (fast render),
        returns immediately after the first poll succeeds.
        """
        import time
        selectors = [
            "[class*='loadingSpinner']",
            "[class*='loading-container']",
            "[aria-label*='Loading']",
            "[class*='spinner']",
        ]
        deadline = time.time() + max_ms / 1_000
        while time.time() < deadline:
            try:
                any_visible = any(
                    self.page.locator(s).is_visible(timeout=200)
                    for s in selectors
                )
                if not any_visible:
                    return  # Dashboard has settled
            except Exception:
                pass  # Timeout on locator check — treat as not visible
            self.page.wait_for_timeout(300)
        # max_ms elapsed — continue regardless

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Context: returns the correct page/frame context for the current embed mode
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_context(self) -> Union[Page, FrameLocator]:
        """
        Return the correct Power BI DOM context.

        This report is authenticated/org-based but its Power BI visuals
        are rendered directly in the outer page DOM, not inside an iframe.
        """
        return self.page

    def _get_org_frame(self) -> FrameLocator:
        """
        Diagnostic Power BI iframe discovery.

        Checks all iframes on the page and logs:
          - iframe index
          - title
          - src
          - whether the frame contains Power BI visual elements

        This is temporary diagnostic logic to identify the actual
        report iframe used by the secure Power BI report.
        """

        log.info("========== POWER BI IFRAME DIAGNOSTIC START ==========")

        iframe_locator = self.page.locator("iframe")

        try:
            iframe_count = iframe_locator.count()
        except Exception as exc:
            log.error(f"Could not count iframes: {exc}")
            raise PwTimeoutError(
                "Unable to inspect iframes on the Power BI page."
            )

        log.info(f"Total iframes found on page: {iframe_count}")

        for i in range(iframe_count):
            try:
                iframe = iframe_locator.nth(i)

                title = iframe.get_attribute("title") or ""
                src = iframe.get_attribute("src") or ""
                aria_label = iframe.get_attribute("aria-label") or ""

                log.info(
                    f"IFRAME[{i}] "
                    f"title='{title}' "
                    f"aria-label='{aria_label}' "
                    f"src='{src[:300]}'"
                )

                # Get the frame corresponding to this iframe.
                frame = self.page.frame_locator("iframe").nth(i)

                # Check whether the frame has loaded.
                try:
                    frame.locator("body").wait_for(
                        state="attached",
                        timeout=3_000
                    )
                    log.info(f"IFRAME[{i}] body: FOUND")
                except Exception:
                    log.info(f"IFRAME[{i}] body: NOT AVAILABLE")
                    continue

                # Check several possible Power BI DOM signatures.
                visual_container_count = 0
                aria_visual_count = 0
                visual_role_count = 0

                try:
                    visual_container_count = frame.locator(
                        "visual-container"
                    ).count()
                except Exception:
                    pass

                try:
                    aria_visual_count = frame.locator(
                        "[aria-roledescription]"
                    ).count()
                except Exception:
                    pass

                try:
                    visual_role_count = frame.locator(
                        "[data-automation-type='visualContainer']"
                    ).count()
                except Exception:
                    pass

                log.info(
                    f"IFRAME[{i}] Power BI elements: "
                    f"visual-container={visual_container_count}, "
                    f"aria-roledescription={aria_visual_count}, "
                    f"visualContainer={visual_role_count}"
                )

                # Get a small amount of body text for diagnostics.
                try:
                    body_text = frame.locator("body").inner_text(
                        timeout=2_000
                    ).strip()

                    body_preview = " ".join(body_text.split())[:500]

                    log.info(
                        f"IFRAME[{i}] body text preview: "
                        f"'{body_preview}'"
                    )
                except Exception as exc:
                    log.debug(
                        f"IFRAME[{i}] body text unavailable: {exc}"
                    )

                # If this iframe actually contains Power BI visuals,
                # use it as the report iframe.
                if (
                    visual_container_count > 0
                    or aria_visual_count > 0
                    or visual_role_count > 0
                ):
                    log.info(
                        f"========== ACTUAL POWER BI IFRAME FOUND: "
                        f"INDEX {i} =========="
                    )
                    return frame

            except Exception as exc:
                log.warning(
                    f"IFRAME[{i}] inspection failed: {exc}"
                )
                continue

        log.info("No iframe containing Power BI visuals was found.")
        log.info("========== POWER BI IFRAME DIAGNOSTIC END ==========")

        raise PwTimeoutError(
            "Could not locate an iframe containing Power BI visuals. "
            "The report may be rendered in the outer page DOM."
        )

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
        self._report_url = url
        current_url = self.page.url
        if "powerbi.com" in current_url and ("/reports/" in current_url or "/groups/" in current_url):
            try:
                if self._ctx().locator(_READY_SEL).first.is_visible(timeout=1_500):
                    log.info("PBI report already open and canvas active — reusing warm page")
                    return
            except Exception:
                pass
        log.info(f"Navigating to: {url}")

        self.page.goto(url, timeout=PBI_RENDER_TIMEOUT)

        if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB:
            # PTW: skip networkidle — map visuals (Azure Maps, Filled Map) make
            # continuous tile requests that never settle.  The explore-canvas
            # selector in _wait_for_ptw_render() is the authoritative ready signal.
            self._wait_for_ptw_render()
        else:
            # Org report: wait for DOM + subresources ("load"), not networkidle.
            # networkidle is unreliable on dashboards with live/streaming tiles.
            self.page.wait_for_load_state("load", timeout=PBI_RENDER_TIMEOUT)
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
        self.page.wait_for_load_state("load", timeout=PBI_RENDER_TIMEOUT)
        log.info("Org report render complete")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Page Navigation
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_all_page_names(self) -> list[str]:
        """
        Return actual Power BI report page names.

        For secure/org reports, Power BI exposes a "Report pages"
        section in the Pages pane. We deliberately scope discovery
        to that section instead of scanning every title/aria-label
        on the page.

        This prevents visual titles, KPI values, buttons and other
        Power BI shell elements from being reported as page names.
        """
        names: list[str] = []

        def add_name(value: Optional[str]) -> None:
            if not value:
                return

            value = value.strip()

            if (
                value
                and value not in names
                and len(value) <= 150
            ):
                names.append(value)

        contexts = []

        if self._embed_mode == EMBED_MODE_ORG_REPORT:
            try:
                contexts.append(self._get_org_frame())
            except Exception:
                pass

            contexts.append(self.page)
        else:
            contexts.append(self.page)

        for ctx in contexts:
            try:
                report_pages = ctx.get_by_text(
                    "Report pages",
                    exact=True,
                ).first

                if report_pages.count() == 0:
                    continue

                if not report_pages.is_visible(timeout=1_000):
                    continue

                # Walk up the DOM to find the container holding
                # the actual report page navigation items.
                for depth in range(1, 9):
                    try:
                        parent = report_pages

                        for _ in range(depth):
                            parent = parent.locator("xpath=..")

                        # First collect semantic navigation elements.
                        candidates = parent.locator(
                            "[role='button'], "
                            "[role='tab'], "
                            "[role='treeitem'], "
                            "[role='link']"
                        ).all()

                        found_in_level = 0

                        for el in candidates:
                            try:
                                if not el.is_visible(timeout=200):
                                    continue

                                text = (
                                    el.get_attribute("title")
                                    or el.get_attribute("aria-label")
                                    or el.inner_text()
                                    or ""
                                ).strip()

                                # Ignore generic Power BI controls.
                                if not text:
                                    continue

                                if text.lower() in {
                                    "report pages",
                                    "hide the pages pane",
                                    "show the pages pane",
                                    "pages",
                                    "filters",
                                }:
                                    continue

                                # Avoid obviously non-page controls.
                                if len(text) > 150:
                                    continue

                                add_name(text)
                                found_in_level += 1

                            except Exception:
                                continue

                        if found_in_level > 0:
                            log.info(
                                f"Found {len(names)} report page labels "
                                f"from Power BI Pages pane: {names}"
                            )
                            return names

                    except Exception:
                        continue

            except Exception:
                continue

        if self._embed_mode == EMBED_MODE_ORG_REPORT:
            log.warning(
                "Power BI Pages pane could not be located for secure/org report."
            )
            return []

        log.debug(
            "Pages pane not found/empty — falling back to legacy whole-page discovery"
        )

        # Fallback: existing report-tab discovery.
        for ctx in contexts:
            try:
                tab_sel = (
                    PBILocators.PTW_PAGE_TAB
                    if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
                    else PBILocators.ORG_PAGE_TAB
                )

                tabs = ctx.locator(tab_sel).all()

                for tab in tabs:
                    try:
                        if not tab.is_visible(timeout=200):
                            continue

                        title = (
                            tab.get_attribute("title")
                            or tab.get_attribute("aria-label")
                            or tab.inner_text()
                            or ""
                        ).strip()

                        if title:
                            add_name(title)

                    except Exception:
                        continue

            except Exception:
                continue

        log.info(
            f"Found {len(names)} report page labels: {names}"
        )

        return names


    def _try_page_navigator_in(self, ctx, page_name: str) -> bool:
        """
        Try to click a page from the Power BI Pages pane.

        For secure/org reports we first locate the "Report pages"
        section and search only inside its DOM container.

        This is intentionally scoped so KPI cards, visual titles,
        slicers and Power BI shell controls are not mistaken for
        report pages.
        """
        target = str(page_name).strip()

        if not target:
            return False

        # ------------------------------------------------------------------
        # Strategy 1:
        # Find the Power BI "Report pages" section and search inside it.
        # ------------------------------------------------------------------
        try:
            report_pages = ctx.get_by_text(
                "Report pages",
                exact=True,
            ).first

            if report_pages.count() > 0 and report_pages.is_visible(
                timeout=1_000
            ):
                log.debug(
                    f"Found Power BI 'Report pages' section while "
                    f"looking for '{target}'"
                )

                # Walk up through likely Pages-pane containers.
                for depth in range(1, 9):
                    try:
                        container = report_pages

                        for _ in range(depth):
                            container = container.locator("xpath=..")

                        # --------------------------------------------------
                        # Exact visible text inside the Pages pane.
                        # --------------------------------------------------
                        matches = container.get_by_text(
                            target,
                            exact=True,
                        )

                        count = matches.count()

                        if count == 0:
                            continue

                        log.debug(
                            f"Found {count} exact '{target}' "
                            f"match(es) inside Pages pane at depth {depth}"
                        )

                        for i in range(count):
                            el = matches.nth(i)

                            try:
                                if not el.is_visible(timeout=300):
                                    continue

                                el.click(timeout=3_000)

                                self.page.wait_for_timeout(
                                    PBI_PAGE_SWITCH_WAIT
                                )

                                self._wait_for_dashboard_settle()

                                log.info(
                                    f"Page navigator: clicked "
                                    f"'{target}' inside Report pages pane"
                                )

                                self._current_page = target
                                return True

                            except Exception as exc:
                                log.debug(
                                    f"Could not click exact page text "
                                    f"'{target}' at depth {depth}, "
                                    f"match {i}: {exc}"
                                )
                                continue

                    except Exception:
                        continue

        except Exception as exc:
            log.debug(
                f"Report pages scoped navigation failed for "
                f"'{target}': {exc}"
            )

        # ------------------------------------------------------------------
        # Strategy 2:
        # Exact title attribute.
        # ------------------------------------------------------------------
        try:
            elements = ctx.locator(
                f"[title={repr(target)}]"
            )

            for i in range(elements.count()):
                el = elements.nth(i)

                try:
                    if not el.is_visible(timeout=300):
                        continue

                    el.click(timeout=3_000)

                    self.page.wait_for_timeout(
                        PBI_PAGE_SWITCH_WAIT
                    )

                    self._wait_for_dashboard_settle()

                    log.info(
                        f"Page navigator: clicked '{target}' "
                        f"using title attribute"
                    )

                    self._current_page = target
                    return True

                except Exception:
                    continue

        except Exception:
            pass

        # ------------------------------------------------------------------
        # Strategy 3:
        # Exact aria-label.
        # ------------------------------------------------------------------
        try:
            elements = ctx.locator(
                f"[aria-label={repr(target)}]"
            )

            for i in range(elements.count()):
                el = elements.nth(i)

                try:
                    if not el.is_visible(timeout=300):
                        continue

                    el.click(timeout=3_000)

                    self.page.wait_for_timeout(
                        PBI_PAGE_SWITCH_WAIT
                    )

                    self._wait_for_dashboard_settle()

                    log.info(
                        f"Page navigator: clicked '{target}' "
                        f"using aria-label"
                    )

                    self._current_page = target
                    return True

                except Exception:
                    continue

        except Exception:
            pass

        # ------------------------------------------------------------------
        # Strategy 4:
        # Exact accessible button.
        # ------------------------------------------------------------------
        try:
            locator = ctx.get_by_role(
                "button",
                name=target,
                exact=True,
            )

            for i in range(locator.count()):
                el = locator.nth(i)

                try:
                    if not el.is_visible(timeout=300):
                        continue

                    el.click(timeout=3_000)

                    self.page.wait_for_timeout(
                        PBI_PAGE_SWITCH_WAIT
                    )

                    self._wait_for_dashboard_settle()

                    log.info(
                        f"Page navigator: clicked '{target}' "
                        f"using button role"
                    )

                    self._current_page = target
                    return True

                except Exception:
                    continue

        except Exception:
            pass

        # ------------------------------------------------------------------
        # Strategy 5:
        # Existing report tab navigation.
        # ------------------------------------------------------------------
        try:
            locator = ctx.get_by_role(
                "tab",
                name=target,
                exact=True,
            )

            for i in range(locator.count()):
                el = locator.nth(i)

                try:
                    if not el.is_visible(timeout=300):
                        continue

                    el.click(timeout=3_000)

                    self.page.wait_for_timeout(
                        PBI_PAGE_SWITCH_WAIT
                    )

                    self._wait_for_dashboard_settle()

                    log.info(
                        f"Page navigator: clicked '{target}' "
                        f"using tab role"
                    )

                    self._current_page = target
                    return True

                except Exception:
                    continue

        except Exception:
            pass

        return False


    def switch_to_page(self, page_name: str) -> None:
        """
        Navigate to a Power BI report page.

        Pipeline Hygiene is an authenticated/org report, but the
        report navigation is rendered in the outer page DOM.
        """

        log.info(f"Switching to page: '{page_name}'")
        ctx = self._ctx()
        try:
            backdrop = self.page.locator(".cdk-overlay-backdrop, [role='dialog']").first
            if backdrop.count() > 0 and backdrop.is_visible(timeout=500):
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            active_tab = ctx.locator(
                f"[role='tab'][aria-label*='{page_name}' i][aria-selected='true'], "
                f"[role='tab'][aria-label*='{page_name} Selected' i], "
                f"button[data-testid='pages-navigation-list-items'][aria-selected='true']:has-text('{page_name}')"
            ).first
            if active_tab.count() > 0 and active_tab.is_visible(timeout=1_000):
                log.info(f"Already on page '{page_name}' — skipping navigation click")
                return
        except Exception:
            pass
        for sel in [
            f"[role='tab'][aria-label*='{page_name}' i]",
            f"button[data-testid='pages-navigation-list-items']:has-text('{page_name}')",
            f"[role='tab']:has-text('{page_name}')",
        ]:
            try:
                tab = ctx.locator(sel).first
                if tab.count() > 0 and tab.is_visible(timeout=1_500):
                    tab.scroll_into_view_if_needed()
                    tab.click(force=True)
                    self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                    log.info(f"Switched to page '{page_name}' via tab selector: {sel}")
                    return
            except Exception:
                pass

        # ---------------------------------------------------------
        # 1. Primary: outer-page DOM navigation
        # ---------------------------------------------------------
        try:
            page_nav = self.page.locator(
                f'[aria-label="{page_name}"]'
            )

            count = page_nav.count()

            if count > 0:
                for i in range(count):
                    item = page_nav.nth(i)

                    try:
                        if item.is_visible():
                            item.click(force=True)
                            self.page.wait_for_timeout(
                                PBI_PAGE_SWITCH_WAIT
                            )

                            log.info(
                                f"Page navigator: clicked "
                                f"'{page_name}' using aria-label"
                            )
                            return

                    except Exception as exc:
                        log.debug(
                            f"Could not click aria-label navigation "
                            f"element {i}: {exc}"
                        )

        except Exception as exc:
            log.debug(
                f"Outer DOM aria-label navigation failed: {exc}"
            )

        # ---------------------------------------------------------
        # 2. Fallback: text/title-based outer DOM navigation
        # ---------------------------------------------------------
        try:
            candidates = [
                self.page.get_by_text(
                    page_name,
                    exact=True
                ),
                self.page.locator(
                    f'[title="{page_name}"]'
                ),
            ]

            for candidate in candidates:
                count = candidate.count()

                for i in range(count):
                    item = candidate.nth(i)

                    try:
                        if item.is_visible():
                            item.click(force=True)
                            self.page.wait_for_timeout(
                                PBI_PAGE_SWITCH_WAIT
                            )

                            log.info(
                                f"Page navigator: clicked "
                                f"'{page_name}' using outer DOM fallback"
                            )
                            return

                    except Exception as exc:
                        log.debug(
                            f"Outer DOM fallback click failed: {exc}"
                        )

        except Exception as exc:
            log.debug(
                f"Outer DOM text/title navigation failed: {exc}"
            )

        # ---------------------------------------------------------
        # 3. Legacy iframe navigation fallback
        # ---------------------------------------------------------
        try:
            frame = self._get_org_frame()

            tab_selector = (
                PBILocators.ORG_PAGE_TAB_BY_NAME
                .format(page_name=page_name)
            )

            tab = frame.locator(tab_selector)

            tab.wait_for(
                state="visible",
                timeout=5_000
            )

            tab.click()

            self.page.wait_for_timeout(
                PBI_PAGE_SWITCH_WAIT
            )

            log.info(
                f"Page navigator: clicked '{page_name}' "
                f"inside org iframe"
            )
            return

        except Exception as exc:
            log.debug(
                f"Legacy iframe page navigation failed: {exc}"
            )

        raise PageNotFoundError(
            f"Page '{page_name}' could not be located "
            f"in the Power BI report."
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
        A greedy `of(\\d+)` would incorrectly match "of31" → 31 pages.

        Fix: use a non-greedy match with a lookahead that detects when a
        section-listing digit-dash pattern follows immediately:

            of(\\d+?)(?=\\d+\\s*-)   →  "of3" (then lookahead sees "1-") ✓

        If no section listings are present (clean "1of19"), the lookahead
        won't match and we fall back to the standard greedy match → 19 ✓

        ── DOM Validation ────────────────────────────────────────────────────
        After parsing the indicator text, the result is validated against the
        next-page button's visibility.  PBI sets `hidden` on nav buttons when
        the report has only one navigable page.  If the next button is hidden
        we return 1 regardless of what the indicator text says — this catches
        the rare case where the indicator text itself is misleading (e.g.
        "1of11" when section label "1- Overview" concatenates with "1of1").

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

                        # ── DOM validation: trust the button over the text ──
                        # If the next-page button is hidden, PBI itself is saying
                        # there is nowhere to navigate — treat as single page.
                        # This catches indicator misparses like "1of11" that are
                        # really "1of1" + section label "1-" concatenated.
                        if count > 1:
                            try:
                                next_btn = self.page.locator(
                                    PBILocators.PTW_NAV_NEXT_PAGE
                                ).first
                                is_hidden = next_btn.evaluate(
                                    "el => el.hidden || el.closest('[hidden]') !== null"
                                )
                                if is_hidden:
                                    log.warning(
                                        f"Indicator says {count} pages but next-page "
                                        "button is hidden — report is single-page. "
                                        f"(Indicator text was: '{raw_text}')"
                                    )
                                    return 1
                            except Exception:
                                pass  # selector not found — nav buttons absent → 1 page

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

        If the button has `hidden` attribute it means PBI has determined there
        is no page to navigate to (single-page report or already at boundary).
        In that case we raise ValueError immediately rather than timing out.

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
            btn.wait_for(state="attached", timeout=3_000)  # attached, not visible
        except PwTimeoutError:
            btn = self.page.locator(fallback_sel).first

        # ── Early exit if button is hidden ──────────────────────────────────
        # PBI sets hidden="" on nav buttons when no further navigation exists.
        # Clicking a hidden element always fails; bail out immediately.
        try:
            is_hidden = btn.evaluate(
                "el => el.hidden || el.closest('[hidden]') !== null "
                "|| window.getComputedStyle(el).display === 'none'"
            )
            if is_hidden:
                raise ValueError(
                    f"Nav button ({direction}) is hidden — report has no more pages "
                    "in this direction. If page count > 1 was reported, the indicator "
                    "text was misread (section label concatenation quirk)."
                )
        except ValueError:
            raise
        except Exception:
            pass  # evaluate failed (element detached) — proceed and let click fail naturally

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
            with 'Sales\\n\\n$1.7M\\n...')
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
                try:
                    self.go_to_next_page()
                except ValueError as e:
                    # Nav button is hidden — indicator text was misread.
                    # Stop here; we've already captured all real pages.
                    log.warning(
                        f"Stopping arrow-nav at page {idx + 1}/{page_count}: {e}"
                    )
                    break

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

        # Auto-discover slicers: any visual of type containing 'slicer' or category 'slicer'
        slicers = []
        for v in visuals:
            vtype_lower = v.get('type', '').lower()
            if 'slicer' in vtype_lower or v.get('category') == 'slicer':
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

                // Noise patterns for titles that are NOT real visual names.
                // ── IMPORTANT: "Scroll up" MUST be here ──────────────────────
                // PBI Table/Matrix visuals render a scroll affordance ("Scroll up")
                // as the very first text node in the visual body, BEFORE any data
                // rows. This means lines[0] of vc.innerText = "Scroll up" for
                // Table/Matrix visuals. Flagging it as noisy causes the discover
                // script to use strategy="type_index" (locate by type+position)
                // instead of strategy="title", which is the correct behaviour for
                // untitled tables.
                function isNoisyTitle(title) {
                    if (!title || title.length === 0) return true;
                    // Arrow / directional symbols only
                    if (title.length <= 2 && /^[⇗⇘⇒⇑⇓→↑↓▲▼]/.test(title)) return true;
                    // Leading currency/paren
                    if (/^[\\$\\(]/.test(title)) return true;
                    // Pure numbers / percentages
                    if (/^[\\d,\\.\\s%]+$/.test(title)) return true;
                    // Formatted numeric values like "$4.2M", "87.5%"
                    if (/^[\\$\\(]?[\\d,\\.]+[KMBkmbTt%]?\\)?$/.test(title)) return true;
                    // Scroll affordance text from Table/Matrix visual body
                    if (/^Scroll (up|down|left|right)$/i.test(title)) return true;
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
                    let roleDesc = div.getAttribute('aria-roledescription') || '';
                    const vc = div.closest('visual-container');
                    const allText  = (vc ? vc.innerText : div.innerText || '').trim();
                    const lines    = allText.split('\\n').map(l => l.trim()).filter(Boolean);

                    // ── Fallback type detection for visuals with empty aria-roledescription ──
                    // Power BI Table / Matrix visuals in PTW mode often lack an
                    // aria-roledescription attribute on their container.
                    // Detect them by inspecting DOM structure and text content.
                    if (!roleDesc || roleDesc === '') {
                        if (
                            (vc && vc.querySelector("[role='grid'], [role='table'], div.pivotTableCellWrapper, [class*='tableContainer'], [class*='matrixContainer']")) ||
                            /^Scroll (up|down|left|right)/i.test(allText) ||
                            lines.some(l => /^(Row Selection|Rank|Agent ID|Agent Name)$/i.test(l))
                        ) {
                            roleDesc = 'Table';
                        }
                    }

                    // Title = lines[0] of the visual's full innerText.
                    // For Table/Matrix: lines[0] = "Scroll up" → isNoisyTitle=true
                    //   → discover script uses type_index strategy (correct).
                    // For KPI Card: lines[0] = label text e.g. "Page Views"
                    //   → used as the visual title (correct).
                    // For Charts: lines[0] = first axis tick or aria label
                    //   → may be noisy; discover script falls back to type_index.
                    const title = lines.length > 0 ? lines[0] : '';

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

    def _extract_card_value_org(self, visual_title: str) -> str:
        """
        Extract KPI value from authenticated Power BI report.

        Pipeline Hygiene is an authenticated/org report, but its Power BI
        visuals are rendered in the outer page DOM. There is no report iframe.

        Extraction order:
          1. ARIA/accessibility label of the Card visual.
          2. Visual container innerText.
          3. Generic visual discovery fallback.

        Power BI commonly exposes cards as:
            "KPI Title, 1.45bn card"
        """

        log.info(
            f"Org report KPI extraction started for: '{visual_title}'"
        )

        # ---------------------------------------------------------
        # Strategy 1 — Power BI accessibility / ARIA label
        # ---------------------------------------------------------
        try:
            result = self.page.evaluate(
                """
                (targetTitle) => {

                    const clean = value =>
                        (value || "")
                            .replace(/\\s+/g, " ")
                            .trim();

                    const elements = Array.from(
                        document.querySelectorAll("[aria-label]")
                    );

                    // Exact card accessibility-label match.
                    for (const el of elements) {

                        const aria = clean(
                            el.getAttribute("aria-label")
                        );

                        if (!aria) {
                            continue;
                        }

                        /*
                         * Typical Power BI Card:
                         *
                         * "Unweighted Revenue of Overdue Opps, 1.45bn card"
                         */
                        if (
                            aria.startsWith(targetTitle + ",")
                            &&
                            /\\bcard\\b/i.test(aria)
                        ) {

                            let value = aria
                                .slice(targetTitle.length + 1)
                                .trim();

                            value = value.replace(
                                /\\s+card\\s*$/i,
                                ""
                            ).trim();

                            if (value) {
                                return {
                                    value: value,
                                    source: "aria-label",
                                    aria: aria
                                };
                            }
                        }

                        /*
                         * More tolerant case:
                         *
                         * "Unweighted Revenue of Overdue Opps 1.45bn card"
                         */
                        if (
                            aria.startsWith(targetTitle)
                            &&
                            /\\bcard\\b/i.test(aria)
                        ) {

                            let remainder = aria
                                .slice(targetTitle.length)
                                .trim();

                            remainder = remainder
                                .replace(/^[,;:\\-]+/, "")
                                .trim();

                            remainder = remainder.replace(
                                /\\s+card\\s*$/i,
                                ""
                            ).trim();

                            if (remainder) {
                                return {
                                    value: remainder,
                                    source: "aria-label-tolerant",
                                    aria: aria
                                };
                            }
                        }
                    }

                    return null;
                }
                """,
                visual_title,
            )

            if result and result.get("value"):

                raw_value = str(
                    result["value"]
                ).strip()

                log.info(
                    f"Card '{visual_title}' → raw value: "
                    f"'{raw_value}' "
                    f"(Power BI {result.get('source')})"
                )

                return raw_value

        except Exception as exc:
            log.debug(
                f"ARIA KPI extraction failed for "
                f"'{visual_title}': {exc}"
            )

        # ---------------------------------------------------------
        # Strategy 2 — Find the visual and inspect its DOM
        # ---------------------------------------------------------
        try:

            result = self.page.evaluate(
                """
                (targetTitle) => {

                    const clean = value =>
                        (value || "")
                            .replace(/\\s+/g, " ")
                            .trim();

                    const visualDivs = Array.from(
                        document.querySelectorAll(
                            "[aria-roledescription]"
                        )
                    );

                    for (const div of visualDivs) {

                        const vc =
                            div.closest("visual-container");

                        // Split on newlines FIRST, then clean each line.
                        // (clean() collapses newlines into spaces, so it
                        // must not run on the whole text before the split.)
                        const rawText = (
                            vc
                                ? vc.innerText
                                : div.innerText
                        ) || "";

                        const lines = rawText
                            .split("\\n")
                            .map(clean)
                            .filter(Boolean);

                        if (!lines.length) {
                            continue;
                        }

                        /*
                         * Standalone card:
                         *
                         * Title
                         * Value
                         */
                        if (
                            lines.length >= 2 &&
                            lines[0] === targetTitle
                        ) {

                            return {
                                value: lines[1],
                                source: "visual-innerText"
                            };
                        }

                        /*
                         * Multi-row / multi-KPI card.
                         */
                        for (
                            let i = 0;
                            i < lines.length;
                            i++
                        ) {

                            if (
                                lines[i] === targetTitle &&
                                i + 1 < lines.length
                            ) {

                                return {
                                    value: lines[i + 1],
                                    source: "visual-innerText-pair"
                                };
                            }
                        }
                    }

                    return null;
                }
                """,
                visual_title,
            )

            if result and result.get("value"):

                raw_value = str(
                    result["value"]
                ).strip()

                log.info(
                    f"Card '{visual_title}' → raw value: "
                    f"'{raw_value}' "
                    f"(outer visual DOM)"
                )

                return raw_value

        except Exception as exc:
            log.debug(
                f"Visual DOM KPI extraction failed for "
                f"'{visual_title}': {exc}"
            )

        # ---------------------------------------------------------
        # Targeted fallback — Overdue Contracts
        # Power BI exposes the title and KPI value separately.
        # ---------------------------------------------------------
        if visual_title == "# of Overdue Contracts":
            try:
                raw_value = self.page.evaluate(
                    """
                    (targetTitle) => {
                        const clean = s =>
                            (s || "").replace(/\\s+/g, " ").trim();

                        const title = [...document.querySelectorAll("*")]
                            .find(el => clean(el.textContent) === targetTitle);

                        if (!title) return null;

                        const titleRect = title.getBoundingClientRect();

                        const numeric = [...document.querySelectorAll("*")]
                            .filter(el => {
                                const text = clean(el.textContent);
                                if (!/^\\d[\\d,]*(?:\\.\\d+)?$/.test(text)) {
                                    return false;
                                }

                                const rect = el.getBoundingClientRect();

                                return (
                                    rect.width > 0 &&
                                    rect.height > 0 &&
                                    rect.left >= titleRect.left - 30 &&
                                    rect.right <= titleRect.right + 30 &&
                                    rect.top > titleRect.bottom &&
                                    rect.top < titleRect.bottom + 180
                                );
                            });

                        numeric.sort(
                            (a, b) =>
                                a.getBoundingClientRect().top -
                                b.getBoundingClientRect().top
                        );

                        return numeric.length
                            ? clean(numeric[0].textContent)
                            : null;
                    }
                    """,
                    visual_title,
                )

                if raw_value:
                    log.info(
                        f"Card '{visual_title}' → raw value: "
                        f"'{raw_value}' (targeted Power BI fallback)"
                    )
                    return raw_value

            except Exception as exc:
                log.debug(
                    f"Overdue Contracts targeted extraction failed: {exc}"
                )

        # ---------------------------------------------------------
        # Strategy 3 — Existing generalized visual discovery
        # ---------------------------------------------------------
        try:

            visuals = self._evaluate_ptw_visuals()

            matches = [
                visual
                for visual in visuals
                if str(
                    visual.get("title", "")
                ).strip() == visual_title
            ]

            for visual in matches:

                full_text = str(
                    visual.get("fullText", "")
                ).strip()

                if not full_text:
                    continue

                lines = [
                    line.strip()
                    for line in full_text.splitlines()
                    if line.strip()
                ]

                for i, line in enumerate(lines):

                    if line == visual_title:

                        if i + 1 < len(lines):

                            value = lines[i + 1].strip()

                            if value:
                                log.info(
                                    f"Card '{visual_title}' → "
                                    f"raw value: '{value}' "
                                    f"(visual discovery fallback)"
                                )
                                return value

        except Exception as exc:
            log.debug(
                f"Visual discovery KPI fallback failed for "
                f"'{visual_title}': {exc}"
            )

        raise ValueError(
            f"Could not extract KPI value for '{visual_title}' "
            f"from the authenticated Power BI report."
        )

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
                          ``\\"Clustered column chart\\"``) — used when
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

        Tries the following strategies in order:
          1. Find slicer container via _find_visual_by_title (iframe for classic
             org reports, outer page for publish-to-web).
          2. If strategy 1 fails (no iframe / flat-DOM org report), use a
             JavaScript-based approach directly on self.page to locate the
             slicer container and click the matching item.

        Args:
            slicer_title: Title of the slicer visual.
            value:        The slicer item to select (e.g. "California", "2024").
        """
        log.info(f"Setting slicer '{slicer_title}' to '{value}'")

        item_sel = (
            PBILocators.PTW_SLICER_ITEM
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SLICER_ITEM
        )
        search_sel = (
            PBILocators.PTW_SLICER_SEARCH
            if self._embed_mode == EMBED_MODE_PUBLISH_TO_WEB
            else PBILocators.ORG_SLICER_SEARCH
        )

        # --- Strategy 1: iframe / classic visual container path ---
        container = None
        try:
            container = self._find_visual_by_title(slicer_title)
            # Verify it actually points to something real
            _ = container.count()
        except Exception:
            container = None

        if container is not None:
            # Search input (if slicer has one)
            try:
                search = container.locator(search_sel)
                search.wait_for(timeout=3_000)
                search.fill(value)
                self.page.wait_for_timeout(500)
            except PwTimeoutError:
                log.debug(f"Slicer '{slicer_title}' has no search input — trying direct click")

            item = container.locator(f"{item_sel}:has-text('{value}')")
            item.click()
            self._wait_for_dashboard_settle()
            log.info(f"Slicer '{slicer_title}' set to '{value}'")
            return

        # --- Strategy 2: flat-DOM approach for modern org reports (no iframe) ---
        log.debug(
            f"Slicer '{slicer_title}' not found via iframe — "
            f"using flat-DOM approach on outer page"
        )

        # Step 2a: Diagnostic — log slicer structure to help debug selector mismatches
        slicer_info = self.page.evaluate("""
            (targetTitle) => {
                const info = [];
                for (const div of document.querySelectorAll('[aria-roledescription]')) {
                    const vc = div.closest('visual-container') || div.parentElement;
                    const rawText = (vc ? vc.innerText : div.innerText || '').trim();
                    const firstLine = rawText.split('\\n')[0].trim();
                    const role = div.getAttribute('aria-roledescription') || '';
                    if (!role.toLowerCase().includes('slicer')) continue;
                    info.push({
                        role: role,
                        firstLine: firstLine,
                        isTarget: firstLine === targetTitle,
                        slicerItems: (vc || div).querySelectorAll(
                            "[class*='slicerItemContainer']"
                        ).length,
                        listOptions: (vc || div).querySelectorAll(
                            "[role='option'], [role='listitem']"
                        ).length,
                        hasDropdownBtn: !!(vc || div).querySelector(
                            "[aria-haspopup], [class*='dropdown'], [class*='Dropdown'], "
                            + "button[class*='slicer']"
                        ),
                    });
                }
                return info;
            }
        """, slicer_title)
        log.info(f"Slicer DOM diagnostic for '{slicer_title}': {slicer_info}")

        # Step 2b: Find the slicer visual container using Playwright directly on self.page.
        # We look for ANY element with aria-roledescription containing 'slicer' that
        # lives inside a visual-container whose first line of text equals slicer_title.
        visual_data = {"loc": None}
        for info_item in (slicer_info or []):
            if info_item.get("isTarget"):
                visual_data["loc"] = self.page.locator(
                    f"visual-container:has-text('{slicer_title}')"
                ).first
                break

        if visual_data["loc"] is None:
            visual_data["loc"] = self.page.locator(
                f"[aria-roledescription*='licer']:near(:text('{slicer_title}'), 100)"
            ).first

        # Step 2c: Expand the slicer dropdown (if it is a dropdown-style slicer).
        # Close any lingering popups FIRST, then record heights_before baseline.
        try:
            self.page.keyboard.press("Escape")
            self.page.evaluate("""
                () => {
                    const lbs = Array.from(document.querySelectorAll('[role="listbox"], .popupContent'));
                    lbs.forEach(el => {
                        if (el.getBoundingClientRect().height > 20) {
                            document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                        }
                    });
                }
            """)
            self.page.wait_for_timeout(200)
        except Exception:
            pass

        heights_before = []
        try:
            heights_before = self.page.evaluate("""
                () => Array.from(document.querySelectorAll('[role="listbox"]'))
                     .map(el => {
                         const style = window.getComputedStyle(el);
                         const r = el.getBoundingClientRect();
                         if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0' || r.width === 0) return 0;
                         return r.height;
                     })
            """)
        except Exception:
            pass

        popup_selectors = [".popupContent", ".slicer-dropdown-menu", "[role='listbox']", "[class*='scrollRegion']"]
        try:
            expand_btn_selectors = [
                "div[class*='slicer-dropdown-content']",
                "div[class*='slicer-rest-of-header']",
                "div[class*='slicerDropdown']",
                "i[class*='dropdown']",
                "i[class*='Dropdown']",
                "[class*='chevron']",
                "[class*='dropdownToggle']",
                "[class*='dropdown-toggle']",
                "[class*='slicerDropdown'] button",
                "button[aria-haspopup='listbox']",
                "button[aria-haspopup='true']",
                "[class*='slicerHeader'] button",
                "[class*='slicerHeader']"
            ]
            clicked_dropdown = False

            if visual_data["loc"]:
                for sel in expand_btn_selectors:
                    locs = visual_data["loc"].locator(sel)
                    if locs.count() > 0:
                        try:
                            locs.first.click(force=True, timeout=1000)
                            log.debug(f"Slicer '{slicer_title}' dropdown expanded via {sel}")
                            clicked_dropdown = True
                            break
                        except Exception:
                            continue

            if not clicked_dropdown:
                # Fallback: Click the visual container itself to trigger the dropdown
                if visual_data.get("loc"):
                    try:
                        visual_data["loc"].click(force=True, timeout=1000)
                        log.debug(f"Slicer '{slicer_title}' expanded via visual container click")
                        clicked_dropdown = True
                    except Exception as exc:
                        log.debug(f"Fallback visual container click failed: {exc}")
        except Exception as e:
            log.debug(f"Slicer '{slicer_title}' dropdown toggle error: {e}")

        # Step 2d: Wait for popup items to actually appear — use Playwright's native wait.
        # The popup container (.popupContent) may appear instantly, but items load async.
        # We wait specifically for item elements, not just the container shell.
        # NOTE: We use :visible on the container to avoid matching old hidden popups left in the DOM.
        ITEM_SELECTORS_IN_POPUP = [
            ".popupContent:visible [class*='slicerItemContainer']",
            ".popupContent:visible [role='option']",
            ".popupContent:visible [role='listitem']",
            ".slicer-dropdown-menu:visible [class*='slicerItemContainer']",
            ".slicer-dropdown-menu:visible [role='option']",
            "[role='listbox']:visible [role='option']",
        ]
        popup_item_found = False
        combined_sel = ", ".join(ITEM_SELECTORS_IN_POPUP)
        try:
            self.page.wait_for_selector(combined_sel, state="visible", timeout=15_000)
            log.debug(f"Slicer '{slicer_title}' popup items ready.")
            popup_item_found = True
        except Exception:
            pass

        if not popup_item_found:
            log.debug(f"Slicer '{slicer_title}' popup items not detected — proceeding anyway after wait")
            self.page.wait_for_timeout(1000)
        else:
            self.page.wait_for_timeout(200)  # small buffer after items appear

        # Step 2e: Click the item in the dropdown popup.
        clicked = False
        self.page.wait_for_timeout(200)

        heights_after = []
        try:
            heights_after = self.page.evaluate("""
                () => Array.from(document.querySelectorAll('[role="listbox"]'))
                     .map(el => {
                         const style = window.getComputedStyle(el);
                         const r = el.getBoundingClientRect();
                         if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0' || r.width === 0) return 0;
                         return r.height;
                     })
            """)
        except Exception:
            pass

        # Find the ACTIVE (open) popup using three strategies:
        # Strategy A (primary): DELTA — listbox whose height GREW most since before expand.
        # Strategy B (primary): HAS-ITEMS — listboxes that contain actual option/item elements.
        # Strategy C (fallback): POSITION — listbox closest to slicer visual.
        search_roots = []
        active_idx = -1  # Safe default — overwritten inside try block below if delta is found
        try:
            heights_after = self.page.evaluate("""
                () => Array.from(document.querySelectorAll('[role="listbox"]'))
                     .map(el => el.getBoundingClientRect().height)
            """)
            heights_str = ', '.join(f'{i}:{round(h)}' for i, h in enumerate(heights_after))
            log.debug(f"Listbox heights after expand: {heights_str}")

            active_idx = -1
            # Strategy A: delta (which listbox grew most, including newly appended listboxes)
            if heights_after:
                max_delta, best_delta_idx = 0, -1
                for i, ah in enumerate(heights_after):
                    bh = heights_before[i] if heights_before and i < len(heights_before) else 0
                    delta = ah - bh
                    if delta > max_delta:
                        max_delta = delta
                        best_delta_idx = i
                if max_delta > 10:  # Only use delta if meaningful growth detected
                    active_idx = best_delta_idx
                    log.debug(f"Active popup (delta strategy): nth={active_idx}, grew by {round(max_delta)}px")

            # Strategy B: Find listboxes that ACTUALLY contain option items in DOM
            item_lb_indices = self.page.evaluate("""
                () => {
                    const lbs = Array.from(document.querySelectorAll('[role="listbox"]'));
                    const results = [];
                    lbs.forEach((lb, i) => {
                        const items = lb.querySelectorAll('[class*="slicerItemContainer"], [role="option"], [role="listitem"]');
                        if (items.length > 0 && lb.getBoundingClientRect().height > 20) {
                            results.push({i: i, count: items.length});
                        }
                    });
                    results.sort((a, b) => b.count - a.count);
                    return results.map(r => r.i);
                }
            """)
            log.debug(f"Listboxes containing items: {item_lb_indices}")

            if active_idx >= 0:
                search_roots.append(self.page.locator("[role='listbox']").nth(active_idx))

            for item_idx in (item_lb_indices or []):
                loc = self.page.locator("[role='listbox']").nth(item_idx)
                if loc not in search_roots:
                    search_roots.append(loc)

            # Strategy C: position proximity — fallback if active_idx not found yet
            if active_idx < 0 and visual_data.get("loc"):
                try:
                    if visual_data["loc"].count() > 0:
                        slicer_rect = visual_data["loc"].evaluate("""
                            el => { const r = el.getBoundingClientRect();
                                    return {cx: r.left + r.width/2, bottom: r.bottom}; }
                        """, timeout=1000)
                        pos_idx = self.page.evaluate(f"""
                            (args) => {{
                                const sr = args[0];
                                const hb = args[1];
                                const lbs = Array.from(document.querySelectorAll('[role="listbox"]'));
                                let bestIdx = -1, bestScore = Infinity;
                                lbs.forEach((lb, i) => {{
                                    const r = lb.getBoundingClientRect();
                                    if (r.height < 80) return;  // skip small in-page listboxes (e.g. 44px 3-item listbox)
                                    if (hb && hb[i] > 100) return;  // skip static listboxes already large before expand
                                    const cx = r.left + r.width / 2;
                                    const score = Math.abs(cx - sr.cx) + Math.max(0, r.top - sr.bottom);
                                    if (score < bestScore) {{ bestScore = score; bestIdx = i; }}
                                }});
                                return bestIdx;
                            }}
                        """, [slicer_rect, heights_before])
                        if pos_idx >= 0:
                            active_idx = pos_idx
                            log.debug(f"Active popup (position strategy): nth={active_idx}")
                            loc = self.page.locator("[role='listbox']").nth(pos_idx)
                            if loc not in search_roots:
                                search_roots.append(loc)
                except Exception as pe:
                    log.debug(f"Position detection failed: {pe}")

            # Append all visible listboxes with height > 50 as comprehensive fallbacks
            try:
                all_vis_indices = self.page.evaluate("""
                    () => Array.from(document.querySelectorAll('[role="listbox"]'))
                         .map((el, i) => ({i, h: el.getBoundingClientRect().height, vis: window.getComputedStyle(el).visibility !== 'hidden'}))
                         .filter(item => item.h > 50 && item.vis)
                         .map(item => item.i)
                """)
                for v_idx in all_vis_indices:
                    v_loc = self.page.locator("[role='listbox']").nth(v_idx)
                    if v_loc not in search_roots:
                        search_roots.append(v_loc)
            except Exception:
                pass
        except Exception as e:
            log.debug(f"Active popup detection failed: {e}")

        # Search box filtering — ONLY if an input element exists strictly inside the active popup listbox
        search_typed = False
        if active_idx >= 0:
            try:
                has_search = self.page.evaluate(f"""
                    (idx) => {{
                        const lbs = document.querySelectorAll('[role="listbox"]');
                        const lb = lbs[idx];
                        if (!lb) return false;
                        const inp = lb.querySelector('input');
                        return inp !== null && inp.getBoundingClientRect().height > 0;
                    }}
                """, active_idx)
                if has_search:
                    s_el = self.page.locator("[role='listbox']").nth(active_idx).locator("input").first
                    if s_el.is_visible(timeout=400):
                        s_el.click(timeout=500)
                        s_el.fill("")
                        s_el.press_sequentially(value, delay=40)
                        self.page.wait_for_timeout(800)
                        log.debug(f"Slicer '{slicer_title}' typed into internal popup search input -> '{value}'")
                        search_typed = True
            except Exception as se:
                log.debug(f"Popup internal search check failed: {se}")

        if not search_typed:
            log.debug(f"Slicer '{slicer_title}' no popup internal search input used.")

        # Fallback candidates for non-standard popups
        for p in [".popupContent:visible", ".slicer-dropdown-menu:visible"]:
            locs = self.page.locator(p)
            if locs.count() > 0:
                search_roots.append(locs.last)

        # Visual container (for non-dropdown list slicers)
        if visual_data.get("loc"):
            search_roots.append(visual_data["loc"])

        # Final fallback: whole page
        search_roots.append(self.page)

        # Scroll the active popup to TOP so alphabetically-early items (e.g. "California") are
        # rendered.  We dispatch scroll(bubbles:true) to reach PBI's container-level scroll
        # listener (which drives the virtualizer).  We do NOT dispatch WheelEvent here because
        # PBI has a document-level 'wheel' listener that misroutes large negative deltaY events
        # and repopulates the popup with a different slicer's stale content.
        try:
            active_lb_idx = active_idx if active_idx >= 0 else -1
            self.page.evaluate(f"""
                (activeIdx) => {{
                    const lbs = Array.from(document.querySelectorAll('[role="listbox"]'));
                    let target = activeIdx >= 0 ? lbs[activeIdx] : null;
                    if (!target) {{
                        let maxH = 0;
                        lbs.forEach(lb => {{
                            const h = lb.getBoundingClientRect().height;
                            if (h > maxH) {{ maxH = h; target = lb; }}
                        }});
                    }}
                    if (!target) return;

                    // Only scroll to top when the popup has inner scrollable content
                    // (i.e. the list is virtualized and items above might not be in the DOM).
                    // Short lists (e.g. Ship Mode with 4 items) fit entirely in the popup,
                    // have no inner scroller, and must NOT be scrolled — dispatching a scroll
                    // event on the listbox itself can corrupt the popup via bubbling.
                    const scrollers = Array.from(target.querySelectorAll('*'))
                        .filter(c => c.scrollHeight > c.clientHeight + 20);
                    if (scrollers.length === 0) return;  // Short list — skip scroll-to-top

                    const s = scrollers[scrollers.length - 1];
                    s.scrollTop = 0;
                    // Dispatch scroll on the inner scroller only (not the listbox itself).
                    // bubbles:true lets PBI's container listener re-render items at position 0.
                    // We do NOT dispatch WheelEvent here — PBI's document 'wheel' listener
                    // misroutes large deltaY and overwrites the popup with stale slicer content.
                    s.dispatchEvent(new Event('scroll', {{bubbles: true, cancelable: false}}));
                }}
            """, active_lb_idx)
            self.page.wait_for_timeout(1200)  # Wait for virtualized items to render at top
        except Exception:
            pass

        self.page.wait_for_timeout(400)  # Baseline render wait for popup items

        item_candidates = [
            f"[class*='slicerItemContainer']:has-text('{value}')",
            f"[role='option']:has-text('{value}')",
            f"[role='listitem']:has-text('{value}')",
            f"[class*='row']:has-text('{value}')",
            f"text='{value}'",
        ]

        for s_root in search_roots:
            if clicked:
                break
                
            for scroll_attempt in range(30):
                if clicked:
                    break
                
                # Keyboard quick-jump on attempt 1 for long virtualized lists (e.g. 'Los Angeles' -> key 'l')
                if scroll_attempt == 1 and not search_typed and value:
                    try:
                        first_char = value[0].lower()
                        active_lb_idx = active_idx if active_idx >= 0 else -1
                        self.page.evaluate(f"""
                            (activeIdx, char) => {{
                                const lbs = Array.from(document.querySelectorAll('[role="listbox"]'));
                                const target = activeIdx >= 0 ? lbs[activeIdx] : document;
                                if (target && target.dispatchEvent) {{
                                    const scrollers = Array.from(target.querySelectorAll('*'))
                                        .filter(c => c.scrollHeight > c.clientHeight);
                                    const s = scrollers.length > 0 ? scrollers[scrollers.length - 1] : target;
                                    s.focus();
                                    s.dispatchEvent(new KeyboardEvent('keydown', {{key: char, code: 'Key' + char.toUpperCase(), keyCode: char.charCodeAt(0), bubbles: true}}));
                                }}
                            }}
                        """, active_lb_idx, first_char)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

                item_found_in_dom = False
                
                for candidate in item_candidates:
                    try:
                        locs = s_root.locator(candidate)
                        count = locs.count()
                        if count > 0:
                            item_found_in_dom = True
                            
                        for i in range(count):
                            loc = locs.nth(i)
                            if loc.is_visible():
                                class_val = loc.get_attribute("class") or ""
                                if "slicerText" in class_val or "slicer-header" in class_val:
                                    continue
                                
                                if loc.get_attribute("aria-selected") == "true" or "selected" in class_val:
                                    log.debug(f"Slicer item '{value}' is already selected.")
                                    clicked = True
                                    break

                                try:
                                    loc.click(timeout=3_000)
                                    clicked = True
                                    log.debug(f"Slicer item clicked via selector: {candidate} in root {s_root} (match {i}) after {scroll_attempt} scrolls")
                                    # Close popup after click — Escape key + verify popup collapsed.
                                    try:
                                        if active_idx >= 0:
                                            self.page.evaluate(f"""
                                                (idx) => {{
                                                    const lbs = document.querySelectorAll('[role="listbox"]');
                                                    if (lbs[idx]) {{
                                                        lbs[idx].focus();
                                                        lbs[idx].dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true}}));
                                                    }}
                                                }}
                                            """, active_idx)
                                        self.page.keyboard.press("Escape")
                                        self.page.wait_for_timeout(300)
                                        _hb_list = heights_before or []
                                        _a_idx = active_idx  # from outer scope (-1 if unknown)
                                        popup_closed = False
                                        for _poll in range(15):
                                            if _a_idx >= 0 and _hb_list and _a_idx < len(_hb_list):
                                                cur_h = self.page.evaluate(f"""
                                                    () => {{
                                                        const lb = document.querySelectorAll('[role="listbox"]')[{_a_idx}];
                                                        return lb ? Math.round(lb.getBoundingClientRect().height) : 0;
                                                    }}
                                                """)
                                                if cur_h <= _hb_list[_a_idx] + 30:
                                                    popup_closed = True
                                                    break  # Popup collapsed ✓
                                            else:
                                                # Check if any popup listbox > 150px height remains
                                                any_large = self.page.evaluate("""
                                                    () => Array.from(document.querySelectorAll('[role="listbox"]'))
                                                               .some(lb => lb.getBoundingClientRect().height > 150)
                                                """)
                                                if not any_large:
                                                    popup_closed = True
                                                    break
                                            self.page.wait_for_timeout(200)
                                        if not popup_closed:
                                            log.debug(f"Slicer popup (listbox nth={_a_idx}) did not close via Escape — clicking visual container header to force-close")
                                            if visual_data.get("loc"):
                                                try:
                                                    visual_data["loc"].click(position={"x": 10, "y": 10}, timeout=1000)
                                                except Exception:
                                                    self.page.mouse.click(200, 200)
                                            else:
                                                self.page.mouse.click(200, 200)
                                            self.page.wait_for_timeout(400)
                                        else:
                                            log.debug(f"Slicer popup (listbox nth={_a_idx}) closed after Escape (polls={_poll+1})")
                                    except Exception:
                                        pass
                                    break
                                except Exception as e:
                                    log.debug(f"Click attempt failed: {e}")
                                    continue
                        if clicked:
                            break
                    except Exception:
                        continue
                
                if not clicked:
                    if item_found_in_dom:
                        # Item IS in DOM but click failed (element covered or not actionable).
                        # Break scroll loop immediately — don't retry 15 times. Try next search root.
                        break
                    else:
                        # Item NOT in DOM yet — scroll popup down to reveal virtualized items.
                        try:
                            active_lb_idx = active_idx if active_idx >= 0 else -1
                            first_char = value[0] if value else ""
                            self.page.evaluate(f"""
                                (args) => {{
                                    const activeIdx = args[0];
                                    const scrollAttempt = args[1];
                                    const targetChar = args[2] || '';
                                    const lbs = Array.from(document.querySelectorAll('[role="listbox"], .popupContent, .slicer-dropdown-menu, .cdk-overlay-pane'));
                                    let scroller = null;
                                    if (activeIdx >= 0 && lbs[activeIdx]) {{
                                        const target = lbs[activeIdx];
                                        const innerScrollers = Array.from(target.querySelectorAll('*')).filter(c => c.scrollHeight > c.clientHeight || (c.className && typeof c.className === 'string' && (c.className.includes('viewport') || c.className.includes('slicer') || c.className.includes('Content'))));
                                        scroller = innerScrollers.length > 0 ? innerScrollers[0] : target;
                                    }}
                                    if (!scroller || scroller === document.body) {{
                                        const openPopup = lbs.find(el => el.getBoundingClientRect().height > 50 && window.getComputedStyle(el).visibility !== 'hidden');
                                        if (openPopup) {{
                                            const inner = Array.from(openPopup.querySelectorAll('*')).filter(c => c.scrollHeight > c.clientHeight || (c.className && typeof c.className === 'string' && (c.className.includes('viewport') || c.className.includes('slicer'))));
                                            scroller = inner.length > 0 ? inner[0] : openPopup;
                                        }}
                                    }}
                                    if (scroller && scroller !== document.body) {{
                                        if (scrollAttempt === 1 && targetChar) {{
                                            const charCode = targetChar.toLowerCase().charCodeAt(0) - 97;
                                            if (charCode >= 0 && charCode < 26) {{
                                                const ratio = charCode / 26;
                                                const targetPos = Math.floor(scroller.scrollHeight * ratio);
                                                scroller.scrollTop = targetPos;
                                                scroller.dispatchEvent(new WheelEvent('wheel', {{deltaY: targetPos, bubbles: true}}));
                                                scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                                                return;
                                            }}
                                        }}
                                        const step = 600;
                                        scroller.scrollTop += step;
                                        scroller.dispatchEvent(new WheelEvent('wheel', {{deltaY: step, bubbles: true}}));
                                        scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                                    }}
                                }}
                            """, [active_lb_idx, scroll_attempt, first_char])
                            try:
                                self.page.keyboard.press("PageDown")
                            except Exception:
                                pass
                            self.page.wait_for_timeout(400)  # Wait for virtualized render
                        except Exception as e:
                            log.debug(f"Scroll evaluate failed: {e}")

        if not clicked:
            # Diagnostic: dump contents of each search root to help debug
            try:
                all_heights = self.page.evaluate("""
                    () => Array.from(document.querySelectorAll('[role="listbox"]'))
                         .map((el, i) => i + ':h' + Math.round(el.getBoundingClientRect().height)).join(', ')
                """)
                log.debug(f"DIAGNOSTIC listbox heights at failure: {all_heights}")
            except Exception:
                pass
            for idx, s_root in enumerate(search_roots):
                try:
                    items_text = s_root.evaluate(
                        "el => Array.from(el.querySelectorAll(\'.slicerItemContainer, [role=\"option\"], [role=\"listitem\"], .row\')).map(i => i.innerText).join(\', \')"
                    )
                    log.debug(f"DEBUG root[{idx}] items: {items_text}")
                except Exception:
                    try:
                        # s_root is the page object
                        items_text = s_root.locator("[role='listbox']:visible .slicerItemContainer").all_text_contents()
                        log.debug(f"DEBUG root[{idx}] items (page): {items_text[:10]}")
                    except Exception:
                        pass
            # Final fallback: JS click (synthetic) on the item text + Auto-scrolling for virtualization
            safe_value = value.replace("'", "\\'")
            safe_val_lower = value.lower().replace("'", "\\'")
            js_result = self.page.evaluate(f"""
                async () => {{
                    const delay = ms => new Promise(res => setTimeout(res, ms));
                    const findTextNode = () => {{
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                        let node;
                        while ((node = walker.nextNode())) {{
                            const txt = (node.textContent || '').trim().toLowerCase();
                            if (txt && txt.includes('{safe_val_lower}')) {{
                                let parent = node.parentElement;
                                while (parent && parent !== document.body) {{
                                    const r = parent.getBoundingClientRect();
                                    if (r.width > 0 && r.height > 0) return parent;
                                    parent = parent.parentElement;
                                }}
                            }}
                        }}
                        return null;
                    }};
                    
                    let target = findTextNode();
                    if (target) {{
                        target.click();
                        return {{clicked: true, text: target.textContent}};
                    }}
                    
                    // Try scrolling all active popup containers to find virtualized items.
                    const popups = Array.from(document.querySelectorAll('.popupContent, [role="listbox"], .slicer-dropdown-menu'))
                        .filter(el => el.getBoundingClientRect().height > 20);
                    for (const popup of popups) {{
                        const innerScrollers = Array.from(popup.querySelectorAll('*'))
                            .filter(c => c.scrollHeight > c.clientHeight || (c.className && typeof c.className === 'string' && (c.className.includes('viewport') || c.className.includes('slicer') || c.className.includes('Content'))));
                        const scroller = innerScrollers.length > 0 ? innerScrollers[0] : popup;
                        // ── Pass 1: scroll to TOP ──
                        scroller.scrollTop = 0;
                        scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                        await delay(400);
                        target = findTextNode();
                        if (target) {{
                            target.click();
                            return {{clicked: true, text: target.textContent, scrolled: false}};
                        }}
                        // ── Pass 2: scroll DOWN from top ──
                        let prevScrollTop = -1;
                        let attempts = 0;
                        while (scroller.scrollTop !== prevScrollTop && attempts < 35) {{
                            attempts++;
                            prevScrollTop = scroller.scrollTop;
                            scroller.scrollTop += 250;
                            scroller.dispatchEvent(new WheelEvent('wheel', {{deltaY: 250, bubbles: true}}));
                            scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                            await delay(250); // wait for virtualized render
                            target = findTextNode();
                            if (target) {{
                                target.click();
                                return {{clicked: true, text: target.textContent, scrolled: true}};
                            }}
                        }}
                    }}
                    return {{clicked: false, matches: []}};
                }}
            """)
            if js_result.get("clicked"):
                clicked = True
                log.debug(f"Slicer item '{value}' clicked via JS text walker. Match: {js_result.get('text')}. Scrolled: {js_result.get('scrolled', False)}")
            else:
                log.debug(f"JS fallback failed to find clickable element. Partial matches found: {js_result.get('matches')}")
                self.page.screenshot(path="slicer_failure.png")

        if not clicked:
            raise ValueError(
                f"Could not set slicer '{slicer_title}' to '{value}'. "
                f"Slicer DOM info: {slicer_info}. "
                f"Check that the slicer title and item value match the dashboard exactly."
            )

        self._wait_for_dashboard_settle()
        log.info(f"Slicer '{slicer_title}' set to '{value}' (flat-DOM strategy)")


    def clear_all_slicers(self) -> None:
        """
        Clear ALL active slicers on the page to their default 'All' / unselected state.

        Power BI only shows eraser icons on *hover*, so checking is_visible() always
        returns False and the previous approach silently did nothing.

        New approach:
          1. JS scans every visual-container with a [aria-roledescription] slicer
             element and checks if a clear/eraser icon exists in its DOM (regardless
             of hover visibility).  Returns the list of active slicer titles.
          2. For each active slicer, call reset_slicer() which handles both list and
             dropdown slicer styles.
          3. Fallback: also force-click ANY clear/eraser icon found in the whole page
             DOM using Playwright with force=True (bypasses hover requirement).
        """
        log.info("Clearing all active slicers on the page...")

        # ── Step 0: Click Power BI's native 'Reset to default' button in top action bar ────
        try:
            reset_sels = [
                "button[aria-label*='Reset to default' i]",
                "button[title*='Reset to default' i]",
                "button[aria-label*='Reset' i]",
                "[class*='resetToDefault']",
                "[class*='reset-to-default']",
            ]
            for r_sel in reset_sels:
                try:
                    r_loc = self.page.locator(r_sel).first
                    if r_loc.count() > 0 and r_loc.is_visible():
                        r_loc.click(timeout=1000)
                        self.page.wait_for_timeout(600)
                        # Check for confirmation dialog button ('Reset')
                        confirm_btn = self.page.locator("button:has-text('Reset'), button[aria-label='Reset']").first
                        if confirm_btn.count() > 0 and confirm_btn.is_visible():
                            confirm_btn.click(timeout=1000)
                            self.page.wait_for_timeout(1000)
                        log.info(f"Cleared all slicers via native 'Reset to default' button ('{r_sel}')")
                        return
                except Exception:
                    continue
        except Exception as e_r:
            log.debug(f"Native 'Reset to default' button click skipped: {e_r}")

        # ── Step 1: JS DOM scan ─────────────────────────────────────────────────
        try:
            active_titles: list = self.page.evaluate("""
                () => {
                    const titles = [];
                    const allDivs = document.querySelectorAll('[aria-roledescription]');
                    for (const div of allDivs) {
                        const role = (div.getAttribute('aria-roledescription') || '').toLowerCase();
                        if (!role.includes('slicer')) continue;
                        const vc = div.closest('visual-container') || div.parentElement;
                        const root = vc || div;
                        const rawText = (root.innerText || '').trim();
                        const title = rawText.split('\\n')[0].trim();
                        if (!title) continue;
                        // Check for a clear / eraser icon in DOM (present when a value is active)
                        const hasClear = !!(
                            root.querySelector("i[class*='clear']") ||
                            root.querySelector("i[class*='eraser']") ||
                            root.querySelector("button[aria-label*='Clear']") ||
                            root.querySelector("[class*='clearIcon']") ||
                            root.querySelector("[class*='resetButton']") ||
                            root.querySelector("button[title*='Clear']")
                        );
                        if (hasClear && !titles.includes(title)) {
                            titles.push(title);
                        }
                    }
                    return titles;
                }
            """)
        except Exception as js_err:
            log.warning(f"JS DOM scan for active slicers failed: {js_err}")
            active_titles = []

        if active_titles:
            log.info(f"JS detected {len(active_titles)} active slicer(s): {active_titles}")
            for title in active_titles:
                try:
                    self.reset_slicer(title)
                    log.info(f"  ✓ reset_slicer('{title}') completed")
                except Exception as e_reset:
                    log.warning(f"  ✗ reset_slicer('{title}') failed: {e_reset}")
        else:
            log.info("JS DOM scan found no active slicers (no eraser icons in DOM).")

        # ── Step 2: Playwright force-click fallback ──────────────────────────────
        # Power BI eraser icons exist in DOM even when hover-hidden. force=True bypasses
        # the visibility/pointer-events requirement so we can click them directly.
        try:
            eraser_locs = self.page.locator(
                "i[class*='clear'], i[class*='eraser'], "
                "[class*='clearIcon'], [class*='resetButton'], "
                "button[aria-label*='Clear'], button[title*='Clear']"
            )
            total = eraser_locs.count()
            clicked = 0
            for i in range(total):
                try:
                    eraser_locs.nth(i).click(force=True, timeout=500)
                    self.page.wait_for_timeout(300)
                    clicked += 1
                except Exception:
                    pass
            if clicked:
                log.info(f"Force-clicked {clicked} eraser icon(s) as fallback.")
        except Exception as e2:
            log.debug(f"Playwright force-click fallback failed: {e2}")

        self.page.wait_for_timeout(800)
        log.info("clear_all_slicers complete.")

    def reset_slicer(self, slicer_title: str) -> None:
        """
        Reset a slicer back to its default "Select all" / "All" state.

        For dropdown-style slicers (which have no 'Select All' visible without opening),
        this method first opens the dropdown, then looks for a clear/select-all option.
        If none exists (slicer has a fixed default), the reset is skipped gracefully.

        Tries strategies in order:
          1. JS — click Select All or Clear button (works for list slicers).
          2. Open the dropdown then click Clear/Select All (for dropdown slicers).
          3. Classic iframe-based approach via _find_visual_by_title.

        Args:
            slicer_title: Title of the slicer visual to reset.
        """
        log.info(f"Resetting slicer '{slicer_title}' to All")
        safe_title = slicer_title.replace("'", "\\'")

        # Strategy 1: JS — click Select All or Clear on outer page (works for list slicers)
        done = self.page.evaluate(f"""
            () => {{
                const allDivs = document.querySelectorAll('[aria-roledescription]');
                for (const div of allDivs) {{
                    const vc = div.closest('visual-container') || div.parentElement;
                    const text = (vc ? vc.innerText : div.innerText || '').trim();
                    if (text.split('\\n')[0].trim() !== '{safe_title}') continue;
                    const root = vc || div;
                    const selAll = root.querySelector(
                        "[aria-label='Select all'], [title='Select all'], "
                        + "input[aria-label*='select all' i]"
                    );
                    if (selAll) {{
                        const checked = selAll.getAttribute('aria-checked');
                        if (checked !== 'true') {{ selAll.click(); return 'clicked_select_all'; }}
                        return 'already_all';
                    }}
                    const clearBtn = root.querySelector(
                        "button[aria-label='Clear'], button[title='Clear'], "
                        + "[class*='clearIcon'], [class*='eraser'], i[class*='clear']"
                    );
                    if (clearBtn) {{ clearBtn.click(); return 'clicked_clear'; }}
                }}
                return null;
            }}
        """)

        if done and done != 'already_all':
            self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
            log.info(f"Slicer '{slicer_title}' reset via JS ({done})")
            return
        if done == 'already_all':
            log.info(f"Slicer '{slicer_title}' already at All — skipping reset")
            return

        # Strategy 2: For dropdown-style slicers — open the dropdown first,
        # then look for a 'Select All' or clear option inside the popup.
        # These slicers don't show clear/select-all until the dropdown is open.
        try:
            # Find the target slicer container
            slicer_info = self.page.evaluate(f"""
                () => {{
                    const allDivs = document.querySelectorAll('[aria-roledescription]');
                    for (const div of allDivs) {{
                        const vc = div.closest('visual-container') || div.parentElement;
                        const text = (vc ? vc.innerText : div.innerText || '').trim();
                        if (text.split('\\n')[0].trim() !== '{safe_title}') continue;
                        return {{ hasDropdown: !!(vc || div).querySelector(
                            "[aria-haspopup], [class*='dropdown'], [class*='Dropdown'], button[class*='slicer']"
                        )}};
                    }}
                    return null;
                }}
            """)

            if slicer_info and slicer_info.get('hasDropdown'):
                # Click to open dropdown
                vc_loc = self.page.locator(f"visual-container:has-text('{safe_title}')").first
                expand_sels = [
                    "i[class*='dropdown']", "i[class*='Dropdown']",
                    "[class*='chevron']", "[class*='dropdownToggle']",
                    "button[aria-haspopup='listbox']", "button[aria-haspopup='true']",
                ]
                opened = False
                for sel in expand_sels:
                    btn = vc_loc.locator(sel)
                    if btn.count() > 0:
                        try:
                            btn.first.click(force=True, timeout=1000)
                            self.page.wait_for_timeout(800)
                            opened = True
                            break
                        except Exception:
                            continue

                if opened:
                    # Now look for Select All or Clear inside the popup
                    popup_done = self.page.evaluate("""
                        () => {
                            const popups = document.querySelectorAll(".popupContent, .slicer-dropdown-menu, [role='listbox']");
                            for (const popup of popups) {
                                const selAll = popup.querySelector(
                                    "[aria-label='Select all'], [title='Select all'], [class*='selectAll']"
                                );
                                if (selAll) { selAll.click(); return 'popup_select_all'; }
                                const clearBtn = popup.querySelector(
                                    "button[aria-label='Clear'], [class*='clearIcon'], [class*='eraser']"
                                );
                                if (clearBtn) { clearBtn.click(); return 'popup_clear'; }
                            }
                            // If no clear/select-all found, press Escape to close without change
                            return 'no_action';
                        }
                    """)
                    if popup_done and popup_done != 'no_action':
                        self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                        log.info(f"Slicer '{slicer_title}' reset via dropdown popup ({popup_done})")
                        return
                    else:
                        # No clear/select-all in popup. Try deselecting each checked item.
                        try:
                            desel_result = self.page.evaluate("""
                                () => {
                                    const popups = [...document.querySelectorAll(
                                        '.popupContent, .slicer-dropdown-menu, [role="listbox"]'
                                    )].filter(el => el.offsetHeight > 0);
                                    if (!popups.length) return 'no_popup';
                                    const popup = popups[popups.length - 1];
                                    // Click all currently-checked items to deselect them
                                    const checked = popup.querySelectorAll(
                                        '[aria-checked="true"], [aria-selected="true"], [class*="selected"]'
                                    );
                                    let count = 0;
                                    checked.forEach(el => { el.click(); count++; });
                                    return count > 0 ? `deselected_${count}` : 'no_checked';
                                }
                            """)
                            if desel_result and 'deselected' in str(desel_result):
                                self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                                self.page.keyboard.press("Escape")
                                log.info(f"Slicer '{slicer_title}' reset by deselecting items in popup ({desel_result})")
                                return
                        except Exception as de:
                            log.debug(f"Deselect items fallback failed: {de}")
                        # Close popup and log skip
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(300)
                        log.info(f"Slicer '{slicer_title}' has no clear option in dropdown — skipping reset (has fixed default)")
        except Exception as e:
            log.debug(f"Slicer '{slicer_title}' dropdown reset strategy failed: {e}")

        # Strategy 3: iframe-based clear (classic org reports)
        try:
            container = self._find_visual_by_title(slicer_title)
            clear_btn = container.locator(
                "button[aria-label='Clear'], button[title='Clear'], "
                "[class*='clearIcon'], [class*='eraser']"
            ).first
            if clear_btn.is_visible():
                clear_btn.click()
                self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                log.info(f"Slicer '{slicer_title}' reset via iframe Clear button")
                return
        except Exception:
            pass

        log.info(f"Slicer '{slicer_title}' reset attempt complete (no clear action taken)")


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

    # ── Track 1 / Scenario 1 Methods ──────────────────────────────────────────

    def _assert_not_login_page(self) -> None:
        """Handle SSO redirect: pause for user login in headed browser instead of crashing."""
        url = self.page.url.lower()
        if any(host in url for host in _LOGIN_HOSTS):
            try:
                self.capture_screenshot("pbi_sso_required")
            except Exception:
                pass
            log.warning(
                f"[AUTH REQUIRED] Power BI redirected to login page ({self.page.url[:80]}...). "
                "Please complete SSO in the open browser window. Waiting up to 60s..."
            )
            try:
                self.page.wait_for_url(
                    lambda u: not any(host in u.lower() for host in _LOGIN_HOSTS) and "powerbi.com" in u.lower(),
                    timeout=90_000,
                )
                log.info("SSO completed! Report loading...")
                self._wait_for_pbi_load()
            except Exception:
                raise RuntimeError(
                    f"Power BI redirected to login page ({self.page.url[:80]}...) "
                    "and SSO was not completed within 90s."
                )


    def _get_frame(self) -> Optional[FrameLocator]:
        """Detect (once per navigation) whether the report is inside an iframe."""
        if self._frame_checked:
            return self._frame
        try:
            self.page.wait_for_selector(_IFRAME_SEL, timeout=PBI_IFRAME_PROBE_TIMEOUT)
            self._frame = self.page.frame_locator(_IFRAME_SEL).first
            log.info("PBI iframe detected")
        except PwTimeoutError:
            log.info("No PBI iframe - using main page DOM")
            self._frame = None
        self._frame_checked = True
        return self._frame


    def _ctx(self):
        """Return FrameLocator for iframe mode, or page for non-iframe mode."""
        f = self._get_frame()
        return f if f else self.page


    def _wait_for_pbi_load(self) -> None:
        """Wait for PBI to start rendering the report canvas."""
        self.page.wait_for_timeout(3_000)  # initial settle
        try:
            self._ctx().locator(_READY_SEL).first.wait_for(
                state="visible", timeout=PBI_RENDER_TIMEOUT
            )
        except PwTimeoutError:
            log.warning(
                "No report visuals became visible within "
                f"{PBI_RENDER_TIMEOUT // 1000}s - continuing anyway"
            )
            self.capture_screenshot("pbi_load_timeout")
        self.page.wait_for_timeout(2_000)


    def _collect_page_names(self, ctx) -> list[str]:
        """One snapshot pass over the page-tab selectors; first selector that yields names wins."""
        for sel in _PAGE_TAB_SELECTORS:
            try:
                els = ctx.locator(sel).all()
            except Exception:
                continue
            names: list[str] = []
            for el in els:
                try:
                    name = (el.get_attribute("aria-label") or el.inner_text() or "").strip()
                    name = re.sub(r"\s+Selected$", "", name, flags=re.I).strip()
                except Exception:
                    continue
                if name and name.lower() not in _NOT_PAGE_NAMES and name not in names:
                    names.append(name)
            if names:
                log.debug(f"Page tabs matched selector: {sel}")
                return names
        return []


    def _try_open_pages_pane(self, ctx) -> None:
        """If the left 'Pages' pane is collapsed, try to expand it (best effort)."""
        try:
            btn = ctx.get_by_role("button", name=re.compile(r"^(show )?pages$", re.I)).first
            if btn.is_visible(timeout=1_500):
                log.info("Expanding PBI Pages pane")
                btn.click(timeout=3_000)
                self.page.wait_for_timeout(1_000)
        except Exception:
            pass


    def get_page_list(self) -> list[str]:
        """
        Return the names of all pages in the PBI report.

        Polls (up to PBI_PAGELIST_TIMEOUT) because page tabs render after the
        visuals. If nothing is found, dumps DOM diagnostics to logs/ so the
        selector can be corrected from real markup.
        """
        log.info("Getting PBI page list")
        if "powerbi.com" not in self.page.url.lower() and self._report_url:
            log.warning(
                f"PBIDashboardPage.get_page_list() invoked while on non-PBI URL ({self.page.url}). "
                f"Re-navigating to {self._report_url}..."
            )
            self.open(self._report_url)

        ctx = self._ctx()
        deadline = time.monotonic() + PBI_PAGELIST_TIMEOUT / 1000
        tried_pane = False
        names: list[str] = []

        while True:
            names = self._collect_page_names(ctx)
            if names or time.monotonic() >= deadline:
                break
            if not tried_pane:
                self._try_open_pages_pane(ctx)
                tried_pane = True
            self.page.wait_for_timeout(1_500)

        if not names:
            self.dump_diagnostics("pbi_page_list_empty")

        log.info(f"PBI pages found ({len(names)}): {names}")
        return names


    def dump_diagnostics(self, tag: str = "pbi_diag") -> None:
        """Save screenshot + full HTML + a summary of candidate nav elements to help fix selectors."""
        import os
        from datetime import datetime

        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.capture_screenshot(tag)
            html_path = os.path.join(LOG_DIR, f"{tag}_{ts}.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(self.page.content())
            log.warning(f"[DIAG] url={self.page.url[:120]}")
            log.warning(f"[DIAG] title={self.page.title()!r}")
            log.warning(f"[DIAG] iframes on page={self.page.locator('iframe').count()}")
            log.warning(f"[DIAG] full HTML saved: {html_path}")
            summary = self.page.evaluate(
                """() => {
                    const out = [];
                    const q = "[role='tab'],[role='tablist'],[role='listitem'],[role='region'],"
                            + "button[aria-label],[class*='age' i][class*='av' i]";
                    document.querySelectorAll(q).forEach(e => {
                        if (out.length < 60) out.push([
                            e.tagName.toLowerCase(),
                            e.getAttribute('role') || '',
                            (e.getAttribute('aria-label') || e.innerText || '').trim().slice(0, 60),
                            (e.className && e.className.toString ? e.className.toString() : '').slice(0, 70),
                        ].join(' | '));
                    });
                    return out;
                }"""
            )
            for row in summary:
                log.warning(f"[DIAG] {row}")
        except Exception as e:  # diagnostics must never mask the real failure
            log.warning(f"[DIAG] could not collect diagnostics: {e}")


    def get_visual_titles(self) -> list[str]:
        """
        Return titles of all data visuals on the current PBI page.
        """
        log.info("Getting PBI visual titles")
        titles = []
        ignored_titles = {"power bi report", "legend", "navigation", "visuals", "filters", "pages"}

        eval_script = """
            () => {
                const titles = [];
                const ignored = new Set(["power bi report", "legend", "navigation", "visuals", "filters", "pages"]);
                const vcs = document.querySelectorAll("visual-container, [data-automation-type='visualContainer']");
                let idx = 0;
                for (const vc of vcs) {
                    idx++;
                    const isBtnSlicer = vc.querySelector(".buttonSlicerVisual, [class*='buttonSlicer']") !== null;
                    const innerVc = vc.querySelector(".visualContainer");
                    let title = null;
                    if (innerVc && innerVc.getAttribute("aria-label")) {
                        title = innerVc.getAttribute("aria-label").trim();
                    }
                    if (!title) {
                        const lbl = vc.querySelector("p[id*='visualsLabel']");
                        if (lbl) title = lbl.textContent.trim();
                    }
                    if (!title) {
                        const h = vc.querySelector("[class*='visualTitle'], [class*='header-title'], h3");
                        if (h && !h.textContent.toLowerCase().includes("press enter to explore")) {
                            title = h.textContent.trim();
                        }
                    }
                    if (!title) {
                        const roledesc = innerVc ? innerVc.getAttribute("aria-roledescription") : "";
                        if (roledesc && roledesc.toLowerCase().includes("table")) {
                            const ths = Array.from(vc.querySelectorAll("[role='columnheader']")).slice(0, 3).map(e => e.textContent.trim());
                            title = ths.length > 0 ? `Table: ${ths.join(', ')}` : "Table Visual";
                        } else if (roledesc && !isBtnSlicer) {
                            title = `${roledesc} ${idx}`;
                        }
                    }
                    if (title) {
                        if (title.toLowerCase().startsWith("press enter to explore data")) {
                            title = title.substring("press enter to explore data".length).trim();
                        }
                        title = title.trim();
                        if (title && !ignored.has(title.toLowerCase()) && !titles.includes(title)) {
                            titles.push(title);
                        }
                    }
                }
                return titles;
            }
        """
        try:
            titles = self.page.evaluate(eval_script) or []
        except Exception as e:
            log.warning(f"Error evaluating PBI visual titles via JS: {e}")

        if not titles:
            ctx = self._ctx()
            selectors = [
                "[data-automation-type='visualContainer'] .visualContainer[aria-label]",
                "visual-container .visualContainer[aria-label]",
                "p[id*='visualsLabel']",
                "[class*='visualTitle'] span",
                "visual-container h3",
            ]
            for sel in selectors:
                try:
                    for el in ctx.locator(sel).all():
                        t = el.get_attribute("aria-label") or el.inner_text().strip()
                        if t.lower().startswith("press enter to explore data"):
                            t = t[len("press enter to explore data"):].strip()
                        if t and t.lower() not in ignored_titles and t not in titles:
                            titles.append(t)
                except Exception:
                    pass

        log.info(f"PBI visual titles found ({len(titles)}): {titles}")
        return titles


    def capture_page_baseline(self) -> dict[str, str | None]:
        """
        Snapshot displayed visual titles and data states on the current PBI page.

        Returns: {visual_title: displayed_value_or_data_summary}

        Captures real visual containers (KPIs, bar/line/pie charts, tables)
        and extracts data representations (KPI values, chart data labels, bar/slice values, table cells).
        """
        log.info("Capturing PBI page baseline")
        eval_script = """
            () => {
                const results = {};
                const ignored = new Set(["power bi report", "legend", "navigation", "visuals", "filters", "pages"]);
                const vcs = document.querySelectorAll("visual-container, [data-automation-type='visualContainer']");
                let idx = 0;
                for (const vc of vcs) {
                    idx++;
                    const isBtnSlicer = vc.querySelector(".buttonSlicerVisual, [class*='buttonSlicer']") !== null;
                    const innerVc = vc.querySelector(".visualContainer");
                    let title = null;
                    if (innerVc && innerVc.getAttribute("aria-label")) {
                        title = innerVc.getAttribute("aria-label").trim();
                    }
                    if (!title) {
                        const lbl = vc.querySelector("p[id*='visualsLabel']");
                        if (lbl) title = lbl.textContent.trim();
                    }
                    if (!title) {
                        const h = vc.querySelector("[class*='visualTitle'], [class*='header-title'], h3");
                        if (h && !h.textContent.toLowerCase().includes("press enter to explore")) {
                            title = h.textContent.trim();
                        }
                    }
                    if (!title) {
                        const roledesc = innerVc ? innerVc.getAttribute("aria-roledescription") : "";
                        if (roledesc && roledesc.toLowerCase().includes("table")) {
                            const ths = Array.from(vc.querySelectorAll("[role='columnheader']")).slice(0, 3).map(e => e.textContent.trim());
                            title = ths.length > 0 ? `Table: ${ths.join(', ')}` : "Table Visual";
                        } else if (roledesc && !isBtnSlicer) {
                            title = `${roledesc} ${idx}`;
                        } else if (isBtnSlicer) {
                            title = `Button Slicer ${idx}`;
                        } else {
                            title = `Visual_${idx}`;
                        }
                    }

                    if (title.toLowerCase().startsWith("press enter to explore data")) {
                        title = title.substring("press enter to explore data".length).trim();
                    }
                    title = title.trim();
                    if (!title || ignored.has(title.toLowerCase())) continue;

                    // 1. KPI single value
                    let val = null;
                    const kpi = vc.querySelector("[class*='kpiValue'], [class*='visualValue'], [class*='value']");
                    if (kpi && kpi.textContent.trim()) {
                        val = kpi.textContent.trim();
                    }
                    // 2. Chart data labels
                    if (!val) {
                        const labels = Array.from(vc.querySelectorAll("text.label, tspan.label-tspan, .label-container text"))
                            .map(e => e.textContent.trim()).filter(Boolean);
                        if (labels.length > 0) val = labels.slice(0, 8).join(", ");
                    }
                    // 3. Rect/path bars and slices with aria-label
                    if (!val) {
                        const rects = Array.from(vc.querySelectorAll("rect[aria-label], [data-automation-type*='chart-rect'][aria-label], g.slice[aria-label], path.slice[aria-label], path[aria-label]"))
                            .map(e => e.getAttribute("aria-label")).filter(Boolean);
                        if (rects.length > 0) val = rects.slice(0, 8).join(", ");
                    }
                    // 4. Table cells
                    if (!val) {
                        const cells = Array.from(vc.querySelectorAll("[role='gridcell']"))
                            .map(e => e.textContent.trim()).filter(Boolean);
                        if (cells.length > 0) val = cells.slice(0, 10).join(" | ");
                    }
                    // 5. Button slicer selected item
                    if (!val && isBtnSlicer) {
                        const selectedBtn = vc.querySelector(".selected, [aria-selected='true'], [aria-checked='true']");
                        if (selectedBtn) val = selectedBtn.textContent.trim();
                    }
                    // 6. Generic text summary
                    if (!val) {
                        const allTxt = (vc.textContent || "").replace(/Press Enter to explore data/g, "").trim();
                        const words = allTxt.split(/\\s+/).slice(0, 10);
                        val = words.length > 0 ? words.join(" ") : "rendered";
                    }

                    results[title] = val;
                }
                return results;
            }
        """
        results = {}
        try:
            results = self.page.evaluate(eval_script) or {}
        except Exception as e:
            log.warning(f"Error evaluating PBI baseline via JS: {e}")

        # Fallback to region locator if JS returned empty
        if not results:
            ctx = self._ctx()
            try:
                for region in ctx.locator("[role='region'][aria-label]").all():
                    t = region.get_attribute("aria-label", timeout=1_000)
                    if t and t.lower() not in {"power bi report", "legend", "navigation"}:
                        val_el = region.locator("[class*='value'], text").first
                        results[t] = val_el.inner_text(timeout=500).strip() if val_el.count() else "rendered"
            except Exception as e:
                log.debug(f"PBI baseline region fallback error: {e}")

        log.info(f"PBI baseline: {len(results)} visuals captured ({list(results.keys())[:4]}...)")
        return results


    def deselect_all_visuals(self) -> None:
        """
        Deselect any active visual on the canvas so the Filters Pane shows
        page-level / report-level filters instead of 'Filters on this visual'.
        """
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
        except Exception as e:
            log.debug(f"Deselect visuals error (non-fatal): {e}")


    def ensure_filters_pane_open(self) -> bool:
        """
        Ensure the Power BI Filters Pane on the right is expanded.
        If collapsed, clicks the expand button.
        """
        ctx = self._ctx()
        check_script = """
            () => {
                const pane = document.querySelector(".filtersPane, [class*='filtersPane'], [data-automation-type='filterPane'], [aria-label='Filters']");
                if (pane) {
                    const rect = pane.getBoundingClientRect();
                    return rect.width > 50;
                }
                return false;
            }
        """
        try:
            is_open = self.page.evaluate(check_script)
            if is_open:
                return True
        except Exception:
            pass

        expand_selectors = [
            "button[aria-label*='Expand filter' i]",
            "button[aria-label*='Expand Filters' i]",
            "button[aria-label*='Expand' i][aria-label*='filter' i]",
            "button[aria-label*='Expand' i]",
            "[data-automation-id='filters-pane-expand-button']",
            ".filtersPaneCollapsed button",
            "button.expandPaneBtn",
            "button:has(.pbi-glyph-chevronleft)",
            "button:has(.pbi-glyph-chevronright)",
            "[title*='Expand filter' i]",
        ]
        for sel in expand_selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    btn.click(timeout=1_500, force=True)
                    self.page.wait_for_timeout(600)
                    log.info(f"Expanded Filters Pane using selector: {sel}")
                    return True
            except Exception:
                continue
        return False


    def _stem_word(word: str) -> str:
        w = re.sub(r"[^a-z0-9]", "", word.lower().strip())
        if w.startswith("opp"):
            return "opp"
        if w.startswith("rev"):
            return "rev"
        if w.startswith("quant") or w.startswith("qty"):
            return "qty"
        return w


    def select_visual(self, visual_target: Optional[str] = None) -> bool:
        """
        Locate and click a visual on the canvas to activate its 'Filters on this visual'
        section in the Power BI Filters Pane.

        Args:
            visual_target: Title, partial name, or keywords of the visual to select.
                          If None, clicks the primary data visual (chart or table) on the canvas.
        Returns:
            True if a visual was successfully located and clicked, False otherwise.
        """
        log.info(f"Selecting visual on canvas: {visual_target or '<primary data visual>'}")
        ctx = self._ctx()
        try:
            candidate_vis = None
            if visual_target:
                v_clean = visual_target.strip().lower()
                is_target_qty = ("quant" in v_clean) or ("qty" in v_clean)
                is_target_rev = ("rev" in v_clean) or ("usd" in v_clean) or ("rate" in v_clean)

                stop_words = {"by", "the", "for", "in", "of", "and", "usd", "@", "actual", "rate"}
                target_stems = [
                    PBIDashboardPage._stem_word(w)
                    for w in re.split(r"\s+", v_clean)
                    if len(w) > 1 and w not in stop_words
                ]

                selectors = [
                    f"visual-container:has([class*='visualTitle']:has-text('{visual_target}'))",
                    f"visual-container[aria-label*='{visual_target}' i]",
                    f"[class*='visualContainer'][aria-label*='{visual_target}' i]",
                    f"visual-container:has-text('{visual_target}')",
                    f"[class*='visualContainer']:has-text('{visual_target}')",
                ]
                v_words = [w for w in re.split(r"\s+", v_clean) if len(w) > 2 and w not in stop_words]
                if len(v_words) >= 2:
                    phrase = " ".join(v_words[:3])
                    selectors.append(f"visual-container:has-text('{phrase}')")
                    selectors.append(f"[class*='visualContainer']:has-text('{phrase}')")

                for sel in selectors:
                    loc = ctx.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=1_500):
                        if loc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                            continue
                        candidate_vis = loc
                        log.debug(f"Matched target visual with selector: {sel}")
                        break

                # If exact visual target did not match a data chart (e.g. 'Quantity of Overdue Opportunities'
                # when chart has author title 'Revenue of Overdue Opportunities'), search via core phrase
                if not candidate_vis:
                    core_target = re.sub(r"^(quantity|revenue)\s+of\s+", "", visual_target, flags=re.I).strip()
                    if core_target and core_target.lower() != visual_target.lower():
                        core_selectors = [
                            f"visual-container:has([class*='visualTitle']:has-text('{core_target}'))",
                            f"visual-container:has-text('{core_target}')",
                            f"[class*='visualContainer'][aria-label*='{core_target}' i]",
                        ]
                        for sel in core_selectors:
                            loc = ctx.locator(sel).first
                            if loc.count() > 0 and loc.is_visible(timeout=1_500):
                                if loc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                                    continue
                                candidate_vis = loc
                                log.debug(f"Matched target visual via core target '{core_target}' with selector: {sel}")
                                break

                if not candidate_vis:
                    all_vcs = ctx.locator("visual-container, [data-automation-type='visualContainer']").all()
                    best_match_vc = None
                    best_match_score = 0

                    for vc in all_vcs:
                        try:
                            if vc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                                continue
                            txt = (vc.inner_text(timeout=500) or "").lower()
                            aria = (vc.get_attribute("aria-label") or "").lower()
                            combined = f"{txt} {aria}"

                            # Prevent cross-measure matching (Revenue vs Quantity)
                            if is_target_qty and ("revenue" in combined or "rev " in combined or "usd" in combined) and ("quantity" not in combined and "qty" not in combined):
                                continue
                            if is_target_rev and ("quantity" in combined or "qty" in combined) and ("revenue" not in combined and "rev" not in combined and "usd" not in combined):
                                continue

                            cand_stems = [PBIDashboardPage._stem_word(w) for w in re.split(r"\s+", combined)]
                            match_count = sum(1 for st in target_stems if st in cand_stems or st in combined)

                            if match_count > best_match_score:
                                best_match_score = match_count
                                best_match_vc = vc
                        except Exception:
                            continue

                    min_required = max(1, len(target_stems) - 1) if len(target_stems) >= 2 else 1
                    if best_match_vc and best_match_score >= min_required:
                        candidate_vis = best_match_vc
                        log.debug(f"Matched target visual via stemmed tokens {target_stems} (score={best_match_score}/{len(target_stems)})")

            if not candidate_vis:
                data_vis_selectors = [
                    "visual-container:has([role='grid'])",
                    "visual-container:has([class*='tablix'])",
                    "visual-container:has([class*='pivotTable'])",
                    "visual-container:has([aria-roledescription*='table' i])",
                    "visual-container:has([aria-roledescription*='chart' i])",
                    "visual-container:has(svg.cartesian-chart)",
                    "visual-container:has(svg)",
                ]
                for d_sel in data_vis_selectors:
                    loc = ctx.locator(d_sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        if loc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                            continue
                        candidate_vis = loc
                        log.debug(f"Matched fallback data visual with selector: {d_sel}")
                        break

            if not candidate_vis:
                log.warning(f"Could not locate visual '{visual_target}' on canvas to select")
                return False

            candidate_vis.scroll_into_view_if_needed()
            clicked = False

            # 1. Header click: click visual title or header wrapper with force=True
            header = candidate_vis.locator(
                "[class*='visualTitle'], [class*='header-title'], h3, .visualHeader, .visualHeaderWrapper, .header-text"
            ).first
            if header.count() > 0 and header.is_visible(timeout=800):
                try:
                    header.click(timeout=1_500, force=True)
                    clicked = True
                    self.page.wait_for_timeout(400)
                    log.info(f"Visual '{visual_target or '<primary data visual>'}' selected via header click")
                except Exception as ex_h:
                    log.debug(f"Header click failed: {ex_h}")

            # 2. Container margin click fallback: click corner offset without opening context menu
            if not clicked:
                try:
                    candidate_vis.click(position={"x": 25, "y": 25}, timeout=1_500, force=True)
                    clicked = True
                    self.page.wait_for_timeout(400)
                    log.info(f"Visual '{visual_target or '<primary data visual>'}' selected via container margin click")
                except Exception as ex_c:
                    log.debug(f"Container margin click failed: {ex_c}")

            # 4. Dispatch event fallback
            if not clicked:
                try:
                    candidate_vis.dispatch_event("click")
                    clicked = True
                    log.debug("Fallback dispatch_event('click') executed on visual container")
                except Exception as ex_d:
                    log.warning(f"Fallback dispatch_event('click') failed: {ex_d}")

            self.page.wait_for_timeout(500)
            log.info(f"Visual '{visual_target or '<primary data visual>'}' selected on canvas")
            return True

        except Exception as e:
            log.warning(f"Error selecting visual '{visual_target}' on canvas: {e}")
            return False


    def set_metric_toggle(self, metric_name: str) -> bool:
        """
        Click the canvas toggle button corresponding to the requested metric.
        e.g., if measure is 'Revenue USD @ Actual Rate' -> click 'Revenue'.
        if measure is 'Quantity' -> click 'Quantity'.
        """
        if not metric_name:
            return False
        ctx = self._ctx()
        m_lower = metric_name.lower()
        target = "Revenue" if ("rev" in m_lower or "usd" in m_lower or "rate" in m_lower) else ("Quantity" if ("qty" in m_lower or "quant" in m_lower) else None)
        if not target:
            return False

        log.info(f"Setting metric toggle to: '{target}'")

        # 1. First, check modern Power BI Button Slicer (.buttonSlicerVisual)
        cell_selectors = [
            f".buttonSlicerVisual[role='button']:has([title='{target}'])",
            f".buttonSlicerVisual[role='button']:has-text('{target}')",
            f".buttonSlicerVisual:has([title='{target}'])",
            f".buttonSlicerVisual:has-text('{target}')",
            f"[class*='buttonSlicer']:has([title='{target}'])",
            f"[class*='buttonSlicer']:has-text('{target}')",
        ]

        target_cell = None
        for c_sel in cell_selectors:
            loc = ctx.locator(c_sel).first
            try:
                if loc.count() > 0 and loc.is_visible(timeout=1_000):
                    target_cell = loc
                    break
            except Exception:
                continue

        if target_cell and target_cell.count() > 0:
            is_pressed = target_cell.get_attribute("aria-pressed") == "true" or "selected" in (target_cell.get_attribute("class") or "").lower()
            if is_pressed:
                log.info(f"Metric toggle '{target}' is already active (aria-pressed=true)")
                self.page.wait_for_timeout(500)
                return True

            log.info(f"Activating metric toggle '{target}' button slicer")
            try:
                target_cell.scroll_into_view_if_needed(timeout=1_000)
            except Exception:
                pass

            clicked = False
            click_targets = [
                target_cell.locator("path.ui-role-button-fill, path.fill, .tile").first,
                target_cell,
                target_cell.locator(f".ui-role-button-text, [title='{target}']").first,
            ]
            for ct in click_targets:
                try:
                    if ct.count() > 0 and ct.is_visible(timeout=500):
                        ct.click(timeout=2_000, force=True)
                        self.page.wait_for_timeout(400)
                        if target_cell.get_attribute("aria-pressed") == "true":
                            clicked = True
                            break
                except Exception:
                    continue

            if not clicked:
                try:
                    target_cell.dispatch_event("click")
                    self.page.wait_for_timeout(400)
                    if target_cell.get_attribute("aria-pressed") == "true":
                        clicked = True
                except Exception:
                    pass

            for _ in range(10):
                if target_cell.get_attribute("aria-pressed") == "true":
                    clicked = True
                    break
                self.page.wait_for_timeout(200)

            if clicked:
                log.info(f"Metric toggle '{target}' toggled and verified (aria-pressed=true)")
                self.page.wait_for_timeout(1_500)
                return True

        # 2. Check for Bookmark / Shape action buttons (common in migrated Qlik reports)
        try:
            bm_locs = ctx.locator("[aria-label*='Bookmark'], .visual-shape, [class*='shapeVisual']")
            bm_count = bm_locs.count()
            if bm_count > 0:
                txt_locs = ctx.locator(f":text-matches('^{target}$', 'i')")
                if txt_locs.count() > 0:
                    txt_box = txt_locs.first.bounding_box()
                    if txt_box:
                        best_shape = None
                        min_dist = 999999
                        for i in range(bm_count):
                            s_loc = bm_locs.nth(i)
                            s_box = s_loc.bounding_box()
                            if s_box and s_box['width'] > 0 and s_box['height'] > 0:
                                dist = (
                                    abs((s_box['x'] + s_box['width'] / 2) - (txt_box['x'] + txt_box['width'] / 2))
                                    + abs((s_box['y'] + s_box['height'] / 2) - (txt_box['y'] + txt_box['height'] / 2))
                                )
                                if dist < min_dist:
                                    min_dist = dist
                                    best_shape = (s_loc, s_box)
                        if best_shape and min_dist < 200:
                            s_loc, s_box = best_shape
                            click_x = s_box['x'] + 15
                            click_y = s_box['y'] + s_box['height'] / 2
                            log.info(f"Clicking bookmark shape for '{target}' at ({click_x:.1f}, {click_y:.1f})")
                            self.page.mouse.click(click_x, click_y)
                            self.page.wait_for_timeout(2_500)
                            return True
        except Exception as e:
            log.debug(f"Bookmark shape detection failed: {e}")

        # 3. Fallback to generic button selectors (classic bookmarks / shapes / actionButtons)
        fallback_selectors = [
            f".visual-actionButton:has-text('{target}')",
            f".actionButtonWrapper:has-text('{target}')",
            f".shape:has-text('{target}')",
            f":text-matches('^{target}$', 'i')",
            f"[role='button']:has-text('{target}')",
            f"button:has-text('{target}')",
            f"div[title='{target}']",
            f"[role='button'][title='{target}']",
        ]
        for sel in fallback_selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1_000):
                    btn.click(timeout=2_000, force=True)
                    self.page.wait_for_timeout(1_500)
                    log.info(f"Metric toggle '{target}' clicked via fallback selector: {sel}")
                    return True
            except Exception:
                continue

        log.debug(f"Metric toggle '{target}' not found on current page")
        return False


    def extract_visual_value(
        self,
        visual_target: str,
        category_filter: Optional[Union[str, list[str]]] = None,
    ) -> Optional[str]:
        """
        Extract the displayed numeric or total value for a target visual on the current page.

        Supports:
          - KPI cards (kpiValue, visualValue, text)
          - Bar & column charts (data bars, axis ticks, category matching)
          - Pie & donut charts (slices with accessibility aria-labels)
          - Tables & matrix grids
        """
        # Ensure stabilization wait for visual rendering post-toggle/slicer
        self.page.wait_for_timeout(1_000)

        target_clean = visual_target.replace("'", "\\'")
        cats_json = "[]"
        if category_filter:
            if isinstance(category_filter, str):
                c_list = [c.strip().lower() for c in category_filter.split(",") if c.strip()]
            else:
                c_list = [str(c).strip().lower() for c in category_filter if str(c).strip()]
            import json
            cats_json = json.dumps(c_list)

        eval_script = f"""
            () => {{
                const targetLower = '{target_clean}'.toLowerCase().trim();
                const targetWords = targetLower.split(/\\s+/).filter(w => w.length > 2);
                const stem = w => {{
                    let s = (w || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                    if (s.startsWith('opp')) return 'opp';
                    if (s.startsWith('rev')) return 'rev';
                    if (s.startsWith('quant') || s.startsWith('qty')) return 'qty';
                    return s;
                }};

                const stopWords = ['the', 'for', 'in', 'of', 'and', 'usd', 'actual', 'rate', 'by'];
                const targetTokens = targetLower.split(/\\s+/).map(stem).filter(w => w.length > 1 && !stopWords.includes(w));
                const catFilters = {cats_json};

                const isTargetQty = targetLower.includes('quant') || targetLower.includes('qty');
                const isTargetRev = targetLower.includes('rev') || targetLower.includes('usd') || targetLower.includes('rate');

                // 1. Find matching visual container
                const vcs = Array.from(document.querySelectorAll('visual-container, [class*="visualContainer"]'));
                let targetVc = null;

                const hasChartVisual = vc => vc.querySelector('svg.cartesian-chart, [data-automation-type*="chart-rect"], rect[aria-label], path[aria-label]') !== null;
                const hasCategoryMatch = vc => {{
                    if (catFilters.length === 0) return false;
                    const texts = Array.from(vc.querySelectorAll('text')).map(t => (t.textContent || '').trim().toLowerCase());
                    return catFilters.some(cf => texts.some(t => t.includes(cf) || cf.includes(t)));
                }};

                let bestScore = -1;
                for (const vc of vcs) {{
                    if (vc.querySelector('.buttonSlicerVisual, [class*="buttonSlicer"]')) continue;

                    const titleEl = vc.querySelector("[class*='visualTitle'], h3, [class*='header-title']");
                    const tText = (titleEl ? titleEl.textContent : '').toLowerCase().trim();
                    const aria = (vc.getAttribute('aria-label') || '').toLowerCase().trim();
                    const combined = tText + ' ' + aria;

                    const catMatch = hasCategoryMatch(vc);
                    const isChart = hasChartVisual(vc);

                    // If it has category match on the chart, do not disqualify due to measure keywords in static title
                    if (!catMatch) {{
                        if (isTargetQty && (combined.includes('revenue') || combined.includes('rev ') || combined.includes('usd'))) continue;
                        if (isTargetRev && (combined.includes('quantity') || combined.includes('qty'))) continue;
                    }}

                    const candTokens = combined.split(/\\s+/).map(stem);
                    const matchWords = targetTokens.filter(t => candTokens.includes(t) || combined.includes(t));

                    let score = 0;
                    if (tText.includes(targetLower) || aria.includes(targetLower)) score += 100;
                    score += matchWords.length * 25;

                    if (catFilters.length > 0) {{
                        if (catMatch) score += 150;
                        if (isChart) score += 50;
                        if (combined.includes('by market') || combined.includes('by geolevel3') || combined.includes('by region')) score += 80;
                    }}

                    if (score > bestScore && score > 0) {{
                        bestScore = score;
                        targetVc = vc;
                    }}
                }}

                if (!targetVc) {{
                    for (const vc of vcs) {{
                        if (vc.querySelector('.buttonSlicerVisual, [class*="buttonSlicer"]')) continue;
                        const fullText = (vc.textContent || '').toLowerCase();
                        const catMatch = hasCategoryMatch(vc);
                        if (!catMatch) {{
                            if (isTargetQty && (fullText.includes('revenue') || fullText.includes('rev ') || fullText.includes('usd'))) continue;
                            if (isTargetRev && (fullText.includes('quantity') || fullText.includes('qty'))) continue;
                        }}

                        const candTokens = fullText.split(/\\s+/).map(stem);
                        const matchWords = targetTokens.filter(t => candTokens.includes(t) || fullText.includes(t));
                        if (matchWords.length >= Math.max(1, targetTokens.length - 1) && targetTokens.length > 0) {{
                            targetVc = vc;
                            break;
                        }}
                    }}
                }}

                if (!targetVc && !targetLower) {{
                    const selVc = document.querySelector(
                        'visual-container.selected, visual-container.visualContainerFocused, ' +
                        'visual-container:has([class*="selected"]), visual-container:has(.visualContainerSelection)'
                    );
                    if (selVc && !selVc.querySelector('.buttonSlicerVisual, [class*="buttonSlicer"]') && (hasChartVisual(selVc) || selVc.querySelector("[class*='kpiValue'], [class*='visualValue']"))) {{
                        targetVc = selVc;
                    }}
                }}

                if (!targetVc) return null;

                // 2. Case A: KPI card values
                const cardSelectors = [
                    \"[class*='kpiValue']\",
                    \"[class*='visualValue']\",
                    \"[class*='card'] [class*='value']\",
                    \".value.kpi\",
                    \"span[class*='value']\",
                ];
                for (const sel of cardSelectors) {{
                    const el = targetVc.querySelector(sel);
                    if (el && el.textContent.trim()) {{
                        return el.textContent.trim();
                    }}
                }}

                // 3. Case B: Bar / Column Chart with rect[aria-label] and axis tick labels
                const barRects = Array.from(targetVc.querySelectorAll(
                    '[data-automation-type="column-chart-rect"], [data-automation-type="bar-chart-rect"], rect[aria-label]'
                )).filter(r => r.getAttribute('aria-label') !== null);

                const dedup = s => {{
                    if (s.length % 2 === 0 && s.slice(0, s.length / 2) === s.slice(s.length / 2)) {{
                        return s.slice(0, s.length / 2);
                    }}
                    return s;
                }};

                const axisTickEls = Array.from(targetVc.querySelectorAll('text')).map(t => dedup(t.textContent.trim()));
                const nonNumericAxis = [];
                for (const t of axisTickEls) {{
                    if (!t || /^[\\d.,]+[kmgb%]?$/i.test(t) || t.includes('GEO Level') || t.includes('Measure')) continue;
                    if (t.toLowerCase().includes('% of') || t.toLowerCase().includes('total')) continue;
                    if (!nonNumericAxis.includes(t)) nonNumericAxis.push(t);
                }}

                if (barRects.length > 0 && nonNumericAxis.length > 0) {{
                    const nCats = nonNumericAxis.length;
                    const catMap = {{}};
                    barRects.forEach((r, i) => {{
                        const cat = nonNumericAxis[i % nCats];
                        const rawAria = r.getAttribute('aria-label');
                        const val = (rawAria && rawAria !== 'null' && !isNaN(parseFloat(rawAria))) ? parseFloat(rawAria) : 0;
                        if (cat) {{
                            if (!(cat in catMap)) {{
                                catMap[cat] = val;
                            }}
                        }}
                    }});

                    if (catFilters.length > 0) {{
                        let matchedSum = 0;
                        let found = false;
                        const matchedCats = [];
                        for (const [cName, val] of Object.entries(catMap)) {{
                            if (catFilters.some(cf => cName.toLowerCase().includes(cf) || cf.includes(cName.toLowerCase()))) {{
                                matchedSum += val;
                                matchedCats.push(`${{cName}}: ${{val}}`);
                                found = true;
                            }}
                        }}
                        if (found) {{
                            return JSON.stringify({{
                                val: String(matchedSum),
                                catMap: catMap,
                                matchedCats: matchedCats,
                                nCats: nCats,
                                nonNumericAxis: nonNumericAxis
                            }});
                        }}
                    }} else {{
                        const total = Object.values(catMap).reduce((a, b) => a + b, 0);
                        return JSON.stringify({{
                            val: String(total),
                            catMap: catMap,
                            nCats: nCats
                        }});
                    }}
                }}

                // 4. Case C: Donut / Pie Chart slices (path[aria-label] / g[aria-label])
                const sliceEls = Array.from(targetVc.querySelectorAll('path[aria-label], g[aria-label]'));
                const sliceData = [];
                for (const s of sliceEls) {{
                    const aria = (s.getAttribute('aria-label') || '').trim();
                    if (!aria || aria.length < 5) continue;
                    const clean = aria.replace(/\\.$/, '');
                    const parts = clean.split('. ');
                    if (parts.length >= 2) {{
                        const cat = parts[0].trim();
                        const valStr = parts[parts.length - 1];
                        const numMatch = valStr.match(/([\\d,]+(?:\\.\\d+)?)/);
                        if (numMatch) {{
                            const val = parseFloat(numMatch[1].replace(/,/g, ''));
                            sliceData.push({{ category: cat, val: val }});
                        }}
                    }}
                }}

                if (sliceData.length > 0) {{
                    if (catFilters.length > 0) {{
                        let matchedSum = 0;
                        let found = false;
                        for (const sd of sliceData) {{
                            if (catFilters.some(cf => sd.category.toLowerCase().includes(cf) || cf.includes(sd.category.toLowerCase()))) {{
                                matchedSum += sd.val;
                                found = true;
                            }}
                        }}
                        if (found) return String(matchedSum);
                    }} else {{
                        const total = sliceData.reduce((a, b) => a + b.val, 0);
                        return String(total);
                    }}
                }}

                // 5. Case D: Table Visual gridcells
                const gridcells = Array.from(targetVc.querySelectorAll("[role='gridcell'], [class*='pivotTableCellWrap']"));
                if (gridcells.length > 0) {{
                    for (const cell of gridcells) {{
                        const txt = cell.textContent.trim();
                        if (txt && !/^[a-zA-Z]{{2,4}}-/i.test(txt) && /^[\\$€£]?\\s*[\\d,]+(?:\\.\\d+)?\\s*[KkMmBb%]?$/.test(txt)) return txt;
                    }}
                }}

                // 6. Case E: Fallback to any numeric string in targetVc
                const allText = targetVc.textContent || '';
                const numMatches = allText.match(/[\\$€£]?\\s*[\\d,]+(?:\\.\\d+)?\\s*[KkMmBb%]?/g);
                if (numMatches && numMatches.length > 0) {{
                    return numMatches[numMatches.length - 1].trim();
                }}

                return null;
            }}
        """

        try:
            raw_eval = self.page.evaluate(eval_script)
            if raw_eval:
                import json
                if isinstance(raw_eval, str) and raw_eval.strip().startswith("{"):
                    try:
                        data = json.loads(raw_eval)
                        raw = data.get("val")
                        if "catMap" in data:
                            log.info(f"Visual '{visual_target}' category breakdown: {data['catMap']}")
                        if "matchedCats" in data:
                            log.info(f"Visual '{visual_target}' matched categories: {data['matchedCats']}")
                    except Exception:
                        raw = raw_eval
                else:
                    raw = raw_eval
                if raw:
                    log.info(f"Extracted visual value for '{visual_target}': '{raw}'")
                    return str(raw).strip()
        except Exception as e:
            log.warning(f"Error evaluating visual extraction for '{visual_target}': {e}")

        return None


    def _card_matches(
        self,
        card: Any,
        target_name: str,
        value: Optional[str] = None,
        tokens: Optional[list[str]] = None,
    ) -> bool:
        """Robustly match a Power BI Filters Pane filter card against target slicer/field name."""
        try:
            t_el = card.locator("[data-testid='filter-card-title'], .textLabel, .title").first
            t_text = (t_el.inner_text(timeout=300) if t_el.count() > 0 else "")
            if not t_text and t_el.count() > 0:
                t_text = t_el.text_content() or ""
            t_text = t_text.strip().lower()
            aria = (card.get_attribute("aria-label") or "").strip().lower()
            title_attr = (card.get_attribute("title") or "").strip().lower()
            card_text = (card.text_content() or "").strip().lower()
            combined = f"{t_text} {aria} {title_attr} {card_text[:120]}"

            t_clean = target_name.strip().lower().replace("[", " ").replace("]", " ")

            # 1. Level 3 vs Level 1 disambiguation
            if "level 3" in t_clean or "level3" in t_clean:
                if "level 3" in t_text or "level3" in t_text:
                    return True
                if "level 1" in t_text or "level1" in t_text or "level 2" in t_text or "level2" in t_text:
                    return False
                return ("level 3" in combined or "level3" in combined or "geo level3" in combined or "geo level 3" in combined)

            if "level 1" in t_clean or "level1" in t_clean:
                if "level 1" in t_text or "level1" in t_text:
                    return True
                if "level 3" in t_text or "level3" in t_text or "level 2" in t_text or "level2" in t_text:
                    return False
                return ("level 1" in combined or "level1" in combined or "geo level1" in combined or "geo level 1" in combined)

            # 2. Calendar / YearMonth / Date matching
            is_date_field = any(k in t_clean for k in ["date", "calendar", "yearmonth", "year month"]) or bool(re.search(r"\d{4}-\d{2}", str(value or "")))
            if is_date_field:
                if "fiscal" in combined and "calendar" not in combined:
                    return False
                date_keywords = [
                    "calendar yearmonth", "calendar[yearmonth]", "calendar year month",
                    "yearmonth", "year month", "calendaryearmonth",
                    "calendar date", "calendar[date]", "date", "calendar"
                ]
                if any(k in t_text for k in date_keywords):
                    return True
                if any(k in combined for k in date_keywords):
                    return True

            # 3. Check against caller tokens
            if tokens:
                for tok in tokens:
                    tok_lower = tok.lower()
                    if tok_lower in t_text or tok_lower in combined:
                        return True

            return t_clean in combined
        except Exception:
            return False


    def has_slicer(self, slicer_name: str) -> bool:
        """Check if a slicer or filter matching slicer_name exists on the current page."""
        self.ensure_filters_pane_open()
        ctx = self._ctx()
        clean = slicer_name.lower().strip()

        # Common dimension aliases across Qlik and PBI models
        alias_map = {
            "geo level 3": ["geo level 3", "geo level3", "level 3", "level 3 region", "country[geo level3]", "geo", "market", "region", "country"],
            "geo level3": ["geo level 3", "geo level3", "level 3", "level 3 region", "country[geo level3]", "geo", "market", "region", "country"],
            "country[geo level3]": ["country[geo level3]", "geo level 3", "geo level3", "level 3", "geo", "market", "region", "country"],
            "geo": ["geo", "geo level3", "geo level 3", "market", "region", "country"],
            "date": ["date", "calendar", "calendar[date]", "calendar date", "yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "month", "period"],
            "calendar[date]": ["calendar[date]", "calendar date", "date", "calendar", "yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "month", "period"],
            "calendar yearmonth": ["calendar yearmonth", "calendar[yearmonth]", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"],
            "calendar[yearmonth]": ["calendar[yearmonth]", "calendar yearmonth", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"],
            "yearmonth": ["yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "calendar[date]", "date", "calendar"],
        }
        tokens = list(alias_map.get(clean, [clean]))
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            # Do not add generic words that cause false positive containment
            if clean_t in ("geo", "level", "region", "country", "calendar", "period"):
                continue
            if len(clean_t) > 2 and clean_t not in tokens:
                tokens.append(clean_t)

        # 1. Check canvas slicers
        for tok in tokens:
            selectors = [
                f"visual-container:has([aria-roledescription*='slicer' i]:has-text('{tok}'))",
                f"visual-container:has([class*='slicer']:has-text('{tok}'))",
                f"[class*='visualContainer'][aria-roledescription*='slicer' i]:has-text('{tok}')",
                f".slicer-header:has-text('{tok}')",
                f".slicerContainer:has-text('{tok}')",
                f"[role='region'][aria-roledescription*='slicer' i][aria-label*='{tok}' i]",
            ]
            for sel in selectors:
                try:
                    cand = ctx.locator(sel).first
                    if cand.count() > 0 and cand.is_visible(timeout=500):
                        return True
                except Exception:
                    pass

        # 2. Check Filters Pane cards (both before and after selecting data visuals on canvas)
        try:
            card_sel = "[data-automation-type='filterCard'], .filterCard, [class*='filterCard'], .card.categorical"
            cards = self.page.locator(card_sel).all()
            for c in cards:
                if self._card_matches(c, slicer_name, tokens=tokens):
                    return True

            if self.select_visual(None):
                cards = self.page.locator(card_sel).all()
                for c in cards:
                    if self._card_matches(c, slicer_name, tokens=tokens):
                        return True
        except Exception:
            pass

        return False


    def apply_slicer(
        self,
        slicer_name: str,
        value: str,
        raise_on_error: bool = False,
        target_visual: Optional[str] = None,
    ) -> bool:
        """
        Apply a slicer on the current PBI page with robust alias matching and dropdown handling.

        Finds the slicer by visual title, aria-label, or alias tokens, interacts with dropdowns/lists,
        and selects the specified values. Falls back to the collapsible Filters Pane (including
        visual-level filters on the selected canvas visual).
        """
        log.info(f"Applying PBI slicer: {slicer_name} = {value} (target_visual='{target_visual}')")
        ctx = self._ctx()

        clean = slicer_name.lower().strip()
        alias_map = {
            "geo level 3": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "geo level3": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "country[geo level3]": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "geo level 1": [
                "country[geo level1]", "geo level 1", "geo level1", "level 1", "level1", "geo level_1", "level_1"
            ],
            "geo level1": [
                "country[geo level1]", "geo level 1", "geo level1", "level 1", "level1", "geo level_1", "level_1"
            ],
            "calendar yearmonth": [
                "calendar[calendaryearmonth]", "calendar[yearmonth]", "calendar yearmonth",
                "calendar year month", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"
            ],
            "calendar[yearmonth]": [
                "calendar[calendaryearmonth]", "calendar[yearmonth]", "calendar yearmonth",
                "calendar year month", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"
            ],
            "calendar[date]": [
                "calendar[date]", "calendar date", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar year month", "yearmonth", "year month", "date", "calendar"
            ],
            "date": [
                "date", "calendar[date]", "calendar date", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar year month", "yearmonth", "year month", "calendar"
            ],
            "yearmonth": [
                "yearmonth", "year month", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar date", "calendar[date]", "date", "calendar"
            ],
        }
        tokens = list(alias_map.get(clean, [clean]))
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            # If slicer specifies a level, do not add generic 'geo' or 'level' as a standalone token
            if ("level 3" in clean or "level3" in clean or "level 1" in clean or "level1" in clean) and clean_t in ("geo", "level"):
                continue
            if len(clean_t) > 2 and clean_t not in tokens:
                tokens.append(clean_t)

        slicer_region = None
        for tok in tokens:
            candidate = ctx.locator(
                f"[role='region'][aria-label*='{tok}' i], "
                f"visual-container:has([class*='visualTitle']:has-text('{tok}')), "
                f"visual-container:has(h3:has-text('{tok}')), "
                f"visual-container:has([class*='slicer']:has-text('{tok}')), "
                f"visual-container[aria-roledescription*='slicer' i]:has-text('{tok}'), "
                f"visual-container[aria-label*='{tok}' i], "
                f"[class*='slicer']:has-text('{tok}'), "
                f".slicer-header:has-text('{tok}'), "
                f".slicerContainer:has-text('{tok}')"
            ).first
            try:
                if candidate.count() > 0 and candidate.is_visible(timeout=1_000):
                    slicer_region = candidate
                    log.debug(f"Slicer '{slicer_name}' matched candidate token '{tok}'")
                    break
            except Exception:
                continue

        if not slicer_region or not slicer_region.count():
            # Check Filters Pane fallback before giving up
            if self._apply_filter_pane_slicer(slicer_name, value, tokens, target_visual=target_visual):
                return True
            msg = f"Slicer region '{slicer_name}' not found on current page or Filters Pane."
            log.warning(msg)
            if raise_on_error:
                raise RuntimeError(msg)
            return False

        try:
            # 1. Expand dropdown if this is a dropdown-style slicer
            dropdown_triggers = [
                "[class*='slicer-dropdown-menu']",
                "[class*='slicerDropdown']",
                "[class*='slicer-rest-of-header']",
                "[class*='dropdown-chevron']",
                "[class*='dropdownChevron']",
                "i[class*='chevron']",
                "i[class*='dropdown']",
                "[aria-haspopup='true']",
                "button[class*='slicer']",
            ]
            expanded = False
            for trig_sel in dropdown_triggers:
                trig = slicer_region.locator(trig_sel).first
                try:
                    if trig.count() > 0 and trig.is_visible(timeout=1_000):
                        trig.click(timeout=3_000)
                        self.page.wait_for_timeout(500)
                        expanded = True
                        break
                except Exception:
                    pass

            if not expanded:
                # Try clicking the slicer region directly
                try:
                    slicer_region.click(timeout=3_000)
                    self.page.wait_for_timeout(400)
                except Exception:
                    pass

            # 2. Select each target value
            vals = [v.strip() for v in str(value).split(",") if v.strip()]
            any_clicked = False
            for single_val in vals:
                # Check for search input in slicer
                search_box = slicer_region.locator("input[type='text'], input[type='search'], [class*='searchInput']").first
                if search_box.count() > 0:
                    try:
                        search_box.fill(single_val)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

                # Locate value element in popup or container
                val_selectors = [
                    f"[role='option']:has-text('{single_val}')",
                    f"[class*='slicerText']:has-text('{single_val}')",
                    f"[aria-label='{single_val}']",
                    f"[title='{single_val}']",
                    f"span:text-is('{single_val}')",
                    f":text-is('{single_val}')",
                    f"div:has-text('{single_val}')",
                ]
                clicked = False
                for vsel in val_selectors:
                    item_el = ctx.locator(vsel).first
                    try:
                        if item_el.count() > 0 and item_el.is_visible(timeout=1_500):
                            item_el.click(timeout=3_000)
                            self.page.wait_for_timeout(400)
                            clicked = True
                            any_clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    log.warning(f"Could not click slicer item '{single_val}' inside '{slicer_name}'")

            # 3. Close popup if open
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

            if not any_clicked:
                log.info(f"Canvas slicer items not matched for '{slicer_name}'; attempting Filters Pane fallback")
                if self._apply_filter_pane_slicer(slicer_name, value, tokens, target_visual=target_visual):
                    return True

            self.page.wait_for_timeout(QLIK_FILTER_WAIT)
            log.info(f"PBI slicer applied: {slicer_name} = {value}")
            return any_clicked

        except Exception as e:
            self.capture_screenshot("pbi_slicer_fail")
            msg = f"Could not apply PBI slicer '{slicer_name}' = '{value}': {e}"
            log.warning(msg)
            if raise_on_error:
                raise RuntimeError(msg)
            return False


    def _apply_filter_pane_slicer(
        self,
        slicer_name: str,
        value: str,
        tokens: list[str],
        target_visual: Optional[str] = None,
    ) -> bool:
        """
        Apply filter via Power BI's collapsible Filters Pane when not found on canvas.
        First locates and clicks the target visual (or fallback data visual) on the canvas
        to activate its 'Filters on this visual' section in the Filters Pane.
        Then searches the Filters Pane, matches the exact filter card (preventing collisions
        such as GEO Level1 vs GEO Level3), expands it, searches and checks target values,
        and allows the visual to update.
        """
        log.info(f"Looking for '{slicer_name}' in Power BI Filters Pane (target_visual='{target_visual}')")
        ctx = self._ctx()
        try:
            # 1. Expand Filters Pane container if present and collapsed
            self.ensure_filters_pane_open()

            top_search_sel = (
                "input[data-testid='search-bar-input'], "
                "article.outspacePane input[data-testid='search-bar-input'], "
                "article.outspacePane input[aria-label*='Search Filters' i], "
                ".filterPaneModern input[placeholder*='Search' i], "
                "mat-form-field.searchBox input, "
                "search-box-modern input"
            )
            top_search = ctx.locator(top_search_sel).first if ctx.locator(top_search_sel).count() > 0 else self.page.locator(top_search_sel).first

            used_top_search = False

            def _clear_top_search() -> None:
                nonlocal used_top_search
                if top_search.count() > 0 and top_search.is_visible(timeout=500):
                    try:
                        top_search.fill("")
                        self.page.wait_for_timeout(200)
                    except Exception:
                        pass
                used_top_search = False

            def _search_pane(term: str) -> None:
                if top_search.count() > 0 and top_search.is_visible(timeout=500):
                    try:
                        top_search.click(timeout=1_000)
                        top_search.fill("")
                        if term:
                            top_search.fill(term)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

            def _find_card() -> Optional[Any]:
                card_sel = "[data-automation-type='filterCard'], .filterCard, [class*='filterCard'], .card.categorical"
                cards = ctx.locator(card_sel).all() if ctx.locator(card_sel).count() > 0 else self.page.locator(card_sel).all()
                for c in cards:
                    try:
                        if self._card_matches(c, slicer_name, value=value, tokens=tokens):
                            try:
                                c.scroll_into_view_if_needed(timeout=1_000)
                            except Exception:
                                pass
                            t_el = c.locator("[data-testid='filter-card-title'], .textLabel, .title").first
                            matched_title = (t_el.inner_text(timeout=300) if t_el.count() > 0 else "") or (t_el.text_content() if t_el.count() > 0 else "")
                            log.info(f"Matched filter card in Filters Pane: '{matched_title}' for '{slicer_name}'")
                            return c
                    except Exception:
                        continue
                return None

            try:
                # 2. Check directly in Filters Pane first (page-level filters may already be visible)
                filter_card = _find_card()

                # If not found directly, activate target visual (or fallback primary visual) to reveal visual-level filters
                if not filter_card:
                    if target_visual:
                        log.info(f"Activating target visual '{target_visual}' on canvas to reveal visual-level filters")
                        self.select_visual(target_visual)
                    else:
                        log.info("Activating canvas primary data visual to reveal visual-level filters")
                        self.select_visual(None)
                    self.page.wait_for_timeout(1_000)
                    filter_card = _find_card()

                # 4. If not directly visible, search the Filters Pane top search bar
                if not filter_card:
                    s_lower = slicer_name.lower()
                    search_candidates = []
                    if "geo" in s_lower:
                        search_candidates = ["GEO Level3" if "3" in s_lower else "GEO Level1", "GEO"]
                    elif "calendar" in s_lower or "date" in s_lower or "yearmonth" in s_lower or re.search(r"\d{4}-\d{2}", str(value)):
                        search_candidates = ["YearMonth", "Calendar", "Date"]
                    else:
                        search_candidates = [slicer_name]

                    for term in search_candidates:
                        log.info(f"Searching '{term}' in Filters Pane top search bar")
                        _search_pane(term)
                        used_top_search = True
                        filter_card = _find_card()
                        if filter_card:
                            break

                # Clear top search bar if probed initial visual without finding card
                if used_top_search and not filter_card:
                    _clear_top_search()

                # 6. If still not found, probe other canvas data visuals (tables, charts)
                if not filter_card and not target_visual:
                    log.info("Filter card not yet found on current visual; probing other canvas data visuals")
                    data_vis_sel = (
                        "visual-container:has([aria-roledescription*='table' i]), "
                        "visual-container:has([class*='tablix']), "
                        "visual-container:has([role='grid']), "
                        "visual-container:has([aria-roledescription*='chart' i]), "
                        "visual-container:has(svg)"
                    )
                    data_visual_candidates = ctx.locator(data_vis_sel).all() if ctx.locator(data_vis_sel).count() > 0 else self.page.locator(data_vis_sel).all()

                    for cand_vc in data_visual_candidates:
                        try:
                            cand_vc.scroll_into_view_if_needed()
                            header = cand_vc.locator("[class*='visualTitle'], [class*='header-title'], h3, .visualHeader, .visualHeaderWrapper, .header-text").first
                            if header.count() > 0 and header.is_visible(timeout=500):
                                header.click(timeout=1_500, force=True)
                            else:
                                cand_vc.click(position={"x": 25, "y": 25}, timeout=1_500, force=True)
                            self.page.wait_for_timeout(300)

                            s_lower = slicer_name.lower()
                            search_term = (
                                "YearMonth" if ("calendar" in s_lower or "yearmonth" in s_lower)
                                else ("GEO" if "geo" in s_lower else slicer_name)
                            )
                            _search_pane(search_term)
                            used_top_search = True
                            filter_card = _find_card()
                            if filter_card:
                                log.info("Discovered filter card after selecting canvas data visual")
                                break
                            _clear_top_search()
                        except Exception:
                            _clear_top_search()
                            continue

                if not filter_card:
                    _clear_top_search()
                    self.deselect_all_visuals()
                    log.debug(f"No filter card matching '{slicer_name}' found in Filters Pane")
                    return False

                filter_card.scroll_into_view_if_needed()

                # 7. Expand card if collapsed (wait for content to be visible)
                collapse_btn = filter_card.locator(
                    "button.collapse, button[aria-label*='Expand or collapse' i], button[aria-label*='expand' i]"
                ).first
                if collapse_btn.count() > 0:
                    is_expanded = collapse_btn.get_attribute("aria-expanded") == "true"
                    if not is_expanded:
                        collapse_btn.click(timeout=1_500, force=True)
                        log.info(f"Clicked collapse button to expand card '{slicer_name}'")
                        try:
                            filter_card.locator(".filterContent:not([hidden]), [role='checkbox']").first.wait_for(
                                state="visible", timeout=2_000
                            )
                        except Exception:
                            self.page.wait_for_timeout(400)
                else:
                    try:
                        filter_card.locator(".filterContent:not([hidden]), [role='checkbox']").first.wait_for(
                            state="visible", timeout=1_500
                        )
                    except Exception:
                        pass
                self.page.wait_for_timeout(300)

                # 8. Apply target values to checkboxes
                vals = [v.strip() for v in str(value).split(",") if v.strip()]
                any_clicked = False

                cb_selectors_template = [
                    lambda v: f".row:has([title='{v}']) [role='checkbox']",
                    lambda v: f".row:has-text('{v}') [role='checkbox']",
                    lambda v: f"[role='checkbox']:has-text('{v}')",
                    lambda v: f"[role='checkbox'][title='{v}']",
                    lambda v: f".slicerItemContainer:has-text('{v}') [role='checkbox']",
                    lambda v: f".categoricalFilterValues [role='checkbox']:has-text('{v}')",
                    lambda v: f"span.textLabel:has-text('{v}')",
                    lambda v: f"span.slicerText:has-text('{v}')",
                    lambda v: f"[role='option']:has-text('{v}')",
                    lambda v: f":text-is('{v}')",
                ]

                for single_val in vals:
                    clicked = False

                    def _try_click_cb() -> bool:
                        for fn in cb_selectors_template:
                            cb = filter_card.locator(fn(single_val)).first
                            if cb.count() > 0 and cb.is_visible(timeout=500):
                                chk_el = None
                                if cb.get_attribute("role") == "checkbox":
                                    chk_el = cb
                                elif cb.locator("[role='checkbox']").count() > 0:
                                    chk_el = cb.locator("[role='checkbox']").first
                                else:
                                    row_parent = cb.locator("xpath=ancestor::*[contains(@class, 'row') or contains(@class, 'slicerItemContainer') or @role='option'][1]")
                                    if row_parent.count() > 0 and row_parent.locator("[role='checkbox']").count() > 0:
                                        chk_el = row_parent.locator("[role='checkbox']").first

                                aria_checked = chk_el.get_attribute("aria-checked") if (chk_el and chk_el.count() > 0) else cb.get_attribute("aria-checked")
                                if aria_checked != "true":
                                    click_target = chk_el if (chk_el and chk_el.count() > 0 and chk_el.is_visible(timeout=300)) else cb
                                    click_target.click(timeout=2_000, force=True)
                                    self.page.wait_for_timeout(400)
                                    log.info(f"Selected checkbox '{single_val}' in Filters Pane")
                                else:
                                    log.info(f"Checkbox '{single_val}' already selected")
                                return True
                        return False

                    # 1. Check if directly visible without scrolling
                    if _try_click_cb():
                        clicked = True
                        any_clicked = True

                    # 2. If not visible directly, incrementally scroll the card container down
                    if not clicked:
                        scroll_target = filter_card.locator(".scrollable-content, .slicer-content-wrapper, .visibleGroup, .scrollWrapper, div[style*='overflow']").first
                        for _scroll_i in range(8):
                            try:
                                if scroll_target.count() > 0:
                                    scroll_target.evaluate("el => el.scrollTop += 150")
                                else:
                                    filter_card.evaluate("el => { const sc = el.querySelector('[class*=\"scroll\"], [class*=\"visibleGroup\"]') || el; sc.scrollTop += 150; }")
                                self.page.wait_for_timeout(250)
                            except Exception:
                                pass
                            if _try_click_cb():
                                clicked = True
                                any_clicked = True
                                break

                    # 3. If still not found, try search inside the filter card (with hover to reveal search button)
                    if not clicked:
                        try:
                            filter_card.hover()
                            self.page.wait_for_timeout(200)
                        except Exception:
                            pass

                        search_icon = filter_card.locator("button[aria-label*='Search' i], .searchBtn, i.pbi-glyph-search, [title*='Search' i]").first
                        if search_icon.count() > 0 and search_icon.is_visible(timeout=300):
                            try:
                                search_icon.click(timeout=1_000, force=True)
                                self.page.wait_for_timeout(300)
                            except Exception:
                                pass

                        search_input = filter_card.locator(
                            "input.searchInput, input[data-testid='filter-search-input'], input[type='search'], input[type='text'], input[placeholder*='Search' i]"
                        ).first
                        if search_input.count() > 0 and search_input.is_visible(timeout=600):
                            try:
                                search_input.click(timeout=1_000)
                                search_input.fill("")
                                search_input.fill(single_val)
                                search_input.press("Enter")
                                self.page.wait_for_timeout(500)
                                if _try_click_cb():
                                    clicked = True
                                    any_clicked = True
                                search_input.fill("")
                                search_input.press("Enter")
                                self.page.wait_for_timeout(200)
                            except Exception:
                                pass

                    # 4. Scroll container back to top for subsequent values
                    try:
                        filter_card.evaluate("el => { const sc = el.querySelector('[class*=\"scroll\"], [class*=\"visibleGroup\"]') || el; sc.scrollTop = 0; }")
                    except Exception:
                        pass

                    if not clicked:
                        log.warning(f"Could not locate checkbox for '{single_val}' in Filters Pane")

                # 9. Clean up top search bar unconditionally
                _clear_top_search()

                if any_clicked:
                    self.page.wait_for_timeout(QLIK_FILTER_WAIT)
                    log.info(f"Filters Pane filter applied: {slicer_name} = {value}")
                    self.deselect_all_visuals()
                    return True
                else:
                    log.warning(f"No values could be selected for '{slicer_name}' in Filters Pane")
                    self.deselect_all_visuals()
                    return False

            finally:
                _clear_top_search()
                self.deselect_all_visuals()

        except Exception as e:
            log.warning(f"Error applying filter via Filters Pane: {e}")
            self.deselect_all_visuals()
            return False

