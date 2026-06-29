"""
test_table_validation.py — Chart and table visual data validation tests.

Tests in this file extract underlying data from Power BI chart/table visuals
using the "Show as a table" feature and compare it against source data.

How it works:
  1. The dashboard_config fixture reads your YAML config file.
  2. For each entry in table_validations[], validate_all_tables() is called.
  3. It right-clicks the visual → "Show as a table" → scrapes the HTML table.
  4. It fetches the equivalent dataset from the DB (SQL) or Excel.
  5. It compares the two DataFrames row-by-row using compare_datasets().

To run:
    pytest tests/dashboard/test_table_validation.py \
        --dashboard-config=dashboard_configs/sample_sales_dashboard.yaml

Notes on "Show as a table":
  • This feature must be enabled on the report by the report author.
    If right-clicking shows no "Show as a table" option, the author
    has disabled it — escalate to have it enabled, or use export instead.
  • For very large datasets, PBI may paginate the table view.
    The current implementation scrapes only the first visible page of rows.
    If your visual has more rows than one screen, the row counts will mismatch.
"""

import pytest

from methods.dashboard_methods import validate_table, validate_all_tables
from utils.config_loader import get_table_validations


@pytest.mark.dashboard
def test_all_tables(dashboard_page, db_engine, dashboard_config):
    """
    Validate ALL table/chart visuals defined in the dashboard YAML config.

    For each table_validations entry in the YAML, this test:
      1. Navigates to the specified page.
      2. Right-clicks the visual and selects "Show as a table".
      3. Scrapes the rendered HTML table.
      4. Runs the equivalent SQL query.
      5. Compares the two datasets row-by-row within the specified tolerance.

    Fails if any validation entry has mismatched rows, row counts, or values.
    """
    results = validate_all_tables(
        dashboard_page=dashboard_page,
        config=dashboard_config,
        db_engine=db_engine,
    )

    if not results:
        pytest.skip("No table validations defined in the dashboard config")

    failures = [r for r in results if not r["passed"]]
    total    = len(results)
    passed   = total - len(failures)

    failure_summary = "\n".join(
        f"  ✗ [{r['page']}] {r['visual_title']}: {r['detail']}"
        for r in failures
    )

    assert not failures, (
        f"Table validation: {passed}/{total} passed.\n"
        f"Failures:\n{failure_summary}"
    )


@pytest.mark.dashboard
def test_table_row_counts(dashboard_page, db_engine, dashboard_config):
    """
    Lighter validation: check only that row counts match for each table visual.

    Faster than test_all_tables because it does not compare cell values —
    only counts rows. Useful as a quick sanity check when running for the
    first time against a new dashboard.
    """
    from methods.dashboard_methods import validate_table
    from utils.validation_utils import compare_row_counts

    tables = get_table_validations(dashboard_config)
    if not tables:
        pytest.skip("No table validations defined in the dashboard config")

    failures = []
    for tbl in tables:
        visual_title = tbl.get("visual_title", "")
        page_name    = tbl.get("page", "")

        try:
            if page_name:
                dashboard_page.switch_to_page(page_name)

            dashboard_data = dashboard_page.extract_table_data(visual_title)
            dashboard_count = len(dashboard_data)

            if db_engine and tbl.get("sql_query"):
                from utils.db_utils import fetch_db_data
                source_df    = fetch_db_data(db_engine, tbl["sql_query"])
                source_count = len(source_df)
            else:
                # Cannot compare without a source — skip row count for this visual
                continue

            passed, detail = compare_row_counts(
                dashboard_count, source_count, label=visual_title
            )
            if not passed:
                failures.append(f"[{page_name}] {visual_title}: {detail}")

        except Exception as e:
            failures.append(f"[{page_name}] {visual_title}: Exception — {e}")

    assert not failures, "Row count mismatches:\n" + "\n".join(failures)
