from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from quantlab.automation import (
    AUTONOMOUS_DAILY_TIME,
    AUTONOMOUS_TIMEZONE,
    MONITOR_INTERVAL_SECONDS,
    AutomationRepository,
    MisfirePolicy,
    ScheduleType,
    ScheduledJob,
    daily_occurrence_at_or_after,
)
from quantlab.market_data import XNYSCalendar
from quantlab.phase4 import Phase4Repository


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
    assert repaired.next_run_at == now + timedelta(minutes=1)


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
