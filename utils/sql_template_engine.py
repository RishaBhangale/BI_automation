"""
sql_template_engine.py — Dynamic SQL query builder for business scenario tests.

Eliminates the need to write and maintain individual .sql files per test case.
Given a KPI card title and a list of (slicer_name, value) pairs from the Excel
test sheet, this module constructs the correct SQL SELECT statement at runtime.

Usage (inside test_business_scenarios.py):
    from utils.sql_template_engine import build_query

    # Equivalent of: SELECT SUM(Sales) FROM SALES WHERE State='California' AND Ship_Mode='Standard Class'
    sql = build_query(
        kpi_title="Total Sales",
        slicers=[("State", "California"), ("Ship Mode", "Standard Class")],
    )

To add a new KPI or slicer, simply add an entry to KPI_MAP or SLICER_MAP below.
"""

from __future__ import annotations

from utils.logger import get_logger

log = get_logger("sql_template_engine")

# ── Source table ──────────────────────────────────────────────────────────────
# Confirmed via DB introspection: table is SALES in devaibisqldb.
SOURCE_TABLE: str = "SALES"

# ── KPI title → SQL aggregation expression ────────────────────────────────────
# Key   : Exact text of the KPI card title as it appears on the Power BI report.
# Value : SQL aggregation expression to place in the SELECT clause.
KPI_MAP: dict[str, str] = {
    "Total Sales":     "SUM(Sales)",
    "Total Profit":    "SUM(Profit)",
    "Total Quantity":  "SUM(Quantity)",
    "# of Orders":     "COUNT(DISTINCT Order_ID)",
    "# of Products":   "COUNT(DISTINCT Product_ID)",
    "# of Customers":  "COUNT(DISTINCT Customer_ID)",
}

# ── Dashboard slicer name → DB column name ────────────────────────────────────
# Key   : Slicer label as it appears in the Power BI filter pane / on screen.
# Value : Corresponding column name in the SALES table.
#
# Confirmed from SALES schema:
#   State, Ship_Mode, Sub_Category, Segment, Category, City,
#   Region, Postal_Code, Customer_Name, Product_Name, Country_Region
SLICER_MAP: dict[str, str] = {
    "State":            "State",
    "Ship Mode":        "Ship_Mode",
    "Sub-Category":     "Sub_Category",
    "Customer Segment": "Segment",
    "Category":         "Category",
    "City":             "City",
    "Region":           "Region",
    "Postal Code":      "Postal_Code",
    "Customer Name":    "Customer_Name",
    "Product Name":     "Product_Name",
    "Country/Region":   "Country_Region",
    "Segment":          "Segment",      # alternate label some dashboards use
}


def build_query(
    kpi_title: str,
    slicers: list[tuple[str, str]],
    table: str = SOURCE_TABLE,
) -> str:
    """
    Build a SQL SELECT query from a KPI title and a list of slicer (name, value) pairs.

    Args:
        kpi_title:  KPI card title as shown on the dashboard (must be in KPI_MAP).
        slicers:    List of (slicer_name, slicer_value) tuples.
                    Slicer names must be in SLICER_MAP.
        table:      Source DB table name (default: SOURCE_TABLE = "SALES").

    Returns:
        A complete SQL SELECT string, e.g.:
        "SELECT SUM(Sales) FROM SALES WHERE State='California' AND Ship_Mode='Standard Class'"

    Raises:
        ValueError: If kpi_title is not in KPI_MAP, or any slicer_name is not in SLICER_MAP.
                    The error message tells the user exactly what to add to the config.

    Examples:
        >>> build_query("Total Sales", [("State", "California"), ("Ship Mode", "Standard Class")])
        "SELECT SUM(Sales) FROM SALES WHERE State='California' AND Ship_Mode='Standard Class'"

        >>> build_query("# of Orders", [])
        "SELECT COUNT(DISTINCT Order_ID) FROM SALES"
    """
    # Resolve KPI
    kpi_title_stripped = (kpi_title or "").strip()
    if kpi_title_stripped not in KPI_MAP:
        known = ", ".join(f'"{k}"' for k in sorted(KPI_MAP))
        raise ValueError(
            f"KPI '{kpi_title_stripped}' is not in KPI_MAP.\n"
            f"Known KPIs: {known}\n"
            f"To add it, edit utils/sql_template_engine.py → KPI_MAP."
        )
    aggregation = KPI_MAP[kpi_title_stripped]

    # Resolve each slicer → WHERE clause condition
    conditions: list[str] = []
    for slicer_name, slicer_value in slicers:
        slicer_name_stripped = (slicer_name or "").strip()
        if slicer_name_stripped not in SLICER_MAP:
            known_slicers = ", ".join(f'"{k}"' for k in sorted(SLICER_MAP))
            raise ValueError(
                f"Slicer '{slicer_name_stripped}' is not in SLICER_MAP.\n"
                f"Known slicers: {known_slicers}\n"
                f"To add it, edit utils/sql_template_engine.py → SLICER_MAP."
            )
        db_col = SLICER_MAP[slicer_name_stripped]
        # Escape single quotes in value (basic SQL injection guard for test use)
        safe_val = str(slicer_value).replace("'", "''")
        conditions.append(f"{db_col}='{safe_val}'")

    # Assemble query
    query = f"SELECT {aggregation} FROM {table}"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    log.info(f"Auto-generated SQL for '{kpi_title_stripped}': {query}")
    return query


