from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_phase6_experiment_audit import _historical_action_fixture

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
    corporate_action_logical_id,
)
from quantlab.market_data_service import (
    CorporateActionCancellationRecord,
    CorporateActionEventAuditRecord,
    CorporateActionRevisionCanonicalizationRecord,
    CorporateActionRevisionRecord,
    PersistentMarketDataService,
    canonical_corporate_action_revisions,
)
from quantlab.persistence import (
    CorporateActionEventRecord,
    CorporateActionReadinessRecord,
    CorporateActionRecord,
    DatasetSnapshotRecord,
    InstrumentRecord,
)
from quantlab.phase4 import Phase4Repository
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6EligibilityService,
    Phase6ExperimentRunner,
)
from quantlab.phase7 import DEFAULT_POLICY, MonitoringState, PaperMonitoringService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


@dataclass
class ActionProvider:
    supports: bool
    actions: list[CorporateAction]
    fails: bool = False
    knowledge_unavailable: bool = False
    name: str | None = None
    lineage: str | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            self.name or f"h2-{self.supports}-{self.fails}-{self.knowledge_unavailable}",
            "1",
            self.supports,
            False,
            self.lineage,
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


def test_split_known_after_signal_close_is_ready_at_preopen_cutoff(scope) -> None:
    factory, instrument = scope
    signal_close = datetime(2026, 8, 24, 20, tzinfo=UTC)
    received_at = datetime(2026, 8, 25, 11, tzinfo=UTC)
    preopen_decision = datetime(2026, 8, 25, 13, tzinfo=UTC)
    execution_open = datetime(2026, 8, 25, 13, 30, tzinfo=UTC)
    split = CorporateAction(
        uuid4().hex,
        instrument.instrument_id,
        CorporateActionKind.SPLIT,
        execution_open,
        received_at,
        Decimal("2"),
    )
    evidence_id = PersistentMarketDataService(
        factory, clock=lambda: preopen_decision
    ).verify_corporate_action_readiness(
        ActionProvider(True, [split]),
        instrument,
        date(2026, 8, 1),
        execution_open.date(),
        preopen_decision,
    )
    with factory() as session:
        evidence = session.get(CorporateActionReadinessRecord, evidence_id)
        persisted = session.get(CorporateActionRecord, split.action_id)
        assert evidence is not None and evidence.status == "COMPLETE"
        assert evidence.knowledge_cutoff == preopen_decision
        assert evidence.requested_end.date() == execution_open.date()
        assert persisted is not None and persisted.known_at == received_at
        assert persisted.effective_at == execution_open
    assert signal_close < received_at <= preopen_decision < execution_open


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
    loaded = next(
        item
        for item in service.corporate_action_events("alpaca")
        if item.event_id == event.event_id
    )

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


def test_a_to_b_to_a_persists_three_causal_incarnations_idempotently(scope) -> None:
    factory, instrument = scope
    provider = "h2-True-False-False"
    provider_action_id = f"provider-{uuid4().hex}"
    action_id = corporate_action_logical_id(provider, provider_action_id)

    def action(payload_hash: str, value: str, hour: int) -> CorporateAction:
        return CorporateAction(
            action_id,
            instrument.instrument_id,
            CorporateActionKind.SPLIT,
            datetime(2026, 8, 25, tzinfo=UTC),
            datetime(2026, 8, 20, hour, tzinfo=UTC),
            Decimal(value),
            None,
            provider_action_id,
            payload_hash,
        )

    incarnations = (
        action("a" * 64, "2", 10),
        action("b" * 64, "4", 11),
        action("a" * 64, "2", 12),
    )
    service = PersistentMarketDataService(factory)
    for index, incarnation in enumerate(incarnations):
        service.verify_corporate_action_readiness(
            ActionProvider(True, [incarnation]),
            instrument,
            date(2026, 8, 1),
            date(2026, 8, 26),
            datetime(2026, 8, 21 + index, tzinfo=UTC),
        )
    # Opakovaný readiness nad stejnou aktivní incarnation nesmí přidat revizi.
    service.verify_corporate_action_readiness(
        ActionProvider(True, [incarnations[-1]]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 23, tzinfo=UTC),
    )

    with factory() as session:
        revisions = session.scalars(
            select(CorporateActionRevisionRecord)
            .where(CorporateActionRevisionRecord.action_id == action_id)
            .order_by(CorporateActionRevisionRecord.known_at)
        ).all()
        assert [(row.payload_hash, row.known_at) for row in revisions] == [
            (incarnation.payload_hash, incarnation.known_at) for incarnation in incarnations
        ]


