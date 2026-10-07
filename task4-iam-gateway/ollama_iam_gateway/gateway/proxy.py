"""Policy Enforcement Point: the proxy pipeline in front of Ollama.

Implements the "Authorized API Request" sequence (05-seq-proxy):

    1. classify   policy.required_permission(method, path)
    2. authenticate  Bearer API key -> Identity        (401 if missing/invalid)
    3. authorize  policy.decide(role, method, path)    (403 if denied)
    4. forward    to Ollama, streaming both directions (502 if unreachable)
    5. audit      one row per request, written when the stream ends

Pipes and Filters: each stage can stop the request; later stages never run.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import anyio
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..data.models import AuditEventType
from ..iam.audit_service import AuditService
from ..iam.policy import Permission, PolicyEngine, UnsafePath, normalize_path
from .credentials import Identity, extract_bearer, resolve_identity

log = logging.getLogger("ollama_iam_gateway.proxy")

# Bodies of normal API calls are read into memory (to find the model name for
# the audit log). Blob uploads (model weights, can be GBs) are streamed.
MAX_BUFFERED_BODY = 64 * 1024 * 1024

# RFC 9110 hop-by-hop headers: meaningful for one connection only.
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "trailers", "transfer-encoding", "upgrade"}
# Credentials for the gateway must never reach Ollama.
_STRIP_FROM_REQUEST = _HOP_BY_HOP | {"host", "authorization", "cookie", "content-length"}
_STRIP_FROM_RESPONSE = _HOP_BY_HOP | {"content-length", "set-cookie"}


def _error(status: int, message: str, headers: Optional[dict] = None) -> JSONResponse:
    # Same shape as Ollama's own errors: {"error": "..."}
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def _raw_path(request: Request) -> str:
    """The path exactly as the client sent it (still percent-encoded)."""
    raw = request.scope.get("raw_path")
    return raw.decode("latin-1") if raw else request.url.path


def _extract_model(segments: tuple[str, ...], body: bytes, content_type: str) -> Optional[str]:
    """Best-effort model name for the audit log. Never raises."""
    if len(segments) == 3 and segments[:2] == ("v1", "models"):
        return segments[2][:128]
    if not body or "json" not in content_type.lower():
        return None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("model", "name", "source"):  # chat/generate, older pull/delete, copy
        value = data.get(key)
        if isinstance(value, str) and value:
            return value[:128]
    return None


@dataclass
class _AuditInfo:
    method: str
    path: str
    client_ip: Optional[str]
    identity: Optional[Identity] = None
    model: Optional[str] = None
    extra: list[str] = field(default_factory=list)


class OllamaProxy:
    def __init__(self, app_state) -> None:
        self.state = app_state

    @property
    def policy(self) -> PolicyEngine:
        return self.state.policy

    # ---- audit helpers (blocking DB work, run in a worker thread) -------
    def _audit_sync(self, event: AuditEventType, info: _AuditInfo,
                    status: Optional[int], detail: Optional[str]) -> None:
        try:
            with self.state.session_factory() as db:
                AuditService(db).log(
                    event,
                    user_id=info.identity.user_id if info.identity else None,
                    username=info.identity.username if info.identity else None,
                    method=info.method, path=info.path, model=info.model,
                    status_code=status, client_ip=info.client_ip, detail=detail)
        except Exception:  # auditing must never break the request
            log.exception("failed to write audit event")

    async def _audit(self, event: AuditEventType, info: _AuditInfo,
                     status: Optional[int], detail: Optional[str] = None) -> None:
        await run_in_threadpool(self._audit_sync, event, info, status, detail)

    # ---- the pipeline ---------------------------------------------------
    async def handle(self, request: Request) -> Response:
        method = request.method.upper()
        path = _raw_path(request)
        info = _AuditInfo(method=method, path=path,
                          client_ip=request.client.host if request.client else None)

        # 1. classify
        required = self.policy.required_permission(method, path)

        if required != Permission.PUBLIC:
            # 2. authenticate
            token = extract_bearer(request.headers.get("authorization"))
            identity = await run_in_threadpool(
                resolve_identity, self.state.session_factory, token)
            if identity is None:
                await self._audit(AuditEventType.AUTH_FAILED, info, 401,
                                  "invalid or revoked API key" if token else "no API key")
                return _error(401, "unauthorized: a valid API key is required "
                                   "(Authorization: Bearer olg_...)",
                              headers={"WWW-Authenticate": 'Bearer realm="ollama-iam-gateway"'})
            info.identity = identity

            # 3. authorize
            decision = self.policy.decide(identity.role, method, path)
            if not decision.allowed:
                await self._audit(AuditEventType.ACCESS_DENIED, info, 403, decision.reason)
                return _error(403, f"forbidden: {decision.reason}")

        # 4. forward
        return await self._forward(request, info, audit=required != Permission.PUBLIC)

    async def _forward(self, request: Request, info: _AuditInfo, audit: bool) -> Response:
        settings = self.state.settings
        try:
            segments = normalize_path(info.path)
        except UnsafePath:  # pragma: no cover - policy already refused these
            return _error(400, "bad path")

        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _STRIP_FROM_REQUEST}
        is_blob = len(segments) >= 2 and segments[:2] == ("api", "blobs")

        if is_blob:
            content = request.stream()
        else:
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_BUFFERED_BODY:
                return _error(413, "request body too large")
            body = await request.body()
            if len(body) > MAX_BUFFERED_BODY:
                return _error(413, "request body too large")
            info.model = _extract_model(segments, body, request.headers.get("content-type", ""))
            content = body

        url = settings.ollama_url + info.path
        if request.url.query:
            url += "?" + request.url.query

        client: httpx.AsyncClient = self.state.http
        upstream_request = client.build_request(info.method, url, headers=headers, content=content)
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            if audit:
                await self._audit(AuditEventType.PROXY_REQUEST, info, 502,
                                  f"upstream error: {type(exc).__name__}")
            return _error(502, "Ollama is not reachable")

        response_headers = {k: v for k, v in upstream.headers.items()
                            if k.lower() not in _STRIP_FROM_RESPONSE}

        async def body_stream() -> AsyncIterator[bytes]:
            completed = False
            sent = 0
            try:
                async for chunk in upstream.aiter_raw():
                    sent += len(chunk)
                    yield chunk
                completed = True
            finally:
                # 5. audit - runs even if the client disconnects mid-stream
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
                    if audit:
                        detail = f"{sent} bytes"
                        if not completed:
                            detail += "; stream interrupted"
                        await self._audit(AuditEventType.PROXY_REQUEST, info,
                                          upstream.status_code, detail)

        return StreamingResponse(body_stream(), status_code=upstream.status_code,
                                 headers=response_headers)
