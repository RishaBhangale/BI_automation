"""
tests/dashboard/conftest.py — Pytest fixtures + report hooks for dashboard validation tests.

FRAMEWORK vs DASHBOARD-SPECIFIC CONFIGURATION

FRAMEWORK:
    • All fixtures defined here
    • Report hook logic
    • Log capture hook

PER-DASHBOARD:
    • YAML config passed via --dashboard-config
    • Dashboard URL / SSO
    • Source DB or Excel-folder configuration
    • KPI / table validation configuration

USAGE:

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
import inspect

from utils.config_loader import (
    load_dashboard_config,
    get_db_uri_from_config,
)

from utils.db_utils import (
    get_db_engine,
    test_connection,
)

# NEW:
# Supports Pipeline Hygiene / Excel-folder source data.
from utils.excel_folder_db import get_excel_folder_engine

from utils.logger import get_logger
from utils.report_generator import TestResult, generate_report

from config.settings import (
    SSO_USERNAME,
    get_sso_password,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    REPORT_DIR,
    PBI_TENANT_ID,
    PBI_CLIENT_ID,
    PBI_CLIENT_SECRET,
)


log = get_logger("dashboard_conftest")


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard report state
# ═══════════════════════════════════════════════════════════════════════════════

DASHBOARD_RESULTS: List[TestResult] = []

_dash_test_start_times: Dict[str, float] = {}

_dash_log_records: Dict[str, list] = {}

_current_dashboard_config: Optional[dict] = None

_validation_ran: bool = False


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
    """

    global _current_dashboard_config

    config_path = request.config.getoption("--dashboard-config")

    if not config_path:
        pytest.fail(
            "Dashboard config not specified.\n"
            "Run: pytest tests/dashboard/ "
            "--dashboard-config=dashboard_configs/_template.yaml"
        )

    _current_dashboard_config = load_dashboard_config(config_path)

    return _current_dashboard_config


@pytest.fixture(scope="session")
def db_engine(dashboard_config: dict):
    """
    Create the source engine required by validation tests.

    Supported source types:

    1. Existing SQL database
       Example:
           source_db:
             driver: "mssql+pymssql"
             host: "..."
             database: "..."
             username: "${DB_USER}"
             password: "${DB_PASSWORD}"

    2. Excel-folder source
       Example:
           source_db:
             type: "excel_folder"
             path: "Source_data"

       Each Excel file under Source_data becomes a SQL table
       in the temporary SQLite source database.

    The existing SQL-database behaviour remains unchanged.
    """

    source_db = dashboard_config.get("source_db") or {}

    # ═══════════════════════════════════════════════════════════════════════
    # NEW: Excel-folder source
    # ═══════════════════════════════════════════════════════════════════════

    if source_db.get("type") == "excel_folder":

        source_path = source_db.get("path")

        if not source_path:
            pytest.fail(
                "source_db.type is 'excel_folder' but source_db.path "
                "is missing from the dashboard YAML."
            )

        engine = None

        try:
            log.info(
                f"Loading Excel source data from: {source_path}"
            )

            engine = get_excel_folder_engine(source_path)

            if not test_connection(engine):
                pytest.fail(
                    "Temporary Excel source database could not be verified."
                )

            log.info(
                "Excel source database loaded successfully."
            )

            yield engine

        finally:
            if engine is not None:
                try:
                    engine.dispose()
                    log.info(
                        "Excel source database engine disposed."
                    )
                except Exception as exc:
                    log.warning(
                        f"Could not dispose Excel source database engine: {exc}"
                    )

        return

    # ═══════════════════════════════════════════════════════════════════════
    # Existing SQL database behaviour
    # ═══════════════════════════════════════════════════════════════════════

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
        log.warning(
            "Could not connect to the source database — "
            "DB-dependent tests will be skipped. "
            "Check your YAML config's source_db section and network access."
        )

        yield None
        return

    yield engine

    engine.dispose()

    log.info("DB engine disposed")


