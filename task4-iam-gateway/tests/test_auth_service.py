import pytest

from ollama_iam_gateway.data.models import AuditEvent, Role
from ollama_iam_gateway.iam import security
from ollama_iam_gateway.iam.auth_service import AuthService
from ollama_iam_gateway.iam.errors import AccountLocked, InvalidCredentials


def events(db):
    return [e.event_type for e in db.query(AuditEvent).order_by(AuditEvent.id)
            if e.event_type != "USER_CREATED"]


def test_successful_login_creates_session(db, settings, clock, make_user):
    make_user("alice")
    result = AuthService(db, settings, clock).login("Alice", "password123", "1.2.3.4")
    assert result.user.username == "alice"
    assert result.raw_session_token
    assert result.session.token_hash == security.hash_token(result.raw_session_token)
    assert events(db) == ["LOGIN_SUCCESS"]


def test_unknown_user_and_wrong_password_look_identical(db, settings, clock, make_user):
    make_user("alice")
    auth = AuthService(db, settings, clock)
    with pytest.raises(InvalidCredentials) as e1:
        auth.login("nobody", "password123")
    with pytest.raises(InvalidCredentials) as e2:
        auth.login("alice", "wrongpass")
    assert str(e1.value) == str(e2.value) == "Invalid username or password."


def test_unknown_user_still_spends_hash_time(db, settings, clock, monkeypatch):
    calls = []
    monkeypatch.setattr(security, "verify_dummy_password", lambda p: calls.append(p))
    with pytest.raises(InvalidCredentials):
        AuthService(db, settings, clock).login("ghost", "x")
    assert calls == ["x"]


def test_lockout_after_threshold_and_unlock_after_timeout(db, settings, clock, make_user):
    user = make_user("alice")
    auth = AuthService(db, settings, clock)
    for _ in range(settings.lockout_threshold - 1):
        with pytest.raises(InvalidCredentials):
            auth.login("alice", "wrong")
    assert user.failed_attempts == settings.lockout_threshold - 1 and user.locked_until is None

    with pytest.raises(InvalidCredentials):
        auth.login("alice", "wrong")           # 5th failure -> locked
    assert user.locked_until is not None

    with pytest.raises(AccountLocked):
        auth.login("alice", "password123")    # even the right password is refused

    clock.advance(minutes=settings.lockout_minutes, seconds=1)
    assert auth.login("alice", "password123").user.id == user.id
    assert user.failed_attempts == 0 and user.locked_until is None
    assert events(db).count("LOGIN_LOCKED") == 1


def test_disabled_user_cannot_login(db, settings, clock, make_user):
    u = make_user("alice")
    u.is_active = False
    db.commit()
    with pytest.raises(InvalidCredentials):
        AuthService(db, settings, clock).login("alice", "password123")


def test_logout_destroys_session(db, settings, clock, make_user):
    make_user("alice")
    auth = AuthService(db, settings, clock)
    r = auth.login("alice", "password123")
    auth.logout(r.raw_session_token)
    assert auth.sessions.validate(r.raw_session_token) is None
    assert events(db) == ["LOGIN_SUCCESS", "LOGOUT"]
    auth.logout("not-a-real-token")  # no error, no audit row
    assert events(db) == ["LOGIN_SUCCESS", "LOGOUT"]


def test_audit_caps_attacker_controlled_username(db, settings, clock):
    with pytest.raises(InvalidCredentials):
        AuthService(db, settings, clock).login("x" * 5000, "pw")
    row = db.query(AuditEvent).one()
    assert row.event_type == "LOGIN_FAILED" and len(row.username) == 64
