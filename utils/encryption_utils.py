import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_ENV_KEY_NAME = "TEST_FRAMEWORK_SECRET_KEY"


class EncryptionKeyMissing(RuntimeError):
    """Raised when the TEST_FRAMEWORK_SECRET_KEY env var is not set."""


def _get_raw_key() -> str:
    key = os.getenv(_ENV_KEY_NAME)
    if not key:
        raise EncryptionKeyMissing(
            f"{_ENV_KEY_NAME} environment variable is not set. "
            f"Cannot decrypt login test data."
        )
    return key


def get_fernet() -> Fernet:
    """
    Build and return a Fernet instance from the env var key.
    """
    key = _get_raw_key()
    # Fernet expects bytes
    return Fernet(key.encode("utf-8"))


def encrypt_value(plaintext: Optional[str]) -> str:
    """
    Encrypt a single string value and return a URL-safe token.
    Empty / None values are returned as empty string.
    """
    if not plaintext:
        return ""
    f = get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_value(token: Optional[str]) -> str:
    """
    Decrypt a token and return the original string.
    Empty tokens are returned as empty string.
    """
    if not token:
        return ""
    f = get_fernet()
    try:
        plaintext = f.decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        # Optional: log here using your logger before re-raising
        raise RuntimeError("Failed to decrypt test data value") from exc
    return plaintext.decode("utf-8")