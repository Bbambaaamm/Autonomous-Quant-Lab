import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.automation import (
    AttemptStatus,
    AutomationRepository,
    JobAttempt,
    JobExecutor,
    JobRun,
    JobType,
    RunStatus,
    ScheduledJob,
    SchedulerService,
    ScheduleType,
    WorkerService,
)
from quantlab.config import Settings
from quantlab.phase4 import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    PositionRecord,
    ReconciliationRecord,
    RiskDecisionRecord,
    TradingCycleRecord,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def repository() -> tuple[AutomationRepository, Settings, str]:
    url = os.environ["DATABASE_URL"]
    account_id = f"phase5-{uuid4()}"
    Phase4Repository(url, bootstrap_test_schema=False).seed_account(account_id)
    return (
        AutomationRepository(url),
        Settings(
            database_url=url,
            automation_enabled=True,
            worker_lease_timeout=2,
            worker_heartbeat_interval=1,
            retry_base_delay=1,
            retry_max_delay=2,
        ),
        account_id,
    )


def concurrent_calls(call_a, call_b):  # type: ignore[no-untyped-def]
    barrier = Barrier(2)

    def synchronized(call):  # type: ignore[no-untyped-def]
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return [
            future.result(timeout=15)
            for future in (pool.submit(synchronized, call_a), pool.submit(synchronized, call_b))
        ]


def test_postgres_concurrent_schedulers_materialize_one_occurrence() -> None:
    repo, _, account_id = repository()
    due = datetime(2026, 8, 11, 20, tzinfo=UTC)
    job = repo.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id=account_id,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=due,
    )
    results = concurrent_calls(
        lambda: SchedulerService(AutomationRepository(str(repo.engine.url))).tick(due),
        lambda: SchedulerService(AutomationRepository(str(repo.engine.url))).tick(due),
    )
    assert sorted(len(result) for result in results) == [0, 1]
    with Session(repo.engine) as session:
        runs = list(session.scalars(select(JobRun).where(JobRun.scheduled_job_id == job.id)))
        assert len(runs) == 1
        assert runs[0].occurrence_key == f"scheduled:{due.isoformat()}"
        stored_job = session.get(ScheduledJob, job.id)
        assert stored_job is not None
        assert stored_job.last_run_at == due
        assert stored_job.next_run_at == due + timedelta(seconds=60)
        # Neověřujeme jen constraint: session je po konkurenčním ticku dále použitelná.
        assert session.scalar(select(func.count()).select_from(ScheduledJob)) >= 1
        runs[0].status = RunStatus.CANCELLED
        runs[0].finished_at = datetime.now(UTC)
        session.commit()


def test_postgres_two_workers_claim_exactly_one_execution_owner() -> None:
    repo, settings, account_id = repository()
    now = datetime.now(UTC)
    job = repo.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id=account_id,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    run_id = SchedulerService(repo).run_now(job.id, str(uuid4()), now)
    worker_a = WorkerService(AutomationRepository(str(repo.engine.url)), settings, worker_id="A")
    worker_b = WorkerService(AutomationRepository(str(repo.engine.url)), settings, worker_id="B")
    claims = concurrent_calls(lambda: worker_a.claim(now), lambda: worker_b.claim(now))
    assert sum(claim is not None for claim in claims) == 1
    assert {claim for claim in claims if claim is not None} == {(run_id, 1)}
    with Session(repo.engine) as session:
        run = session.get(JobRun, run_id)
        assert run is not None and run.status == RunStatus.RUNNING
        assert run.lease_owner in {"A", "B"}
        assert run.lease_expires_at is not None and run.lease_expires_at > now
        attempts = list(session.scalars(select(JobAttempt).where(JobAttempt.job_run_id == run_id)))
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.RUNNING
        assert attempts[0].worker_id == run.lease_owner
        assert attempts[0].fencing_token == run.fencing_token == 1


def market_data(path: Path, decision_time: datetime) -> None:
    path.write_text(
        "symbol,timestamp,open,high,low,close,volume,adjusted_close,source,timeframe\n"
        f"SPY,{decision_time.isoformat()},100,102,99,101,10000,101,test,1d\n"
        f"SPY,{(decision_time + timedelta(days=1)).isoformat()},100,102,99,101,10000,101,test,1d\n",
        encoding="utf-8",
    )


def paper_job(repo: AutomationRepository, account_id: str, path: Path, now: datetime):  # type: ignore[no-untyped-def]
    job = repo.create_job(
        job_type=JobType.RUN_PAPER_CYCLE,
        account_id=account_id,
        strategy_id=f"phase5-recovery:{uuid4()}",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
        config={
            "dataset_path": str(path),
            "symbol": "SPY",
            "target_weights": {"SPY": "0.10"},
            "session_date": now.date().isoformat(),
        },
    )
    run_id = SchedulerService(repo).run_now(job.id, str(uuid4()), now)
    with Session(repo.engine) as session:
        run = session.get(JobRun, run_id)
        assert run is not None
        session.expunge(run)
    return job, run


