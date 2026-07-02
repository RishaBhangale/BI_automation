"""
config_loader.py — YAML dashboard config parser.

Loads and validates a dashboard YAML config file (from dashboard_configs/).
Resolves ${ENV_VAR} references in string values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from utils.logger import get_logger

log = get_logger("config_loader")

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """
    Recursively walk a parsed YAML structure and replace ${VAR_NAME}
    references with the corresponding environment variable values.

    Args:
        value: Any Python object (str, dict, list, int, None, etc.)

    Returns:
        The same structure with env var references resolved.
    """
    if isinstance(value, str):
        def replacer(match):
            var_name = match.group(1)
            resolved = os.getenv(var_name, "")
            if not resolved:
                log.warning(f"Environment variable '{var_name}' is not set — using empty string")
            return resolved
        return _ENV_VAR_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_dashboard_config(yaml_path: str) -> dict:
    """
    Load a dashboard YAML config file and return it as a Python dict.
    Environment variable references (${VAR_NAME}) in values are resolved.

    Args:
        yaml_path: Path to the YAML config file, e.g.
                   "dashboard_configs/sample_sales_dashboard.yaml"

    Returns:
        Parsed config dict with env vars resolved.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError:        If the YAML is missing required top-level keys.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Dashboard config not found: {yaml_path}\n"
            f"Available configs: {list(Path('dashboard_configs').glob('*.yaml'))}"
        )

    log.info(f"Loading dashboard config: {yaml_path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Dashboard config is empty: {yaml_path}")

    config = _resolve_env_vars(raw)

    # Validate required top-level keys
    required_keys = ["dashboard"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Dashboard config missing required key: '{key}'")

    if not config["dashboard"].get("url", "").strip():
        log.warning("Dashboard URL is empty in config — tests will fail until it is set")

    log.info(f"Config loaded: {config['dashboard'].get('name', 'Unnamed Dashboard')}")
    _validate_config_schema(config)
    return config

def _validate_config_schema(config: dict) -> None:
    """
    Validate the parsed dashboard YAML config and warn about common issues.

    Does NOT raise hard errors (the framework still runs in extraction-only mode).
    Logs a clear warning for each problem found so QA engineers can fix their YAML.

    Checks:
      • dashboard.url is not empty
      • Each KPI entry has a non-empty visual_title
      • Each KPI entry has sql_query OR (excel_column + excel_agg) — otherwise no comparison will happen
      • Each table entry has a non-empty visual_title
      • Each table entry has join_keys and compare_cols
    """
    warnings_found = 0

    # Check URL
    if not config.get("dashboard", {}).get("url", "").strip():
        log.warning("[CONFIG] dashboard.url is empty — tests will fail when they try to navigate")
        warnings_found += 1

    # Validate KPI entries
    kpis = config.get("kpi_validations") or []
    for i, kpi in enumerate(kpis):
        label = f"kpi_validations[{i}]"

        if not kpi.get("visual_title", "").strip():
            log.warning(
                f"[CONFIG] {label}.visual_title is empty or missing. "
                f"This KPI will be skipped — fill in the exact visual title from the dashboard."
            )
            warnings_found += 1

        has_sql   = bool(kpi.get("sql_query", "").strip())
        has_excel = bool(kpi.get("excel_column", "").strip())
        if not has_sql and not has_excel:
            title = kpi.get('visual_title', f'entry {i}')
            log.warning(
                f"[CONFIG] {label} ('{title}') has no sql_query and no excel_column. "
                f"The KPI value will be EXTRACTED but NOT compared against any source "
                f"(extraction-only mode). Add sql_query or excel_column to enable validation."
            )
            warnings_found += 1

    # Validate table entries
    tables = config.get("table_validations") or []
    for i, tbl in enumerate(tables):
        label = f"table_validations[{i}]"

        if not tbl.get("visual_title", "").strip():
            log.warning(
                f"[CONFIG] {label}.visual_title is empty or missing. "
                f"This table will be skipped — fill in the exact visual title from the dashboard."
            )
            warnings_found += 1

        if not tbl.get("join_keys"):
            title = tbl.get('visual_title', f'entry {i}')
            log.warning(
                f"[CONFIG] {label} ('{title}') has no join_keys. "
                f"Row-by-row comparison requires at least one join key column."
            )
            warnings_found += 1

        if not tbl.get("compare_cols"):
            title = tbl.get('visual_title', f'entry {i}')
            log.warning(
                f"[CONFIG] {label} ('{title}') has no compare_cols. "
                f"No numeric columns will be compared — add at least one column name."
            )
            warnings_found += 1

    if warnings_found == 0:
        log.info("[CONFIG] Validation passed — no issues found in the config")
    else:
        log.warning(
            f"[CONFIG] Validation found {warnings_found} issue(s) in the dashboard config. "
            f"Review the warnings above and update your YAML file."
        )


def get_db_uri_from_config(config: dict) -> str:
    """
    Build and return a SQLAlchemy connection URI from the config's
    ``source_db`` section. Returns empty string if DB is not configured.

    Args:
        config: Parsed dashboard config dict (from load_dashboard_config).

    Returns:
        SQLAlchemy URI string, or empty string if not configured.
    """
    from config.db_config import build_db_uri
    return build_db_uri(config)


def get_kpi_validations(config: dict) -> list[dict]:
    """
    Extract the list of KPI validation entries from the config.

    Returns:
        List of KPI validation dicts. Empty list if none defined.
    """
    return config.get("kpi_validations") or []


def get_table_validations(config: dict) -> list[dict]:
    """
    Extract the list of table/chart validation entries from the config.

    Returns:
        List of table validation dicts. Empty list if none defined.
    """
    return config.get("table_validations") or []


def get_excel_source(config: dict) -> tuple[str, str]:
    """
    Extract the Excel source filepath and sheet name from the config.

    Returns:
        Tuple of (filepath, sheet_name). Both empty strings if not configured.
    """
    src = config.get("source_excel") or {}
    return src.get("filepath", ""), src.get("sheet_name", "")
