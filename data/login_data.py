"""
Wrapper around Excel-based login test data.

Primary data source: testdata/login_testdata.xlsx (sheet 'LoginData').
All other code should import from here, not from excel_utils directly.
"""

from typing import List, Dict
from utils.excel_utils import load_login_testdata


def get_login_test_rows() -> List[Dict[str, str]]:
    """
    Returns all login test rows from the Excel file as a list of dicts.
    Keys per row: scenario, username, password, expected_result
    """
    return load_login_testdata()