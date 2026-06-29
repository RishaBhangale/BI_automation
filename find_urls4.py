from playwright.sync_api import sync_playwright
import time
import re

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Try searching GitHub Gists
    page.goto("https://gist.github.com/search?q=app.powerbi.com")
    time.sleep(3)
    html = page.content()
    links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Gist links:", set(links))
    
    # Try an alternative site or query
    page.goto("https://www.google.com/search?q=site:github.com+%22app.powerbi.com/view%3Fr%3D%22")
    time.sleep(3)
    html = page.content()
    links2 = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Google links:", set(links2))
    
    browser.close()
