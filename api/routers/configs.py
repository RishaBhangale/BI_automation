"""
configs.py — /configs endpoint: list and update YAML configs.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

CONFIG_DIR = Path(__file__).parent.parent.parent / "dashboard_configs"


@router.get("/configs")
def list_configs():
    configs = []
    for p in sorted(CONFIG_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            dash = data.get("dashboard", {})
            db   = data.get("source_db", {})
            configs.append({
                "file": p.name,
                "dashboard": dash.get("name", p.stem),
                "pages": len(dash.get("pages", [])),
                "driver": db.get("driver", "—"),
                "content": p.read_text(encoding="utf-8"),
            })
        except Exception:
            pass
    return configs


class ConfigPayload(BaseModel):
    content: str


@router.put("/configs/{filename}")
def save_config(filename: str, body: ConfigPayload):
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = CONFIG_DIR / filename
    # Validate YAML before saving
    try:
        yaml.safe_load(body.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {e}")
    path.write_text(body.content, encoding="utf-8")
    return {"saved": filename}
