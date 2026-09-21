from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from phase6_audit_helpers import seed_phase6_snapshot
from sqlalchemy.orm import sessionmaker

from quantlab.config import Settings
from quantlab.operator_read_model import OperatorReadModel
from quantlab.phase4 import (
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    PositionRecord,
    ReconciliationRecord,
    RiskDecisionRecord,
    RiskEventRecord,
    TradingCycleRecord,
)
from quantlab.phase6_runtime import DeploymentService, Phase6ExperimentRunner
from quantlab.phase7 import DEFAULT_POLICY, PaperMonitoringService, PaperPerformanceSnapshotRecord

NOW = datetime(2026, 8, 27, 22, tzinfo=UTC)


@pytest.fixture
def environment(tmp_path):
    url = f"sqlite:///{tmp_path / 'accounts.db'}"
    repository = Phase4Repository(url)
    repository.seed_account()
    repository.seed_account("paper-test")
    factory = sessionmaker(repository.engine, expire_on_commit=False)
    return factory, OperatorReadModel(factory, Settings(database_url=url))


def seed_monitoring(factory, account, timestamp, equity):
    _, _, _, _, request = seed_phase6_snapshot(factory)
    experiment = Phase6ExperimentRunner(factory).run(request)
    # Pre-approved fixture: these tests exercise read projections, not promotion.
    with factory() as session, session.begin():
        session.add(experiment)
        experiment.decision = "PAPER_CANDIDATE"
    service = DeploymentService(factory)
    deployment = service.create(experiment.id, account)
    service.approve(deployment.deployment_id, timestamp)
    monitoring = PaperMonitoringService(factory)
    policy = monitoring.create_policy(account, DEFAULT_POLICY.copy(), timestamp)
    run = monitoring.enroll(deployment.deployment_id, policy.policy_id, timestamp)
    with factory() as session, session.begin():
        session.add(
            PaperPerformanceSnapshotRecord(
                snapshot_id=account,
                monitoring_id=run.monitoring_id,
                deployment_id=deployment.deployment_id,
                paper_account_id=account,
                session_date=timestamp.date(),
                captured_at=timestamp,
                as_of=timestamp,
                calendar_identity="XNYS",
                cash=equity,
                marked_equity=equity,
                realized_pnl=0,
                gross_exposure=0,
                net_exposure=0,
                position_count=0,
                daily_return=None,
                cumulative_return=0,
                drawdown=0,
                cumulative_turnover=0,
                cumulative_commissions=0,
                cumulative_slippage_cost=0,
                order_count=0,
                fill_count=0,
                risk_rejection_count=0,
                reconciliation_status="SAFE",
                trading_state="NORMAL",
                observation_lineage_json="[]",
                content_hash=account,
            )
        )
    return run


def seed_activity(factory, account, timestamp):
    with factory() as session, session.begin():
        session.add(
            TradingCycleRecord(
                id=account,
                cycle_key=account,
                account_id=account,
                strategy_id="strategy",
                session_date=timestamp.date(),
                started_at=timestamp,
                completed_at=timestamp,
                status="COMPLETED",
                correlation_id=account,
                data_fingerprint=account,
            )
        )
        session.add(
            PositionRecord(
                account_id=account,
                instrument_id="IBM",
                quantity=1,
                average_cost=100,
                realized_pnl=0,
                lots_json="[]",
                updated_at=timestamp,
            )
        )
        session.add(
            ReconciliationRecord(
                id=account,
                account_id=account,
                timestamp=timestamp,
                status="SAFE" if account == "paper-main" else "UNSAFE",
                differences_json="[]",
                correlation_id=account,
            )
        )
        session.add(
            RiskEventRecord(
                id=account,
                account_id=account,
                timestamp=timestamp,
                event_type="TEST",
                reason="isolation",
                correlation_id=account,
            )
        )
        session.flush()
        session.add(
            RiskDecisionRecord(
                id=account,
                timestamp=timestamp,
                account_id=account,
                order_intent_id=account,
                trading_cycle_id=account,
                status="APPROVED",
                original_quantity=2,
                approved_quantity=2,
                reasons_json="[]",
                limits_json="{}",
                portfolio_json="{}",
                correlation_id=account,
            )
        )
        session.flush()
        session.add(
            PaperOrderRecord(
                id=account,
                client_order_id=account,
                account_id=account,
                trading_cycle_id=account,
                order_intent_id=account,
                risk_decision_id=account,
                instrument_id="IBM",
                side="BUY",
                order_type="MARKET",
                quantity=2,
                submitted_notional=200,
                filled_quantity=1,
                remaining_quantity=1,
                status="PARTIALLY_FILLED",
                created_at=timestamp,
                correlation_id=account,
            )
        )
        session.flush()
        session.add(
            PaperFillRecord(
                id=account,
                order_id=account,
                sequence=1,
                quantity=1,
                price=100,
                reference_price=100,
                commission=0,
                timestamp=timestamp,
            )
        )


