"""
conftest.py — Pytest fixtures for Scenario 1: Qlik ↔ PBI Functional Parity.

Architecture:
  - ONE persistent browser context per pytest session (stored at playwright/.auth/browser_profile).
  - Preserves all SSO cookies, tokens, IndexedDB, and active logins permanently across test runs.
  - In-line SSO auto-prompting: if login is required, tests pause headed for up to 60s for user login.
  - Warm page fixtures: Power BI and Qlik load ONCE per session.
  - Tests reuse the warm pages and only switch tabs or reset slicers (~1-2s per test).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Generator, Dict, List, Optional

import pytest
import yaml
from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.logger import get_logger
from utils.report_generator import TestResult, generate_report
from config.settings import (
    BROWSER_PROFILE_DIR,
    QLIK_SESSION_FILE,
    PBI_SESSION_FILE,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    REPORT_DIR,
)

log = get_logger("scenario1_conftest")

# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard report state (session-level, populated by pytest hooks)
# ═══════════════════════════════════════════════════════════════════════════════
DASHBOARD_RESULTS: List[TestResult] = []
_dash_test_start_times: Dict[str, float] = {}
_dash_log_records: Dict[str, list] = {}
_current_dashboard_config: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────────
# CLI Options
# ─────────────────────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    try:
        parser.addoption(
            "--config",
            action="store",
            default="dashboard_configs/scenario1_track1.yaml",
            help="Path to the scenario YAML config file",
        )
    except ValueError:
        pass

    try:
        parser.addoption(
            "--dashboard-config",
            action="store",
            default=None,
            help="Alias for --config for compatibility with BI_automation UI runner",
        )
    except ValueError:
        pass

    try:
        parser.addoption(
            "--qlik-only",
            action="store_true",
            default=False,
            help="Run only Qlik tests, skipping Power BI interactions",
        )
    except ValueError:
        pass

    try:
        parser.addoption(
            "--pbi-only",
            action="store_true",
            default=False,
            help="Run only Power BI tests, skipping Qlik interactions",
        )
    except ValueError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# YAML Config (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def config(request) -> dict:
    """Load and return the scenario YAML config with smart default resolution."""
    config_opt = None
    try:
        config_opt = request.config.getoption("--config")
    except Exception:
        pass

    try:
        dash_opt = request.config.getoption("--dashboard-config")
        if dash_opt:
            config_opt = dash_opt
    except Exception:
        pass

    if not config_opt:
        config_opt = "dashboard_configs/scenario1_track1.yaml"

    config_path = Path(config_opt)
    if not config_path.exists():
        pytest.fail(
            f"Config file not found: '{config_path}'. "
            "Please check the path. For Track 1, use: --config=dashboard_configs/scenario1_track1.yaml"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    global _current_dashboard_config
    _current_dashboard_config = cfg
    log.info(f"Loaded config: {config_path} (scenario={cfg.get('scenario')}, track={cfg.get('track')})")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Persistent Browser Context (session-scoped — survives the whole test run)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def persistent_browser_context() -> Generator[BrowserContext, None, None]:
    """
    Launch a Chromium persistent context pointing to playwright/.auth/browser_profile.

    Why this is the primary session mechanism:
      - Cookies, auth tokens, session storage, and IndexedDB are saved to disk automatically.
      - Once you log in (Qlik or Power BI), you STAY logged in across test runs.
      - No manual session capture script needed for normal runs.
      - First run will show headed browser with SSO prompt if not already authenticated.
    """
    playwright_ctx = sync_playwright()
    _playwright: Playwright = playwright_ctx.__enter__()

    profile_dir = Path(BROWSER_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Launching persistent browser context from {profile_dir}")
    ctx = _playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=False,
        args=["--start-maximized"],
        no_viewport=True,
    )

    # Only seed cookies from legacy session files if the persistent context has NO cookies yet
    current_cookies = ctx.cookies()
    if not current_cookies:
        for session_file in [QLIK_SESSION_FILE, PBI_SESSION_FILE]:
            sf = Path(session_file)
            if sf.exists():
                try:
                    data = json.loads(sf.read_text(encoding="utf-8"))
                    cookies = data.get("cookies", [])
                    if cookies:
                        ctx.add_cookies(cookies)
                        log.info(f"Seeded {len(cookies)} cookies into empty profile from {sf.name}")
                except Exception as e:
                    log.warning(f"Could not seed cookies from {sf}: {e}")

    yield ctx
    try:
        live_cookies = ctx.cookies()
        if live_cookies:
            Path(PBI_SESSION_FILE).write_text(
                json.dumps({"cookies": live_cookies}, indent=2), encoding="utf-8"
            )
    except Exception:
        pass
    ctx.close()
    playwright_ctx.__exit__(None, None, None)
    log.info("Persistent browser context closed")


# Backward compatibility aliases
@pytest.fixture(scope="session")
def browser_instance(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


@pytest.fixture(scope="session")
def qlik_browser_context(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


@pytest.fixture(scope="session")
def pbi_browser_context(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


# ─────────────────────────────────────────────────────────────────────────────
# Warm Pages (session-scoped — loaded once per test run)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def warm_pbi_page(request, persistent_browser_context, config) -> Page:
    """
    Session-scoped warm Power BI page.
    Navigates to the report URL ONCE at session start.
    All tests share this warm tab, avoiding repeated 15s cold boots.
    """
    if request.config.getoption("--qlik-only", default=False):
        pytest.skip("Power BI tests skipped (--qlik-only active)")

    report_url = config.get("pbi", {}).get("report_url")
    if not report_url:
        pytest.skip("No PBI report URL configured")

    # Find dedicated PBI page or allocate an unclaimed/new tab
    page = None
    for p in persistent_browser_context.pages:
        if getattr(p, "_platform", None) == "pbi":
            page = p
            break
        elif getattr(p, "_platform", None) is None and page is None:
            page = p

    if page is None:
        page = persistent_browser_context.new_page()

    page._platform = "pbi"
    dashboard = PBIDashboardPage(page)
    dashboard.open(report_url)
    return page


@pytest.fixture(scope="session")
def warm_qlik_page(request, persistent_browser_context, config) -> Page:
    """
    Session-scoped warm Qlik Cloud page.
    Navigates to the app/sheet ONCE at session start.
    All tests share this warm tab, avoiding repeated 12s cold boots.
    """
    if request.config.getoption("--pbi-only", default=False):
        pytest.skip("Qlik tests skipped (--pbi-only active)")

    app_id = config.get("qlik", {}).get("app_id")
    sheet_id = config.get("qlik", {}).get("primary_sheet_id")
    if not app_id:
        pytest.skip("No Qlik app_id configured")

    # Find dedicated Qlik page or allocate an unclaimed/new tab
    page = None
    for p in persistent_browser_context.pages:
        if getattr(p, "_platform", None) == "qlik":
            page = p
            break
        elif getattr(p, "_platform", None) is None and page is None:
            page = p

    if page is None:
        page = persistent_browser_context.new_page()

    page._platform = "qlik"
    dashboard = QlikDashboardPage(page)
    dashboard.open(app_id, sheet_id)
    return page


# ─────────────────────────────────────────────────────────────────────────────
# Function-scoped Page & Dashboard fixtures (injected into tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def pbi_dashboard(warm_pbi_page) -> PBIDashboardPage:
    """PBIDashboardPage bound to the warm PBI page."""
    return PBIDashboardPage(warm_pbi_page)


@pytest.fixture(scope="function")
def qlik_dashboard(warm_qlik_page) -> QlikDashboardPage:
    """QlikDashboardPage bound to the warm Qlik page."""
    return QlikDashboardPage(warm_qlik_page)


@pytest.fixture(scope="function")
def pbi_page(warm_pbi_page) -> Page:
    """Direct reference to the warm PBI page."""
    return warm_pbi_page


@pytest.fixture(scope="function")
def qlik_page(warm_qlik_page) -> Page:
    """Direct reference to the warm Qlik page."""
    return warm_qlik_page


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: track test start times
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logstart(nodeid, location):
    _dash_test_start_times[nodeid] = datetime.now().timestamp()


# ═══════════════════════════════════════════════════════════════════════════════
# Log capture hook
# ═══════════════════════════════════════════════════════════════════════════════

class _DashLogHandler(logging.Handler):
    def __init__(self, nodeid: str):
        super().__init__()
        self.nodeid = nodeid
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    handler = _DashLogHandler(item.nodeid)
    handler.setLevel(logging.INFO)
    root_log = logging.getLogger()
    root_log.addHandler(handler)
    item._dash_log_handler = handler
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    yield
    handler = getattr(item, "_dash_log_handler", None)
    if handler:
        _dash_log_records[item.nodeid] = handler.records
        logging.getLogger().removeHandler(handler)


# ═══════════════════════════════════════════════════════════════════════════════
# Result collector hook
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when != "call":
        return

    duration = getattr(report, "duration", 0.0)

    raw_name = item.name
    dash_name = (
        _current_dashboard_config.get("description")
        or _current_dashboard_config.get("name")
        or "Pipeline Hygiene"
        if _current_dashboard_config else "Pipeline Hygiene"
    )

    tc_id = ""
    clean_test_title = ""

    param_match = re.search(r'\[(?:chromium-|firefox-|webkit-)?(.*?)\]', raw_name)
    if param_match:
        param_str = param_match.group(1).strip()
        if " - " in param_str:
            parts = param_str.split(" - ", 1)
            tc_id = parts[0].strip()
            clean_test_title = parts[1].strip()
        elif param_str.startswith("FP-") or param_str.startswith("TC-"):
            tc_id = param_str.strip()
            clean_test_title = item.name.split('[')[0].replace('test_', '').replace('_', ' ').title()
        else:
            tc_id = f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
            clean_test_title = param_str
    elif "test_qlik_sheet_list_is_discoverable" in item.name:
        tc_id = "FP-A-000"
        clean_test_title = "Dynamic Qlik Sheet Discovery"
    elif "test_pbi_page_list_is_discoverable" in item.name:
        tc_id = "FP-A-001"
        clean_test_title = "Dynamic PBI Page Discovery"
    elif "test_sheet_count_parity" in item.name:
        tc_id = "FP-A-002"
        clean_test_title = "Sheet/Page Existence Parity"
    else:
        tc_id = f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
        clean_test_title = item.name.replace('test_', '').replace('_', ' ').title()

    readable_name = f"{clean_test_title} — {dash_name}"

    # Determine group
    if "FP-A" in tc_id:
        group = "CATEGORY A — DISCOVERY & PARITY"
    elif "FP-B" in tc_id:
        group = "CATEGORY B — VISUAL EXISTENCE"
    elif "FP-C" in tc_id:
        group = "CATEGORY C — FILTER / SLICER EXISTENCE"
    elif "FP-D" in tc_id:
        group = "CATEGORY D — FILTER IMPACT PROPAGATION"
    elif "FP-E" in tc_id:
        group = "CATEGORY E — NAVIGATION & BUTTONS"
    elif "FP-F" in tc_id:
        group = "CATEGORY F — CROSS-FILTER BEHAVIOR"
    elif "FP-G" in tc_id:
        group = "CATEGORY G — FILTER CLEAR & STATE ISOLATION"
    else:
        group = "TRACK 1 — FUNCTIONAL PARITY"

    log_records = _dash_log_records.get(item.nodeid, [])
    steps = _parse_steps_from_logs(log_records)

    test_outcome = "passed" if report.passed else ("skipped" if report.skipped else "failed")
    error_text = ""
    screenshot_b64 = None

    if report.failed:
        if report.longrepr:
            error_text = str(report.longrepr)
        if steps:
            steps[-1]["failed"] = True

        page = item.funcargs.get("warm_pbi_page") or item.funcargs.get("warm_qlik_page")
        if page:
            try:
                png_bytes = page.screenshot()
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception as e:
                log.warning(f"Could not capture failure screenshot: {e}")

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
# Session finish: HTML + PDF + JSON report generation
# ═══════════════════════════════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    if not DASHBOARD_RESULTS:
        return

    dash_name = (
        _current_dashboard_config.get("description")
        or _current_dashboard_config.get("name")
        or "Pipeline Hygiene"
        if _current_dashboard_config else "Pipeline Hygiene"
    )
    dash_url = (
        _current_dashboard_config.get("pbi", {}).get("report_url", "")
        if _current_dashboard_config else ""
    )
    config_file = Path(session.config.getoption("--config", default="scenario1_track1.yaml") or "scenario1_track1.yaml").name

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dash_slug   = (dash_name or "dashboard").lower().replace(" ", "_")[:30]
    report_name = f"dashboard_validation_{dash_slug}_{timestamp}.html"

    reports_dir = Path(REPORT_DIR)
    if reports_dir.name.lower() == "html_reports":
        html_reports_dir = reports_dir
    else:
        html_reports_dir = reports_dir / "HTML_reports"
    html_reports_dir.mkdir(parents=True, exist_ok=True)

    output_path = html_reports_dir / report_name
    latest_html = html_reports_dir / "dashboard_validation_latest.html"

    try:
        generate_report(
            results          = DASHBOARD_RESULTS,
            output_path      = str(output_path),
            project          = "Lenovo Qlik ↔ Power BI Migration",
            environment      = "Production / Qlik Cloud & PBI Service",
            release          = "Track 1: Functional Parity",
            suite            = "Qlik Cloud ↔ Power BI Governance Suite",
            base_url         = dash_url or "https://5wnjr4er56khtqe.in.qlikcloud.com",
            browser          = "Google Chrome (Headed)",
            viewport         = f"{BROWSER_WIDTH} × {BROWSER_HEIGHT}",
            executed_by      = "qe.automation",
            test_data_source = config_file,
        )

        # Maintain latest copies
        shutil.copy2(str(output_path), str(latest_html))
        if html_reports_dir.resolve() != reports_dir.resolve():
            shutil.copy2(str(output_path), str(reports_dir / "dashboard_validation_latest.html"))
        log.info(f"Dashboard validation HTML report saved → {output_path}")
        log.info(f"Latest report link                     → {latest_html}")

        # Automatically generate matching PDF report with all test cards expanded
        try:
            pdf_output_path = output_path.with_suffix(".pdf")
            latest_pdf = html_reports_dir / "dashboard_validation_latest.pdf"

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel="chrome")
                pdf_page = browser.new_page(viewport={"width": 1200, "height": 1600})
                pdf_page.goto(f"file://{output_path.resolve()}", wait_until="networkidle")
                # Expand all test cards in the PDF
                pdf_page.evaluate("""() => {
                    document.querySelectorAll('details.tc').forEach(d => d.setAttribute('open', ''));
                }""")
                pdf_page.wait_for_timeout(400)
                pdf_page.pdf(
                    path=str(pdf_output_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
                )
                browser.close()

            shutil.copy2(str(pdf_output_path), str(latest_pdf))
            if html_reports_dir.resolve() != reports_dir.resolve():
                shutil.copy2(str(pdf_output_path), str(reports_dir / "dashboard_validation_latest.pdf"))
            log.info(f"Dashboard validation PDF report saved  → {pdf_output_path}")
            log.info(f"Latest PDF report link                 → {latest_pdf}")
        except Exception as pdf_err:
            log.warning(f"Could not generate automated PDF report: {pdf_err}")

        # Export structured JSON results
        json_results = []
        for r in DASHBOARD_RESULTS:
            dur_mins, dur_secs = divmod(int(r.duration), 60)
            dur_display = f"{dur_mins}m {dur_secs:02d}s" if dur_mins > 0 else f"{int(r.duration)}s"
            json_results.append({
                "tc_id": r.tc_id,
                "name": r.name,
                "status": r.outcome,
                "duration": dur_display,
                "duration_secs": round(r.duration, 2),
                "error_text": r.error_text,
                "steps": r.steps,
                "group": r.group,
                "screenshot_b64": r.screenshot_b64,
            })

        latest_json = html_reports_dir / "dashboard_validation_latest.json"
        json_data = json.dumps(json_results, indent=2, ensure_ascii=False)
        latest_json.write_text(json_data, encoding="utf-8")
        if html_reports_dir.resolve() != reports_dir.resolve():
            (reports_dir / "dashboard_validation_latest.json").write_text(json_data, encoding="utf-8")
        log.info(f"JSON execution results exported        → {latest_json}")

    except Exception as e:
        log.error(f"Failed to generate dashboard HTML report: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: parse STEP markers from logs
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_steps_from_logs(log_records: list) -> List[dict]:
    steps = []
    current = None

    for record in log_records:
        msg = record.getMessage()
        level = record.levelname
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        start_match = re.match(r"STEP(\d+(?:\.\d+)?|\d+)_START\|(.+)", msg)
        end_match = re.match(r"STEP(\d+(?:\.\d+)?|\d+)_END", msg)

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
                level in ("ERROR", "CRITICAL")
                or msg.startswith("FAIL")
                or "AssertionError" in msg
            )
            if is_fail:
                current["failed"] = True

            if level == "DEBUG":
                continue

            current["lines"].append((level, ts, msg))

    return steps
