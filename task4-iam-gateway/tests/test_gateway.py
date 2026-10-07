"""Gateway (PEP) tests against a fake Ollama (httpx.MockTransport)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ollama_iam_gateway.data.models import AuditEvent, Role
from ollama_iam_gateway.gateway import proxy as proxy_module
from ollama_iam_gateway.gateway.credentials import extract_bearer
from ollama_iam_gateway.iam.apikey_service import ApiKeyService
from ollama_iam_gateway.iam.user_service import UserService
from ollama_iam_gateway.main import create_app

CHAT_CHUNKS = [
    b'{"model":"qwen2.5:0.5b","message":{"content":"Hel"},"done":false}\n',
    b'{"model":"qwen2.5:0.5b","message":{"content":"lo"},"done":false}\n',
    b'{"model":"qwen2.5:0.5b","message":{"content":""},"done":true}\n',
]


def streamed(status, body: bytes, content_type="application/json"):
    """Like a real network response: the body arrives as a stream."""
    async def gen():
        yield body
    return httpx.Response(status, content=gen(), headers={"content-type": content_type})


class FakeOllama:
    """Records what reaches 'Ollama' and answers like it."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        self.requests.append(request)
        path = request.url.raw_path.decode().split("?")[0]
        if path == "/":
            return streamed(200, b"Ollama is running", "text/plain; charset=utf-8")
        if path == "/api/tags":
            return streamed(200, json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode())
        if path == "/api/chat":
            async def stream():
                for c in CHAT_CHUNKS:
                    yield c
            return httpx.Response(200, content=stream(),
                                  headers={"content-type": "application/x-ndjson"})
        if path == "/api/delete":
            return streamed(404, b'{"error":"model \'nope\' not found"}')
        return streamed(200, json.dumps({"ok": True, "path": path}).encode())


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def setup(settings, fake):
    app = create_app(settings, transport=httpx.MockTransport(fake))
    keys = {}
    with app.state.session_factory() as db:
        users = UserService(db, settings)
        for name, role in [("ana", Role.ADMIN), ("uma", Role.USER), ("vic", Role.VIEWER)]:
            u = users.create_user(name, "password123", role)
            keys[name] = ApiKeyService(db).create(u, "test").raw_key
    with TestClient(app) as client:
        yield app, client, keys


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def audit_rows(app, event_type=None):
    with app.state.session_factory() as db:
        q = db.query(AuditEvent).filter(AuditEvent.event_type != "USER_CREATED",
                                        AuditEvent.event_type != "APIKEY_CREATED")
        if event_type:
            q = q.filter_by(event_type=event_type)
        return q.order_by(AuditEvent.id).all()


# ---- credentials ------------------------------------------------------
@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None),
    ("Bearer", None), ("", None), (None, None)])
def test_extract_bearer(header, expected):
    assert extract_bearer(header) == expected


# ---- 1. public ---------------------------------------------------------
def test_public_root_needs_no_key_and_is_not_audited(setup, fake):
    app, client, _ = setup
    r = client.get("/")
    assert r.status_code == 200 and r.text == "Ollama is running"
    assert len(fake.requests) == 1
    assert audit_rows(app) == []


# ---- 2. authenticate ---------------------------------------------------
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer olg_00000000_" + "x" * 43},
    {"Authorization": "Basic YWRtaW46YWRtaW4="}])
