"""User management (FR4, FR5, FR6)."""
from __future__ import annotations

import re
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..config import Settings
from ..data.models import AuditEventType, Role, User, utcnow
from ..data.repositories import SessionRepository, UserRepository
from . import security
from .audit_service import AuditService
from .errors import (IAMError, InvalidCredentials, NotFound, PermissionDenied,
                     UserAlreadyExists, ValidationError)

# Kept for backwards compatibility with step 1 imports.
UserServiceError = IAMError

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
MAX_PASSWORD_LENGTH = 256  # protects the slow hash from huge inputs

__all__ = ["UserService", "UserServiceError", "ValidationError", "UserAlreadyExists",
           "NotFound", "PermissionDenied", "InvalidCredentials"]


class UserService:
    def __init__(self, db: Session, settings: Settings, clock: Callable = utcnow) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)
        self.audit = AuditService(db, clock)

    # ---- validation -------------------------------------------------
    @staticmethod
    def normalize_username(username: str) -> str:
        return (username or "").strip().lower()

    def validate_username(self, username: str) -> None:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValidationError(
                "Username must be 3-32 characters: lowercase letters, digits, '_', '.', '-'.")

    def validate_password(self, password: str) -> None:
        if password is None or len(password) < self.settings.min_password_length:
            raise ValidationError(
                f"Password must be at least {self.settings.min_password_length} characters.")
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValidationError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
        if not password.strip():
            raise ValidationError("Password cannot be only whitespace.")

    # ---- helpers ----------------------------------------------------
    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != Role.ADMIN or not actor.is_active:
            raise PermissionDenied("Only admins can manage users.")

    def _get_target(self, user_id: int) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFound("User not found.")
        return user

    def _would_remove_last_admin(self, target: User) -> bool:
        return (target.role == Role.ADMIN and target.is_active
                and self.users.count_active_admins() <= 1)

    def _log(self, event: AuditEventType, actor: Optional[User], detail: str,
             client_ip: Optional[str]) -> None:
        self.audit.log(event, user=actor, username=None if actor else "system",
                       client_ip=client_ip, detail=detail, commit=False)

    # ---- commands ---------------------------------------------------
    def create_user(self, username: str, password: str, role: Role,
                    actor: Optional[User] = None, client_ip: Optional[str] = None) -> User:
        """Create a user. ``actor=None`` means the CLI (first admin bootstrap)."""
        if actor is not None:
            self._require_admin(actor)
        username = self.normalize_username(username)
        self.validate_username(username)
        self.validate_password(password)
        if self.users.get_by_username(username) is not None:
            raise UserAlreadyExists(f"User '{username}' already exists.")

        user = self.users.add(User(username=username,
                                   password_hash=security.hash_password(password),
                                   role=role, created_at=self.clock()))
        self._log(AuditEventType.USER_CREATED, actor,
                  f"created user '{username}' with role '{role.value}'", client_ip)
        self.db.commit()
        return user

    def set_role(self, actor: User, user_id: int, role: Role,
                 client_ip: Optional[str] = None) -> User:
        self._require_admin(actor)
        target = self._get_target(user_id)
        if target.role == role:
            return target
        if role != Role.ADMIN and self._would_remove_last_admin(target):
            raise ValidationError("Cannot demote the last active admin.")
        old = target.role
        target.role = role
        self._log(AuditEventType.USER_UPDATED, actor,
                  f"role of '{target.username}': {old.value} -> {role.value}", client_ip)
        self.db.commit()
        return target

    def set_active(self, actor: User, user_id: int, active: bool,
                   client_ip: Optional[str] = None) -> User:
        """Enable/disable a user. Disabling logs them out everywhere at once;
        their API keys stop working because resolve() checks is_active."""
        self._require_admin(actor)
        target = self._get_target(user_id)
        if target.is_active == active:
            return target
        if not active:
            if target.id == actor.id:
                raise ValidationError("You cannot disable your own account.")
            if self._would_remove_last_admin(target):
                raise ValidationError("Cannot disable the last active admin.")
            self.sessions.delete_for_user(target.id)
        target.is_active = active
        self._log(AuditEventType.USER_UPDATED, actor,
                  f"user '{target.username}' {'enabled' if active else 'disabled'}", client_ip)
        self.db.commit()
        return target

    def unlock(self, actor: User, user_id: int, client_ip: Optional[str] = None) -> User:
        self._require_admin(actor)
        target = self._get_target(user_id)
        target.locked_until = None
        target.failed_attempts = 0
        self._log(AuditEventType.USER_UPDATED, actor,
                  f"user '{target.username}' unlocked", client_ip)
        self.db.commit()
        return target

    def reset_password(self, actor: User, user_id: int, new_password: str,
                       client_ip: Optional[str] = None) -> User:
        """Admin sets a new password (e.g. user forgot it). Logs the user out."""
        self._require_admin(actor)
        target = self._get_target(user_id)
        self.validate_password(new_password)
        target.password_hash = security.hash_password(new_password)
        target.locked_until = None
        target.failed_attempts = 0
        self.sessions.delete_for_user(target.id)
        self._log(AuditEventType.PASSWORD_CHANGED, actor,
                  f"password of '{target.username}' reset by admin", client_ip)
        self.db.commit()
        return target

    def change_own_password(self, user: User, current_password: str, new_password: str,
                            keep_session_id: Optional[int] = None,
                            client_ip: Optional[str] = None) -> User:
        """FR4. Other sessions of the user are closed (keep the current one)."""
        if not security.verify_password(user.password_hash, current_password or ""):
            raise InvalidCredentials("Current password is wrong.")
        self.validate_password(new_password)
        if current_password == new_password:
            raise ValidationError("New password must be different from the current one.")
        user.password_hash = security.hash_password(new_password)
        self.sessions.delete_for_user(user.id, except_session_id=keep_session_id)
        self.audit.log(AuditEventType.PASSWORD_CHANGED, user=user, client_ip=client_ip,
                       detail="changed own password", commit=False)
        self.db.commit()
        return user

    # ---- queries ----------------------------------------------------
    def get(self, user_id: int) -> Optional[User]:
        return self.users.get(user_id)

    def get_by_username(self, username: str) -> Optional[User]:
        return self.users.get_by_username(self.normalize_username(username))

    def list_users(self) -> list[User]:
        return self.users.list_all()
