"""Audit service (FR11, FR12). Thin layer over the append-only repository."""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..data.models import AuditEvent, AuditEventType, User, utcnow
from ..data.repositories import AuditRepository


class AuditService:
    def __init__(self, db: Session, clock: Callable = utcnow) -> None:
        self.db = db
        self.repo = AuditRepository(db)
        self.clock = clock

    def log(self, event_type: AuditEventType, *, user: Optional[User] = None,
            username: Optional[str] = None, method: Optional[str] = None,
            path: Optional[str] = None, model: Optional[str] = None,
            status_code: Optional[int] = None, client_ip: Optional[str] = None,
            detail: Optional[str] = None, commit: bool = True) -> AuditEvent:
        """Record one event.

        ``commit=False`` lets another service include the audit row in its
        own unit of work (both are saved together, or neither is).
        """
        # Some fields come straight from the request (e.g. a typed username
        # on a failed login), so cap their length.
        username = (user.username if user else username)
        event = self.repo.add(AuditEvent(
            timestamp=self.clock(),
            user_id=user.id if user else None,
            username=username[:64] if username else None,
            event_type=event_type.value,
            method=method,
            path=path[:512] if path else None,
            model=model[:128] if model else None,
            status_code=status_code,
            client_ip=client_ip[:64] if client_ip else None,
            detail=detail[:1000] if detail else None,
        ))
        if commit:
            self.db.commit()
        return event

    def list_recent(self, limit: int = 100, offset: int = 0,
                    username: Optional[str] = None,
                    event_type: Optional[str] = None) -> list[AuditEvent]:
        limit = max(1, min(limit, 500))
        return self.repo.list_recent(limit=limit, offset=max(0, offset),
                                     username=username, event_type=event_type)

    def count(self) -> int:
        return self.repo.count()
