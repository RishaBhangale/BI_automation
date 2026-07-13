"""
validation_utils.py — Data comparison and assertion engine.

This module contains all the logic for comparing values extracted from
Power BI dashboard visuals against values from the source database or Excel.

Key challenge: Power BI displays formatted strings like "$4.2M", "1,234",
"87.5%", "(1,234)" etc. The source DB returns raw floats/ints.
The parse_pbi_number() function bridges this gap.
"""

from __future__ import annotations

import re
from typing import Optional, Union

import pandas as pd
from pandas.testing import assert_frame_equal

from utils.logger import get_logger

log = get_logger("validation_utils")


# ── Number Parsing ─────────────────────────────────────────────────────────

def parse_pbi_number(raw: str) -> Optional[float]:
    """
    Parse a Power BI formatted display value into a Python float.

    Handles all common Power BI number formatting patterns:
      • Currency symbols:    "$4.2M"   → 4_200_000.0
      • K suffix:            "1.5K"    → 1_500.0
      • M suffix:            "3.2M"    → 3_200_000.0
      • B suffix:            "2.1B"    → 2_100_000_000.0
      • Percentage:          "87.5%"   → 87.5
      • Comma separators:    "1,234,567" → 1_234_567.0
      • Negative parens:     "(1,234)" → -1_234.0
      • Negative dash:       "-1,234"  → -1_234.0
      • Plain integer:       "12345"   → 12_345.0
      • Empty / dash:        ""  "–"   → None

    Args:
        raw: The raw string value as displayed in the Power BI visual.

    Returns:
        Parsed float value, or None if the string is empty, a dash, or
        cannot be parsed.
    """
    if not raw:
        return None

    cleaned = raw.strip()

    # Handle empty / dash / N/A displays
    if cleaned in ("", "–", "-", "N/A", "n/a", "null", "Null"):
        return None

    # Detect negative in parentheses format: (1,234) → -1234
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
        negative = True

    # Strip currency symbols and spaces: $, £, €, ₹, ¥
    cleaned = re.sub(r"[£$€₹¥\s]", "", cleaned)

    # Strip trailing trend/arrow symbols that Power BI appends to percentage KPIs:
    # e.g. "620.4% ▲" → "620.4%", "45% ▼" → "45%"
    cleaned = re.sub(r"[\s▲▼↑↓⇗⇘⇑⇓→←⟶]+$", "", cleaned).strip()


    # Strip percentage sign — keep the numeric value as-is (87.5% → 87.5)
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1]

    # Strip commas used as thousands separators
    cleaned = cleaned.replace(",", "")

    # Apply K / M / B multipliers
    multiplier = 1.0
    if cleaned.endswith("K") or cleaned.endswith("k"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M") or cleaned.endswith("m"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("B") or cleaned.endswith("b"):
        multiplier = 1_000_000_000.0
        cleaned = cleaned[:-1]

    try:
        value = float(cleaned) * multiplier
    except ValueError:
        log.warning(f"Could not parse Power BI value: '{raw}' — returning None")
        return None

    if negative:
        value = -value

    log.debug(f"parse_pbi_number('{raw}') → {value}")
    return value


# ── Single Value Comparison ────────────────────────────────────────────────

def compare_single_value(
    dashboard_raw: str,
    source_value: Optional[float],
    tolerance: float = 0.01,
    label: str = ""
) -> tuple[bool, str]:
    """
    Compare a KPI card value from the dashboard against a scalar from the source.

    Args:
        dashboard_raw:  Raw string value from the Power BI KPI card visual.
        source_value:   Numeric value from the DB or Excel source.
        tolerance:      Acceptable relative difference (0.01 = 1%).
        label:          Optional label for logging (e.g., visual title).

    Returns:
        Tuple of (passed: bool, detail: str).
        - passed: True if values match within tolerance.
        - detail: Human-readable explanation of the comparison result.
    """
    prefix = f"[{label}] " if label else ""

    dashboard_value = parse_pbi_number(dashboard_raw)

    if dashboard_value is None and source_value is None:
        return True, f"{prefix}Both values are null/empty — considered matching"

    if dashboard_value is None:
        return False, f"{prefix}Dashboard shows null/empty, source has {source_value}"

    if source_value is None:
        return False, f"{prefix}Source is null, dashboard shows {dashboard_raw} ({dashboard_value})"

    # Relative tolerance check: |a - b| / max(|a|, |b|, 1) <= tolerance
    denominator = max(abs(source_value), abs(dashboard_value), 1.0)
    relative_diff = abs(dashboard_value - source_value) / denominator

    passed = relative_diff <= tolerance
    detail = (
        f"{prefix}Dashboard={dashboard_raw} (parsed={dashboard_value:,.4f}), "
        f"Source={source_value:,.4f}, "
        f"Diff={relative_diff:.4%}, "
        f"Tolerance={tolerance:.2%} → {'PASS' if passed else 'FAIL'}"
    )
    log.info(detail)
    return passed, detail


# ── Dataset Comparison ─────────────────────────────────────────────────────

def compare_datasets(
    dashboard_data: list[dict],
    source_df: pd.DataFrame,
    join_keys: list[str],
    compare_cols: list[str],
    tolerance: float = 0.01
) -> tuple[bool, str]:
    """
    Compare tabular data extracted from a dashboard visual against source data.

    Process:
      1. Convert dashboard_data (list of dicts) to a DataFrame.
      2. Normalize column names to lowercase in both DataFrames.
      3. Parse numeric values in the dashboard DataFrame using parse_pbi_number().
      4. Sort both DataFrames by join_keys.
      5. Assert row counts match.
      6. Assert each compare_col matches within tolerance.

    Args:
        dashboard_data: List of dicts from PBIDashboardPage.extract_table_data().
                        All values are strings as scraped from the HTML.
        source_df:      DataFrame from db_utils.fetch_db_data() or excel_data_utils.
        join_keys:      Column(s) to align the two datasets on (e.g., ["region"]).
        compare_cols:   Numeric column(s) to compare (e.g., ["total_sales"]).
        tolerance:      Acceptable relative difference per cell (0.01 = 1%).

    Returns:
        Tuple of (passed: bool, detail: str).
    """
    if not dashboard_data:
        return False, "Dashboard returned no rows — visual may have rendered empty"

    # Step 1: Build DataFrame from dashboard scraped data
    dash_df = pd.DataFrame(dashboard_data)

    # Step 2: Normalize column names to lowercase
    dash_df.columns  = [c.strip().lower() for c in dash_df.columns]
    source_df        = source_df.copy()
    source_df.columns = [c.strip().lower() for c in source_df.columns]

    lower_keys = [k.lower() for k in join_keys]
    lower_cols = [c.lower() for c in compare_cols]

    # Check all required columns exist
    for col in lower_keys + lower_cols:
        if col not in dash_df.columns:
            available = list(dash_df.columns)
            return False, f"Column '{col}' not found in dashboard data. Available: {available}"
        if col not in source_df.columns:
            available = list(source_df.columns)
            return False, f"Column '{col}' not found in source data. Available: {available}"

    # Step 3: Parse numeric strings in dashboard compare_cols
    for col in lower_cols:
        dash_df[col] = dash_df[col].apply(
            lambda x: parse_pbi_number(str(x)) if pd.notna(x) else None
        )
        source_df[col] = pd.to_numeric(source_df[col], errors="coerce")

    # Step 4: Sort both by join keys
    dash_df   = dash_df.sort_values(by=lower_keys).reset_index(drop=True)
    source_df = source_df.sort_values(by=lower_keys).reset_index(drop=True)

    # Step 5: Row count check
    if len(dash_df) != len(source_df):
        return False, (
            f"Row count mismatch — Dashboard: {len(dash_df)}, Source: {len(source_df)}"
        )

    # Step 6: Per-column comparison with tolerance
    failures = []
    for col in lower_cols:
        for i, (d_val, s_val) in enumerate(zip(dash_df[col], source_df[col])):
            key_vals = {k: dash_df.iloc[i][k] for k in lower_keys}
            passed, detail = compare_single_value(
                str(d_val) if d_val is not None else "",
                float(s_val) if s_val is not None else None,
                tolerance=tolerance,
                label=f"row {i + 1} {key_vals} col={col}"
            )
            if not passed:
                failures.append(detail)

    if failures:
        return False, f"{len(failures)} cell(s) failed:\n" + "\n".join(failures)

    summary = (
        f"All {len(dash_df)} rows × {len(lower_cols)} column(s) matched "
        f"within {tolerance:.2%} tolerance — PASS"
    )
    log.info(summary)
    return True, summary


# ── Row Count Comparison ───────────────────────────────────────────────────

def compare_row_counts(
    dashboard_count: int,
    source_count: int,
    label: str = ""
) -> tuple[bool, str]:
    """
    Assert that the number of rows in the dashboard visual matches the source.

    Args:
        dashboard_count: Number of rows scraped from the visual.
        source_count:    Number of rows in the source DB/Excel result.
        label:           Optional label for the assertion message.

    Returns:
        Tuple of (passed: bool, detail: str).
    """
    prefix = f"[{label}] " if label else ""
    passed = dashboard_count == source_count
    detail = (
        f"{prefix}Row count — Dashboard: {dashboard_count}, "
        f"Source: {source_count} → {'PASS' if passed else 'FAIL'}"
    )
    log.info(detail)
    return passed, detail
