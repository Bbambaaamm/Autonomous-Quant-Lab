from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from quantlab.automation import (
    PREOPEN_EXECUTION_INTENT_BLOCK,
    AutomationRepository,
    JobExecutor,
    JobRun,
    RunStatus,
    WorkerService,
)
from quantlab.config import Settings
from quantlab.market_data import XNYSCalendar
from quantlab.persistence import StrategyDeploymentRecord
from quantlab.phase4 import PaperAccountRecord, PaperFillRecord, PaperOrderRecord, PositionRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def _economic_state(session: Session, account_id: str) -> tuple[object, ...]:
    account = session.get(PaperAccountRecord, account_id)
    assert account is not None
    positions = tuple(
        session.execute(
            select(PositionRecord.instrument_id, PositionRecord.quantity)
            .where(PositionRecord.account_id == account_id)
            .order_by(PositionRecord.instrument_id)
        )
    )
    return (
        account.cash,
        positions,
        session.scalar(
            select(func.count())
            .select_from(PaperOrderRecord)
            .where(PaperOrderRecord.account_id == account_id)
        ),
        session.scalar(
            select(func.count())
            .select_from(PaperFillRecord)
            .join(PaperOrderRecord)
            .where(PaperOrderRecord.account_id == account_id)
        ),
    )


def test_worker_restart_five_minutes_after_open_is_stable_no_action() -> None:
    repository = AutomationRepository(os.environ["DATABASE_URL"])
    calendar = XNYSCalendar()
    execution_session = date(2026, 7, 6)
    execution_open = calendar.session_open(execution_session)
    late = execution_open + timedelta(minutes=5)
    with Session(repository.engine) as session, session.begin():
        deployment = session.scalar(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status == "APPROVED")
            .order_by(StrategyDeploymentRecord.created_at.desc())
        )
        if deployment is None:
            pytest.skip("P0 regression navazuje na B1/B2 acceptance deployment")
        session.execute(
            update(JobRun)
            .where(JobRun.status.in_((RunStatus.PENDING, RunStatus.RETRY_SCHEDULED)))
            .values(status=RunStatus.CANCELLED, finished_at=late)
        )
        account_id = deployment.paper_account_id
        deployment_id = deployment.deployment_id
        before = _economic_state(session, account_id)

    run_id = repository.materialize_execution_session(
        deployment_id=deployment_id,
        account_id=account_id,
        execution_session=execution_session,
        execution_time=execution_open,
        created_at=execution_open - timedelta(minutes=1),
    )
    settings = Settings(database_url=os.environ["DATABASE_URL"], automation_enabled=True)
    worker = WorkerService(
        repository,
        settings,
        executor=JobExecutor(repository, clock=lambda: late),
        worker_id="p0-missed-open-worker",
    )
    assert worker.execute_one(late) == run_id

    with Session(repository.engine) as session:
        stored = session.get(JobRun, run_id)
        assert stored is not None
        assert stored.status == RunStatus.SUCCEEDED
        assert stored.outcome == "NO_ACTION"
        # Legacy/materialized XNYS occurrence nemá sealed immutable pre-open intent.
        # Po upgradu proto failuje dříve a přísněji než samotný missed-open cutoff.
        assert stored.no_action_reason == PREOPEN_EXECUTION_INTENT_BLOCK
        assert stored.trading_cycle_id is None
        assert _economic_state(session, account_id) == before

    # Stejná occurrence je terminální a materializace ani další claim ji neoživí.
    assert (
        repository.materialize_execution_session(
            deployment_id=deployment_id,
            account_id=account_id,
            execution_session=execution_session,
            execution_time=execution_open,
            created_at=late,
        )
        == run_id
    )
    assert worker.execute_one(late) is None
