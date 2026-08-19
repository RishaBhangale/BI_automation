"""
run_manager.py — Subprocess runner that streams pytest output via WebSocket.

Each run launches pytest as a child process. Log lines are:
  1. Cleaned of ANSI terminal codes
  2. Parsed for log level / step markers
  3. Sent via WebSocket to the connected browser client
  4. Buffered and parsed for real-time progress tracking
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import AsyncIterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from terminal logs."""
    return _ANSI_RE.sub("", text)


def _classify(line: str) -> str:
    """Return log level for a log line."""
    l = line.upper()
    if "[PASS]" in l or " PASSED " in l or l.endswith(" PASSED") or l.startswith("PASSED"):
        return "PASS"
    if "[FAIL]" in l or " FAILED " in l or l.endswith(" FAILED") or l.startswith("FAILED") or "ASSERTIONERROR" in l:
        return "FAIL"
    if "ERROR" in l or "TRACEBACK" in l:
        return "ERROR"
    if "STEP" in line and "_START" in line:
        return "STEP"
    return "INFO"


def _get_python_exec() -> str:
    """Return appropriate python executable (prefer project venv)."""
    venv_py = PROJECT_ROOT / "venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


async def stream_pytest(
    config_path: str,
    selected_ids: list[str],
    run_id: str,
    excel_file: str = "",
) -> AsyncIterator[dict]:
    """
    Async generator: yields log-line dicts as pytest runs.
    Each dict: {"level": str, "text": str, "time": str}
    """
    # 1. Resolve dashboard config path
    cfg_clean = config_path.strip() if config_path else "demo_detection.yaml"
    if not cfg_clean.startswith("dashboard_configs/") and not Path(cfg_clean).is_absolute():
        actual_config = f"dashboard_configs/{cfg_clean}"
    else:
        actual_config = cfg_clean

    # Verify config exists
    full_cfg = PROJECT_ROOT / actual_config if not Path(actual_config).is_absolute() else Path(actual_config)
    if not full_cfg.exists():
        yield {
            "level": "ERROR",
            "text": f"Configuration file not found: {actual_config}",
            "time": time.strftime("%H:%M:%S"),
        }
        return

    # 2. Determine target test file & Excel source
    test_file = "tests/dashboard/test_business_scenarios.py"
    if excel_file and "stress" in excel_file.lower():
        test_file = "tests/dashboard/test_stress_suite.py"
    elif any("TC-S-" in str(tid) for tid in selected_ids):
        test_file = "tests/dashboard/test_stress_suite.py"

    # 3. Build -k filter if specific tests are selected
    k_filter = ""
    if selected_ids:
        clean_ids = [str(tid).strip() for tid in selected_ids if str(tid).strip()]
        if clean_ids:
            k_filter = " or ".join(clean_ids)

    cmd = [
        _get_python_exec(), "-m", "pytest",
        test_file,
        f"--dashboard-config={actual_config}",
        "-v",
        "-s",
        "--tb=short",
        "--no-header",
        "-p", "no:html",
        "--log-cli-level=INFO",
    ]
    if k_filter:
        cmd += ["-k", k_filter]

    yield {
        "level": "INFO",
        "text": f"Launching validation suite: {test_file} with config: {actual_config}",
        "time": time.strftime("%H:%M:%S"),
    }
    if excel_file:
        yield {
            "level": "INFO",
            "text": f"Test case source file: {excel_file}",
            "time": time.strftime("%H:%M:%S"),
        }
    if k_filter:
        yield {
            "level": "INFO",
            "text": f"Filtered scenarios: {k_filter}",
            "time": time.strftime("%H:%M:%S"),
        }

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    if excel_file:
        excel_path = PROJECT_ROOT / "test_data" / excel_file
        if excel_path.exists():
            env["BI_TEST_EXCEL_PATH"] = str(excel_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    now = lambda: time.strftime("%H:%M:%S")

    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        raw_str = raw.decode("utf-8", errors="replace").rstrip()
        line = _strip_ansi(raw_str)
        if not line.strip():
            continue
        yield {
            "level": _classify(line),
            "text": line,
            "time": now(),
        }

    await proc.wait()
    status_label = "SUCCESS" if proc.returncode == 0 else f"COMPLETED WITH EXIT CODE {proc.returncode}"
    yield {
        "level": "INFO" if proc.returncode == 0 else "ERROR",
        "text": f"--- Validation run finished: {status_label} ---",
        "time": now(),
    }