# ── Item 11: Table & Matrix Visual Query Builder ──────────────────────────────

# Pre-configured table visuals → SELECT expressions (group-by columns + aggregated metrics)
TABLE_COLUMN_MAP: dict[str, list[str]] = {
    "Sales by Region": [
        "Region AS region",
        "SUM(Sales) AS total_sales",
        "SUM(Profit) AS total_profit",
    ],
    "Sales by State": [
        "State AS state",
        "SUM(Sales) AS total_sales",
        "SUM(Profit) AS total_profit",
    ],
    "Sales by Category": [
        "Category AS category",
        "SUM(Sales) AS total_sales",
        "SUM(Profit) AS total_profit",
        "SUM(Quantity) AS total_quantity",
    ],
    "Sales by Sub-Category": [
        "Sub_Category AS sub_category",
        "SUM(Sales) AS total_sales",
        "SUM(Profit) AS total_profit",
    ],
    "Orders by Ship Mode": [
        "Ship_Mode AS ship_mode",
        "COUNT(DISTINCT Order_ID) AS order_count",
        "SUM(Sales) AS total_sales",
    ],
    "Sales by Customer Segment": [
        "Segment AS segment",
        "SUM(Sales) AS total_sales",
        "SUM(Profit) AS total_profit",
    ],
}

# Column alias → SQL aggregation expression for dynamic table queries
_METRIC_COL_MAP: dict[str, str] = {
    "sales":          "SUM(Sales)",
    "total_sales":    "SUM(Sales)",
    "total sales":    "SUM(Sales)",
    "sum of sales":   "SUM(Sales)",
    "profit":         "SUM(Profit)",
    "total_profit":   "SUM(Profit)",
    "total profit":   "SUM(Profit)",
    "sum of profit":  "SUM(Profit)",
    "quantity":       "SUM(Quantity)",
    "total_quantity": "SUM(Quantity)",
    "total quantity": "SUM(Quantity)",
    "sum of quantity":"SUM(Quantity)",
    "orders":         "COUNT(DISTINCT Order_ID)",
    "order_count":    "COUNT(DISTINCT Order_ID)",
    "# of orders":    "COUNT(DISTINCT Order_ID)",
    "products":       "COUNT(DISTINCT Product_ID)",
    "product_count":  "COUNT(DISTINCT Product_ID)",
    "# of products":  "COUNT(DISTINCT Product_ID)",
    "customers":      "COUNT(DISTINCT Customer_ID)",
    "customer_count": "COUNT(DISTINCT Customer_ID)",
    "# of customers": "COUNT(DISTINCT Customer_ID)",
}

# Normalize dimension key → DB column name
_DIM_COL_MAP: dict[str, str] = {
    **{k.lower(): v for k, v in SLICER_MAP.items()},
    **{v.lower(): v for v in SLICER_MAP.values()},
}


