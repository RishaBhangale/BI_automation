"""
pbi_locators.py — CSS / XPath selectors for Power BI published reports.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOM STRUCTURE FINDINGS (confirmed via inspect_pbi_dom.py, 2026-06-26)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Power BI has TWO different embed modes that produce different DOM structures:

MODE A — "Publish to Web"  (URL pattern: app.powerbi.com/view?r=...)
  • Report renders DIRECTLY in the outer page — NO <iframe>
  • Root custom element: <report-embed>
  • Visual containers: <visual-container> (custom element, 22 found)
  • Also selectable via: [data-testid='visual-container']
  • Title bar class: .stylableVisualContainerHeader
  • Containers without titles have class: noVisualTitle
  • Page tabs: in <pbi-status-bar>
  • No authentication required (public reports)

MODE B — "Secure Embed / Org Report"  (URL pattern: app.powerbi.com/groups/.../reports/...)
  • Report renders inside an <iframe title='Report section'>
  • Requires Microsoft SSO (Azure AD) login
  • All visual elements are inside the iframe — must use frame_locator()
  • NOTE: This mode is for client org dashboards (future use)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  When Microsoft updates the Power BI web UI, selectors may break.
    To fix: Open dashboard in Chrome → right-click element → Inspect
    → find stable attribute (aria-label, role, data-testid, class) → update.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


class PageNotFoundError(Exception):
    """Raised when switch_to_page() cannot find the requested page."""


class VisualNotFoundError(Exception):
    """Raised when a visual_title is not found on the current page."""


class DashboardLoadError(Exception):
    """Raised when the dashboard URL fails to load or shows an error banner."""


class PBILocators:
    """
    Selectors for Power BI published reports on app.powerbi.com.

    Contains two selector sets:
      • PTW_* selectors  → "Publish to Web" reports (view?r=...), outer page, no iframe
      • ORG_* selectors  → Org/secure reports, must be used inside an iframe context

    Usage in PBIDashboardPage:
        If embed_mode == "publish_to_web": use self.page.locator(PBILocators.PTW_*)
        If embed_mode == "org_report":     use self._get_frame().locator(PBILocators.ORG_*)
    """

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODE A: "Publish to Web" selectors (outer page — no iframe needed)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Root container of the entire embedded report
    PTW_REPORT_ROOT        = "report-embed"

    # ── Visual containers ──────────────────────────────────────────────────
    # Each chart, card, table, slicer lives in a <visual-container> element.
    PTW_VISUAL_CONTAINER   = "visual-container"
    # Same, using data-testid (more stable — less likely to change on UI updates)
    PTW_VISUAL_CONTAINER_TESTID = "[data-testid='visual-container']"

    # ── CONFIRMED STABLE: The inner div inside each visual-container ────────
    # This is the most stable selector — Microsoft uses data-automation-type
    # as a testing hook that rarely changes across PBI version updates.
    # (confirmed from live DOM inspection 2026-06-26)
    PTW_VISUAL_INNER_DIV   = "[data-automation-type='visualContainer']"

    # The aria-roledescription attribute on PTW_VISUAL_INNER_DIV tells us the
    # visual type. Confirmed values (Sales Performance Report, 2026-06-26):
    #   'Card'                   → KPI Card (single value)
    #   'Multi-row card'         → Multi-row KPI Card
    #   'Clustered column chart' → Bar/column chart
    #   'Line chart'             → Line chart
    #   'Slicer'                 → Filter slicer
    #   'Text box'               → Static label/heading (skip for tests)
    #   'img'                    → Static image (skip for tests)
    #   'button'                 → Button (skip for tests)
    PTW_VISUAL_TYPE_ATTR   = "aria-roledescription"

    # Types to SKIP entirely — not data visuals, not testable.
    # Slicer is skipped because it's a filter control, not a KPI or chart.
    PTW_SKIP_TYPES         = {'Text box', 'img', 'button', 'Slicer'}

    # Types that contain TESTABLE DATA — used for KPI and chart validation.
    # get_all_visual_titles() returns only visuals whose aria-roledescription
    # is in this set. discover_all_visuals() returns everything not in PTW_SKIP_TYPES.
    PTW_TESTABLE_TYPES     = {
        # KPI cards
        'Card', 'Multi-row card', 'KPI',
        # Column / bar charts
        'Clustered bar chart', 'Clustered column chart',
        'Stacked bar chart',   'Stacked column chart',
        '100% stacked bar chart', '100% stacked column chart',
        # Line / area
        'Line chart', 'Area chart', 'Stacked area chart',
        'Line and stacked column chart', 'Line and clustered column chart',
        'Ribbon chart',
        # Other charts
        'Waterfall chart', 'Funnel', 'Scatter chart',
        'Pie chart', 'Donut chart', 'Treemap',
        'Map', 'Filled map', 'Shape map', 'Azure map',
        # Tabular
        'Table', 'Matrix',
        # Gauge / bullet — validated as table data (not KPI card)
        'Gauge', 'Card (new)',
    }

    # Containers WITH visible titles (excludes untitled visuals)
    PTW_VISUAL_WITH_TITLE  = "visual-container:not([class*='noVisualTitle'])"

    # The title bar / header area of each visual
    PTW_VISUAL_TITLE_BAR   = ".stylableVisualContainerHeader"

    # ── Visual title text (CANDIDATE selectors — to be confirmed by find_pbi_titles.py)
    # Try these in order until one works:
    PTW_TITLE_CANDIDATES   = [
        ".stylableVisualContainerHeader span",
        ".stylableVisualContainerHeader div",
        "[class*='visualTitle'] span",
        "[class*='visualTitle']",
        "[class*='titleText']",
        "[class*='header'] span",
        ".visualHeaderAbove span",
        ".visualHeaderAbove",
    ]

    # ── KPI Card values ─────────────────────────────────────────────────────
    # Candidate selectors for the large value shown on a Card visual.
    # To be confirmed after find_pbi_titles.py runs.
    PTW_CARD_VALUE_CANDIDATES = [
        "visual-modern [class*='value']",
        "visual-modern [class*='callout']",
        "[class*='cardCallout']",
        "visual-modern",
        "[class*='kpi']",
    ]

    # ── Multi-row card sub-item selectors ───────────────────────────────────
    # Power BI packs multiple KPI sub-values inside a single Multi-row card
    # visual container. These selectors are candidate patterns discovered via
    # dynamic JS DOM crawl — NOT hardcoded for any specific dashboard.
    # The extraction logic in pbi_dashboard_page.py tries them in order and
    # uses the one that yields the most sub-items.
    PTW_MULTIROW_CARD_ITEM    = (
        "[class*='cardItemContainer'], "
        "[class*='cardItem'], "
        "[class*='row'] [class*='card']"
    )
    PTW_MULTIROW_CARD_LABEL   = (
        "[class*='caption'], "
        "[class*='label'], "
        "[class*='category'], "
        "[class*='title']"
    )
    PTW_MULTIROW_CARD_VALUE   = (
        "[class*='value'], "
        "[class*='callout'], "
        "[class*='data']"
    )

    # ── Loading state ───────────────────────────────────────────────────────
    # The explore-canvas is the report rendering area.
    # Wait for it to appear before interacting.
    PTW_EXPLORE_CANVAS     = "explore-canvas"
    PTW_LOADING_SPINNER    = "[class*='loadingSpinner'], [class*='loadingGif']"

    # ── Page navigation — TAB-BASED (most org reports & some PTW) ────────────
    # Tabs live inside <pbi-status-bar> (custom element at bottom of page)
    PTW_STATUS_BAR         = "pbi-status-bar"
    PTW_PAGE_TAB           = "[role='tab']"
    PTW_PAGE_TAB_ACTIVE    = "[role='tab'][aria-selected='true']"
    # Format with page_name:
    PTW_PAGE_TAB_BY_NAME   = "[role='tab'][title='{page_name}']"

    # ── Page navigation — ARROW-BASED (many "Publish to Web" reports) ────────
    # Some PTW reports use Previous/Next arrows instead of tabs.
    # The page indicator shows text like "1of19" or "Page 1 of 3".
    # Confirmed present in: Sales Analytics dashboard (19 pages)
    PTW_NAV_CONTAINER      = "logo-bar-navigation"
    PTW_NAV_NEXT_PAGE      = "logo-bar-navigation button[aria-label='Next page']"
    PTW_NAV_PREV_PAGE      = "logo-bar-navigation button[aria-label='Previous page']"
    # Fallback aria-labels (PBI localisation may vary):
    PTW_NAV_NEXT_FALLBACK  = "logo-bar-navigation button:has([class*='next']), logo-bar-navigation button:last-of-type"
    PTW_NAV_PREV_FALLBACK  = "logo-bar-navigation button:has([class*='prev']), logo-bar-navigation button:first-of-type"
    # The page counter element — innerText is e.g. "1of19" or "1 of 19"
    PTW_NAV_PAGE_INFO      = "logo-bar-navigation [class*='pageInfo'], logo-bar-navigation span"
    # ── Context menu (right-click on visual) ────────────────────────────────
    PTW_CONTEXT_MENU       = "div[class*='contextMenu'], ul[class*='contextMenu']"
    PTW_SHOW_AS_TABLE      = "button:has-text('Show as a table'), li:has-text('Show data')"
    PTW_BACK_TO_REPORT     = "button:has-text('Back to report')"
    # The '...' More Options button that appears when hovering a visual in PTW mode.
    # This is the correct way to open the context menu on PTW embeds
    # (right-click does NOT trigger the PBI context menu on publish-to-web).
    PTW_MORE_OPTIONS       = (
        "button[aria-label='More options'], "
        "button[title='More options'], "
        "[class*='visualHeaderItemsContainer'] button:last-of-type, "
        "[class*='moreOptions'], "
        "[aria-label*='more' i]"
    )

    # ── Data table (after "Show as a table") ────────────────────────────────
    PTW_DATA_TABLE         = "[class*='dataViewTable'], [class*='pivotTable']"
    PTW_DATA_TABLE_HEADER  = "[class*='dataViewTable'] th, [class*='columnHeaderCell']"
    PTW_DATA_TABLE_ROW     = "[class*='dataViewTable'] tr"
    PTW_DATA_TABLE_CELL    = "td"

    # ── Slicers ─────────────────────────────────────────────────────────────
    PTW_SLICER_SEARCH      = "visual-container [class*='slicer'] input"
    PTW_SLICER_ITEM        = "[class*='slicerItemContainer']"
    # Items that are currently selected (checked) in a slicer
    PTW_SLICER_SELECTED_ITEM = "[class*='slicerItemContainer'][class*='selected'], [class*='slicerItemContainer'] input:checked"

    # ── Error / access banners ──────────────────────────────────────────────
    # Shown when: report is inaccessible, permissions denied, or URL is broken.
    PTW_ERROR_BANNER = (
        "[class*='errorContainer'], "
        "[class*='errorMessage'], "
        "[class*='pbi-error'], "
        "[aria-label*='error' i], "
        "[data-automation-id*='error']"
    )
    PTW_PERMISSION_DENIED_TEXT = (
        "text=You do not have permission, "
        "text=This content is not available, "
        "text=Access denied, "
        "text=Something went wrong"
    )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODE B: Org / Secure Report selectors (INSIDE iframe only)
    # Use these only after entering the iframe with page.frame_locator(ORG_IFRAME)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ORG_IFRAME             = "iframe[title='Report section']"
    ORG_IFRAME_FALLBACK    = "iframe[src*='reportEmbed']"

    ORG_LOADING_SPINNER    = "div.loadingSpinner, div[class*='loadingGif']"

    ORG_PAGE_TAB_BAR       = "ul[role='tablist']"
    ORG_PAGE_TAB           = "li[role='tab']"
    ORG_PAGE_TAB_BY_NAME   = "li[role='tab'][title='{page_name}']"

    ORG_VISUAL_CONTAINER   = "div[class*='visualContainer']"
    ORG_VISUAL_TITLE_BAR   = "div[class*='visualTitle']"
    ORG_VISUAL_TITLE_TEXT  = "div[class*='visualTitle'] span, div[class*='titleText']"

    ORG_CARD_VALUE         = (
        "div[class*='card'] div[class*='value'], "
        "visual-container[type='card'] .label"
    )

    ORG_CONTEXT_MENU       = "div[class*='contextMenu'], ul[class*='contextMenu']"
    ORG_SHOW_AS_TABLE      = "button:has-text('Show as a table'), li:has-text('Show data')"
    ORG_BACK_TO_REPORT     = "button:has-text('Back to report')"

    ORG_DATA_TABLE         = "div[class*='dataViewTable'], div[class*='pivotTable']"
    ORG_DATA_TABLE_HEADER  = "div[class*='dataViewTable'] th, div[class*='columnHeaderCell']"
    ORG_DATA_TABLE_ROW     = "div[class*='dataViewTable'] tr[class*='rowGroup']"
    ORG_DATA_TABLE_CELL    = "div[class*='dataViewTable'] td"

    ORG_SLICER_SEARCH      = "div[class*='slicer'] input[type='text']"
    ORG_SLICER_ITEM        = "div[class*='slicerItemContainer']"

    # ── Legacy aliases (kept for backward compatibility) ─────────────────────
    # These map to ORG_ selectors — used by old code written before embed mode distinction.
    REPORT_IFRAME_SELECTOR = ORG_IFRAME
    REPORT_IFRAME_FALLBACK = ORG_IFRAME_FALLBACK
    LOADING_SPINNER        = ORG_LOADING_SPINNER
    PAGE_TAB_BAR           = ORG_PAGE_TAB_BAR
    PAGE_TAB               = ORG_PAGE_TAB
    PAGE_TAB_BY_NAME       = ORG_PAGE_TAB_BY_NAME
    VISUAL_CONTAINER       = ORG_VISUAL_CONTAINER
    VISUAL_TITLE_BAR       = ORG_VISUAL_TITLE_BAR
    VISUAL_TITLE_TEXT      = ORG_VISUAL_TITLE_TEXT
    CARD_CALLOUT_VALUE     = ORG_CARD_VALUE
    CONTEXT_MENU           = ORG_CONTEXT_MENU
    SHOW_AS_TABLE_OPTION   = ORG_SHOW_AS_TABLE
    BACK_TO_REPORT_BTN     = ORG_BACK_TO_REPORT
    DATA_TABLE_CONTAINER   = ORG_DATA_TABLE
    DATA_TABLE_HEADER      = ORG_DATA_TABLE_HEADER
    DATA_TABLE_ROW         = ORG_DATA_TABLE_ROW
    DATA_TABLE_CELL        = ORG_DATA_TABLE_CELL
    SLICER_SEARCH_INPUT    = ORG_SLICER_SEARCH
    SLICER_LIST_ITEM       = ORG_SLICER_ITEM
    VISUAL_MORE_OPTIONS    = "button[aria-label='More options']"
    EXPORT_DATA_OPTION     = "button:has-text('Export data'), li:has-text('Export data')"
    FILTERS_PANE_TOGGLE    = "button[aria-label='Filters']"
