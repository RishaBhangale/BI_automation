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
    p.add_argument("--db-uri",       default="",   help="SQLAlchemy DB URI for SQL suggestion (Phase B)")
    p.add_argument("--db-env",       action="store_true",
                   help="Load DB credentials from .env / settings.py instead of --db-uri")
    p.add_argument("--headless",     action="store_true", default=True,
                   help="Run browser in headless mode (default: True)")
    p.add_argument("--no-headless",  action="store_false", dest="headless",
                   help="Run browser with a visible window")
    p.add_argument("--timeout",      type=int, default=30,
                   help="Max seconds to wait for dashboard to load (default: 30)")
    p.add_argument("--skip-headers", action="store_true",
                   help="Skip 'Show as a table' header extraction (faster, lower SQL quality)")
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
    print("\n⏳  Phase A: Crawling dashboard pages…")
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
            if vtype in KPI_TYPES:
                v["category"] = "kpi"
            elif vtype in CHART_TYPES:
                v["category"] = "chart"
            elif vtype == "Slicer":
                v["category"] = "slicer"
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


# ── Phase B: SQL Suggestion ────────────────────────────────────────────────────

def run_phase_b(
    pages_data: list[dict],
    dashboard_page: PBIDashboardPage,
    db_uri: str,
    skip_headers: bool,
) -> dict:
    """
    Run Phase B: connect to DB, introspect schema, suggest SQL per visual.

    Returns:
        Dict mapping (page_index, visual_list_index) → {sql, confidence,
        join_keys, compare_cols}.
    """
    from utils.schema_introspector import introspect_schema
    from utils.llm_sql_generator import suggest_sql_via_llm
    from utils.db_utils import get_db_engine
    import json

    print("\n⏳  Phase B: Connecting to database…")
    try:
        engine = get_db_engine(db_uri)
        schema = introspect_schema(engine)
        print(f"    Schema: {len(schema['tables'])} table(s) introspected")
    except Exception as e:
        print(f"    ⚠️  DB connection failed: {e}")
        print("    Skipping SQL suggestion — queries will be empty (TODO).")
        return {}

    # Detect DB driver from URI for date function dialect
    db_driver = db_uri.split("://")[0] if "://" in db_uri else ""

    suggestions: dict[tuple, dict] = {}

    schema_ddl = json.dumps(schema, indent=2)

    for page in pages_data:
        page_idx = page["page_index"]
        slicers  = page["slicers"]
        visuals  = page["visuals"]

        for v_idx, v in enumerate(visuals):
            cat = v.get("category")
            desc = v.get("descriptive_title") or v.get("title") or f"{v.get('type')}[{v.get('type_index', 0)}]"
            
            if cat == "kpi":
                sql, conf, join_keys, compare_cols = suggest_sql_via_llm(
                    visual_type="KPI Card",
                    visual_title=desc,
                    chart_headers=[],
                    schema_ddl=schema_ddl,
                    slicers=slicers,
                    db_driver=db_driver
                )
                suggestions[(page_idx, v_idx)] = {
                    "sql": sql, "confidence": conf,
                    "join_keys": [], "compare_cols": [],
                }

            elif cat == "chart" and not skip_headers:
                # Extract column headers via "Show as a table"
                vtype  = v.get("type", "")
                tidx   = v.get("type_index", 0)
                vtitle = "" if v.get("strategy") == "type_index" else v.get("title", "")
                headers = []
                try:
                    headers = dashboard_page.extract_chart_headers(vtype, tidx, vtitle)
                except Exception as e:
                    log.debug(f"extract_chart_headers failed: {e}")

                sql, conf, join_keys, compare_cols = suggest_sql_via_llm(
                    visual_type=vtype,
                    visual_title=desc,
                    chart_headers=headers,
                    schema_ddl=schema_ddl,
                    slicers=slicers,
                    db_driver=db_driver
                )
                suggestions[(page_idx, v_idx)] = {
                    "sql": sql, "confidence": conf,
                    "join_keys": join_keys, "compare_cols": compare_cols,
                }

    return suggestions


