from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobAttempt,
    JobExecutor,
    JobRun,
    JobType,
    LeaseLost,
    MisfirePolicy,
    RunStatus,
    SchedulerService,
    ScheduleType,
    TransientJobError,
    WorkerHeartbeat,
    WorkerService,
    next_occurrence,
)
from quantlab.config import Settings
from quantlab.phase4 import Phase4Repository, TradingCycleRecord


def test_migration_revisions_own_only_their_tables() -> None:
    root = Path(__file__).parents[2]

    def load(name: str, path: Path):  # type: ignore[no-untyped-def]
        spec = spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    initial = load(
        "initial_migration", root / "alembic/versions/20260810_01_production_registry.py"
    )
    phase5 = load("phase5_migration", root / "alembic/versions/20260811_03_phase5_automation.py")
    assert initial.INITIAL_TABLES.isdisjoint(phase5.TABLES)
    assert "scheduled_jobs" not in initial.INITIAL_TABLES
    assert "paper_accounts" not in initial.INITIAL_TABLES


def setup(tmp_path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'phase5.db'}"
    Phase4Repository(url, bootstrap_test_schema=True).seed_account()
    return AutomationRepository(url), Settings(
        database_url=url,
        automation_enabled=True,
        worker_lease_timeout=10,
        worker_heartbeat_interval=2,
        retry_base_delay=3,
        retry_max_delay=20,
    )


def test_scheduler_is_idempotent_and_advances_without_drift(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    due = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=due,
    )
    scheduler = SchedulerService(repository)
    assert len(scheduler.tick(due + timedelta(seconds=5))) == 1
    assert scheduler.tick(due + timedelta(seconds=5)) == []
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count()).select_from(JobRun)) == 1
        assert session.get(type(job), job.id).next_run_at.replace(tzinfo=UTC) == due + timedelta(
            seconds=60
        )  # type: ignore[union-attr]


def test_misfire_skip_does_not_materialize_backlog(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    due = datetime(2026, 1, 1, tzinfo=UTC)
    repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=due,
        misfire_policy=MisfirePolicy.SKIP_IF_TOO_OLD,
        misfire_grace_seconds=10,
    )
    assert SchedulerService(repository).tick(due + timedelta(hours=2)) == []


def test_worker_retry_backoff_and_fencing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, settings = setup(tmp_path)
    due = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=due,
        max_attempts=2,
    )
    run_id = SchedulerService(repository).run_now(job.id, "once", due)
    worker_a = WorkerService(
        repository,
        settings,
        executor=lambda *_: (_ for _ in ()).throw(TimeoutError("temporary")),
        worker_id="A",
    )
    assert worker_a.execute_one(due) == run_id
    with Session(repository.engine) as session:
        run = session.get(JobRun, run_id)
        assert run is not None and run.status == RunStatus.RETRY_SCHEDULED
        assert run.next_attempt_at.replace(tzinfo=UTC) == due + timedelta(seconds=3)
    worker_b = WorkerService(
        repository,
        settings,
        executor=lambda *_: {"outcome": "OK", "trading_cycle_id": None, "reconciliation_id": None},
        worker_id="B",
    )
    claimed = worker_b.claim(due + timedelta(seconds=3))
    assert claimed is not None
    with pytest.raises(LeaseLost):
        worker_a.finish(run_id, 1, {"outcome": "STALE"}, due + timedelta(seconds=4))
    worker_b.finish(run_id, claimed[1], {"outcome": "OK"}, due + timedelta(seconds=4))
    with Session(repository.engine) as session:
        assert session.get(JobRun, run_id).status == RunStatus.SUCCEEDED  # type: ignore[union-attr]
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 2


