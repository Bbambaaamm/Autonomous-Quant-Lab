from __future__ import annotations

import os

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from quantlab.market_data import DatasetInvalid
from quantlab.persistence import StrategyDeploymentRecord
from quantlab.phase4 import Phase4Repository
from quantlab.phase6_runtime import DeploymentService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_h3_approval_identity_is_immutable_and_legacy_fails_closed() -> None:
    repository = Phase4Repository(os.environ["DATABASE_URL"], bootstrap_test_schema=False)
    with Session(repository.engine) as session:
        approved = session.scalar(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status == "APPROVED")
            .order_by(StrategyDeploymentRecord.created_at.desc())
        )
        if approved is None:
            pytest.skip("H3 acceptance navazuje na B1 deployment")
        assert approved.runtime_manifest_hash
        original = approved.runtime_manifest_hash
        with pytest.raises(DBAPIError, match="approved runtime manifest is immutable"):
            session.execute(
                update(StrategyDeploymentRecord)
                .where(StrategyDeploymentRecord.deployment_id == approved.deployment_id)
                .values(runtime_manifest_hash="0" * 64)
            )
            session.commit()
        session.rollback()
        assert (
            session.get(StrategyDeploymentRecord, approved.deployment_id).runtime_manifest_hash
            == original
        )

    legacy = StrategyDeploymentRecord()
    legacy.runtime_manifest_json = None
    legacy.runtime_manifest_hash = None
    legacy.runtime_manifest_version = None
    with pytest.raises(DatasetInvalid, match="RUNTIME_CONFIG_IDENTITY_MISSING"):
        DeploymentService._validated_runtime_manifest(legacy)
