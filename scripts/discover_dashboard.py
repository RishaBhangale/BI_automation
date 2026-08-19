#!/usr/bin/env python3
"""
discover_dashboard.py — Auto-discovery CLI for the BI validation framework.

Crawls a Power BI dashboard URL, detects every KPI card, chart, and slicer
on every page, and generates a ready-to-use YAML config file.

Optionally connects to the source database to suggest SQL queries for each
visual, so the user's only job is to review, tweak, and approve.

Usage
-----
  # Phase A only — visual discovery (no DB required)
  python scripts/discover_dashboard.py \\
      "https://app.powerbi.com/view?r=..." \\
      --name "Sales Dashboard" \\
      --output dashboard_configs/sales_dashboard.yaml

  # Phase A + B — visual discovery + SQL suggestion
  python scripts/discover_dashboard.py \\
      "https://app.powerbi.com/view?r=..." \\
      --name "Sales Dashboard" \\
      --output dashboard_configs/sales_dashboard.yaml \\
      --db-uri "postgresql://user:pass@host:5432/mydb"

  # Phase A + B using credentials from .env / settings.py
  python scripts/discover_dashboard.py \\
      "https://app.powerbi.com/view?r=..." \\
      --name "Sales Dashboard" \\
      --output dashboard_configs/sales_dashboard.yaml \\
      --db-env

Output
------
  Generates a YAML file at the specified --output path.
  Prints a summary table to the terminal when complete.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from playwright_stealth import Stealth
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure project root is on the path when running as a script
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright.sync_api import sync_playwright

from pageobjects.pbi_dashboard_page import PBIDashboardPage
from locators.pbi_locators import PBILocators
from utils.logger import get_logger

log = get_logger("discover_dashboard")

# Visual types that we treat as KPI cards
KPI_TYPES: frozenset[str] = frozenset({
    "Card", "Multi-row card", "KPI", "Card (new)", "Gauge",
})

# Visual types that we treat as charts/tables (data extraction possible)
CHART_TYPES: frozenset[str] = PBILocators.PTW_TESTABLE_TYPES - KPI_TYPES

# ── Chart extraction capability ────────────────────────────────────────────────
# Chart types where PBI renders aria-label on every SVG data point.
# _extract_chart_data_ptw_aria() works reliably on these in headless mode.
ARIA_EXTRACTABLE_TYPES: frozenset[str] = frozenset({
    "Clustered bar chart",
    "Clustered column chart",
    "Stacked bar chart",
    "Stacked column chart",
    "100% stacked bar chart",
    "100% stacked column chart",
    "Pie chart",
    "Donut chart",
    "Treemap",
    "Funnel",
    "Waterfall chart",
    "Ribbon chart",
    # Line/area charts work when data point markers are enabled by the author
    "Line chart",
    "Area chart",
    "Line and stacked column chart",
    "Line and clustered column chart",
    # Scatter renders a circle per data point
    "Scatter chart",
})

# Chart types that need "Show as a table" (requires UI hover/click on inner div)
# or direct SQL comparison instead of aria scraping.
SHOW_AS_TABLE_TYPES: frozenset[str] = frozenset({
    "Table",
    "Matrix",
    "Map",
    "Filled map",
    "Azure map",
    "Shape map",
    "Decomposition tree",
    "Key influencers",
    "Smart narrative",
    "Q&A visual",
    "Paginated report visual",
    "Python visual",
    "R visual",
})


def _chart_extraction_method(vtype: str) -> str:
    """
    Return the extraction method label for a given chart type.

    Returns:
        "aria"          — Data extractable via aria-label scraping (headless-safe).
        "show_as_table" — Requires 'Show as a table' UI flow or SQL comparison.
        "unknown"       — Type not classified; try aria first, then fall back.
    """
    if vtype in ARIA_EXTRACTABLE_TYPES:
        return "aria"
    if vtype in SHOW_AS_TABLE_TYPES:
        return "show_as_table"
    return "unknown"



# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto-discover a Power BI dashboard and generate a YAML test config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python scripts/discover_dashboard.py \\
                  "https://app.powerbi.com/view?r=..." \\
                  --name "Sales Dashboard" \\
                  --output dashboard_configs/sales_dashboard.yaml

              python scripts/discover_dashboard.py \\
                  "https://app.powerbi.com/view?r=..." \\
                  --name "Sales Dashboard" \\
                  --db-uri "postgresql://user:pass@host/db"
        """),
    )
    p.add_argument("url",            help="Power BI dashboard URL")
    p.add_argument("--name",         default="",   help="Human-readable dashboard name (used in YAML)")
    p.add_argument("--output", "-o", default="",   help="Output YAML path (default: auto-named in dashboard_configs/)")
    p.add_argument("--headless",     action="store_true", default=False,
                   help="Run browser in headless mode (default: False)")
    p.add_argument("--no-headless",  action="store_false", dest="headless",
                   help="Run browser with a visible window (default)")
    p.add_argument("--timeout",      type=int, default=30,
                   help="Max seconds to wait for dashboard to load (default: 30)")
    p.add_argument("--skip-headers", action="store_true",
                   help="Skip chart header extraction (faster)")
    return p.parse_args(argv)


