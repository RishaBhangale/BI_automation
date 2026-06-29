import urllib.request
import urllib.parse
import re

query = 'site:app.powerbi.com "view?r="'
url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(query)
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    links = set(re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html))
    for link in links:
        print(link)
except Exception as e:
    print(e)
