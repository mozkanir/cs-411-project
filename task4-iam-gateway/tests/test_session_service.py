from ollama_iam_gateway.iam.session_service import SessionService


def test_valid_session(db, settings, clock, make_user):
    u = make_user()
    svc = SessionService(db, settings, clock)
    new = svc.create(u)
    assert svc.validate(new.raw_token).user_id == u.id
    assert svc.validate("wrong") is None and svc.validate(None) is None


def test_idle_timeout(db, settings, clock, make_user):
    svc = SessionService(db, settings, clock)
    new = svc.create(make_user())
    clock.advance(minutes=settings.session_idle_minutes + 1)
    assert svc.validate(new.raw_token) is None
    clock.advance(seconds=1)
    assert svc.validate(new.raw_token) is None  # row was deleted


def test_activity_extends_idle_but_not_absolute_limit(db, settings, clock, make_user):
    svc = SessionService(db, settings, clock)
    new = svc.create(make_user())
    # use the session every 20 min for (max_hours - 1) hours: still valid
    for _ in range((settings.session_max_hours - 1) * 3):
        clock.advance(minutes=20)
        assert svc.validate(new.raw_token) is not None
    # keep using it past the absolute limit: it must end anyway
    for _ in range(4):
        clock.advance(minutes=20)
    assert svc.validate(new.raw_token) is None


def test_disabled_user_session_invalid(db, settings, clock, make_user):
    u = make_user()
    svc = SessionService(db, settings, clock)
    new = svc.create(u)
    u.is_active = False
    db.commit()
    assert svc.validate(new.raw_token) is None


def test_destroy_all_except_current(db, settings, clock, make_user):
    u = make_user()
    svc = SessionService(db, settings, clock)
    keep, other1, other2 = svc.create(u), svc.create(u), svc.create(u)
    assert svc.destroy_all_for_user(u.id, except_session_id=keep.session.id) == 2
    assert svc.validate(keep.raw_token) is not None
    assert svc.validate(other1.raw_token) is None


def test_cleanup_expired(db, settings, clock, make_user):
    svc = SessionService(db, settings, clock)
    svc.create(make_user())
    clock.advance(hours=settings.session_max_hours + 1)
    assert svc.cleanup_expired() == 1
