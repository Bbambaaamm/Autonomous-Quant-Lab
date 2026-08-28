from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from quantlab.market_data import DatasetInvalid, XNYSCalendar
from quantlab.multi_asset import RebalanceFrequency
from quantlab.persistence import (
    Base,
    DatasetSnapshotRecord,
    ExperimentRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
)
from quantlab.phase4 import PaperAccountRecord
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6EligibilityService,
    Phase6PaperExecutionService,
)


def _factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed(factory, *, decision="RESEARCH_ONLY") -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    with factory() as session, session.begin():
        session.add(
            UniverseDefinitionRecord(
                universe_id="u", name="PIT", kind="POINT_IN_TIME_MEMBERSHIP", created_at=now
            )
        )
        session.add(
            DatasetSnapshotRecord(
                snapshot_id="s",
                created_at=now,
                as_of=now,
                provider="fixture",
                calendar_identity="XNYS:fixture",
                universe_id="u",
                start_at=now,
                end_at=now,
                timeframe="1d",
                content_hash="a" * 64,
                status="VALID",
                coverage="1",
                manifest_json="{}",
            )
        )
        session.add(
            StrategyRecord(
                strategy_identity="trend-1",
                strategy_name="multi_asset_trend",
                strategy_version="1.0.0",
                created_at=now,
                metadata_json="{}",
            )
        )
        session.add(
            ExperimentRecord(
                id="e",
                created_at=now,
                completed_at=now,
                status="COMPLETED",
                snapshot_id="s",
                strategy_identity="trend-1",
                strategy_name="multi_asset_trend",
                strategy_version="1.0.0",
                decision=decision,
                total_return=0.1,
                sharpe=1.0,
                max_drawdown=-0.1,
                trade_count=10,
                code_sha="b" * 40,
                cost_model_json="{}",
                selected_parameters_json='{"fast": 2, "slow": 3}',
                config_json="{}",
                result_json='{"stage":"OOS","metrics":{}}',
            )
        )
        session.add(
            PaperAccountRecord(
                id="paper",
                base_currency="USD",
                starting_cash=Decimal(100000),
                cash=Decimal(100000),
                equity=Decimal(100000),
                high_water_mark=Decimal(100000),
                realized_pnl=Decimal(0),
                trading_state="NORMAL",
                session_date=None,
                session_start_equity=None,
                reconciliation_safe=True,
                created_at=now,
                updated_at=now,
            )
        )


def test_explicit_promotion_is_required_and_idempotent() -> None:
    factory = _factory()
    _seed(factory)
    with factory() as session:
        assert session.scalar(select(ExperimentRecord.decision)) == "RESEARCH_ONLY"
        assert session.scalar(select(StrategyDeploymentRecord)) is None
    service = Phase6EligibilityService(factory)
    decision = service.evaluate_eligibility("e", actor={"id": "test"}, reason="test eligibility")
    assert decision.status == "ELIGIBLE"
    first = service.promote("e", actor={"id": "test"}, reason="test promotion")
    second = service.promote("e", actor={"id": "test"}, reason="test promotion")
    assert first.id == second.id == "e"
    assert second.decision == "PAPER_CANDIDATE"


def test_explicit_deployment_creation_and_approval() -> None:
    factory = _factory()
    _seed(factory)
    service = Phase6EligibilityService(factory)
    service.evaluate_eligibility("e", actor={"id": "test"}, reason="test eligibility")
    service.promote("e", actor={"id": "test"}, reason="test promotion")
    service = DeploymentService(factory)
    deployment = service.create("e", "paper")
    assert deployment.status == "PENDING_REVIEW"
    service.approve(deployment.deployment_id, datetime(2026, 1, 3, tzinfo=UTC))
    with factory() as session:
        assert session.get(StrategyDeploymentRecord, deployment.deployment_id).status == "APPROVED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_sha", None),
        ("code_sha", "BAD"),
        ("cost_model_json", None),
        ("cost_model_json", "[]"),
        ("selected_parameters_json", None),
        ("selected_parameters_json", "[]"),
    ],
)
def test_promotion_fails_closed_on_missing_or_malformed_evidence(
    field: str, value: str | None
) -> None:
    factory = _factory()
    _seed(factory)
    with factory() as session, session.begin():
        setattr(session.get(ExperimentRecord, "e"), field, value)
    with pytest.raises(DatasetInvalid):
        service = Phase6EligibilityService(factory)
        service.evaluate_eligibility("e", actor={"id": "test"}, reason="test eligibility")


