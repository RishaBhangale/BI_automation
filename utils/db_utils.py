"""
db_utils.py — Source database connectivity and query execution.

Provides a minimal, read-only interface to the source database.
All functions are generic — they do not contain any dashboard-specific
SQL queries. SQL lives in the dashboard YAML config files.

Supported drivers (set ``driver`` in your dashboard YAML source_db section):
    mssql+pymssql   → Azure SQL / SQL Server via pymssql   ACTIVE
                      pip install pymssql
    postgresql      → PostgreSQL via psycopg2
                      pip install psycopg2-binary
    mssql+pyodbc    → SQL Server via system ODBC driver
                      requires Microsoft ODBC Driver for SQL Server
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from utils.logger import get_logger

log = get_logger("db_utils")


def get_db_engine(uri: str) -> Engine:
    """
    Create and return a SQLAlchemy engine for the given URI.

    Args:
        uri: SQLAlchemy connection URI, e.g.
             "mssql+pymssql://user:pass@server:1433/dbname"

    Returns:
        A SQLAlchemy Engine instance.

    Raises:
        OperationalError: If the connection cannot be established.
    """
    if not uri:
        raise ValueError("DB URI is empty — check your dashboard YAML config or settings.py")

    log.info(f"Creating DB engine for: {uri.split('@')[-1]}")  # Log host/db only, no creds
    engine = create_engine(uri, pool_pre_ping=True)
    return engine


def engine_from_config(config: dict) -> Optional[Engine]:
    """
    Build a SQLAlchemy engine directly from a parsed dashboard YAML config dict.

    Reads the ``source_db`` section of the config, builds the URI, and
    returns a ready-to-use engine.  Returns None if DB is not configured.

    Args:
        config: Parsed dashboard config dict (from config_loader.load_dashboard_config).

    Returns:
        SQLAlchemy Engine instance, or None if source_db is not configured.

    Example:
        config = load_dashboard_config("dashboard_configs/agent_detection.yaml")
        engine = engine_from_config(config)
        df = fetch_db_data(engine, "SELECT TOP 5 * FROM dbo.SALES")
    """
    from config.db_config import build_db_uri
    uri = build_db_uri(config)
    if not uri:
        log.warning("source_db not configured in YAML — skipping DB engine creation")
        return None
    return get_db_engine(uri)


def test_connection(engine: Engine) -> bool:
    """
    Verify the database connection is alive by running a trivial query.

    Args:
        engine: SQLAlchemy Engine instance.

    Returns:
        True if connection is OK. False if connection fails.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("DB connection verified successfully")
        return True
    except OperationalError as e:
        log.error(f"DB connection failed: {e}")
        return False


def fetch_db_data(engine: Engine, query: str) -> pd.DataFrame:
    """
    Execute a SQL query and return the results as a pandas DataFrame.

    Args:
        engine: SQLAlchemy Engine instance.
        query:  SQL query string. Should be a SELECT statement.
                For SQL Server use TOP N instead of LIMIT N, e.g.:
                    SELECT TOP 100 * FROM dbo.SALES

    Returns:
        pandas DataFrame containing the query results.
        Returns an empty DataFrame if the query returns no rows.

    Raises:
        SQLAlchemyError: If the query fails to execute.
    """
    log.info(f"Executing DB query: {query[:120].strip()}{'...' if len(query) > 120 else ''}")
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        log.info(f"Query returned {len(df)} rows, {len(df.columns)} columns")
        return df
    except SQLAlchemyError as e:
        log.error(f"DB query failed: {e}")
        raise


def fetch_scalar(engine: Engine, query: str) -> Optional[float]:
    """
    Execute a SQL query that returns a single scalar value (for KPI comparison).

    Args:
        engine: SQLAlchemy Engine instance.
        query:  SQL query that returns exactly one row with one column,
                e.g. "SELECT SUM(Sales) FROM dbo.SALES"

    Returns:
        The scalar value as a float, or None if the result is NULL.

    Raises:
        SQLAlchemyError: If the query fails.
        ValueError:      If the query returns more than one row or column.
    """
    log.info(f"Fetching scalar: {query[:120].strip()}")
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query))
            row = result.fetchone()
            if row is None:
                log.warning("Scalar query returned no rows — returning None")
                return None
            if len(row) > 1:
                raise ValueError(
                    f"Scalar query returned {len(row)} columns — expected exactly 1. "
                    "Use a query like: SELECT SUM(column) FROM table"
                )
            value = row[0]
            log.info(f"Scalar result: {value}")
            return float(value) if value is not None else None
    except SQLAlchemyError as e:
        log.error(f"Scalar query failed: {e}")
        raise
