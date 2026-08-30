from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from phase6_audit_helpers import (
    CALENDAR,
    MappingProvider,
    daily_bar,
    seed_phase6_snapshot,
)
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from quantlab.automation import PreOpenExecutionIntentRecord
from quantlab.domain import ReconciliationStatus, SystemTradingState
from quantlab.market_data import AssetType, CorporateAction, CorporateActionKind, Instrument
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.multi_asset import MultiAssetPortfolio
from quantlab.persistence import (
    CorporateActionRecord,
    DatasetSnapshotRecord,
    ExperimentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import (
    AuditEventRecord,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    PositionRecord,
    ProductionRiskConfig,
    ReconciliationRecord,
    RiskDecisionRecord,
    TradingCycleService,
)
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6EligibilityService,
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
    Phase6PaperExecutionService,
    ValidatedCurrentDataAccessor,
)
from quantlab.phase7 import DEFAULT_POLICY, MonitoringState, PaperMonitoringService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def test_phase6_experiment_runner_postgres_race_is_exactly_once(factory) -> None:
    _, _, _, _, request = seed_phase6_snapshot(factory, suffix=f"race-{uuid4().hex}")
    barrier = threading.Barrier(2)

    def worker():
        # Každý runner otevírá vlastní Session a connection až po synchronizaci workerů.
        barrier.wait(timeout=10)
        return Phase6ExperimentRunner(factory).run(request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=30) for future in (pool.submit(worker), pool.submit(worker))
        ]
    assert results[0].id == results[1].id
    assert results[0].idempotency_key == results[1].idempotency_key
    assert results[0].result_json == results[1].result_json
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentRecord)
                .where(ExperimentRecord.id == results[0].id)
            )
            == 1
        )


def test_postgres_snapshot_correction_replay_is_immutable(factory) -> None:
    instrument, provider, sessions, first, request = seed_phase6_snapshot(
        factory, suffix=f"replay-{uuid4().hex}"
    )
    runner = Phase6ExperimentRunner(factory)
    experiment_one = runner.run(request)
    with factory() as session:
        stored = session.get(DatasetSnapshotRecord, first.snapshot_id)
        before = (stored.snapshot_id, stored.content_hash, stored.manifest_json)
        before_manifest = json.loads(stored.manifest_json)
    corrected_bars = list(provider.bars[instrument.symbol])
    corrected_bars[-1] = daily_bar(sessions[-1], Decimal("250"), "provider-correction")
    correction_provider = MappingProvider(provider.name, {instrument.symbol: corrected_bars}, {})
    correction = PersistentMarketDataService(factory).ingest(
        correction_provider,
        instrument,
        sessions[-1],
        sessions[-1],
        CALENDAR.session_close(sessions[-1]) + timedelta(days=1),
    )
    assert correction.status == "SUCCEEDED" and correction.observations[0].revision == 2
    replay = runner.run(request)
    with factory() as session:
        stored = session.get(DatasetSnapshotRecord, first.snapshot_id)
        assert (stored.snapshot_id, stored.content_hash, stored.manifest_json) == before
        assert json.loads(stored.manifest_json)["observations"] == before_manifest["observations"]
    assert replay.id == experiment_one.id and replay.result_json == experiment_one.result_json
    second = DatasetSnapshotService(factory).build(
        as_of=CALENDAR.session_close(sessions[-1]) + timedelta(days=1),
        provider=provider.name,
        universe_id=first.universe_id,
        start=sessions[0],
        end=sessions[-1],
        minimum_coverage=Decimal("1"),
    )
    with factory() as session:
        second_manifest = json.loads(
            session.get(DatasetSnapshotRecord, second.snapshot_id).manifest_json
        )
    assert second.snapshot_id != first.snapshot_id
    assert second.content_hash != first.content_hash
    assert any(item["revision"] == 2 for item in second_manifest["observations"])
    second_request = request.__class__(
        second.snapshot_id,
        request.strategy_name,
        request.strategy_version,
        request.parameter_configs,
        code_sha=request.code_sha,
    )
    assert runner.run(second_request).id != experiment_one.id