def test_newer_foreign_monitoring_cannot_replace_main_account(environment):
    factory, model = environment
    main = seed_monitoring(factory, "paper-main", NOW - timedelta(days=2), Decimal("101000"))
    other = seed_monitoring(factory, "paper-test", NOW - timedelta(days=1), Decimal("999000"))
    seed_activity(factory, "paper-main", NOW - timedelta(days=2))
    seed_activity(factory, "paper-test", NOW - timedelta(days=1))
    paper = model.paper()
    assert paper["monitoring"]["monitoring_id"] == main.monitoring_id
    assert paper["marked_equity"] == Decimal("101000")
    for key in ("positions", "orders"):
        assert {row["account_id"] for row in paper[key]} == {"paper-main"}
    assert [row["id"] for row in paper["fills"]] == ["paper-main"]
    assert paper["latest_reconciliation"]["status"] == "SAFE"
    overview = model.overview(NOW)
    assert overview["monitoring_id"] == main.monitoring_id
    assert overview["paper_equity"] == Decimal("101000")
    assert overview["latest_reconciliation_status"] == "SAFE"
    assert overview["last_trading_cycle"] == NOW - timedelta(days=2)
    assert overview["open_order_count"] == 1
    assert overview["position_count"] == 1
    risk = model.risk()
    assert risk["marked_equity"] == Decimal("101000")
    assert risk["position_count"] == 1
    for key in ("decisions", "events"):
        assert [row["account_id"] for row in risk[key]] == ["paper-main"]
    performance = model.performance("ALL", NOW)
    assert performance["monitoring_id"] == main.monitoring_id
    assert [p["marked_equity"] for p in performance["points"]] == [Decimal("101000")]
    assert model.performance("ALL", NOW, other.monitoring_id)["points"] == []


def test_missing_main_monitoring_never_falls_back_to_foreign_data(environment):
    factory, model = environment
    seed_monitoring(factory, "paper-test", NOW - timedelta(days=1), Decimal("999000"))
    assert model.paper()["monitoring"] is None
    assert model.paper()["marked_equity"] is None
    assert model.overview(NOW)["paper_equity"] is None
    assert model.risk()["marked_equity"] is None
    assert model.performance("ALL", NOW)["points"] == []


@pytest.mark.parametrize("offset", [timedelta(days=1), timedelta(hours=1)])
def test_performance_excludes_future_session_and_as_of(environment, offset):
    factory, model = environment
    seed_monitoring(factory, "paper-main", NOW + offset, Decimal("101000"))
    assert model.performance("ALL", NOW)["points"] == []
    assert model.performance("ALL", NOW + timedelta(days=2))["points"]


@pytest.mark.parametrize("provider,supports", [("stooq", False), ("alpaca", True)])
def test_provider_capabilities_follow_config_without_network(environment, provider, supports):
    factory, _ = environment
    settings = Settings(
        market_data_provider=provider, alpaca_key_id="test", alpaca_secret_key="test"
    )
    result = OperatorReadModel(factory, settings).data_health(NOW)
    assert result["provider"]["name"] == provider
    assert result["provider"]["supports_actions"] is supports


