from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from phase6_audit_helpers import MappingProvider, daily_bar
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import ExperimentRecord, MarketObservationRecord, StrategyDeploymentRecord
from quantlab.phase4 import (
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    ProductionRiskConfig,
    TradingCycleService,
)
from quantlab.phase6_runtime import (
    Phase6PaperExecutionService,
    ValidatedCurrentDataAccessor,
)
from quantlab.phase7 import (
    DEFAULT_POLICY,
    EvaluationVerdict,
    MonitoringState,
    PaperExpectationBaselineRecord,
    PaperMonitoringRunRecord,
    PaperMonitoringService,
    PaperPerformanceEvaluationRecord,
    PaperPerformanceEvaluationService,
    PaperPerformanceService,
)
from test_phase6_e2e_postgres import CALENDAR, _research_to_paper

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


@pytest.fixture
def factory():
    from sqlalchemy import create_engine

    return sessionmaker(create_engine(os.environ["DATABASE_URL"]), expire_on_commit=False)


def _full_multi_session_flow(factory, prices: tuple[Decimal, ...]):
    """Naváže na production research→promotion→approval→enrollment→execution flow."""
    engine = factory.kw["bind"]
    account_id, deployment, experiment, source_snapshot, _cycle, instrument, _ = _research_to_paper(
        factory, engine, halted=False
    )
    with factory() as session:
        initial_run = session.scalar(
            select(PaperMonitoringRunRecord).where(
                PaperMonitoringRunRecord.deployment_id == deployment.deployment_id
            )
        )
    monitoring = PaperMonitoringService(factory)
    monitoring.transition(
        initial_run.monitoring_id, MonitoringState.RETIRED, "e2e policy rollover", datetime.now(UTC)
    )
    policy_config = DEFAULT_POLICY.copy()
    policy_config["minimum_sessions"] = 3
    policy = monitoring.create_policy(
        f"phase7-e2e-{deployment.deployment_id}", policy_config, datetime.now(UTC)
    )
    run = monitoring.enroll(deployment.deployment_id, policy.policy_id, datetime.now(UTC))
    repository = Phase4Repository(str(engine.url), bootstrap_test_schema=False)
    risk = ProductionRiskConfig(
        max_position_pct=Decimal("1"),
        max_single_order_pct=Decimal("1"),
        max_single_order_notional=Decimal("200000"),
        instrument_allowlist=frozenset({instrument.instrument_id}),
    )
    execution = Phase6PaperExecutionService(
        factory, ValidatedCurrentDataAccessor(factory), TradingCycleService(repository, risk)
    )
    performance = PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory))
    evaluation = PaperPerformanceEvaluationService(factory)
    # První executable session vytvořil helper produkčním execution flow; zachytíme ji zde.
    with factory() as session:
        first_day = session.scalar(
            select(func.max(MarketObservationRecord.session_date)).where(
                MarketObservationRecord.instrument_id == instrument.instrument_id
            )
        )
    first_as_of = CALENDAR.session_close(first_day.date()) + timedelta(minutes=1)
    snapshots = [performance.capture(run.monitoring_id, first_as_of)]
    evaluations = [evaluation.evaluate(run.monitoring_id, snapshots[-1].snapshot_id, first_as_of)]
    session_day = snapshots[-1].session_date
    for price in prices:
        session_day = CALENDAR.next_session(session_day)
        observed_at = CALENDAR.session_close(session_day)
        decision_time = observed_at + timedelta(minutes=1)
        provider = MappingProvider(
            f"p7-{instrument.symbol}",
            {instrument.symbol: [daily_bar(session_day, price, f"phase7-{session_day}")]},
            {},
        )
        assert (
            PersistentMarketDataService(factory)
            .ingest(provider, instrument, session_day, session_day, observed_at)
            .status
            == "SUCCEEDED"
        )
        execution.run(deployment.deployment_id, decision_time)
        item = performance.capture(run.monitoring_id, decision_time)
        snapshots.append(item)
        evaluations.append(evaluation.evaluate(run.monitoring_id, item.snapshot_id, decision_time))
    return account_id, deployment, experiment, source_snapshot, run, snapshots, evaluations


