import hashlib
import secrets

from pwdlib import PasswordHash


password_hasher = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    """Convert a plaintext password into a one-way password hash."""
    return password_hasher.hash(plain_password)


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Check a login password against the hash stored in the database."""
    return password_hasher.verify(plain_password, stored_hash)


def generate_access_token() -> str:
    """Create an unpredictable token that can safely identify one login session."""
    return secrets.token_urlsafe(32)


def hash_access_token(access_token: str) -> str:
    """Create the fixed-length value stored instead of the raw access token."""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()

