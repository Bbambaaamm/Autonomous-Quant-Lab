from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import quantlab.api as api
from quantlab.market_data import DatasetInvalid
from quantlab.phase7 import DEFAULT_POLICY, MonitoringState


def test_phase7_policy_and_monitoring_read_endpoints() -> None:
    client = TestClient(api.app)
    created = client.post(
        "/paper/monitoring/policies", json={"name": "api-policy", "config": DEFAULT_POLICY}
    )
    assert created.status_code == 200
    assert created.json()["content_hash"]
    assert client.get("/paper/monitoring").status_code == 200
    assert client.get("/paper/performance/summary").status_code == 200
    assert client.get("/paper/monitoring/unknown").status_code == 404
    assert client.get("/paper/monitoring/unknown/performance").status_code == 404
    assert client.get("/paper/monitoring/unknown/evaluations").status_code == 404


def test_phase7_policy_rejects_invalid_safety_configuration() -> None:
    invalid = DEFAULT_POLICY.copy()
    invalid["hard_suspend_on_halted"] = False
    response = TestClient(api.app).post(
        "/paper/monitoring/policies", json={"name": "unsafe", "config": invalid}
    )
    assert response.status_code == 422


def test_phase7_enrollment_and_lifecycle_routes_call_production_service(monkeypatch) -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        monitoring_id="api-monitor",
        deployment_id="approved-deployment",
        state="ACTIVE",
        state_reason="test",
        state_changed_at=now,
        created_at=now,
    )

    class RecordingService:
        def enroll(self, deployment_id, policy_id, called_at):
            assert deployment_id == "approved-deployment" and policy_id == "policy"
            assert called_at.tzinfo is not None
            return row

        def transition(self, monitoring_id, target, reason, called_at):
            assert monitoring_id == "api-monitor" and reason == "operator"
            row.state = target
            return row

    monkeypatch.setattr(api, "monitoring_service", RecordingService())
    client = TestClient(api.app)
    enrolled = client.post(
        "/paper/deployments/approved-deployment/monitoring/enroll?policy_id=policy"
    )
    assert enrolled.status_code == 200 and enrolled.json()["monitoring_id"] == "api-monitor"
    for endpoint, state in (
        ("pause", MonitoringState.PAUSED),
        ("resume", MonitoringState.ACTIVE),
        ("retire", MonitoringState.RETIRED),
    ):
        response = client.post(
            f"/paper/monitoring/api-monitor/{endpoint}", json={"reason": "operator"}
        )
        assert response.status_code == 200 and response.json()["state"] == state


def test_phase7_enrollment_and_invalid_transition_fail_closed(monkeypatch) -> None:
    class RejectingService:
        def enroll(self, deployment_id, policy_id, called_at):
            raise DatasetInvalid("deployment není schválen")

        def transition(self, monitoring_id, target, reason, called_at):
            raise DatasetInvalid("neplatný transition nebo unsafe reconciliation")

    monkeypatch.setattr(api, "monitoring_service", RejectingService())
    client = TestClient(api.app)
    response = client.post("/paper/deployments/unapproved/monitoring/enroll?policy_id=p")
    assert response.status_code == 409
    assert (
        client.post("/paper/monitoring/unknown/resume", json={"reason": "operator"}).status_code
        == 409
    )
