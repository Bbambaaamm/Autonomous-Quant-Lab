from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

import pytest
from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_phase7_e2e_postgres import _economic_counts, _execution_service
from test_phase7_postgres import _completed_as_of, _executed_monitoring

from quantlab.domain import Bar
from quantlab.market_data import CorporateActionKind, DatasetInvalid
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import CorporateActionRecord, MarketObservationRecord
from quantlab.phase4 import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    PositionRecord,
    ProductionRiskConfig,
    TradingCycleService,
)
from quantlab.phase6_runtime import ValidatedCurrentDataAccessor
from quantlab.phase7 import (
    PaperCorporateActionApplicationRecord,
    PaperCorporateActionService,
    PaperMonitoringRunRecord,
    PaperPerformanceService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


@pytest.fixture
def factory():
    from sqlalchemy import create_engine

    return sessionmaker(create_engine(os.environ["DATABASE_URL"]), expire_on_commit=False)


def _action(
    factory,
    instrument_id: str,
    kind: CorporateActionKind,
    effective: datetime,
    known: datetime,
    value: str | None,
) -> str:
    action_id = f"phase7-{kind}-{uuid4().hex}"
    with factory() as session, session.begin():
        session.add(
            CorporateActionRecord(
                action_id=action_id,
                instrument_id=instrument_id,
                kind=kind,
                effective_at=effective,
                known_at=known,
                value=value,
                new_symbol=None,
            )
        )
    return action_id


def _snapshot_market_price(factory, snapshot) -> Decimal:
    observation_id = json.loads(snapshot.observation_lineage_json)[0]["observation_id"]
    with factory() as session:
        return Decimal(
            session.scalar(
                select(MarketObservationRecord.close).where(
                    MarketObservationRecord.observation_id == observation_id
                )
            )
        )


def _next_account_session(factory, account_id: str, after: date) -> date:
    with factory() as session:
        created_day = session.get(PaperAccountRecord, account_id).created_at.date()
    return CALENDAR.next_session(max(after, created_day))


def test_late_known_corporate_action_is_causal_and_exactly_once(factory) -> None:
    account, _deployment, _run, instrument = _executed_monitoring(factory)
    with factory() as session:
        before = session.get(PositionRecord, (account, instrument.instrument_id)).quantity
    effective = datetime.now(UTC) + timedelta(seconds=1)
    known = effective + timedelta(days=2)
    action_id = _action(
        factory, instrument.instrument_id, CorporateActionKind.SPLIT, effective, known, "2"
    )
    assert PaperCorporateActionService(factory).apply(account, effective + timedelta(days=1)) == ()
    with factory() as session:
        assert session.get(PositionRecord, (account, instrument.instrument_id)).quantity == before
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperCorporateActionApplicationRecord)
                .where(PaperCorporateActionApplicationRecord.action_id == action_id)
            )
            == 0
        )
    first = PaperCorporateActionService(factory).apply(account, known + timedelta(seconds=1))
    assert len(first) == 1
    assert PaperCorporateActionService(factory).apply(account, known + timedelta(seconds=2)) == ()
    with factory() as session:
        assert (
            session.get(PositionRecord, (account, instrument.instrument_id)).quantity == before * 2
        )


def test_delisting_suspends_without_synthetic_economics_and_blocks_execution(factory) -> None:
    account, deployment, run, instrument = _executed_monitoring(factory)
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    _action(factory, instrument.instrument_id, CorporateActionKind.DELISTING, cutoff, cutoff, None)
    before = _economic_counts(factory, account)
    applied = PaperCorporateActionService(factory).apply(account, cutoff + timedelta(seconds=1))
    assert json.loads(applied[0].effect_json)["resolution"] == "SUSPENDED_NO_SYNTHETIC_FILL"
    with factory() as session:
        stored = session.get(PaperMonitoringRunRecord, run.monitoring_id)
        assert stored.state == "SUSPENDED" and stored.state_reason == "DELISTING_UNSUPPORTED"
    assert _economic_counts(factory, account) == before
    with pytest.raises(DatasetInvalid):
        _execution_service(factory, instrument.instrument_id).run(
            deployment.deployment_id, cutoff + timedelta(days=1)
        )
    assert _economic_counts(factory, account) == before


