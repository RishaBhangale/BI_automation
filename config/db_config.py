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
from urllib.parse import quote_plus

from config.settings import (
    DB_DRIVER, DB_HOST, DB_PORT, DB_NAME, DB_USER, get_db_password
)


def build_db_uri(dashboard_config: Optional[dict] = None) -> str:
    """
    Construct and return a SQLAlchemy connection URI string.

    Passwords and usernames containing special characters (e.g. @, #, %)
    are URL-encoded automatically to prevent URI parse errors.

    Args:
        dashboard_config: Parsed dashboard YAML dict. If provided and contains
                          a non-empty ``source_db`` section, values from the
                          YAML take priority over global settings.

    Returns:
        A SQLAlchemy-compatible URI string, e.g.
        ``"mssql+pymssql://user:Devdb%402026@host:1433/dbname"``.
        Returns an empty string if the driver or host is not configured.
    """
    # Attempt to read from dashboard YAML config
    if dashboard_config:
        src = dashboard_config.get("source_db", {}) or {}

        def _resolve(val: str, default: str) -> str:
            val = str(val or "").strip()
            if val.startswith("${") and val.endswith("}"):
                import os
                env_var = val[2:-1]
                return os.getenv(env_var, default)
            return val or default

        driver   = _resolve(src.get("driver"), DB_DRIVER)
        host     = _resolve(src.get("host"), DB_HOST)
        port     = _resolve(src.get("port"), str(DB_PORT) if DB_PORT else "")
        dbname   = _resolve(src.get("database"), DB_NAME)
        user     = _resolve(src.get("username"), DB_USER)
        password = _resolve(src.get("password"), get_db_password())
    else:
        driver   = DB_DRIVER
        host     = DB_HOST
        port     = str(DB_PORT) if DB_PORT else ""
        dbname   = DB_NAME
        user     = DB_USER
        password = get_db_password()

    if not driver or not host:
        return ""  # Not configured — caller must handle gracefully

    # URL-encode credentials so special chars (@ # % etc.) don't break the URI
    encoded_user = quote_plus(user)
    encoded_pass = quote_plus(password)

    if port:
        return f"{driver}://{encoded_user}:{encoded_pass}@{host}:{port}/{dbname}"
    return f"{driver}://{encoded_user}:{encoded_pass}@{host}/{dbname}"
