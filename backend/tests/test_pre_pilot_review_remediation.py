from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from quantlab.automation import (
    AUTONOMOUS_DAILY_TIME,
    AUTONOMOUS_TIMEZONE,
    MONITOR_INTERVAL_SECONDS,
    PREOPEN_EXECUTION_INTENT_BLOCK,
    AutomationRepository,
    JobRun,
    JobType,
    MisfirePolicy,
    RunStatus,
    ScheduledJob,
    ScheduleType,
    daily_occurrence_at_or_after,
)
from quantlab.market_data import XNYSCalendar
from quantlab.phase4 import AuditEventRecord, Phase4Repository


def test_monitoring_schedule_ensure_reenables_and_normalizes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'monitoring.db'}"
    Phase4Repository(database_url, bootstrap_test_schema=True).seed_account()
    repository = AutomationRepository(database_url)
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    job = repository.ensure_monitoring_job(
        monitoring_id="monitoring-test", account_id="paper-main", now=now
    )
    with Session(repository.engine) as session:
        stored = session.get(ScheduledJob, job.id)
        assert stored is not None
        stored.enabled = False
        stored.interval_seconds = 60
        session.commit()
    repaired = repository.ensure_monitoring_job(
        monitoring_id="monitoring-test",
        account_id="paper-main",
        now=now + timedelta(minutes=1),
    )
    assert repaired.id == job.id
    assert repaired.enabled is True
    assert repaired.schedule_type == ScheduleType.INTERVAL
    assert repaired.interval_seconds == MONITOR_INTERVAL_SECONDS
    assert repaired.misfire_policy == MisfirePolicy.RUN_ONCE_IF_MISSED
    assert repaired.next_run_at.replace(tzinfo=UTC) == now + timedelta(minutes=1)


def test_autonomous_daily_occurrence_tracks_new_york_dst() -> None:
    summer = daily_occurrence_at_or_after(
        datetime(2026, 7, 6, 12, tzinfo=UTC),
        AUTONOMOUS_DAILY_TIME,
        AUTONOMOUS_TIMEZONE,
    )
    winter = daily_occurrence_at_or_after(
        datetime(2026, 1, 5, 13, tzinfo=UTC),
        AUTONOMOUS_DAILY_TIME,
        AUTONOMOUS_TIMEZONE,
    )
    assert summer == datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
    assert winter == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def test_executable_open_window_remains_fail_closed() -> None:
    calendar = XNYSCalendar()
    session = date(2026, 7, 6)
    opened = calendar.session_open(session)
    assert calendar.is_executable_open_time(session, opened)
    assert calendar.is_executable_open_time(session, opened + timedelta(milliseconds=999))
    assert not calendar.is_executable_open_time(session, opened + timedelta(seconds=1))


def test_legacy_autonomous_schedule_is_disabled_before_polling(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'legacy-schedule.db'}"
    Phase4Repository(database_url, bootstrap_test_schema=True).seed_account()
    repository = AutomationRepository(database_url)
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.PREPARE_PAPER_SESSION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=300,
        next_run_at=now,
        config={"deployment_id": "legacy-deployment"},
    )
    assert repository.reconcile_managed_schedules(now) == 1
    with Session(repository.engine) as session:
        stored = session.get(ScheduledJob, job.id)
        assert stored is not None
        assert stored.enabled is False


def test_managed_retry_and_audit_commit_in_one_repository_transaction(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'atomic-retry.db'}"
    Phase4Repository(database_url, bootstrap_test_schema=True).seed_account()
    repository = AutomationRepository(database_url)
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    job = repository.create_job(
        job_type=JobType.PREPARE_PAPER_SESSION,
        account_id="paper-main",
        schedule_type=ScheduleType.DAILY,
        daily_time=AUTONOMOUS_DAILY_TIME,
        timezone=AUTONOMOUS_TIMEZONE,
        next_run_at=now,
        misfire_policy=MisfirePolicy.SKIP_IF_TOO_OLD,
        misfire_grace_seconds=1,
        max_attempts=1,
        config={"deployment_id": "deployment-test"},
    )
    with Session(repository.engine) as session:
        session.add(
            JobRun(
                id="run-test",
                scheduled_job_id=job.id,
                occurrence_key="test:dead-letter",
                scheduled_for=now,
                status=RunStatus.DEAD_LETTER,
                attempt_count=1,
                fencing_token=1,
                config_snapshot_json="{}",
                correlation_id="original-correlation",
                created_at=now,
                finished_at=now,
            )
        )
        session.commit()
    retried = repository.retry_managed_run(
        "run-test",
        actor={"actor_id": "admin", "actor_role": "ADMIN", "authentication": "bearer"},
        reason="operator recovery",
        correlation_id="retry-correlation",
        now=now + timedelta(seconds=1),
    )
    assert retried.status == RunStatus.RETRY_SCHEDULED
    with Session(repository.engine) as session:
        stored = session.get(JobRun, "run-test")
        assert stored is not None and stored.status == RunStatus.RETRY_SCHEDULED
        audit = (
            session.query(AuditEventRecord)
            .filter_by(event_type="CONTROL_AUTOMATION_RUN_RETRY", entity_id="run-test")
            .one()
        )
        assert audit.correlation_id == "retry-correlation"


def test_autonomous_execution_stays_fail_closed_without_preopen_order_intent() -> None:
    assert PREOPEN_EXECUTION_INTENT_BLOCK == "PREOPEN_EXECUTION_INTENT_NOT_PERSISTED"
