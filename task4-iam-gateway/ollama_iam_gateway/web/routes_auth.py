"""Login / logout pages (FR1, FR2, FR3)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..iam import security
from ..iam.auth_service import AuthService
from ..iam.errors import AccountLocked, InvalidCredentials
from ..iam.session_service import SessionService
from .deps import (COOKIE_PATH, LOGIN_CSRF_COOKIE, SESSION_COOKIE, WebContext, check_csrf,
                   clear_session_cookie, client_ip, current, get_db, render, set_session_cookie)

router = APIRouter()

NOTICES = {"signed_out": "You are signed out."}


def _login_page(request: Request, *, error: Optional[str] = None, username: str = "",
                notice: Optional[str] = None, status_code: int = 200):
    token = security.generate_token()
    response = render(request, "login.html", status_code=status_code, error=error,
                      notice=notice, username=username, login_csrf=token,
                      server=request.url.hostname)
    response.set_cookie(LOGIN_CSRF_COOKIE, token, max_age=1800, path=COOKIE_PATH,
                        httponly=True, samesite="strict",
                        secure=request.app.state.settings.cookie_secure)
    return response


@router.get("/iam")
@router.get("/iam/")
def iam_root():
    return RedirectResponse("/iam/account", status_code=303)


@router.get("/iam/login")
def login_form(request: Request, db: Session = Depends(get_db), notice: str = ""):
    if SessionService(db, request.app.state.settings).validate(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/iam/account", status_code=303)
    return _login_page(request, notice=NOTICES.get(notice))


@router.post("/iam/login")
def login_submit(request: Request, db: Session = Depends(get_db),
                 username: str = Form(""), password: str = Form(""),
                 csrf: str = Form("")):
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not csrf or not cookie_token or not security.constant_time_equals(csrf, cookie_token):
        return _login_page(request, username=username, status_code=400,
                           error="This form has expired. Try signing in again.")
    try:
        result = AuthService(db, request.app.state.settings).login(
            username, password, client_ip(request))
    except AccountLocked as exc:
        return _login_page(request, username=username, error=str(exc), status_code=423)
    except InvalidCredentials as exc:
        return _login_page(request, username=username, error=str(exc), status_code=401)

    response = RedirectResponse("/iam/account", status_code=303)
    set_session_cookie(response, request, result.raw_session_token)
    response.delete_cookie(LOGIN_CSRF_COOKIE, path=COOKIE_PATH)
    return response


@router.post("/iam/logout")
def logout(request: Request, db: Session = Depends(get_db),
           ctx: WebContext = Depends(current), csrf: str = Form("")):
    check_csrf(ctx, csrf)
    AuthService(db, request.app.state.settings).logout(
        request.cookies.get(SESSION_COOKIE), client_ip(request))
    response = RedirectResponse("/iam/login?notice=signed_out", status_code=303)
    clear_session_cookie(response)
    return response

