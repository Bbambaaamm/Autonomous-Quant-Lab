from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from sqlalchemy import select
from sqlalchemy.orm import Session

import quantlab.api as api_module
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    StrategyDeploymentRecord,
    UniverseMembershipRecord,
)
from quantlab.phase7 import PaperMonitoringRunRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_supported_b1_control_plane_reaches_active_monitoring(monkeypatch) -> None:
    suffix = uuid4().hex
    instrument_id = f"b1-{suffix[:36]}"
    symbol = f"B{suffix[:7]}".upper()
    universe_id = f"b1-u-{suffix[:32]}"
    sessions = list(CALENDAR.sessions_between(date(2026, 1, 2), date(2026, 2, 6)))[:18]
    provider = MappingProvider(
        "stooq",
        {
            symbol: [
                daily_bar(day, Decimal(100 + index), f"b1:{suffix}:{day}")
                for index, day in enumerate(sessions)
            ]
        },
        {},
    )
    monkeypatch.setattr(api_module, "StooqProvider", lambda: provider)
    client = TestClient(api_module.app)
    reason = "B1 PostgreSQL acceptance"

    instrument = client.post(
        "/operator/instruments",
        json={
            "instrument_id": instrument_id,
            "symbol": symbol,
            "active_from": "2020-01-01",
            "reason": reason,
        },
    )
    assert instrument.status_code == 200, instrument.text
    assert (
        client.post(
            "/operator/instruments",
            json={
                "instrument_id": instrument_id,
                "symbol": symbol,
                "active_from": "2020-01-01",
                "reason": reason,
            },
        ).json()["instrument_id"]
        == instrument_id
    )
    assert (
        client.post(
            "/operator/universes",
            json={"universe_id": universe_id, "name": universe_id, "reason": reason},
        ).status_code
        == 200
    )
    membership_payload = {
        "instrument_id": instrument_id,
        "valid_from": CALENDAR.session_open(sessions[0]).isoformat(),
        "known_at": CALENDAR.session_open(sessions[0]).isoformat(),
        "reason": reason,
    }
    membership = client.post(
        f"/operator/universes/{universe_id}/memberships", json=membership_payload
    )
    assert membership.status_code == 200, membership.text
    invalid = dict(membership_payload)
    invalid["valid_to"] = membership_payload["valid_from"]
    assert (
        client.post(f"/operator/universes/{universe_id}/memberships", json=invalid).status_code
        == 409
    )
    ingestion = client.post(
        "/operator/market-data/ingestions",
        json={
            "instrument_id": instrument_id,
            "start": sessions[0].isoformat(),
            "end": sessions[-1].isoformat(),
            "reason": reason,
        },
    )
    assert ingestion.status_code == 200, ingestion.text
    ingested_observations = ingestion.json()["observations"]
    assert ingested_observations
    observed_at = max(datetime.fromisoformat(item["observed_at"]) for item in ingested_observations)
    snapshot = client.post(
        "/operator/datasets",
        json={
            "provider": "stooq",
            "universe_id": universe_id,
            "start": sessions[0].isoformat(),
            "end": sessions[-1].isoformat(),
            "as_of": observed_at.isoformat(),
            "minimum_coverage": "1",
            "reason": reason,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    experiment = client.post(
        "/operator/research/experiments",
        json={
            "snapshot_id": snapshot.json()["snapshot_id"],
            "strategy_name": "multi_asset_trend",
            "strategy_version": "1.0.0",
            "parameter_configs": [{"fast": 2, "slow": 3}, {"fast": 2, "slow": 4}],
            "code_sha": "a" * 40,
            "reason": reason,
        },
    )
    assert experiment.status_code == 200, experiment.text
    assert experiment.json()["status"] == "COMPLETED"
    experiment_id = experiment.json()["id"]
    assert (
        client.post(
            f"/operator/research/experiments/{experiment_id}/promote", json={"reason": reason}
        ).json()["decision"]
        == "PAPER_CANDIDATE"
    )
    deployment = client.post(
        "/operator/deployments",
        json={"experiment_id": experiment_id, "reason": reason},
    )
    assert deployment.status_code == 200, deployment.text
    deployment_id = deployment.json()["deployment_id"]
    unapproved = client.post(
        "/operator/monitoring/enrollments",
        json={"deployment_id": deployment_id, "policy_id": "missing", "reason": reason},
    )
    assert unapproved.status_code == 409
    assert (
        client.post(
            f"/operator/deployments/{deployment_id}/approve", json={"reason": reason}
        ).json()["status"]
        == "APPROVED"
    )
    policy = client.post(
        "/operator/monitoring/policies",
        json={"name": universe_id, "reason": reason},
    )
    assert policy.status_code == 200, policy.text
    enrollment = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert enrollment.status_code == 200, enrollment.text
    assert enrollment.json()["state"] == "ACTIVE"
    retry = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert retry.json()["monitoring_id"] == enrollment.json()["monitoring_id"]
    with Session(api_module.paper_repository.engine) as session:
        monitoring = session.get(PaperMonitoringRunRecord, enrollment.json()["monitoring_id"])
        assert monitoring is not None
        deployment_row = session.get(StrategyDeploymentRecord, monitoring.deployment_id)
        assert deployment_row is not None
        experiment_row = session.get(ExperimentRecord, deployment_row.experiment_id)
        assert experiment_row is not None
        snapshot_row = session.get(DatasetSnapshotRecord, experiment_row.snapshot_id)
        assert snapshot_row is not None
        assert deployment_row.snapshot_id == snapshot.json()["snapshot_id"]
        assert snapshot_row.universe_id == universe_id

        membership_row = session.scalar(
            select(UniverseMembershipRecord).where(
                UniverseMembershipRecord.universe_id == universe_id,
                UniverseMembershipRecord.instrument_id == instrument_id,
            )
        )
        assert membership_row is not None
        assert session.get(InstrumentRecord, membership_row.instrument_id) is not None

        manifest = json.loads(snapshot_row.manifest_json)
        manifest_observations = {
            (item["id"], item["revision"], item["hash"]) for item in manifest["observations"]
        }
        ingested_lineage = {
            (item["observation_id"], item["revision"], item["source_hash"])
            for item in ingested_observations
        }
        assert manifest_observations == ingested_lineage


def test_control_plane_requires_admin_and_authentication() -> None:
    client = TestClient(api_module.app)
    payload = {
        "instrument_id": "forbidden",
        "symbol": "DENY",
        "active_from": "2020-01-01",
        "reason": "security negative",
    }
    client.headers["Authorization"] = f"Bearer {api_module.settings.api_viewer_token}"
    assert client.post("/operator/instruments", json=payload).status_code == 403
    del client.headers["Authorization"]
    assert client.post("/operator/instruments", json=payload).status_code == 401
