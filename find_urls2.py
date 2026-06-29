import urllib.request
import re

url = "https://raw.githubusercontent.com/microsoft/PowerBI-visuals/master/README.md"
try:
    html = urllib.request.urlopen(url).read().decode('utf-8')
    links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', html)
    print("Links:", set(links))
except:
    pass

url2 = "https://api.github.com/search/code?q=app.powerbi.com/view?r="
req = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
try:
    res = urllib.request.urlopen(req).read().decode('utf-8')
    links = re.findall(r'https://app\.powerbi\.com/view\?r=[a-zA-Z0-9_-]+', res)
    print("GitHub Search Links:", set(links))
except Exception as e:
    print(e)

