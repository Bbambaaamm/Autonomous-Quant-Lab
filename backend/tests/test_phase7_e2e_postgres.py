from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from phase6_audit_helpers import MappingProvider, daily_bar
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_phase6_e2e_postgres import CALENDAR, _research_to_paper

from quantlab.market_data import AssetType, DatasetInvalid, Instrument
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import (
    ExperimentRecord,
    InstrumentRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
)
from quantlab.phase4 import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    ProductionRiskConfig,
    ReconciliationService,
    TradingCycleRecord,
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
    PaperPerformanceSnapshotRecord,
)

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


def _execution_service(factory, instrument_id: str) -> Phase6PaperExecutionService:
    repository = Phase4Repository(str(factory.kw["bind"].url), bootstrap_test_schema=False)
    risk = ProductionRiskConfig(
        max_position_pct=Decimal("1"),
        max_single_order_pct=Decimal("1"),
        max_single_order_notional=Decimal("200000"),
        instrument_allowlist=frozenset({instrument_id}),
    )
    return Phase6PaperExecutionService(
        factory, ValidatedCurrentDataAccessor(factory), TradingCycleService(repository, risk)
    )


def _economic_counts(factory, account_id: str) -> tuple[int, int, int]:
    with factory() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            ),
            session.scalar(
                select(func.count())
                .select_from(PaperFillRecord)
                .join(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            ),
            session.scalar(
                select(func.count())
                .select_from(TradingCycleRecord)
                .where(TradingCycleRecord.account_id == account_id)
            ),
        )


def _instrument(factory, instrument_id: str) -> Instrument:
    with factory() as session:
        row = session.get(InstrumentRecord, instrument_id)
        return Instrument(
            row.instrument_id,
            row.symbol,
            row.exchange,
            row.calendar,
            row.currency,
            AssetType(row.asset_type),
            row.active_from.date(),
            row.active_to.date() if row.active_to else None,
        )


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


@pytest.mark.parametrize(
    ("name", "prices", "expected"),
    [
        ("HEALTHY", (Decimal("180"), Decimal("220")), EvaluationVerdict.HEALTHY),
        ("WATCH", (Decimal("121"), Decimal("123.2")), EvaluationVerdict.WATCH),
        (
            "REVIEW_REQUIRED",
            (Decimal("121"), Decimal("122.5")),
            EvaluationVerdict.REVIEW_REQUIRED,
        ),
    ],
)
def test_controlled_paper_series_proves_healthy_watch_and_review_without_auto_retune(
    factory, name, prices, expected
) -> None:
    with factory() as session:
        experiments_before = session.scalar(select(func.count()).select_from(ExperimentRecord))
        deployments_before = session.scalar(
            select(func.count()).select_from(StrategyDeploymentRecord)
        )
    _account, deployment, experiment, _source, run, _snapshots, evaluations = (
        _full_multi_session_flow(factory, prices)
    )
    assert evaluations[-1].verdict == expected, name
    with factory() as session:
        stored_run = session.get(PaperMonitoringRunRecord, run.monitoring_id)
        stored_experiment = session.get(ExperimentRecord, experiment.id)
        stored_deployment = session.get(StrategyDeploymentRecord, deployment.deployment_id)
        assert stored_run.state == MonitoringState.ACTIVE
        assert (
            session.scalar(select(func.count()).select_from(ExperimentRecord))
            == experiments_before + 1
        )
        assert (
            session.scalar(select(func.count()).select_from(StrategyDeploymentRecord))
            == deployments_before + 1
        )
        assert stored_experiment.selected_parameters_json == experiment.selected_parameters_json
        assert stored_deployment.parameters_json == deployment.parameters_json
        assert stored_deployment.strategy_version == deployment.strategy_version


def test_suspended_monitoring_requires_safe_explicit_operator_resume(factory) -> None:
    account, _deployment, _experiment, _source, run, _snapshots, _evaluations = (
        _full_multi_session_flow(factory, ())
    )
    monitoring = PaperMonitoringService(factory)
    monitoring.transition(
        run.monitoring_id, MonitoringState.SUSPENDED, "reconciliation incident", datetime.now(UTC)
    )
    with factory() as session, session.begin():
        session.get(PaperAccountRecord, account).reconciliation_safe = False
    with pytest.raises(DatasetInvalid):
        monitoring.transition(
            run.monitoring_id, MonitoringState.ACTIVE, "unsafe resume", datetime.now(UTC)
        )
    repository = Phase4Repository(str(factory.kw["bind"].url), bootstrap_test_schema=False)
    assert ReconciliationService(repository).reconcile(account).status == "SUCCEEDED"
    resumed = monitoring.transition(
        run.monitoring_id,
        MonitoringState.ACTIVE,
        "operator verified reconciliation",
        datetime.now(UTC),
    )
    assert resumed.state == MonitoringState.ACTIVE


