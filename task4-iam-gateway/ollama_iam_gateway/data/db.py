"""Database engine and session factory."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_engine(db_url: str) -> Engine:
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        # FastAPI may use the connection from another thread; wait up to
        # 10 s if another request is writing (SQLite allows one writer).
        connect_args = {"check_same_thread": False, "timeout": 10}
    engine = create_engine(db_url, connect_args=connect_args)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables if they do not exist yet."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
