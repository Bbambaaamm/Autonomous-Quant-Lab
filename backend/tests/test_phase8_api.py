from __future__ import annotations

import importlib
import re
from pathlib import Path

from fastapi.testclient import TestClient


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase8.db'}")
    import quantlab.api as module
    from quantlab.config import get_settings

    get_settings.cache_clear()
    module = importlib.reload(module)
    return TestClient(module.app)


def test_overview_is_explicitly_paper_and_empty_evidence_is_null(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).get("/operator/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["trading_mode"] == "PAPER"
    assert body["live_trading_enabled"] is False
    assert body["paper_equity"] is None
    assert body["monitoring_verdict"] is None


def test_performance_rejects_unknown_period_and_preserves_empty_state(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    assert api.get("/operator/paper/performance?period=2W").status_code == 422
    assert api.get("/operator/paper/performance?period=1M").json()["points"] == []


def test_operator_actions_require_confirmation_and_reason(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    assert (
        api.post("/operator/risk/halt", json={"confirmation": "", "reason": "incident"}).status_code
        == 422
    )
    assert (
        api.post("/operator/risk/halt", json={"confirmation": "HALT", "reason": ""}).status_code
        == 422
    )
    response = api.post(
        "/operator/risk/halt", json={"confirmation": "HALT", "reason": "operator incident"}
    )
    assert response.status_code == 200
    assert api.get("/operator/risk").json()["trading_state"] == "HALTED"


def test_unsafe_resume_returns_conflict_and_remains_halted(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    api.post("/operator/risk/halt", json={"confirmation": "HALT", "reason": "incident"})
    from sqlalchemy.orm import Session

    import quantlab.api as module
    from quantlab.phase4 import PaperAccountRecord

    with Session(module.paper_repository.engine) as session:
        account = session.get(PaperAccountRecord, "paper-main")
        assert account is not None
        account.reconciliation_safe = False
        session.commit()
    response = api.post(
        "/operator/risk/resume", json={"confirmation": "RESUME", "reason": "zkusit resume"}
    )
    assert response.status_code == 409
    assert api.get("/operator/risk").json()["trading_state"] == "HALTED"


def test_audit_filters_are_server_side_and_range_is_validated(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    halted = api.post("/operator/risk/halt", json={"confirmation": "HALT", "reason": "audit event"})
    assert halted.status_code == 200
    result = api.get("/operator/audit?event_type=KILL_SWITCH_MANUAL_HALT&limit=1").json()
    assert result["limit"] == 1
    assert result["total"] >= 1
    assert all(item["event_type"] == "KILL_SWITCH_MANUAL_HALT" for item in result["items"])
    assert (
        api.get(
            "/operator/audit?start_utc=2026-02-01T00:00:00Z&end_utc=2026-01-01T00:00:00Z"
        ).status_code
        == 422
    )


def test_openapi_exposes_stable_operator_contracts(tmp_path, monkeypatch):
    schema = client(tmp_path, monkeypatch).get("/openapi.json").json()
    required = {
        "/operator/overview",
        "/operator/paper",
        "/operator/paper/performance",
        "/operator/risk",
        "/operator/data-health",
        "/operator/automation",
        "/operator/audit",
    }
    assert required <= schema["paths"].keys()
    for path in required:
        method = "get"
        assert "application/json" in schema["paths"][path][method]["responses"]["200"]["content"]
    response_schema = schema["paths"]["/operator/overview"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    component = response_schema["$ref"].rsplit("/", 1)[-1]
    backend_fields = set(schema["components"]["schemas"][component]["properties"])
    frontend_source = (Path(__file__).parents[2] / "frontend/lib/api.ts").read_text()
    match = re.search(r"export type Overview=\{([^}]+)\}", frontend_source)
    assert match is not None
    frontend_fields = {part.split(":", 1)[0] for part in match.group(1).split(";") if ":" in part}
    assert frontend_fields == backend_fields


def test_data_health_requires_persisted_observation_coverage(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import Session

    import quantlab.api as module
    from quantlab.market_data import XNYSCalendar
    from quantlab.persistence import (
        InstrumentRecord,
        MarketDataIngestionRecord,
        MarketObservationRecord,
    )

    now = datetime.now(UTC)
    completed = XNYSCalendar().latest_completed_session(now)
    session_time = datetime.combine(completed, datetime.min.time(), tzinfo=UTC)
    with Session(module.paper_repository.engine) as session:
        session.add(
            InstrumentRecord(
                instrument_id="SPY",
                symbol="SPY",
                exchange="XNYS",
                calendar="XNYS",
                currency="USD",
                asset_type="EQUITY",
                active_from=session_time - timedelta(days=1),
                active_to=None,
                created_at=now,
            )
        )
        session.add(
            MarketDataIngestionRecord(
                id="successful-empty",
                provider="stooq",
                scope_hash="scope",
                started_at=now,
                finished_at=now,
                status="SUCCEEDED",
                requested_start=session_time,
                requested_end=session_time,
                instrument_count=1,
                row_count=0,
                error_summary=None,
            )
        )
        session.commit()

    empty = api.get("/operator/data-health").json()
    assert empty["fresh"] is False
    assert empty["current_observation_count"] == 0

    with Session(module.paper_repository.engine) as session:
        session.add(
            MarketObservationRecord(
                observation_id="observation",
                instrument_id="SPY",
                ingestion_id="successful-empty",
                provider="stooq",
                timeframe="1d",
                session_date=session_time,
                timestamp=session_time,
                open="100",
                high="101",
                low="99",
                close="100",
                volume="1000",
                observed_at=now,
                source_id="SPY:test",
                revision=1,
                source_hash="hash",
            )
        )
        session.commit()

    covered = api.get("/operator/data-health").json()
    assert covered["fresh"] is True
    assert covered["latest_successful_session"] == completed.isoformat()


def test_resume_persists_long_reason_outside_bounded_correlation_id(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    reason = "bezpečné obnovení po ruční kontrole " + "x" * 500
    response = api.post("/operator/risk/resume", json={"confirmation": "RESUME", "reason": reason})
    assert response.status_code == 200
    audit = api.get("/operator/audit?event_type=KILL_SWITCH_RESUMED").json()["items"][0]
    assert len(audit["correlation_id"]) <= 64
    assert audit["payload"]["reason"] == reason