def _build_where_conditions(slicers: list[tuple[str, str]]) -> list[str]:
    """Convert a list of (slicer_name, slicer_value) pairs into SQL WHERE conditions."""
    conditions: list[str] = []
    for slicer_name, slicer_value in slicers:
        slicer_name_stripped = (slicer_name or "").strip()
        if slicer_name_stripped not in SLICER_MAP:
            known_slicers = ", ".join(f'"{k}"' for k in sorted(SLICER_MAP))
            raise ValueError(
                f"Slicer '{slicer_name_stripped}' is not in SLICER_MAP.\n"
                f"Known slicers: {known_slicers}\n"
                f"To add it, edit utils/sql_template_engine.py → SLICER_MAP."
            )
        db_col = SLICER_MAP[slicer_name_stripped]
        safe_val = str(slicer_value).replace("'", "''")
        conditions.append(f"{db_col}='{safe_val}'")
    return conditions


def build_table_query(
    visual_name: str,
    slicers: list[tuple[str, str]],
    join_keys: list[str] | None = None,
    compare_cols: list[str] | None = None,
    table: str = SOURCE_TABLE,
) -> str:
    """
    Build a GROUP BY SELECT query for multi-row Table / Matrix visual comparison (Item 11).

    Supports two modes:
      1. Dynamic schema resolution from ``join_keys`` and ``compare_cols`` (when provided in Excel).
      2. Pre-configured lookup from ``TABLE_COLUMN_MAP`` by ``visual_name``.

    Args:
        visual_name:  Exact title of the table/matrix visual on the Power BI report.
        slicers:      List of (slicer_name, slicer_value) tuples applied on the page.
        join_keys:    Dimension columns used to align rows (e.g. ["region"] or ["State"]).
        compare_cols: Numeric metric columns to validate (e.g. ["total_sales", "total_profit"]).
        table:        Source DB table name (default: SOURCE_TABLE = "SALES").

    Returns:
        Complete SQL SELECT ... WHERE ... GROUP BY ... string.
    """
    visual_stripped = (visual_name or "").strip()
    join_keys = [k.strip() for k in (join_keys or []) if k and k.strip()]
    compare_cols = [c.strip() for c in (compare_cols or []) if c and c.strip()]

    select_exprs: list[str] = []
    group_by_cols: list[str] = []

    if join_keys and compare_cols:
        # Mode 1: Dynamic generation from join_keys + compare_cols
        for jk in join_keys:
            db_dim = _DIM_COL_MAP.get(jk.lower(), jk)
            select_exprs.append(f"{db_dim} AS [{jk.lower()}]")
            group_by_cols.append(db_dim)

        for cc in compare_cols:
            cc_lower = cc.lower()
            if cc in KPI_MAP:
                agg_expr = KPI_MAP[cc]
            elif cc_lower in _METRIC_COL_MAP:
                agg_expr = _METRIC_COL_MAP[cc_lower]
            else:
                # Fallback: SUM of the raw column name
                agg_expr = f"SUM({cc})"
            select_exprs.append(f"{agg_expr} AS [{cc_lower}]")

    elif visual_stripped in TABLE_COLUMN_MAP:
        # Mode 2: Pre-configured visual mapping
        select_exprs = list(TABLE_COLUMN_MAP[visual_stripped])
        for expr in select_exprs:
            if "(" not in expr:
                raw_col = expr.split(" AS ")[0].split(" as ")[0].strip()
                group_by_cols.append(raw_col)
    else:
        known_tables = ", ".join(f'"{k}"' for k in sorted(TABLE_COLUMN_MAP))
        raise ValueError(
            f"Table visual '{visual_stripped}' is not in TABLE_COLUMN_MAP and "
            f"Join Keys / Compare Columns were not both specified.\n"
            f"Known table visuals: {known_tables}\n"
            f"Either provide Join Keys + Compare Columns in the Excel sheet, "
            f"or add '{visual_stripped}' to utils/sql_template_engine.py → TABLE_COLUMN_MAP."
        )

    conditions = _build_where_conditions(slicers)

    query = f"SELECT {', '.join(select_exprs)} FROM {table}"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    if group_by_cols:
        query += " GROUP BY " + ", ".join(group_by_cols)

    log.info(f"Auto-generated Table SQL for '{visual_stripped}': {query}")
    return query


# ── Item 12: Tier 2 DAX Query Builders (PBI REST API Fallback) ────────────────

