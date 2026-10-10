"""Shared pieces for the web UI (MVC: these are controller helpers).

Security model of the pages:
  * session cookie  iam_session   HttpOnly, SameSite=Strict, Path=/iam
  * every POST form carries a CSRF token tied to the session
  * the login form (no session yet) uses a double-submit CSRF cookie
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..data.models import User, UserSession
from ..iam import security
from ..iam.policy import Permission
from ..iam.session_service import SessionService

SESSION_COOKIE = "iam_session"
LOGIN_CSRF_COOKIE = "iam_login_csrf"
COOKIE_PATH = "/iam"

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

PERMISSION_TEXT = {
    Permission.MODELS_READ: "See which models are installed and running",
    Permission.INFERENCE: "Chat, generate text and create embeddings",
    Permission.TOOLS_WEB: "Use web search and web fetch",
    Permission.MODELS_MANAGE: "Download, create, copy and delete models",
    Permission.CLOUD_ACCOUNT: "Manage this server's ollama.com sign-in",
    Permission.IAM_MANAGE: "Manage users and read the audit log",
}


class NotSignedIn(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "You do not have access to this page.") -> None:
        super().__init__(message)


@dataclass
class WebContext:
    user: User
    session: UserSession

    @property
    def csrf(self) -> str:
        return self.session.csrf_token


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def current(request: Request, db: Session = Depends(get_db)) -> WebContext:
    session = SessionService(db, request.app.state.settings).validate(
        request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise NotSignedIn()
    return WebContext(session.user, session)


def require_admin(request: Request, ctx: WebContext = Depends(current)) -> WebContext:
    if not request.app.state.policy.has_permission(ctx.user.role, Permission.IAM_MANAGE):
        raise Forbidden("Only admins can open this page.")
    return ctx


def check_csrf(ctx: WebContext, token: Optional[str]) -> None:
    if not token or not security.constant_time_equals(ctx.csrf, token):
        raise Forbidden("This form has expired. Reload the page and try again.")


def render(request: Request, name: str, ctx: Optional[WebContext] = None,
           status_code: int = 200, **values):
    policy = request.app.state.policy
    base = {
        "user": ctx.user if ctx else None,
        "csrf": ctx.csrf if ctx else None,
        "is_admin": bool(ctx and policy.has_permission(ctx.user.role, Permission.IAM_MANAGE)),
        "active": values.pop("active", ""),
    }
    base.update(values)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def set_session_cookie(response, request: Request, raw_token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(SESSION_COOKIE, raw_token, max_age=settings.session_max_hours * 3600,
                        path=COOKIE_PATH, httponly=True, samesite="strict",
                        secure=settings.cookie_secure)


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
