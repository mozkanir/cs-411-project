"""Domain / data model (see docs/diagrams/03-datamodel).

Datetimes are stored as naive UTC because SQLite has no timezone type.
Always create them with :func:`utcnow`.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Current time as naive UTC (the format stored in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    """Hierarchical roles: VIEWER < USER < ADMIN."""

    VIEWER = "viewer"
    USER = "user"
    ADMIN = "admin"


class AuditEventType(str, enum.Enum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    APIKEY_CREATED = "APIKEY_CREATED"
    APIKEY_REVOKED = "APIKEY_REVOKED"
    AUTH_FAILED = "AUTH_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    PROXY_REQUEST = "PROXY_REQUEST"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=16,
               values_callable=lambda e: [m.value for m in e])
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User {self.username!r} role={self.role.value} active={self.is_active}>"


class UserSession(Base):
    """Browser login session. Only the sha256 of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiKey(Base):
    """API key for programmatic access. Only the sha256 of the key is stored."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    user: Mapped[User] = relationship(back_populates="api_keys")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class AuditEvent(Base):
    """Append-only audit record. ``username`` is copied so the record
    stays readable even if the user row changes later."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None)
    username: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[Optional[str]] = mapped_column(String(8), default=None)
    path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    model: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    status_code: Mapped[Optional[int]] = mapped_column(default=None)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    detail: Mapped[Optional[str]] = mapped_column(Text, default=None)
