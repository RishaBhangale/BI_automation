"""
history_store.py — JSON-based run history for the BI Validator control panel.

Each test run is stored as one JSON object in reports/run_history.json.
Appended on completion, read for history/dashboard views.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HISTORY_FILE = Path(__file__).parent.parent / "reports" / "run_history.json"


def _load() -> list[dict]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(runs: list[dict]) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")


def _config_prefix(config: str) -> str:
    """Extract a short uppercase prefix from a config filename.

    e.g. 'demo_detection.yaml' -> 'DEMO'
         'sales_dashboard.yaml' -> 'SALES'
    """
    stem = Path(config).stem  # strip .yaml
    # Take first word (split on _ or -)
    first_word = re.split(r"[_\-]", stem)[0]
    return re.sub(r"[^A-Z0-9]", "", first_word.upper())[:8] or "RUN"


def new_run_id(config: str = "") -> str:
    """Generate a human-readable Run ID: PREFIX-YYMMDD-SEQ.

    e.g. DEMO-240824-001
    """
    prefix = _config_prefix(config) if config else "RUN"
    today = datetime.now(tz=timezone.utc).strftime("%y%m%d")

    # Count existing runs today with the same prefix to determine sequence number
    existing = _load()
    today_seq = sum(
        1 for r in existing
        if r.get("runId", "").startswith(f"{prefix}-{today}-")
    )
    seq = str(today_seq + 1).zfill(3)
    return f"{prefix}-{today}-{seq}"



def create_run(run_id: str, config: str, selected_tests: list[str], test_metadata: list[dict] | None = None) -> dict:
    run = {
        "runId": run_id,
        "config": config,
        "selectedTests": selected_tests,
        "testMetadata": test_metadata or [],  # List of Excel rows for selected TCs
        "status": "running",
        "startedAt": datetime.now(tz=timezone.utc).isoformat(),
        "finishedAt": None,
        "duration": None,
        "total": len(selected_tests),
        "passed": 0,
        "failed": 0,
        "results": [],
    }
    runs = _load()
    runs.insert(0, run)
    _save(runs)
    return run


def get_all() -> list[dict]:
    return _load()


def get_by_id(run_id: str) -> dict | None:
    for r in _load():
        if r["runId"] == run_id:
            return r
    return None


def update_run(run_id: str, patch: dict) -> None:
    runs = _load()
    for r in runs:
        if r["runId"] == run_id:
            r.update(patch)
            break
    _save(runs)


def finish_run(run_id: str, results: list[dict], duration_str: str) -> None:
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    update_run(run_id, {
        "status": "finished",
        "finishedAt": datetime.now(tz=timezone.utc).isoformat(),
        "duration": duration_str,
        "passed": passed,
        "failed": failed,
        "results": results,
    })
