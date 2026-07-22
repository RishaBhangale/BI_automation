"""
pbi_api_client.py — Power BI REST API client for executing DAX queries.

This module provides Tier 2 extraction — querying the Power BI semantic model
(dataset) directly via the ExecuteQueries REST API endpoint. This bypasses the
browser DOM entirely and can access data from ANY visual type, including Maps,
AI visuals, Python/R scripts, Custom AppSource visuals, and any other visual
that cannot be scraped from the browser.

Authentication uses the Azure AD Client Credentials (Service Principal) flow.
No user interaction is required — suitable for automated CI/CD pipelines.

Prerequisites
─────────────
1. Client IT registers an Azure AD App (App Registration).
2. The Service Principal is added as a Viewer (or higher) in the Power BI workspace.
3. The Power BI tenant admin enables "Allow service principals to use Power BI APIs"
   in the Power BI Admin portal (Tenant settings).
4. The client provides:
     - Tenant ID       (Azure AD → Tenant properties → Tenant ID)
     - Client ID       (Azure AD → App registrations → App ID)
     - Client Secret   (Azure AD → App registrations → Certificates & secrets)
     - Dataset ID      (Power BI Service → Dataset settings → URL contains datasets/{id})

Usage
─────
    from utils.pbi_api_client import PBIApiClient

    client = PBIApiClient(tenant_id, client_id, client_secret, dataset_id)
    if client.test_connection():
        df = client.execute_dax("EVALUATE SUMMARIZECOLUMNS('Region'[Name], 'Sales'[Revenue])")

Dependencies
────────────
    pip install msal requests pandas
    (msal and requests are added to requirements.txt)
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger("pbi_api_client")


class PBIApiError(Exception):
    """Raised when the Power BI API returns an error response."""


class PBIApiClient:
    """
    Client for the Power BI REST API (ExecuteQueries endpoint).

    All interaction is via the Power BI REST API v1.0. Authentication uses
    Azure AD Service Principal (client_credentials OAuth2 flow) — no user
    sign-in required. Suitable for CI/CD pipelines.

    Args:
        tenant_id:     Azure Active Directory Tenant ID (GUID).
        client_id:     Azure AD App Registration Client ID (GUID).
        client_secret: Azure AD App Registration Client Secret (plaintext).
        dataset_id:    Power BI Dataset (Semantic Model) GUID.
                       Find it in the Power BI Service:
                         Dataset settings → URL contains datasets/{id}
    """

    # OAuth2 token endpoint
    _TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    # Power BI API scope
    _PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

    # Power BI ExecuteQueries endpoint
    _EXECUTE_QUERIES_URL_TEMPLATE = (
        "https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/executeQueries"
    )

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        dataset_id: str,
    ) -> None:
        self._tenant_id    = tenant_id
        self._client_id    = client_id
        self._client_secret = client_secret
        self._dataset_id   = dataset_id
        self._access_token: Optional[str] = None

    # ── Authentication ─────────────────────────────────────────────────────

    def authenticate(self) -> str:
        """
        Obtain an OAuth2 bearer token from Azure AD using the
        Client Credentials flow (Service Principal, no user interaction).

        Returns:
            The access token string.

        Raises:
            PBIApiError: If the token request fails.
        """
        url = self._TOKEN_URL_TEMPLATE.format(tenant_id=self._tenant_id)
        payload = {
            "grant_type":    "client_credentials",
            "client_id":     self._client_id,
            "client_secret": self._client_secret,
            "scope":         self._PBI_SCOPE,
        }

        log.info("Requesting Azure AD access token for Power BI API...")
        resp = requests.post(url, data=payload, timeout=30)

        if resp.status_code != 200:
            raise PBIApiError(
                f"Azure AD token request failed [{resp.status_code}]: {resp.text}"
            )

        token_data = resp.json()
        self._access_token = token_data.get("access_token", "")

        if not self._access_token:
            raise PBIApiError(
                f"Azure AD token response did not contain access_token: {token_data}"
            )

        log.info("Azure AD access token obtained successfully.")
        return self._access_token

    def _get_headers(self) -> dict:
        """Return authorization headers, authenticating first if needed."""
        if not self._access_token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
        }

    # ── DAX Query Execution ────────────────────────────────────────────────

    def execute_dax(self, dax_query: str) -> pd.DataFrame:
        """
        Execute a DAX query against the Power BI dataset and return results
        as a pandas DataFrame.

        The query must be a valid DAX expression that returns a table, e.g.:
            EVALUATE SUMMARIZECOLUMNS('Region'[Name], "Revenue", SUM('Sales'[Revenue]))
            EVALUATE VALUES('Date'[Year])
            EVALUATE ROW("Total", SUM('Sales'[Revenue]))

        How to get a DAX query for a visual:
            1. Open the dashboard in Power BI Desktop (if you have access).
            2. Open Performance Analyzer (View → Performance Analyzer).
            3. Click "Start recording", then refresh each visual.
            4. Click "Copy query" next to the visual. That's the exact DAX
               query Power BI uses to populate the visual.

        Args:
            dax_query: A DAX query string beginning with EVALUATE.

        Returns:
            A pandas DataFrame with the query results.
            Returns an empty DataFrame if the query returns no rows.

        Raises:
            PBIApiError: If the API returns an error.
        """
        url = self._EXECUTE_QUERIES_URL_TEMPLATE.format(dataset_id=self._dataset_id)
        body = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": True},
        }

        log.info(
            f"Executing DAX query on dataset '{self._dataset_id}': "
            f"{dax_query[:120].strip()}{'...' if len(dax_query) > 120 else ''}"
        )

        resp = requests.post(
            url,
            headers=self._get_headers(),
            json=body,
            timeout=60,
        )

        # Handle token expiry: re-authenticate once and retry
        if resp.status_code == 401:
            log.warning("Access token expired — re-authenticating...")
            self._access_token = None
            resp = requests.post(
                url,
                headers=self._get_headers(),
                json=body,
                timeout=60,
            )

        if resp.status_code != 200:
            raise PBIApiError(
                f"ExecuteQueries API failed [{resp.status_code}]: {resp.text}"
            )

        data = resp.json()

        # Navigate the response structure:
        # { "results": [{ "tables": [{ "rows": [...] }] }] }
        try:
            results = data.get("results", [])
            if not results:
                log.warning("DAX query returned no results block.")
                return pd.DataFrame()

            tables = results[0].get("tables", [])
            if not tables:
                log.warning("DAX query returned no tables in results.")
                return pd.DataFrame()

            rows = tables[0].get("rows", [])
            if not rows:
                log.info("DAX query returned 0 rows.")
                return pd.DataFrame()

            df = pd.DataFrame(rows)

            # PBI API prefixes column names with the table name, e.g.
            # "[Region]" or "Region[Name]". Strip the table prefix for clean
            # column names that match the YAML join_keys / compare_cols.
            df.columns = [_clean_dax_column_name(c) for c in df.columns]

            log.info(
                f"DAX query returned {len(df)} rows, {len(df.columns)} columns: "
                f"{list(df.columns)}"
            )
            return df

        except (KeyError, IndexError, TypeError) as exc:
            raise PBIApiError(
                f"Unexpected DAX response structure: {exc}\nResponse: {data}"
            ) from exc

    # ── Health Check ───────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """
        Verify API access by executing a trivial DAX query (EVALUATE ROW).

        Returns:
            True if the connection and authentication are working.
            False if any error occurs (logged as a warning).
        """
        try:
            df = self.execute_dax('EVALUATE ROW("test", 1)')
            log.info("Power BI API connection verified successfully.")
            return not df.empty or True  # Empty frame is still a success (0-row tables)
        except PBIApiError as exc:
            log.warning(f"Power BI API connection test failed: {exc}")
            return False
        except Exception as exc:
            log.warning(f"Power BI API connection test error: {exc}")
            return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean_dax_column_name(raw: str) -> str:
    """
    Normalise a DAX response column name to a simple, lowercase string.

    Examples:
        "[Region]"              → "region"
        "Region[Name]"          → "name"
        "Sales[Total Revenue]"  → "total revenue"
        "Sum of Revenue"        → "sum of revenue"
    """
    # Strip wrapping brackets: "[Name]" → "Name"
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1].strip().lower()

    # Strip table prefix: "Table[Column]" → "Column"
    if "[" in raw and raw.endswith("]"):
        return raw.split("[", 1)[1].rstrip("]").strip().lower()

    return raw.strip().lower()
