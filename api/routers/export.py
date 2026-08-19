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
        # Check if an existing rich HTML report file exists
        html_candidates = [
            PROJECT_ROOT / "reports" / "HTML_reports" / "dashboard_validation_latest.html",
            PROJECT_ROOT / "reports" / "html_reports" / "dashboard_validation_latest.html",
            PROJECT_ROOT / "reports" / "dashboard_validation_latest.html",
        ]
        html_content = ""
        for p in html_candidates:
            if p.exists():
                try:
                    html_content = p.read_text(encoding="utf-8")
                    if html_content:
                        break
                except Exception:
                    pass

        # If not found, check the newest html file in HTML_reports/
        if not html_content:
            for d in [PROJECT_ROOT / "reports" / "HTML_reports", PROJECT_ROOT / "reports" / "html_reports"]:
                if d.exists():
                    files = sorted(d.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if files:
                        try:
                            html_content = files[0].read_text(encoding="utf-8")
                            break
                        except Exception:
                            pass

        if not html_content:
            html_content = run_to_html(run)

        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.html"'},
        )