# ── Phase A: Visual Discovery ──────────────────────────────────────────────────

def _associate_textbox_titles(raw_visuals: list[dict]) -> dict[int, str]:
    """
    Associate Text box visuals with nearby charts using spatial proximity.

    For each chart with a noisy title (Y-axis value), find the Text box that
    is directly above it within a 60px vertical gap. Its text becomes the
    chart's descriptive title in the YAML comment.

    Returns:
        Dict mapping visual list index → descriptive title string.
    """
    text_boxes  = [(i, v) for i, v in enumerate(raw_visuals) if v.get("type") == "Text box"]
    chart_idxs  = [
        (i, v) for i, v in enumerate(raw_visuals)
        if v.get("type") in CHART_TYPES and v.get("is_noisy_title", False)
    ]

    associations: dict[int, str] = {}
    GAP_PX = 80  # max vertical gap between Text box bottom and chart top

    for chart_i, chart in chart_idxs:
        chart_x = chart.get("x", 0)
        chart_y = chart.get("y", 0)
        chart_w = chart.get("width", 0)

        best_title = ""
        best_gap   = float("inf")

        for _, tb in text_boxes:
            tb_x     = tb.get("x", 0)
            tb_y     = tb.get("y", 0)
            tb_w     = tb.get("width", 0)
            tb_h     = tb.get("height", 0)
            tb_title = (tb.get("title") or tb.get("fullText") or "").strip().split("\n")[0]

            if not tb_title:
                continue

            # Text box must be ABOVE the chart
            vertical_gap = chart_y - (tb_y + tb_h)
            if not (0 <= vertical_gap <= GAP_PX):
                continue

            # Text box must horizontally overlap with the chart (at least 30%)
            overlap_start = max(chart_x, tb_x)
            overlap_end   = min(chart_x + chart_w, tb_x + tb_w)
            overlap       = max(0, overlap_end - overlap_start)
            min_width     = min(chart_w, tb_w)
            if min_width > 0 and (overlap / min_width) < 0.30:
                continue

            if vertical_gap < best_gap:
                best_gap   = vertical_gap
                best_title = tb_title

        if best_title:
            associations[chart_i] = best_title
            log.debug(f"TextBox association: chart[{chart_i}] → '{best_title}'")

    return associations


def run_phase_a(
    dashboard_page: PBIDashboardPage,
    args: argparse.Namespace,
) -> list[dict]:
    """
    Run Phase A: crawl all pages and collect visual / slicer data.

    Returns the raw pages list from discover_all_pages().
    """
    print("\nPhase A: Crawling dashboard pages…")
    pages_data = dashboard_page.discover_all_pages()
    print(f"    Found {len(pages_data)} page(s)")

    for page in pages_data:
        page_name = page["page_name"]
        visuals   = page["visuals"]
        slicers   = page["slicers"]

        # Compute per-type index WITHIN this page (for visual_type + visual_index strategy)
        type_counters: dict[str, int] = {}
        for v in visuals:
            vtype = v["type"]
            v["type_index"] = type_counters.get(vtype, 0)
            type_counters[vtype] = v["type_index"] + 1

        # Associate Text box titles with nearby charts
        associations = _associate_textbox_titles(visuals)
        for chart_i, desc_title in associations.items():
            visuals[chart_i]["descriptive_title"] = desc_title

        # Classify visuals
        for v in visuals:
            vtype = v["type"]
            title = v.get("title", "").strip()
            full_text = v.get("fullText", "")

            # Reclassify Parameter/Slider controls that claim to be "Card" in aria-roledescription
            is_parameter_slicer = False
            if vtype in KPI_TYPES:
                if any(p in title for p in ["TopN", "BottomN", "Top N", "Bottom N", "Parameter"]):
                    is_parameter_slicer = True
                elif not any(c in full_text for c in ["%", "$", "Target:", "vs PM", "vs PQTD", "vs PTD"]):
                    if any(p in full_text for p in ["TopN", "BottomN", "Field"]):
                        is_parameter_slicer = True

            if is_parameter_slicer or "slicer" in vtype.lower():
                v["category"] = "slicer"
            elif vtype in KPI_TYPES:
                v["category"] = "kpi"
            elif vtype in CHART_TYPES:
                v["category"] = "chart"
            else:
                v["category"] = "other"

            # Pick locating strategy
            if v.get("is_noisy_title") or not v.get("title", "").strip():
                v["strategy"] = "type_index"
            else:
                v["strategy"] = "title"

        print(
            f"    Page '{page_name}': "
            f"{sum(1 for v in visuals if v['category']=='kpi')} KPIs, "
            f"{sum(1 for v in visuals if v['category']=='chart')} charts, "
            f"{len(slicers)} slicer(s)"
        )

    return pages_data


