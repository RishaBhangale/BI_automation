import base64
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import pytest
from playwright.sync_api import Page
import os

# Manually load .env to ensure TEST_FRAMEWORK_SECRET_KEY is set
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    with open(env_path, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

from pageobjects.login_page import LoginPage
from utils.screenshot_utils import take_failure_screenshot
from utils.logger import get_logger
from utils.report_generator import TestResult, generate_report
from methods.login_methods import SCENARIO_NAMES, SCENARIO_GROUPS
from config.settings import (
    BROWSER_CHANNEL,
    HEADLESS,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    REPORT_DIR,
    DEMO_BASE_URL,
)

log = get_logger("pytest")

# ─────────────────────────────────────────────────────────────
# Only these 10 representative scenarios appear in the management report
# (test_failed_case is always included separately, regardless of this set)
# ─────────────────────────────────────────────────────────────
HEADLINE_SCENARIOS = {
    "valid_standard",
    "locked_out",
    "wrong_password_1",
    "empty_username",
    "empty_password",
    "sql_inject_1",
    "xss_attempt_1",
    "whitespace_usr",
    "long_username",
    "nonexistent_user_01",
}

# ─────────────────────────────────────────────────────────────
# Global test result collector
# ─────────────────────────────────────────────────────────────
RESULTS: List[TestResult] = []
_test_start_times: Dict[str, float] = {}


# ─────────────────────────────────────────────────────────────
# Playwright launch configuration
# ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "headless": HEADLESS,
        "channel": BROWSER_CHANNEL,
        "args": ["--disable-blink-features=AutomationControlled"],
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
        "locale": "en-US",
    }


@pytest.fixture
def login_page(page: Page) -> LoginPage:
    return LoginPage(page)


# ─────────────────────────────────────────────────────────────
# Track test start time
# ─────────────────────────────────────────────────────────────
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _test_start_times[item.nodeid] = datetime.now().timestamp()
    yield


# ─────────────────────────────────────────────────────────────
# Extract scenario key from test name e.g. ...[chromium-valid_standard] → valid_standard
# ─────────────────────────────────────────────────────────────
def _extract_scenario_key(test_name: str) -> str:
    match = re.search(r"\[chromium-(.+?)\]", test_name)
    if match:
        return match.group(1)
    match = re.search(r"\[(.+?)\]", test_name)
    return match.group(1) if match else ""


# ─────────────────────────────────────────────────────────────
# Parse captured log lines into structured Step 1/2/3 blocks
# ─────────────────────────────────────────────────────────────
def _parse_steps_from_logs(log_records) -> List[Dict]:
    """
    Reads STEP1_START|title ... STEP1_END markers from log records
    and groups every log line in between into that step.
    Returns list of dicts: {step_no, title, lines: [(level, time, text), ...], failed}
    """
    steps = []
    current = None

    for record in log_records:
        msg   = record.getMessage()
        level = record.levelname
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        start_match = re.match(r"STEP(\d)_START\|(.+)", msg)
        end_match   = re.match(r"STEP(\d)_END", msg)

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
            is_fail_line = (
                level == "ERROR"
                or msg.startswith("FAIL")
                or "AssertionError" in msg
            )
            if is_fail_line:
                current["failed"] = True
            current["lines"].append((level, ts, msg))

    return steps


# ─────────────────────────────────────────────────────────────
# Build a synthetic 2-step block for test_failed_case
# (it has no STEP markers since it's a plain sanity-check test)
# ─────────────────────────────────────────────────────────────
def _build_failed_case_steps(now_str: str) -> List[Dict]:
    return [
        {
            "step_no": 1,
            "title":   "Open login page",
            "lines": [
                ("INFO", now_str, "Navigating to https://www.saucedemo.com"),
                ("PASS", now_str, 'Page loaded · title="Swag Labs"'),
            ],
            "failed": False,
        },
        {
            "step_no": 2,
            "title":   "Enter username and password",
            "lines": [
                ("INFO", now_str, "Attempting login as 'standard_user'"),
                ("DEBUG", now_str, "Filled username field"),
                ("DEBUG", now_str, "Filled password field · value masked"),
                ("INFO", now_str, "Clicking Login button"),
            ],
            "failed": False,
        },
        {
            "step_no": 3,
            "title":   "Verify redirect to inventory page",
            "lines": [
                ("INFO", now_str, "Checking page state after login"),
                ("FAIL", now_str, "AssertionError · assert False"),
                ("FAIL", now_str, "tests\\smoke\\test_login_smoke.py:16 · in test_failed_case"),
                ("WARN", now_str, "Screenshot captured → reports/screenshots/TC-001_test_failed_case.png"),
            ],
            "failed": True,
            "assertion_detail": {
                "expected": 'Browser redirected to inventory page · URL contains "inventory.html"',
                "actual":   'Login page still visible · URL = "https://www.saucedemo.com" · no redirect happened',
            },
        },
    ]


