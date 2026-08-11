from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobAttempt,
    JobRun,
    JobType,
    LeaseLost,
    MisfirePolicy,
    RunStatus,
    SchedulerService,
    ScheduleType,
    WorkerService,
    next_occurrence,
)
from quantlab.config import Settings
from quantlab.phase4 import Phase4Repository


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
