"""
sql_suggester.py — SQL query suggestion engine for the auto-discovery script.

Takes matched column information from schema_introspector.py and produces
candidate SQL queries for KPI cards and chart/table visuals.

Each generated query comes with a confidence tag:
  HIGH   — direct column match, straightforward aggregation
  MEDIUM — partial column match or inferred aggregation
  LOW    — best-guess based on limited evidence
  NONE   — could not generate a sensible query

These confidence tags are written as comments into the generated YAML so
the user can focus their review on uncertain queries.
"""

from __future__ import annotations

from utils.logger import get_logger
from utils.schema_introspector import (
    match_columns,
    find_best_table,
    infer_aggregation,
    build_filter_clause,
)

log = get_logger("sql_suggester")


# ── KPI Card SQL ───────────────────────────────────────────────────────────────

def suggest_kpi_sql(
    card_title: str,
    schema: dict,
    slicers: list[dict],
    db_driver: str = "",
) -> tuple[str, str]:
    """
    Generate a candidate scalar SQL query for a KPI card visual.

    The query returns a single aggregate value (SUM, COUNT, or AVG) that
    can be compared directly against the card's displayed value.

    Args:
        card_title: KPI card title (e.g. "Sales", "Profit Margin", "Quantity").
        schema:     Schema dict from introspect_schema().
        slicers:    Detected slicer state from discover_all_pages().
        db_driver:  SQLAlchemy driver name (for date function dialect).

    Returns:
        Tuple (sql_query, confidence) where confidence is one of:
        "HIGH", "MEDIUM", "LOW", "NONE".
    """
    log.info(f"suggest_kpi_sql: '{card_title}'")

    # Match the card title to a DB column
    col_matches = match_columns([card_title], schema)
    if not col_matches:
        return "", "NONE"

    m = col_matches[0]
    if m["confidence"] == "NONE":
        log.debug(f"No column match for KPI '{card_title}'")
        return "", "NONE"

    table_name = m["db_table"]
    col_name   = m["db_col"]
    db_type    = m["db_type"]
    confidence = m["confidence"]

    # Detect complex measures that we can't auto-generate reliably
    complex_keywords = ("yoy", "y-o-y", "growth", "variance", "vs ", " vs",
                        "prior year", "previous year", "py ", " py", "running",
                        "cumulative", "ytd", "mtd")
    if any(kw in card_title.lower() for kw in complex_keywords):
        log.debug(f"KPI '{card_title}' looks like a complex measure — skipping")
        return "", "NONE"

    # Infer aggregation
    agg = infer_aggregation(col_name, db_type, kpi_title=card_title)
    if not agg:
        agg = "SUM"   # Fallback

    # Build filter clause
    where = build_filter_clause(slicers, schema, db_driver)
    where_clause = f"\nWHERE {where}" if where else ""

    sql = f'SELECT {agg}("{col_name}") FROM {table_name}{where_clause}'

    # Downgrade confidence for AVG (more ambiguous than SUM)
    if agg == "AVG" and confidence == "HIGH":
        confidence = "MEDIUM"

    log.info(f"KPI '{card_title}' → {sql!r} [{confidence}]")
    return sql, confidence


# ── Chart / Table SQL ──────────────────────────────────────────────────────────

