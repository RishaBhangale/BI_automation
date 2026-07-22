# Power BI Dashboard Validation Framework — Context for LLMs

This document is a comprehensive context guide for LLMs. It covers architecture, file responsibilities, extraction strategies, and configuration schema of the Power BI validation framework.

---

## 1. Project Overview

This framework automates the validation of Microsoft Power BI dashboards by comparing data extracted from the dashboard UI (via Playwright) against the source of truth (Database via SQL, or Excel/CSV).

It handles two types of Power BI embeds:
1. **Publish to Web (PTW)**: Public, unauthenticated dashboards (no iframe). URL pattern: `app.powerbi.com/view?r=...`
2. **Org/Secure Reports**: Authenticated via Azure AD SSO (inside an iframe). URL pattern: `app.powerbi.com/groups/.../reports/...`

---

## 2. Complete File Inventory & Responsibilities

### Core Framework Files

| File | Responsibility |
|---|---|
| `pageobjects/pbi_dashboard_page.py` | **Main POM** (1900+ lines). Handles navigation, visual discovery, card/chart/table extraction. Auto-detects embed mode (PTW vs. Org). |
| `locators/pbi_locators.py` | CSS/XPath selectors for PBI DOM elements. Defines `PTW_TESTABLE_TYPES`, PTW and ORG selector sets. |
| `methods/dashboard_methods.py` | High-level orchestration. Bridges POM + DB + validation utils. Contains `validate_kpi()`, `validate_chart_data()` (3-tier router), `validate_all_kpis()`, `validate_all_tables()`. |
| `tests/dashboard/conftest.py` | Pytest fixtures: `dashboard_page`, `db_engine`, `pbi_client`, `dashboard_config`. Report generation hooks at session end. |
| `tests/dashboard/test_kpi_validation.py` | KPI card validation tests + `test_kpi_discover_visuals` diagnostic. |
| `tests/dashboard/test_table_validation.py` | Table/chart data validation tests + row count sanity check. |

### Utility Files

| File | Responsibility |
|---|---|
| `utils/db_utils.py` | SQLAlchemy engine creation, `fetch_db_data()` (→ DataFrame), `fetch_scalar()` (→ float). |
| `utils/pbi_api_client.py` | **NEW** — Power BI REST API client. Azure AD OAuth2 (Service Principal). `PBIApiClient.execute_dax()` runs any DAX query and returns a DataFrame. Used for Tier 2 extraction. |
| `utils/config_loader.py` | Loads and validates YAML configs. Resolves `${ENV_VAR}` syntax. |
| `utils/validation_utils.py` | Comparison engine. `parse_pbi_number()` converts PBI display strings ("$4.2M", "87.5% ▲") to floats. Row-by-row dataset comparison with tolerance. |
| `utils/schema_introspector.py` | DB schema introspection via SQLAlchemy. Used by the discovery script to suggest SQL. |
| `utils/sql_suggester.py` / `utils/llm_sql_generator.py` | Rule-based and Azure OpenAI-based SQL query suggestion. |
| `utils/report_generator.py` | Generates rich HTML test reports at pytest session end. |
| `utils/excel_data_utils.py` | Loads Excel/CSV as Pandas DataFrame, handles column aggregation. |
| `utils/encryption_utils.py` | Fernet-based password encryption/decryption for storing credentials safely. |

### Config Files

| File | Responsibility |
|---|---|
| `config/settings.py` | Global framework settings: timeouts, browser config, SSO credentials, DB globals, Azure OpenAI keys, **PBI REST API credentials** (`PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`). |
| `config/db_config.py` | Builds the final SQLAlchemy URI by merging YAML config with global settings. |

### Scripts & Docs

| File | Responsibility |
|---|---|
| `scripts/discover_dashboard.py` | Auto-discovery CLI. Crawls a PBI dashboard URL, classifies all visuals, and generates a YAML config with chart extraction coverage matrix (Tier 1/2/3 hints). |
| `docs/client_onboarding.md` | **NEW** — Production handoff guide. Explains what to ask each client depending on the tier chosen, and what Azure AD setup is required for Tier 2. |

---

## 3. Configuration Schema (YAML)

Each dashboard is driven by a YAML file in `dashboard_configs/`. Based on `_template.yaml`:

```yaml
dashboard:
  name: "Sales Dashboard"
  url: "https://app.powerbi.com/view?r=..."
  pages: ["Executive Summary", "Details"]

source_db:                    # Read-only DB connection
  driver: "postgresql"        # postgresql | mssql+pyodbc | snowflake | mysql+pymysql
  host: "db.company.com"
  port: "5432"
  database: "analytics"
  username: "qa_readonly"
  password: "${DB_PASSWORD}"  # Supports env var substitution

source_excel:                 # Fallback if no DB
  filepath: "testdata/export.xlsx"
  sheet_name: "Sheet1"

# NEW: Tier 2 — optional, for DOM-inaccessible visuals
pbi_api:
  dataset_id: ""              # Power BI Dataset GUID (leave empty to skip Tier 2)

kpi_validations:              # Single scalar value comparisons (KPI cards)
  - visual_title: "Total Revenue"
    page: "Executive Summary"
    sql_query: "SELECT SUM(revenue) FROM fact_sales"
    tolerance: 0.01

table_validations:            # Tabular dataset comparisons (charts, tables)
  - visual_title: "Sales by Region"
    page: "Executive Summary"
    extraction_tier: "auto"   # NEW: "auto" | "dom" | "api" | "source"
    sql_query: "SELECT region, SUM(revenue) as total FROM fact_sales GROUP BY region"
    dax_query: ""             # NEW: DAX query for Tier 2 (from Performance Analyzer)
    join_keys: ["region"]
    compare_cols: ["total"]
    tolerance: 0.01

expected_filters:             # Slicer state assertions (optional)
  - slicer_title: "Year"
    page: "Executive Summary"
    expected_value: "2024"
```

