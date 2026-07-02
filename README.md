# BI Dashboard Validation Framework (Pytest + Playwright)

A configuration-driven automated testing framework for validating **published Power BI dashboards** against source-of-truth data (database or Excel).

Built with Python, Playwright, and Pytest — no PBIX access required. Works against any publicly published or Microsoft SSO-protected Power BI dashboard URL.

---

## What It Does

- **Health Checks** — verifies the dashboard loads, pages exist, and visual titles match config before running any data tests
- **KPI Validation** — extracts card visual values and compares them against SQL queries or Excel data
- **Table Validation** — right-clicks chart/table visuals → "Show as a table" → scrapes data → compares row-by-row against source
- **Slicer State Validation** — reads current slicer defaults and asserts they match your SQL WHERE clause context
- **HTML Report** — generates a styled report with step-by-step logs and failure screenshots

---

## Project Structure

```
test_playwright combined/
├── config/                     # Framework settings (timeouts, browser, output paths)
│   ├── settings.py             # Section A: framework constants | Section B: credentials
│   └── db_config.py            # Builds SQLAlchemy URI from YAML or settings.py
├── dashboard_configs/          # ← One YAML file per dashboard (your main workspace)
│   ├── _template.yaml          # Template — copy this when onboarding a new dashboard
│   └── public_sales_dashboard.yaml
├── locators/
│   └── pbi_locators.py         # All CSS/JS selectors for Power BI DOM elements
├── pageobjects/
│   └── pbi_dashboard_page.py   # Power BI POM — navigation, extraction, health checks
├── methods/
│   └── dashboard_methods.py    # Orchestration — combines extraction + validation
├── tests/
│   └── dashboard/
│       ├── conftest.py         # Fixtures + HTML report hooks
│       ├── test_dashboard_health.py   # Smoke tests (run first)
│       ├── test_kpi_validation.py     # KPI card validation
│       └── test_table_validation.py  # Table/chart data validation
├── utils/
│   ├── config_loader.py        # YAML loader + config schema validation
│   ├── validation_utils.py     # Number parsing, value comparison, dataset diff
│   ├── db_utils.py             # SQLAlchemy engine + query execution
│   ├── excel_data_utils.py     # Excel/CSV source-of-truth loader
│   ├── encryption_utils.py     # Password encryption/decryption
│   └── logger.py               # Structured logging setup
├── reports/html_reports/       # Generated HTML test reports (gitignored)
├── requirements.txt
└── pytest.ini
```

---

## Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install Playwright browser (Chrome)
playwright install chrome

# 3. Set up environment variables
cp .env.example .env
# Edit .env and set TEST_FRAMEWORK_SECRET_KEY
```

---

## Onboarding a New Dashboard

```bash
# Step 1: Copy the template
cp dashboard_configs/_template.yaml dashboard_configs/my_dashboard.yaml

# Step 2: Fill in the dashboard URL (and SSO credentials in config/settings.py if needed)

# Step 3: Run visual discovery to find all available visual titles
pytest tests/dashboard/test_kpi_validation.py::test_kpi_discover_visuals \
    --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Step 4: Copy the discovered visual titles into your YAML kpi_validations / table_validations

# Step 5: Add SQL queries or Excel source references for each validation entry

# Step 6: Run the full suite
pytest tests/dashboard/ --dashboard-config=dashboard_configs/my_dashboard.yaml -s
```

See [`dashboard_onboarding_guide.md`](dashboard_onboarding_guide.md) for a detailed walkthrough.

---

## Running Tests

```bash
# Full dashboard validation suite
pytest tests/dashboard/ --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Health checks only (fast — run first to catch config issues)
pytest tests/dashboard/test_dashboard_health.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# KPI validation only
pytest tests/dashboard/test_kpi_validation.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Table validation only
pytest tests/dashboard/test_table_validation.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Visible browser (for debugging)
# Edit config/settings.py → set HEADLESS = False, then run normally
```

---

## Report

The HTML validation report is generated at:

```
reports/html_reports/dashboard_validation_report.html
```

Open it in any browser — it includes pass/fail status per visual, step-by-step logs, and failure screenshots.

---

## Supported Dashboard Types

| Type | URL Pattern | Auth |
|---|---|---|
| Publish to Web | `app.powerbi.com/view?r=...` | None (public) |
| Org / Secure Report | `app.powerbi.com/groups/.../reports/...` | Microsoft SSO |