def test_legacy_revision_id_is_reused_by_semantic_incarnation_identity(scope) -> None:
    factory, instrument = scope
    provider = "h2-True-False-False"
    provider_action_id = f"legacy-{uuid4().hex}"
    action_id = corporate_action_logical_id(provider, provider_action_id)
    known_at = datetime(2026, 8, 30, 11, 31, 6, 378413, tzinfo=UTC)
    action = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        datetime(2026, 8, 10, tzinfo=UTC),
        known_at,
        Decimal("1.69"),
        None,
        provider_action_id,
        "d" * 64,
    )
    legacy_revision_id = uuid4().hex.ljust(64, "0")
    with factory() as session, session.begin():
        session.add(
            CorporateActionRevisionRecord(
                revision_id=legacy_revision_id,
                action_id=action_id,
                provider=provider,
                provider_action_id=provider_action_id,
                payload_hash=action.payload_hash,
                instrument_id=instrument.instrument_id,
                kind=action.kind.value,
                effective_at=action.effective_at,
                known_at=known_at,
                value="1.69",
                new_symbol=None,
            )
        )

    PersistentMarketDataService(factory).verify_corporate_action_readiness(
        ActionProvider(True, [action]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 31, tzinfo=UTC),
    )

    with factory() as session:
        revisions = session.scalars(
            select(CorporateActionRevisionRecord).where(
                CorporateActionRevisionRecord.action_id == action_id
            )
        ).all()
        assert len(revisions) == 1
        assert revisions[0].revision_id == legacy_revision_id
        assert revisions[0].known_at == known_at


