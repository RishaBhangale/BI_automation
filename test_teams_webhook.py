import os
import requests
from dotenv import load_dotenv

load_dotenv()

webhook_url = os.getenv("TEAMS_WEBHOOK_URL")

if not webhook_url:
    raise RuntimeError("TEAMS_WEBHOOK_URL is missing from .env")

payload = {
    "type": "message",
    "attachments": [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.2",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "Automated BI Validation - Webhook Test"
                    },
                    {
                        "type": "TextBlock",
                        "text": "Webhook connection test successful."
                    }
                ]
            }
        }
    ]
}

response = requests.post(
    webhook_url,
    headers={
        "Content-Type": "application/json"
    },
    json=payload,
    timeout=30
)

print("Status code:", response.status_code)
print("Response:", response.text)

response.raise_for_status()

print("Webhook request sent successfully.")