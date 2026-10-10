"""Security utilities: password hashing, random tokens, API keys.

Passwords  -> argon2id (slow on purpose: users pick guessable passwords).
Tokens/keys -> sha256 (they are 256-bit random values, so a slow hash adds
               nothing but latency on every request).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id with the library's recommended parameters (RFC 9106 low-memory profile).
_hasher = PasswordHasher()

# Used when the username does not exist, so a failed login takes the same
# time whether or not the user exists (prevents user enumeration by timing).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")

API_KEY_SCHEME = "olg"  # "Ollama Gateway"


# ---- passwords ---------------------------------------------------------
def hash_password(password: str) -> str:
    """Return an argon2id hash string (contains algorithm, params and salt)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if ``password`` matches ``password_hash``. Never raises."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    """Spend the same time as a real check. Result is ignored."""
    verify_password(_DUMMY_HASH, password)


def password_needs_rehash(password_hash: str) -> bool:
    """True if the hash was made with older/weaker parameters."""
    return _hasher.check_needs_rehash(password_hash)


# ---- random tokens -----------------------------------------------------
def generate_token(nbytes: int = 32) -> str:
    """URL-safe random token (session cookie value, CSRF token)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """sha256 hex digest; what we store instead of the raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---- API keys ----------------------------------------------------------
def generate_api_key() -> tuple[str, str]:
    """Return ``(full_key, prefix)``.

    Format: ``olg_<8 hex prefix>_<random secret>``. The prefix is stored in
    clear text to find the row quickly and to show the key in the UI
    (``olg_1a2b3c4d_...``); the full key is only stored as a sha256 hash.
    """
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"{API_KEY_SCHEME}_{prefix}_{secret}", prefix


def parse_api_key_prefix(raw_key: str) -> Optional[str]:
    """Extract the prefix from a raw key, or None if the format is wrong."""
    parts = (raw_key or "").split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_SCHEME:
        return None
    prefix, secret = parts[1], parts[2]
    if len(prefix) != 8 or not all(c in "0123456789abcdef" for c in prefix) or len(secret) < 20:
        return None
    return prefix
