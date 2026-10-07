"""User management service (FR5, FR6).

Step 1 contains user creation and lookup. Disable / role change /
password change are added in step 2.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..config import Settings
from ..data.models import AuditEvent, AuditEventType, Role, User
from ..data.repositories import AuditRepository, UserRepository
from . import security

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
MAX_PASSWORD_LENGTH = 256  # protects the slow hash from huge inputs


class UserServiceError(Exception):
    """Base class for user management errors (message is safe to show)."""


class ValidationError(UserServiceError):
    pass


class UserAlreadyExists(UserServiceError):
    pass


class UserService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)
        self.audit = AuditRepository(db)

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

    # ---- commands ---------------------------------------------------
    def create_user(self, username: str, password: str, role: Role,
                    actor: Optional[User] = None) -> User:
        """Create a user and record a USER_CREATED audit event.

        ``actor`` is the admin doing it; ``None`` means the CLI/system.
        """
        username = self.normalize_username(username)
        self.validate_username(username)
        self.validate_password(password)
        if self.users.get_by_username(username) is not None:
            raise UserAlreadyExists(f"User '{username}' already exists.")

        user = self.users.add(User(
            username=username,
            password_hash=security.hash_password(password),
            role=role,
        ))
        self.audit.add(AuditEvent(
            user_id=actor.id if actor else None,
            username=actor.username if actor else "system",
            event_type=AuditEventType.USER_CREATED.value,
            detail=f"created user '{username}' with role '{role.value}'",
        ))
        self.db.commit()
        return user

    # ---- queries ----------------------------------------------------
    def get_by_username(self, username: str) -> Optional[User]:
        return self.users.get_by_username(self.normalize_username(username))

    def list_users(self) -> list[User]:
        return self.users.list_all()
