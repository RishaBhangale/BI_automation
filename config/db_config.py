"""
db_config.py — SQLAlchemy URI builder.

Builds a database connection URI from either:
  1. A dashboard YAML config dict (per-dashboard override), OR
  2. The global template settings in config/settings.py.

Usage:
    from config.db_config import build_db_uri
    uri = build_db_uri(dashboard_config)   # dashboard_config is the parsed YAML dict
    uri = build_db_uri()                   # falls back to global settings
"""

from __future__ import annotations
from typing import Optional
from config.settings import (
    DB_DRIVER, DB_HOST, DB_PORT, DB_NAME, DB_USER, get_db_password
)


def build_db_uri(dashboard_config: Optional[dict] = None) -> str:
    """
    Construct and return a SQLAlchemy connection URI string.

    Args:
        dashboard_config: Parsed dashboard YAML dict. If provided and contains
                          a non-empty ``source_db`` section, values from the
                          YAML take priority over global settings.

    Returns:
        A SQLAlchemy-compatible URI string, e.g.
        ``"postgresql://user:pass@host:5432/dbname"``.
        Returns an empty string if the driver or host is not configured.

    Notes:
        - If source_db section is empty/missing in the YAML, falls back to
          the global template values in settings.py.
        - Passwords are resolved via get_db_password() which handles
          encrypted tokens.
        - Caller must check for empty string — it means DB is not configured
          and tests should fall back to Excel/CSV source.
    """
    # Attempt to read from dashboard YAML config
    if dashboard_config:
        src = dashboard_config.get("source_db", {}) or {}
        driver = src.get("driver", "").strip()
        host   = src.get("host",   "").strip()
        port   = str(src.get("port", "")).strip()
        dbname = src.get("database", "").strip()
        user   = src.get("username", "").strip()
        # Password in YAML may reference env var: "${DB_PASSWORD}"
        raw_pass = str(src.get("password", "")).strip()
        if raw_pass.startswith("${") and raw_pass.endswith("}"):
            import os
            env_var = raw_pass[2:-1]
            password = os.getenv(env_var, "")
        else:
            password = raw_pass or get_db_password()
    else:
        driver   = DB_DRIVER
        host     = DB_HOST
        port     = str(DB_PORT)
        dbname   = DB_NAME
        user     = DB_USER
        password = get_db_password()

    if not driver or not host:
        return ""  # Not configured — caller must handle gracefully

    if port:
        return f"{driver}://{user}:{password}@{host}:{port}/{dbname}"
    return f"{driver}://{user}:{password}@{host}/{dbname}"
