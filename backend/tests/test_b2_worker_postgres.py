from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.automation import AutomationRepository, JobType, ScheduledJob, ScheduleType
from quantlab.persistence import StrategyDeploymentRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_b2_deployment_job_contract_is_minimal_and_idempotent() -> None:
    database_url = os.environ["DATABASE_URL"]
    repository = AutomationRepository(database_url)
    with Session(repository.engine) as session:
        deployment = session.scalar(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status == "APPROVED")
            .order_by(StrategyDeploymentRecord.created_at.desc())
        )
    if deployment is None:
        pytest.skip("B2 contract test navazuje na B1 acceptance deployment")
    arguments = {
        "deployment_id": deployment.deployment_id,
        "schedule_type": ScheduleType.INTERVAL,
        "interval_seconds": 86400,
        "next_run_at": datetime.now(UTC),
    }
    first = repository.create_deployment_job(**arguments)
    second = repository.create_deployment_job(**arguments)
    assert first.id == second.id
    assert first.job_type == JobType.RUN_PAPER_DEPLOYMENT
    assert first.strategy_id is None
    assert json.loads(first.config_json) == {"deployment_id": deployment.deployment_id}
    with Session(repository.engine) as session:
        stored = session.get(ScheduledJob, first.id)
        assert stored is not None and stored.account_id == deployment.paper_account_id
