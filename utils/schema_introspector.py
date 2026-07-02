"""
schema_introspector.py — Database schema introspection and column matching.

Used by the auto-discovery script (discover_dashboard.py) to:
  1. Introspect the source database schema (tables, columns, types, FKs)
  2. Fuzzy-match dashboard column headers / KPI titles to DB columns
  3. Infer the correct SQL aggregation function (SUM / COUNT / AVG)
  4. Generate WHERE clause fragments from detected slicer state

No dashboard-specific logic lives here — all functions are generic.
"""

from __future__ import annotations

import re
from typing import Optional

from utils.logger import get_logger

log = get_logger("schema_introspector")


# ── Schema Introspection ───────────────────────────────────────────────────────

def introspect_schema(engine) -> dict:
    """
    Inspect the source database and return a structured schema map.

    Uses SQLAlchemy's ``inspect()`` interface which works across all supported
    databases: PostgreSQL, SQL Server, MySQL, Snowflake, etc.

    Returns:
        Dict with the structure::

            {
              "tables": {
                "fact_sales": {
                  "columns": {
                    "sale_id":   {"type": "INTEGER",  "nullable": False},
                    "sale_date": {"type": "DATE",     "nullable": True},
                    "market":   {"type": "VARCHAR",   "nullable": True},
                    "sales":    {"type": "DECIMAL",   "nullable": True},
                  }
                },
                ...
              },
              "foreign_keys": [
                {"from_table": "fact_sales", "from_col": "product_id",
                 "to_table": "dim_product", "to_col": "id"}
              ]
            }
    """
    from sqlalchemy import inspect as sa_inspect

    log.info("Introspecting database schema…")
    inspector = sa_inspect(engine)
    schema_map: dict = {"tables": {}, "foreign_keys": []}

    table_names = inspector.get_table_names()
    # Also check views for flexibility
    try:
        table_names += inspector.get_view_names()
    except Exception:
        pass

    log.info(f"Found {len(table_names)} tables/views: {table_names}")

    for table_name in table_names:
        cols = {}
        try:
            for col in inspector.get_columns(table_name):
                type_str = str(col["type"].__class__.__name__).upper()
                cols[col["name"]] = {
                    "type":     type_str,
                    "nullable": col.get("nullable", True),
                }
        except Exception as e:
            log.warning(f"Could not introspect table '{table_name}': {e}")
            continue

        schema_map["tables"][table_name] = {"columns": cols}

        # Collect foreign keys
        try:
            for fk_group in inspector.get_foreign_keys(table_name):
                for from_col, to_col in zip(
                    fk_group.get("constrained_columns", []),
                    fk_group.get("referred_columns", []),
                ):
                    schema_map["foreign_keys"].append({
                        "from_table": table_name,
                        "from_col":   from_col,
                        "to_table":   fk_group.get("referred_table", ""),
                        "to_col":     to_col,
                    })
        except Exception:
            pass

    log.info(
        f"Schema introspection complete: {len(schema_map['tables'])} tables, "
        f"{len(schema_map['foreign_keys'])} foreign keys"
    )
    return schema_map


# ── Column Matching ────────────────────────────────────────────────────────────

