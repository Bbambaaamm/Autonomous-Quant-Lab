from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobRun,
    JobType,
    ScheduledJob,
)
from quantlab.market_data import XNYSCalendar
from quantlab.persistence import StrategyDeploymentRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_stage_c_autonomous_opt_in_and_session_occurrence_are_idempotent() -> None:
    repository = AutomationRepository(os.environ["DATABASE_URL"])
    with Session(repository.engine) as session:
        deployment = session.scalar(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status == "APPROVED")
            .order_by(StrategyDeploymentRecord.created_at.desc())
        )
    if deployment is None:
        pytest.skip("Stage C navazuje na B1/B2 acceptance deployment")

    now = datetime(2026, 7, 3, 18, tzinfo=UTC)
    first = repository.set_autonomous_deployment(
        deployment_id=deployment.deployment_id, enabled=True, now=now
    )
    second = repository.set_autonomous_deployment(
        deployment_id=deployment.deployment_id, enabled=True, now=now
    )
    assert first.id == second.id
    assert first.job_type == JobType.PREPARE_PAPER_SESSION
    assert json.loads(first.config_json) == {"deployment_id": deployment.deployment_id}

    calendar = XNYSCalendar()
    friday = date(2026, 7, 3)
    # Independence Day observed: Friday není session a next session je Monday.
    assert not calendar.is_session(friday)
    signal = date(2026, 7, 2)
    execution = calendar.next_session(signal)
    execution_open = calendar.session_open(execution)
    run_a = repository.materialize_execution_session(
        deployment_id=deployment.deployment_id,
        account_id=deployment.paper_account_id,
        execution_session=execution,
        execution_time=execution_open,
        created_at=now,
    )
    run_b = repository.materialize_execution_session(
        deployment_id=deployment.deployment_id,
        account_id=deployment.paper_account_id,
        execution_session=execution,
        execution_time=execution_open,
        created_at=now,
    )
    assert run_a == run_b
    with Session(repository.engine) as session:
        assert session.scalar(select(JobRun).where(JobRun.id == run_a)) is not None
        assert len(list(session.scalars(select(JobRun).where(JobRun.id == run_a)))) == 1

        persisted = session.get(JobRun, run_a)
        assert persisted is not None and persisted.scheduled_for == execution_open

    disabled = repository.set_autonomous_deployment(
        deployment_id=deployment.deployment_id, enabled=False, now=now
    )
    assert disabled.enabled is False
    with Session(repository.engine) as session:
        stored = session.get(ScheduledJob, first.id)
        assert stored is not None and stored.enabled is False


def test_xnys_early_close_and_dst_are_calendar_authoritative() -> None:
    calendar = XNYSCalendar()
    assert calendar.session_close(date(2026, 11, 27)).hour == 18
    assert calendar.session_open(date(2026, 3, 6)).hour == 14
    assert calendar.session_open(date(2026, 3, 9)).hour == 13