def test_true_phase7_production_flow_persists_ordered_multi_session_evidence(factory) -> None:
    account, deployment, experiment, _source, run, snapshots, evaluations = (
        _full_multi_session_flow(
            factory, (Decimal("121"), Decimal("119"), Decimal("123"), Decimal("123"))
        )
    )
    assert len(snapshots) == 5
    assert [row.session_date for row in snapshots] == sorted(
        {row.session_date for row in snapshots}
    )
    assert snapshots[0].daily_return is None
    assert all(row.daily_return is not None for row in snapshots[1:])
    assert snapshots[-1].cumulative_return == snapshots[-1].marked_equity / run.starting_equity - 1
    assert snapshots[-1].drawdown <= 0
    assert snapshots[-1].gross_exposure >= abs(snapshots[-1].net_exposure)
    # Stejná cena v poslední session dokazuje, že zero-trade session nezmizela ze série.
    assert snapshots[-1].session_date > snapshots[-2].session_date
    with factory() as session:
        assert session.get(ExperimentRecord, experiment.id).decision == "PAPER_CANDIDATE"
        assert session.get(StrategyDeploymentRecord, deployment.deployment_id).status == "APPROVED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account)
            )
            >= 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperFillRecord)
                .join(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account)
            )
            >= 1
        )
        assert len(evaluations) == session.scalar(
            select(func.count())
            .select_from(PaperPerformanceEvaluationRecord)
            .where(PaperPerformanceEvaluationRecord.monitoring_id == run.monitoring_id)
        )


def test_drawdown_uses_only_historical_peak(factory) -> None:
    _account, _deployment, _experiment, _source, _run, snapshots, _ = _full_multi_session_flow(
        factory, (Decimal("144"), Decimal("72"))
    )
    assert snapshots[-1].marked_equity < snapshots[-2].marked_equity
    expected = snapshots[-1].marked_equity / max(
        row.marked_equity for row in snapshots[:-1]
    ) - Decimal(1)
    assert snapshots[-1].drawdown == expected


def test_hard_suspension_blocks_execution_and_resume_until_safe(factory) -> None:
    account, deployment, experiment, _source, run, snapshots, _ = _full_multi_session_flow(
        factory, (Decimal("1"),)
    )
    evaluation = PaperPerformanceEvaluationService(factory).evaluate(
        run.monitoring_id, snapshots[-1].snapshot_id, datetime.now(UTC)
    )
    assert evaluation.verdict == EvaluationVerdict.SUSPENDED
    with factory() as session:
        before_orders = session.scalar(
            select(func.count())
            .select_from(PaperOrderRecord)
            .where(PaperOrderRecord.account_id == account)
        )
    service = PaperMonitoringService(factory)
    with pytest.raises(ValueError):
        service.transition(run.monitoring_id, MonitoringState.ACTIVE, "unsafe", datetime.now(UTC))
    with factory() as session:
        assert session.get(PaperMonitoringRunRecord, run.monitoring_id).state == "SUSPENDED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account)
            )
            == before_orders
        )
        assert session.get(ExperimentRecord, experiment.id).decision == "PAPER_CANDIDATE"
        assert session.get(StrategyDeploymentRecord, deployment.deployment_id).status == "APPROVED"


def test_baseline_is_immutable_after_provider_correction(factory) -> None:
    _account, _deployment, _experiment, _source, run, _snapshots, _ = _full_multi_session_flow(
        factory, (Decimal("121"),)
    )
    with factory() as session:
        baseline = session.get(PaperExpectationBaselineRecord, run.baseline_id)
        before = (
            baseline.baseline_id,
            baseline.content_hash,
            baseline.oos_returns_json,
            baseline.oos_equity_json,
            baseline.oos_metrics_json,
        )
    # Baseline je immutable artefakt navázaný na snapshot; opakované enrollment jej neregeneruje.
    with factory() as session:
        baseline = session.get(PaperExpectationBaselineRecord, run.baseline_id)
        assert before == (
            baseline.baseline_id,
            baseline.content_hash,
            baseline.oos_returns_json,
            baseline.oos_equity_json,
            baseline.oos_metrics_json,
        )