def test_postgres_pit_abc_coverage_has_no_survivorship_leakage(factory) -> None:
    suffix = uuid4().hex
    sessions = list(CALENDAR.sessions_between(date(2026, 2, 2), date(2026, 2, 20)))[:12]
    instruments = [
        Instrument(
            f"{name}-{suffix}", name, "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2020, 1, 1)
        )
        for name in ("A", "B", "C")
    ]
    provider = MappingProvider(
        f"pit-{suffix}",
        {
            item.symbol: [
                daily_bar(day, Decimal(100 + index)) for index, day in enumerate(sessions)
            ]
            for item in instruments
        },
        {},
    )
    service = PersistentMarketDataService(factory)
    for item in instruments:
        assert (
            service.ingest(
                provider, item, sessions[0], sessions[-1], CALENDAR.session_close(sessions[-1])
            ).status
            == "SUCCEEDED"
        )
    universe_id = f"pit-{suffix}"
    entry, exit_day = sessions[6], sessions[6]
    with factory() as session, session.begin():
        session.add(
            UniverseDefinitionRecord(
                universe_id=universe_id,
                name=universe_id,
                kind="POINT_IN_TIME_MEMBERSHIP",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        session.add_all(
            [
                UniverseMembershipRecord(
                    universe_id=universe_id,
                    instrument_id=instruments[0].instrument_id,
                    valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                    valid_to=None,
                    known_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                UniverseMembershipRecord(
                    universe_id=universe_id,
                    instrument_id=instruments[1].instrument_id,
                    valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                    valid_to=CALENDAR.session_open(exit_day),
                    known_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                UniverseMembershipRecord(
                    universe_id=universe_id,
                    instrument_id=instruments[2].instrument_id,
                    valid_from=CALENDAR.session_open(entry),
                    valid_to=None,
                    known_at=CALENDAR.session_open(entry),
                ),
            ]
        )
        if (
            session.scalar(
                select(StrategyRecord).where(
                    StrategyRecord.strategy_name == "multi_asset_trend",
                    StrategyRecord.strategy_version == "1.0.0",
                )
            )
            is None
        ):
            session.add(
                StrategyRecord(
                    strategy_identity=f"trend-{suffix}",
                    strategy_name="multi_asset_trend",
                    strategy_version="1.0.0",
                    created_at=datetime.now(UTC),
                    metadata_json="{}",
                )
            )
    snapshot = DatasetSnapshotService(factory).build(
        as_of=CALENDAR.session_close(sessions[-1]),
        provider=provider.name,
        universe_id=universe_id,
        start=sessions[0],
        end=sessions[-1],
        minimum_coverage=Decimal("1"),
    )
    with factory() as session:
        manifest = json.loads(
            session.get(DatasetSnapshotRecord, snapshot.snapshot_id).manifest_json
        )
        observation_ids = [item["id"] for item in manifest["observations"]]
        rows = tuple(
            session.scalars(
                select(MarketObservationRecord).where(
                    MarketObservationRecord.observation_id.in_(observation_ids)
                )
            )
        )
    used = {(row.instrument_id, row.session_date.date()) for row in rows}
    assert all((instruments[0].instrument_id, day) in used for day in sessions)
    assert all((instruments[1].instrument_id, day) not in used for day in sessions[6:])
    assert all((instruments[2].instrument_id, day) not in used for day in sessions[:6])
    assert manifest["expected_count"] == len(sessions) + 6 + (len(sessions) - 6)
    assert manifest["present_count"] == manifest["expected_count"]
    experiment = Phase6ExperimentRunner(factory).run(
        Phase6ExperimentRequest(
            snapshot.snapshot_id,
            "multi_asset_trend",
            "1.0.0",
            ({"fast": 2, "slow": 3},),
            code_sha="a" * 40,
        )
    )
    assert experiment.status == "COMPLETED" and experiment.decision == "RESEARCH_ONLY"


def test_postgres_corporate_actions_are_persistent_immutable_and_causal(factory) -> None:
    suffix = uuid4().hex
    sessions = list(CALENDAR.sessions_between(date(2026, 3, 2), date(2026, 3, 20)))[:10]
    instrument = Instrument(
        f"action-{suffix}",
        f"A{suffix[:7]}",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2020, 1, 1),
    )
    known_at = CALENDAR.session_close(sessions[6])
    split = CorporateAction(
        f"split-{suffix}",
        instrument.instrument_id,
        CorporateActionKind.SPLIT,
        CALENDAR.session_open(sessions[5]),
        known_at,
        Decimal("2"),
    )
    dividend = CorporateAction(
        f"dividend-{suffix}",
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        CALENDAR.session_open(sessions[7]),
        CALENDAR.session_close(sessions[7]),
        Decimal("1"),
    )
    provider = MappingProvider(
        f"actions-{suffix}",
        {
            instrument.symbol: [
                daily_bar(day, Decimal("100" if index < 5 else "50"))
                for index, day in enumerate(sessions)
            ]
        },
        {instrument.symbol: [split, dividend]},
    )
    ingestion = PersistentMarketDataService(factory)
    assert (
        ingestion.ingest(
            provider, instrument, sessions[0], sessions[4], CALENDAR.session_close(sessions[4])
        ).status
        == "SUCCEEDED"
    )
    universe_id = f"actions-{suffix}"
    with factory() as session, session.begin():
        session.add(
            UniverseDefinitionRecord(
                universe_id=universe_id,
                name=universe_id,
                kind="POINT_IN_TIME_MEMBERSHIP",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            UniverseMembershipRecord(
                universe_id=universe_id,
                instrument_id=instrument.instrument_id,
                valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                valid_to=None,
                known_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    snapshots = DatasetSnapshotService(factory)
    old = snapshots.build(
        as_of=CALENDAR.session_close(sessions[4]),
        provider=provider.name,
        universe_id=universe_id,
        start=sessions[0],
        end=sessions[4],
        minimum_coverage=Decimal("1"),
    )
    assert (
        ingestion.ingest(
            provider,
            instrument,
            sessions[5],
            sessions[-1],
            CALENDAR.session_close(sessions[-1]),
        ).status
        == "SUCCEEDED"
    )
    new = snapshots.build(
        as_of=CALENDAR.session_close(sessions[-1]),
        provider=provider.name,
        universe_id=universe_id,
        start=sessions[0],
        end=sessions[-1],
        minimum_coverage=Decimal("1"),
    )
    with factory() as session:
        assert (
            session.get(CorporateActionRecord, split.action_id).effective_at == split.effective_at
        )
        assert session.get(CorporateActionRecord, split.action_id).known_at == split.known_at
        old_manifest = json.loads(session.get(DatasetSnapshotRecord, old.snapshot_id).manifest_json)
        new_manifest = json.loads(session.get(DatasetSnapshotRecord, new.snapshot_id).manifest_json)
        raw = tuple(
            session.scalars(
                select(MarketObservationRecord)
                .where(MarketObservationRecord.instrument_id == instrument.instrument_id)
                .order_by(MarketObservationRecord.session_date)
            )
        )
    assert old_manifest["corporate_actions"] == []
    assert {item["action_id"] for item in new_manifest["corporate_actions"]} == {
        split.action_id,
        dividend.action_id,
    }
    assert [Decimal(item.close) for item in raw[:6]] == [Decimal("100")] * 5 + [Decimal("50")]
    # Split-adjusted pre-event cena 50 navazuje na raw post-split 50; raw OHLC se nemění.
    from quantlab.market_data import causal_adjusted_close
    from quantlab.market_data_service import _observation

    adjusted = causal_adjusted_close(
        [_observation(item) for item in raw], [split], CALENDAR.session_close(sessions[-1])
    )
    assert adjusted[sessions[4]] == adjusted[sessions[5]] == Decimal("50")
    portfolio = MultiAssetPortfolio(
        Decimal("100"), positions={instrument.instrument_id: Decimal("10")}
    )
    portfolio.apply_action(dividend)
    portfolio.apply_action(dividend)
    assert portfolio.dividend_income == Decimal("10")
    assert portfolio.cash == Decimal("110")


def _seed_opening_observation(
    factory, instrument_id: str, session_day: date, price: Decimal
) -> None:
    suffix = uuid4().hex
    opened_at = CALENDAR.session_open(session_day)
    ingestion_id = f"open-{suffix}"
    with factory() as session, session.begin():
        session.add(
            MarketDataIngestionRecord(
                id=ingestion_id,
                provider="opening-fixture",
                scope_hash=suffix.ljust(64, "0"),
                started_at=opened_at,
                finished_at=opened_at,
                status="SUCCEEDED",
                requested_start=opened_at,
                requested_end=opened_at,
                instrument_count=1,
                row_count=1,
            )
        )
        session.add(
            MarketObservationRecord(
                observation_id=f"opening-{suffix}",
                instrument_id=instrument_id,
                ingestion_id=ingestion_id,
                provider="opening-fixture",
                timeframe="open",
                session_date=datetime.combine(session_day, datetime.min.time(), UTC),
                timestamp=opened_at,
                open=str(price),
                high=str(price),
                low=str(price),
                close=str(price),
                volume="10000",
                observed_at=opened_at,
                source_id=suffix,
                source_hash=suffix.ljust(64, "0"),
                revision=1,
            )
        )


def _research_to_paper(factory, engine, *, halted: bool):
    suffix = uuid4().hex
    instrument, provider, sessions, snapshot, request = seed_phase6_snapshot(factory, suffix=suffix)
    runner = Phase6ExperimentRunner(factory)
    experiment = runner.run(request)
    assert experiment.decision == "RESEARCH_ONLY"
    eligibility = Phase6EligibilityService(factory)
    decision = eligibility.evaluate_eligibility(
        experiment.id, actor={"id": "acceptance"}, reason="M1 acceptance"
    )
    assert decision.status == "ELIGIBLE"
    promoted = eligibility.promote(experiment.id, actor={"id": "acceptance"}, reason="M1 promotion")
    assert promoted.decision == "PAPER_CANDIDATE"
    account_id = f"paper-{suffix}"
    repository = Phase4Repository(
        engine.url.render_as_string(hide_password=False), bootstrap_test_schema=False
    )
    repository.seed_account(account_id, Decimal("100000"))
    if halted:
        repository.halt(account_id, "audit-test", f"halt-{suffix}")
    approved_risk = ProductionRiskConfig(
        max_position_pct=Decimal("1"),
        max_single_order_pct=Decimal("1"),
        max_single_order_notional=Decimal("200000"),
        instrument_allowlist=frozenset({instrument.instrument_id}),
    )
    deployment_service = DeploymentService(factory)
    deployment = deployment_service.create(experiment.id, account_id, risk_config=approved_risk)
    assert deployment.status == "PENDING_REVIEW"
    deployment_service.approve(deployment.deployment_id, datetime.now(UTC))
    monitoring_service = PaperMonitoringService(factory)
    policy = monitoring_service.create_policy(
        f"phase6-e2e-{suffix}", DEFAULT_POLICY.copy(), datetime.now(UTC)
    )
    monitoring = monitoring_service.enroll(
        deployment.deployment_id, policy.policy_id, datetime.now(UTC)
    )
    assert monitoring.state == MonitoringState.ACTIVE
    next_session = CALENDAR.next_session(sessions[-1])
    current_provider = MappingProvider(
        provider.name,
        {instrument.symbol: [daily_bar(next_session, Decimal("120"), "current-not-snapshot")]},
        {},
    )
    assert (
        PersistentMarketDataService(factory)
        .ingest(
            current_provider,
            instrument,
            next_session,
            next_session,
            CALENDAR.session_open(next_session) + timedelta(minutes=1),
        )
        .status
        == "SUCCEEDED"
    )
    _seed_opening_observation(factory, instrument.instrument_id, next_session, Decimal("120"))
    # Procesní default záměrně neumožňuje instrument. Deployment musí použít approved manifest.
    drifted_runtime_risk = ProductionRiskConfig(instrument_allowlist=frozenset())
    cycle = TradingCycleService(repository, drifted_runtime_risk)
    service = Phase6PaperExecutionService(factory, ValidatedCurrentDataAccessor(factory), cycle)
    cycle_id = service.run(deployment.deployment_id, CALENDAR.session_open(next_session))
    return account_id, deployment, experiment, snapshot, cycle_id, instrument, halted


def test_postgres_research_to_paper_authoritative_e2e(factory, engine) -> None:
    account_id, deployment, experiment, snapshot, cycle_id, instrument, _ = _research_to_paper(
        factory, engine, halted=False
    )
    with Session(engine) as session:
        assert session.get(ExperimentRecord, experiment.id).decision == "PAPER_CANDIDATE"
        assert session.get(PaperAccountRecord, account_id).cash < Decimal("100000")
        assert session.get(PositionRecord, (account_id, instrument.instrument_id)).quantity > 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(RiskDecisionRecord)
                .where(RiskDecisionRecord.trading_cycle_id == cycle_id)
            )
            == 1
        )
        fill = session.scalar(
            select(PaperFillRecord)
            .join(PaperOrderRecord)
            .where(PaperOrderRecord.trading_cycle_id == cycle_id)
        )
        assert fill is not None
        # Close-derived signal končí před executable session a fill používá její raw open.
        assert fill.reference_price == Decimal("120")
        assert fill.timestamp == CALENDAR.session_open(fill.timestamp.date())
        assert fill.timestamp > session.get(PaperOrderRecord, fill.order_id).submitted_at
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.trading_cycle_id == cycle_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperFillRecord)
                .join(PaperOrderRecord)
                .where(PaperOrderRecord.trading_cycle_id == cycle_id)
            )
            == 1
        )
        reconciliation = session.scalar(
            select(ReconciliationRecord)
            .where(ReconciliationRecord.correlation_id == cycle_id)
            .order_by(ReconciliationRecord.timestamp.desc())
        )
        assert reconciliation.status == ReconciliationStatus.SUCCEEDED
        lineage = [
            json.loads(item)
            for item in session.scalars(
                select(AuditEventRecord.payload_json).where(
                    AuditEventRecord.entity_id == deployment.deployment_id
                )
            )
        ]
        snapshot_ids = {
            item["id"]
            for item in json.loads(
                session.get(DatasetSnapshotRecord, snapshot.snapshot_id).manifest_json
            )["observations"]
        }

    execution_open = CALENDAR.session_open(fill.timestamp.date())
    immutable_id = uuid4().hex.ljust(64, "0")
    with Session(engine) as session, session.begin():
        session.add(
            PreOpenExecutionIntentRecord(
                intent_id=immutable_id,
                deployment_id=deployment.deployment_id,
                account_id=account_id,
                strategy_id=f"phase6:{deployment.deployment_id}",
                instrument_id=instrument.instrument_id,
                side="BUY",
                quantity=Decimal("1"),
                order_type="MARKET",
                execution_session=execution_open.date(),
                intended_execution_open=execution_open,
                decision_time=execution_open - timedelta(hours=1),
                created_at=execution_open - timedelta(hours=1),
                sizing_reference_price=Decimal("100"),
                sizing_reference_known_at=execution_open - timedelta(hours=1),
                snapshot_id=snapshot.snapshot_id,
                universe_id=snapshot.universe_id,
                signal_observation_ids_json="[]",
                evidence_json="{}",
                integrity_hash="0" * 64,
            )
        )
    with pytest.raises(DBAPIError, match="pre-open execution intents are immutable"):
        with Session(engine) as session, session.begin():
            session.execute(
                update(PreOpenExecutionIntentRecord)
                .where(PreOpenExecutionIntentRecord.intent_id == immutable_id)
                .values(quantity=Decimal("2"))
            )
    with pytest.raises(DBAPIError, match="pre-open execution intents are immutable"):
        with Session(engine) as session, session.begin():
            session.execute(
                delete(PreOpenExecutionIntentRecord).where(
                    PreOpenExecutionIntentRecord.intent_id == immutable_id
                )
            )
    matching = next(
        item
        for item in lineage
        if item.get("experiment_id") == experiment.id
        and item.get("snapshot_id") == snapshot.snapshot_id
    )
    assert matching["current_observation_ids"]
    assert set(matching["current_observation_ids"]).isdisjoint(snapshot_ids)
    assert set(matching["signal_observation_ids"]).isdisjoint(matching["current_observation_ids"])
    assert matching["signal_through_session"] < matching["executable_session"]
    assert datetime.fromisoformat(matching["execution_time"]) > datetime.fromisoformat(
        matching["decision_time"]
    )
    assert {
        instrument_id: Decimal(raw_open)
        for instrument_id, raw_open in matching["raw_open_by_instrument"].items()
    } == {instrument.instrument_id: Decimal("120")}


def test_postgres_halted_approved_deployment_cannot_trade(factory, engine) -> None:
    account_id, _, _, _, cycle_id, _, _ = _research_to_paper(factory, engine, halted=True)
    with Session(engine) as session:
        assert (
            session.get(PaperAccountRecord, account_id).trading_state == SystemTradingState.HALTED
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperFillRecord)
                .join(PaperOrderRecord)
                .where(PaperOrderRecord.account_id == account_id)
            )
            == 0
        )
        decision = session.scalar(
            select(RiskDecisionRecord).where(RiskDecisionRecord.trading_cycle_id == cycle_id)
        )
        assert decision is not None and "TRADING_HALTED" in decision.reasons_json
