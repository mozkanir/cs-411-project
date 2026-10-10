"""Account page: who am I, my API keys, my password (FR4, FR7)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..iam.apikey_service import ApiKeyService, NewApiKey
from ..iam.errors import IAMError
from ..iam.policy import Permission
from ..iam.user_service import UserService
from .deps import PERMISSION_TEXT, WebContext, check_csrf, client_ip, current, get_db, render

router = APIRouter()

NOTICES = {"key_revoked": "API key revoked. Programs using it can no longer reach Ollama.",
           "password_changed": "Password changed. Your other sessions were signed out."}


def account_page(request: Request, ctx: WebContext, db: Session, *,
                 error: Optional[str] = None, notice: Optional[str] = None,
                 new_key: Optional[NewApiKey] = None, status_code: int = 200):
    policy = request.app.state.policy
    can = [text for perm, text in PERMISSION_TEXT.items()
           if policy.has_permission(ctx.user.role, perm)]
    cannot = [text for perm, text in PERMISSION_TEXT.items()
              if not policy.has_permission(ctx.user.role, perm)]
    response = render(
        request, "account.html", ctx, status_code=status_code, active="account",
        keys=ApiKeyService(db).list_for_user(ctx.user, include_revoked=False),
        can=can, cannot=cannot, error=error, notice=notice, new_key=new_key,
        base_url=str(request.base_url).rstrip("/"),
        can_chat=policy.has_permission(ctx.user.role, Permission.INFERENCE))
    if new_key is not None:
        response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/iam/account")
def account(request: Request, ctx: WebContext = Depends(current),
            db: Session = Depends(get_db), notice: str = ""):
    return account_page(request, ctx, db, notice=NOTICES.get(notice))


@router.post("/iam/account/keys")
def create_key(request: Request, ctx: WebContext = Depends(current),
               db: Session = Depends(get_db), name: str = Form(""), csrf: str = Form("")):
    check_csrf(ctx, csrf)
    try:
        new = ApiKeyService(db).create(ctx.user, name, client_ip(request))
    except IAMError as exc:
        return account_page(request, ctx, db, error=str(exc), status_code=400)
    # Rendered directly (no redirect) so the key is shown exactly once.
    return account_page(request, ctx, db, new_key=new, status_code=201)


@router.post("/iam/account/keys/{key_id}/revoke")
def revoke_key(key_id: int, request: Request, ctx: WebContext = Depends(current),
               db: Session = Depends(get_db), csrf: str = Form("")):
    check_csrf(ctx, csrf)
    try:
        ApiKeyService(db).revoke(ctx.user, key_id, client_ip(request))
    except IAMError as exc:
        return account_page(request, ctx, db, error=str(exc), status_code=404)
    return RedirectResponse("/iam/account?notice=key_revoked", status_code=303)


@router.post("/iam/account/password")
def change_password(request: Request, ctx: WebContext = Depends(current),
                    db: Session = Depends(get_db), current_password: str = Form(""),
                    new_password: str = Form(""), confirm_password: str = Form(""),
                    csrf: str = Form("")):
    check_csrf(ctx, csrf)
    if new_password != confirm_password:
        return account_page(request, ctx, db, status_code=400,
                            error="The new passwords do not match.")
    try:
        UserService(db, request.app.state.settings).change_own_password(
            ctx.user, current_password, new_password,
            keep_session_id=ctx.session.id, client_ip=client_ip(request))
    except IAMError as exc:
        return account_page(request, ctx, db, error=str(exc), status_code=400)
    return RedirectResponse("/iam/account?notice=password_changed", status_code=303)
