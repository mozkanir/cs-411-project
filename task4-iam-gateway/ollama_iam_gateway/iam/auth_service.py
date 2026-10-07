"""Authentication: login with lockout, logout (FR1-FR3).

Implements the "Browser Login" sequence diagram (04-seq-login).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..config import Settings
from ..data.models import AuditEventType, User, UserSession, utcnow
from ..data.repositories import UserRepository
from . import security
from .audit_service import AuditService
from .errors import AccountLocked, InvalidCredentials
from .session_service import SessionService


@dataclass
class LoginResult:
    user: User
    raw_session_token: str   # put in the cookie
    session: UserSession


class AuthService:
    def __init__(self, db: Session, settings: Settings, clock: Callable = utcnow) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self.users = UserRepository(db)
        self.sessions = SessionService(db, settings, clock)
        self.audit = AuditService(db, clock)

    def login(self, username: str, password: str, client_ip: Optional[str] = None) -> LoginResult:
        """Check credentials and open a session.

        Raises InvalidCredentials or AccountLocked. Every outcome is audited.
        """
        username = (username or "").strip().lower()
        password = password or ""
        now = self.clock()
        user = self.users.get_by_username(username)

        # 1) Unknown or disabled user: same message and same time cost as a
        #    wrong password, so attackers cannot tell which usernames exist.
        if user is None or not user.is_active:
            security.verify_dummy_password(password)
            self.audit.log(AuditEventType.LOGIN_FAILED, user=user, username=username,
                           client_ip=client_ip,
                           detail="unknown user" if user is None else "user disabled")
            raise InvalidCredentials()

        # 2) Locked: do not even check the password (no guessing while locked).
        if user.locked_until is not None and user.locked_until > now:
            self.audit.log(AuditEventType.LOGIN_LOCKED, user=user, client_ip=client_ip,
                           detail=f"locked until {user.locked_until:%Y-%m-%d %H:%M:%S} UTC")
            raise AccountLocked()

        # 3) Wrong password: count it, lock after N failures.
        if not security.verify_password(user.password_hash, password):
            user.failed_attempts += 1
            detail = f"wrong password (attempt {user.failed_attempts})"
            if user.failed_attempts >= self.settings.lockout_threshold:
                user.locked_until = now + timedelta(minutes=self.settings.lockout_minutes)
                user.failed_attempts = 0
                detail += f"; account locked for {self.settings.lockout_minutes} min"
            self.audit.log(AuditEventType.LOGIN_FAILED, user=user, client_ip=client_ip,
                           detail=detail, commit=False)
            self.db.commit()
            raise InvalidCredentials()

        # 4) Success.
        user.failed_attempts = 0
        user.locked_until = None
        if security.password_needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(password)
        new = self.sessions.create(user, commit=False)
        self.audit.log(AuditEventType.LOGIN_SUCCESS, user=user, client_ip=client_ip,
                       commit=False)
        self.db.commit()
        return LoginResult(user, new.raw_token, new.session)

    def logout(self, raw_session_token: Optional[str], client_ip: Optional[str] = None) -> None:
        user = self.sessions.destroy(raw_session_token)
        if user is not None:
            self.audit.log(AuditEventType.LOGOUT, user=user, client_ip=client_ip)