def test_hard_suspension_blocks_execution_and_resume_until_safe(factory) -> None:
    account, deployment, experiment, _source, run, snapshots, _ = _full_multi_session_flow(
        factory, (Decimal("1"),)
    )
    evaluation = PaperPerformanceEvaluationService(factory).evaluate(
        run.monitoring_id, snapshots[-1].snapshot_id, datetime.now(UTC)
    )
    assert evaluation.verdict == EvaluationVerdict.SUSPENDED
    with factory() as session:
        instrument_id = session.scalar(
            select(MarketObservationRecord.instrument_id).order_by(
                MarketObservationRecord.session_date.desc()
            )
        )
    before = _economic_counts(factory, account)
    with pytest.raises(DatasetInvalid):
        _execution_service(factory, instrument_id).run(
            deployment.deployment_id, datetime.now(UTC) + timedelta(days=1)
        )
    service = PaperMonitoringService(factory)
    with pytest.raises(ValueError):
        service.transition(run.monitoring_id, MonitoringState.ACTIVE, "unsafe", datetime.now(UTC))
    with factory() as session:
        assert session.get(PaperMonitoringRunRecord, run.monitoring_id).state == "SUSPENDED"
        assert session.get(ExperimentRecord, experiment.id).decision == "PAPER_CANDIDATE"
        assert session.get(StrategyDeploymentRecord, deployment.deployment_id).status == "APPROVED"
    assert _economic_counts(factory, account) == before


@pytest.mark.parametrize("blocked_state", [MonitoringState.PAUSED, MonitoringState.RETIRED])
def test_paused_and_retired_monitoring_block_actual_execution(factory, blocked_state) -> None:
    account, deployment, _experiment, _source, run, _snapshots, _ = _full_multi_session_flow(
        factory, ()
    )
    with factory() as session:
        instrument_id = session.scalar(
            select(MarketObservationRecord.instrument_id).order_by(
                MarketObservationRecord.session_date.desc()
            )
        )
    PaperMonitoringService(factory).transition(
        run.monitoring_id, blocked_state, f"audit {blocked_state}", datetime.now(UTC)
    )
    before = _economic_counts(factory, account)
    with pytest.raises(DatasetInvalid):
        _execution_service(factory, instrument_id).run(
            deployment.deployment_id, datetime.now(UTC) + timedelta(days=1)
        )
    assert _economic_counts(factory, account) == before


def test_baseline_is_immutable_after_provider_correction(factory) -> None:
    _account, _deployment, _experiment, _source, run, snapshots, _ = _full_multi_session_flow(
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
        lineage = json.loads(snapshots[-1].observation_lineage_json)
        observation = session.get(MarketObservationRecord, lineage[0]["observation_id"])
        instrument = _instrument(factory, observation.instrument_id)
    corrected_day = observation.session_date.date()
    provider = MappingProvider(
        observation.provider,
        {
            instrument.symbol: [
                daily_bar(corrected_day, Decimal(observation.close) + Decimal("7"), "correction")
            ]
        },
        {},
    )
    correction = PersistentMarketDataService(factory).ingest(
        provider,
        instrument,
        corrected_day,
        corrected_day,
        snapshots[-1].as_of + timedelta(days=1),
    )
    assert correction.status == "SUCCEEDED"
    assert correction.observations[0].revision == observation.revision + 1
    with factory() as session:
        baseline = session.get(PaperExpectationBaselineRecord, run.baseline_id)
        assert before == (
            baseline.baseline_id,
            baseline.content_hash,
            baseline.oos_returns_json,
            baseline.oos_equity_json,
            baseline.oos_metrics_json,
        )


def test_historical_paper_snapshot_and_evaluation_survive_real_provider_correction(factory) -> None:
    _account, _deployment, _experiment, _source, run, snapshots, evaluations = (
        _full_multi_session_flow(factory, (Decimal("121"),))
    )
    snapshot, evaluation = snapshots[-1], evaluations[-1]
    before_snapshot = (
        snapshot.snapshot_id,
        snapshot.content_hash,
        snapshot.observation_lineage_json,
        snapshot.daily_return,
        snapshot.cumulative_return,
        snapshot.drawdown,
    )
    before_evaluation = (evaluation.evaluation_id, evaluation.content_hash, evaluation.verdict)
    with factory() as session:
        lineage = json.loads(snapshot.observation_lineage_json)
        observation = session.get(MarketObservationRecord, lineage[0]["observation_id"])
        instrument = _instrument(factory, observation.instrument_id)
    day = observation.session_date.date()
    correction = PersistentMarketDataService(factory).ingest(
        MappingProvider(
            observation.provider,
            {instrument.symbol: [daily_bar(day, Decimal(observation.close) + 9, "correction")]},
            {},
        ),
        instrument,
        day,
        day,
        snapshot.as_of + timedelta(days=1),
    )
    assert correction.observations[0].revision == observation.revision + 1
    assert (
        PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory))
        .capture(run.monitoring_id, snapshot.as_of)
        .snapshot_id
        == snapshot.snapshot_id
    )
    assert (
        PaperPerformanceEvaluationService(factory)
        .evaluate(run.monitoring_id, snapshot.snapshot_id, snapshot.as_of)
        .evaluation_id
        == evaluation.evaluation_id
    )
    with factory() as session:
        stored_snapshot = session.get(PaperPerformanceSnapshotRecord, snapshot.snapshot_id)
        stored_evaluation = session.get(PaperPerformanceEvaluationRecord, evaluation.evaluation_id)
        assert before_snapshot == (
            stored_snapshot.snapshot_id,
            stored_snapshot.content_hash,
            stored_snapshot.observation_lineage_json,
            stored_snapshot.daily_return,
            stored_snapshot.cumulative_return,
            stored_snapshot.drawdown,
        )
        assert before_evaluation == (
            stored_evaluation.evaluation_id,
            stored_evaluation.content_hash,
            stored_evaluation.verdict,
        )
