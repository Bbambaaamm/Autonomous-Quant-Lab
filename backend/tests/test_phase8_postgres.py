from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import AuditEventType
from quantlab.phase4 import AuditEventRecord, PaperAccountRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


@pytest.fixture
def api(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    import importlib

    import quantlab.api as module
    from quantlab.config import get_settings

    get_settings.cache_clear()
    module = importlib.reload(module)
    with Session(module.paper_repository.engine) as session:
        account = session.get(PaperAccountRecord, "paper-main")
        assert account is not None
        account.trading_state = "NORMAL"
        account.reconciliation_safe = True
        session.commit()
    return TestClient(module.app), module


def test_postgres_operator_halt_resume_persist_and_are_auditable(api) -> None:  # type: ignore[no-untyped-def]
    client, module = api

    halted = client.post(
        "/operator/risk/halt",
        json={"confirmation": "HALT", "reason": "PostgreSQL audit proof"},
    )
    assert halted.status_code == 200

    with Session(module.paper_repository.engine) as independent_session:
        account = independent_session.get(PaperAccountRecord, "paper-main")
        assert account is not None and account.trading_state == "HALTED"
        halt_event = independent_session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == AuditEventType.KILL_SWITCH_MANUAL_HALT
            )
        )
        assert halt_event is not None

    assert client.get("/operator/overview").json()["trading_state"] == "HALTED"
    assert client.get("/operator/risk").json()["trading_state"] == "HALTED"
    halt_audit = client.get(
        "/operator/audit?event_type=KILL_SWITCH_MANUAL_HALT&entity_id=paper-main"
    ).json()
    assert halt_audit["total"] >= 1

    resumed = client.post(
        "/operator/risk/resume",
        json={"confirmation": "RESUME", "reason": "Reconciliation je bezpečná"},
    )
    assert resumed.status_code == 200
    with Session(module.paper_repository.engine) as independent_session:
        account = independent_session.get(PaperAccountRecord, "paper-main")
        assert account is not None and account.trading_state == "NORMAL"
        resume_event = independent_session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == AuditEventType.KILL_SWITCH_RESUMED
            )
        )
        assert resume_event is not None


def test_postgres_concurrent_manual_halts_remain_fail_closed(api) -> None:  # type: ignore[no-untyped-def]
    _client, module = api
    barrier = Barrier(2)

    def halt() -> None:
        barrier.wait(timeout=10)
        module.paper_repository.halt(
            "paper-main",
            "Souběžný safety požadavek",
            str(uuid4()),
            AuditEventType.KILL_SWITCH_MANUAL_HALT,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(halt), pool.submit(halt)]
        for future in futures:
            future.result(timeout=30)

    with Session(module.paper_repository.engine) as independent_session:
        account = independent_session.get(PaperAccountRecord, "paper-main")
        assert account is not None and account.trading_state == "HALTED"

    module.paper_repository.resume(
        "paper-main", str(uuid4()), "Úklid po concurrency regression testu"
    )
