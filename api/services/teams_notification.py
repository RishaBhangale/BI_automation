"""
teams_notification.py

Sends completed Automated BI Validation run results
to the configured Microsoft Teams Workflow webhook
using an Adaptive Card payload.
"""

from __future__ import annotations

import asyncio
import os

import requests
from dotenv import load_dotenv


load_dotenv()


async def send_teams_notification(
    run_id: str,
    results: list[dict],
    duration: str,
) -> None:
    """Send the completed validation run summary to Microsoft Teams."""

    webhook_url = os.getenv("TEAMS_WEBHOOK_URL")

    if not webhook_url:
        print(
            "Teams notification skipped: "
            "TEAMS_WEBHOOK_URL is not configured."
        )
        return

    passed = sum(
        1
        for result in results
        if result.get("status") == "passed"
    )

    failed = sum(
        1
        for result in results
        if result.get("status") == "failed"
    )

    skipped = sum(
        1
        for result in results
        if result.get("status") in ("skipped", "skip")
    )

    total = len(results)

    status = "Failed" if failed > 0 else "Completed"

    message = (
        f"Automated BI Validation - Run {run_id}\n\n"
        f"Status: {status}\n"
        f"Total Validations: {total}\n"
        f"Passed: {passed}\n"
        f"Failed: {failed}\n"
        f"Skipped: {skipped}\n"
        f"Duration: {duration}"
    )

    # Teams Workflow is using the
    # "Post card in a chat or channel" action,
    # so send a valid Adaptive Card payload.
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
                            "text": message,
                            "wrap": True,
                        }
                    ],
                },
            }
        ],
    }

    try:
        response = await asyncio.to_thread(
            requests.post,
            webhook_url,
            headers={
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        print(
            f"Teams notification sent successfully for Run {run_id}."
        )

    except requests.RequestException as exc:
        print(
            f"Teams notification failed for Run {run_id}: {exc}"
        )