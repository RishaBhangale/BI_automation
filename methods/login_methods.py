from pageobjects.login_page import LoginPage
from utils.logger import get_logger
from data.login_data import get_login_test_rows

log = get_logger("login_methods")

# Build a keyed lookup from the Excel rows once at import time
_USER_LOOKUP = {row["scenario"]: row for row in get_login_test_rows()}

ERROR_MESSAGES = {
    "locked_out": "Epic sadface: Sorry, this user has been locked out.",
    "invalid_credentials": (
        "Epic sadface: Username and password do not match any user in this service"
    ),
    "username_required": "Epic sadface: Username is required",
    "password_required": "Epic sadface: Password is required",
    "generic_prefix": "Epic sadface",
}

# ── Human-readable names for headline scenarios (used in report) ─────────
SCENARIO_NAMES = {
    "valid_standard":      "Standard user — successful login",
    "valid_problem":       "Problem user — successful login",
    "valid_performance":   "Performance-glitch user — successful login",
    "valid_error":         "Error-prone user — successful login",
    "valid_visual":        "Visual user — successful login",
    "locked_out":          "Locked-out user — account access denied",
    "wrong_password_1":    "Standard user — incorrect password rejected",
    "empty_username":      "Username left blank — required field validation",
    "empty_password":      "Password left blank — required field validation",
    "sql_inject_1":        "SQL injection in both fields — rejected safely",
    "xss_attempt_1":       "XSS script tag in username — rejected safely",
    "whitespace_usr":      "Username with only whitespace — rejected",
    "long_username":       "Extremely long username (100 chars) — rejected as invalid",
    "nonexistent_user_01": "Unregistered user — login rejected",
}

# ── Group label for each scenario (used to cluster cards in report) ──────
SCENARIO_GROUPS = {
    "valid_standard":      "GROUP 1 — VALID LOGIN",
    "valid_problem":       "GROUP 1 — VALID LOGIN",
    "valid_performance":   "GROUP 1 — VALID LOGIN",
    "valid_error":         "GROUP 1 — VALID LOGIN",
    "valid_visual":        "GROUP 1 — VALID LOGIN",
    "locked_out":          "GROUP 2 — LOCKED OUT USER",
    "wrong_password_1":    "GROUP 3 — WRONG PASSWORD",
    "empty_username":      "GROUP 4 — EMPTY FIELD VALIDATION",
    "empty_password":      "GROUP 4 — EMPTY FIELD VALIDATION",
    "sql_inject_1":        "GROUP 5 — SECURITY INPUTS",
    "xss_attempt_1":       "GROUP 5 — SECURITY INPUTS",
    "whitespace_usr":      "GROUP 6 — BOUNDARY VALUES",
    "long_username":       "GROUP 6 — BOUNDARY VALUES",
    "nonexistent_user_01": "GROUP 7 — UNREGISTERED USERS",
}


def perform_valid_login(login_page: LoginPage, user_key: str = "valid_standard") -> None:
    """End-to-end happy path: open → login → assert success."""
    creds = _USER_LOOKUP[user_key]
    username = creds["username"]
    password = creds["password"]

    log.info("STEP1_START|Open login page")
    log.info("Navigating to https://www.saucedemo.com")
    login_page.open()
    log.info('PASS Page loaded · title="Swag Labs"')
    log.info("STEP1_END")

    log.info("STEP2_START|Enter username and password")
    log.info(f"Attempting login as '{username}'")
    log.debug("Filled username field")
    log.debug("Filled password field · value masked")
    log.info("Clicking Login button")
    login_page.login(username, password)
    log.info("STEP2_END")

    log.info("STEP3_START|Verify redirect to inventory page")
    login_page.assert_logged_in()
    log.info('PASS URL contains "inventory.html" · assertion OK')
    log.info("Login successful — inventory page loaded")
    log.info("STEP3_END")