def test_ibm_legacy_revision_is_canonicalized_and_projection_repaired(scope) -> None:
    factory, instrument = scope
    evidence_provider = "alpaca"
    revision_provider = "alpaca:iex"
    provider_action_id = "fa45827c-2bfb-454f-8107-23852efbaae6"
    action_id = corporate_action_logical_id(evidence_provider, provider_action_id)
    first_receipt = datetime(2026, 8, 30, 11, 12, 36, 213520, tzinfo=UTC)
    legacy_receipt = datetime(2026, 8, 30, 11, 31, 6, 378413, tzinfo=UTC)
    payload_hash = "d7d7b91079f6282dd921bd570d0949d6c3eece5cb232cc144f5360b963003d6b"
    action = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        datetime(2026, 8, 10, tzinfo=UTC),
        first_receipt,
        Decimal("1.69"),
        None,
        provider_action_id,
        payload_hash,
    )
    service = PersistentMarketDataService(factory, clock=lambda: datetime(2026, 8, 31, tzinfo=UTC))
    for event_id, received_at in (("ibm-first", first_receipt), ("ibm-legacy", legacy_receipt)):
        service.record_corporate_action_event(
            evidence_provider,
            CorporateActionEvent(
                event_id,
                received_at,
                CorporateActionEventType.UPDATE,
                provider_action_id,
                payload_hash,
                received_at,
                (instrument.symbol,),
                date(2026, 8, 10),
            ),
        )
    rows = (
        (
            "b6e9de44b2f1ef56200c007241ac74a51f38f45393cc2428cecc716859317b3b",
            first_receipt,
        ),
        (
            "bc4ccd4bc9339cca8c9168f7aa7ab1b4a0be941f090545470b06f9abf72f2916",
            legacy_receipt,
        ),
    )
    with factory() as session, session.begin():
        for revision_id, known_at in rows:
            session.add(
                CorporateActionRevisionRecord(
                    revision_id=revision_id,
                    action_id=action_id,
                    provider=revision_provider,
                    provider_action_id=provider_action_id,
                    payload_hash=payload_hash,
                    instrument_id=instrument.instrument_id,
                    kind=action.kind.value,
                    effective_at=action.effective_at,
                    known_at=known_at,
                    value="1.69",
                    new_symbol=None,
                )
            )
        session.add(
            CorporateActionRecord(
                action_id=action_id,
                instrument_id=instrument.instrument_id,
                kind=action.kind.value,
                effective_at=action.effective_at,
                known_at=legacy_receipt,
                value="1.69",
                new_symbol=None,
            )
        )

    readiness = None
    for _ in range(3):
        result = service.verify_corporate_action_readiness(
            ActionProvider(
                True,
                [action],
                name=evidence_provider,
                lineage=revision_provider,
            ),
            instrument,
            date(2026, 8, 1),
            date(2026, 8, 26),
            datetime(2026, 8, 31, tzinfo=UTC),
        )
        readiness = readiness or result
        assert result == readiness

    with factory() as session:
        raw = session.scalars(
            select(CorporateActionRevisionRecord).where(
                CorporateActionRevisionRecord.action_id == action_id
            )
        ).all()
        canonical = canonical_corporate_action_revisions(
            session, CorporateActionRevisionRecord.action_id == action_id
        )
        links = session.scalars(select(CorporateActionRevisionCanonicalizationRecord)).all()
        current = session.get(CorporateActionRecord, action_id)
        assert len(raw) == 2
        assert [(row.revision_id, row.known_at) for row in canonical] == [rows[0]]
        assert [(row.superseded_revision_id, row.canonical_revision_id) for row in links] == [
            (rows[1][0], rows[0][0])
        ]
        assert links[0].provider == revision_provider
        source_event = session.get(CorporateActionEventRecord, links[0].source_event_id)
        assert source_event is not None and source_event.provider == evidence_provider
        assert current is not None and current.known_at == first_receipt

    with factory() as session, pytest.raises(DBAPIError, match="immutable"):
        session.execute(
            text(
                "UPDATE corporate_action_revision_canonicalizations "
                "SET reason='tampered' WHERE superseded_revision_id=:revision_id"
            ),
            {"revision_id": rows[1][0]},
        )
        session.commit()


def test_delete_event_tombstones_current_projection_and_persists_cancellation(scope) -> None:
    factory, instrument = scope
    suffix = uuid4().hex
    provider = "h2-True-False-False"
    provider_action_id = f"provider-{suffix}"
    action_id = corporate_action_logical_id(provider, provider_action_id)
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

    service.record_corporate_action_event(provider, delete)

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


def test_delete_then_reinsert_same_payload_preserves_cancellation_and_both_revisions(scope) -> None:
    factory, instrument = scope
    provider = "h2-True-False-False"
    provider_action_id = f"provider-{uuid4().hex}"
    action_id = corporate_action_logical_id(provider, provider_action_id)
    first = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        datetime(2026, 8, 25, tzinfo=UTC),
        datetime(2026, 8, 20, 10, tzinfo=UTC),
        Decimal("1.25"),
        None,
        provider_action_id,
        "e" * 64,
    )
    reinserted = CorporateAction(
        action_id,
        instrument.instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        first.effective_at,
        datetime(2026, 8, 20, 12, tzinfo=UTC),
        first.value,
        None,
        provider_action_id,
        first.payload_hash,
    )
    service = PersistentMarketDataService(factory)
    service.verify_corporate_action_readiness(
        ActionProvider(True, [first]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 21, tzinfo=UTC),
    )
    service.record_corporate_action_event(
        provider,
        CorporateActionEvent(
            f"delete-{uuid4().hex}",
            datetime(2026, 8, 20, 9, tzinfo=UTC),
            CorporateActionEventType.DELETE,
            provider_action_id,
            "f" * 64,
            datetime(2026, 8, 20, 11, tzinfo=UTC),
            (instrument.symbol,),
            date(2026, 8, 25),
        ),
    )
    service.verify_corporate_action_readiness(
        ActionProvider(True, [reinserted]),
        instrument,
        date(2026, 8, 1),
        date(2026, 8, 26),
        datetime(2026, 8, 22, tzinfo=UTC),
    )

    with factory() as session:
        revisions = session.scalars(
            select(CorporateActionRevisionRecord)
            .where(CorporateActionRevisionRecord.action_id == action_id)
            .order_by(CorporateActionRevisionRecord.known_at)
        ).all()
        cancellations = session.scalars(
            select(CorporateActionCancellationRecord).where(
                CorporateActionCancellationRecord.action_id == action_id
            )
        ).all()
        current = session.get(CorporateActionRecord, action_id)
        assert [row.known_at for row in revisions] == [first.known_at, reinserted.known_at]
        assert len(cancellations) == 1
        assert cancellations[0].known_at == datetime(2026, 8, 20, 11, tzinfo=UTC)
        assert current is not None and current.known_at == reinserted.known_at


