from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobRun,
    JobType,
    RunStatus,
    SchedulerService,
    ScheduleType,
    WorkerHeartbeat,
    WorkerService,
)
from quantlab.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def _settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        automation_enabled=True,
        worker_poll_interval=0.1,
        worker_heartbeat_interval=1,
        worker_lease_timeout=5,
    )


def test_p0b_scheduler_worker_heartbeat_claim_and_restart_are_persistent() -> None:
    repository = AutomationRepository(os.environ["DATABASE_URL"])
    now = datetime.now(UTC)
    job = repository.create_job(
        job_id=f"p0b-{uuid4()}",
        job_type=JobType.RUN_RECONCILIATION,
        account_id="paper-main",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        next_run_at=now,
    )
    first = WorkerService(repository, _settings(), worker_id=f"p0b-first-{uuid4()}")
    first.heartbeat(now=now)
    run_ids = SchedulerService(repository).tick(now + timedelta(milliseconds=1))
    first.heartbeat(now=now + timedelta(milliseconds=1), scheduler_ticked=True)
    assert len(run_ids) == 1
    assert first.execute_one(now + timedelta(milliseconds=2)) == run_ids[0]
    first.mark_stopped(now + timedelta(seconds=1))

    restarted = WorkerService(repository, _settings(), worker_id=f"p0b-restarted-{uuid4()}")
    restarted.heartbeat(now=now + timedelta(seconds=2), scheduler_ticked=True)
    assert restarted.execute_one(now + timedelta(seconds=2)) is None

    with Session(repository.engine) as session:
        run = session.get(JobRun, run_ids[0])
        old_heartbeat = session.get(WorkerHeartbeat, first.worker_id)
        new_heartbeat = session.get(WorkerHeartbeat, restarted.worker_id)
        assert run is not None and run.status == RunStatus.SUCCEEDED
        assert run.scheduled_job_id == job.id and run.attempt_count == 1
        assert old_heartbeat is not None and old_heartbeat.stopped_at is not None
        assert new_heartbeat is not None and new_heartbeat.scheduler_heartbeat_at is not None


def test_production_worker_rejects_invalid_database_credentials() -> None:
    secret = "x" * 48
    env = {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://invalid:invalid@127.0.0.1:1/quantlab?connect_timeout=1",
        "AUTOMATION_ENABLED": "true",
        "TRUSTED_HOSTS": "worker",
        "API_VIEWER_TOKEN": "v" + secret,
        "API_OPERATOR_TOKEN": "o" + secret,
        "API_ADMIN_TOKEN": "a" + secret,
    }
    result = subprocess.run(
        [sys.executable, "-m", "quantlab.worker"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "invalid:invalid" not in result.stderr
