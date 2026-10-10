"""Application factory: wires the layers together.

    uvicorn: python -m ollama_iam_gateway.cli serve
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .data.db import init_db, make_engine, make_session_factory
from .gateway.proxy import OllamaProxy
from .iam.policy import ANY, PolicyEngine
from .web import routes_account, routes_admin, routes_auth
from .web.deps import SESSION_COOKIE, Forbidden, NotSignedIn, render

WEB_DIR = Path(__file__).parent / "web"

# Strict headers for the gateway's own pages: no inline scripts, no third-party
# resources, cannot be framed (clickjacking), never cached.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

log = logging.getLogger("ollama_iam_gateway")

# Read timeout is unlimited: loading a model or generating a long answer can
# take minutes. Connect fails fast so a dead Ollama gives a quick 502.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=60.0, pool=10.0)


def _is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def create_app(settings: Optional[Settings] = None, *,
               transport: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    """Build the gateway. ``transport`` lets tests replace the real Ollama."""
    settings = settings or Settings.from_env()
    engine = make_engine(settings.db_url)
    init_db(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not _is_loopback_url(settings.ollama_url):
            log.warning("IAM_OLLAMA_URL (%s) is not a loopback address. Ollama must only "
                        "be reachable through the gateway.", settings.ollama_url)
        app.state.http = httpx.AsyncClient(
            transport=transport, timeout=UPSTREAM_TIMEOUT,
            trust_env=False,          # never send localhost traffic through a system proxy
            follow_redirects=False)
        try:
            yield
        finally:
            await app.state.http.aclose()
            engine.dispose()

    app = FastAPI(title="Ollama IAM Gateway", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.policy = PolicyEngine()
    proxy = OllamaProxy(app.state)

    # ---- gateway web pages (/iam/*), registered before the catch-all ------
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/iam" or request.url.path.startswith("/iam/"):
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
        return response

    @app.exception_handler(NotSignedIn)
    async def _not_signed_in(request: Request, exc: NotSignedIn):
        response = RedirectResponse("/iam/login", status_code=303)
        if request.cookies.get(SESSION_COOKIE):
            response.delete_cookie(SESSION_COOKIE, path="/iam")
        return response

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, "error.html", None, status_code=403,
                      title="Not allowed", message=str(exc),
                      signed_in=bool(request.cookies.get(SESSION_COOKIE)))

    app.mount("/iam/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(routes_auth.router)
    app.include_router(routes_account.router)
    app.include_router(routes_admin.router)

    # Browsers ask for /favicon.ico on their own. Answer it here so it never
    # reaches the proxy (it would be a noisy AUTH_FAILED in the audit log).
    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon():
        return RedirectResponse("/iam/static/favicon.svg", status_code=308)

    @app.api_route("/iam/{rest:path}", methods=sorted(ANY), include_in_schema=False)
    async def _iam_not_found(request: Request, rest: str):  # noqa: ARG001
        return render(request, "error.html", None, status_code=404, title="Page not found",
                      message="There is no gateway page at this address.",
                      signed_in=bool(request.cookies.get(SESSION_COOKIE)))

    async def ollama_proxy(request: Request, full_path: str):  # noqa: ARG001
        return await proxy.handle(request)

    # Catch-all: every path that is not a gateway page is an Ollama request.
    app.add_api_route("/{full_path:path}", ollama_proxy, methods=sorted(ANY),
                      include_in_schema=False)
    return app