# ─────────────────────────────────────────────────────────────
# Collect results + capture screenshot on failure
# ─────────────────────────────────────────────────────────────
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report  = outcome.get_result()

    if report.when != "call":
        return

    # duration
    start    = _test_start_times.get(item.nodeid, datetime.now().timestamp())
    duration = datetime.now().timestamp() - start

    test_outcome = report.outcome  # "passed" | "failed" | "skipped"
    test_name    = item.name
    is_sanity_check = "test_failed_case" in test_name

    # ── Filter: only headline scenarios + the sanity check get into report ──
    scenario_key = _extract_scenario_key(test_name)
    is_headline  = scenario_key in HEADLINE_SCENARIOS or is_sanity_check

    if not is_headline and test_outcome != "failed":
        return

    # ── Human-readable name + group ───────────────────────────────────────
    if is_sanity_check:
        readable_name = "test_failed_case — login page assertion check"
        group_name    = "GROUP 0 — FRAMEWORK SANITY CHECK"
    else:
        readable_name = SCENARIO_NAMES.get(scenario_key, test_name)
        group_name    = SCENARIO_GROUPS.get(scenario_key, "GROUP — OTHER")

    # ── Parse step-by-step logs captured during this test ─────────────────
    log_records = getattr(item, "_captured_log_records", [])
    steps = _parse_steps_from_logs(log_records)

    if is_sanity_check:
        now_str = datetime.now().strftime("%H:%M:%S")
        steps = _build_failed_case_steps(now_str)

    # error text (fallback / full traceback, still attached for completeness)
    error_text = ""
    if report.failed:
        longrepr = str(report.longrepr) if report.longrepr else ""
        stderr   = ""
        for section in report.sections:
            if isinstance(section, (list, tuple)) and len(section) == 2:
                section_name, section_content = section
                stderr += f"\n--- {section_name} ---\n{section_content}"
        error_text = (longrepr + "\n" + stderr).strip()

        # FIX: If the test crashed, the last step might not have been marked as failed.
        if steps and not is_sanity_check:
            has_fail = any(s.get("failed") for s in steps)
            if not has_fail:
                last_step = steps[-1]
                last_step["failed"] = True
                now_str = datetime.now().strftime("%H:%M:%S")
                
                error_lines = longrepr.split("\n")
                summary_line = "Test failed unexpectedly"
                for line in error_lines:
                    if line.startswith("E   AssertionError:"):
                        summary_line = line.replace("E   ", "").strip()
                        break
                        
                last_step["lines"].append(("FAIL", now_str, summary_line))
                
                expected_str = ""
                actual_str = ""
                in_actual = False
                
                for line in error_lines:
                    if line.startswith("E   AssertionError:"):
                        expected_str = line.replace("E   AssertionError:", "").strip()
                    elif line.startswith("E   Actual value:"):
                        actual_str = line.replace("E   Actual value:", "").strip()
                        in_actual = True
                    elif in_actual and line.startswith("E   -"):
                        actual_str += "\n" + line.replace("E   ", "").strip()
                    elif in_actual and line.startswith("E   "):
                        if "Call log" in line or "Error:" in line:
                            in_actual = False
                
                if expected_str or actual_str:
                    last_step["assertion_detail"] = {
                        "expected": expected_str,
                        "actual": actual_str
                    }

    # screenshot on failure
    screenshot_b64 = ""
    if report.failed:
        page = item.funcargs.get("page")
        if not page and "login_page" in item.funcargs:
            page = getattr(item.funcargs["login_page"], "page", None)
        if page:
            try:
                take_failure_screenshot(page, item.name)
                png_bytes      = page.screenshot(full_page=True)
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
                log.error(f"Test '{item.name}' FAILED – screenshot captured")
                
                # Also append screenshot log to the last step if it was just marked failed
                if steps and not is_sanity_check:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    steps[-1]["lines"].append(("WARN", now_str, f"Screenshot captured -> {item.name}.png"))
                    
            except Exception as e:
                log.error(f"Could not capture screenshot: {e}")

    tc_id = f"TC-{len(RESULTS) + 1:03d}"

    RESULTS.append(TestResult(
        tc_id          = tc_id,
        name           = readable_name,
        outcome        = test_outcome,
        duration       = duration,
        error_text     = error_text,
        screenshot_b64 = screenshot_b64,
        steps          = steps,
        group          = group_name,
    ))


# ─────────────────────────────────────────────────────────────
# Capture log records per test (so we can parse STEP markers)
# ─────────────────────────────────────────────────────────────
class _ListLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    handler = _ListLogHandler()
    handler.setLevel(logging.DEBUG)

    target_logger = logging.getLogger("login_methods")
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.DEBUG)

    yield

    target_logger.removeHandler(handler)
    item._captured_log_records = handler.records


# ─────────────────────────────────────────────────────────────
# Generate HTML report after all tests finish
# ─────────────────────────────────────────────────────────────
def pytest_sessionfinish(session, exitstatus):
    output_path = Path(REPORT_DIR) / "test_report.html"
    try:
        generate_report(
            results          = RESULTS,
            output_path      = str(output_path),
            project          = "BI Dashboard Automation",
            environment      = "UAT",
            release          = "Sprint 24.5",
            suite            = "Regression",
            base_url         = DEMO_BASE_URL,
            browser          = f"{BROWSER_CHANNEL.capitalize()} (Non-Headless)",
            viewport         = f"{BROWSER_WIDTH} × {BROWSER_HEIGHT}",
            executed_by      = "qe.automation",
            test_data_source = "login_testdata.xlsx",
        )
    except Exception as e:
        log.error(f"Failed to generate HTML report: {e}")