def _normalise(name: str) -> str:
    """Lowercase, strip whitespace and punctuation for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def match_columns(
    dashboard_headers: list[str],
    schema: dict,
) -> list[dict]:
    """
    Fuzzy-match dashboard column headers (from KPI titles or chart table headers)
    to database columns.

    Matching is tried in cascading order until a match is found:
      1. Exact match (case-insensitive, ignoring spaces/punctuation)
      2. Partial match: dashboard header is contained in the DB column name
      3. Partial match: DB column name is contained in the dashboard header
      4. No match → confidence = "NONE"

    Each result dict contains:
        dashboard_col  — original dashboard header string
        db_table       — matched table name (empty string if no match)
        db_col         — matched column name (empty string if no match)
        db_type        — SQL type string (e.g. "INTEGER", "VARCHAR")
        confidence     — "HIGH" | "MEDIUM" | "LOW" | "NONE"
        is_numeric     — True when db_type is a numeric type
        is_date        — True when db_type is a date/timestamp type
        is_categorical — True when db_type is text/char/boolean
    """
    results: list[dict] = []

    for header in dashboard_headers:
        norm_header = _normalise(header)

        best: Optional[dict] = None
        best_confidence = "NONE"

        for table_name, table_info in schema["tables"].items():
            for col_name, col_info in table_info["columns"].items():
                norm_col = _normalise(col_name)

                # Level 1 — exact normalised match
                if norm_col == norm_header:
                    conf = "HIGH"
                # Level 2 — header is a substring of col name or vice versa
                elif norm_header in norm_col or norm_col in norm_header:
                    conf = "MEDIUM"
                # Level 3 — at least 4-char overlap
                elif len(norm_header) >= 4 and any(
                    norm_header[i:i+4] in norm_col for i in range(len(norm_header) - 3)
                ):
                    conf = "LOW"
                else:
                    continue

                # Keep the highest-confidence match per header
                priority = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
                if priority[conf] > priority.get(best_confidence, 0):
                    db_type = col_info["type"]
                    best = {
                        "dashboard_col":  header,
                        "db_table":       table_name,
                        "db_col":         col_name,
                        "db_type":        db_type,
                        "confidence":     conf,
                        "is_numeric":     _is_numeric_type(db_type),
                        "is_date":        _is_date_type(db_type),
                        "is_categorical": _is_categorical_type(db_type),
                    }
                    best_confidence = conf

        if best is None:
            best = {
                "dashboard_col":  header,
                "db_table":       "",
                "db_col":         "",
                "db_type":        "",
                "confidence":     "NONE",
                "is_numeric":     False,
                "is_date":        False,
                "is_categorical": False,
            }

        log.debug(
            f"Column match '{header}' → {best['db_table']}.{best['db_col']} "
            f"[{best['confidence']}]"
        )
        results.append(best)

    return results


def _is_numeric_type(type_str: str) -> bool:
    numeric_keywords = {
        "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER",
        "BIGINT", "SMALLINT", "REAL", "MONEY", "CURRENCY",
    }
    return any(kw in type_str.upper() for kw in numeric_keywords)


def _is_date_type(type_str: str) -> bool:
    date_keywords = {"DATE", "TIME", "TIMESTAMP", "DATETIME", "YEAR"}
    return any(kw in type_str.upper() for kw in date_keywords)


def _is_categorical_type(type_str: str) -> bool:
    cat_keywords = {"CHAR", "TEXT", "STRING", "BOOL", "ENUM", "NVAR"}
    return any(kw in type_str.upper() for kw in cat_keywords)


# ── Table Selection ────────────────────────────────────────────────────────────

def find_best_table(matches: list[dict]) -> str:
    """
    Given a list of column matches (from match_columns), pick the single
    database table that is most likely the source for this visual.

    Strategy: the table with the most HIGH/MEDIUM confidence matches wins.
    If tied, prefer tables whose name starts with 'fact' (common data warehouse
    naming convention).

    Returns:
        Best table name string, or empty string if no matches at all.
    """
    if not matches:
        return ""

    table_scores: dict[str, int] = {}
    priority = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}

    for m in matches:
        tbl = m["db_table"]
        if not tbl:
            continue
        table_scores[tbl] = table_scores.get(tbl, 0) + priority.get(m["confidence"], 0)

    if not table_scores:
        return ""

    max_score = max(table_scores.values())
    best_tables = [t for t, s in table_scores.items() if s == max_score]

    # Prefer fact tables (data warehouse convention)
    fact_tables = [t for t in best_tables if t.lower().startswith("fact")]
    return (fact_tables or best_tables)[0]


# ── Aggregation Inference ──────────────────────────────────────────────────────

def infer_aggregation(col_name: str, col_type: str, kpi_title: str = "") -> str:
    """
    Infer the most appropriate SQL aggregation function for a column.

    Rules (in priority order):
      • Title contains "count" or "number of" → COUNT
      • Title contains "average" or "avg"     → AVG
      • Title contains "margin", "rate", "%"  → AVG (ratio)
      • Column is numeric                      → SUM (default for KPIs)
      • Column is categorical                  → COUNT (group-by candidate)
      • Fallback                               → SUM

    Returns:
        SQL aggregation string: "SUM", "COUNT", "AVG", or "".
    """
    combined = (kpi_title + " " + col_name).lower()

    if any(kw in combined for kw in ("count", "number of", "# of", "qty", "quantity")):
        return "COUNT" if "count" in combined else "SUM"
    if any(kw in combined for kw in ("average", " avg", "mean", "per ")):
        return "AVG"
    if any(kw in combined for kw in ("margin", "rate", "ratio", "percent", "%", "growth")):
        return "AVG"
    if _is_numeric_type(col_type):
        return "SUM"
    if _is_categorical_type(col_type):
        return ""   # no aggregation — this is a GROUP BY column
    return "SUM"


# ── Filter Clause Generation ───────────────────────────────────────────────────

def build_filter_clause(
    slicers: list[dict],
    schema: dict,
    db_driver: str = "",
) -> str:
    """
    Build a SQL WHERE clause from detected slicer state.

    For each slicer, finds the matching DB column and generates the appropriate
    filter expression.  Date/year slicers produce EXTRACT() clauses; categorical
    slicers produce equality or IN() clauses.

    Args:
        slicers:   List of {title, values} dicts from discover_all_pages().
        schema:    Schema dict from introspect_schema().
        db_driver: SQLAlchemy driver name (affects date extraction syntax).

    Returns:
        WHERE clause string without the "WHERE" keyword, e.g.:
        "EXTRACT(YEAR FROM sale_date) = 2022 AND region = 'North'"
        Returns empty string if no slicer matches any DB column.
    """
    clauses: list[str] = []

    for slicer in slicers:
        title = slicer.get("title", "")
        values = slicer.get("values", [])
        if not values or values == ["All"]:
            continue

        # Match slicer title to a DB column
        col_matches = match_columns([title], schema)
        if not col_matches:
            continue

        m = col_matches[0]
        if m["confidence"] == "NONE":
            continue

        col_ref = f'"{m["db_col"]}"'

        # For date columns where value is a year number
        if m["is_date"] and all(v.isdigit() and len(v) == 4 for v in values):
            year_val = values[0]
            date_fn = _year_extract_fn(m["db_col"], db_driver)
            clauses.append(f"{date_fn} = {year_val}")

        # Categorical / exact match
        elif len(values) == 1:
            val = values[0].replace("'", "''")
            clauses.append(f"{col_ref} = '{val}'")

        # Multiple selections → IN()
        else:
            in_vals = ", ".join(f"'{v.replace(chr(39), chr(39)*2)}'" for v in values)
            clauses.append(f"{col_ref} IN ({in_vals})")

    result = " AND ".join(clauses)
    if result:
        log.info(f"Generated filter clause: {result}")
    return result


def _year_extract_fn(col_name: str, db_driver: str) -> str:
    """Return the correct year-extraction SQL fragment for the given driver."""
    col_ref = f'"{col_name}"'
    if "mssql" in db_driver or "pyodbc" in db_driver:
        return f"YEAR({col_ref})"
    if "mysql" in db_driver or "pymysql" in db_driver:
        return f"YEAR({col_ref})"
    if "snowflake" in db_driver:
        return f"DATE_PART(year, {col_ref})"
    # PostgreSQL / default (ANSI)
    return f"EXTRACT(YEAR FROM {col_ref})"