def test_multiple_splits_then_dividend_preserve_entitlement_and_retry(factory) -> None:
    account, _deployment, _run, instrument = _executed_monitoring(factory)
    with factory() as session:
        quantity = session.get(PositionRecord, (account, instrument.instrument_id)).quantity
        cash = session.get(PaperAccountRecord, account).cash
    start = datetime.now(UTC) + timedelta(seconds=1)
    action_ids = [
        _action(factory, instrument.instrument_id, CorporateActionKind.SPLIT, start, start, "2"),
        _action(
            factory,
            instrument.instrument_id,
            CorporateActionKind.SPLIT,
            start + timedelta(seconds=1),
            start + timedelta(seconds=1),
            "1.5",
        ),
        _action(
            factory,
            instrument.instrument_id,
            CorporateActionKind.CASH_DIVIDEND,
            start + timedelta(seconds=2),
            start + timedelta(seconds=2),
            "1",
        ),
    ]
    service = PaperCorporateActionService(factory)
    service.apply(account, start + timedelta(seconds=3))
    service.apply(account, start + timedelta(seconds=4))
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperCorporateActionApplicationRecord)
                .where(
                    PaperCorporateActionApplicationRecord.account_id == account,
                    PaperCorporateActionApplicationRecord.action_id.in_(action_ids),
                )
            )
            == 3
        )
        assert (
            session.get(PositionRecord, (account, instrument.instrument_id)).quantity
            == quantity * 3
        )
        assert session.get(PaperAccountRecord, account).cash == cash + quantity * 3


def test_sell_after_split_preserves_basis_and_realized_pnl(factory) -> None:
    account, _deployment, _run, instrument = _executed_monitoring(factory)
    effective = datetime.now(UTC) + timedelta(seconds=1)
    _action(factory, instrument.instrument_id, CorporateActionKind.SPLIT, effective, effective, "2")
    PaperCorporateActionService(factory).apply(account, effective + timedelta(seconds=1))
    with factory() as session:
        position = session.get(PositionRecord, (account, instrument.instrument_id))
        split_quantity = position.quantity
        split_basis = position.average_cost
        realized_before = position.realized_pnl
        account_equity = session.get(PaperAccountRecord, account).equity
    desired_quantity = (split_quantity / 2).to_integral_value(rounding=ROUND_DOWN)
    target_weight = (desired_quantity + Decimal("0.000001")) * split_basis / account_equity
    decision = effective + timedelta(days=1)
    executable = decision + timedelta(days=1)
    bars = [
        Bar(
            instrument.instrument_id,
            decision,
            split_basis,
            split_basis,
            split_basis,
            split_basis,
            Decimal("1000000"),
            split_basis,
        ),
        Bar(
            instrument.instrument_id,
            executable,
            split_basis,
            split_basis,
            split_basis,
            split_basis,
            Decimal("1000000"),
            split_basis,
        ),
    ]
    repository = Phase4Repository(str(factory.kw["bind"].url), bootstrap_test_schema=False)
    service = TradingCycleService(
        repository,
        ProductionRiskConfig(
            max_position_pct=Decimal("1"),
            max_single_order_pct=Decimal("2"),
            max_single_order_notional=Decimal("1000000"),
            max_notional_per_day=Decimal("200000"),
            max_gross_exposure=Decimal("2"),
            max_net_exposure=Decimal("2"),
            max_leverage=Decimal("2"),
            instrument_allowlist=frozenset({instrument.instrument_id}),
        ),
    )
    cycle_id = service.run(
        account,
        "phase7-split-sell",
        bars,
        {instrument.instrument_id: target_weight},
        executable.date(),
        decision,
    )
    with factory() as session:
        position = session.get(PositionRecord, (account, instrument.instrument_id))
        fills = tuple(
            session.scalars(
                select(PaperFillRecord)
                .join(PaperOrderRecord)
                .where(PaperOrderRecord.trading_cycle_id == cycle_id)
                .order_by(PaperFillRecord.sequence)
            )
        )
        sold_quantity = sum((fill.quantity for fill in fills), Decimal(0))
        execution_cost = sum(
            (
                fill.quantity * (fill.reference_price - fill.price) + fill.commission
                for fill in fills
            ),
            Decimal(0),
        )
        assert fills
        assert sold_quantity > 0
        assert position.quantity == split_quantity - sold_quantity
        assert position.realized_pnl == pytest.approx(
            realized_before - execution_cost, abs=Decimal("0.0001")
        )


