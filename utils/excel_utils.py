from pathlib import Path
from typing import List, Dict

import pandas as pd

from utils.logger import get_logger
from config.settings import LOG_DIR  # kept for consistency if you log to LOG_DIR later
from utils.encryption_utils import decrypt_value

log = get_logger("excel_utils")

LOGIN_TESTDATA_FILE = Path("testdata") / "login_testdata.xlsx"
LOGIN_SHEET_NAME = "LoginData"
PASSWORD_COLUMN = "password"  # column name in the Excel sheet


def load_login_testdata() -> List[Dict[str, str]]:
    """
    Load login test data from Excel and return a list of dicts.

    Behavior:
    - Reads testdata/login_testdata.xlsx, sheet 'LoginData'.
    - Normalizes columns by stripping whitespace.
    - Converts NaN values to empty strings ("").
    - Casts all values to strings (safe for Playwright .fill()).
    - Transparently decrypts the 'password' column using Fernet if it is encrypted.
      - If the value is not a valid Fernet token (i.e., still plaintext during
        migration), it is returned as-is.

    This allows a smooth migration:
    - Before you encrypt the Excel file, tests continue to work with plaintext.
    - After you run the one-time encryption script, the same code decrypts
      and returns plaintext passwords to the tests.
    """
    if not LOGIN_TESTDATA_FILE.is_file():
        raise FileNotFoundError(
            f"Login testdata Excel not found at {LOGIN_TESTDATA_FILE}"
        )

    log.info(
        f"Loading login test data from {LOGIN_TESTDATA_FILE} "
        f"(sheet={LOGIN_SHEET_NAME})"
    )

    df = pd.read_excel(LOGIN_TESTDATA_FILE, sheet_name=LOGIN_SHEET_NAME)

    # Normalize columns and values
    df.columns = [c.strip() for c in df.columns]
    df = df.fillna("")
    df = df.astype(str)

    records: List[Dict[str, str]] = df.to_dict(orient="records")

    # Decrypt password field per row (backwards-compatible)
    if PASSWORD_COLUMN in df.columns:
        for row in records:
            raw_pwd = row.get(PASSWORD_COLUMN, "")
            if not raw_pwd:
                continue

            try:
                decrypted = decrypt_value(raw_pwd)
                row[PASSWORD_COLUMN] = decrypted
            except Exception as exc:
                # Two main reasons we land here:
                # 1) The value is still plaintext (not a Fernet token) during
                #    the migration period.
                # 2) The TEST_FRAMEWORK_SECRET_KEY is wrong or missing, in which
                #    case decrypt_value will already have raised a clear error.
                #
                # For case (1) we keep the original value; for case (2) the
                # decrypt_value implementation should fail fast.
                log.debug(
                    "Password value could not be decrypted, using raw value. "
                    f"Value starts with '{raw_pwd[:6]}' – error: {exc}"
                )
                # leave row[PASSWORD_COLUMN] unchanged

    log.debug(f"Loaded {len(records)} login test rows from Excel")
    return records