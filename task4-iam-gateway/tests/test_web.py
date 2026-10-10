"""Web UI tests: login page, account page, admin pages (MVC controllers)."""
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from ollama_iam_gateway.data.models import AuditEvent, Role
from ollama_iam_gateway.iam.user_service import UserService
from ollama_iam_gateway.main import create_app

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')
KEY_RE = re.compile(r'id="new-key" class="key">(olg_[^<]+)<')


def fake_ollama(request: httpx.Request) -> httpx.Response:
    async def body():
        yield b'{"models":[]}'
    return httpx.Response(200, content=body(), headers={"content-type": "application/json"})


@pytest.fixture
def app(settings):
    app = create_app(settings, transport=httpx.MockTransport(fake_ollama))
    with app.state.session_factory() as db:
        users = UserService(db, settings)
        users.create_user("root", "password123", Role.ADMIN)
        users.create_user("uma", "password123", Role.USER)
        users.create_user("vic", "password123", Role.VIEWER)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as c:
        yield c


def csrf_of(html):
    return CSRF_RE.search(html).group(1)


def login(client, username, password="password123"):
    token = csrf_of(client.get("/iam/login").text)
    return client.post("/iam/login", data={"username": username, "password": password,
                                           "csrf": token})


def signed_in(client, username):
    r = login(client, username)
    assert r.status_code == 303
    return csrf_of(client.get("/iam/account").text)


def audit(app, **filters):
    with app.state.session_factory() as db:
        return db.query(AuditEvent).filter_by(**filters).order_by(AuditEvent.id).all()


# ---- login / logout ------------------------------------------------------
def test_pages_require_login(client):
    for path in ["/iam/account", "/iam/admin/users", "/iam/admin/audit", "/iam"]:
        r = client.get(path)
        assert r.status_code == 303
    assert client.get("/iam/account").headers["location"] == "/iam/login"


def test_login_page_security_headers(client):
    r = client.get("/iam/login")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "iam_login_csrf=" in r.headers["set-cookie"]


def test_login_success_sets_hardened_cookie(client):
    r = login(client, "Root")
    assert r.status_code == 303 and r.headers["location"] == "/iam/account"
    cookie = r.headers["set-cookie"].lower()
    for attr in ["iam_session=", "httponly", "samesite=strict", "path=/iam"]:
        assert attr in cookie
    assert "Your role: admin" in client.get("/iam/account").text


def test_login_without_csrf_token_is_rejected(client, app):
    client.get("/iam/login")
    r = client.post("/iam/login", data={"username": "root", "password": "password123"})
    assert r.status_code == 400 and "iam_session" not in r.headers.get("set-cookie", "")
    assert audit(app, event_type="LOGIN_SUCCESS") == []


def test_wrong_password_and_unknown_user_same_message(client):
    r1, r2 = login(client, "root", "nope"), login(client, "ghost", "nope")
    assert r1.status_code == r2.status_code == 401
    assert "Invalid username or password." in r1.text and "Invalid username or password." in r2.text
    assert 'value="root"' in r1.text  # username kept, password not


def test_lockout_shown_on_login_page(client, settings):
    for _ in range(settings.lockout_threshold):
        login(client, "uma", "wrong")
    r = login(client, "uma")
    assert r.status_code == 423 and "temporarily locked" in r.text


