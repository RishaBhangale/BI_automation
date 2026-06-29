from playwright.sync_api import Page, Locator
from utils.logger import get_logger
from utils.screenshot_utils import take_screenshot

log = get_logger("common_methods")


def navigate_and_verify_title(page: Page, url: str, expected_substring: str) -> bool:
    """
    Navigate to a URL and verify the title contains the expected substring.
    Returns True/False and captures a screenshot on mismatch.
    """
    log.info(f"Navigating to {url} expecting title to contain '{expected_substring}'")
    page.goto(url)
    actual = page.title()
    result = expected_substring in actual
    if not result:
        log.warning(f"Title mismatch. Expected '{expected_substring}', got '{actual}'")
        take_screenshot(page, f"title_mismatch_{expected_substring}")
    return result


def clear_and_fill(locator: Locator, value: str) -> None:
    """Utility to clear an input and fill with value."""
    locator.clear()
    locator.fill(value)
    log.debug(f"Filled field with value: {value}")