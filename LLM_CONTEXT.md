# Power BI Dashboard Validation Framework — Context for LLMs

This document serves as a comprehensive context guide for LLMs interacting with this project. It outlines the architecture, file responsibilities, extraction strategies, and configuration schemas of the Power BI validation framework.

## 1. Project Overview
This framework automates the validation of Microsoft Power BI dashboards by comparing data extracted from the dashboard UI (via Playwright) against the source of truth (Database via SQL, or Excel/CSV). 

It handles two types of Power BI embeds:
1. **Publish to Web (PTW)**: Public, unauthenticated dashboards (no iframe).
2. **Org/Secure Reports**: Authenticated via Azure AD SSO (inside an iframe).

## 2. Complete File Inventory & Responsibilities

### Core Framework Files
- **`pageobjects/pbi_dashboard_page.py`**: The main Page Object Model (POM). Handles navigation, visual discovery, card/chart/table data extraction, page switching, and SSO login. Auto-detects the embed mode (PTW vs. Org).
- **`locators/pbi_locators.py`**: Contains CSS/XPath selectors for PBI DOM elements. Defines `PTW_TESTABLE_TYPES` and separates locators into PTW (Mode A) and ORG (Mode B).
- **`methods/dashboard_methods.py`**: High-level orchestration. Bridges the POM, database fetching, and validation utilities into reusable functions like `validate_kpi()` and `validate_table()`.
- **`tests/dashboard/conftest.py`**: Pytest fixtures (`dashboard_page`, `db_engine`, `dashboard_config`) and report generation hooks. Captures STEP markers from logs to build HTML reports.
- **`tests/dashboard/test_kpi_validation.py`**: Contains test functions for KPI card validation and a diagnostic test (`test_kpi_discover_visuals`) to identify testable visuals on a dashboard.
- **`tests/dashboard/test_table_validation.py`**: Contains test functions for Table/Chart data validation and row count sanity checks.

### Utility Files
- **`utils/db_utils.py`**: Database connectivity using SQLAlchemy. Functions: `get_db_engine()`, `fetch_db_data()` (returns Pandas DataFrame), and `fetch_scalar()` (returns single float).
- **`utils/config_loader.py`**: Loads and validates YAML dashboard configurations. Resolves `${ENV_VAR}` syntax for secrets.
- **`utils/validation_utils.py`**: The comparison engine. Includes `parse_pbi_number()` to convert formatted PBI strings (e.g., "$4.2M", "87.5% ▲") into floats, and methods to compare datasets row-by-row with tolerance.
- **`utils/schema_introspector.py`**: Database schema introspection via SQLAlchemy. Used by the discovery script to suggest SQL queries by mapping DB columns to dashboard visuals.
- **`utils/sql_suggester.py` & `utils/llm_sql_generator.py`**: Suggests SQL queries for visuals. `llm_sql_generator.py` uses Azure OpenAI with structured outputs (Pydantic).
- **`utils/report_generator.py`**: Generates rich HTML test reports at the end of a pytest run.
- **`utils/excel_data_utils.py`**: Fallback data source handling (loading Excel/CSV and aggregating columns).
- **`utils/encryption_utils.py`**: Fernet-based password encryption/decryption for storing credentials in config files securely.

### Config Files
- **`config/settings.py`**: Global framework settings (timeouts, browser dimensions, headless mode, SSO credentials, global DB credentials, Azure OpenAI keys).
- **`config/db_config.py`**: Constructs the final SQLAlchemy URI by merging dashboard-specific YAML config with global settings from `settings.py`.

### Scripts
- **`scripts/discover_dashboard.py`**: Auto-discovery CLI. Crawls a Power BI dashboard URL, detects all KPI cards, charts, and slicers, and generates a ready-to-use YAML configuration file. Can optionally connect to a database to auto-suggest SQL queries (Phase B).

---

## 3. Configuration Schema (YAML)
Each dashboard is driven by a YAML configuration file (e.g., `dashboard_configs/my_dashboard.yaml`).

```yaml
dashboard:
  name: "Sales Dashboard"
  url: "https://app.powerbi.com/view?r=..."
  pages: ["Executive Summary"]

source_db: # DB Connection info (overrides global settings)
  driver: "postgresql"
  host: "..."
  database: "..."
  username: "..."
  password: "${DB_PASSWORD}" 

kpi_validations: # Single scalar value comparisons
  - visual_title: "Total Revenue"
    page: "Executive Summary"
    sql_query: "SELECT SUM(revenue) FROM fact_sales"
    tolerance: 0.01

table_validations: # Tabular dataset comparisons
  - visual_title: "Sales by Region"
    page: "Executive Summary"
    sql_query: "SELECT region, SUM(revenue) as total_sales FROM fact_sales GROUP BY region"
    join_keys: ["region"]
    compare_cols: ["total_sales"]
    tolerance: 0.01
```

---

## 4. Chart Extraction Strategies
Power BI restricts DOM access, especially in Publish-to-Web (PTW) mode. The framework uses a multi-pass strategy defined in `pbi_dashboard_page.py`:

**Tier 1: DOM Scraping (Implemented)**
1. **Aria-label parsing**: Scrapes `[aria-label]` from SVG elements (format: "Category. Measure. Value."). Works for standard Bar, Column, Pie, Line, and Treemap charts.
2. **Column/bar rect + axis-tick pairing**: For dense charts where PBI omits full aria-labels. Pairs `column-chart-rect` elements with `axis-tick-text` elements.
3. **SVG text fallback**: Last resort parsing of raw `<text>` elements in the SVG.
4. **DOM Grid Scraping**: Reads `role="gridcell"` directly from the DOM for Table and Matrix visuals.
5. **Show as a table UI flow**: Right-click context menu (works reliably only in Org/Secure mode, as PTW mode disables UI context menus for most visuals).

**Future Implementation Considerations (Tier 2 & 3 Fallbacks)**
For visuals that cannot be scraped from the DOM at all (e.g., Maps, AI Visuals, Python/R scripts, Smart Narratives):
- **Tier 2 (PBI API)**: Planned integration with Power BI REST API (`executeQueries`) using DAX to fetch data directly from the semantic model (requires Azure AD Service Principal).
- **Tier 3 (Source-Only)**: Direct source validation fallback where extraction is skipped entirely, and only the data pipeline (SQL query) is validated for integrity.
