"""Engine, sessions and SQLite hardening.

Why SQLAlchemy: the same models run on SQLite in development and PostgreSQL/MySQL in
production. Switching is a one-line change of DATABASE_URL (install the driver, e.g.
``pip install "psycopg[binary]"`` for PostgreSQL).

SQLite concurrency (the "database is locked" problem)
-----------------------------------------------------
1. WAL mode: readers never block the single writer and vice versa.
2. ``busy_timeout``: a writer that finds the database busy WAITS (up to 30 s) instead of
   failing at once.
3. ``BEGIN IMMEDIATE`` for write sessions: the write lock is taken when the transaction
   starts. The default deferred BEGIN lets two transactions both read, then fail with
   SQLITE_BUSY when one tries to upgrade to a write lock, and that failure ignores
   busy_timeout. Taking the lock up front makes writers queue politely.
4. ``check_same_thread=False`` + a connection pool: Streamlit runs every browser session
   in its own thread, and the outbox worker is another thread.
Reads use a normal deferred transaction and run in parallel with a writer under WAL.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from functools import lru_cache, wraps
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .config import get_config
from .models import Base

log = logging.getLogger(__name__)
SCHEMA_VERSION = "3.0"


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def on_connect(dbapi_conn, _record):  # noqa: ANN001
        # Disable pysqlite's implicit transaction handling; we emit BEGIN ourselves below.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster than FULL
        cur.execute("PRAGMA foreign_keys=ON")      # SQLite ignores foreign keys unless asked
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    @event.listens_for(engine, "begin")
    def on_begin(conn):  # noqa: ANN001
        mode = conn.get_execution_options().get("sqlite_begin", "DEFERRED")
        conn.exec_driver_sql(f"BEGIN {mode}")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide engine (and connection pool). Built once, shared by all threads."""
    url = make_url(get_config().database_url)
    options: dict = {"pool_pre_ping": True}
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url.database in (None, "", ":memory:"):
            options["poolclass"] = StaticPool
        else:
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **options)
    if is_sqlite:
        _configure_sqlite(engine)
    return engine


@lru_cache(maxsize=1)
def get_write_engine() -> Engine:
    """Same pool, but SQLite transactions start with BEGIN IMMEDIATE (see module docs)."""
    return get_engine().execution_options(sqlite_begin="IMMEDIATE")


@contextmanager
def read_session() -> Iterator[Session]:
    """Read-only unit of work; nothing is committed."""
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@contextmanager
def write_session() -> Iterator[Session]:
    """Transactional unit of work: commit on success, roll back on any exception."""
    session = Session(get_write_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def init_schema() -> None:
    """Create missing tables. For schema CHANGES in production use Alembic migrations;
    ``create_all`` never alters existing tables."""
    from . import events  # noqa: F401  (registers the ORM hooks)

    Base.metadata.create_all(get_engine())
    with write_session() as s:
        from .models import AppSetting

        if s.get(AppSetting, "schema_version") is None:
            s.add(AppSetting(key="schema_version", value=SCHEMA_VERSION))


def database_info() -> dict[str, str]:
    engine = get_engine()
    info = {"backend": engine.dialect.name, "url": engine.url.render_as_string(hide_password=True)}
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            info["journal_mode"] = str(conn.execute(text("PRAGMA journal_mode")).scalar())
    return info


def retry_transient(attempts: int = 3, base_delay: float = 0.25):
    """Retry a READ that failed for a transient reason (dropped connection, brief lock).

    Only operational/connection errors are retried, never logic or constraint errors,
    and only for idempotent reads. Delays: 0.25 s, 0.5 s, 1 s ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except (OperationalError, InterfaceError) as exc:
                    if attempt + 1 == attempts:
                        raise
                    log.warning("Transient database error (%s); retry %d/%d", exc.__class__.__name__, attempt + 1, attempts - 1)
                    time.sleep(base_delay * 2 ** attempt)
        return wrapper
    return decorator


def healthcheck() -> bool:
    """True when the database answers a trivial query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        log.exception("Database health check failed")
        return False
