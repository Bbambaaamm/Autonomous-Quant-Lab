from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import quantlab.api as api_module
from quantlab.automation import JobRun, RunStatus, ScheduledJob
from quantlab.market_data import IngestionResult
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    InstrumentSymbolRecord,
    StrategyDeploymentRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import AuditEventRecord
from quantlab.phase7 import PaperMonitoringRunRecord
from quantlab.security import limiter

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_failed_ingestion_api_returns_json_502_with_iso_dates(monkeypatch) -> None:
    suffix = uuid4().hex
    instrument_id = f"failed-{suffix[:32]}"
    client = TestClient(api_module.app)
    instrument = client.post(
        "/operator/instruments",
        json={
            "instrument_id": instrument_id,
            "symbol": f"F{suffix[:7]}".upper(),
            "active_from": "2020-01-01",
            "reason": "příprava failed ingestion regrese",
        },
    )
    assert instrument.status_code == 200, instrument.text
    failed = IngestionResult(
        f"failed-{suffix}",
        date(2026, 6, 1),
        date(2026, 8, 28),
        "FAILED",
        (),
        "provider unavailable",
    )
    monkeypatch.setattr(api_module, "build_market_data_provider", lambda settings, engine: object())
    monkeypatch.setattr(api_module.market_data_service, "ingest", lambda *args: failed)

    response = client.post(
        "/operator/market-data/ingestions",
        json={
            "instrument_id": instrument_id,
            "start": "2026-06-01",
            "end": "2026-08-28",
            "reason": "ověření JSON chyby ingestion",
        },
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["requested_start"] == "2026-06-01"
    assert response.json()["detail"]["requested_end"] == "2026-08-28"
    assert response.json()["detail"]["error"] == "provider unavailable"
    # Acceptance job sdílí databázi s navazujícími scénáři. Syntetický instrument
    # proto po API regresi odstraníme, aby neměnil jejich market-data universe.
    with Session(api_module.repository.engine) as session, session.begin():
        session.execute(
            delete(InstrumentSymbolRecord).where(
                InstrumentSymbolRecord.instrument_id == instrument_id
            )
        )
        session.execute(
            delete(InstrumentRecord).where(InstrumentRecord.instrument_id == instrument_id)
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
    monkeypatch.setattr(api_module, "build_market_data_provider", lambda settings, engine: provider)
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
    forged = client.post(
        f"/operator/research/experiments/{experiment_id}/eligibility",
        json={"reason": reason, "metrics": {"total_return": 999}},
    )
    assert forged.status_code == 422
    eligibility = client.post(
        f"/operator/research/experiments/{experiment_id}/eligibility",
        json={"reason": reason},
    )
    assert eligibility.status_code == 200, eligibility.text
    assert eligibility.json()["status"] == "ELIGIBLE"
    evidence = client.get(f"/operator/research/experiments/{experiment_id}/eligibility")
    assert evidence.status_code == 200
    assert evidence.json()["actor"]["actor_id"] == "api-admin"
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
    monitoring_job_id = enrollment.json()["monitoring_job"]["id"]
    with Session(api_module.automation_repository.engine) as session:
        monitoring_job = session.get(ScheduledJob, monitoring_job_id)
        assert monitoring_job is not None
        monitoring_job.enabled = False
        session.commit()
    blocked_autonomous = client.post(
        f"/operator/deployments/{deployment_id}/autonomous/enable",
        json={"reason": reason},
    )
    assert blocked_autonomous.status_code == 409
    ensured = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert ensured.status_code == 200, ensured.text
    assert ensured.json()["monitoring_job"]["id"] == monitoring_job_id
    assert ensured.json()["monitoring_job"]["enabled"] is True
    generic_disable = client.post(f"/automation/jobs/{monitoring_job_id}/disable")
    assert generic_disable.status_code == 422
    # Tento B1 acceptance test záměrně skládá mnoho validních mutation scénářů do
    # jediného request bucketu. Rate-limit behavior má samostatné security testy;
    # před pokračováním workflow acceptance tedy izolovaně vyčistíme process-local bucket.
    with limiter.lock:
        limiter.events.clear()
    autonomous = client.post(
        f"/operator/deployments/{deployment_id}/autonomous/enable",
        json={"reason": reason},
    )
    assert autonomous.status_code == 200, autonomous.text
    assert autonomous.json()["enabled"] is True
    assert autonomous.json()["schedule_type"] == "DAILY"
    assert autonomous.json()["daily_time"] == "09:00"
    assert autonomous.json()["timezone"] == "America/New_York"

    recovery_run_id = api_module.automation_scheduler.run_now(
        autonomous.json()["id"], f"recovery-{suffix}"
    )
    with Session(api_module.automation_repository.engine) as session:
        recovery_run = session.get(JobRun, recovery_run_id)
        assert recovery_run is not None
        recovery_run.status = RunStatus.DEAD_LETTER
        recovery_run.finished_at = datetime.now(UTC)
        session.commit()
    legacy_retry = client.post(f"/automation/runs/{recovery_run_id}/retry")
    assert legacy_retry.status_code == 422
    audited_retry = client.post(
        f"/operator/automation/runs/{recovery_run_id}/retry",
        json={"reason": reason},
    )
    assert audited_retry.status_code == 200, audited_retry.text
    assert audited_retry.json()["status"] == "RETRY_SCHEDULED"
    with Session(api_module.paper_repository.engine) as session:
        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == "CONTROL_AUTOMATION_RUN_RETRY",
                AuditEventRecord.entity_id == recovery_run_id,
            )
        )
        assert audit is not None
        assert reason in audit.payload_json
        assert "api-admin" in audit.payload_json
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

    with pytest.raises(DBAPIError), Session(api_module.paper_repository.engine) as session:
        session.execute(
            text(
                "UPDATE phase6_eligibility_decisions SET status='INELIGIBLE' "
                "WHERE experiment_id=:experiment_id"
            ),
            {"experiment_id": experiment_id},
        )
        session.commit()


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
