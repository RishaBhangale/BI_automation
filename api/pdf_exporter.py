"""
pdf_exporter.py — Converts a validation run dict to a PDF (via ReportLab) or rich HTML.

ReportLab is pure Python and works cross-platform (macOS, Linux, Windows)
without requiring native C-libraries like libcairo or libpango.

Produces an executive, audit-ready validation report with:
  1. Executive Header & Environment Metadata
  2. KPI Scorecard & Pass/Fail Distribution Visual
  3. Consolidated Scenario Validation Matrix
  4. Deep-Dive Scenario Audit & Data Reconciliation (Per Scenario)
  5. Governance, Sign-Off Block & Two-Pass NumberedCanvas ("Page X of Y")
"""
from __future__ import annotations

import html
import re
import tempfile
from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ── Two-Pass Canvas for Dynamic "Page X of Y" & Running Headers ────────────────

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that accumulates page states and renders dynamic
    'Page X of Y' page numbers and running headers upon save.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.run_id = ""

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_decorations(num_pages)
            super().showPage()
        super().save()

    def _draw_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        page_w, page_h = A4
        left_m = 36
        right_m = page_w - 36

        # Running header on pages 2+
        if self._pageNumber > 1:
            self.drawString(left_m, page_h - 28, "Automated BI Testing — Validation & Audit Report")
            run_lbl = f"Run: {self.run_id}" if self.run_id else ""
            self.drawRightString(right_m, page_h - 28, run_lbl)
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.6)
            self.line(left_m, page_h - 32, right_m, page_h - 32)

        # Running footer on all pages
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(right_m, 24, page_str)
        self.drawString(left_m, 24, "Confidential — For Internal Quality Assurance & BI Governance Only")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.6)
        self.line(left_m, 32, right_m, 32)

        self.restoreState()


# ── String & Data Helpers ──────────────────────────────────────────────────────

def _esc(val: Any) -> str:
    """Safely escape text for ReportLab XML/HTML flowables."""
    if val is None:
        return ""
    return html.escape(str(val).strip())


def _parse_duration_to_secs(dur: str) -> float:
    """Convert '2m 05s' or '45s' to float seconds."""
    total = 0.0
    m = re.search(r"(\d+)\s*m", str(dur))
    s = re.search(r"(\d+)\s*s", str(dur))
    if m:
        total += int(m.group(1)) * 60
    if s:
        total += int(s.group(1))
    return total or 1.0


def _extract_slicers_and_kpi(res: dict, run_meta: list[dict], index: int) -> tuple[list[tuple[str, str]], str, str]:
    """
    Extract (slicers_list, target_kpi, sql_query) from result dict,
    falling back to run_meta and step logs.
    """
    slicers: list[tuple[str, str]] = []
    target_kpi = ""
    sql_query = ""

    meta = res.get("meta")
    if not meta and 0 <= index < len(run_meta):
        meta = run_meta[index]

    if meta and isinstance(meta, dict):
        target_kpi = str(meta.get("KPI to Read") or "").strip()
        sql_query = str(meta.get("SQL File Name") or "").strip()
        for i in range(1, 13):
            s_name = meta.get(f"Slicer {i} Name")
            s_val = meta.get(f"Slicer {i} Value")
            if s_name and s_val and str(s_name).strip().lower() != "nan" and str(s_val).strip().lower() != "nan":
                slicers.append((str(s_name).strip(), str(s_val).strip()))

    # Fallback to inspecting steps if meta wasn't populated
    steps = res.get("steps") or []
    for st in steps:
        title = st.get("title", "")
        # Check for slicer in step title: e.g. "Apply Slicer: State = California"
        s_m = re.search(r"Apply Slicer:\s*([^=]+?)\s*=\s*(.+)$", title, re.IGNORECASE)
        if s_m and (s_m.group(1).strip(), s_m.group(2).strip()) not in slicers:
            slicers.append((s_m.group(1).strip(), s_m.group(2).strip()))

        # Check for KPI in step title: e.g. "Read KPI card: Total Sales"
        if not target_kpi:
            kpi_m = re.search(r"Read KPI card:\s*(.+)$", title, re.IGNORECASE)
            if kpi_m:
                target_kpi = kpi_m.group(1).strip()

        # Check step lines for SQL query
        if not sql_query:
            for l_item in st.get("lines", []):
                line_text = l_item[2] if len(l_item) > 2 else ""
                if "Auto-generated SQL:" in line_text:
                    sql_query = line_text.split("Auto-generated SQL:", 1)[1].strip()
                elif "Using SQL file:" in line_text:
                    sql_query = line_text.split("Using SQL file:", 1)[1].strip()

    return slicers, target_kpi, sql_query


