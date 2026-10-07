import pytest

from ollama_iam_gateway.config import Settings
from ollama_iam_gateway.data.db import init_db, make_engine, make_session_factory


@pytest.fixture
def settings(tmp_path):
    return Settings(db_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def engine(settings):
    eng = make_engine(settings.db_url)
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with make_session_factory(engine)() as session:
        yield session


class FakeClock:
    """Controllable clock so tests can 'wait' 15 minutes instantly."""

    def __init__(self):
        from ollama_iam_gateway.data.models import utcnow
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        from datetime import timedelta
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_user(db, settings, clock):
    from ollama_iam_gateway.data.models import Role
    from ollama_iam_gateway.iam.user_service import UserService

    def _make(name="alice", role=Role.USER, password="password123"):
        return UserService(db, settings, clock).create_user(name, password, role)
    return _make
