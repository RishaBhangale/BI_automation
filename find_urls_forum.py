import urllib.request
import re

url = "https://community.powerbi.com/t5/Data-Stories-Gallery/bd-p/DataStoriesGallery"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    # The gallery might have article links
    links = set(re.findall(r'/t5/Data-Stories-Gallery/[a-zA-Z0-9_-]+/m-p/[0-9]+', html))
    print("Found", len(links), "posts.")
    
    for link in list(links)[:5]:
        post_url = "https://community.powerbi.com" + link
        print("Checking", post_url)
        post_req = urllib.request.Request(post_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        post_html = urllib.request.urlopen(post_req).read().decode('utf-8')
        pbi_links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', post_html)
        if pbi_links:
            print("FOUND PBI LINK:", pbi_links)
except Exception as e:
    print(e)
