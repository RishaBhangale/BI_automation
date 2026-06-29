from playwright.sync_api import Page
from config.settings import NAVIGATION_TIMEOUT
from utils.logger import get_logger
from utils.screenshot_utils import take_screenshot


class BasePage:
    """Shared wrapper around Playwright Page. All POMs inherit this."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.log = get_logger(self.__class__.__name__)

    def goto(self, url: str) -> None:
        self.log.info(f"Navigating to {url}")
        self.page.goto(url, timeout=NAVIGATION_TIMEOUT)

    def get_title(self) -> str:
        title = self.page.title()
        self.log.debug(f"Current page title: {title}")
        return title

    def get_url(self) -> str:
        url = self.page.url
        self.log.debug(f"Current URL: {url}")
        return url

    def capture_screenshot(self, name: str = "screenshot") -> None:
        path = take_screenshot(self.page, name)
        self.log.info(f"Screenshot saved to {path}")