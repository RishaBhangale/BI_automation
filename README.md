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

## Onboarding a New Dashboard (Automated)

Instead of manually listing visual titles and mapping columns, you can use the **Visual Auto-Discovery Engine** to crawl the dashboard, map pages, identify slicer states, introspect the database schema, and auto-suggest SQL queries using Azure OpenAI.

```bash
# 1. Run the auto-discovery engine
python3 scripts/discover_dashboard.py "https://app.powerbi.com/view?r=..." \
    --name "My Dashboard" \
    --output dashboard_configs/my_dashboard.yaml \
    --db-uri "sqlite:///path/to/db.sqlite"
```

### Options:
- `--db-uri`: Optional SQLAlchemy database connection string. If provided, the database schema will be introspected.
- `--headless`: Run the browser headlessly (defaults to headless). Pass `--no-headless` to watch it crawl.
- `--skip-headers`: Skip right-clicking charts/tables to extract table schema (saves execution time).

### LLM SQL Auto-Suggestion:
If you supply Azure OpenAI credentials in your `.env` file, the discovery engine will introspect your database schema and automatically generate candidates for your validation SQL queries:

```ini
FOUNDRY_API_KEY=your_azure_api_key
FOUNDRY_ENDPOINT=https://your-resource.openai.azure.com/
FOUNDRY_MODEL=gpt-5.2-chat
FOUNDRY_API_VERSION=2024-12-01-preview
```

---

## Running Tests

Once the auto-discovery script has generated the YAML config:

1. Open the generated YAML (`dashboard_configs/my_dashboard.yaml`) and review the auto-generated SQL queries (particularly those marked `⚠️ MEDIUM` confidence).
2. Edit database credentials/passwords in your `.env` or YAML.
3. Run the validation suite:

```bash
# Full dashboard validation suite
pytest tests/dashboard/ --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Health checks only (fast — run first to catch config issues)
pytest tests/dashboard/test_dashboard_health.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# KPI validation only
pytest tests/dashboard/test_kpi_validation.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s

# Table validation only
pytest tests/dashboard/test_table_validation.py --dashboard-config=dashboard_configs/my_dashboard.yaml -s
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
