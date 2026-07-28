import os
from utils.encryption_utils import decrypt_value

# ── Auto-load .env from project root (no-op if python-dotenv is not installed) ─
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)  # never overrides vars already set in the shell
except ImportError:
    pass  # python-dotenv is optional; CI can inject env vars directly


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
HEADLESS = False

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
# Set these in .env (never hardcode here):
#   SSO_USERNAME=test-user@yourorg.onmicrosoft.com
#   SSO_PASSWORD=your_plain_password   (or SSO_PASSWORD_ENC for encrypted)
SSO_USERNAME      = os.getenv("SSO_USERNAME", "")
SSO_PASSWORD_ENC  = os.getenv("SSO_PASSWORD_ENC", "")   # Encrypted token (gAAAAAB...)
SSO_PASSWORD_PLAIN = os.getenv("SSO_PASSWORD", "")       # Plain fallback

def get_sso_password() -> str:
    """Return SSO password. Priority: encrypted token > plain env var > empty."""
    if SSO_PASSWORD_ENC:
        return decrypt_value(SSO_PASSWORD_ENC)
    return SSO_PASSWORD_PLAIN


# ── Source Database — global template (override per-dashboard in the YAML) ────
# Each dashboard YAML config's source_db section takes precedence over these.
# When the YAML uses ${ENV_VAR} syntax the value is resolved from the env at
# runtime, so nothing is ever hardcoded in Python or YAML source.
#
# Set these in .env (or export in shell / CI secrets) — example:
#   DB_DRIVER=mssql+pymssql
#   DB_HOST=myserver.database.windows.net
#   DB_PORT=1433
#   DB_NAME=mydatabase
#   DB_USER=mylogin
#   DB_PASSWORD=MyP@ssword   (or DB_PASSWORD_ENC for an encrypted token)
DB_DRIVER        = os.getenv("DB_DRIVER", "")    # e.g. "mssql+pymssql", "postgresql"
DB_HOST          = os.getenv("DB_HOST",   "")    # e.g. "server.database.windows.net"
DB_PORT          = os.getenv("DB_PORT",   "")    # e.g. "1433", "5432"
DB_NAME          = os.getenv("DB_NAME",   "")    # Database / schema name
DB_USER          = os.getenv("DB_USER",   "")    # Read-only service account username
DB_PASSWORD_ENC  = os.getenv("DB_PASSWORD_ENC", "")  # Encrypted token (gAAAAAB...)
DB_PASSWORD_PLAIN = os.getenv("DB_PASSWORD", "")      # Plain password env var

def get_db_password() -> str:
    """Return DB password. Priority: encrypted token > plain env var > empty."""
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