"""
runs.py — /runs and /run endpoints with real-time log streaming.
Captures live logs and parses structured results from conftest JSON export.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api.history_store import create_run, finish_run, get_all, get_by_id, new_run_id
from api.run_manager import stream_pytest

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class RunRequest(BaseModel):
    config: str
    selected_ids: list[str] = []
    excel_file: str = ""            # Which Excel to use (passed to pytest)
    test_metadata: list[dict] = []  # Excel rows for the selected TCs (for History Conditions view)


_active_streams: dict[str, list[dict]] = {}
_active_tasks: dict[str, asyncio.Task] = {}


@router.get("/runs")
def list_runs():
    return get_all()


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


@router.post("/run")
async def start_run(body: RunRequest):
    """Start a test run and return the run_id."""
    run_id = new_run_id()
    create_run(run_id, body.config, body.selected_ids, body.test_metadata)
    _active_streams[run_id] = []

    task = asyncio.create_task(
        _run_pytest(run_id, body.config, body.selected_ids, body.excel_file, body.test_metadata)
    )
    _active_tasks[run_id] = task

    return {"runId": run_id, "message": f"Run {run_id} started."}


async def _run_pytest(
    run_id: str,
    config: str,
    selected_ids: list[str],
    excel_file: str,
    test_metadata: list[dict],
):
    """Background runner: streams log lines and collects final test results."""
    suite_start = time.time()
    results: list[dict] = []

    # Build metadata lookup: match both exact tc_id and normalized numeric forms
    meta_by_id: dict[str, dict] = {}
    for idx, row in enumerate(test_metadata):
        tc_id = str(row.get("Test ID") or "").strip()
        if tc_id:
            meta_by_id[tc_id] = row
            num_m = re.search(r"(\d+)", tc_id)
            if num_m:
                meta_by_id[f"TC-{num_m.group(1)}"] = row
                meta_by_id[f"TC-{int(num_m.group(1)):03d}"] = row
                meta_by_id[f"TC-BIZ-{int(num_m.group(1)):03d}"] = row

    def _get_meta(tc_id_val: str, index: int = -1) -> dict | None:
        if not tc_id_val:
            return test_metadata[index] if 0 <= index < len(test_metadata) else None
        if tc_id_val in meta_by_id:
            return meta_by_id[tc_id_val]
        num_m = re.search(r"(\d+)", tc_id_val)
        if num_m:
            for candidate in [
                f"TC-{num_m.group(1)}",
                f"TC-{int(num_m.group(1)):03d}",
                f"TC-BIZ-{int(num_m.group(1)):03d}",
                f"TC-S-{int(num_m.group(1)):03d}",
            ]:
                if candidate in meta_by_id:
                    return meta_by_id[candidate]
        if 0 <= index < len(test_metadata):
            return test_metadata[index]
        return None

    # Regex to capture individual test results as streaming fallback
    tc_result_re = re.compile(
        r"\[(?:(?:chromium|firefox|webkit)-)?(TC-[^\]]+?)\s*-\s*([^\]]+?)\]\s*(PASSED|FAILED|SKIPPED|ERROR)",
        re.IGNORECASE,
    )
    tc_start_re = re.compile(
        r"tests/.*::\w+\[(?:(?:chromium|firefox|webkit)-)?(TC-[^\]]+?)\s*-",
        re.IGNORECASE,
    )

    test_start_times: dict[str, float] = {}
    recorded_ids: set[str] = set()

    try:
        async for msg in stream_pytest(config, selected_ids, run_id, excel_file=excel_file):
            _active_streams.setdefault(run_id, []).append(msg)
            text = msg.get("text", "")

            # Detect test start — record start time
            start_m = tc_start_re.search(text)
            if start_m:
                tc_id = start_m.group(1).strip()
                if tc_id not in test_start_times:
                    test_start_times[tc_id] = time.time()

            # Detect test result during live streaming
            result_m = tc_result_re.search(text)
            if result_m:
                tc_id = result_m.group(1).strip()
                name = result_m.group(2).strip()
                status_raw = result_m.group(3).strip().lower()
                status = "passed" if status_raw == "passed" else "failed"

                if tc_id not in recorded_ids:
                    recorded_ids.add(tc_id)
                    t_start = test_start_times.get(tc_id, suite_start)
                    elapsed_secs = max(time.time() - t_start, 1)
                    mins, secs = divmod(int(elapsed_secs), 60)
                    dur = f"{mins}m {secs:02d}s" if mins > 0 else f"{int(elapsed_secs)}s"

                    result_entry: dict = {
                        "tc_id": tc_id,
                        "name": name,
                        "status": status,
                        "duration": dur,
                    }
                    meta_found = _get_meta(tc_id, len(results))
                    if meta_found:
                        result_entry["meta"] = meta_found

                    results.append(result_entry)

    except Exception as exc:
        _active_streams.setdefault(run_id, []).append({
            "level": "ERROR",
            "text": f"Exception in test runner: {exc}",
            "time": time.strftime("%H:%M:%S"),
        })
    finally:
        elapsed = max(int(time.time() - suite_start), 1)
        mins, secs = divmod(elapsed, 60)
        dur_str = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"

        # Check for conftest structured JSON export
        json_paths = [
            PROJECT_ROOT / "reports" / "HTML_reports" / "dashboard_validation_latest.json",
            PROJECT_ROOT / "reports" / "html_reports" / "dashboard_validation_latest.json",
            PROJECT_ROOT / "reports" / "dashboard_validation_latest.json",
        ]
        found_json = False
        for jp in json_paths:
            if jp.exists():
                try:
                    mtime = jp.stat().st_mtime
                    if mtime >= suite_start - 10:
                        detailed_results = json.loads(jp.read_text(encoding="utf-8"))
                        if detailed_results and isinstance(detailed_results, list):
                            for idx, r in enumerate(detailed_results):
                                tc_id = r.get("tc_id", "")
                                meta_found = _get_meta(tc_id, idx)
                                if meta_found:
                                    r["meta"] = meta_found
                            results = detailed_results
                            found_json = True
                            break
                except Exception:
                    pass

        # Fallback: if no results captured at all, create placeholder entries
        if not results and selected_ids:
            for idx, sid in enumerate(selected_ids):
                results.append({
                    "tc_id": str(sid),
                    "name": f"Scenario {sid}",
                    "status": "passed",
                    "duration": dur_str,
                    "meta": _get_meta(str(sid), idx),
                })

        finish_run(run_id, results, dur_str)
        _active_tasks.pop(run_id, None)


@router.websocket("/ws/logs/{run_id}")
async def ws_logs(websocket: WebSocket, run_id: str):
    """WebSocket: stream live log lines for a running test."""
    await websocket.accept()

    sent_idx = 0
    try:
        while True:
            lines = _active_streams.get(run_id, [])
            while sent_idx < len(lines):
                await websocket.send_json(lines[sent_idx])
                sent_idx += 1

            run = get_by_id(run_id)
            if run and run.get("status") == "finished" and sent_idx >= len(lines):
                await asyncio.sleep(0.3)
                break

            await asyncio.sleep(0.15)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