---

## 4. Three-Tier Extraction Architecture

The core of the framework is a three-tier extraction chain defined in `methods/dashboard_methods.py::validate_chart_data()`. Each visual routes through tiers in order based on its `extraction_tier` YAML field.

### Tier 1 — DOM Scraping (Playwright)
**Implemented. Covers ~70% of dashboards.**

| Strategy | Covers | Mechanism |
|---|---|---|
| Pass 1: Aria-label | Bar, Column, Pie, Donut, Treemap, Funnel, Waterfall, Line, Scatter | Scrapes `[aria-label="Category. Measure. Value."]` from SVG |
| Pass 2: Rect+Axis pairing | Dense Bar/Column charts | Pairs `column-chart-rect` values with `axis-tick-text` labels |
| Pass 3: SVG text fallback | Simple visuals | Parses raw `<text>` nodes from SVG |
| DOM Grid Scraping | Table / Matrix visuals | Reads `role="gridcell"` and `role="columnheader"` directly |

**Cannot extract** (falls through to Tier 2/3): Maps, AI Visuals (Decomposition Tree, Key Influencers), Smart Narrative, Q&A, Python/R visuals, Custom AppSource visuals.

### Tier 2 — Power BI REST API (DAX)
**Implemented in `utils/pbi_api_client.py`. Optional. Covers ALL visual types.**

- Authenticates via Azure AD Service Principal (Client Credentials OAuth2 flow).
- Calls `POST /v1.0/myorg/datasets/{dataset_id}/executeQueries` with a DAX query.
- Returns a clean Pandas DataFrame regardless of the visual type.
- Activated only when `pbi_api.dataset_id` is set in YAML AND `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET` env vars are present.

**How to get a DAX query:** Power BI Desktop → View → Performance Analyzer → Start recording → Refresh the visual → "Copy query".

### Tier 3 — Direct Source Validation (SQL/Excel)
**Implemented. The ultimate fallback.**

- Skips all visual extraction entirely.
- Runs the `sql_query` directly against the source DB.
- PASSES if the query returns > 0 rows (data pipeline is healthy).
- FAILS if query returns 0 rows or errors.
- Use when: visual is DOM-inaccessible AND client cannot provide Azure AD credentials.

---

## 5. Database Connection Architecture

1. `config/db_config.py::build_db_uri()` — Constructs SQLAlchemy URI from YAML `source_db` + global `settings.py`
2. `utils/db_utils.py` — `get_db_engine()`, `fetch_scalar()`, `fetch_db_data()`
3. `conftest.py::db_engine` fixture — Session-scoped. Verifies connection on startup. Yields `None` if no DB is configured.

Supported DB drivers: `postgresql`, `mssql+pyodbc`, `snowflake`, `mysql+pymysql`

---

## 6. Credential Management

All credentials follow the same pattern — priority order:

1. Encrypted token (Fernet, via `utils/encryption_utils.py`) stored in `settings.py`
2. Plaintext value in `settings.py` (local dev only, never commit to git)
3. Environment variable (recommended for CI/CD)

**Credential types:**
- SSO: `SSO_USERNAME`, `SSO_PASSWORD_ENC` / `SSO_PASSWORD_PLAIN`
- DB: `DB_DRIVER`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD_ENC`
- PBI API: `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET` (env vars only, no encryption needed as they're already service-level secrets)
- Azure OpenAI: `FOUNDRY_API_KEY`, `FOUNDRY_ENDPOINT`, `FOUNDRY_MODEL`

---

## 7. Test Execution Flow

```
pytest tests/dashboard/ --dashboard-config=dashboard_configs/my_dashboard.yaml
  │
  ├── conftest.py loads YAML → dashboard_config fixture
  ├── conftest.py creates DB engine → db_engine fixture
  ├── conftest.py creates PBI API client (if configured) → pbi_client fixture
  ├── conftest.py opens browser, navigates to PTW URL → dashboard_page fixture
  │
  ├── test_all_kpis()
  │     └── validate_all_kpis() → validate_kpi() × N → compare_single_value()
  │
  └── test_all_tables() (in test_table_validation.py)
        └── validate_all_tables() → validate_chart_data() × N
              ├── Tier 1: dashboard_page.extract_table_data() → compare_datasets()
              ├── Tier 2: pbi_client.execute_dax(dax_query) → compare_datasets()
              └── Tier 3: fetch_db_data(sql_query) → row count check
```

