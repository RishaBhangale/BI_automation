import urllib.request
import json
import re

url = "https://en.wikipedia.org/w/api.php?action=query&list=exturlusage&euquery=app.powerbi.com&format=json&eulimit=50"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    res = urllib.request.urlopen(req).read().decode('utf-8')
    data = json.loads(res)
    links = [item['url'] for item in data['query']['exturlusage']]
    pbi_links = [l for l in links if 'view?r=' in l]
    print("Wikipedia links:", pbi_links)
except Exception as e:
    print(e)
