import pytest

from ollama_iam_gateway.data.models import AuditEvent, Role
from ollama_iam_gateway.iam import security
from ollama_iam_gateway.iam.user_service import (
    UserAlreadyExists, UserService, ValidationError)


def test_create_user_normalizes_and_hashes(db, settings):
    svc = UserService(db, settings)
    u = svc.create_user("  Alice ", "password123", Role.USER)
    assert u.username == "alice"
    assert u.password_hash != "password123"
    assert security.verify_password(u.password_hash, "password123")
    assert svc.get_by_username("ALICE").id == u.id


@pytest.mark.parametrize("bad", ["ab", "has space", "ünicode", "x" * 33, ""])
def test_rejects_bad_usernames(db, settings, bad):
    with pytest.raises(ValidationError):
        UserService(db, settings).create_user(bad, "password123", Role.USER)


@pytest.mark.parametrize("bad", ["short", "        ", "x" * 300])
def test_rejects_bad_passwords(db, settings, bad):
    with pytest.raises(ValidationError):
        UserService(db, settings).create_user("bob", bad, Role.USER)


def test_duplicate_is_case_insensitive(db, settings):
    svc = UserService(db, settings)
    svc.create_user("bob", "password123", Role.USER)
    with pytest.raises(UserAlreadyExists):
        svc.create_user("BOB", "password123", Role.ADMIN)


def test_creation_is_audited(db, settings):
    svc = UserService(db, settings)
    admin = svc.create_user("root", "password123", Role.ADMIN)
    svc.create_user("carol", "password123", Role.VIEWER, actor=admin)
    events = db.query(AuditEvent).order_by(AuditEvent.id).all()
    assert [(e.username, e.event_type) for e in events] == [
        ("system", "USER_CREATED"), ("root", "USER_CREATED")]
    assert "carol" in events[1].detail and "viewer" in events[1].detail
