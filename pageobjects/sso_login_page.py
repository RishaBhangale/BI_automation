"""
sso_login_page.py — Microsoft Azure AD (SSO) login flow.

Handles the multi-step Microsoft login page that appears when navigating
to a Power BI dashboard URL that requires authentication.

Microsoft's login flow typically has these steps:
  1. Email entry page (login.microsoftonline.com)
  2. Password entry page (same domain, different view)
  3. "Stay signed in?" prompt (optional, appears post-login)
  4. Redirect back to the original URL (app.powerbi.com/...)

Notes on MFA:
  - If MFA is enforced on the account, this class cannot handle it
    automatically without a TOTP secret or a pre-authenticated browser state.
  - Recommendation: Use a service account with MFA excluded in your
    Conditional Access policy, or use Playwright's storageState to reuse
    a saved authenticated session.
"""

from __future__ import annotations

from playwright.sync_api import Page, TimeoutError as PwTimeoutError

from pageobjects.base_page import BasePage
from utils.logger import get_logger

log = get_logger("sso_login_page")


class SSOLoginPage(BasePage):
    """
    Page Object Model for Microsoft Azure AD login.

    The Microsoft SSO flow renders on login.microsoftonline.com.
    All selectors target elements on that domain.
    """

    # Microsoft login page selectors
    # These are stable — Microsoft rarely changes them.
    _EMAIL_INPUT    = "input[type='email']"
    _NEXT_BTN       = "input[value='Next'], button:has-text('Next')"
    _PASSWORD_INPUT = "input[type='password']"
    _SIGN_IN_BTN    = "input[value='Sign in'], button:has-text('Sign in')"

    # "Stay signed in?" page that sometimes appears after password
    _STAY_SIGNED_IN_YES = "input[value='Yes'], button:has-text('Yes')"
    _STAY_SIGNED_IN_NO  = "input[value='No'],  button:has-text('No')"

    # URL fragments to detect where we are in the flow
    _MS_LOGIN_DOMAIN   = "login.microsoftonline.com"
    _MS_LIVE_DOMAIN    = "login.live.com"
    _PBI_DOMAIN        = "app.powerbi.com"

    def is_on_login_page(self) -> bool:
        """
        Returns True if the browser is currently showing a Microsoft login page.
        Used to detect whether SSO login is required before proceeding.
        """
        current = self.page.url
        return self._MS_LOGIN_DOMAIN in current or self._MS_LIVE_DOMAIN in current

    def enter_email(self, email: str) -> None:
        """
        Type the SSO email address and click Next.

        Args:
            email: Microsoft account email, e.g. "test-user@yourorg.onmicrosoft.com"
        """
        log.info(f"SSO: Entering email for '{email}'")
        self.page.wait_for_selector(self._EMAIL_INPUT)
        self.page.fill(self._EMAIL_INPUT, email)
        self.page.click(self._NEXT_BTN)
        # Wait for page to transition to password step
        self.page.wait_for_load_state("networkidle", timeout=15_000)

    def enter_password(self, password: str) -> None:
        """
        Type the SSO password and click Sign in.

        Args:
            password: Account password (decrypted, in plaintext at this point).
        """
        log.info("SSO: Entering password")
        self.page.wait_for_selector(self._PASSWORD_INPUT)
        self.page.fill(self._PASSWORD_INPUT, password)
        self.page.click(self._SIGN_IN_BTN)
        # Wait for redirect — may go to "Stay signed in?" or directly to PBI
        self.page.wait_for_load_state("networkidle", timeout=30_000)

    def handle_stay_signed_in(self, click_yes: bool = False) -> None:
        """
        Handle the "Stay signed in?" prompt that appears after Microsoft login.
        In CI environments, always click No to avoid browser state persistence issues.

        Args:
            click_yes: If True, clicks Yes (persists session). Default is False (No).
        """
        try:
            # Check if the prompt is visible (it doesn't always appear)
            self.page.wait_for_selector(
                self._STAY_SIGNED_IN_YES,
                timeout=5_000,
                state="visible"
            )
            if click_yes:
                log.info("SSO: 'Stay signed in?' — clicking Yes")
                self.page.click(self._STAY_SIGNED_IN_YES)
            else:
                log.info("SSO: 'Stay signed in?' — clicking No (CI-safe default)")
                self.page.click(self._STAY_SIGNED_IN_NO)
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except PwTimeoutError:
            log.info("SSO: 'Stay signed in?' prompt did not appear — skipping")

    def wait_for_redirect_to_pbi(self, timeout_ms: int = 30_000) -> None:
        """
        Wait until the browser has left the Microsoft login domain and
        arrived at the Power BI report URL.

        Raises:
            PwTimeoutError: If the redirect does not complete within timeout.
        """
        log.info("SSO: Waiting for redirect back to Power BI...")
        self.page.wait_for_url(f"**/{self._PBI_DOMAIN}/**", timeout=timeout_ms)
        log.info(f"SSO: Redirect complete. URL: {self.page.url}")

    def login(self, email: str, password: str) -> None:
        """
        Full SSO login sequence: email → password → stay-signed-in → wait for PBI.

        Args:
            email:    Microsoft account email.
            password: Account password (plaintext, already decrypted by caller).
        """
        self.enter_email(email)
        self.enter_password(password)
        self.handle_stay_signed_in(click_yes=False)
        self.wait_for_redirect_to_pbi()
        log.info("SSO: Login completed successfully")