def test_authenticated_api_preserves_account_isolation(environment, monkeypatch):
    from fastapi.testclient import TestClient

    from quantlab import api

    factory, model = environment
    main = seed_monitoring(factory, "paper-main", NOW - timedelta(days=2), Decimal("101000"))
    seed_monitoring(factory, "paper-test", NOW - timedelta(days=1), Decimal("999000"))
    monkeypatch.setattr(api, "operator_read_model", model)
    client = TestClient(api.app)
    headers = {"Authorization": f"Bearer {api.settings.api_viewer_token}"}
    paper = client.get("/operator/paper", headers=headers)
    assert paper.status_code == 200
    assert paper.json()["monitoring"]["monitoring_id"] == main.monitoring_id
    overview = client.get("/operator/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["monitoring_id"] == main.monitoring_id
    risk = client.get("/operator/risk", headers=headers)
    assert risk.status_code == 200
    assert Decimal(str(risk.json()["marked_equity"])) == Decimal("101000")
    performance = client.get("/operator/paper/performance?period=ALL", headers=headers)
    assert performance.status_code == 200
    assert performance.json()["monitoring_id"] == main.monitoring_id


def test_stopped_workers_are_not_counted_as_stale(environment):
    from quantlab.automation import WorkerHeartbeat

    factory, model = environment
    with factory() as session, session.begin():
        for name, heartbeat, stopped in [
            ("active", NOW, None),
            ("stale", NOW - timedelta(hours=1), None),
            ("stopped", NOW, NOW),
        ]:
            session.add(
                WorkerHeartbeat(
                    worker_id=name,
                    started_at=NOW - timedelta(days=1),
                    last_heartbeat_at=heartbeat,
                    stopped_at=stopped,
                )
            )
    result = model.overview(NOW)
    assert result["healthy_worker_count"] == 1
    assert result["stale_worker_count"] == 1
    assert result["stopped_worker_count"] == 1
    states = {x["worker_id"]: x["state"] for x in model.automation(NOW)["workers"]}
    assert states == {"active": "HEALTHY", "stale": "STALE", "stopped": "STOPPED"}


def test_latest_trade_diagnostic_excludes_foreign_accounts_and_monitoring(environment):
    from quantlab.automation import JobRun, ScheduledJob

    factory, model = environment
    main = seed_monitoring(factory, "paper-main", NOW - timedelta(days=2), Decimal("101000"))
    other = seed_monitoring(factory, "paper-test", NOW - timedelta(days=1), Decimal("999000"))
    with factory() as session, session.begin():
        for name, account, deployment, kind, offset in [
            ("trade", "paper-main", main.deployment_id, "PREPARE_PAPER_SESSION", 0),
            ("foreign", "paper-test", other.deployment_id, "PREPARE_PAPER_SESSION", 1),
            ("monitor", "paper-main", main.deployment_id, "MONITOR_PAPER_DEPLOYMENT", 2),
        ]:
            timestamp = NOW + timedelta(minutes=offset)
            session.add(
                ScheduledJob(
                    id=name,
                    job_type=kind,
                    account_id=account,
                    enabled=True,
                    schedule_type="DAILY",
                    daily_time="13:00",
                    timezone="UTC",
                    misfire_policy="RUN_ONCE_IF_MISSED",
                    misfire_grace_seconds=60,
                    next_run_at=timestamp + timedelta(days=1),
                    max_attempts=3,
                    config_json="{}",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.flush()
            session.add(
                JobRun(
                    id=name,
                    scheduled_job_id=name,
                    occurrence_key=name,
                    scheduled_for=timestamp,
                    status="SUCCEEDED",
                    config_snapshot_json="{}",
                    correlation_id=name,
                    deployment_id=deployment,
                    created_at=timestamp,
                    outcome="NO_ACTION" if name == "trade" else "INSUFFICIENT_DATA",
                    no_action_reason="NO_TRADE_DELTA" if name == "trade" else None,
                )
            )
    result = model.overview(NOW + timedelta(hours=1))
    assert result["latest_paper_run"]["id"] == "trade"
    assert result["latest_paper_run"]["no_action_reason"] == "NO_TRADE_DELTA"
    assert result["next_scheduled_paper_cycle"] == NOW + timedelta(days=1)
