from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker
from test_phase7_postgres import _executed_monitoring

from quantlab.automation import (
    AutomationRepository,
    JobRun,
    JobType,
    RunStatus,
    ScheduledJob,
    SchedulerService,
    ScheduleType,
    WorkerService,
)
from quantlab.config import Settings
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import MarketObservationRecord
from quantlab.phase4 import PaperFillRecord, PaperOrderRecord, TradingCycleRecord
from quantlab.phase7 import PaperPerformanceEvaluationRecord, PaperPerformanceSnapshotRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


@pytest.fixture
def factory():
    from sqlalchemy import create_engine

    return sessionmaker(create_engine(os.environ["DATABASE_URL"]), expire_on_commit=False)


def _counts(factory, account_id: str) -> tuple[int, int, int, int, int]:
    with factory() as session:
        return (
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
            session.scalar(
                select(func.count())
                .select_from(TradingCycleRecord)
                .where(TradingCycleRecord.account_id == account_id)
            ),
            session.scalar(select(func.count()).select_from(PaperPerformanceSnapshotRecord)),
            session.scalar(select(func.count()).select_from(PaperPerformanceEvaluationRecord)),
        )


def _isolate_automation_queue(repository: AutomationRepository, now: datetime) -> None:
    """Ukončí pouze nevyřízenou evidenci, kterou ve sdílené DB zanechaly starší testy."""
    with Session(repository.engine) as session:
        session.execute(update(ScheduledJob).values(enabled=False, updated_at=now))
        session.execute(
            update(JobRun)
            .where(JobRun.status.in_((RunStatus.PENDING, RunStatus.RETRY_SCHEDULED)))
            .values(status=RunStatus.CANCELLED, finished_at=now, next_attempt_at=None)
        )
        session.commit()


def test_monitoring_automation_production_e2e_is_non_economic_and_retry_idempotent(factory) -> None:
    account_id, _deployment, run, instrument = _executed_monitoring(factory)
    repository = AutomationRepository(str(factory.kw["bind"].url))
    execution_time = datetime.now(UTC)
    _isolate_automation_queue(repository, execution_time)
    completed_session = CALENDAR.latest_completed_session(execution_time)
    with factory() as session:
        price = Decimal(
            session.scalar(
                select(MarketObservationRecord.close)
                .where(MarketObservationRecord.instrument_id == instrument.instrument_id)
                .order_by(MarketObservationRecord.session_date.desc())
                .limit(1)
            )
        )
    PersistentMarketDataService(factory).ingest(
        MappingProvider(
            f"automation-{uuid4().hex[:20]}",
            {instrument.symbol: [daily_bar(completed_session, price, "automation-monitoring")]},
            {},
        ),
        instrument,
        completed_session,
        completed_session,
        CALENDAR.session_close(completed_session),
    )
    due = execution_time
    job = repository.create_job(
        job_type=JobType.MONITOR_PAPER_DEPLOYMENT,
        account_id=account_id,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        next_run_at=due,
        config={"monitoring_id": run.monitoring_id},
    )
    settings = Settings(
        database_url=str(factory.kw["bind"].url),
        automation_enabled=True,
        worker_lease_timeout=30,
        worker_heartbeat_interval=2,
    )
    before = _counts(factory, account_id)
    run_ids = SchedulerService(repository).tick(due + timedelta(seconds=1))
    assert len(run_ids) == 1
    assert (
        WorkerService(repository, settings, worker_id="phase7-monitor-1").execute_one()
        == run_ids[0]
    )
    after_first = _counts(factory, account_id)
    with Session(repository.engine) as session:
        stored = session.get(JobRun, run_ids[0])
        assert stored.status == RunStatus.SUCCEEDED
        assert stored.trading_cycle_id is None and stored.reconciliation_id is None
    assert after_first[:3] == before[:3]
    assert after_first[3:] == (before[3] + 1, before[4] + 1)

    retry_id = SchedulerService(repository).run_now(
        job.id, "phase7-idempotent-retry", due + timedelta(seconds=1)
    )
    assert (
        WorkerService(repository, settings, worker_id="phase7-monitor-2").execute_one() == retry_id
    )
    after_retry = _counts(factory, account_id)
    assert after_retry == after_first
    with Session(repository.engine) as session:
        retried = session.get(JobRun, retry_id)
        assert retried.status == RunStatus.SUCCEEDED
        assert retried.trading_cycle_id is None and retried.reconciliation_id is None
