"""Engine, sessions and dialect-aware bulk upserts (Postgres in prod, SQLite in tests)."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import env
from .models import Base, Setting, WorkerStatus

log = logging.getLogger(__name__)

_engine: Engine | None = None
_Session: sessionmaker | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine, _Session
    if _engine is None or url is not None:
        url = url or env.database_url
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(pool_size=10, max_overflow=10)
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(url: str | None = None, retries: int = 30) -> Engine:
    """Create tables, waiting for the database to accept connections."""
    eng = get_engine(url)
    for attempt in range(retries):
        try:
            Base.metadata.create_all(eng)
            return eng
        except Exception as exc:  # database not ready yet
            if attempt == retries - 1:
                raise
            log.warning("database not ready (%s), retrying…", exc.__class__.__name__)
            time.sleep(2)
    return eng


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        get_engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _insert_for(session: Session):
    return postgresql.insert if session.bind.dialect.name == "postgresql" else sqlite.insert


def _chunks(rows: Sequence[dict], n: int) -> Iterable[Sequence[dict]]:
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def insert_ignore(session: Session, model, rows: Sequence[dict], conflict_cols: list[str]) -> int:
    """Bulk insert, silently skipping rows that hit a unique conflict."""
    if not rows:
        return 0
    ins = _insert_for(session)
    n = 0
    for chunk in _chunks(list(rows), 500):
        stmt = ins(model).values(list(chunk)).on_conflict_do_nothing(index_elements=conflict_cols)
        res = session.execute(stmt)
        n += res.rowcount or 0
    return n


def upsert(
    session: Session,
    model,
    rows: Sequence[dict],
    conflict_cols: list[str],
    update_cols: list[str] | None = None,
) -> None:
    """Bulk insert-or-update on the given conflict columns."""
    if not rows:
        return
    ins = _insert_for(session)
    rows = list(rows)
    cols = update_cols or [c for c in rows[0].keys() if c not in conflict_cols]
    for chunk in _chunks(rows, 500):
        stmt = ins(model).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols, set_={c: getattr(stmt.excluded, c) for c in cols}
        )
        session.execute(stmt)


# -------------------------------------------------------------- settings
def get_setting(session: Session, key: str, default=None):
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value) -> None:
    upsert(session, Setting, [{"key": key, "value": value, "updated_at": int(time.time())}], ["key"])


def config_overrides(session: Session) -> dict:
    return get_setting(session, "config_overrides", {}) or {}


# --------------------------------------------------------- worker status
def heartbeat(task: str, ok: bool = True, error: str | None = None, **counters) -> None:
    now = int(time.time())
    with session_scope() as s:
        row = s.get(WorkerStatus, task)
        if row is None:
            row = WorkerStatus(task=task, counters={})
            s.add(row)
        row.heartbeat = now
        if ok:
            row.last_ok = now
        if error:
            row.last_error = error[:2000]
            row.last_error_at = now
        if counters:
            merged = dict(row.counters or {})
            for k, v in counters.items():
                if isinstance(v, (int, float)) and k.startswith("n_"):
                    merged[k] = merged.get(k, 0) + v
                else:
                    merged[k] = v
            row.counters = merged


def worker_statuses(session: Session) -> list[dict]:
    rows = session.scalars(select(WorkerStatus)).all()
    return [
        {
            "task": r.task,
            "heartbeat": r.heartbeat,
            "last_ok": r.last_ok,
            "last_error": r.last_error,
            "last_error_at": r.last_error_at,
            "counters": r.counters or {},
        }
        for r in rows
    ]
