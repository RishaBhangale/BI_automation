import os
from utils.encryption_utils import decrypt_value


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — FRAMEWORK SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
# These values apply to every dashboard and should NOT be changed per project.
# If you need to tune a timeout for one specific dashboard, do it in the YAML
# config (under dashboard.settings), not here.
# ══════════════════════════════════════════════════════════════════════════════

# ── Timeouts (milliseconds) ───────────────────────────────────────────────────
# How long to wait for the PBI report canvas to finish rendering after navigate.
# Real dashboards with large datasets can take 30–90 seconds.
PBI_RENDER_TIMEOUT   = 90_000

# How long to wait after switching to a new page tab before asserting visuals.
PBI_PAGE_SWITCH_WAIT = 5_000

# General-purpose Playwright element timeout.
DEFAULT_TIMEOUT      = 30_000
NAVIGATION_TIMEOUT   = 60_000
ELEMENT_TIMEOUT      = 10_000

# ── Browser configuration ─────────────────────────────────────────────────────
# Uses the locally-installed Chrome browser (more stable than Chromium for PBI).
BROWSER_CHANNEL = "chrome"

# True = no browser window (recommended for CI). False = visible window (local dev).
HEADLESS = True

# Dashboard viewport — wider than default so PBI renders all visuals correctly.
BROWSER_WIDTH  = 1600
BROWSER_HEIGHT = 900

# ms delay between Playwright actions (0 = fastest, increase for debugging).
SLOW_MO = 0

# ── Output paths ──────────────────────────────────────────────────────────────
# All paths are relative to the project root (the directory containing pytest.ini).
SCREENSHOT_DIR = "screenshots"
LOG_DIR        = "logs"
REPORT_DIR     = "reports/html_reports"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — PER-PROJECT CREDENTIALS
# ══════════════════════════════════════════════════════════════════════════════
# These values MUST be filled in for each new client dashboard.
# Use encrypted tokens (see utils/encryption_utils.py) for passwords — never
# store plain-text passwords in this file in a shared/CI environment.
#
# How to encrypt a password:
#   1.  python -c "from utils.encryption_utils import generate_key; generate_key()"
#   2.  Set TEST_FRAMEWORK_SECRET_KEY env var (copy from step 1)
#   3.  python -c "from utils.encryption_utils import encrypt_value; print(encrypt_value('your_password'))"
#   4.  Paste the gAAAAAB... token as *_ENC below.
# ══════════════════════════════════════════════════════════════════════════════

# ── Microsoft SSO (Azure AD) — for org/secure Power BI dashboards ─────────────
# Leave blank for "Publish to Web" public dashboards (no auth required).
SSO_USERNAME      = ""   # e.g. "test-user@yourorg.onmicrosoft.com"
SSO_PASSWORD_ENC  = ""   # Encrypted password token (gAAAAAB...)
SSO_PASSWORD_PLAIN = ""  # Plaintext fallback — local dev ONLY, never in CI

def get_sso_password() -> str:
    """Return SSO password. Priority: encrypted token > plaintext fallback > empty."""
    if SSO_PASSWORD_ENC:
        return decrypt_value(SSO_PASSWORD_ENC)
    return SSO_PASSWORD_PLAIN


# ── Source Database — global template (override per-dashboard in the YAML) ────
# These are the global defaults. Each dashboard YAML config's source_db section
# takes precedence over these when running a specific dashboard.
DB_DRIVER        = ""   # e.g. "mssql+pyodbc", "postgresql", "snowflake"
DB_HOST          = ""   # e.g. "db.internal.company.com"
DB_PORT          = ""   # e.g. "1433", "5432"
DB_NAME          = ""   # Database / schema name
DB_USER          = ""   # Read-only service account username
DB_PASSWORD_ENC  = ""   # Encrypted password token
DB_PASSWORD_PLAIN = ""  # Plaintext fallback — local dev ONLY

def get_db_password() -> str:
    """Return DB password. Priority: encrypted token > plaintext fallback > empty."""
    if DB_PASSWORD_ENC:
        return decrypt_value(DB_PASSWORD_ENC)
    return DB_PASSWORD_PLAIN


# ── Azure OpenAI / Foundry Credentials ────────────────────────────────────────
FOUNDRY_API_KEY     = os.getenv("FOUNDRY_API_KEY", "")
FOUNDRY_ENDPOINT    = os.getenv("FOUNDRY_ENDPOINT", "")
FOUNDRY_MODEL       = os.getenv("FOUNDRY_MODEL", "gpt-5.2-chat")
FOUNDRY_API_VERSION = os.getenv("FOUNDRY_API_VERSION", "2024-12-01-preview")


# ── Power BI REST API — Tier 2 extraction (optional) ──────────────────────────
# Required ONLY when your dashboard contains visuals that cannot be scraped from
# the browser DOM (Maps, AI visuals, Python/R scripts, Custom AppSource visuals).
#
# How to obtain these values:
#   1. Client IT creates an Azure AD App Registration.
#   2. The App's Service Principal is added as a Viewer in the PBI workspace.
#   3. PBI tenant admin enables "Allow service principals to use Power BI APIs".
#   4. The client provides Tenant ID, Client ID, Client Secret, and Dataset ID.
#
# Set these via environment variables (recommended for CI) or fill in directly
# for local development. Never commit a real client_secret to git.
#
# The Dataset ID is per-dashboard — set it in the YAML config under pbi_api.dataset_id.
# These three values are global (shared across all dashboards run from this machine).
PBI_TENANT_ID     = os.getenv("PBI_TENANT_ID", "")    # Azure AD Tenant ID (GUID)
PBI_CLIENT_ID     = os.getenv("PBI_CLIENT_ID", "")    # App Registration Client ID (GUID)
PBI_CLIENT_SECRET = os.getenv("PBI_CLIENT_SECRET", "") # App Registration Client Secret