# ── YAML Generation ────────────────────────────────────────────────────────────

def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


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
    suggestions: dict,
    db_uri: str,
    generated_at: str,
) -> str:
    """
    Build the complete YAML string from the discovered data and SQL suggestions.
    """
    from utils.sql_suggester import CONFIDENCE_EMOJI, CONFIDENCE_COMMENT

    kpi_total   = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "kpi")
    chart_total = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "chart")
    slicer_total = sum(len(p["slicers"]) for p in pages_data)

    # Determine page names for the config
    all_page_names = [p["page_name"] for p in pages_data]
    is_single_page = len(pages_data) == 1

    # DB section
    if db_uri:
        driver_raw = db_uri.split("://")[0]
        rest       = db_uri.split("://", 1)[1] if "://" in db_uri else ""
        user_host  = rest.split("@")[1] if "@" in rest else rest
        db_section = f"""\
source_db:
  driver:   "{driver_raw}"
  host:     "{user_host.split('/')[0].split(':')[0]}"
  port:     ""
  database: "{user_host.split('/')[-1] if '/' in user_host else ''}"
  username: ""
  password: "${{DB_PASSWORD}}"  # set DB_PASSWORD in .env"""
    else:
        db_section = """\
source_db:
  driver:   ""    # TODO: e.g. "postgresql" or "mssql+pyodbc"
  host:     ""    # TODO
  port:     ""    # TODO
  database: ""    # TODO
  username: ""    # TODO
  password: ""    # TODO: or "${DB_PASSWORD}" """

    # Page list
    if is_single_page:
        pages_yaml = "pages: []  # Single-page report"
    else:
        pages_list = "\n".join(f'    - "{n}"' for n in all_page_names)
        pages_yaml = f"pages:\n{pages_list}"

    lines: list[str] = []

    # ── Header ──
    phase_b_note = (
        f"#   SQL Suggestion: ENABLED ({len(db_uri.split('@')[-1]) if db_uri else 0} tables scanned)\n"
        if db_uri else
        "#   SQL Suggestion: DISABLED (run with --db-uri to enable)\n"
    )

    lines.append(f"""\
# {'─'*78}
# Auto-generated by discover_dashboard.py
# Generated:  {generated_at}
# Dashboard:  {name or url}
# URL:        {url}
# Pages:      {len(pages_data)}
# Visuals:    {kpi_total + chart_total} ({kpi_total} KPI cards, {chart_total} charts)
# Slicers:    {slicer_total}
{phase_b_note}#
# ⚡ REVIEW CHECKLIST:
#   1. Verify each visual entry matches what you see on screen
#   2. Review SQL queries (focus on ⚠️ MEDIUM and ❌ UNMATCHED)
#   3. Fill in source_db credentials (or source_excel path)
#   4. Run: pytest tests/dashboard/ --dashboard-config=<this_file>
# {'─'*78}

dashboard:
  name: "{name or 'My Dashboard'}"
  url: "{url}"
  {pages_yaml}

{db_section}

source_excel:
  filepath:   ""  # TODO: path to Excel export (alternative to DB)
  sheet_name: ""
""")

    # ── KPI Validations ──
    lines.append(f"# {'─'*78}")
    lines.append(f"# Auto-discovered KPI Cards ({kpi_total} found)")
    lines.append(f"# {'─'*78}")
    lines.append("kpi_validations:")

    for page in pages_data:
        page_name = page["page_name"]
        page_idx  = page["page_index"]
        kpis = [(i, v) for i, v in enumerate(page["visuals"]) if v.get("category") == "kpi"]

        if not kpis:
            continue

        if not is_single_page:
            lines.append(f"\n  # ── Page: '{page_name}' ──")

        for v_idx, v in kpis:
            title = v.get("title", "")
            page_field = "" if is_single_page else page_name

            sg = suggestions.get((page_idx, v_idx), {})
            sql        = sg.get("sql", "")
            confidence = sg.get("confidence", "NONE") if db_uri else ""
            sql_block  = _sql_block(sql, 4) if sql else '""  # TODO: add SQL query'

            conf_comment = ""
            if db_uri:
                emoji = CONFIDENCE_EMOJI.get(confidence, confidence)
                cmnt  = CONFIDENCE_COMMENT.get(confidence, "")
                conf_comment = f"  # {emoji} — {cmnt}\n"

            lines.append(
                f"\n{conf_comment}"
                f"  - visual_title: \"{title}\"\n"
                f"    page: \"{page_field}\"\n"
                f"    sql_query: {sql_block}\n"
                f"    tolerance: 0.01"
            )

    lines.append("")

    # ── Table Validations ──
    lines.append(f"\n# {'─'*78}")
    lines.append(f"# Auto-discovered Charts ({chart_total} found)")
    lines.append("# Each entry uses 'Show as a table' to extract underlying data.")
    lines.append("# Strategy A = located by title | Strategy B = located by type+index")
    lines.append(f"# {'─'*78}")
    lines.append("table_validations:")

    for page in pages_data:
        page_name = page["page_name"]
        page_idx  = page["page_index"]
        charts = [(i, v) for i, v in enumerate(page["visuals"]) if v.get("category") == "chart"]

        if not charts:
            continue

        if not is_single_page:
            lines.append(f"\n  # ── Page: '{page_name}' ──")

        for v_idx, v in charts:
            vtype    = v.get("type", "")
            tidx     = v.get("type_index", 0)
            strategy = v.get("strategy", "title")
            title    = v.get("title", "")
            desc     = v.get("descriptive_title", "")
            page_field = "" if is_single_page else page_name

            sg = suggestions.get((page_idx, v_idx), {})
            sql          = sg.get("sql", "")
            confidence   = sg.get("confidence", "NONE") if db_uri else ""
            join_keys    = sg.get("join_keys", [])
            compare_cols = sg.get("compare_cols", [])

            sql_block = _sql_block(sql, 4) if sql else '""  # TODO: add SQL query'

            conf_comment = ""
            if db_uri:
                emoji = CONFIDENCE_EMOJI.get(confidence, confidence)
                cmnt  = CONFIDENCE_COMMENT.get(confidence, "")
                conf_comment = f"  # {emoji} — {cmnt}\n"

            # Strategy A — title-based
            if strategy == "title":
                desc_note = f"  # Chart: \"{title}\" — Strategy A (title-based)\n"
                loc_block = f'  - visual_title: "{title}"\n    visual_type:  ""\n    visual_index: null\n'
            # Strategy B — type+index
            else:
                chart_label = desc or f"{vtype}[{tidx}]"
                desc_note = (
                    f"  # Chart: \"{chart_label}\" — Strategy B (type+index)\n"
                    f"  # Title is in a separate Text box visual. Locating by type+index.\n"
                )
                loc_block = f'  - visual_title: ""\n    visual_type:  "{vtype}"\n    visual_index: {tidx}\n'

            # Join keys
            if join_keys:
                jk_yaml = "\n    ".join(f'- "{k}"' for k in join_keys)
                join_block = f"    join_keys:\n      {jk_yaml}"
            else:
                join_block = "    join_keys:\n      - \"\"  # TODO: column(s) to align on"

            # Compare cols
            if compare_cols:
                cc_yaml = "\n    ".join(f'- "{c}"' for c in compare_cols)
                cmp_block = f"    compare_cols:\n      {cc_yaml}"
            else:
                cmp_block = "    compare_cols:\n      - \"\"  # TODO: numeric column(s) to compare"

            lines.append(
                f"\n{conf_comment}{desc_note}"
                f"{loc_block}"
                f"    page: \"{page_field}\"\n"
                f"    sql_query: {sql_block}\n"
                f"{join_block}\n"
                f"{cmp_block}\n"
                f"    tolerance: 0.01"
            )

    lines.append("")

    # ── Expected Filters ──
    all_slicers = [(p["page_name"], s) for p in pages_data for s in p["slicers"]]
    lines.append(f"\n# {'─'*78}")
    lines.append(f"# Auto-discovered Slicer State ({len(all_slicers)} slicer(s))")
    lines.append("# These assert that the dashboard's default filter state matches")
    lines.append("# the context of your SQL WHERE clauses.")
    lines.append(f"# {'─'*78}")
    lines.append("expected_filters:")

    if all_slicers:
        for spage, slicer in all_slicers:
            stitle = slicer.get("title", "")
            svals  = slicer.get("values", [])
            sval   = svals[0] if len(svals) == 1 else str(svals)
            spage_field = "" if is_single_page else spage
            lines.append(
                f"  - slicer_title: \"{stitle}\"\n"
                f"    page: \"{spage_field}\"\n"
                f"    expected_value: \"{sval}\""
            )
    else:
        lines.append("  []  # No slicers detected")

    lines.append("")
    return "\n".join(lines)