def suggest_table_sql(
    chart_headers: list[str],
    schema: dict,
    slicers: list[dict],
    db_driver: str = "",
    descriptive_title: str = "",
) -> tuple[str, str, list[str], list[str]]:
    """
    Generate a candidate GROUP-BY SQL query for a chart or table visual.

    Uses the column headers from "Show as a table" to identify which DB columns
    correspond to the chart's dimensions (join keys) and measures (compare cols).

    Args:
        chart_headers:     Column header strings from extract_chart_headers().
        schema:            Schema dict from introspect_schema().
        slicers:           Detected slicer state from discover_all_pages().
        db_driver:         SQLAlchemy driver name.
        descriptive_title: Human-readable chart title for logging.

    Returns:
        Tuple (sql_query, confidence, join_keys, compare_cols).
        join_keys    — list of categorical column names (GROUP BY candidates)
        compare_cols — list of numeric column names (for value comparison)
        If no query can be generated, returns ("", "NONE", [], []).
    """
    label = descriptive_title or str(chart_headers)
    log.info(f"suggest_table_sql: '{label}' headers={chart_headers}")

    if not chart_headers:
        return "", "NONE", [], []

    col_matches = match_columns(chart_headers, schema)

    # Separate into matched / unmatched
    matched   = [m for m in col_matches if m["confidence"] != "NONE"]
    unmatched = [m for m in col_matches if m["confidence"] == "NONE"]

    if not matched:
        log.debug(f"No column matches for chart '{label}'")
        return "", "NONE", [], []

    # Pick the dominant source table
    source_table = find_best_table(matched)
    if not source_table:
        return "", "NONE", [], []

    # Split into GROUP BY columns (categorical) and aggregate columns (numeric)
    group_cols:  list[dict] = []
    metric_cols: list[dict] = []

    for m in matched:
        if m["db_table"] != source_table:
            continue   # skip columns from other tables (cross-join risk)
        if m["is_numeric"]:
            metric_cols.append(m)
        else:
            group_cols.append(m)

    # If we have no metrics, treat all matched numeric-ish cols as metrics
    if not metric_cols and group_cols:
        metric_cols = group_cols
        group_cols  = []

    # Build SELECT clause
    select_parts: list[str] = []
    join_keys:    list[str] = []
    compare_cols: list[str] = []

    for m in group_cols:
        db_col   = m["db_col"]
        dash_col = m["dashboard_col"]
        select_parts.append(f'"{db_col}" AS "{dash_col}"')
        join_keys.append(dash_col)

    for m in metric_cols:
        db_col   = m["db_col"]
        dash_col = m["dashboard_col"]
        agg      = infer_aggregation(db_col, m["db_type"])
        if agg:
            select_parts.append(f'{agg}("{db_col}") AS "{dash_col}"')
            compare_cols.append(dash_col)
        else:
            select_parts.append(f'"{db_col}" AS "{dash_col}"')
            join_keys.append(dash_col)

    if not select_parts:
        return "", "NONE", [], []

    # Build WHERE clause from slicers
    where = build_filter_clause(slicers, schema, db_driver)
    where_clause = f"\nWHERE {where}" if where else ""

    # Build GROUP BY clause
    group_by_cols = [f'"{m["db_col"]}"' for m in group_cols]
    group_by      = f"\nGROUP BY {', '.join(group_by_cols)}" if group_by_cols else ""

    sql = (
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM {source_table}"
        f"{where_clause}"
        f"{group_by}"
    )

    # Determine overall confidence
    confidences = [m["confidence"] for m in matched]
    if all(c == "HIGH" for c in confidences):
        confidence = "HIGH"
    elif any(c in ("HIGH", "MEDIUM") for c in confidences):
        confidence = "MEDIUM"
    elif unmatched:
        confidence = "LOW"
    else:
        confidence = "MEDIUM"

    log.info(f"Chart '{label}' → [{confidence}] join_keys={join_keys} compare_cols={compare_cols}")
    return sql, confidence, join_keys, compare_cols


# ── Confidence Formatting ──────────────────────────────────────────────────────

CONFIDENCE_EMOJI = {
    "HIGH":   "✅ HIGH",
    "MEDIUM": "⚠️  MEDIUM",
    "LOW":    "🔸 LOW",
    "NONE":   "❌ COULD NOT AUTO-SUGGEST",
}

CONFIDENCE_COMMENT = {
    "HIGH":   "Direct column match — verify query is correct.",
    "MEDIUM": "Partial match — review and adjust the query if needed.",
    "LOW":    "Best guess — likely needs manual correction.",
    "NONE":   "No DB column match found. Write this SQL manually.",
}


def format_confidence_comment(confidence: str, extra: str = "") -> str:
    """Return a YAML comment string for the given confidence level."""
    emoji   = CONFIDENCE_EMOJI.get(confidence, confidence)
    comment = CONFIDENCE_COMMENT.get(confidence, "")
    parts   = [f"# {emoji} — {comment}"]
    if extra:
        parts.append(f"# {extra}")
    return "\n    ".join(parts)