def test_downgrade_preserves_immutable_canonicalization_and_revisions(scope) -> None:
    factory, instrument = scope
    provider_action_id = f"downgrade-{uuid4().hex}"
    action_id = corporate_action_logical_id("alpaca:iex", provider_action_id)
    with factory() as session, session.begin():
        for suffix, known_at in (
            ("first", datetime(2026, 8, 20, 10, tzinfo=UTC)),
            ("second", datetime(2026, 8, 20, 12, tzinfo=UTC)),
        ):
            session.add(
                CorporateActionRevisionRecord(
                    revision_id=f"{suffix}-{uuid4().hex}".ljust(64, "0"),
                    action_id=action_id,
                    provider="alpaca:iex",
                    provider_action_id=provider_action_id,
                    payload_hash="a" * 64,
                    instrument_id=instrument.instrument_id,
                    kind=CorporateActionKind.CASH_DIVIDEND.value,
                    effective_at=datetime(2026, 8, 25, tzinfo=UTC),
                    known_at=known_at,
                    value="1.69",
                    new_symbol=None,
                )
            )

    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    with pytest.raises(RuntimeError, match="není možný bez ztráty immutable"):
        command.downgrade(config, "20260830_02")

    with factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20260831_02"
        assert (
            session.scalar(
                select(func.count()).select_from(CorporateActionRevisionCanonicalizationRecord)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CorporateActionRevisionRecord)
                .where(CorporateActionRevisionRecord.action_id == action_id)
            )
            == 2
        )


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


def test_postgres_enrolls_snapshot_backed_by_superseded_raw_revision(scope) -> None:
    factory, _ = scope
    snapshot, request, canonical_at, legacy_at = _historical_action_fixture(factory)
    runner = Phase6ExperimentRunner(factory)
    replay = runner.replay(request)
    experiment = runner.run(request)
    eligibility = Phase6EligibilityService(factory)
    eligibility.evaluate_eligibility(experiment.id, actor={"id": "pr81"}, reason="PR81")
    eligibility.promote(experiment.id, actor={"id": "pr81"}, reason="PR81")
    account_id = f"paper-pr81-{uuid4().hex}"
    Phase4Repository(
        factory.kw["bind"].url.render_as_string(hide_password=False),
        bootstrap_test_schema=False,
    ).seed_account(account_id, Decimal("100000"))
    deployment_service = DeploymentService(factory)
    deployment = deployment_service.create(experiment.id, account_id)
    deployment_service.approve(deployment.deployment_id, datetime.now(UTC))
    monitoring_service = PaperMonitoringService(factory)
    policy = monitoring_service.create_policy(
        f"pr81-{uuid4().hex}", DEFAULT_POLICY.copy(), datetime.now(UTC)
    )

    monitoring = monitoring_service.enroll(
        deployment.deployment_id, policy.policy_id, datetime.now(UTC)
    )

    assert replay.oos_sessions
    assert monitoring.state == MonitoringState.ACTIVE
    with factory() as session:
        persisted_snapshot = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        current = session.get(CorporateActionRecord, "a" * 64)
        assert persisted_snapshot is not None and persisted_snapshot.status == "VALID"
        assert current is not None and current.known_at == canonical_at
        assert current.known_at != legacy_at