def test_xnys_session_for_timestamp_checks_open_and_close() -> None:
    calendar = XNYSCalendar()
    cases = (
        (8, 0, 0, None),
        (9, 29, 59, None),
        (9, 30, 0, date(2026, 1, 2)),
        (12, 0, 0, date(2026, 1, 2)),
        (16, 0, 0, date(2026, 1, 2)),
        (16, 0, 1, None),
    )
    for hour, minute, second, expected in cases:
        local = datetime(2026, 1, 2, hour, minute, second, tzinfo=calendar.timezone)
        assert calendar.session_for_timestamp(local.astimezone(UTC)) == expected


@pytest.mark.parametrize(
    "frequency,previous,current,expected",
    [
        (RebalanceFrequency.DAILY, date(2026, 1, 5), date(2026, 1, 6), True),
        (RebalanceFrequency.WEEKLY, date(2026, 1, 5), date(2026, 1, 6), False),
        (RebalanceFrequency.WEEKLY, date(2026, 1, 9), date(2026, 1, 12), True),
        (RebalanceFrequency.MONTHLY, date(2026, 1, 30), date(2026, 2, 2), True),
        (RebalanceFrequency.MONTHLY, date(2026, 2, 2), date(2026, 2, 27), False),
    ],
)
def test_phase6_paper_execution_enforces_rebalance_frequency(
    frequency: RebalanceFrequency, previous: date, current: date, expected: bool
) -> None:
    assert Phase6PaperExecutionService._rebalance_due(current, previous, frequency) is expected


@pytest.mark.parametrize(
    "now,signal_session,execution_session",
    [
        (
            datetime(2026, 1, 9, 21, 1, tzinfo=UTC),
            date(2026, 1, 9),
            date(2026, 1, 12),
        ),
        (
            datetime(2026, 1, 16, 21, 1, tzinfo=UTC),
            date(2026, 1, 16),
            date(2026, 1, 20),
        ),
        (
            datetime(2026, 11, 27, 18, 1, tzinfo=UTC),
            date(2026, 11, 27),
            date(2026, 11, 30),
        ),
    ],
)
def test_phase6_execution_timing_uses_next_xnys_session(
    now: datetime, signal_session: date, execution_session: date
) -> None:
    calendar = XNYSCalendar()
    timing = Phase6PaperExecutionService.execution_timing(calendar, now)

    assert timing.signal_session == signal_session
    assert timing.decision_time == calendar.session_close(signal_session)
    assert timing.execution_session == execution_session
    assert timing.execution_time == calendar.session_open(execution_session)
    assert timing.execution_time > timing.decision_time


@pytest.mark.parametrize("case", ["after_close", "before_next_open", "after_next_open"])
def test_phase6_run_outside_next_open_fails_before_any_economic_service(case: str) -> None:
    calendar = XNYSCalendar()
    signal_close = calendar.session_close(date(2026, 1, 9))
    execution_open = calendar.session_open(date(2026, 1, 12))
    if case == "after_close":
        now = signal_close + timedelta(minutes=1)
    elif case == "before_next_open":
        now = execution_open - timedelta(microseconds=1)
    else:
        now = execution_open + timedelta(microseconds=1)
    sessions = Mock(side_effect=AssertionError("Před open nesmí služba přistoupit k persistence"))
    trading_cycle = Mock()
    current_data = Mock(calendar=calendar)
    service = Phase6PaperExecutionService(sessions, current_data, trading_cycle)

    with pytest.raises(DatasetInvalid, match="nelze zpětně fillovat"):
        service.run("deployment", now)

    trading_cycle.run.assert_not_called()
    current_data.for_execution_session.assert_not_called()


@pytest.mark.parametrize(
    "offset,reason",
    [
        (-timedelta(microseconds=1), "EXECUTION_SESSION_NOT_OPEN"),
        (timedelta(seconds=1), "MISSED_EXECUTION_OPEN"),
        (timedelta(minutes=5), "MISSED_EXECUTION_OPEN"),
    ],
)
def test_persistent_intent_fails_closed_outside_exact_open(offset, reason: str) -> None:
    calendar = XNYSCalendar()
    execution_open = calendar.session_open(date(2026, 1, 12))
    sessions = Mock(side_effect=AssertionError("Mimo open nesmí služba přistoupit k persistence"))
    trading_cycle = Mock()
    current_data = Mock(calendar=calendar)

    with pytest.raises(DatasetInvalid, match=reason):
        Phase6PaperExecutionService(sessions, current_data, trading_cycle).run(
            "deployment", execution_open + offset, execution_intent_time=execution_open
        )

    trading_cycle.run.assert_not_called()
    current_data.for_execution_session.assert_not_called()