# ── Terminal Summary ───────────────────────────────────────────────────────────

def print_summary(
    name: str,
    pages_data: list[dict],
    suggestions: dict,
    output_path: str,
    db_uri: str,
) -> None:
    """Print a formatted terminal summary after generation is complete."""
    kpi_total    = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "kpi")
    chart_total  = sum(1 for p in pages_data for v in p["visuals"] if v.get("category") == "chart")
    slicer_total = sum(len(p["slicers"]) for p in pages_data)

    if db_uri and suggestions:
        confidences  = [s.get("confidence", "NONE") for s in suggestions.values()]
        high_count   = confidences.count("HIGH")
        medium_count = confidences.count("MEDIUM")
        low_count    = confidences.count("LOW")
        none_count   = confidences.count("NONE")
        sql_line     = f"  SQL Suggestions:  ✅ {high_count} HIGH | ⚠️  {medium_count} MEDIUM | 🔸 {low_count} LOW | ❌ {none_count} UNMATCHED"
    else:
        sql_line = "  SQL Suggestions:  Disabled (run with --db-uri to enable)"

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
    print(f"║  {sql_line:<{width - 2}}║")
    print(f"╠{sep}╣")
    print(f"║  Config saved to:{'  ' + output_path:<{width - 18}}║")
    print(f"╠{sep}╣")
    print(f"║  Next steps:{' ' * (width - 13)}║")
    print(f"║    1. Open the YAML file and review all TODO items{' ' * (width - 51)}║")
    print(f"║    2. Focus review on ⚠️  MEDIUM and ❌ UNMATCHED SQL{' ' * (width - 53)}║")
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
    db_uri = args.db_uri
    if args.db_env and not db_uri:
        from config.db_config import build_db_uri
        db_uri = build_db_uri()
        if not db_uri:
            print("⚠️  --db-env specified but no DB credentials found in settings. Skipping SQL.")

    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n🚀  Starting discovery for: {args.url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page    = context.new_page()

        dashboard_page = PBIDashboardPage(page)
        dashboard_page.open(args.url)

        # --- Phase A ---
        pages_data = run_phase_a(dashboard_page, args)

        # --- Phase B (optional) ---
        suggestions: dict = {}
        if db_uri:
            suggestions = run_phase_b(pages_data, dashboard_page, db_uri, args.skip_headers)

        browser.close()

    # --- Generate YAML ---
    print("\n📝  Generating YAML config…")
    yaml_content = generate_yaml(
        url=args.url,
        name=args.name,
        pages_data=pages_data,
        suggestions=suggestions,
        db_uri=db_uri,
        generated_at=generated_at,
    )

    output_path.write_text(yaml_content, encoding="utf-8")
    print(f"    Saved → {output_path}")

    print_summary(
        name=args.name or args.url[:60],
        pages_data=pages_data,
        suggestions=suggestions,
        output_path=str(output_path),
        db_uri=db_uri,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
