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
