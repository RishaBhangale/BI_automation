"""
pdf_exporter.py — Converts a run dict to a PDF (via reportlab) or HTML.

reportlab is pure Python and works on both macOS and Windows without
requiring system libraries like libcairo / libpango (which WeasyPrint needs).

Install: pip install reportlab
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any


# ── HTML export ───────────────────────────────────────────────────────────────

def run_to_html(run: dict) -> str:
    """
    Generate a rich HTML report using the same template as report_generator.py.
    Falls back to a clean minimal HTML if report_generator is not importable.
    """
    try:
        # Use the existing rich report_generator template logic
        from utils.report_generator import TestResult, generate_report
        import tempfile, os

        results = []
        for r in run.get("results", []):
            status = (r.get("status") or "").lower()
            outcome = "passed" if status == "passed" else ("failed" if status == "failed" else "skipped")
            # Parse duration string back to seconds
            dur_str = r.get("duration") or "0s"
            dur_secs = _parse_duration_to_secs(dur_str)
            results.append(TestResult(
                tc_id=r.get("tc_id", "-"),
                name=r.get("name", "-"),
                outcome=outcome,
                duration=dur_secs,
                steps=r.get("steps") or [],
            ))

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp_path = tmp.name

        generate_report(
            results=results,
            output_path=tmp_path,
            project=f"BI Validator — {run.get('runId', '')}",
            environment="Validation Run",
            suite="Dashboard KPI & Table Validation",
            base_url=run.get("config", ""),
            test_data_source=run.get("config", ""),
        )
        html = open(tmp_path, encoding="utf-8").read()
        os.unlink(tmp_path)
        return html

    except Exception:
        # Fallback minimal HTML if report_generator not available
        return _minimal_html(run)


def _minimal_html(run: dict) -> str:
    """Minimal fallback HTML report."""
    passed = run.get("passed", 0)
    failed = run.get("failed", 0)
    total  = run.get("total", 0)
    results = run.get("results", [])

    rows = ""
    for r in results:
        status = r.get("status", "unknown")
        color  = "#15a34a" if status == "passed" else "#dc2626"
        rows += (
            f'<tr><td style="font-family:monospace;color:#4f46e5">{r.get("tc_id","-")}</td>'
            f'<td>{r.get("name","-")}</td>'
            f'<td style="color:{color};font-weight:600">{status.upper()}</td>'
            f'<td>{r.get("duration","-")}</td></tr>'
        )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>BI Validator — {run['runId']}</title>
<style>body{{font-family:sans-serif;padding:24px;color:#1f2433}}
h1{{font-size:20px;margin-bottom:4px}} .meta{{color:#6b7280;font-size:12px;margin-bottom:20px}}
.stats{{display:flex;gap:20px;margin-bottom:20px}}
.stat{{border:1px solid #e8eaf1;border-radius:8px;padding:12px 20px}}
.stat p{{margin:0;font-size:22px;font-weight:700}} .stat small{{color:#6b7280;font-size:11px}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:8px 10px;font-size:12px;border-bottom:1px solid #e8eaf1}}
th{{color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-size:11px}}
</style></head><body>
<h1>BI Validator — Validation Report</h1>
<p class="meta">{run['runId']} · {run.get('config','')} · {run.get('startedAt','')}</p>
<div class="stats">
  <div class="stat"><small>Total</small><p>{total}</p></div>
  <div class="stat"><small>Passed</small><p style="color:#15a34a">{passed}</p></div>
  <div class="stat"><small>Failed</small><p style="color:#dc2626">{failed}</p></div>
  <div class="stat"><small>Duration</small><p style="font-size:16px">{run.get('duration','-')}</p></div>
</div>
<h2>Test Results</h2>
<table><thead><tr><th>Test ID</th><th>Scenario</th><th>Status</th><th>Duration</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def _parse_duration_to_secs(dur: str) -> float:
    """Convert '2m 05s' or '45s' or '1m' to float seconds."""
    import re
    total = 0.0
    m = re.search(r"(\d+)\s*m", dur)
    s = re.search(r"(\d+)\s*s", dur)
    if m:
        total += int(m.group(1)) * 60
    if s:
        total += int(s.group(1))
    return total or 1.0


# ── PDF export ────────────────────────────────────────────────────────────────

def export_pdf(run: dict) -> bytes:
    """
    Generate a PDF report using reportlab (pure Python, cross-platform).
    Produces a clean table-based A4 report with pass/fail colouring.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        )
        from reportlab.lib.enums import TA_LEFT, TA_CENTER

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            rightMargin=1.8 * cm,
            leftMargin=1.8 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        INDIGO = colors.HexColor("#4f46e5")
        PASS_C = colors.HexColor("#15a34a")
        FAIL_C = colors.HexColor("#dc2626")
        MUTED  = colors.HexColor("#6b7280")
        BG_PASS = colors.HexColor("#effaf2")
        BG_FAIL = colors.HexColor("#fdf1f1")
        LIGHT_BORDER = colors.HexColor("#e8eaf1")

        title_style = ParagraphStyle(
            "Title", parent=styles["Heading1"],
            fontSize=18, textColor=INDIGO, spaceAfter=4,
        )
        meta_style = ParagraphStyle(
            "Meta", parent=styles["Normal"],
            fontSize=9, textColor=MUTED, spaceAfter=12,
        )
        section_style = ParagraphStyle(
            "Section", parent=styles["Heading2"],
            fontSize=12, textColor=colors.HexColor("#1f2433"), spaceBefore=16, spaceAfter=8,
        )

        story: list = []

        # ── Title ──────────────────────────────────────────────────────────────
        story.append(Paragraph("Automated BI Testing — Validation Report", title_style))
        story.append(Paragraph(
            f"{run.get('runId','')} &nbsp;·&nbsp; {run.get('config','')} &nbsp;·&nbsp; "
            f"{run.get('startedAt','')}",
            meta_style,
        ))
        story.append(HRFlowable(width="100%", thickness=1, color=LIGHT_BORDER, spaceAfter=12))

        # ── Summary stats table ────────────────────────────────────────────────
        total    = run.get("total", 0)
        passed   = run.get("passed", 0)
        failed   = run.get("failed", 0)
        duration = run.get("duration", "-")
        pass_rate = f"{round(passed / max(total, 1) * 100, 1)}%"

        stat_data = [
            ["Total", "Passed", "Failed", "Duration", "Pass Rate"],
            [str(total), str(passed), str(failed), duration, pass_rate],
        ]
        stat_table = Table(stat_data, colWidths=[3.2 * cm] * 5)
        stat_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, 0), 9),
            ("FONTSIZE",   (0, 1), (-1, 1), 13),
            ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
            ("TEXTCOLOR",  (1, 1), (1, 1), PASS_C),
            ("TEXTCOLOR",  (2, 1), (2, 1), FAIL_C),
            ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, 1), [colors.white]),
            ("BOX",        (0, 0), (-1, -1), 0.5, LIGHT_BORDER),
            ("GRID",       (0, 0), (-1, -1), 0.3, LIGHT_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(stat_table)
        story.append(Spacer(1, 16))

        # ── Results table ──────────────────────────────────────────────────────
        story.append(Paragraph("Test Results", section_style))

        col_widths = [3 * cm, 8.5 * cm, 2.5 * cm, 3 * cm]
        header = [
            Paragraph("<b>Test ID</b>", styles["Normal"]),
            Paragraph("<b>Scenario</b>", styles["Normal"]),
            Paragraph("<b>Status</b>", styles["Normal"]),
            Paragraph("<b>Duration</b>", styles["Normal"]),
        ]
        table_data = [header]
        row_styles: list[tuple] = []

        for i, r in enumerate(run.get("results", []), start=1):
            status = (r.get("status") or "").lower()
            is_pass = status == "passed"
            status_text = Paragraph(
                f'<font color="{"#15a34a" if is_pass else "#dc2626"}"><b>{status.upper()}</b></font>',
                styles["Normal"],
            )
            table_data.append([
                Paragraph(f'<font color="#4f46e5">{r.get("tc_id", "-")}</font>', styles["Normal"]),
                Paragraph(r.get("name", "-"), styles["Normal"]),
                status_text,
                Paragraph(r.get("duration", "-"), styles["Normal"]),
            ])
            bg = BG_PASS if is_pass else BG_FAIL
            row_styles.append(("BACKGROUND", (0, i), (-1, i), bg))

        results_table = Table(table_data, colWidths=col_widths)
        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f8")),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("GRID",       (0, 0), (-1, -1), 0.3, LIGHT_BORDER),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ]
        results_table.setStyle(TableStyle(base_style + row_styles))
        story.append(results_table)

        # ── Footer ─────────────────────────────────────────────────────────────
        story.append(Spacer(1, 20))
        story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_BORDER))
        story.append(Paragraph(
            f'Generated by BI Validator &nbsp;·&nbsp; {datetime.now().strftime("%d-%b-%Y %H:%M")}',
            ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8, textColor=MUTED,
                           alignment=TA_CENTER, spaceBefore=6),
        ))

        doc.build(story)
        return buf.getvalue()

    except ImportError:
        # reportlab not installed — fall back to HTML bytes
        html = run_to_html(run)
        return html.encode("utf-8")
