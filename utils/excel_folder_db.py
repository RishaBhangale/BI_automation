"""
excel_folder_db.py

Build a temporary SQLite source database from source files
under the configured Source_data folder.

Supported source file types:
    .xlsx
    .xls
    .csv

Each source file becomes one SQL table using its filename
as the table name.

Examples:
    Source_data/facts.csv
        -> SQL table: facts

    Source_data/Pipeline_dimension.csv
        -> SQL table: Pipeline_dimension

    Source_data/calendar.csv
        -> SQL table: calendar
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _safe_table_name(name: str) -> str:
    """
    Convert a source filename into a safe SQL table name.
    """

    table_name = re.sub(
        r"[^A-Za-z0-9_]",
        "_",
        name.strip(),
    )

    if not table_name:
        raise ValueError(
            f"Invalid table name generated from: {name}"
        )

    if table_name[0].isdigit():
        table_name = f"t_{table_name}"

    return table_name


def get_excel_folder_engine(source_folder: str) -> Engine:
    """
    Load all CSV/Excel files from the configured source folder
    into a temporary SQLite database.

    Supported files:
        .csv
        .xlsx
        .xls
    """

    # -----------------------------------------------------------------------
    # Resolve source folder relative to project root
    # -----------------------------------------------------------------------
    project_root = Path(__file__).resolve().parent.parent

    source_path = Path(source_folder)

    if not source_path.is_absolute():
        source_path = project_root / source_path

    source_path = source_path.resolve()

    # -----------------------------------------------------------------------
    # Validate source folder
    # -----------------------------------------------------------------------
    if not source_path.exists():
        raise FileNotFoundError(
            f"Source data folder not found: {source_path}"
        )

    if not source_path.is_dir():
        raise NotADirectoryError(
            f"Source data path is not a directory: {source_path}"
        )

    # -----------------------------------------------------------------------
    # Find supported source files
    # -----------------------------------------------------------------------
    source_files = sorted(
        [
            file
            for pattern in (
                "*.csv",
                "*.xlsx",
                "*.xls",
            )
            for file in source_path.glob(pattern)
            if not file.name.startswith("~$")
        ],
        key=lambda file: file.name.lower(),
    )

    if not source_files:
        raise FileNotFoundError(
            f"No CSV or Excel source files found in: {source_path}"
        )

    # -----------------------------------------------------------------------
    # Temporary SQLite database
    # -----------------------------------------------------------------------
    cache_dir = project_root / "reports" / "excel_source_cache"
    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    db_path = cache_dir / "pipeline_hygiene_source.sqlite"

    # Remove previous temporary database
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError as exc:
            raise RuntimeError(
                f"Could not replace temporary source database: {db_path}. "
                f"Make sure no previous process is locking the file."
            ) from exc

    engine = create_engine(
        f"sqlite:///{db_path}"
    )

    # -----------------------------------------------------------------------
    # Load every CSV / Excel file
    # -----------------------------------------------------------------------
    for source_file in source_files:

        table_name = _safe_table_name(
            source_file.stem
        )

        try:

            if source_file.suffix.lower() == ".csv":

                df = pd.read_csv(
                    source_file
                )

            else:

                df = pd.read_excel(
                    source_file
                )

        except Exception as exc:

            engine.dispose()

            raise RuntimeError(
                f"Could not read source file: "
                f"{source_file}"
            ) from exc

        # Clean column names
        df.columns = [
            str(column).strip()
            for column in df.columns
        ]

        # -------------------------------------------------------------------
        # Write dataframe into SQLite
        # -------------------------------------------------------------------
        try:

            df.to_sql(
                table_name,
                engine,
                if_exists="replace",
                index=False,
            )

        except Exception as exc:

            engine.dispose()

            raise RuntimeError(
                f"Could not create SQL table "
                f"'{table_name}' from "
                f"'{source_file.name}'."
            ) from exc

        print(
            f"[EXCEL SOURCE] "
            f"{source_file.name} -> {table_name} "
            f"({len(df)} rows, {len(df.columns)} columns)"
        )

    print(
        f"[EXCEL SOURCE] Loaded "
        f"{len(source_files)} source files "
        f"into {db_path}"
    )

    return engine