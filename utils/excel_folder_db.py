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

import gzip
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _is_lfs_pointer(file_path: Path) -> bool:
    """
    Check if a file is an un-downloaded Git LFS pointer stub (< 1KB with LFS header).
    """
    try:
        if not file_path.exists() or file_path.stat().st_size > 1024:
            return False
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline()
        return first_line.startswith("version https://git-lfs.github.com/spec/v1")
    except Exception:
        return False


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
    # Auto-extract compressed .csv.gz if uncompressed is missing or an LFS stub
    # -----------------------------------------------------------------------
    for gz_file in source_path.glob("*.csv.gz"):
        uncompressed = gz_file.with_suffix("")
        if not uncompressed.exists() or _is_lfs_pointer(uncompressed):
            print(f"[EXCEL SOURCE] Extracting {gz_file.name} -> {uncompressed.name}...")
            with gzip.open(gz_file, "rb") as f_in, open(uncompressed, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            print(f"[EXCEL SOURCE] Extracted {uncompressed.name} ({uncompressed.stat().st_size / (1024*1024):.1f} MB)")

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

    folder_slug = re.sub(r"[^A-Za-z0-9_]", "_", source_path.name.strip().lower())
    db_path = cache_dir / f"{folder_slug}_source.sqlite"

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

        # Check if file is an un-downloaded Git LFS pointer
        if source_file.suffix.lower() == ".csv" and _is_lfs_pointer(source_file):
            gz_candidate = source_file.with_name(f"{source_file.name}.gz")
            if gz_candidate.exists():
                print(f"[EXCEL SOURCE] Git LFS pointer detected for {source_file.name}. Extracting from {gz_candidate.name}...")
                with gzip.open(gz_candidate, "rb") as f_in, open(source_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                print(f"[EXCEL SOURCE] Extraction complete: {source_file.name} ({source_file.stat().st_size / (1024*1024):.1f} MB)")
            else:
                try:
                    subprocess.run(["git", "lfs", "pull"], cwd=project_root, capture_output=True, timeout=60)
                except Exception:
                    pass

            if _is_lfs_pointer(source_file):
                engine.dispose()
                raise RuntimeError(
                    f"\n========================================================================================\n"
                    f"[GIT LFS POINTER DETECTED] '{source_file.name}' is a {source_file.stat().st_size}-byte pointer stub.\n"
                    f"GitHub web 'Download ZIP' downloads do NOT bundle Git LFS binary files.\n"
                    f"\n"
                    f"To fix this on your machine:\n"
                    f"  Option 1: If git is installed, run in the repository directory:\n"
                    f"              git lfs install\n"
                    f"              git lfs pull\n"
                    f"  Option 2: Place '{source_file.name}.gz' or the uncompressed '{source_file.name}' into:\n"
                    f"              {source_path}\n"
                    f"========================================================================================\n"
                )

        try:

            if source_file.suffix.lower() == ".csv":

                df = pd.read_csv(
                    source_file,
                    low_memory=False,
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