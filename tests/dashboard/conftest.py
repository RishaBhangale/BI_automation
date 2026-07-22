"""
tests/dashboard/conftest.py — Pytest fixtures + report hooks for dashboard validation tests.

═══════════════════════════════════════════════════════════════════════════════
FRAMEWORK vs DASHBOARD-SPECIFIC CONFIGURATION
═══════════════════════════════════════════════════════════════════════════════

FRAMEWORK (unchanged across dashboards):
  • All fixtures defined here (dashboard_page, db_engine, dashboard_config)
  • Report hook logic (pytest_runtest_makereport, pytest_sessionfinish)
  • Log capture hook (pytest_runtest_call)

PER-DASHBOARD (changes for each new dashboard):
  • The YAML config file passed via --dashboard-config=...
  • Dashboard URL, SSO credentials (in config/settings.py or .env)
  • DB connection string (in the YAML source_db section)
  • KPI / table validation entries (in the YAML kpi_validations section)

═══════════════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════════════
    pytest tests/dashboard/ \
        --dashboard-config=dashboard_configs/my_dashboard.yaml
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pytest
from playwright.sync_api import Browser

from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.config_loader import load_dashboard_config, get_db_uri_from_config
from utils.db_utils import get_db_engine, test_connection
from utils.logger import get_logger
from utils.report_generator import TestResult, generate_report
from config.settings import (
    SSO_USERNAME, get_sso_password,
    BROWSER_WIDTH, BROWSER_HEIGHT,
    REPORT_DIR,
    PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET,
)

log = get_logger("dashboard_conftest")


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard report state (session-level, populated by hooks below)
# ═══════════════════════════════════════════════════════════════════════════════

DASHBOARD_RESULTS:       List[TestResult] = []
_dash_test_start_times:  Dict[str, float] = {}
_dash_log_records:       Dict[str, list]  = {}
_current_dashboard_config: Optional[dict] = None   # set by dashboard_config fixture
_validation_ran: bool = False  # True only when a non-discovery validation test runs


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Option
# ═══════════════════════════════════════════════════════════════════════════════

def pytest_addoption(parser):
    """Register --dashboard-config CLI option."""
    parser.addoption(
        "--dashboard-config",
        action="store",
        default=None,
        help=(
            "Path to the dashboard YAML config file. "
            "Example: dashboard_configs/public_sales_dashboard.yaml"
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def dashboard_config(request) -> dict:
    """
    Load and return the parsed dashboard YAML config.
    Requires --dashboard-config CLI argument.

    FRAMEWORK FIXTURE — do not modify.
    PER-DASHBOARD — pass a different YAML file via --dashboard-config.
    """
    global _current_dashboard_config
    config_path = request.config.getoption("--dashboard-config")
    if not config_path:
        pytest.fail(
            "Dashboard config not specified.\n"
            "Run: pytest tests/dashboard/ --dashboard-config=dashboard_configs/_template.yaml"
        )
    _current_dashboard_config = load_dashboard_config(config_path)
    return _current_dashboard_config


@pytest.fixture(scope="session")
def db_engine(dashboard_config: dict):
    """
    Create a SQLAlchemy DB engine from the dashboard config.

    Yields:
        SQLAlchemy Engine if source_db is configured.
        None if no database connection is specified (uses Excel fallback or skip).

    FRAMEWORK FIXTURE — do not modify.
    PER-DASHBOARD — set source_db fields in the YAML config.
    """
    uri = get_db_uri_from_config(dashboard_config)

    if not uri:
        log.warning(
            "No database configured in dashboard config — "
            "tests relying on DB source will use Excel fallback or skip."
        )
        yield None
        return

    engine = get_db_engine(uri)
    if not test_connection(engine):
        pytest.fail(
            "Could not connect to the source database. "
            "Check your YAML config's source_db section and network access."
        )

    yield engine
    engine.dispose()
    log.info("DB engine disposed")


@pytest.fixture(scope="session")
def pbi_client(dashboard_config: dict):
    """
    Create a Power BI API client for Tier 2 DAX-based extraction (optional).

    Activates ONLY when ALL of the following are true:
      1. The dashboard YAML config has a non-empty pbi_api.dataset_id.
      2. PBI_TENANT_ID, PBI_CLIENT_ID, and PBI_CLIENT_SECRET are set in
         environment variables or config/settings.py.
      3. The API connection test passes (authenticate + trivial DAX query).

    If any condition is missing, returns None silently.
    Existing tests that do not reference pbi_client are unaffected.

    FRAMEWORK FIXTURE — do not modify.
    PER-DASHBOARD — set pbi_api.dataset_id in the YAML config.
                    Set PBI_TENANT_ID / PBI_CLIENT_ID / PBI_CLIENT_SECRET via env vars.
    """
    api_cfg    = dashboard_config.get("pbi_api", {}) or {}
    dataset_id = (api_cfg.get("dataset_id") or "").strip()

    if not dataset_id:
        log.info(
            "PBI REST API (Tier 2) not configured — "
            "pbi_api.dataset_id is empty in the YAML config. Skipping."
        )
        yield None
        return

    if not all([PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET]):
        missing = [
            name for name, val in [
                ("PBI_TENANT_ID", PBI_TENANT_ID),
                ("PBI_CLIENT_ID", PBI_CLIENT_ID),
                ("PBI_CLIENT_SECRET", PBI_CLIENT_SECRET),
            ] if not val
        ]
        log.warning(
            f"PBI REST API (Tier 2) skipped — pbi_api.dataset_id is set but "
            f"the following credentials are missing: {missing}. "
            f"Set them via environment variables or config/settings.py."
        )
        yield None
        return

    from utils.pbi_api_client import PBIApiClient
    client = PBIApiClient(PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET, dataset_id)

    if client.test_connection():
        log.info(
            f"PBI REST API (Tier 2) ready — dataset_id='{dataset_id}'"
        )
        yield client
    else:
        log.warning(
            f"PBI REST API (Tier 2) connection failed — dataset_id='{dataset_id}'. "
            "Continuing without Tier 2. Check credentials and workspace permissions."
        )
        yield None




@pytest.fixture(scope="session")
def dashboard_page(browser: Browser, dashboard_config: dict) -> PBIDashboardPage:
    """
    Open the Power BI dashboard and handle SSO login if required.

    Creates a browser context sized for dashboards (1600x900).
    Session-scoped: all dashboard tests share this browser context.

    FRAMEWORK FIXTURE — do not modify.
    PER-DASHBOARD — set dashboard.url and SSO credentials in config/settings.py.
    """
    context = browser.new_context(
        viewport={"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
        ignore_https_errors=True,
    )
    page = context.new_page()
    pbi  = PBIDashboardPage(page)
    url  = dashboard_config["dashboard"]["url"]

    if not url:
        pytest.fail(
            "Dashboard URL is empty in the config file. "
            "Fill in dashboard.url in your YAML config before running tests."
        )

    # Navigate to the dashboard (embed mode is auto-detected from URL)
    pbi.open(url)

    # Handle SSO login if required (publish-to-web skips this automatically)
    username = SSO_USERNAME
    password = get_sso_password()

    if username and password:
        pbi.login_via_sso(username, password)
    else:
        log.info(
            f"SSO credentials not configured — "
            f"assuming report is publicly accessible (embed_mode={pbi._embed_mode})"
        )

    log.info(
        f"Dashboard ready: {dashboard_config['dashboard'].get('name', 'Unnamed')} "
        f"[{pbi._embed_mode}]"
    )
    yield pbi

    context.close()
    log.info("Dashboard browser context closed")


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: track test start times
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _is_dashboard_test(item):
        _dash_test_start_times[item.nodeid] = datetime.now().timestamp()
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: capture log records during each dashboard test
# ═══════════════════════════════════════════════════════════════════════════════

class _DashListLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not _is_dashboard_test(item):
        yield
        return

    handler = _DashListLogHandler()
    handler.setLevel(logging.DEBUG)

    # Capture these loggers — they emit the STEP markers and validation details
    target_loggers = [
        logging.getLogger("dashboard_methods"),
        logging.getLogger("pbi_dashboard_page"),
        logging.getLogger("validation_utils"),
    ]
    for lg in target_loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)

    yield

    for lg in target_loggers:
        lg.removeHandler(handler)

    _dash_log_records[item.nodeid] = handler.records


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: build TestResult after each dashboard test completes
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _validation_ran
    outcome = yield
    report  = outcome.get_result()

    if report.when != "call":
        return
    if not _is_dashboard_test(item):
        return

    # Mark that a real validation test ran (not just discovery)
    if "discover" not in item.name:
        _validation_ran = True

    start    = _dash_test_start_times.get(item.nodeid, datetime.now().timestamp())
    duration = datetime.now().timestamp() - start

    test_outcome = report.outcome          # "passed" | "failed" | "skipped"
    log_records  = _dash_log_records.get(item.nodeid, [])
    steps        = _parse_steps_from_logs(log_records)

    # Human-readable name from YAML config name
    dash_name = (
        _current_dashboard_config["dashboard"].get("name", "Dashboard")
        if _current_dashboard_config else "Dashboard"
    )
    readable_name = f"{item.name.replace('test_', '').replace('_', ' ').title()} — {dash_name}"

    # Determine group from test name
    if "discover" in item.name:
        group = "GROUP 1 — VISUAL DISCOVERY"
    elif "kpi" in item.name:
        group = "GROUP 2 — KPI VALIDATION"
    elif "table" in item.name:
        group = "GROUP 3 — TABLE VALIDATION"
    else:
        group = "GROUP 4 — OTHER DASHBOARD TESTS"

    # Capture error text + mark last step failed
    error_text = ""
    if report.failed:
        longrepr = str(report.longrepr) if report.longrepr else ""
        error_text = longrepr.strip()

        if steps:
            last_step = steps[-1]
            if not last_step.get("failed"):
                last_step["failed"] = True
                now_str = datetime.now().strftime("%H:%M:%S")
                error_lines = longrepr.split("\n")
                summary_line = "Test failed"
                for line in error_lines:
                    if line.startswith("E   AssertionError:"):
                        summary_line = line.replace("E   AssertionError:", "").strip()
                        break
                    elif "assert" in line.lower() and "E " in line:
                        summary_line = line.replace("E   ", "").strip()
                        break
                last_step["lines"].append(("FAIL", now_str, summary_line))

    # Screenshot on failure
    screenshot_b64 = ""
    if report.failed:
        page_obj = item.funcargs.get("dashboard_page")
        if page_obj:
            try:
                png_bytes      = page_obj.page.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception as e:
                log.warning(f"Could not capture screenshot: {e}")

    tc_id = f"DTC-{len(DASHBOARD_RESULTS) + 1:03d}"
    DASHBOARD_RESULTS.append(TestResult(
        tc_id          = tc_id,
        name           = readable_name,
        outcome        = test_outcome,
        duration       = duration,
        error_text     = error_text,
        screenshot_b64 = screenshot_b64,
        steps          = steps,
        group          = group,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: generate HTML report at session end
# ═══════════════════════════════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    if not DASHBOARD_RESULTS or not _validation_ran:
        return  # No validation tests ran — skip report generation

    dash_name = (
        _current_dashboard_config["dashboard"].get("name", "Dashboard")
        if _current_dashboard_config else "Dashboard"
    )
    dash_url = (
        _current_dashboard_config["dashboard"].get("url", "")
        if _current_dashboard_config else ""
    )
    config_path = session.config.getoption("--dashboard-config") or "unknown"
    config_file = Path(config_path).name

    from datetime import datetime
    import shutil

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dash_slug   = (dash_name or "dashboard").lower().replace(" ", "_")[:30]
    report_name = f"dashboard_validation_{dash_slug}_{timestamp}.html"
    output_path = Path(REPORT_DIR) / report_name
    latest_path = Path(REPORT_DIR) / "dashboard_validation_latest.html"

    try:
        generate_report(
            results          = DASHBOARD_RESULTS,
            output_path      = str(output_path),
            project          = f"Dashboard Validation — {dash_name}",
            environment      = "Published Dashboard",
            release          = "Validation Run",
            suite            = "Dashboard KPI & Table Validation",
            base_url         = dash_url,
            browser          = "Chrome (Headless)",
            viewport         = f"{BROWSER_WIDTH} × {BROWSER_HEIGHT}",
            executed_by      = "qe.automation",
            test_data_source = config_file,
        )
        # Also keep a "latest" copy for quick access
        shutil.copy2(str(output_path), str(latest_path))
        log.info(f"Dashboard validation report saved → {output_path}")
        log.info(f"Latest report symlink       → {latest_path}")
    except Exception as e:
        log.error(f"Failed to generate dashboard HTML report: {e}")



# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_dashboard_test(item) -> bool:
    """Return True if this test item is in the dashboard test suite."""
    return "dashboard" in str(item.fspath)


def _parse_steps_from_logs(log_records: list) -> List[dict]:
    """
    Parse STEP marker log lines into structured step dicts.

    Looks for:
        STEP1_START|Step title text
        ... log lines ...
        STEP1_END

    Returns list of:
        {step_no, title, lines: [(level, time, text)], failed}
    """
    steps   = []
    current = None

    for record in log_records:
        msg   = record.getMessage()
        level = record.levelname
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        start_match = re.match(r"STEP(\d)_START\|(.+)", msg)
        end_match   = re.match(r"STEP(\d)_END",         msg)

        if start_match:
            current = {
                "step_no": int(start_match.group(1)),
                "title":   start_match.group(2),
                "lines":   [],
                "failed":  False,
            }
            steps.append(current)
            continue

        if end_match:
            current = None
            continue

        if current is not None:
            is_fail = (
                level == "ERROR"
                or msg.startswith("FAIL")
                or "AssertionError" in msg
            )
            if is_fail:
                current["failed"] = True
            current["lines"].append((level, ts, msg))

    return steps
