import pytest

from ollama_iam_gateway.data.models import AuditEvent, Role
from ollama_iam_gateway.iam.auth_service import AuthService
from ollama_iam_gateway.iam.errors import (InvalidCredentials, PermissionDenied,
                                           ValidationError)
from ollama_iam_gateway.iam.user_service import UserService


@pytest.fixture
def svc(db, settings, clock):
    return UserService(db, settings, clock)


def test_non_admin_cannot_manage_users(svc, make_user):
    user = make_user("alice")
    with pytest.raises(PermissionDenied):
        svc.create_user("bob", "password123", Role.USER, actor=user)
    with pytest.raises(PermissionDenied):
        svc.set_role(user, user.id, Role.ADMIN)


def test_set_role_and_last_admin_protection(svc, make_user):
    admin = make_user("root", Role.ADMIN)
    alice = make_user("alice")
    svc.set_role(admin, alice.id, Role.VIEWER)
    assert alice.role == Role.VIEWER
    with pytest.raises(ValidationError):
        svc.set_role(admin, admin.id, Role.USER)       # last admin
    svc.set_role(admin, alice.id, Role.ADMIN)
    svc.set_role(admin, admin.id, Role.USER)           # now allowed
    assert admin.role == Role.USER


def test_disable_logs_user_out_and_rules(db, settings, clock, svc, make_user):
    admin = make_user("root", Role.ADMIN)
    alice = make_user("alice")
    auth = AuthService(db, settings, clock)
    token = auth.login("alice", "password123").raw_session_token
    svc.set_active(admin, alice.id, False)
    assert auth.sessions.validate(token) is None
    with pytest.raises(ValidationError):
        svc.set_active(admin, admin.id, False)          # cannot disable self
    svc.set_active(admin, alice.id, True)
    assert auth.login("alice", "password123")


def test_unlock_and_reset_password(db, settings, clock, svc, make_user):
    admin = make_user("root", Role.ADMIN)
    alice = make_user("alice")
    auth = AuthService(db, settings, clock)
    for _ in range(settings.lockout_threshold):
        with pytest.raises(InvalidCredentials):
            auth.login("alice", "wrong")
    assert alice.locked_until is not None
    svc.unlock(admin, alice.id)
    assert alice.locked_until is None
    svc.reset_password(admin, alice.id, "brand-new-pass")
    assert auth.login("alice", "brand-new-pass")


def test_change_own_password(db, settings, clock, svc, make_user):
    alice = make_user("alice")
    auth = AuthService(db, settings, clock)
    current = auth.login("alice", "password123")
    other = auth.login("alice", "password123")
    with pytest.raises(InvalidCredentials):
        svc.change_own_password(alice, "wrong", "new-password-1")
    with pytest.raises(ValidationError):
        svc.change_own_password(alice, "password123", "password123")
    svc.change_own_password(alice, "password123", "new-password-1",
                            keep_session_id=current.session.id)
    assert auth.sessions.validate(current.raw_session_token) is not None
    assert auth.sessions.validate(other.raw_session_token) is None
    assert auth.login("alice", "new-password-1")


def test_admin_actions_are_audited(db, svc, make_user):
    admin = make_user("root", Role.ADMIN)
    alice = make_user("alice")
    svc.set_role(admin, alice.id, Role.VIEWER)
    svc.set_active(admin, alice.id, False)
    rows = db.query(AuditEvent).filter_by(event_type="USER_UPDATED").all()
    assert [r.username for r in rows] == ["root", "root"]
    assert "user -> viewer" in rows[0].detail and "disabled" in rows[1].detail