def test_missing_or_bad_key_is_401_and_never_reaches_ollama(setup, fake, headers):
    app, client, _ = setup
    r = client.get("/api/tags", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert "error" in r.json()
    assert fake.requests == []
    assert [e.event_type for e in audit_rows(app)] == ["AUTH_FAILED"]


def test_revoked_key_and_disabled_user_are_401(setup, fake, settings):
    app, client, keys = setup
    with app.state.session_factory() as db:
        users = UserService(db, settings)
        ana = users.get_by_username("ana")
        uma = users.get_by_username("uma")
        k = ApiKeyService(db).list_for_user(uma)[0]
        ApiKeyService(db).revoke(uma, k.id)
        users.set_active(ana, users.get_by_username("vic").id, False)
    assert client.get("/api/tags", headers=auth(keys["uma"])).status_code == 401
    assert client.get("/api/tags", headers=auth(keys["vic"])).status_code == 401
    assert fake.requests == []


# ---- 3. authorize ------------------------------------------------------
@pytest.mark.parametrize("who,method,path,expected", [
    ("vic", "GET", "/api/tags", 200),
    ("vic", "POST", "/api/chat", 403),
    ("uma", "POST", "/api/chat", 200),
    ("uma", "DELETE", "/api/delete", 403),
    ("uma", "POST", "/api/pull", 403),
    ("ana", "DELETE", "/api/delete", 404),     # allowed; Ollama's own 404 passes through
    ("ana", "GET", "/api/not-a-route", 403),   # default deny, even for admin
])
def test_role_matrix_end_to_end(setup, fake, who, method, path, expected):
    app, client, keys = setup
    r = client.request(method, path, headers=auth(keys[who]),
                       json={"model": "nope"} if method != "GET" else None)
    assert r.status_code == expected
    reached = len(fake.requests) == 1
    assert reached == (expected != 403)


def test_denied_request_is_audited_with_reason(setup, fake):
    app, client, keys = setup
    r = client.post("/api/chat", headers=auth(keys["vic"]), json={"model": "m"})
    assert r.json()["error"] == "forbidden: role 'viewer' lacks inference"
    row = audit_rows(app, "ACCESS_DENIED")[0]
    assert (row.username, row.method, row.path, row.status_code) == ("vic", "POST", "/api/chat", 403)


@pytest.mark.parametrize("path", ["/api/chat/%2e%2e/delete", "/api/chat%2F..%2Fdelete",
                                  "/api/delete/", "/api/%2e/delete"])
def test_path_tricks_blocked_at_gateway(setup, fake, path):
    app, client, keys = setup
    r = client.request("DELETE", path, headers=auth(keys["ana"]))
    assert r.status_code == 403 and fake.requests == []


# ---- 4. forward --------------------------------------------------------
def test_streaming_chat_passes_through_unchanged(setup, fake):
    app, client, keys = setup
    body = {"model": "qwen2.5:0.5b", "messages": [{"role": "user", "content": "hi"}]}
    with client.stream("POST", "/api/chat", headers=auth(keys["uma"]), json=body) as r:
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/x-ndjson"
        lines = [line for line in r.iter_lines() if line]
    assert [json.loads(line)["message"]["content"] for line in lines] == ["Hel", "lo", ""]
    sent = fake.requests[0]
    assert json.loads(sent.content) == body


def test_gateway_credentials_never_reach_ollama(setup, fake):
    app, client, keys = setup
    client.get("/api/tags?verbose=1", headers={**auth(keys["vic"]), "Cookie": "iam_session=x",
                                                "X-Custom": "kept"})
    sent = fake.requests[0]
    assert "authorization" not in sent.headers
    assert "cookie" not in sent.headers
    assert sent.headers["x-custom"] == "kept"
    assert sent.url.query == b"verbose=1"


def test_raw_path_forwarded_unchanged(setup, fake):
    app, client, keys = setup
    r = client.get("/v1/models/qwen2.5%3A0.5b", headers=auth(keys["vic"]))
    assert r.status_code == 200
    assert fake.requests[0].url.raw_path == b"/v1/models/qwen2.5%3A0.5b"
    assert audit_rows(app, "PROXY_REQUEST")[0].model == "qwen2.5:0.5b"


def test_ollama_down_gives_502_and_is_audited(setup, fake):
    app, client, keys = setup
    fake.down = True
    r = client.get("/api/tags", headers=auth(keys["vic"]))
    assert r.status_code == 502 and r.json() == {"error": "Ollama is not reachable"}
    row = audit_rows(app, "PROXY_REQUEST")[0]
    assert row.status_code == 502 and "ConnectError" in row.detail


def test_body_size_limit(setup, fake, monkeypatch):
    app, client, keys = setup
    monkeypatch.setattr(proxy_module, "MAX_BUFFERED_BODY", 100)
    r = client.post("/api/chat", headers=auth(keys["uma"]), content=b"x" * 101)
    assert r.status_code == 413 and fake.requests == []


# ---- 5. audit ----------------------------------------------------------
def test_successful_request_audit_row(setup, fake):
    app, client, keys = setup
    with client.stream("POST", "/api/chat", headers=auth(keys["uma"]),
                       json={"model": "qwen2.5:0.5b", "messages": []}) as r:
        b"".join(r.iter_bytes())
    row = audit_rows(app, "PROXY_REQUEST")[0]
    assert (row.username, row.method, row.path, row.model, row.status_code) == \
        ("uma", "POST", "/api/chat", "qwen2.5:0.5b", 200)
    assert row.detail == f"{sum(map(len, CHAT_CHUNKS))} bytes"
    assert row.client_ip == "testclient"


def test_model_extraction_ignores_bad_json(setup, fake):
    app, client, keys = setup
    client.post("/api/chat", headers={**auth(keys["uma"]), "Content-Type": "application/json"},
                content=b"{not json")
    assert audit_rows(app, "PROXY_REQUEST")[0].model is None
