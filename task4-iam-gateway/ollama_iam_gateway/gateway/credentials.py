"""Credential Resolver: turns the Authorization header into an identity.

Only ``Authorization: Bearer <api key>`` is accepted on the Ollama API
(decision: no cookies on the API, so CSRF cannot reach it).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session, sessionmaker

from ..data.models import Role
from ..iam.apikey_service import ApiKeyService


@dataclass(frozen=True)
class Identity:
    user_id: int
    username: str
    role: Role
    key_prefix: str


def extract_bearer(header_value: Optional[str]) -> Optional[str]:
    """Return the token from 'Bearer <token>', or None."""
    if not header_value:
        return None
    scheme, _, token = header_value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def resolve_identity(session_factory: sessionmaker[Session],
                     token: Optional[str]) -> Optional[Identity]:
    """Look up the API key. Blocking (DB) - call it from a worker thread."""
    if not token:
        return None
    with session_factory() as db:
        resolved = ApiKeyService(db).resolve(token)
        if resolved is None:
            return None
        return Identity(resolved.user.id, resolved.user.username,
                        resolved.user.role, resolved.key.prefix)
