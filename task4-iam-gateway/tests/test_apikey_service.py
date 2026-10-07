import pytest

from ollama_iam_gateway.data.models import Role
from ollama_iam_gateway.iam.apikey_service import MAX_ACTIVE_KEYS_PER_USER, ApiKeyService
from ollama_iam_gateway.iam.errors import NotFound, ValidationError


def test_create_and_resolve(db, clock, make_user):
    u = make_user()
    svc = ApiKeyService(db, clock)
    new = svc.create(u, "laptop")
    assert new.raw_key.startswith("olg_")
    assert new.key.key_hash != new.raw_key and new.raw_key not in new.key.key_hash
    resolved = svc.resolve(new.raw_key)
    assert resolved.user.id == u.id and resolved.key.last_used_at is not None


def test_resolve_rejects_bad_keys(db, clock, make_user):
    svc = ApiKeyService(db, clock)
    new = svc.create(make_user(), "k")
    tampered = new.raw_key[:-1] + ("A" if new.raw_key[-1] != "A" else "B")
    for bad in [None, "", "garbage", tampered, "olg_00000000_" + "x" * 43]:
        assert svc.resolve(bad) is None


def test_revoked_key_and_disabled_user_rejected(db, clock, make_user):
    u = make_user()
    svc = ApiKeyService(db, clock)
    k1, k2 = svc.create(u, "a"), svc.create(u, "b")
    svc.revoke(u, k1.key.id)
    assert svc.resolve(k1.raw_key) is None
    assert svc.resolve(k2.raw_key) is not None
    u.is_active = False
    db.commit()
    assert svc.resolve(k2.raw_key) is None


def test_only_owner_or_admin_can_revoke(db, clock, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    admin = make_user("root", Role.ADMIN)
    svc = ApiKeyService(db, clock)
    key = svc.create(alice, "k")
    with pytest.raises(NotFound):
        svc.revoke(bob, key.key.id)
    svc.revoke(admin, key.key.id)
    assert key.key.is_revoked
    with pytest.raises(NotFound):
        svc.revoke(admin, 9999)


def test_validation_and_limit(db, clock, make_user):
    u = make_user()
    svc = ApiKeyService(db, clock)
    for bad in ["", "   ", "x" * 65]:
        with pytest.raises(ValidationError):
            svc.create(u, bad)
    for i in range(MAX_ACTIVE_KEYS_PER_USER):
        svc.create(u, f"k{i}")
    with pytest.raises(ValidationError):
        svc.create(u, "one-too-many")
    assert len(svc.list_for_user(u)) == MAX_ACTIVE_KEYS_PER_USER
