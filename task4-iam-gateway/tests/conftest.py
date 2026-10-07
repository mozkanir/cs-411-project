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
