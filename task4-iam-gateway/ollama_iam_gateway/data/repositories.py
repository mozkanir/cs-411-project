"""Repository pattern: the only place that builds SQL queries.

Repositories never commit. The service layer decides when a unit of
work is finished and calls ``commit()``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import ApiKey, AuditEvent, Role, User, UserSession


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()  # assigns user.id, raises IntegrityError on duplicates
        return user

    def get(self, user_id: int) -> Optional[User]:
        return self.db.get(User, user_id)

    def get_by_username(self, username: str) -> Optional[User]:
        return self.db.scalar(select(User).where(User.username == username))

    def list_all(self) -> list[User]:
        return list(self.db.scalars(select(User).order_by(User.id)))

    def count_active_admins(self) -> int:
        return self.db.scalar(
            select(func.count()).select_from(User)
            .where(User.role == Role.ADMIN, User.is_active.is_(True))
        ) or 0


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, session: UserSession) -> UserSession:
        self.db.add(session)
        self.db.flush()
        return session

    def get_by_token_hash(self, token_hash: str) -> Optional[UserSession]:
        return self.db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))

    def delete(self, session: UserSession) -> None:
        self.db.delete(session)
        self.db.flush()

    def delete_for_user(self, user_id: int) -> int:
        result = self.db.execute(delete(UserSession).where(UserSession.user_id == user_id))
        return result.rowcount or 0

    def delete_expired(self, now: datetime) -> int:
        result = self.db.execute(delete(UserSession).where(UserSession.expires_at <= now))
        return result.rowcount or 0


class ApiKeyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, key: ApiKey) -> ApiKey:
        self.db.add(key)
        self.db.flush()
        return key

    def get(self, key_id: int) -> Optional[ApiKey]:
        return self.db.get(ApiKey, key_id)

    def get_by_prefix(self, prefix: str) -> Optional[ApiKey]:
        return self.db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))

    def list_for_user(self, user_id: int, include_revoked: bool = False) -> list[ApiKey]:
        stmt = select(ApiKey).where(ApiKey.user_id == user_id)
        if not include_revoked:
            stmt = stmt.where(ApiKey.revoked_at.is_(None))
        return list(self.db.scalars(stmt.order_by(ApiKey.id)))


class AuditRepository:
    """Append-only: there are intentionally no update or delete methods."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, event: AuditEvent) -> AuditEvent:
        self.db.add(event)
        self.db.flush()
        return event

    def list_recent(self, limit: int = 100, offset: int = 0,
                    username: Optional[str] = None,
                    event_type: Optional[str] = None) -> list[AuditEvent]:
        stmt = select(AuditEvent)
        if username:
            stmt = stmt.where(AuditEvent.username == username)
        if event_type:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        stmt = stmt.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        return list(self.db.scalars(stmt.limit(limit).offset(offset)))

    def count(self) -> int:
        return self.db.scalar(select(func.count()).select_from(AuditEvent)) or 0
