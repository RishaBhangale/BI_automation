import pytest

from pageobjects.login_page import LoginPage
from data.login_data import get_login_test_rows
from methods.login_methods import (
    perform_valid_login,
    perform_locked_out_login,
    perform_invalid_login_generic,
)

LOGIN_ROWS = get_login_test_rows()

def test_failed_case(page):
    # Navigating somewhere so the screenshot isn't just a blank white screen
    page.goto("https://www.saucedemo.com/")
    assert False
def _scenario_ids(row: dict) -> str:
    return row["scenario"]


@pytest.mark.smoke
@pytest.mark.login
@pytest.mark.parametrize("row", LOGIN_ROWS, ids=_scenario_ids)
def test_login_scenarios_from_excel(login_page: LoginPage, row: dict) -> None:
    """
    Excel-driven login tests.

    Behaves differently based on expected_result:
    - success: user should land on inventory page
    - locked_out: specific locked-out error message
    - invalid: generic/field-specific invalid-credentials handling
    """
    scenario = row["scenario"]
    username = row["username"]
    password = row["password"]
    expected = row["expected_result"]

    if expected == "success":
        perform_valid_login(login_page, user_key=scenario)

    elif expected == "locked_out":
        perform_locked_out_login(login_page, user_key=scenario)

    elif expected == "invalid":
        perform_invalid_login_generic(login_page, username=username, password=password)

    else:
        pytest.fail(f"Unknown expected_result '{expected}' in scenario '{scenario}'")