def _extract_reconciliation_values(res: dict) -> tuple[str, str, str]:
    """
    Extract (ui_val, db_val, status_msg) from steps lines.
    """
    ui_val = "—"
    db_val = "—"
    status_msg = ""

    for st in res.get("steps", []):
        for l_item in st.get("lines", []):
            line_text = l_item[2] if len(l_item) > 2 else ""
            if "Dashboard KPI value:" in line_text:
                m = re.search(r"Dashboard KPI value:\s*['\"]?([^'\"]+)['\"]?", line_text)
                if m:
                    ui_val = m.group(1).strip()
            elif "Database result:" in line_text:
                m = re.search(r"Database result:\s*(.+)$", line_text)
                if m:
                    db_val = m.group(1).strip()
            elif "[PASS]" in line_text or "[FAIL]" in line_text:
                status_msg = line_text

    return ui_val, db_val, status_msg


# ── HTML Export ────────────────────────────────────────────────────────────────

def run_to_html(run: dict) -> str:
    """
    Generate a rich HTML report using report_generator.py if available,
    falling back to a clean standalone HTML template.
    """
    try:
        from utils.report_generator import TestResult, generate_report
        import os

        results = []
        for r in run.get("results", []):
            status = (r.get("status") or "").lower()
            outcome = "passed" if status == "passed" else ("failed" if status == "failed" else "skipped")
            dur_str = r.get("duration") or "0s"
            results.append(TestResult(
                tc_id=r.get("tc_id", "-"),
                name=r.get("name", "-"),
                outcome=outcome,
                duration=_parse_duration_to_secs(dur_str),
                steps=r.get("steps") or [],
            ))

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp_path = tmp.name

        generate_report(
            results=results,
            output_path=tmp_path,
            project=f"Automated BI Testing — {run.get('runId', '')}",
            environment="Validation Run",
            suite="Dashboard Regression & KPI Validation",
            base_url=run.get("config", ""),
            test_data_source=run.get("config", ""),
        )
        html_out = open(tmp_path, encoding="utf-8").read()
        os.unlink(tmp_path)
        return html_out

    except Exception:
        return _minimal_html(run)


def _minimal_html(run: dict) -> str:
    """Clean fallback HTML report."""
    passed = run.get("passed", 0)
    failed = run.get("failed", 0)
    total = run.get("total", 0)
    results = run.get("results", [])

    rows = ""
    for r in results:
        status = (r.get("status") or "unknown").lower()
        color = "#15a34a" if status == "passed" else "#dc2626"
        rows += (
            f'<tr><td style="font-family:monospace;color:#4f46e5;font-weight:600">{_esc(r.get("tc_id","-"))}</td>'
            f'<td>{_esc(r.get("name","-"))}</td>'
            f'<td style="color:{color};font-weight:700">{status.upper()}</td>'
            f'<td>{_esc(r.get("duration","-"))}</td></tr>'
        )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Automated BI Testing — {_esc(run.get('runId', ''))}</title>
