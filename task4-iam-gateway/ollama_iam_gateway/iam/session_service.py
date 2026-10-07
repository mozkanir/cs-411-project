"""Server-side browser sessions (FR2, decision D2).

The cookie holds a random token; the database stores only its sha256.
A session ends when:
  * the user logs out (row deleted), or
  * it is idle longer than ``session_idle_minutes``, or
  * it is older than ``session_max_hours`` (absolute limit), or
  * the user is disabled (all rows deleted by UserService).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..config import Settings
from ..data.models import User, UserSession, utcnow
from ..data.repositories import SessionRepository
from . import security

# Do not write last_seen_at on every page view; once a minute is enough.
_TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass
class NewSession:
    raw_token: str          # goes into the cookie, never stored
    session: UserSession


class SessionService:
    def __init__(self, db: Session, settings: Settings, clock: Callable = utcnow) -> None:
        self.db = db
        self.settings = settings
        self.repo = SessionRepository(db)
        self.clock = clock

    def create(self, user: User, commit: bool = True) -> NewSession:
        now = self.clock()
        raw = security.generate_token()
        session = self.repo.add(UserSession(
            user_id=user.id,
            token_hash=security.hash_token(raw),
            csrf_token=security.generate_token(),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=self.settings.session_max_hours),
        ))
        if commit:
            self.db.commit()
        return NewSession(raw, session)

    def validate(self, raw_token: Optional[str]) -> Optional[UserSession]:
        """Return the live session for this cookie value, or None."""
        if not raw_token:
            return None
        session = self.repo.get_by_token_hash(security.hash_token(raw_token))
        if session is None:
            return None
        now = self.clock()
        idle_limit = timedelta(minutes=self.settings.session_idle_minutes)
        if (session.expires_at <= now
                or session.last_seen_at + idle_limit <= now
                or not session.user.is_active):
            self.repo.delete(session)
            self.db.commit()
            return None
        if now - session.last_seen_at >= _TOUCH_INTERVAL:
            session.last_seen_at = now
            self.db.commit()
        return session

    def destroy(self, raw_token: Optional[str]) -> Optional[User]:
        """Delete the session; returns its user (for the audit log)."""
        if not raw_token:
            return None
        session = self.repo.get_by_token_hash(security.hash_token(raw_token))
        if session is None:
            return None
        user = session.user
        self.repo.delete(session)
        self.db.commit()
        return user

    def destroy_all_for_user(self, user_id: int, except_session_id: Optional[int] = None,
                             commit: bool = True) -> int:
        removed = self.repo.delete_for_user(user_id, except_session_id)
        if commit:
            self.db.commit()
        return removed

    def cleanup_expired(self) -> int:
        n = self.repo.delete_expired(self.clock())
        self.db.commit()
        return n
