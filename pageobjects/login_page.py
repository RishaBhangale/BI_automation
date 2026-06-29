import re
from playwright.sync_api import Page, expect
from config.settings import DEMO_BASE_URL
from pageobjects.base_page import BasePage
from locators.login_locators import LoginLocators


class LoginPage(BasePage):
    """Page Object for the SauceDemo login page."""

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self.username_input = self.page.locator(LoginLocators.USERNAME_INPUT)
        self.password_input = self.page.locator(LoginLocators.PASSWORD_INPUT)
        self.login_button   = self.page.locator(LoginLocators.LOGIN_BUTTON)
        self.error_message  = self.page.locator(LoginLocators.ERROR_MESSAGE)

    # ── Actions ────────────────────────────────────────────────
    def open(self) -> None:
        self.goto(DEMO_BASE_URL)

    def login(self, username, password) -> None:
        """
        Perform login with given credentials.

        Username/password are coerced to strings and None/NaN are
        converted to empty strings before passing to Playwright .fill().
        """
        username_str = "" if username is None else str(username)
        password_str = "" if password is None else str(password)

        self.username_input.fill(username_str)
        self.password_input.fill(password_str)
        self.login_button.click()

    # ── Positive assertion ─────────────────────────────────────
    def assert_logged_in(self) -> None:
        """Inventory page URL contains 'inventory.html' after successful login."""
        expect(self.page).to_have_url(re.compile(r"inventory\.html"))

    # ── Error handling helpers ─────────────────────────────────
    def get_error_text(self) -> str:
        """Return the error banner text (or empty string if not visible yet)."""
        try:
            text = self.error_message.text_content(timeout=5_000) or ""
        except Exception as exc:
            self.log.warning(f"Error while fetching error text: {exc}")
            text = ""
        self.log.debug(f"Error banner text: '{text}'")
        return text

    def assert_error_contains(self, expected_substring: str) -> None:
        """Assert that the error banner contains a given substring."""
        expect(self.error_message).to_be_visible()
        actual = self.get_error_text()
        assert expected_substring in actual, (
            f"Expected error to contain '{expected_substring}', got '{actual}'"
        )

    def assert_error_equals(self, expected_text: str) -> None:
        """Assert that the error banner matches the full expected text."""
        expect(self.error_message).to_have_text(expected_text)