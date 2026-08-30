from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
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
from quantlab.market_data_service import (
    CorporateActionCancellationRecord,
    CorporateActionEventAuditRecord,
    CorporateActionRevisionRecord,
    PersistentMarketDataService,
)
from quantlab.persistence import (
    CorporateActionEventRecord,
    CorporateActionReadinessRecord,
    CorporateActionRecord,
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


def test_event_persistence_separates_provider_time_from_first_local_receipt(scope) -> None:
    factory, _ = scope
    suffix = uuid4().hex
    provider_at = datetime(2026, 8, 29, 15, tzinfo=UTC)
    received_at = datetime(2026, 8, 29, 17, tzinfo=UTC)
    event = CorporateActionEvent(
        f"receipt-{suffix}",
        provider_at,
        CorporateActionEventType.INSERT,
        f"ca-{suffix}",
        "c" * 64,
        received_at,
        ("H2A",),
        date(2026, 8, 30),
    )
    service = PersistentMarketDataService(factory)

    service.record_corporate_action_event("alpaca", event)
    loaded = next(item for item in service.corporate_action_events("alpaca") if item.event_id == event.event_id)

    assert loaded.at == provider_at
    assert loaded.received_at == received_at
    assert loaded.symbols == ("H2A",)
    with factory() as session:
        persisted = session.get(CorporateActionEventRecord, event.event_id)
        audit = session.get(CorporateActionEventAuditRecord, event.event_id)
        assert persisted is not None and persisted.occurred_at == received_at
        assert audit is not None and audit.provider_at == provider_at


def test_corporate_action_event_tables_reject_direct_mutation(scope) -> None:
    factory, _ = scope
    suffix = uuid4().hex
    event = CorporateActionEvent(
        f"immutable-{suffix}",
        datetime(2026, 8, 29, 15, tzinfo=UTC),
        CorporateActionEventType.INSERT,
        f"ca-{suffix}",
        "d" * 64,
        datetime(2026, 8, 29, 16, tzinfo=UTC),
    )
    PersistentMarketDataService(factory).record_corporate_action_event("alpaca", event)

    with factory() as session, pytest.raises(DBAPIError, match="immutable"):
        session.execute(
            text("UPDATE corporate_action_events SET action='delete' WHERE event_id=:event_id"),
            {"event_id": event.event_id},
        )
        session.commit()


def test_updated_action_creates_immutable_revision_and_updates_current_projection(scope) -> None:
    factory, instrument = scope
    suffix = uuid4().hex
    action_id = uuid4().hex
    provider_action_id = f"provider-{suffix}"
    first = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.SPLIT,
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 19, tzinfo=UTC),
        Decimal("2"),
        None,
        provider_action_id,
        "a" * 64,
    )
    revised = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.SPLIT,
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 20, tzinfo=UTC),
        Decimal("4"),
        None,
        provider_action_id,
        "b" * 64,
    )
    service = PersistentMarketDataService(factory)

    service.verify_corporate_action_readiness(
        ActionProvider(True, [first]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 21, tzinfo=UTC),
    )
    service.verify_corporate_action_readiness(
        ActionProvider(True, [revised]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 22, tzinfo=UTC),
    )

    with factory() as session:
        revisions = session.scalars(
            select(CorporateActionRevisionRecord).where(
                CorporateActionRevisionRecord.action_id == action_id
            )
        ).all()
        current = session.get(CorporateActionRecord, action_id)
        assert len(revisions) == 2
        assert {row.value for row in revisions} == {"2", "4"}
        assert current is not None and current.value == "4"
        assert current.known_at == revised.known_at


def test_delete_event_tombstones_current_projection_and_persists_cancellation(scope) -> None:
    factory, instrument = scope
    suffix = uuid4().hex
    action_id = uuid4().hex
    provider_action_id = f"provider-{suffix}"
    action = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        datetime(2026, 8, 25, tzinfo=UTC),
        datetime(2026, 8, 20, tzinfo=UTC),
        Decimal("1.25"),
        None,
        provider_action_id,
        "e" * 64,
    )
    service = PersistentMarketDataService(factory)
    service.verify_corporate_action_readiness(
        ActionProvider(True, [action]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 21, tzinfo=UTC),
    )
    delete = CorporateActionEvent(
        f"delete-{suffix}",
        datetime(2026, 8, 21, 15, tzinfo=UTC),
        CorporateActionEventType.DELETE,
        provider_action_id,
        "f" * 64,
        datetime(2026, 8, 21, 16, tzinfo=UTC),
        (instrument.symbol,),
        date(2026, 8, 25),
    )

    service.record_corporate_action_event("h2-True-False-False", delete)

    with factory() as session:
        current = session.get(CorporateActionRecord, action_id)
        cancellations = session.scalars(
            select(CorporateActionCancellationRecord).where(
                CorporateActionCancellationRecord.action_id == action_id
            )
        ).all()
        assert current is not None and current.effective_at.year == 9999
        assert len(cancellations) == 1
        assert cancellations[0].known_at == datetime(2026, 8, 21, 16, tzinfo=UTC)


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
