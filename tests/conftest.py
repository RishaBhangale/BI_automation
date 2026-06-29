import pytest
from config.settings import HEADLESS, BROWSER_CHANNEL, BROWSER_WIDTH, BROWSER_HEIGHT

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