def test_partial_sell_after_split_keeps_remaining_dividend_entitlement(factory) -> None:
    account, _deployment, _run, instrument = _executed_monitoring(factory)
    effective = datetime.now(UTC) + timedelta(seconds=1)
    _action(factory, instrument.instrument_id, CorporateActionKind.SPLIT, effective, effective, "2")
    PaperCorporateActionService(factory).apply(account, effective + timedelta(seconds=1))
    with factory() as session:
        position = session.get(PositionRecord, (account, instrument.instrument_id))
        split_quantity, price = position.quantity, position.average_cost
        ledger_equity = session.get(PaperAccountRecord, account).cash + split_quantity * price
    desired_quantity = split_quantity * Decimal("0.75")
    target_weight = desired_quantity * price / ledger_equity
    decision = effective + timedelta(days=1)
    executable = decision + timedelta(days=1)
    bars = [
        Bar(
            instrument.instrument_id,
            decision,
            price,
            price,
            price,
            price,
            Decimal("1000000"),
            price,
        ),
        Bar(
            instrument.instrument_id,
            executable,
            price,
            price,
            price,
            price,
            Decimal("1000000"),
            price,
        ),
    ]
    repository = Phase4Repository(str(factory.kw["bind"].url), bootstrap_test_schema=False)
    TradingCycleService(
        repository,
        ProductionRiskConfig(
            max_position_pct=Decimal("1"),
            max_single_order_pct=Decimal("1"),
            max_single_order_notional=Decimal("1000000"),
            max_notional_per_day=Decimal("200000"),
            instrument_allowlist=frozenset({instrument.instrument_id}),
        ),
    ).run(
        account,
        "phase7-partial-split-sell",
        bars,
        {instrument.instrument_id: target_weight},
        executable.date(),
        decision,
    )
    with factory() as session:
        remaining = session.get(PositionRecord, (account, instrument.instrument_id)).quantity
        cash_before = session.get(PaperAccountRecord, account).cash
    assert remaining < split_quantity
    dividend_at = effective + timedelta(days=2)
    _action(
        factory,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        dividend_at,
        dividend_at,
        "1",
    )
    PaperCorporateActionService(factory).apply(account, dividend_at + timedelta(seconds=1))
    with factory() as session:
        assert session.get(PaperAccountRecord, account).cash == cash_before + remaining


def test_split_performance_continuity_has_no_fake_minus_fifty_percent_return(factory) -> None:
    account, _deployment, run, instrument = _executed_monitoring(factory)
    performance = PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory))
    before = performance.capture(
        run.monitoring_id, _completed_as_of(factory, instrument.instrument_id)
    )
    next_day = _next_account_session(factory, account, before.session_date)
    effective = CALENDAR.session_open(next_day)
    _action(factory, instrument.instrument_id, CorporateActionKind.SPLIT, effective, effective, "2")
    pre_split_price = _snapshot_market_price(factory, before)
    observed = CALENDAR.session_close(next_day)
    PersistentMarketDataService(factory).ingest(
        MappingProvider(
            f"split-{uuid4().hex[:24]}",
            {instrument.symbol: [daily_bar(next_day, pre_split_price / 2, "post-split")]},
            {},
        ),
        instrument,
        next_day,
        next_day,
        observed,
    )
    after = performance.capture(run.monitoring_id, observed + timedelta(minutes=1))
    assert after.daily_return is not None
    assert after.daily_return == pytest.approx(Decimal("0"), abs=Decimal("0.0001"))


def test_dividend_performance_continuity_credits_equity_once_on_retry(factory) -> None:
    account, _deployment, run, instrument = _executed_monitoring(factory)
    performance = PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory))
    before = performance.capture(
        run.monitoring_id, _completed_as_of(factory, instrument.instrument_id)
    )
    next_day = _next_account_session(factory, account, before.session_date)
    effective = CALENDAR.session_open(next_day)
    _action(
        factory,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        effective,
        effective,
        "1",
    )
    with factory() as session:
        quantity = session.get(PositionRecord, (account, instrument.instrument_id)).quantity
    price = _snapshot_market_price(factory, before)
    observed = CALENDAR.session_close(next_day)
    PersistentMarketDataService(factory).ingest(
        MappingProvider(
            f"dividend-{uuid4().hex[:21]}",
            {instrument.symbol: [daily_bar(next_day, price, "ex-dividend-equivalent")]},
            {},
        ),
        instrument,
        next_day,
        next_day,
        observed,
    )
    after = performance.capture(run.monitoring_id, observed + timedelta(minutes=1))
    retry = performance.capture(run.monitoring_id, observed + timedelta(minutes=2))
    assert after.marked_equity == before.marked_equity + quantity
    assert retry.snapshot_id == after.snapshot_id
    assert retry.marked_equity == after.marked_equity
