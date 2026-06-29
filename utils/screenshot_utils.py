from pathlib import Path
from playwright.sync_api import Page
from config.settings import SCREENSHOT_DIR


def _safe_name(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")


def take_screenshot(page: Page, name: str) -> Path:
    """Take a screenshot and return the file path."""
    directory = Path(SCREENSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{_safe_name(name)}.png"
    page.screenshot(path=str(file_path))
    return file_path


def take_failure_screenshot(page: Page, test_name: str) -> Path:
    """Standard naming for failure screenshots used in hooks."""
    return take_screenshot(page, f"FAILED_{test_name}")