<style>body{{font-family:system-ui,-apple-system,sans-serif;padding:32px;color:#1e293b;background:#f8fafc}}
h1{{font-size:22px;color:#0f172a;margin-bottom:4px}} .meta{{color:#64748b;font-size:13px;margin-bottom:24px}}
.stats{{display:flex;gap:16px;margin-bottom:24px}}
.stat{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 20px;min-width:110px;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
.stat p{{margin:0;font-size:24px;font-weight:700}} .stat small{{color:#64748b;font-size:11px;text-transform:uppercase;font-weight:600}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
th,td{{text-align:left;padding:10px 14px;font-size:13px;border-bottom:1px solid #e2e8f0}}
th{{background:#f1f5f9;color:#475569;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
</style></head><body>
<h1>Automated BI Testing — Validation Report</h1>
<p class="meta">{_esc(run.get('runId',''))} · {_esc(run.get('config',''))} · {_esc(run.get('startedAt',''))}</p>
<div class="stats">
  <div class="stat"><small>Total</small><p>{total}</p></div>
  <div class="stat"><small>Passed</small><p style="color:#15a34a">{passed}</p></div>
  <div class="stat"><small>Failed</small><p style="color:#dc2626">{failed}</p></div>
  <div class="stat"><small>Duration</small><p style="font-size:18px">{_esc(run.get('duration','-'))}</p></div>
</div>
<table><thead><tr><th>Test ID</th><th>Scenario Name</th><th>Status</th><th>Duration</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


# ── PDF Export (ReportLab Executive Audit Report) ──────────────────────────────

def export_pdf(run: dict) -> bytes:
    """
    Generate an executive, audit-ready PDF validation report using ReportLab.
    Cross-platform, pure-Python, styled with modern data-density and hierarchy.
    """
    buf = BytesIO()

    # Document Geometry: A4 with 36pt (0.5 in) margins = 523.27pt content width
    PAGE_W, PAGE_H = A4
    CONTENT_W = PAGE_W - 72  # 523.27 points

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=42,
        bottomMargin=42,
    )

    # ── Color Palette ──────────────────────────────────────────────────────────
    C_PRIMARY     = colors.HexColor("#0f172a")  # Slate 900
    C_HEADER_BG   = colors.HexColor("#1e293b")  # Slate 800
    C_INDIGO      = colors.HexColor("#4f46e5")  # Indigo 600
    C_PASS        = colors.HexColor("#15a34a")  # Green 600
    C_PASS_BG     = colors.HexColor("#f0fdf4")  # Green 50
    C_PASS_BORDER = colors.HexColor("#bbf7d0")  # Green 200
    C_FAIL        = colors.HexColor("#dc2626")  # Red 600
    C_FAIL_BG     = colors.HexColor("#fef2f2")  # Red 50
    C_FAIL_BORDER = colors.HexColor("#fecaca")  # Red 200
    C_CARD_BG     = colors.HexColor("#f8fafc")  # Slate 50
    C_BORDER      = colors.HexColor("#e2e8f0")  # Slate 200
    C_MUTED       = colors.HexColor("#64748b")  # Slate 500
    C_TEXT        = colors.HexColor("#1e293b")  # Slate 800

    # ── Typography Styles ──────────────────────────────────────────────────────
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=C_PRIMARY,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=C_MUTED,
        spaceAfter=10,
    )
    section_h1 = ParagraphStyle(
        "SectionH1",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=C_PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
    )
    section_h2 = ParagraphStyle(
        "SectionH2",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=C_HEADER_BG,
        spaceBefore=8,
        spaceAfter=4,
    )
    body_text = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=C_TEXT,
    )
    code_text = ParagraphStyle(
        "Code",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#334155"),
    )
    meta_label = ParagraphStyle(
        "MetaLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=C_MUTED,
    )
    meta_val = ParagraphStyle(
        "MetaVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=C_TEXT,
    )

    story: list = []

    # ── Run Data Normalization ─────────────────────────────────────────────────
    run_id       = str(run.get("runId") or "RUN-UNKNOWN")
    config_file  = str(run.get("config") or "—")
    started_at   = str(run.get("startedAt") or datetime.now().isoformat())
    duration_str = str(run.get("duration") or "—")
    total        = int(run.get("total") or len(run.get("results") or []))
    passed       = int(run.get("passed") or sum(1 for r in run.get("results", []) if (r.get("status") or "").lower() == "passed"))
    failed       = int(run.get("failed") or sum(1 for r in run.get("results", []) if (r.get("status") or "").lower() == "failed"))
    pass_pct     = round((passed / max(total, 1)) * 100, 1)
    run_meta     = run.get("testMetadata") or []
    results      = run.get("results") or []

    # Format human-friendly timestamp
    try:
        dt_obj = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        started_str = dt_obj.strftime("%d-%b-%Y %H:%M:%S UTC")
    except Exception:
        started_str = started_at

    # ── 1. Header & Organization Banner ────────────────────────────────────────
    story.append(Paragraph("Automated BI Testing — Validation & Audit Report", title_style))
    story.append(Paragraph(
        f"<b>Audit Run ID:</b> {_esc(run_id)} &nbsp;·&nbsp; "
        f"<b>Target Config:</b> {_esc(config_file)} &nbsp;·&nbsp; "
        f"<b>Generated:</b> {started_str}",
        subtitle_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_INDIGO, spaceBefore=0, spaceAfter=8))

    story.append(Spacer(1, 8))


    # ── 2. Executive KPI Scorecard ─────────────────────────────────────────────
    card_w = CONTENT_W / 5.0  # ~104.6 pt per tile
    scorecard_data = [
        [
            Paragraph("<b>TOTAL SCENARIOS</b>", ParagraphStyle("ScH", fontName="Helvetica-Bold", fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)),
            Paragraph("<b>PASSED</b>", ParagraphStyle("ScH", fontName="Helvetica-Bold", fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)),
            Paragraph("<b>FAILED</b>", ParagraphStyle("ScH", fontName="Helvetica-Bold", fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)),
            Paragraph("<b>PASS RATE</b>", ParagraphStyle("ScH", fontName="Helvetica-Bold", fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)),
            Paragraph("<b>TOTAL DURATION</b>", ParagraphStyle("ScH", fontName="Helvetica-Bold", fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)),
        ],
        [
            Paragraph(f"<b>{total}</b>", ParagraphStyle("ScV", fontName="Helvetica-Bold", fontSize=15, textColor=C_PRIMARY, alignment=TA_CENTER)),
            Paragraph(f"<b>{passed}</b>", ParagraphStyle("ScV", fontName="Helvetica-Bold", fontSize=15, textColor=C_PASS, alignment=TA_CENTER)),
            Paragraph(f"<b>{failed}</b>", ParagraphStyle("ScV", fontName="Helvetica-Bold", fontSize=15, textColor=C_FAIL, alignment=TA_CENTER)),
            Paragraph(f"<b>{pass_pct}%</b>", ParagraphStyle("ScV", fontName="Helvetica-Bold", fontSize=15, textColor=C_PASS if failed == 0 else C_FAIL, alignment=TA_CENTER)),
            Paragraph(f"<b>{_esc(duration_str)}</b>", ParagraphStyle("ScV", fontName="Helvetica-Bold", fontSize=11, textColor=C_PRIMARY, alignment=TA_CENTER)),
        ],
    ]
    scorecard_table = Table(scorecard_data, colWidths=[card_w] * 5)
    scorecard_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.white),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING",    (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    story.append(scorecard_table)
    story.append(Spacer(1, 6))

    # ── 3. Consolidated Scenario Validation Matrix ─────────────────────────────
    story.append(Paragraph("Scenario Validation Matrix", section_h1))

    matrix_cols = [55, 175, 80, 125, 45, 43]  # Sum = 523pt
    matrix_headers = [
        Paragraph("<b>Test ID</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white)),
        Paragraph("<b>Scenario Name</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white)),
        Paragraph("<b>Target KPI</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white)),
        Paragraph("<b>Applied Filters</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white)),
        Paragraph("<b>Status</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("<b>Time</b>", ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white, alignment=TA_RIGHT)),
    ]
    matrix_rows = [matrix_headers]
    matrix_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_HEADER_BG),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]

    for idx, r in enumerate(results, start=1):
        status = (r.get("status") or "unknown").lower()
        is_pass = status == "passed"
        slicers, target_kpi, _ = _extract_slicers_and_kpi(r, run_meta, idx - 1)

        slicers_str = "None (Grand Total)"
        if slicers:
            slicers_str = " · ".join([f"<b>{_esc(s[0])}:</b> {_esc(s[1])}" for s in slicers])

        status_badge = Paragraph(
            f"<font color='{'#15a34a' if is_pass else '#dc2626'}'><b>{status.upper()}</b></font>",
            ParagraphStyle("StBadge", fontName="Helvetica-Bold", fontSize=7.5, alignment=TA_CENTER),
        )

        matrix_rows.append([
            Paragraph(f"<font color='#4f46e5'><b>{_esc(r.get('tc_id', '-'))}</b></font>", body_text),
            Paragraph(_esc(r.get("name", "-")), body_text),
            Paragraph(f"<b>{_esc(target_kpi or '—')}</b>", body_text),
            Paragraph(slicers_str, ParagraphStyle("SlicersComp", fontName="Helvetica", fontSize=7, leading=9, textColor=C_MUTED)),
            status_badge,
            Paragraph(_esc(r.get("duration", "-")), ParagraphStyle("Dur", fontName="Helvetica", fontSize=7.5, alignment=TA_RIGHT)),
        ])

        row_bg = colors.HexColor("#ffffff") if idx % 2 == 1 else colors.HexColor("#f8fafc")
        matrix_styles.append(("BACKGROUND", (0, idx), (-1, idx), row_bg))

    matrix_table = Table(matrix_rows, colWidths=matrix_cols, repeatRows=1)
    matrix_table.setStyle(TableStyle(matrix_styles))
    story.append(matrix_table)

    # ── Build Document (single page) ───────────────────────────────────────────
    def canvas_maker(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.run_id = run_id
        return c

    doc.build(story, canvasmaker=canvas_maker)
    return buf.getvalue()