# (Phase B SQL Suggestion removed — SQL is now auto-generated at runtime
#  via utils/sql_template_engine.py from KPI title + slicer state in Excel.)


# ── YAML Generation ────────────────────────────────────────────────────────────

def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def _safe_title(title: str, max_len: int = 80) -> str:
    """
    Sanitize a visual title for safe embedding in a YAML string.

    Power BI table/matrix visuals have no header title in the DOM on some
    dashboard themes.  When that happens, the title field falls back to the
    first body line — which can be the entire table body (multi-line, 300+
    chars).  Embedding that raw into a YAML double-quoted string breaks the
    file and produces garbage like 300 lines of data inside a visual_title.

    This function:
      1. Takes only the FIRST line of the title string.
      2. Strips leading/trailing whitespace and YAML-unsafe quote chars.
      3. Truncates to max_len characters.
    """
    if not title:
        return ""
    # Take only the first non-empty line
    first_line = next((l.strip() for l in title.splitlines() if l.strip()), "")
    # Remove embedded double-quotes (would break YAML double-quoted string)
    first_line = first_line.replace('"', "'")
    # Truncate
    if len(first_line) > max_len:
        first_line = first_line[:max_len].rstrip() + "…"
    return first_line


def _sql_block(sql: str, spaces: int) -> str:
    """Format a SQL query as a YAML block scalar."""
    if not sql:
        return '""'
    if "\n" in sql:
        indented = _indent(sql, spaces + 2)
        return f"|\n{indented}"
    import json
    return json.dumps(sql)


def generate_yaml(
    url: str,
    name: str,
    pages_data: list[dict],
    generated_at: str,
) -> str:
    """Build the complete YAML string from the discovered data."""
    kpi_total   = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "kpi")
    chart_total = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "chart")
    slicer_total = sum(len(p["slicers"]) for p in pages_data)

    # Determine page names for the config
    all_page_names = [p["page_name"] for p in pages_data]
    is_single_page = len(pages_data) == 1

    # DB section — always emit a TODO template for the user to fill in
    db_section = """\
source_db:
  driver:   ""    # TODO: e.g. "mssql+pymssql" or "postgresql"
  host:     ""    # TODO
  port:     ""    # TODO
  database: ""    # TODO
  username: ""    # TODO
  password: ""    # TODO: or use "${DB_PASSWORD}" referencing .env"""

    # Page list
    if is_single_page:
        pages_yaml = "pages: []  # Single-page report"
    else:
        pages_list = "\n".join(f'    - "{n}"' for n in all_page_names)
        pages_yaml = f"pages:\n{pages_list}"

    lines: list[str] = []

    # ── Header ──
    lines.append(f"""\
# {'─'*78}
# Auto-generated by discover_dashboard.py
# Generated:  {generated_at}
# Dashboard:  {name or url}
# URL:        {url}
# Pages:      {len(pages_data)}
# Visuals:    {kpi_total + chart_total} ({kpi_total} KPI cards, {chart_total} charts)
# Slicers:    {slicer_total}
#
# ⚡ REVIEW CHECKLIST:
#   1. Define business logic in business_scenarios.xlsx
#   2. Fill in source_db credentials (or source_excel path)
#   3. Run: pytest tests/dashboard/ --dashboard-config=<this_file>
# {'─'*78}

dashboard:
  name: "{name or 'My Dashboard'}"
  url: "{url}"

{db_section}

source_excel:
  filepath:   ""  # TODO: path to Excel export (alternative to DB)
  sheet_name: ""
""")

    # ── Discovery Reference — printed to terminal, NOT written to YAML ─────────
    # (kpi_validations / table_validations / expected_filters are no longer written
    #  to the YAML. Test cases are driven by business_scenarios.xlsx instead.)
    all_slicers = [(p["page_name"], s) for p in pages_data for s in p["slicers"]]
    kpi_titles  = [
        _safe_title(v.get("title", ""))
        for p in pages_data for v in p["visuals"]
        if v.get("category") == "kpi" and not v.get("is_noisy_title")
        and _safe_title(v.get("title", ""))
    ]

    lines.append("")
    return "\n".join(lines), kpi_titles, all_slicers


