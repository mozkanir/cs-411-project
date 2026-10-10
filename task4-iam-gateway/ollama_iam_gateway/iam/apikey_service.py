"""API keys for programmatic access (FR7, FR8).

Implements steps 1-3 of the "Authorized API Request" sequence (05-seq-proxy).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..data.models import ApiKey, AuditEventType, Role, User, utcnow
from ..data.repositories import ApiKeyRepository
from . import security
from .audit_service import AuditService
from .errors import NotFound, ValidationError

MAX_ACTIVE_KEYS_PER_USER = 10
_TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass
class NewApiKey:
    raw_key: str     # shown to the user exactly once
    key: ApiKey


@dataclass
class ResolvedKey:
    key: ApiKey
    user: User


class ApiKeyService:
    def __init__(self, db: Session, clock: Callable = utcnow) -> None:
        self.db = db
        self.clock = clock
        self.repo = ApiKeyRepository(db)
        self.audit = AuditService(db, clock)

    def create(self, user: User, name: str, client_ip: Optional[str] = None) -> NewApiKey:
        name = (name or "").strip()
        if not 1 <= len(name) <= 64:
            raise ValidationError("Key name must be 1-64 characters.")
        if self.repo.count_active_for_user(user.id) >= MAX_ACTIVE_KEYS_PER_USER:
            raise ValidationError(
                f"You can have at most {MAX_ACTIVE_KEYS_PER_USER} active keys. Revoke one first.")
        raw, prefix = security.generate_api_key()
        key = self.repo.add(ApiKey(user_id=user.id, name=name, prefix=prefix,
                                   key_hash=security.hash_token(raw),
                                   created_at=self.clock()))
        self.audit.log(AuditEventType.APIKEY_CREATED, user=user, client_ip=client_ip,
                       detail=f"key '{name}' (olg_{prefix}_...)", commit=False)
        self.db.commit()
        return NewApiKey(raw, key)

    def list_for_user(self, user: User, include_revoked: bool = False) -> list[ApiKey]:
        return self.repo.list_for_user(user.id, include_revoked)

    def revoke(self, actor: User, key_id: int, client_ip: Optional[str] = None) -> ApiKey:
        """Owners can revoke their own keys; admins can revoke any key."""
        key = self.repo.get(key_id)
        if key is None:
            raise NotFound("API key not found.")
        if key.user_id != actor.id and actor.role != Role.ADMIN:
            raise NotFound("API key not found.")  # do not reveal other users' keys
        if key.revoked_at is None:
            key.revoked_at = self.clock()
            self.audit.log(AuditEventType.APIKEY_REVOKED, user=actor, client_ip=client_ip,
                           detail=f"key '{key.name}' (olg_{key.prefix}_...) "
                                  f"of user '{key.user.username}'", commit=False)
            self.db.commit()
        return key

    def resolve(self, raw_key: Optional[str]) -> Optional[ResolvedKey]:
        """Turn a Bearer token into (key, user), or None if not valid.

        Checks: format, existence, hash match (constant time), not revoked,
        owner active. Does not write an audit row: the caller (gateway)
        knows the request context and logs AUTH_FAILED itself.
        """
        prefix = security.parse_api_key_prefix(raw_key or "")
        if prefix is None:
            return None
        key = self.repo.get_by_prefix(prefix)
        if key is None:
            return None
        if not security.constant_time_equals(key.key_hash, security.hash_token(raw_key)):
            return None
        if key.revoked_at is not None or not key.user.is_active:
            return None
        now = self.clock()
        if key.last_used_at is None or now - key.last_used_at >= _TOUCH_INTERVAL:
            key.last_used_at = now
            self.db.commit()
        return ResolvedKey(key, key.user)

