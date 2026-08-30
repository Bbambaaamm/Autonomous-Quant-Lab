from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from quantlab.market_data import (
    AssetType,
    CorporateAction,
    CorporateActionEvent,
    CorporateActionEventType,
    CorporateActionKind,
    DatasetInvalid,
    Instrument,
    ProviderMetadata,
    ProviderUnavailable,
    StooqProvider,
)
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import (
    CorporateActionEventRecord,
    CorporateActionReadinessRecord,
    InstrumentRecord,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


@dataclass
class ActionProvider:
    supports: bool
    actions: list[CorporateAction]
    fails: bool = False
    knowledge_unavailable: bool = False

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            f"h2-{self.supports}-{self.fails}-{self.knowledge_unavailable}",
            "1",
            self.supports,
            False,
        )

    def resolve(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol}

    def historical_daily(self, symbol, start, end):  # type: ignore[no-untyped-def]
        return []

    def corporate_actions(self, symbol, start, end):  # type: ignore[no-untyped-def]
        if self.knowledge_unavailable:
            raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
        if self.fails:
            raise ProviderUnavailable("simulované selhání actions API")
        return self.actions


@pytest.fixture
def scope():
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    instrument = Instrument(
        f"h2-{suffix}", "H2A", "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2020, 1, 1)
    )
    with factory() as session, session.begin():
        session.add(
            InstrumentRecord(
                instrument_id=instrument.instrument_id,
                symbol=instrument.symbol,
                exchange="XNYS",
                calendar="XNYS",
                currency="USD",
                asset_type="EQUITY",
                active_from=datetime(2020, 1, 1, tzinfo=UTC),
                active_to=None,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    return factory, instrument


@pytest.mark.parametrize("kind", [CorporateActionKind.SPLIT, CorporateActionKind.CASH_DIVIDEND])
def test_unsupported_provider_is_not_empty_complete_even_with_hidden_event(scope, kind) -> None:
    factory, instrument = scope
    cutoff = datetime(2026, 8, 26, 20, tzinfo=UTC)
    hidden = CorporateAction(
        uuid4().hex,
        instrument.instrument_id,
        kind,
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 19, tzinfo=UTC),
        Decimal("2") if kind == CorporateActionKind.SPLIT else Decimal("1.25"),
    )
    service = PersistentMarketDataService(factory, clock=lambda: cutoff)
    with pytest.raises(DatasetInvalid, match="CORPORATE_ACTIONS_UNSUPPORTED"):
        service.verify_corporate_action_readiness(
            ActionProvider(False, [hidden]), instrument, date(2026, 8, 1), date(2026, 8, 26), cutoff
        )
    with factory() as session:
        evidence = session.scalar(
            select(CorporateActionReadinessRecord).where(
                CorporateActionReadinessRecord.instrument_id == instrument.instrument_id
            )
        )
        assert evidence is not None
        assert evidence.status == "UNSUPPORTED"
        assert evidence.action_count == 0


@pytest.mark.parametrize("actions", [[], ["split"]])
def test_capable_provider_can_prove_empty_or_pit_valid_actions(scope, actions) -> None:
    factory, instrument = scope
    cutoff = datetime(2026, 8, 26, 20, tzinfo=UTC)
    payload = (
        []
        if not actions
        else [
            CorporateAction(
                uuid4().hex,
                instrument.instrument_id,
                CorporateActionKind.SPLIT,
                datetime(2026, 8, 20, tzinfo=UTC),
                datetime(2026, 8, 19, tzinfo=UTC),
                Decimal("2"),
            )
        ]
    )
    service = PersistentMarketDataService(factory, clock=lambda: cutoff)
    first = service.verify_corporate_action_readiness(
        ActionProvider(True, payload), instrument, date(2026, 8, 1), date(2026, 8, 26), cutoff
    )
    second = service.verify_corporate_action_readiness(
        ActionProvider(True, payload), instrument, date(2026, 8, 1), date(2026, 8, 26), cutoff
    )
    assert first == second
    with factory() as session:
        evidence = session.get(CorporateActionReadinessRecord, first)
        assert evidence is not None and evidence.status == "COMPLETE"
        assert evidence.action_count == len(payload)


def test_action_api_failure_is_persisted_and_fails_closed(scope) -> None:
    factory, instrument = scope
    cutoff = datetime(2026, 8, 26, 20, tzinfo=UTC)
    with pytest.raises(ProviderUnavailable):
        PersistentMarketDataService(
            factory, clock=lambda: cutoff
        ).verify_corporate_action_readiness(
            ActionProvider(True, [], fails=True),
            instrument,
            date(2026, 8, 1),
            date(2026, 8, 26),
            cutoff,
        )
    with factory() as session:
        evidence = session.scalar(
            select(CorporateActionReadinessRecord).where(
                CorporateActionReadinessRecord.instrument_id == instrument.instrument_id
            )
        )
        assert evidence is not None
        assert evidence.status == "FAILED"
        assert evidence.blocking_reason == "CORPORATE_ACTIONS_UNAVAILABLE"


def test_missing_causal_knowledge_reason_is_persisted_exactly(scope) -> None:
    factory, instrument = scope
    cutoff = datetime(2026, 8, 26, 20, tzinfo=UTC)
    service = PersistentMarketDataService(factory, clock=lambda: cutoff)

    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        service.verify_corporate_action_readiness(
            ActionProvider(True, [], knowledge_unavailable=True),
            instrument,
            date(2026, 8, 1),
            date(2026, 8, 26),
            cutoff,
        )

    with factory() as session:
        evidence = session.scalar(
            select(CorporateActionReadinessRecord).where(
                CorporateActionReadinessRecord.instrument_id == instrument.instrument_id
            )
        )
        assert evidence is not None
        assert evidence.status == "FAILED"
        assert evidence.blocking_reason == "CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"


def test_corporate_action_event_persistence_is_idempotent_and_collision_strict(scope) -> None:
    factory, _ = scope
    suffix = uuid4().hex
    service = PersistentMarketDataService(factory)
    event = CorporateActionEvent(
        f"event-{suffix}",
        datetime(2026, 8, 29, 15, tzinfo=UTC),
        CorporateActionEventType.INSERT,
        f"ca-{suffix}",
        "a" * 64,
    )

    service.record_corporate_action_event("alpaca", event)
    service.record_corporate_action_event("alpaca", event)

    with factory() as session:
        rows = session.scalars(
            select(CorporateActionEventRecord).where(
                CorporateActionEventRecord.event_id == event.event_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].payload_hash == "a" * 64

    collision = CorporateActionEvent(
        event.event_id,
        event.at,
        event.action,
        event.provider_action_id,
        "b" * 64,
    )
    with pytest.raises(DatasetInvalid, match="identity koliduje"):
        service.record_corporate_action_event("alpaca", collision)


def test_standard_stooq_is_explicitly_unsupported(scope) -> None:
    factory, instrument = scope
    cutoff = datetime(2026, 8, 26, 20, tzinfo=UTC)
    with pytest.raises(DatasetInvalid, match="CORPORATE_ACTIONS_UNSUPPORTED"):
        PersistentMarketDataService(
            factory, clock=lambda: cutoff
        ).verify_corporate_action_readiness(
            StooqProvider(lambda url, timeout: pytest.fail("unsupported provider se nesmí volat")),
            instrument,
            date(2026, 8, 1),
            date(2026, 8, 26),
            cutoff,
        )