@pytest.fixture(scope="session")
def pbi_client(dashboard_config: dict):
    """
    Create a Power BI API client for Tier 2 DAX-based extraction (optional).

    Activates only when:
        1. dashboard YAML has pbi_api.dataset_id
        2. PBI credentials are configured
        3. API connection test succeeds

    Existing tests that do not reference pbi_client are unaffected.
    """

    api_cfg = dashboard_config.get("pbi_api", {}) or {}

    dataset_id = (api_cfg.get("dataset_id") or "").strip()

    if not dataset_id:
        log.info(
            "PBI REST API (Tier 2) not configured — "
            "pbi_api.dataset_id is empty in the YAML config. Skipping."
        )

        yield None
        return

    if not all(
        [
            PBI_TENANT_ID,
            PBI_CLIENT_ID,
            PBI_CLIENT_SECRET,
        ]
    ):
        missing = [
            name
            for name, value in [
                ("PBI_TENANT_ID", PBI_TENANT_ID),
                ("PBI_CLIENT_ID", PBI_CLIENT_ID),
                ("PBI_CLIENT_SECRET", PBI_CLIENT_SECRET),
            ]
            if not value
        ]

        log.warning(
            "PBI REST API (Tier 2) skipped — "
            f"pbi_api.dataset_id is set but the following credentials "
            f"are missing: {missing}. "
            "Set them via environment variables or config/settings.py."
        )

        yield None
        return

    from utils.pbi_api_client import PBIApiClient

    client = PBIApiClient(
        PBI_TENANT_ID,
        PBI_CLIENT_ID,
        PBI_CLIENT_SECRET,
        dataset_id,
    )

    if client.test_connection():

        log.info(
            f"PBI REST API (Tier 2) ready — "
            f"dataset_id='{dataset_id}'"
        )

        yield client

    else:

        log.warning(
            f"PBI REST API (Tier 2) connection failed — "
            f"dataset_id='{dataset_id}'. "
            "Continuing without Tier 2. "
            "Check credentials and workspace permissions."
        )

        yield None


