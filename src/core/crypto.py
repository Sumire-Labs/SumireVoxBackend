# src/core/crypto.py

import os
import logging
from cryptography.fernet import Fernet, InvalidToken

from src.core.config import IS_PRODUCTION

logger = logging.getLogger(__name__)

_key = os.environ.get("ENCRYPTION_KEY")

if not _key:
    if IS_PRODUCTION:
        raise RuntimeError(
            "ENCRYPTION_KEY environment variable is required in production. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    else:
        logger.warning(
            "ENCRYPTION_KEY not set. Generating a temporary key for development. "
            "DO NOT use this in production!"
        )
        _key = Fernet.generate_key().decode()

_fernet = Fernet(_key.encode())


def encrypt(text: str) -> str:
    """Encrypt a string using Fernet symmetric encryption."""
    if not text:
        return text
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    """
    Decrypt a Fernet-encrypted string.
    Returns None and logs error if decryption fails.
    """
    if not token:
        return token
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error(
            "Failed to decrypt token. This may indicate the ENCRYPTION_KEY has changed "
            "or the data is corrupted. The session will be invalidated."
        )
        return None
    except Exception as e:
        logger.error(f"Unexpected error during decryption: {e}")
        return None
