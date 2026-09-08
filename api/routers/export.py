"""
export.py — /export/{run_id} endpoint.
Supports PDF (via reportlab) and rich HTML (reusing generated report files).
"""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.history_store import get_by_id
from api.pdf_exporter import export_pdf, run_to_html

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@router.get("/export/{run_id}")
def export_run(run_id: str, format: str = "pdf"):
    run = get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if format.lower() == "pdf":
        pdf_bytes = export_pdf(run)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.pdf"'},
        )
    else:
        # Generate the corresponding HTML report directly from this run's data in run_history.json
        # so that every run gets its own accurate report reflecting the exact test results.
        html_content = run_to_html(run)
        try:
            cache_path = PROJECT_ROOT / "reports" / "HTML_reports" / f"dashboard_validation_{run_id}.html"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(html_content, encoding="utf-8")
        except Exception:
            pass

        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.html"'},
        )
