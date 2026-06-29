"""
excel_data_utils.py — Load source-of-truth data from Excel or CSV files.

Used as a fallback when direct database access is not available.
The source Excel/CSV should contain the same raw data that the
Power BI dashboard is visualising.

Dependencies:
    pip install pandas openpyxl
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from utils.logger import get_logger

log = get_logger("excel_data_utils")


def load_source_excel(filepath: str, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """
    Load a source-of-truth Excel file into a pandas DataFrame.

    Args:
        filepath:   Path to the Excel file, relative to the project root.
                    e.g. "testdata/sales_export.xlsx"
        sheet_name: Name of the sheet to load. Defaults to "Sheet1".

    Returns:
        pandas DataFrame with the sheet contents.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Source Excel not found: {filepath}")

    log.info(f"Loading Excel source: {filepath} (sheet='{sheet_name}')")
    df = pd.read_excel(path, sheet_name=sheet_name)
    # Normalize column names: strip whitespace, lowercase
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    log.info(f"Loaded {len(df)} rows, {len(df.columns)} columns from Excel")
    return df


def load_source_csv(filepath: str, encoding: str = "utf-8") -> pd.DataFrame:
    """
    Load a source-of-truth CSV file into a pandas DataFrame.

    Args:
        filepath: Path to the CSV file, relative to the project root.
        encoding: File encoding. Defaults to "utf-8".

    Returns:
        pandas DataFrame with the CSV contents.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Source CSV not found: {filepath}")

    log.info(f"Loading CSV source: {filepath}")
    df = pd.read_csv(path, encoding=encoding)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    log.info(f"Loaded {len(df)} rows, {len(df.columns)} columns from CSV")
    return df


def aggregate_column(
    df: pd.DataFrame,
    column: str,
    agg_func: str = "sum"
) -> Optional[float]:
    """
    Compute a single aggregated value from a DataFrame column.
    Used for KPI validation when using Excel as the source of truth instead of DB.

    Args:
        df:       Source DataFrame (from load_source_excel or load_source_csv).
        column:   Column name to aggregate. Case-insensitive.
        agg_func: Aggregation function — "sum", "count", "avg", "min", "max".

    Returns:
        Aggregated float value, or None if column is not found or result is NaN.

    Raises:
        ValueError: If agg_func is not one of the supported values.
    """
    col = column.strip().lower()
    if col not in df.columns:
        raise KeyError(
            f"Column '{column}' not found in DataFrame. "
            f"Available columns: {list(df.columns)}"
        )

    series = pd.to_numeric(df[col], errors="coerce")
    agg_map = {
        "sum":   series.sum,
        "count": series.count,
        "avg":   series.mean,
        "mean":  series.mean,
        "min":   series.min,
        "max":   series.max,
    }
    if agg_func not in agg_map:
        raise ValueError(
            f"Unsupported agg_func: '{agg_func}'. "
            f"Use one of: {list(agg_map.keys())}"
        )

    result = agg_map[agg_func]()
    log.info(f"Excel agg — column='{column}', func='{agg_func}', result={result}")
    return float(result) if pd.notna(result) else None
