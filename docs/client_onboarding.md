# Client Onboarding Guide — Power BI Dashboard Validation Framework

This guide explains exactly what information a client needs to provide, and what setup is required on your team's side, to onboard a new Power BI dashboard into the validation framework.

---

## Choose Your Tier

The framework supports three tiers of validation. Choose the appropriate tier based on what the client can provide and the complexity of their dashboard.

| Tier | What gets validated | What the client provides | Azure AD setup required? |
|---|---|---|---|
| **Tier 1** (DOM) | KPI cards + most charts | PTW link + DB/Excel source | ❌ No |
| **Tier 2** (API) | ALL visuals incl. Maps, AI, Custom | PTW link + DB/Excel + Azure AD creds | ✅ Yes |
| **Tier 3** (Source) | Data pipeline health only | PTW link + DB/Excel | ❌ No |

> **Start with Tier 1.** Run `discover_dashboard.py` first to see which visuals need Tier 2.

---

## Tier 1 — DOM Scraping (Minimum Setup)

Covers ~70% of common dashboards. No Azure AD required.

### What the client must provide:

| Item | Example | Where to find it |
|---|---|---|
| **PTW Link** | `https://app.powerbi.com/view?r=eyJr...` | PBI Service → File → Publish to web → embed URL |
| **DB host** | `db.company.com` | IT / DBA team |
| **DB port** | `5432` | IT / DBA team |
| **DB name** | `analytics_prod` | IT / DBA team |
| **DB username** | `qa_readonly` | Ask client to create a read-only service account |
| **DB password** | `••••••••` | Client provides securely (do not share over email) |
| **DB driver** | `postgresql` / `mssql+pyodbc` / `snowflake` | Match to DB type |

OR instead of DB, an **Excel/CSV export** of the source data.

### Your team's setup:

1. Copy `dashboard_configs/_template.yaml` to `dashboard_configs/<client_name>.yaml`
2. Fill in `dashboard.url`, `dashboard.pages`, and `source_db` fields
3. Run auto-discovery:
   ```bash
   python scripts/discover_dashboard.py \
       "https://app.powerbi.com/view?r=..." \
       --name "Client Dashboard Name" \
       --output dashboard_configs/client_name.yaml \
       --db-uri "postgresql://qa_readonly:password@db.company.com:5432/analytics_prod"
   ```
4. Review and tweak the generated YAML (SQL queries, join_keys, compare_cols)
5. Run tests:
   ```bash
   pytest tests/dashboard/ --dashboard-config=dashboard_configs/client_name.yaml
   ```

---

## Tier 2 — Power BI REST API (Full Coverage)

Use this when `discover_dashboard.py` reports visuals classified as:
- `Map` / `Filled map` / `Azure map` / `Shape map`
- `Decomposition tree` / `Key influencers`
- `Smart narrative` / `Q&A visual`
- `Python visual` / `R visual`
- Any Custom AppSource visual

### What the client must provide (in addition to Tier 1):

| Item | Example | Where to find it |
|---|---|---|
| **Tenant ID** | `12345678-abcd-...` | Azure Portal → Azure Active Directory → Tenant properties |
| **Client ID** | `87654321-efgh-...` | Azure Portal → App Registrations → your app → Application (client) ID |
| **Client Secret** | `abc~DEF...` | Azure Portal → App Registrations → Certificates & secrets → New client secret |
| **Dataset ID** | `aabbcc00-...` | PBI Service → Datasets → Settings → copy GUID from URL |

### Client IT must do (one-time, ~30 minutes):

1. **Create an App Registration** in Azure AD (Azure Portal → App registrations → New registration)
2. **Create a Client Secret** on the app (Certificates & secrets → New client secret → copy the value immediately)
3. **Add the App as a Viewer** in the Power BI workspace (PBI Service → Workspace → Access → paste the app name)
4. **Enable the tenant setting**: PBI Admin portal → Tenant settings → Developer settings → "Allow service principals to use Power BI APIs" → Enable

### Your team's setup:

1. Add credentials to `.env` (do NOT commit to git):
   ```env
   PBI_TENANT_ID=12345678-abcd-efgh-ijkl-123456789012
   PBI_CLIENT_ID=87654321-mnop-qrst-uvwx-987654321098
   PBI_CLIENT_SECRET=abc~DEFGHIJKlmnopqrstuvwxyz12345
   ```
2. Add `dataset_id` to the YAML config:
   ```yaml
   pbi_api:
     dataset_id: "aabbcc00-1234-5678-9abc-def012345678"
   ```
3. Add `dax_query` to the unreadable visual entries. Get the DAX from **Performance Analyzer**:
   - Open the dashboard in Power BI Desktop
   - View → Performance Analyzer → Start recording
   - Refresh the visual → Click "Copy query"
   - Paste into the YAML `dax_query` field
4. Set `extraction_tier: "api"` on those visual entries in the YAML

---

## Tier 3 — Source-Only Validation (Fallback)

Use this when a visual cannot be scraped (Tier 1 fails) AND the client cannot provide Azure AD credentials (Tier 2 unavailable).

This validates that the **data pipeline** feeding the visual is healthy — the SQL query runs and returns the expected data. The visual's rendering on-screen is NOT verified.

### YAML configuration:

```yaml
table_validations:
  - visual_title: "AI Insights"
    page: "Insights"
    extraction_tier: "source"       # Skip DOM and API
    sql_query: "SELECT * FROM ai_insights_summary WHERE month = '2024-06'"
    join_keys: []
    compare_cols: []
    tolerance: 0.01
```

---

## Quick Reference: What to Ask the Client

**Minimum (Tier 1):**
```
1. Power BI "Publish to Web" embed URL
2. Read-only database credentials (host, port, db name, username, password)
   OR an Excel/CSV export of the source data
```

**Full Coverage (Tier 2, in addition to above):**
```
3. Azure AD Tenant ID
4. App Registration Client ID
5. App Registration Client Secret
6. Power BI Dataset (Semantic Model) GUID
7. Client IT to complete the 4 Azure AD setup steps above
```

---

## Security Notes

- **Never store credentials in YAML files** that are committed to git. Use environment variables or the encrypted token mechanism (`utils/encryption_utils.py`).
- The DB account should be **read-only**. Never use admin or write-enabled accounts.
- The Azure AD Client Secret should be **scoped to the minimum required** — Power BI Viewer access only.
- Rotate the Client Secret on the same schedule as your other service account secrets.
