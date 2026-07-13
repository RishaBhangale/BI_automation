"""
report_generator.py
Builds a fully custom HTML test execution report with grouped,
step-by-step test cards (Step 1 / Step 2 / Step 3...) matching the
manager's required format.
Called from conftest.py after all tests complete.
"""

from __future__ import annotations
import html as html_module
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


# ── Data structure for one test result ───────────────────────────────────────
class TestResult:
    def __init__(
        self,
        tc_id: str,
        name: str,
        outcome: str,          # "passed" | "failed" | "skipped"
        duration: float,       # seconds
        error_text: str = "",
        screenshot_b64: str = "",
        steps: Optional[List[Dict]] = None,
        group: str = "GROUP — OTHER",
    ):
        self.tc_id          = tc_id
        self.name           = name
        self.outcome        = outcome
        self.duration       = duration
        self.error_text     = error_text
        self.screenshot_b64 = screenshot_b64
        self.steps          = steps or []
        self.group          = group


# ── Main generator function ───────────────────────────────────────────────────
def generate_report(
    results: List[TestResult],
    output_path: str,
    project: str = "Retail Commerce Platform",
    environment: str = "UAT",
    release: str = "Sprint 24.5",
    suite: str = "Regression Suite",
    base_url: str = "https://www.saucedemo.com",
    browser: str = "Chromium (Non-Headless)",
    viewport: str = "1280 × 720",
    executed_by: str = "qe.automation",
    test_data_source: str = "login_testdata.xlsx",
) -> None:

    now        = datetime.now()
    date_str   = now.strftime("%d-%b-%Y at %H:%M:%S")
    exec_date  = now.strftime("%d-%b-%Y")

    total   = len(results)
    passed  = sum(1 for r in results if r.outcome == "passed")
    failed  = sum(1 for r in results if r.outcome == "failed")
    skipped = sum(1 for r in results if r.outcome == "skipped")
    blocked = 0
    rate    = round((passed / max(total, 1)) * 100, 1)

    total_secs = sum(r.duration for r in results)
    mins, secs = divmod(int(total_secs), 60)
    duration_str = f"{mins:02d} m {secs:02d} s"

    pass_pct = round((passed / max(total, 1)) * 100, 1)
    fail_pct = round((failed / max(total, 1)) * 100, 1)

    # ── CSS ────────────────────────────────────────────────────────────────────
    css = """
  :root{
    --indigo-900:#2e2a6e; --indigo-700:#3f3aa8; --indigo-600:#4f46e5;
    --bg:#f6f7fb; --surface:#ffffff; --surface-2:#fbfbfe;
    --ink:#1f2433; --muted:#6b7280; --faint:#9aa1ae;
    --line:#e8eaf1; --line-strong:#d8dbe6;
    --pass:#15a34a; --pass-bg:#effaf2; --pass-line:#bce5c9;
    --fail:#dc2626; --fail-bg:#fdf1f1; --fail-line:#f3c5c5;
    --skip:#d97706; --skip-bg:#fef6ea;
    --blocked:#7c3aed;
    --info:#2563eb; --debug:#9333ea; --warn:#d97706;
    --mono:'SF Mono','Cascadia Code','JetBrains Mono',Consolas,'Liberation Mono',monospace;
    --sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  }
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 24px 64px}
  .doc-head{margin:4px 0 22px}
  .doc-head h1{margin:0;font-size:22px;font-weight:700;letter-spacing:-.01em;color:var(--indigo-900)}
  .doc-meta{margin-top:6px;font-size:12.5px;color:var(--muted)}
  .doc-meta b{color:var(--ink);font-weight:600}
  .banner{background:linear-gradient(100deg,var(--indigo-700),var(--indigo-600));color:#fff;border-radius:12px;padding:18px 24px;box-shadow:0 8px 24px -14px rgba(63,58,168,.55)}
  .banner h2{margin:0;font-size:16px;font-weight:600;letter-spacing:.01em}
  .top-grid{display:grid;grid-template-columns:1.35fr 1fr;gap:18px;margin-top:18px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 22px}
  .card-title{font-size:11px;font-weight:700;letter-spacing:.09em;color:var(--indigo-600);text-transform:uppercase;margin:0 0 14px;padding-bottom:12px;border-bottom:1px solid var(--line)}
  .kv{display:grid;grid-template-columns:1fr 1fr;gap:16px 24px}
  .kv .label{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}
  .kv .value{font-size:14px;font-weight:500;color:var(--ink)}
  .kv .value.mono{font-family:var(--mono);font-size:12.5px;font-weight:400;overflow-wrap:break-word;word-break:break-all;max-width:100%}
  .stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:18px}
  .tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 12px;text-align:center}
  .tile .t-label{font-size:11px;color:var(--muted);font-weight:500;margin-bottom:6px}
  .tile .t-num{font-size:26px;font-weight:700;line-height:1;letter-spacing:-.02em}
  .tile.total .t-num{color:var(--indigo-600)}
  .tile.pass  .t-num{color:var(--pass)}
  .tile.fail  .t-num{color:var(--fail)}
  .tile.skip  .t-num{color:var(--skip)}
  .tile.block .t-num{color:var(--blocked)}
  .tile.rate{background:var(--pass-bg);border-color:var(--pass-line)}
  .tile.rate .t-num{color:var(--pass)}
  .progress{margin-top:14px;height:8px;border-radius:6px;overflow:hidden;display:flex;background:var(--line);border:1px solid var(--line)}
  .progress span{display:block;height:100%}
  .progress .p-pass{background:var(--pass)}
  .progress .p-fail{background:var(--fail)}
  .section-h{display:flex;align-items:baseline;gap:12px;margin:34px 2px 14px;flex-wrap:wrap}
  .section-h h3{margin:0;font-size:15px;font-weight:700;color:var(--ink);letter-spacing:-.01em}
  .section-h .count{font-size:12px;color:var(--muted)}
  .legend{display:flex;gap:14px;font-size:11.5px;color:var(--muted)}
  .legend i{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:1px}
  .legend .lp{background:var(--pass)} .legend .lf{background:var(--fail)}
  .toolbar{margin-left:auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .toolbar input[type=text]{padding:6px 12px;border:1px solid var(--line);border-radius:6px;font-size:12px;outline:none;width:200px;font-family:var(--sans)}
  .toolbar label{display:flex;align-items:center;gap:5px;cursor:pointer;font-size:12.5px;font-weight:600}
  .toolbar button{appearance:none;border:none;background:none;cursor:pointer;font:inherit;font-size:12.5px;color:var(--indigo-600);font-weight:600}
  .tc{background:var(--surface);border:1px solid var(--line);border-radius:12px;margin-bottom:12px;overflow:hidden}
  .tc[open]{border-color:var(--line-strong);box-shadow:0 4px 18px -12px rgba(31,36,51,.25)}
  .tc summary{list-style:none;cursor:pointer;padding:15px 20px;display:flex;align-items:center;gap:14px;user-select:none}
  .tc summary::-webkit-details-marker{display:none}
  .tc summary:hover{background:var(--surface-2)}
  .pill{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:4px 10px;border-radius:999px;flex:none}
  .pill.pass{color:var(--pass);background:var(--pass-bg);border:1px solid var(--pass-line)}
  .pill.fail{color:var(--fail);background:var(--fail-bg);border:1px solid var(--fail-line)}
  .pill.skip{color:var(--skip);background:var(--skip-bg);border:1px solid #f8d99a}
  .tc-id{font-family:var(--mono);font-size:12px;color:var(--muted);flex:none}
  .tc-name{font-weight:600;font-size:13.5px;color:var(--ink);flex:1;min-width:0}
  .tc-dur{font-family:var(--mono);font-size:12px;color:var(--faint);flex:none}
  .chev{flex:none;color:var(--faint);transition:transform .18s ease}
  .tc[open] .chev{transform:rotate(90deg)}
  .tc-body{border-top:1px solid var(--line)}
  .step{padding:16px 20px;border-left:3px solid var(--pass-line);border-top:1px solid var(--line)}
  .step:first-child{border-top:none}
  .step.ok{border-left-color:var(--pass);background:#f0fdf4}
  .step.bad{border-left-color:var(--fail);background:var(--fail-bg)}
  .step-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .s-dot{flex:none;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff}
  .s-dot.ok{background:var(--pass)} .s-dot.bad{background:var(--fail)}
  .s-label{font-size:11px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:.04em}
  .s-title{font-size:13.5px;font-weight:600;color:var(--ink)}
  .s-time{margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--faint)}
  .step-lines{margin-left:30px;font-family:var(--mono);font-size:12px;line-height:1.85;color:#475067}
  .step-lines .lvl{font-weight:700;margin-right:6px}
  .lvl-info{color:var(--info)}
  .lvl-debug{color:var(--debug)}
  .lvl-pass{color:var(--pass)}
  .lvl-warn{color:var(--warn)}
  .lvl-fail{color:var(--fail)}
  .fail-grid{display:flex;gap:16px;width:100%;align-items:stretch;margin-left:30px;margin-top:4px}
  .fail-left{flex:1 1 50%;min-width:240px}
  .fail-left .step-lines{margin-left:0}
  .shot{flex:1 1 46%;min-width:260px;max-width:460px;border:1px solid var(--fail-line);border-radius:9px;overflow:hidden;background:#fff;box-shadow:0 6px 18px -12px rgba(220,38,38,.4);align-self:flex-start}
  .shot-cap{font-size:10px;letter-spacing:.07em;text-transform:uppercase;font-weight:700;color:var(--fail);background:var(--fail-bg);padding:7px 12px;border-bottom:1px solid var(--fail-line);display:flex;justify-content:space-between;align-items:center}
  .shot-cap span{color:var(--faint);font-weight:500;letter-spacing:.02em;text-transform:none}
  .shot img{display:block;width:100%;height:auto}
  .assert-detail{margin-left:30px;margin-top:14px;background:#fafbfc;border:1px solid var(--line);border-radius:8px;padding:14px 16px}
  .assert-detail .ad-title{font-size:10.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin-bottom:12px}
  .assert-row{margin-bottom:10px}
  .assert-row:last-child{margin-bottom:0}
  .assert-label{font-size:12.5px;font-weight:700;margin-bottom:4px}
  .assert-label.exp{color:var(--pass)}
  .assert-label.act{color:var(--fail)}
  .assert-box{font-family:var(--mono);font-size:12px;line-height:1.6;padding:9px 12px;border-radius:6px;background:#fff;border:1px solid var(--line);color:var(--ink);word-break:break-word}
  .assert-box.exp{background:var(--pass-bg);border-color:var(--pass-line)}
  .assert-box.act{background:var(--fail-bg);border-color:var(--fail-line)}
  footer{margin-top:36px;text-align:center;font-size:11.5px;color:var(--faint)}
  @media(max-width:820px){.top-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.fail-grid{flex-direction:column;margin-left:0}.shot{max-width:100%}.legend{display:none}}
"""

    # ── Render one log line with colored level tag ─────────────────────────────
    def render_line(level: str, ts: str, text: str) -> str:
        lvl = level.upper()
        cls_map = {
            "INFO": "lvl-info", "DEBUG": "lvl-debug", "PASS": "lvl-pass",
            "WARNING": "lvl-warn", "WARN": "lvl-warn", "ERROR": "lvl-fail", "FAIL": "lvl-fail",
        }
        display_lvl = {"WARNING": "WARN", "ERROR": "FAIL"}.get(lvl, lvl)
        cls = cls_map.get(lvl, "lvl-info")
        safe_text = html_module.escape(text)
        return f'[{ts}] <span class="lvl {cls}">{display_lvl}</span> {safe_text}'

    # ── Render assertion detail panel (Expected vs Actual only) ────────────────
    def render_assertion_detail(detail: Dict) -> str:
        rows = ""
        row_defs = [
            ("exp", "Expected", detail.get("expected", "")),
            ("act", "Actual",   detail.get("actual", "")),
        ]
        for cls, label, text in row_defs:
            if not text:
                continue
            safe_text = html_module.escape(text)
            rows += f"""
        <div class="assert-row">
          <div class="assert-label {cls}">{label}</div>
          <div class="assert-box {cls}">{safe_text}</div>
        </div>"""
        return f"""
      <div class="assert-detail">
        <div class="ad-title">Result Comparison</div>
        {rows}
      </div>"""

    # ── Render one step block ───────────────────────────────────────────────────
    def render_step(step: Dict, shot_html: str = "") -> str:
        is_bad   = step.get("failed", False)
        dot_cls  = "bad" if is_bad else "ok"
        dot_icon = "✕" if is_bad else "✓"
        step_cls = "bad" if is_bad else "ok"
        first_ts = step["lines"][0][1] if step["lines"] else ""
        lines_html = "<br>".join(render_line(lvl, ts, txt) for lvl, ts, txt in step["lines"])

        if is_bad and shot_html:
            body = f"""
        <div class="fail-grid">
          <div class="fail-left"><div class="step-lines">{lines_html}</div></div>
          {shot_html}
        </div>"""
        else:
            body = f'<div class="step-lines">{lines_html}</div>'

        assert_html = ""
        if step.get("assertion_detail"):
            assert_html = render_assertion_detail(step["assertion_detail"])

        return f"""
      <div class="step {step_cls}">
        <div class="step-head">
          <div class="s-dot {dot_cls}">{dot_icon}</div>
          <span class="s-label">Step {step['step_no']}</span>
          <span class="s-title">{html_module.escape(step['title'])}</span>
          <span class="s-time">{first_ts}</span>
        </div>
        {body}
        {assert_html}
      </div>"""

    # ── Build one test card ──────────────────────────────────────────────────
    def fmt_duration(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        return f"{m:02d}:{s:02d}"

    def make_card(r: TestResult) -> str:
        pill_cls  = {"passed": "pass", "failed": "fail", "skipped": "skip"}.get(r.outcome, "skip")
        pill_lbl  = r.outcome.capitalize()
        dur       = fmt_duration(r.duration)
        open_attr = ' open' if r.outcome == "failed" else ''
        safe_name = html_module.escape(r.name)

        shot_html = ""
        if r.screenshot_b64:
            shot_html = f"""
          <div class="shot">
            <div class="shot-cap">Captured Screenshot <span>{r.tc_id}_fail.png</span></div>
            <img src="data:image/png;base64,{r.screenshot_b64}" alt="Failure Screenshot"/>
          </div>"""

        if r.steps:
            steps_html = ""
            for i, step in enumerate(r.steps):
                attach_shot = shot_html if (step.get("failed") and i == len(r.steps) - 1) else ""
                steps_html += render_step(step, attach_shot)
        else:
            # fallback if no structured steps were captured
            fallback_text = r.error_text if r.outcome == "failed" else "Test completed successfully."
            steps_html = render_step({
                "step_no": 1, "title": "Result", "failed": r.outcome == "failed",
                "lines": [("FAIL" if r.outcome == "failed" else "PASS",
                           datetime.now().strftime("%H:%M:%S"), fallback_text)],
            }, shot_html)

        chev_svg = '<svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M9 6l6 6-6 6"/></svg>'

        return f"""
  <details class="tc"{open_attr}>
    <summary>
      <span class="pill {pill_cls}">{pill_lbl}</span>
      <span class="tc-id">{html_module.escape(r.tc_id)}</span>
      <span class="tc-name">{safe_name}</span>
      <span class="tc-dur">{dur}</span>
      {chev_svg}
    </summary>
    <div class="tc-body">{steps_html}</div>
  </details>"""

    # ── Render cards in order, no group headers shown ──────────────────────────
    cards_html = "\n".join(make_card(r) for r in results)

    # ── Assemble full HTML ────────────────────────────────────────────────────
    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BI Dashboard Automation – Test Execution Report</title>
<style>
{css}
</style>
</head>
<body>
<div class="wrap">

  <div class="doc-head">
    <h1>BI Dashboard Automation – Test Execution Report</h1>
    <div class="doc-meta">Generated on <b>{date_str}</b></div>
  </div>

  <div class="banner">
    <h2>Test Automation Execution Report</h2>
  </div>

  <div class="top-grid">
    <div class="card">
      <p class="card-title">Executive Summary</p>
      <div class="kv">
        <div><div class="label">Project</div><div class="value">{html_module.escape(project)}</div></div>
        <div><div class="label">Environment</div><div class="value">{html_module.escape(environment)}</div></div>
        <div><div class="label">Release</div><div class="value">{html_module.escape(release)}</div></div>
        <div><div class="label">Suite</div><div class="value">{html_module.escape(suite)}</div></div>
        <div><div class="label">Executed By</div><div class="value">{html_module.escape(executed_by)}</div></div>
        <div><div class="label">Executed Date</div><div class="value">{exec_date}</div></div>
      </div>
    </div>
    <div class="card">
      <p class="card-title">Test Run Details</p>
      <div class="kv">
        <div><div class="label">Base URL</div><div class="value mono" title="{html_module.escape(base_url)}">{html_module.escape(base_url[:60] + ('...' if len(base_url) > 60 else ''))}</div></div>
        <div><div class="label">Browser</div><div class="value">{html_module.escape(browser)}</div></div>
        <div><div class="label">Viewport</div><div class="value mono">{html_module.escape(viewport)}</div></div>
        <div><div class="label">Duration</div><div class="value mono">{duration_str}</div></div>
        <div><div class="label">Execution Mode</div><div class="value">Local Automated Run</div></div>
        <div><div class="label">Test Data Source</div><div class="value">{html_module.escape(test_data_source)}</div></div>
      </div>
    </div>
  </div>

  <div class="stats">
    <div class="tile total"><div class="t-label">Total</div><div class="t-num">{total}</div></div>
    <div class="tile pass"> <div class="t-label">Passed</div><div class="t-num">{passed}</div></div>
    <div class="tile fail"> <div class="t-label">Failed</div><div class="t-num">{failed}</div></div>
    <div class="tile skip"> <div class="t-label">Skipped</div><div class="t-num">{skipped}</div></div>
    <div class="tile block"><div class="t-label">Blocked</div><div class="t-num">{blocked}</div></div>
    <div class="tile rate"> <div class="t-label">Pass Rate</div><div class="t-num">{rate}%</div></div>
  </div>

  <div class="progress">
    <span class="p-pass" style="width:{pass_pct}%"></span>
    <span class="p-fail" style="width:{fail_pct}%"></span>
  </div>

  <div class="section-h">
    <h3>Execution Results</h3>
    <span class="count">{total} tests</span>
    <div class="legend">
      <span><i class="lp"></i>Passed step</span>
      <span><i class="lf"></i>Failed step</span>
    </div>
    <div class="toolbar">
      <input type="text" id="search-box" placeholder="🔍 Search tests..." onkeyup="filterTests()"/>
      <label style="color:var(--pass)"><input type="checkbox" id="cb-pass" checked onchange="filterTests()" style="accent-color:var(--pass)"/> Passed</label>
      <label style="color:var(--fail)"><input type="checkbox" id="cb-fail" checked onchange="filterTests()" style="accent-color:var(--fail)"/> Failed</label>
      <button onclick="toggleAllDetails(true)">Show all</button>
      <span style="color:var(--faint)">/</span>
      <button onclick="toggleAllDetails(false)">Hide all</button>
    </div>
  </div>

{cards_html}

  <footer></footer>
</div>
<script>
  function toggleAllDetails(open){{
    document.querySelectorAll('details.tc:not([style*="display:none"])').forEach(function(d){{ d.open = open; }});
  }}
  function filterTests(){{
    var search   = document.getElementById('search-box').value.toLowerCase();
    var showPass = document.getElementById('cb-pass').checked;
    var showFail = document.getElementById('cb-fail').checked;
    document.querySelectorAll('details.tc').forEach(function(d){{
      var pill    = d.querySelector('.pill');
      var name    = d.querySelector('.tc-name').textContent.toLowerCase();
      var outcome = pill ? pill.textContent.toLowerCase().trim() : '';
      var matchFilter = (outcome === 'passed' && showPass) || (outcome === 'failed' && showFail);
      var matchSearch = name.includes(search);
      d.style.display = (matchFilter && matchSearch) ? '' : 'none';
    }});
  }}
</script>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html_out, encoding="utf-8")
    print(f"\n[SUCCESS] Custom HTML report saved -> {output_path}\n")
