"""Admin pages: users and audit log (FR5, FR12). Requires iam:manage."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..data.models import AuditEventType, Role, utcnow
from ..iam.audit_service import AuditService
from ..iam.errors import IAMError
from ..iam.user_service import UserService
from .deps import WebContext, check_csrf, client_ip, get_db, render, require_admin

router = APIRouter()

NOTICES = {
    "created": "User created. Share the password with them; they can change it after signing in.",
    "role": "Role updated. It applies to their next request.",
    "enabled": "User enabled.",
    "disabled": "User disabled. Their sessions ended and their API keys stopped working.",
    "unlocked": "User unlocked.",
    "password": "Password reset. The user was signed out everywhere.",
}
AUDIT_PAGE_SIZE = 50


def users_page(request: Request, ctx: WebContext, db: Session, *,
               error: Optional[str] = None, notice: Optional[str] = None,
               status_code: int = 200):
    return render(request, "admin_users.html", ctx, status_code=status_code, active="users",
                  users=UserService(db, request.app.state.settings).list_users(),
                  roles=[r.value for r in Role], now=utcnow(), error=error, notice=notice)


def _do(request: Request, ctx: WebContext, db: Session, csrf: str, action, notice: str):
    check_csrf(ctx, csrf)
    try:
        action(UserService(db, request.app.state.settings))
    except IAMError as exc:
        return users_page(request, ctx, db, error=str(exc), status_code=400)
    return RedirectResponse(f"/iam/admin/users?notice={notice}", status_code=303)


@router.get("/iam/admin/users")
def users(request: Request, ctx: WebContext = Depends(require_admin),
          db: Session = Depends(get_db), notice: str = ""):
    return users_page(request, ctx, db, notice=NOTICES.get(notice))


@router.post("/iam/admin/users")
def create_user(request: Request, ctx: WebContext = Depends(require_admin),
                db: Session = Depends(get_db), username: str = Form(""),
                password: str = Form(""), role: str = Form("user"), csrf: str = Form("")):
    if role not in {r.value for r in Role}:
        return users_page(request, ctx, db, error="Unknown role.", status_code=400)
    return _do(request, ctx, db, csrf, lambda s: s.create_user(
        username, password, Role(role), actor=ctx.user, client_ip=client_ip(request)), "created")


@router.post("/iam/admin/users/{user_id}/role")
def set_role(user_id: int, request: Request, ctx: WebContext = Depends(require_admin),
             db: Session = Depends(get_db), role: str = Form(""), csrf: str = Form("")):
    if role not in {r.value for r in Role}:
        return users_page(request, ctx, db, error="Unknown role.", status_code=400)
    return _do(request, ctx, db, csrf, lambda s: s.set_role(
        ctx.user, user_id, Role(role), client_ip(request)), "role")


@router.post("/iam/admin/users/{user_id}/active")
def set_active(user_id: int, request: Request, ctx: WebContext = Depends(require_admin),
               db: Session = Depends(get_db), active: str = Form(""), csrf: str = Form("")):
    enable = active == "1"
    return _do(request, ctx, db, csrf, lambda s: s.set_active(
        ctx.user, user_id, enable, client_ip(request)), "enabled" if enable else "disabled")


@router.post("/iam/admin/users/{user_id}/unlock")
def unlock(user_id: int, request: Request, ctx: WebContext = Depends(require_admin),
           db: Session = Depends(get_db), csrf: str = Form("")):
    return _do(request, ctx, db, csrf, lambda s: s.unlock(
        ctx.user, user_id, client_ip(request)), "unlocked")


@router.post("/iam/admin/users/{user_id}/password")
def reset_password(user_id: int, request: Request, ctx: WebContext = Depends(require_admin),
                   db: Session = Depends(get_db), new_password: str = Form(""),
                   csrf: str = Form("")):
    return _do(request, ctx, db, csrf, lambda s: s.reset_password(
        ctx.user, user_id, new_password, client_ip(request)), "password")


@router.get("/iam/admin/audit")
def audit(request: Request, ctx: WebContext = Depends(require_admin),
          db: Session = Depends(get_db), user: str = "", event: str = "", page: int = 1):
    page = max(1, page)
    event = event if event in {e.value for e in AuditEventType} else ""
    user = user.strip().lower()[:64]
    rows = AuditService(db).list_recent(limit=AUDIT_PAGE_SIZE + 1,
                                        offset=(page - 1) * AUDIT_PAGE_SIZE,
                                        username=user or None, event_type=event or None)
    return render(request, "admin_audit.html", ctx, active="audit",
                  rows=rows[:AUDIT_PAGE_SIZE], has_next=len(rows) > AUDIT_PAGE_SIZE,
                  page=page, f_user=user, f_event=event,
                  events=[e.value for e in AuditEventType])