def _approved_candidate(factory):
    service = Phase6EligibilityService(factory)
    service.evaluate_eligibility("e", actor={"id": "test"}, reason="test eligibility")
    service.promote("e", actor={"id": "test"}, reason="test promotion")
    return DeploymentService(factory).create("e", "paper")


def test_approval_rejects_missing_deployment() -> None:
    factory = _factory()
    with pytest.raises(ValueError):
        DeploymentService(factory).approve("missing", datetime.now(UTC))


@pytest.mark.parametrize(
    "case",
    [
        "wrong_deployment_state",
        "missing_experiment",
        "experiment_not_completed",
        "research_only",
        "missing_snapshot",
        "invalid_snapshot",
        "experiment_snapshot_mismatch",
        "deployment_snapshot_mismatch",
        "universe_mismatch",
        "strategy_name_mismatch",
        "strategy_version_mismatch",
        "missing_strategy_registry_entry",
        "registry_strategy_version_mismatch",
        "missing_paper_account",
        "paper_account_currency_mismatch",
        "unsupported_currency",
        "unsupported_timeframe",
        "snapshot_timeframe_mismatch",
        "missing_code_sha",
        "malformed_code_sha",
        "missing_cost_model_json",
        "malformed_cost_model_json",
        "invalid_cost_model_shape",
        "missing_selected_parameters_json",
        "malformed_selected_parameters_json",
        "invalid_selected_parameters_shape",
        "deployment_parameters_mismatch",
    ],
)
def test_deployment_approval_fail_closed_matrix(case: str, monkeypatch) -> None:
    from quantlab.multi_asset import STRATEGY_REGISTRY, TrendStrategy

    factory = _factory()
    _seed(factory)
    deployment = _approved_candidate(factory)
    with factory() as session, session.begin():
        row = session.get(StrategyDeploymentRecord, deployment.deployment_id)
        experiment = session.get(ExperimentRecord, "e")
        snapshot = session.get(DatasetSnapshotRecord, "s")
        account = session.get(PaperAccountRecord, "paper")
        assert row and experiment and snapshot and account
        if case == "wrong_deployment_state":
            row.status = "APPROVED"
        elif case == "missing_experiment":
            session.delete(experiment)
        elif case == "experiment_not_completed":
            experiment.status = "FAILED"
        elif case == "research_only":
            experiment.decision = "RESEARCH_ONLY"
        elif case == "missing_snapshot":
            session.delete(snapshot)
        elif case == "invalid_snapshot":
            snapshot.status = "INVALID"
        elif case == "experiment_snapshot_mismatch":
            experiment.snapshot_id = "different"
        elif case == "deployment_snapshot_mismatch":
            row.snapshot_id = "different"
        elif case == "universe_mismatch":
            row.universe_id = "different"
        elif case == "strategy_name_mismatch":
            row.strategy_name = "different"
        elif case == "strategy_version_mismatch":
            row.strategy_version = "9.9.9"
        elif case == "missing_strategy_registry_entry":
            experiment.strategy_name = "unregistered"
        elif case == "missing_paper_account":
            session.delete(account)
        elif case == "paper_account_currency_mismatch":
            account.base_currency = "EUR"
        elif case == "unsupported_currency":
            row.currency = "EUR"
        elif case == "unsupported_timeframe":
            row.timeframe = "1h"
        elif case == "snapshot_timeframe_mismatch":
            snapshot.timeframe = "1h"
        elif case == "missing_code_sha":
            experiment.code_sha = None
        elif case == "malformed_code_sha":
            experiment.code_sha = "BAD"
        elif case == "missing_cost_model_json":
            experiment.cost_model_json = None
        elif case == "malformed_cost_model_json":
            experiment.cost_model_json = "{"
        elif case == "invalid_cost_model_shape":
            experiment.cost_model_json = "[]"
        elif case == "missing_selected_parameters_json":
            experiment.selected_parameters_json = None
        elif case == "malformed_selected_parameters_json":
            experiment.selected_parameters_json = "{"
        elif case == "invalid_selected_parameters_shape":
            experiment.selected_parameters_json = "[]"
        elif case == "deployment_parameters_mismatch":
            row.parameters_json = '{"fast": 1, "slow": 3}'
    if case == "registry_strategy_version_mismatch":
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class WrongVersionTrend(TrendStrategy):
            version: str = "9.9.9"

        monkeypatch.setitem(STRATEGY_REGISTRY, "multi_asset_trend", WrongVersionTrend)
    with pytest.raises((DatasetInvalid, ValueError)):
        DeploymentService(factory).approve(deployment.deployment_id, datetime.now(UTC))