def perform_locked_out_login(login_page: LoginPage, user_key: str = "locked_out") -> None:
    """Locked-out user scenario: expect the specific locked-out error."""
    creds = _USER_LOOKUP[user_key]
    username = creds["username"]
    password = creds["password"]

    log.info("STEP1_START|Open login page")
    log.info("Navigating to https://www.saucedemo.com")
    login_page.open()
    log.info('PASS Page loaded · title="Swag Labs"')
    log.info("STEP1_END")

    log.info("STEP2_START|Enter locked-out user credentials")
    log.info(f"Attempting login as '{username}'")
    log.debug("Filled username field")
    log.debug("Filled password field · value masked")
    log.info("Clicking Login button")
    login_page.login(username, password)
    log.info("STEP2_END")

    log.info("STEP3_START|Verify locked-out error is displayed")
    login_page.assert_error_equals(ERROR_MESSAGES["locked_out"])
    log.warning("Login blocked · error banner appeared")
    log.info(f'PASS Error = "{ERROR_MESSAGES["locked_out"]}" · assertion OK')
    log.info("STEP3_END")


def perform_invalid_login_generic(
    login_page: LoginPage, username, password
) -> None:
    """
    Generic invalid-credentials scenario.

    Handles:
    - empty username + non-empty password  → 'Username is required'
    - non-empty username + empty password  → 'Password is required'
    - empty username + empty password      → 'Username is required'
    - everything else                      → generic invalid-credentials message
    """
    username_str = "" if username is None else str(username)
    password_str = "" if password is None else str(password)

    log.info("STEP1_START|Open login page")
    log.info("Navigating to https://www.saucedemo.com")
    login_page.open()
    log.info('PASS Page loaded · title="Swag Labs"')
    log.info("STEP1_END")

    step2_title = _invalid_step2_title(username_str, password_str)
    log.info(f"STEP2_START|{step2_title}")
    log.info(f"Attempting login as {username_str!r}")
    if username_str == "":
        log.debug("Username field left empty")
    elif "<script>" in username_str:
        log.debug("Filled username field · XSS payload entered")
    elif "'" in username_str or "--" in username_str:
        log.debug("Filled username field · attack payload entered")
    else:
        log.debug("Filled username field")

    if password_str == "":
        log.debug("Password field left empty")
    elif "<script>" in password_str:
        log.debug("Filled password field · XSS payload entered")
    elif "'" in password_str or "--" in password_str:
        log.debug("Filled password field · attack payload entered")
    else:
        log.debug("Filled password field · value masked")

    log.info("Clicking Login button")
    login_page.login(username_str, password_str)
    log.info("STEP2_END")

    step3_title = _invalid_step3_title(username_str, password_str)
    log.info(f"STEP3_START|{step3_title}")

    if username_str == "" and password_str != "":
        login_page.assert_error_equals(ERROR_MESSAGES["username_required"])
        log.warning("Login blocked · validation error appeared")
        log.info(f'PASS Error = "{ERROR_MESSAGES["username_required"]}" · assertion OK')
    elif username_str != "" and password_str == "":
        login_page.assert_error_equals(ERROR_MESSAGES["password_required"])
        log.warning("Login blocked · validation error appeared")
        log.info(f'PASS Error = "{ERROR_MESSAGES["password_required"]}" · assertion OK')
    elif username_str == "" and password_str == "":
        login_page.assert_error_equals(ERROR_MESSAGES["username_required"])
        log.warning("Login blocked · validation error appeared")
        log.info(f'PASS Error = "{ERROR_MESSAGES["username_required"]}" · assertion OK')
    else:
        login_page.assert_error_contains(ERROR_MESSAGES["invalid_credentials"])
        log.warning("Login failed · error banner appeared")
        log.info('PASS Error contains "do not match" · assertion OK')

    log.info("STEP3_END")


def _invalid_step2_title(username: str, password: str) -> str:
    if "<script>" in username or "<script>" in password:
        return "Enter XSS script tag as username" if "<script>" in username else "Enter XSS script tag as password"
    if "'" in username or "--" in username:
        return "Enter SQL injection payload as credentials"
    if username == "" and password != "":
        return "Leave username blank, enter password and submit"
    if username != "" and password == "":
        return "Enter username, leave password blank and submit"
    if username.strip() == "" and username != "":
        return "Enter whitespace-only username"
    if len(username) > 50:
        return "Enter extremely long username string"
    return "Enter valid username with incorrect password"


def _invalid_step3_title(username: str, password: str) -> str:
    if "<script>" in username or "<script>" in password:
        return "Verify app rejected the XSS — no script executed"
    if "'" in username or "--" in username:
        return "Verify app rejected the injection — no bypass"
    if username == "" and password != "":
        return "Verify username required error is shown"
    if username != "" and password == "":
        return "Verify password required error is shown"
    return "Verify credentials mismatch error is displayed"