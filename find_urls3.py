from playwright.sync_api import sync_playwright
import time
import re

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Try searching Google
    page.goto("https://www.bing.com/search?q=site:app.powerbi.com+%22view%3Fr%3D%22")
    time.sleep(5)
    html = page.content()
    links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Bing Search links:", set(links))
    
    browser.close()