@pytest.fixture(scope="session")
def dashboard_page(
    browser: Browser,
    dashboard_config: dict,
) ->PBIDashboardPage:
    """
    Open the Power BI dashboard and handle SSO login if required.

    Creates a browser context sized for dashboards.

    Session-scoped: all dashboard tests share this browser context.
    """

    session_file = Path("playwright/.auth/pbi_session.json")
    context_kwargs = {
        "viewport": {
            "width": BROWSER_WIDTH,
            "height": BROWSER_HEIGHT,
        },
        "ignore_https_errors": True,
    }
    if session_file.exists():
        context_kwargs["storage_state"] = str(session_file)

    context = browser.new_context(**context_kwargs)

    page = context.new_page()

    pbi = PBIDashboardPage(page)

    log.info(
        f"PBIDashboardPage loaded from: "
        f"{inspect.getfile(PBIDashboardPage)}"
    )

    log.info(
        f"PBIDashboardPage has open(): "
        f"{hasattr(PBIDashboardPage, 'open')}"
    )

    url = dashboard_config["dashboard"]["url"]

    if not url:
        pytest.fail(
            "Dashboard URL is empty in the config file. "
            "Fill in dashboard.url in the YAML config before running tests."
        )

    # Navigate to dashboard.
    pbi.open(url)
    # Handle SSO login if required.
    username = SSO_USERNAME
    password = get_sso_password()

    if username and password:

        pbi.login_via_sso(
            username,
            password,
        )

    else:

        log.info(
            "SSO credentials not configured — "
            f"assuming report is publicly accessible "
            f"(embed_mode={pbi._embed_mode})"
        )

    log.info(
        f"Dashboard ready: "
        f"{dashboard_config['dashboard'].get('name', 'Unnamed')} "
        f"[{pbi._embed_mode}]"
    )

    try:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(session_file))
    except Exception:
        pass

    yield pbi

    context.close()

    log.info(
        "Dashboard browser context closed"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: track test start times
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):

    if _is_dashboard_test(item):

        _dash_test_start_times[item.nodeid] = (
            datetime.now().timestamp()
        )

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

    # Capture only high-level logs needed for reports.
    target_loggers = [
        logging.getLogger("dashboard_methods"),
        logging.getLogger("validation_utils"),
    ]

    for logger in target_loggers:

        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    yield

    for logger in target_loggers:

        logger.removeHandler(handler)

    _dash_log_records[item.nodeid] = handler.records


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: build TestResult after each dashboard test completes
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):

    global _validation_ran

    outcome = yield

    report = outcome.get_result()

    if report.when != "call":
        return

    if not _is_dashboard_test(item):
        return

    # Mark that a real validation test ran.
    if "discover" not in item.name:
        _validation_ran = True

    start = _dash_test_start_times.get(
        item.nodeid,
        datetime.now().timestamp(),
    )

    duration = (
        datetime.now().timestamp() - start
    )

    test_outcome = report.outcome

    log_records = _dash_log_records.get(
        item.nodeid,
        [],
    )

    steps = _parse_steps_from_logs(
        log_records
    )

    # Dashboard name.
    dash_name = (
        _current_dashboard_config["dashboard"].get(
            "name",
            "Dashboard",
        )
        if _current_dashboard_config
        else "Dashboard"
    )

    # Extract clean Test ID and scenario title.
    raw_name = item.name

    param_match = re.search(
        r'\[(?:chromium-|firefox-|webkit-)?(.*?)\]',
        raw_name,
    )

    if param_match:

        param_str = param_match.group(1).strip()

        if " - " in param_str:

            parts = param_str.split(
                " - ",
                1,
            )

            tc_id = parts[0].strip()

            clean_test_title = parts[1].strip()

        elif param_str.startswith("TC-"):

            tc_id = param_str.strip()

            clean_test_title = (
                item.name
                .split("[")[0]
                .replace("test_", "")
                .replace("_", " ")
                .title()
            )

        else:

            tc_id = (
                f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
            )

            clean_test_title = param_str

    else:

        tc_id = (
            f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
        )

        clean_test_title = (
            item.name
            .replace("test_", "")
            .replace("_", " ")
            .title()
        )

    readable_name = (
        f"{clean_test_title} — {dash_name}"
    )

    # Determine report group.
    if "discover" in item.name:

        group = "GROUP 1 — VISUAL DISCOVERY"

    elif "kpi" in item.name:

        group = "GROUP 2 — KPI VALIDATION"

    elif "table" in item.name:

        group = "GROUP 3 — TABLE VALIDATION"

    elif (
        "biz" in item.name
        or "business" in item.name
        or item.fspath.basename == "test_business_scenarios.py"
    ):

        group = "GROUP 5 — BUSINESS SCENARIOS"

    else:

        group = "GROUP 4 — OTHER DASHBOARD TESTS"

    # Capture error text + mark last step failed.
    error_text = ""

    if report.failed:

        longrepr = (
            str(report.longrepr)
            if report.longrepr
            else ""
        )

        error_text = longrepr.strip()

        if steps:

            last_step = steps[-1]

            if not last_step.get("failed"):

                last_step["failed"] = True

                now_str = datetime.now().strftime(
                    "%H:%M:%S"
                )

                error_lines = longrepr.split(
                    "\n"
                )

                summary_line = "Test failed"

                for line in error_lines:

                    if line.startswith(
                        "E   AssertionError:"
                    ):

                        summary_line = (
                            line
                            .replace(
                                "E   AssertionError:",
                                "",
                            )
                            .strip()
                        )

                        break

                    elif (
                        "assert" in line.lower()
                        and "E " in line
                    ):

                        summary_line = (
                            line
                            .replace(
                                "E   ",
                                "",
                            )
                            .strip()
                        )

                        break

                last_step["lines"].append(
                    (
                        "FAIL",
                        now_str,
                        summary_line,
                    )
                )

    # Screenshot on failure.
    screenshot_b64 = ""

    if report.failed:

        page_obj = item.funcargs.get(
            "dashboard_page"
        )

        if page_obj:

            try:

                png_bytes = (
                    page_obj.page.screenshot(
                        full_page=False
                    )
                )

                screenshot_b64 = (
                    base64.b64encode(
                        png_bytes
                    ).decode("ascii")
                )

            except Exception as exc:

                log.warning(
                    f"Could not capture screenshot: {exc}"
                )

    DASHBOARD_RESULTS.append(
        TestResult(
            tc_id=tc_id,
            name=readable_name,
            outcome=test_outcome,
            duration=duration,
            error_text=error_text,
            screenshot_b64=screenshot_b64,
            steps=steps,
            group=group,
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: generate HTML report at session end
# ═══════════════════════════════════════════════════════════════════════════════


def pytest_sessionfinish(session, exitstatus):

    if not DASHBOARD_RESULTS or not _validation_ran:

        return

    dash_name = (
        _current_dashboard_config["dashboard"].get(
            "name",
            "Dashboard",
        )
        if _current_dashboard_config
        else "Dashboard"
    )

    dash_url = (
        _current_dashboard_config["dashboard"].get(
            "url",
            "",
        )
        if _current_dashboard_config
        else ""
    )

    config_path = (
        session.config.getoption(
            "--dashboard-config"
        )
        or "unknown"
    )

    config_file = Path(
        config_path
    ).name

    import shutil
    import json

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    dash_slug = (
        dash_name or "dashboard"
    ).lower().replace(
        " ",
        "_",
    )[:30]

    report_name = (
        f"dashboard_validation_"
        f"{dash_slug}_"
        f"{timestamp}.html"
    )

    output_path = (
        Path(REPORT_DIR)
        / report_name
    )

    latest_path = (
        Path(REPORT_DIR)
        / "dashboard_validation_latest.html"
    )

    try:

        generate_report(
            results=DASHBOARD_RESULTS,
            output_path=str(output_path),
            project=(
                f"Dashboard Validation — "
                f"{dash_name}"
            ),
            environment="Published Dashboard",
            release="Validation Run",
            suite="Dashboard KPI & Table Validation",
            base_url=dash_url,
            browser=(
                "Chrome (Headed)"
                if session.config.getoption("headed")
                else "Google Chrome"
            ),
            viewport=(
                f"{BROWSER_WIDTH} × "
                f"{BROWSER_HEIGHT}"
            ),
            executed_by="qe.automation",
            test_data_source=config_file,
        )

        # Keep latest report copy.
        shutil.copy2(
            str(output_path),
            str(latest_path),
        )

        log.info(
            f"Dashboard validation report saved → "
            f"{output_path}"
        )

        log.info(
            f"Latest report copy → "
            f"{latest_path}"
        )

        # Export structured JSON results.
        json_results = []

        for result in DASHBOARD_RESULTS:

            dur_mins, dur_secs = divmod(
                int(result.duration),
                60,
            )

            dur_display = (
                f"{dur_mins}m {dur_secs:02d}s"
                if dur_mins > 0
                else f"{int(result.duration)}s"
            )

            json_results.append(
                {
                    "tc_id": result.tc_id,
                    "name": result.name,
                    "status": result.outcome,
                    "duration": dur_display,
                    "duration_secs": round(
                        result.duration,
                        2,
                    ),
                    "error_text": result.error_text,
                    "steps": result.steps,
                    "group": result.group,
                    "screenshot_b64": (
                        result.screenshot_b64
                    ),
                }
            )

        json_latest_path = (
            Path(REPORT_DIR)
            / "dashboard_validation_latest.json"
        )

        json_latest_path.write_text(
            json.dumps(
                json_results,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Mirror JSON + HTML in reports root.
        root_reports = (
            Path(__file__)
            .resolve()
            .parent.parent.parent
            / "reports"
        )

        if root_reports.exists():

            (
                root_reports
                / "dashboard_validation_latest.json"
            ).write_text(
                json.dumps(
                    json_results,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            shutil.copy2(
                str(output_path),
                str(
                    root_reports
                    / "dashboard_validation_latest.html"
                ),
            )

    except Exception as exc:

        log.error(
            f"Failed to generate dashboard HTML report: {exc}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _is_dashboard_test(item) -> bool:
    """Return True if this test item is in the dashboard test suite."""

    return "dashboard" in str(
        item.fspath
    )


def _parse_steps_from_logs(
    log_records: list,
) -> List[dict]:
    """
    Parse STEP marker log lines into structured step dictionaries.

    Looks for:

        STEP1_START|Step title text
        ... log lines ...
        STEP1_END
    """

    steps = []

    current = None

    technical_keywords = [
        "iframe",
        "flat-DOM",
        "PTW",
        "selector failed",
        "trying fallback",
        "trying outer page",
        "DOM diagnostic",
        "reset attempt complete",
        "no clear action taken",
        "Switching to page",
        "Extracting KPI card value for",
        "Visual titles",
        "slicer dropdown expanded",
        "popup items ready",
        "Listbox heights",
        "Listboxes containing items",
        "Active popup",
        "no popup internal search",
        "Slicer item clicked via selector",
        "closed after Escape",
        "did not close via Escape",
        "Force-clicked",
        "JS DOM scan",
        "already at All",
        "no clear option in dropdown",
    ]

    for record in log_records:

        msg = record.getMessage()

        level = record.levelname

        ts = datetime.fromtimestamp(
            record.created
        ).strftime("%H:%M:%S")

        start_match = re.match(
            r"STEP(\d+(?:\.\d+)?|\d+)_START\|(.+)",
            msg,
        )

        end_match = re.match(
            r"STEP(\d+(?:\.\d+)?|\d+)_END",
            msg,
        )

        if start_match:

            current = {
                "step_no": len(steps) + 1,
                "title": start_match.group(2).strip(),
                "lines": [],
                "failed": False,
            }

            steps.append(current)

            continue

        if end_match:

            current = None

            continue

        if current is not None:

            is_fail = (
                level in (
                    "ERROR",
                    "CRITICAL",
                )
                or msg.startswith("FAIL")
                or "AssertionError" in msg
            )

            if is_fail:

                current["failed"] = True

            # Ignore debug logs.
            if level == "DEBUG":
                continue

            # Ignore technical framework traces
            # unless they represent a failure.
            if (
                any(
                    keyword in msg
                    for keyword in technical_keywords
                )
                and not is_fail
            ):
                continue

            current["lines"].append(
                (
                    level,
                    ts,
                    msg,
                )
            )

    return steps