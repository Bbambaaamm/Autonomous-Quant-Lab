import hashlib
import json
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
    PermanentJobError,
    RunStatus,
    SchedulerService,
    ScheduleType,
    WorkerHeartbeat,
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


def test_b2_migration_accepts_columns_created_by_phase5_metadata(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = Path(__file__).parents[2]
    spec = spec_from_file_location(
        "b2_worker_lineage_migration",
        root / "alembic/versions/20260826_01_b2_worker_lineage.py",
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    class ExistingPhase5Schema:
        @staticmethod
        def get_columns(_table: str) -> list[dict[str, str]]:
            return [{"name": "deployment_id"}, {"name": "monitoring_id"}]

        @staticmethod
        def get_indexes(_table: str) -> list[dict[str, str]]:
            return [
                {"name": "ix_job_runs_deployment_id"},
                {"name": "ix_job_runs_monitoring_id"},
            ]

        @staticmethod
        def get_foreign_keys(_table: str) -> list[dict[str, str]]:
            return []

    added_columns: list[str] = []
    added_indexes: list[str] = []
    added_foreign_keys: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: ExistingPhase5Schema())
    monkeypatch.setattr(
        migration.op, "add_column", lambda _table, column: added_columns.append(column.name)
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, _table, _columns: added_indexes.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "create_foreign_key",
        lambda name, *_args, **_kwargs: added_foreign_keys.append(name),
    )

    migration.upgrade()

    assert added_columns == []
    assert added_indexes == []
    assert added_foreign_keys == [
        "fk_job_runs_deployment_id",
        "fk_job_runs_monitoring_id",
    ]


def test_legacy_snapshot_migration_separates_config_from_execution_identity() -> None:
    root = Path(__file__).parents[2]
    spec = spec_from_file_location(
        "version_snapshot_migration",
        root / "alembic/versions/20260811_04_version_job_run_snapshots.py",
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    legacy = json.dumps(
        {
            "dataset_path": "bars.csv",
            "__automation_account_id": "user-config-value",
            "snapshot_version": 1,
            "identity": {"job_type": "user-config-value"},
        }
    )
    migrated = json.loads(
        migration.version_snapshot(
            legacy,
            account_id="paper-main",
            job_type=JobType.RUN_PAPER_CYCLE,
            strategy_id="moving_average:1.0.0",
        )
    )

    assert migrated["snapshot_version"] == 1
    assert migrated["identity"] == {
        "account_id": "paper-main",
        "job_type": JobType.RUN_PAPER_CYCLE,
        "strategy_id": "moving_average:1.0.0",
    }
    assert migrated["config"] == json.loads(legacy)
    assert json.loads(migration.legacy_snapshot(json.dumps(migrated))) == json.loads(legacy)


def test_executor_rejects_unmigrated_legacy_snapshot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    legacy_run = JobRun(config_snapshot_json=json.dumps({"dataset_path": "legacy.csv"}))

    with pytest.raises(PermanentJobError, match="legacy snapshot"):
        JobExecutor(repository)(job, legacy_run)


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


def test_scheduler_recovers_session_after_duplicate_occurrence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    due = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=due,
    )
    occurrence = f"scheduled:{due.isoformat()}"
    run_id = hashlib.sha256(f"{job.id}|{occurrence}".encode()).hexdigest()
    with Session(repository.engine) as session:
        session.add(
            JobRun(
                id=run_id,
                scheduled_job_id=job.id,
                occurrence_key=occurrence,
                scheduled_for=due,
                status=RunStatus.PENDING,
                attempt_count=0,
                fencing_token=0,
                config_snapshot_json="{}",
                correlation_id=run_id,
                created_at=due,
            )
        )
        session.commit()

    assert SchedulerService(repository).tick(due) == []
    with Session(repository.engine) as session:
        stored_job = session.get(type(job), job.id)
        assert stored_job is not None
        assert stored_job.next_run_at.replace(tzinfo=UTC) == due + timedelta(seconds=60)
        assert session.scalar(select(func.count()).select_from(JobRun)) == 1


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


def test_expired_owner_cannot_renew_finish_or_fail_without_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, settings = setup(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    run_id = SchedulerService(repository).run_now(job.id, "expired-owner", now)
    worker = WorkerService(repository, settings, worker_id="expired-owner")
    assert worker.claim(now) == (run_id, 1)
    expired = now + timedelta(seconds=settings.worker_lease_timeout)
    assert worker.heartbeat(run_id, 1, expired) is False
    with pytest.raises(LeaseLost):
        worker.finish(run_id, 1, {"outcome": "STALE"}, expired)
    with pytest.raises(LeaseLost):
        worker.fail(run_id, 1, TimeoutError("stale"), expired)


def test_retry_uses_materialized_account_strategy_and_job_type_snapshot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    run_id = SchedulerService(repository).run_now(job.id, "immutable-identity", now)
    with Session(repository.engine) as session:
        stored_job = session.get(type(job), job.id)
        run = session.get(JobRun, run_id)
        assert stored_job is not None and run is not None
        stored_job.job_type = JobType.RUN_PAPER_CYCLE
        stored_job.strategy_id = "changed-strategy"
        session.commit()
        # Commit standardně expiruje ORM atributy; před detach je explicitně znovu načteme,
        # aby test předával executorovi stejný plně materializovaný objekt jako worker.
        session.refresh(stored_job)
        session.refresh(run)
        session.expunge(stored_job)
        session.expunge(run)
    executor = JobExecutor(repository)
    observed: list[str] = []
    executor.reconciliation.reconcile = lambda account_id: type(  # type: ignore[method-assign]
        "Result", (), {"id": "reconciliation", "status": observed.append(account_id) or "SUCCEEDED"}
    )()
    result = executor(stored_job, run)
    assert observed == ["paper-main"]
    assert result["reconciliation_id"] == "reconciliation"


def test_manual_run_rejects_disabled_job_at_service_boundary(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    now = datetime.now(UTC)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
        enabled=False,
    )
    with pytest.raises(ValueError, match="Zakázaný job"):
        SchedulerService(repository).run_now(job.id, "disabled", now)


def test_executor_rejects_legacy_paper_cycle_even_with_fixture_payload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repository, _ = setup(tmp_path)
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "symbol,timestamp,open,high,low,close,volume,adjusted_close,source,timeframe\n"
        "SPY,2026-01-01T20:00:00+00:00,100,101,99,100,1000,100,test,1d\n"
        "SPY,2026-01-02T20:00:00+00:00,200,201,199,200,1000,200,test,1d\n"
        "SPY,2026-01-03T20:00:00+00:00,300,301,299,300,1000,300,test,1d\n",
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
    with pytest.raises(PermanentJobError, match="legacy demo contract"):
        JobExecutor(repository)(job, run)


def test_manual_run_is_immediately_claimable_but_scheduled_run_waits(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    repository, settings = setup(tmp_path)
    now = datetime(2026, 1, 2, 12, tzinfo=UTC)
    future = now + timedelta(hours=1)
    job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        next_run_at=future + timedelta(days=1),
    )
    manual_run = SchedulerService(repository).run_now(job.id, "future-logical-time", future)

    worker = WorkerService(repository, settings, executor=lambda *_: {}, worker_id="manual-worker")
    claimed = worker.claim(now)

    assert claimed is not None and claimed[0] == manual_run

    scheduled_job = repository.create_job(
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        next_run_at=future,
    )
    scheduled_runs = SchedulerService(repository).tick(future)
    assert len(scheduled_runs) == 1
    scheduled_worker = WorkerService(
        repository,
        settings,
        executor=lambda *_: {},
        worker_id="scheduled-worker",
    )
    assert scheduled_worker.claim(now) is None
    with Session(repository.engine) as session:
        stored = session.get(JobRun, scheduled_runs[0])
        assert stored is not None and stored.scheduled_job_id == scheduled_job.id