def test_postgres_recovers_crash_after_economic_commit(tmp_path: Path) -> None:
    repo, settings, account_id = repository()
    now = datetime.now(UTC).replace(microsecond=0)
    path = tmp_path / "recovery.csv"
    market_data(path, now)
    job, detached_run = paper_job(repo, account_id, path, now)
    crashed = WorkerService(repo, settings, worker_id="crashed-worker")
    assert crashed.claim(now) == (detached_run.id, 1)
    economic_result = JobExecutor(repo)(job, detached_run)
    assert economic_result["trading_cycle_id"] is not None
    # Simulace pádu: ekonomický commit proběhl, finish JobRun nikoli.
    recovered = WorkerService(
        AutomationRepository(str(repo.engine.url)), settings, worker_id="recovered-worker"
    )
    assert recovered.execute_one(now + timedelta(seconds=3)) == detached_run.id
    with Session(repo.engine) as session:
        run = session.get(JobRun, detached_run.id)
        assert run is not None and run.status == RunStatus.SUCCEEDED
        assert run.attempt_count == 2
        assert run.trading_cycle_id == economic_result["trading_cycle_id"]
        attempts = list(
            session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_run_id == run.id)
                .order_by(JobAttempt.attempt_number)
            )
        )
        assert [attempt.status for attempt in attempts] == [
            AttemptStatus.LEASE_LOST,
            AttemptStatus.SUCCEEDED,
        ]
        assert (
            session.scalar(
                select(func.count())
                .select_from(TradingCycleRecord)
                .where(TradingCycleRecord.account_id == account_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RiskDecisionRecord)
                .where(
                    RiskDecisionRecord.account_id == account_id,
                    RiskDecisionRecord.status == "APPROVED",
                )
            )
            == 1
        )
        order = session.scalar(
            select(PaperOrderRecord).where(PaperOrderRecord.account_id == account_id)
        )
        assert order is not None
        fills = list(
            session.scalars(select(PaperFillRecord).where(PaperFillRecord.order_id == order.id))
        )
        assert len(fills) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            )
            == 1
        )
        account = session.get(PaperAccountRecord, account_id)
        position = session.get(PositionRecord, (account_id, "SPY"))
        assert account is not None and position is not None
        assert account.cash == (
            account.starting_cash - fills[0].price * fills[0].quantity - fills[0].commission
        )
        assert position.quantity == fills[0].quantity
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReconciliationRecord)
                .where(
                    ReconciliationRecord.account_id == account_id,
                    ReconciliationRecord.status == "SUCCEEDED",
                )
            )
            == 1
        )


def test_postgres_account_lock_serializes_cycle_and_reconciliation(tmp_path: Path) -> None:
    repo, _, account_id = repository()
    now = datetime.now(UTC).replace(microsecond=0)
    path = tmp_path / "locking.csv"
    market_data(path, now)
    paper, paper_run = paper_job(repo, account_id, path, now)
    reconciliation = repo.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id=account_id,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    reconciliation_id = SchedulerService(repo).run_now(reconciliation.id, str(uuid4()), now)
    with Session(repo.engine) as session:
        reconciliation_run = session.get(JobRun, reconciliation_id)
        assert reconciliation_run is not None
        session.expunge(reconciliation_run)
    executor_a = JobExecutor(AutomationRepository(str(repo.engine.url)))
    executor_b = JobExecutor(AutomationRepository(str(repo.engine.url)))
    cycle_entered = Event()
    release_cycle = Event()
    reconciliation_entered = Event()
    original_cycle_run = executor_a.trading.run
    original_reconcile = executor_b.reconciliation.reconcile

    def held_cycle(*args, **kwargs):  # type: ignore[no-untyped-def]
        cycle_entered.set()
        assert release_cycle.wait(timeout=5)
        return original_cycle_run(*args, **kwargs)

    def observed_reconciliation(*args, **kwargs):  # type: ignore[no-untyped-def]
        reconciliation_entered.set()
        return original_reconcile(*args, **kwargs)

    executor_a.trading.run = held_cycle  # type: ignore[method-assign]
    executor_b.reconciliation.reconcile = observed_reconciliation  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=2) as pool:
        cycle_future = pool.submit(executor_a, paper, paper_run)
        assert cycle_entered.wait(timeout=5)
        reconciliation_future = pool.submit(executor_b, reconciliation, reconciliation_run)
        # Druhý executor nesmí vstoupit do reconciliation, dokud první drží account lock.
        assert not reconciliation_entered.wait(timeout=0.5)
        release_cycle.set()
        results = [cycle_future.result(timeout=15), reconciliation_future.result(timeout=15)]
    assert reconciliation_entered.is_set()
    assert {result["outcome"] for result in results} == {"PROCESSED", "SUCCEEDED"}
    with Session(repo.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            )
            == 1
        )
        reconciliations = list(
            session.scalars(
                select(ReconciliationRecord).where(ReconciliationRecord.account_id == account_id)
            )
        )
        assert reconciliations and all(row.status == "SUCCEEDED" for row in reconciliations)
