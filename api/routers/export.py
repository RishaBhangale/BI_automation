"""
export.py — /export/{run_id} endpoint.
Generates and serves run-specific PDF and rich HTML validation reports.
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

    fmt = format.lower().strip()
    config_name = run.get("config", "report")
    config_slug = Path(config_name).stem.replace(" ", "_")
    download_filename = f"dashboard_validation_{run_id}_{config_slug}.{fmt}"

    # Dedicated per-run cache in reports/HTML_reports/
    reports_dir = PROJECT_ROOT / "reports" / "HTML_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    candidate_files = [
        reports_dir / f"dashboard_validation_{run_id}_{config_slug}.{fmt}",
        reports_dir / f"dashboard_validation_{run_id}.{fmt}",
    ]

    def _is_valid_cache(f: Path, file_fmt: str) -> bool:
        if not f.exists():
            return False
        if file_fmt == "pdf":
            # Must be a rich Playwright PDF (at least 35KB; ReportLab table is 3.7KB)
            if f.stat().st_size < 35000:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            try:
                head = f.read_bytes()[:2000]
                if b"ReportLab" in head:
                    f.unlink(missing_ok=True)
                    return False
            except Exception:
                pass
            return True
        elif file_fmt == "html":
            if f.stat().st_size < 5000:
                return False
            try:
                content = f.read_text(encoding="utf-8")
                # Ensure the HTML includes the run ID and print styles
                if run_id and run_id not in content:
                    return False
                if "@media print" not in content:
                    return False
            except Exception:
                return False
            return True
        return False

    cached_file = next((f for f in candidate_files if _is_valid_cache(f, fmt)), None)

    if fmt == "pdf":
        if cached_file:
            try:
                return Response(
                    content=cached_file.read_bytes(),
                    media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
                )
            except Exception:
                pass

        pdf_bytes = export_pdf(run)
        save_target = candidate_files[0]
        try:
            save_target.write_bytes(pdf_bytes)
        except Exception:
            pass

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
        )
    else:
        if cached_file:
            try:
                html_text = cached_file.read_text(encoding="utf-8")
                return Response(
                    content=html_text.encode("utf-8"),
                    media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
                )
            except Exception:
                pass

        html_content = run_to_html(run)
        save_target = candidate_files[0]
        try:
            save_target.write_text(html_content, encoding="utf-8")
        except Exception:
            pass

        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
        )