def test_logout_needs_csrf_and_ends_session(client):
    csrf = signed_in(client, "uma")
    assert client.post("/iam/logout", data={"csrf": "forged"}).status_code == 403
    r = client.post("/iam/logout", data={"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/iam/login?notice=signed_out"
    assert client.get("/iam/account").status_code == 303


# ---- account ------------------------------------------------------------
def test_account_shows_what_role_can_do(client):
    signed_in(client, "vic")
    html = client.get("/iam/account").text
    assert '<li class="yes">See which models are installed and running</li>' in html
    assert '<li class="no">Chat, generate text and create embeddings</li>' in html
    assert "/iam/admin/users" not in html  # no admin nav for viewers


def test_api_key_lifecycle_through_the_ui(client):
    csrf = signed_in(client, "uma")
    r = client.post("/iam/account/keys", data={"name": "laptop", "csrf": csrf})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    raw = KEY_RE.search(r.text).group(1)
    # shown once only
    assert raw not in client.get("/iam/account").text
    # the key works on the Ollama API ...
    api = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/tags", headers=api).status_code == 200
    # ... until it is revoked in the UI
    key_id = re.search(r'/iam/account/keys/(\d+)/revoke', client.get("/iam/account").text).group(1)
    r = client.post(f"/iam/account/keys/{key_id}/revoke", data={"csrf": csrf})
    assert r.status_code == 303
    assert client.get("/api/tags", headers=api).status_code == 401


def test_forged_csrf_creates_nothing(client, app):
    signed_in(client, "uma")
    r = client.post("/iam/account/keys", data={"name": "evil", "csrf": "forged"})
    assert r.status_code == 403
    assert audit(app, event_type="APIKEY_CREATED") == []


def test_cannot_revoke_someone_elses_key(client, app):
    csrf_u = signed_in(client, "uma")
    client.post("/iam/account/keys", data={"name": "k", "csrf": csrf_u})
    with TestClient(app, follow_redirects=False) as other:
        csrf_v = signed_in(other, "vic")
        r = other.post("/iam/account/keys/1/revoke", data={"csrf": csrf_v})
        assert r.status_code == 404


def test_change_password(client, app):
    csrf = signed_in(client, "uma")
    r = client.post("/iam/account/password", data={
        "current_password": "password123", "new_password": "new-pass-123",
        "confirm_password": "other-pass", "csrf": csrf})
    assert r.status_code == 400 and "do not match" in r.text
    r = client.post("/iam/account/password", data={
        "current_password": "password123", "new_password": "new-pass-123",
        "confirm_password": "new-pass-123", "csrf": csrf})
    assert r.status_code == 303
    assert client.get("/iam/account").status_code == 200  # current session kept
    with TestClient(app, follow_redirects=False) as fresh:   # another browser
        assert login(fresh, "uma").status_code == 401
        assert login(fresh, "uma", "new-pass-123").status_code == 303


def test_session_cookie_is_not_an_api_credential(client):
    signed_in(client, "root")
    assert client.get("/api/tags").status_code == 401  # cookie alone does not work on the API


# ---- admin --------------------------------------------------------------
def test_admin_pages_forbidden_for_non_admins(client):
    csrf = signed_in(client, "uma")
    r = client.get("/iam/admin/users")
    assert r.status_code == 403 and "Back to your account" in r.text
    assert client.get("/iam/admin/audit").status_code == 403
    r = client.post("/iam/admin/users", data={"username": "x", "password": "password123",
                                              "role": "admin", "csrf": csrf})
    assert r.status_code == 403


def test_admin_user_management(client, app):
    csrf = signed_in(client, "root")
    r = client.post("/iam/admin/users", data={"username": "newbie", "password": "password123",
                                              "role": "viewer", "csrf": csrf})
    assert r.status_code == 303
    page = client.get("/iam/admin/users").text
    assert "newbie" in page
    uid = re.search(r'/iam/admin/users/(\d+)/role"[^>]*>\s*<input[^>]*>\s*<label[^>]*>Role of newbie',
                    page).group(1)
    assert client.post(f"/iam/admin/users/{uid}/role",
                       data={"role": "user", "csrf": csrf}).status_code == 303
    assert client.post(f"/iam/admin/users/{uid}/active",
                       data={"active": "0", "csrf": csrf}).status_code == 303
    assert login(TestClient(app), "newbie").status_code == 401   # disabled
    assert client.post(f"/iam/admin/users/{uid}/active",
                       data={"active": "1", "csrf": csrf}).status_code == 303
    assert client.post(f"/iam/admin/users/{uid}/password",
                       data={"new_password": "reset-pass-1", "csrf": csrf}).status_code == 303
    with TestClient(app, follow_redirects=False) as c2:
        assert login(c2, "newbie", "reset-pass-1").status_code == 303
    events = [e.detail for e in audit(app, event_type="USER_UPDATED")]
    assert any("user -> viewer" in d or "viewer -> user" in d for d in events)


def test_admin_errors_are_shown_not_crashed(client):
    csrf = signed_in(client, "root")
    r = client.post("/iam/admin/users", data={"username": "uma", "password": "password123",
                                              "role": "user", "csrf": csrf})
    assert r.status_code == 400 and "already exists" in r.text
    r = client.post("/iam/admin/users/1/role", data={"role": "viewer", "csrf": csrf})
    assert r.status_code == 400 and "last active admin" in r.text
    r = client.post("/iam/admin/users/1/role", data={"role": "superuser", "csrf": csrf})
    assert r.status_code == 400


def test_audit_page_filters_and_escapes(client):
    login(client, "<script>alert(1)</script>", "x")   # attacker-controlled username
    signed_in(client, "root")
    html = client.get("/iam/admin/audit").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    only = client.get("/iam/admin/audit?event=LOGIN_SUCCESS").text
    assert "LOGIN_SUCCESS" in only and "LOGIN_FAILED</span>" not in only
    assert client.get("/iam/admin/audit?page=999").status_code == 200


# ---- misc ---------------------------------------------------------------
def test_favicon_and_unknown_pages(client, app):
    r = client.get("/favicon.ico")
    assert r.status_code == 308 and r.headers["location"] == "/iam/static/favicon.svg"
    assert client.get("/iam/static/favicon.svg").status_code == 200
    assert client.get("/iam/does-not-exist").status_code == 404
    assert audit(app, event_type="AUTH_FAILED") == []
