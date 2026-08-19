"""
discovery.py — /discover endpoint: runs discover_dashboard.py with a given URL and name.
Streams live stdout via WebSocket so the user can see discovery progress in the UI.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter()

# Root of the project (two levels up from api/routers/)
_ROOT = Path(__file__).resolve().parent.parent.parent
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_active_discoveries: dict[str, list[dict]] = {}
_discovery_status: dict[str, str] = {}  # disc_id -> "running" | "finished" | "error"


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _get_python_exec() -> str:
    """Return python executable, preferring project virtualenv if available."""
    venv_py = _ROOT / "venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


class DiscoverRequest(BaseModel):
    url: str
    name: str  # Human-readable name, also used to derive output YAML filename


def _new_disc_id() -> str:
    import uuid
    return f"DISC-{uuid.uuid4().hex[:6].upper()}"


@router.post("/discover")
async def run_discovery(body: DiscoverRequest):
    """Start a discovery run. Returns discId for WebSocket tracking."""
    if not body.url.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="url is required")
    if not body.name.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="name is required")

    disc_id = _new_disc_id()
    _active_discoveries[disc_id] = []
    _discovery_status[disc_id] = "running"

    asyncio.create_task(_run_discover(disc_id, body.url.strip(), body.name.strip()))
    return {"discId": disc_id, "message": f"Discovery {disc_id} started."}


@router.get("/discover/results")
def get_discovery_results():
    """List all YAML configs in dashboard_configs/ folder."""
    configs_dir = _ROOT / "dashboard_configs"
    if not configs_dir.exists():
        return []
    files = sorted(configs_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "file": f.name,
            "path": str(f),
            "sizeBytes": f.stat().st_size,
            "modifiedAt": f.stat().st_mtime,
        }
        for f in files
    ]


@router.websocket("/ws/discover/{disc_id}")
async def ws_discover(websocket: WebSocket, disc_id: str):
    """WebSocket: stream live log lines for a running discovery."""
    await websocket.accept()
    sent_idx = 0
    try:
        while True:
            lines = _active_discoveries.get(disc_id, [])
            while sent_idx < len(lines):
                await websocket.send_json(lines[sent_idx])
                sent_idx += 1

            status = _discovery_status.get(disc_id, "running")
            if status in ("finished", "error") and sent_idx >= len(lines):
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


async def _run_discover(disc_id: str, url: str, name: str) -> None:
    """Background task: runs discover_dashboard.py and captures output line by line."""

    def _push(text: str, level: str = "INFO") -> None:
        _active_discoveries.setdefault(disc_id, []).append({
            "level": level,
            "text": text,
            "time": time.strftime("%H:%M:%S"),
        })

    script = _ROOT / "scripts" / "discover_dashboard.py"
    py_exec = _get_python_exec()
    cmd = [
        py_exec, str(script),
        url,
        "--name", name,
        "--no-headless",
    ]

    _push(f"Starting discovery for: {url}")
    _push(f"Output config name: {name}")

    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(_ROOT)}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_ROOT),
            env=env,
        )

        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = _strip_ansi(raw.decode("utf-8", errors="replace").rstrip())
            if not line.strip():
                continue
            level = "ERROR" if "error" in line.lower() or "traceback" in line.lower() else "INFO"
            if "saved →" in line or "Config saved" in line or "Discovery complete" in line:
                level = "PASS"
            _push(line, level)

        await proc.wait()

        if proc.returncode == 0:
            _push("Discovery complete. YAML config saved.", "PASS")
            _discovery_status[disc_id] = "finished"
        else:
            _push(f"Discovery exited with code {proc.returncode}", "ERROR")
            _discovery_status[disc_id] = "error"

    except Exception as exc:
        _push(f"Exception: {exc}", "ERROR")
        _discovery_status[disc_id] = "error"