# ── Terminal Summary ───────────────────────────────────────────────────────────

def print_summary(
    name: str,
    pages_data: list[dict],
    output_path: str,
    kpi_titles: list[str],
    all_slicers: list[tuple],
) -> None:
    """Print a formatted terminal summary with discovered dashboard metadata."""
    kpi_total    = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "kpi")
    chart_total  = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "chart")
    slicer_total = sum(len(p["slicers"]) for p in pages_data)

    width = 72
    sep   = "─" * width

    print(f"\n╔{sep}╗")
    print(f"║  Discovery Complete{' ' * (width - 19)}║")
    print(f"╠{sep}╣")
    print(f"║  Dashboard:  {name:<{width - 14}}║")
    print(f"║  Pages:      {len(pages_data):<{width - 14}}║")
    print(f"║  KPI Cards:  {kpi_total:<{width - 14}}║")
    print(f"║  Charts:     {chart_total:<{width - 14}}║")
    print(f"║  Slicers:    {slicer_total:<{width - 14}}║")
    print(f"╠{sep}╣")
    print(f"║  Config saved → {output_path:<{width - 17}}║")
    print(f"╠{sep}╣")

    # ── Discovered KPI titles (copy these into Excel 'KPI to Read' column) ──
    print(f"║  KPI Titles found on dashboard:{' ' * (width - 31)}║")
    for t in kpi_titles:
        label = f"    • {t}"
        print(f"║  {label:<{width - 2}}║")

    print(f"╠{sep}╣")

    # ── Discovered slicer names (copy these into Excel 'Slicer N Name' column) ──
    seen_slicers: set[str] = set()
    print(f"║  Slicer Names found on dashboard:{' ' * (width - 33)}║")
    for _, s in all_slicers:
        stitle = s.get("title", "")
        if stitle and stitle not in seen_slicers:
            seen_slicers.add(stitle)
            label = f"    • {stitle}"
            print(f"║  {label:<{width - 2}}║")

    print(f"╠{sep}╣")
    print(f"║  Next steps:{' ' * (width - 13)}║")
    print(f"║    1. Open the YAML file and review all TODO items{' ' * (width - 51)}║")
    print(f"║    2. Focus review on MEDIUM and UNMATCHED SQL{' ' * (width - 53)}║")
    print(f"║    3. Run: pytest tests/dashboard/ --dashboard-config=<file>{' ' * (width - 61)}║")
    print(f"╚{sep}╝\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Resolve output path
    if args.output:
        output_path = Path(args.output)
    else:
        slug = re.sub(r"[^a-z0-9]+", "_", (args.name or "dashboard").lower()).strip("_")
        output_path = _ROOT / "dashboard_configs" / f"{slug}.yaml"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve DB URI
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n  Starting discovery for: {args.url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"]
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page    = context.new_page()
        Stealth().apply_stealth_sync(page)

        from config.settings import SSO_USERNAME, get_sso_password
        dashboard_page = PBIDashboardPage(page)
        dashboard_page.open(args.url)
        dashboard_page.login_via_sso(SSO_USERNAME, get_sso_password())

        # Modern Power BI org reports render visuals in the main DOM just like
        # Publish-to-Web (no iframe). Force PTW mode so extraction uses main frame locators.
        from pageobjects.pbi_dashboard_page import EMBED_MODE_PUBLISH_TO_WEB
        dashboard_page._embed_mode = EMBED_MODE_PUBLISH_TO_WEB

        # --- Phase A ---
        pages_data = run_phase_a(dashboard_page, args)

    # --- Generate YAML ---
    print("\n  Generating YAML config\u2026")
    yaml_content, kpi_titles, all_slicers = generate_yaml(
        url=args.url,
        name=args.name,
        pages_data=pages_data,
        generated_at=generated_at,
    )

    output_path.write_text(yaml_content, encoding="utf-8")
    print(f"    Saved \u2192 {output_path}")

    print_summary(
        name=args.name or args.url[:60],
        pages_data=pages_data,
        output_path=str(output_path),
        kpi_titles=kpi_titles,
        all_slicers=all_slicers,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
