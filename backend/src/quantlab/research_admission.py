from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import text
from sqlalchemy.engine import Engine

RESEARCH_ADVISORY_LOCK_KEY = 0x5155414E544C4142
_local_research_lock = Lock()


class ResearchAdmissionBusy(RuntimeError):
    pass


@contextmanager
def research_admission(engine: Engine) -> Iterator[None]:
    """Allow at most one heavy research child across the runtime."""
    if engine.dialect.name != "postgresql":
        acquired = _local_research_lock.acquire(blocking=False)
        if not acquired:
            raise ResearchAdmissionBusy("RESEARCH_CONCURRENCY_LIMIT")
        try:
            yield
        finally:
            _local_research_lock.release()
        return

    connection = engine.connect()
    try:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": RESEARCH_ADVISORY_LOCK_KEY},
        )
        if acquired is not True:
            raise ResearchAdmissionBusy("RESEARCH_CONCURRENCY_LIMIT")
        try:
            yield
        finally:
            try:
                released = connection.scalar(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": RESEARCH_ADVISORY_LOCK_KEY},
                )
                if released is not True:
                    connection.invalidate()
            except Exception:
                # Invalidating/closing the connection releases a session-level
                # advisory lock server-side without masking the request outcome.
                connection.invalidate()
    finally:
        connection.close()