def test_manual_trigger_and_unsafe_payload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    now = datetime.now(UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.DAILY,
        daily_time="09:30",
        timezone="America/New_York",
        next_run_at=now,
    )
    scheduler = SchedulerService(repository)
    assert scheduler.run_now(job.id, "operator-1", now) == scheduler.run_now(
        job.id, "operator-1", now
    )
    assert next_occurrence(job, now) > now
    with pytest.raises(ValueError, match="execution mode"):
        repository.create_job(
            job_type=JobType.RUN_RECONCILIATION,
            account_id="paper-main",
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=10,
            next_run_at=now,
            config={"mode": "live"},
        )


def test_invalid_worker_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="Heartbeat"):
        Settings(worker_heartbeat_interval=60, worker_lease_timeout=30)


def test_daily_time_is_validated_when_job_is_created(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="HH:MM"):
        repository.create_job(
            job_type=JobType.RUN_RECONCILIATION,
            account_id="paper-main",
            schedule_type=ScheduleType.DAILY,
            daily_time="25:00",
            next_run_at=datetime.now(UTC),
        )


def test_worker_heartbeat_page_uses_heartbeat_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, settings = setup(tmp_path)
    worker = WorkerService(repository, settings, worker_id="worker-health")
    assert worker.heartbeat(now=datetime.now(UTC))
    rows = repository.page(WorkerHeartbeat)
    assert [row.worker_id for row in rows] == ["worker-health"]


def test_reclaim_closes_superseded_attempt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, settings = setup(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    run_id = SchedulerService(repository).run_now(job.id, "expired", now)
    worker_a = WorkerService(repository, settings, worker_id="expired-worker")
    assert worker_a.claim(now) == (run_id, 1)
    worker_b = WorkerService(repository, settings, worker_id="replacement-worker")
    assert worker_b.claim(now + timedelta(seconds=11)) == (run_id, 2)
    with Session(repository.engine) as session:
        old_attempt = session.scalar(
            select(JobAttempt).where(
                JobAttempt.job_run_id == run_id, JobAttempt.attempt_number == 1
            )
        )
        assert old_attempt is not None
        assert old_attempt.status == "LEASE_LOST"
        assert old_attempt.finished_at is not None
        assert old_attempt.retryable is True


def test_executor_bounds_bars_and_rejects_incomplete_cycle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "symbol,timestamp,open,high,low,close,volume,adjusted_close,source,timeframe\n"
        "SPY,2026-01-01T20:00:00+00:00,100,101,99,100,1000,100,test,1d\n"
        "SPY,2026-01-02T20:00:00+00:00,200,201,199,200,1000,200,test,1d\n",
        encoding="utf-8",
    )
    decision_time = datetime(2026, 1, 1, 20, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_PAPER_CYCLE,
        account_id="paper-main",
        strategy_id="moving_average:1.0.0",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=decision_time,
        config={"dataset_path": str(csv_path), "symbol": "SPY", "target_weights": {}},
    )
    run_id = SchedulerService(repository).run_now(job.id, "point-in-time", decision_time)
    with Session(repository.engine) as session:
        run = session.get(JobRun, run_id)
        assert run is not None
        session.expunge(run)
    executor = JobExecutor(repository)
    observed_timestamps: list[datetime] = []

    def incomplete_run(
        account_id, strategy_id, bars, target_weights, session_date, supplied_decision_time
    ):  # type: ignore[no-untyped-def]
        observed_timestamps.extend(bar.timestamp for bar in bars)
        cycle_id = "incomplete-cycle"
        with Session(repository.engine) as session:
            session.add(
                TradingCycleRecord(
                    id=cycle_id,
                    cycle_key=cycle_id,
                    account_id=account_id,
                    strategy_id=strategy_id,
                    session_date=session_date,
                    started_at=decision_time,
                    status="RUNNING",
                    correlation_id=cycle_id,
                    data_fingerprint="test",
                    lease_owner="phase4-worker",
                    lease_expires_at=decision_time + timedelta(minutes=5),
                )
            )
            session.commit()
        return cycle_id

    executor.trading.run = incomplete_run  # type: ignore[method-assign]
    with pytest.raises(TransientJobError, match="stále RUNNING"):
        executor(job, run)
    assert observed_timestamps == [decision_time]