# KPI title → DAX aggregation expression on the semantic model
DAX_KPI_MAP: dict[str, str] = {
    "Total Sales":     f"SUM('{SOURCE_TABLE}'[Sales])",
    "Total Profit":    f"SUM('{SOURCE_TABLE}'[Profit])",
    "Total Quantity":  f"SUM('{SOURCE_TABLE}'[Quantity])",
    "# of Orders":     f"DISTINCTCOUNT('{SOURCE_TABLE}'[Order_ID])",
    "# of Products":   f"DISTINCTCOUNT('{SOURCE_TABLE}'[Product_ID])",
    "# of Customers":  f"DISTINCTCOUNT('{SOURCE_TABLE}'[Customer_ID])",
}


def build_dax_kpi_query(
    kpi_title: str,
    slicers: list[tuple[str, str]],
    table: str = SOURCE_TABLE,
) -> str:
    """
    Build a slicer-aware DAX query for a scalar KPI (Item 12 Tier 2 fallback).

    Example output (with State='California'):
        EVALUATE ROW("Total Sales", CALCULATE(SUM('SALES'[Sales]), 'SALES'[State] = "California"))
    """
    kpi_stripped = (kpi_title or "").strip()
    dax_measure = DAX_KPI_MAP.get(kpi_stripped, f"[{kpi_stripped}]")

    filter_exprs: list[str] = []
    for slicer_name, slicer_value in slicers:
        s_name = (slicer_name or "").strip()
        db_col = SLICER_MAP.get(s_name, s_name)
        safe_val = str(slicer_value).replace('"', '""')
        filter_exprs.append(f"'{table}'[{db_col}] = \"{safe_val}\"")

    if filter_exprs:
        calc_expr = f"CALCULATE({dax_measure}, {', '.join(filter_exprs)})"
    else:
        calc_expr = dax_measure

    dax = f'EVALUATE ROW("{kpi_stripped}", {calc_expr})'
    log.info(f"Auto-generated Tier 2 DAX KPI query: {dax}")
    return dax


def build_dax_table_query(
    visual_name: str,
    slicers: list[tuple[str, str]],
    join_keys: list[str],
    compare_cols: list[str],
    table: str = SOURCE_TABLE,
) -> str:
    """
    Build a slicer-aware DAX SUMMARIZECOLUMNS query for a Table / Matrix visual
    (Item 12 Tier 2 fallback for Item 11 table comparisons).

    Example output:
        EVALUATE SUMMARIZECOLUMNS('SALES'[Region], FILTER('SALES', 'SALES'[State] = "California"), "total_sales", SUM('SALES'[Sales]))
    """
    group_cols: list[str] = []
    for jk in join_keys:
        db_dim = _DIM_COL_MAP.get(jk.strip().lower(), jk.strip())
        group_cols.append(f"'{table}'[{db_dim}]")

    filter_exprs: list[str] = []
    for slicer_name, slicer_value in slicers:
        s_name = (slicer_name or "").strip()
        db_col = SLICER_MAP.get(s_name, s_name)
        safe_val = str(slicer_value).replace('"', '""')
        filter_exprs.append(
            f"FILTER(ALL('{table}'[{db_col}]), '{table}'[{db_col}] = \"{safe_val}\")"
        )

    measure_pairs: list[str] = []
    for cc in compare_cols:
        cc_clean = cc.strip()
        cc_lower = cc_clean.lower()
        if cc_clean in DAX_KPI_MAP:
            dax_agg = DAX_KPI_MAP[cc_clean]
        elif cc_lower in ("sales", "total_sales", "total sales", "sum of sales"):
            dax_agg = f"SUM('{table}'[Sales])"
        elif cc_lower in ("profit", "total_profit", "total profit", "sum of profit"):
            dax_agg = f"SUM('{table}'[Profit])"
        elif cc_lower in ("quantity", "total_quantity", "total quantity", "sum of quantity"):
            dax_agg = f"SUM('{table}'[Quantity])"
        elif cc_lower in ("orders", "order_count", "# of orders"):
            dax_agg = f"DISTINCTCOUNT('{table}'[Order_ID])"
        else:
            dax_agg = f"SUM('{table}'[{cc_clean}])"
        measure_pairs.append(f'"{cc_lower}", {dax_agg}')

    parts = group_cols + filter_exprs + measure_pairs
    dax = f"EVALUATE SUMMARIZECOLUMNS({', '.join(parts)})"
    log.info(f"Auto-generated Tier 2 DAX Table query for '{visual_name}': {dax}")
    return dax

