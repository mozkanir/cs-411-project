from datetime import timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from ollama_iam_gateway.data.models import (
    ApiKey, AuditEvent, Role, User, UserSession, utcnow)
from ollama_iam_gateway.data.repositories import (
    ApiKeyRepository, AuditRepository, SessionRepository, UserRepository)


def make_user(db, name="alice", role=Role.USER):
    return UserRepository(db).add(User(username=name, password_hash="x", role=role))


def test_all_tables_created(engine):
    assert set(inspect(engine).get_table_names()) == {
        "users", "sessions", "api_keys", "audit_events"}


def test_user_defaults_and_lookup(db):
    repo = UserRepository(db)
    u = make_user(db)
    db.commit()
    assert u.id is not None
    assert u.is_active is True
    assert u.failed_attempts == 0
    assert u.locked_until is None
    assert repo.get_by_username("alice").id == u.id
    assert repo.get_by_username("nobody") is None


def test_role_stored_as_text(db, engine):
    make_user(db, role=Role.ADMIN)
    db.commit()
    with engine.connect() as conn:
        raw = conn.exec_driver_sql("SELECT role FROM users").scalar_one()
    assert raw == "admin"


def test_username_is_unique(db):
    make_user(db)
    with pytest.raises(IntegrityError):
        make_user(db)


def test_count_active_admins(db):
    repo = UserRepository(db)
    make_user(db, "a1", Role.ADMIN)
    a2 = make_user(db, "a2", Role.ADMIN)
    make_user(db, "u1", Role.USER)
    a2.is_active = False
    db.commit()
    assert repo.count_active_admins() == 1


def test_sessions_lookup_and_expiry(db):
    u = make_user(db)
    repo = SessionRepository(db)
    now = utcnow()
    repo.add(UserSession(user_id=u.id, token_hash="h1", csrf_token="c",
                         expires_at=now + timedelta(hours=1)))
    repo.add(UserSession(user_id=u.id, token_hash="h2", csrf_token="c",
                         expires_at=now - timedelta(minutes=1)))
    db.commit()
    assert repo.get_by_token_hash("h1") is not None
    assert repo.delete_expired(now) == 1
    db.commit()
    assert repo.get_by_token_hash("h2") is None
    assert repo.delete_for_user(u.id) == 1


def test_api_keys_exclude_revoked_by_default(db):
    u = make_user(db)
    repo = ApiKeyRepository(db)
    repo.add(ApiKey(user_id=u.id, name="k1", prefix="aaaa1111", key_hash="h1"))
    repo.add(ApiKey(user_id=u.id, name="k2", prefix="bbbb2222", key_hash="h2",
                    revoked_at=utcnow()))
    db.commit()
    assert [k.name for k in repo.list_for_user(u.id)] == ["k1"]
    assert len(repo.list_for_user(u.id, include_revoked=True)) == 2
    assert repo.get_by_prefix("bbbb2222").is_revoked is True


def test_deleting_user_cascades_sessions_and_keys_but_keeps_audit(db):
    u = make_user(db)
    SessionRepository(db).add(UserSession(user_id=u.id, token_hash="h", csrf_token="c",
                                          expires_at=utcnow()))
    ApiKeyRepository(db).add(ApiKey(user_id=u.id, name="k", prefix="p", key_hash="h"))
    AuditRepository(db).add(AuditEvent(user_id=u.id, username="alice", event_type="X"))
    db.commit()
    db.delete(u)
    db.commit()
    assert db.query(UserSession).count() == 0
    assert db.query(ApiKey).count() == 0
    ev = db.query(AuditEvent).one()
    assert ev.user_id is None and ev.username == "alice"  # history survives


def test_audit_is_append_only_and_newest_first(db):
    repo = AuditRepository(db)
    t = utcnow()
    repo.add(AuditEvent(username="a", event_type="LOGIN_SUCCESS", timestamp=t))
    repo.add(AuditEvent(username="b", event_type="LOGIN_FAILED",
                        timestamp=t + timedelta(seconds=1)))
    db.commit()
    assert [e.username for e in repo.list_recent()] == ["b", "a"]
    assert [e.username for e in repo.list_recent(username="a")] == ["a"]
    assert [e.username for e in repo.list_recent(event_type="LOGIN_FAILED")] == ["b"]
    assert repo.count() == 2
    assert not any(hasattr(repo, m) for m in ("update", "delete", "remove"))
