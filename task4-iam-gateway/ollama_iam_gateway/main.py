"""Application factory: wires the layers together.

    uvicorn: python -m ollama_iam_gateway.cli serve
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request

from .config import Settings
from .data.db import init_db, make_engine, make_session_factory
from .gateway.proxy import OllamaProxy
from .iam.policy import ANY, PolicyEngine

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

    # Step 5 adds the /iam/* web pages here, before the catch-all.

    async def ollama_proxy(request: Request, full_path: str):  # noqa: ARG001
        return await proxy.handle(request)

    # Catch-all: every path that is not a gateway page is an Ollama request.
    app.add_api_route("/{full_path:path}", ollama_proxy, methods=sorted(ANY),
                      include_in_schema=False)
    return app
