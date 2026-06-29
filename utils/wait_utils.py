import time
from playwright.sync_api import Page, Locator
from config.settings import ELEMENT_TIMEOUT
from utils.logger import get_logger

log = get_logger("wait_utils")


def wait_for_seconds(seconds: float) -> None:
    """Hard sleep – use rarely, prefer Playwright auto-wait."""
    log.debug(f"Sleeping for {seconds} seconds")
    time.sleep(seconds)


def wait_for_element_visible(locator: Locator, timeout: int = ELEMENT_TIMEOUT) -> None:
    """Wait until the given locator is visible."""
    log.debug(f"Waiting for element to be visible (timeout={timeout}ms)")
    locator.wait_for(state="visible", timeout=timeout)


def wait_for_url_contains(page: Page, partial_url: str, timeout: int = ELEMENT_TIMEOUT) -> None:
    """Wait until the page URL contains given string."""
    log.debug(f"Waiting for URL to contain '{partial_url}' (timeout={timeout}ms)")
    page.wait_for_url(f"**{partial_url}**", timeout=timeout)