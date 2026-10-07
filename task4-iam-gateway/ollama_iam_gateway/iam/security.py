"""Security utilities.

Step 1: password hashing only. Token generation, key hashing and CSRF
helpers are added in step 2.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id with the library's recommended parameters (RFC 9106 low-memory
# profile). It is deliberately slow to make offline brute force expensive.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2id hash string (contains algorithm, params and salt)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if ``password`` matches ``password_hash``. Never raises."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True if the hash was made with older/weaker parameters."""
    return _hasher.check_needs_rehash(password_hash)
