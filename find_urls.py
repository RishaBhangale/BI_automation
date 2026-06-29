from playwright.sync_api import sync_playwright
import time
import re

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://community.powerbi.com/t5/Data-Stories-Gallery/bd-p/DataStoriesGallery')
    time.sleep(3)
    
    # Extract links
    html = page.content()
    links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Direct links from Data Stories Gallery:", links)
    
    # Try searching Google
    page.goto("https://www.google.com/search?q=site:app.powerbi.com+%22view%3Fr%3D%22")
    time.sleep(3)
    html = page.content()
    links2 = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Google Search links:", set(links2))
    
    browser.close()
