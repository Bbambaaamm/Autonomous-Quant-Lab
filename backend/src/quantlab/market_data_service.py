from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Select, String, Text, UniqueConstraint, and_, func, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.domain import require_utc
from quantlab.market_data import (
    CorporateAction,
    CorporateActionEvent,
    CorporateActionEventType,
    DatasetInvalid,
    DatasetSnapshot,
    IngestionResult,
    Instrument,
    MarketDataProvider,
    Observation,
    ProviderBar,
    XNYSCalendar,
    corporate_action_logical_id,
    normalize_bar,
)
from quantlab.persistence import (
    Base,
    CorporateActionEventRecord,
    CorporateActionReadinessRecord,
    CorporateActionRecord,
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)


def _instant(day: date) -> datetime:
    return datetime.combine(day, time.min, UTC)


def _database_utc(value: datetime) -> datetime:
    """Normalizuje ORM timestamp; SQLite může timezone informaci při čtení zahodit."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lock(session: Session, identity: str) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _observation(row: MarketObservationRecord) -> Observation:
    return Observation(
        row.observation_id,
        row.instrument_id,
        row.provider,
        row.timeframe,
        row.session_date.date(),
        _database_utc(row.timestamp),
        Decimal(row.open),
        Decimal(row.high),
        Decimal(row.low),
        Decimal(row.close),
        Decimal(row.volume),
        _database_utc(row.observed_at),
        row.source_id,
        row.source_hash,
        row.ingestion_id,
        row.revision,
    )


class CorporateActionEventAuditRecord(Base):
    """Provider timestamp a scope oddělený od kauzálního lokálního receipt času."""

    __tablename__ = "corporate_action_event_audit"
    event_id: Mapped[str] = mapped_column(
        ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"), primary_key=True
    )
    provider_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False)
    scope_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CorporateActionRevisionRecord(Base):
    """Immutable PIT verze ekonomického corporate-action faktu."""

    __tablename__ = "corporate_action_revisions"
    revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[str | None] = mapped_column(String(50))
    new_symbol: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_action_id",
            "payload_hash",
            name="uq_corporate_action_revision_provider_payload",
        ),
    )


class CorporateActionCancellationRecord(Base):
    """Immutable DELETE/tombstone evidence; více delete/reinsert cyklů zůstává auditovatelných."""

    __tablename__ = "corporate_action_cancellations"
    cancellation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PersistentMarketDataService:
    """Transakční produkční ingestion; provider je jediná část mimo DB transakci."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        calendar: XNYSCalendar | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self.calendar = calendar or XNYSCalendar()
        self.clock = clock or (lambda: datetime.now(UTC))

    def record_corporate_action_event(self, provider: str, event: CorporateActionEvent) -> None:
        """Zapíše první lokální receipt jednou; replay nesmí přepsat kauzální čas."""
        received_at = require_utc(event.received_at or event.at)
        symbols_json = json.dumps(list(event.symbols), sort_keys=True, separators=(",", ":"))
        scope_date = _instant(event.scope_date) if event.scope_date is not None else None
        with self._sessions() as session, session.begin():
            _lock(session, f"corporate-action-event:{provider}:{event.event_id}")
            existing = session.get(CorporateActionEventRecord, event.event_id)
            if existing is not None:
                persisted = (
                    existing.provider,
                    existing.action,
                    existing.provider_action_id,
                    existing.payload_hash,
                )
                values = (
                    provider,
                    event.action.value,
                    event.provider_action_id,
                    event.payload_hash,
                )
                audit = session.get(CorporateActionEventAuditRecord, event.event_id)
                if persisted != values or audit is None:
                    raise DatasetInvalid("Corporate-action event identity koliduje s jiným obsahem")
                audit_values = (
                    _database_utc(audit.provider_at),
                    audit.symbols_json,
                    _database_utc(audit.scope_date) if audit.scope_date is not None else None,
                )
                expected_audit = (event.at, symbols_json, scope_date)
                if audit_values != expected_audit:
                    raise DatasetInvalid("Corporate-action event audit koliduje s jiným obsahem")
                return
            session.add(
                CorporateActionEventRecord(
                    event_id=event.event_id,
                    provider=provider,
                    occurred_at=received_at,
                    action=event.action.value,
                    provider_action_id=event.provider_action_id,
                    payload_hash=event.payload_hash,
                )
            )
            session.flush()
            session.add(
                CorporateActionEventAuditRecord(
                    event_id=event.event_id,
                    provider_at=event.at,
                    symbols_json=symbols_json,
                    scope_date=scope_date,
                )
            )
            if event.action is CorporateActionEventType.DELETE:
                action_id = corporate_action_logical_id(provider, event.provider_action_id)
                cancellation_id = hashlib.sha256(
                    f"{provider}|{event.event_id}|{action_id}".encode()
                ).hexdigest()
                session.add(
                    CorporateActionCancellationRecord(
                        cancellation_id=cancellation_id,
                        action_id=action_id,
                        provider=provider,
                        provider_action_id=event.provider_action_id,
                        event_id=event.event_id,
                        known_at=received_at,
                    )
                )

    def corporate_action_events(self, provider: str) -> tuple[CorporateActionEvent, ...]:
        """Načte persistentní stream evidence v pořadí prvního lokálního receipt času."""
        with self._sessions() as session:
            rows = tuple(
                session.execute(
                    select(CorporateActionEventRecord, CorporateActionEventAuditRecord)
                    .join(
                        CorporateActionEventAuditRecord,
                        CorporateActionEventAuditRecord.event_id
                        == CorporateActionEventRecord.event_id,
                    )
                    .where(CorporateActionEventRecord.provider == provider)
                    .order_by(
                        CorporateActionEventRecord.occurred_at,
                        CorporateActionEventRecord.event_id,
                    )
                )
            )
        result: list[CorporateActionEvent] = []
        for row, audit in rows:
            decoded = json.loads(audit.symbols_json)
            if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
                raise DatasetInvalid("Corporate-action event audit má neplatný symbol scope")
            result.append(
                CorporateActionEvent(
                    row.event_id,
                    _database_utc(audit.provider_at),
                    CorporateActionEventType(row.action),
                    row.provider_action_id,
                    row.payload_hash,
                    _database_utc(row.occurred_at),
                    tuple(decoded),
                    audit.scope_date.date() if audit.scope_date is not None else None,
                )
            )
        return tuple(result)

    def ingest(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        start: date,
        end: date,
        observed_at: datetime,
    ) -> IngestionResult:
        return self._ingest(provider, instrument, start, end, observed_at, executable_open=False)

    def ingest_open(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        session_date: date,
        observed_at: datetime,
    ) -> IngestionResult:
        """Persistuje raw open pouze poté, co session skutečně začala."""
        observed_at = require_utc(observed_at)
        if not self.calendar.is_executable_open_time(session_date, observed_at):
            reason = (
                "EXECUTION_SESSION_NOT_OPEN"
                if observed_at < self.calendar.session_open(session_date)
                else "MISSED_EXECUTION_OPEN"
            )
            raise DatasetInvalid(f"{reason}: raw open request nezačal v XNYS open okně")
        return self._ingest(
            provider,
            instrument,
            session_date,
            session_date,
            observed_at,
            executable_open=True,
        )

    def verify_corporate_action_readiness(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        start: date,
        end: date,
        knowledge_cutoff: datetime,
    ) -> str:
        """Připne úplnost intervalu; prázdný výsledek je úplný jen u capable provideru."""
        cutoff = require_utc(knowledge_cutoff)
        identity = "|".join(
            (
                provider.metadata.name,
                provider.metadata.version,
                instrument.instrument_id,
                start.isoformat(),
                end.isoformat(),
                cutoff.isoformat(),
            )
        )
        with self._sessions() as session:
            existing = session.scalar(
                select(CorporateActionReadinessRecord).where(
                    CorporateActionReadinessRecord.provider == provider.metadata.name,
                    CorporateActionReadinessRecord.provider_version == provider.metadata.version,
                    CorporateActionReadinessRecord.instrument_id == instrument.instrument_id,
                    CorporateActionReadinessRecord.requested_start == _instant(start),
                    CorporateActionReadinessRecord.requested_end == _instant(end),
                    CorporateActionReadinessRecord.knowledge_cutoff == cutoff,
                    CorporateActionReadinessRecord.status == "COMPLETE",
                )
            )
            if existing is not None:
                return existing.evidence_id

        status = "UNSUPPORTED"
        reason: str | None = "CORPORATE_ACTIONS_UNSUPPORTED"
        actions: list[CorporateAction] = []
        error: Exception | None = None
        if provider.metadata.supports_actions:
            try:
                actions = provider.corporate_actions(instrument.symbol, start, end)
                if any(action.instrument_id != instrument.instrument_id for action in actions):
                    raise DatasetInvalid("Corporate action neodpovídá požadovanému instrumentu")
                if any(require_utc(action.known_at) > cutoff for action in actions):
                    raise DatasetInvalid("Corporate action porušuje knowledge cutoff")
                status, reason = "COMPLETE", None
            except Exception as exc:
                status = "FAILED"
                reason = (
                    "CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"
                    if isinstance(exc, DatasetInvalid)
                    and str(exc) == "CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"
                    else "CORPORATE_ACTIONS_UNAVAILABLE"
                )
                error = exc
        evidence_payload = "|".join(sorted(action.action_id for action in actions))
        evidence_id = hashlib.sha256(f"{identity}|{status}|{evidence_payload}".encode()).hexdigest()
        checked_at = require_utc(self.clock())
        with self._sessions() as session, session.begin():
            _lock(session, f"action-readiness:{evidence_id}")
            if session.get(CorporateActionReadinessRecord, evidence_id) is None:
                session.add(
                    CorporateActionReadinessRecord(
                        evidence_id=evidence_id,
                        provider=provider.metadata.name,
                        provider_version=provider.metadata.version,
                        instrument_id=instrument.instrument_id,
                        requested_start=_instant(start),
                        requested_end=_instant(end),
                        knowledge_cutoff=cutoff,
                        checked_at=checked_at,
                        supports_actions=int(provider.metadata.supports_actions),
                        status=status,
                        blocking_reason=reason,
                        action_count=len(actions),
                    )
                )
                for action in actions:
                    self._persist_action(session, provider.metadata.name, action)
        if status != "COMPLETE":
            if error is not None:
                raise error
            raise DatasetInvalid(reason or "CORPORATE_ACTIONS_NOT_READY")
        return evidence_id

    def _ingest(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        start: date,
        end: date,
        observed_at: datetime,
        *,
        executable_open: bool,
    ) -> IngestionResult:
        observed_at = require_utc(observed_at)
        if len(provider.metadata.name) > 40:
            raise DatasetInvalid("Provider identity překračuje persistentní limit 40 znaků")
        scope_identity = (
            f"{provider.metadata.name}|{instrument.instrument_id}|{start}|{end}|"
            f"{observed_at.isoformat()}"
        )
        if executable_open:
            scope_identity += "|open"
        scope = hashlib.sha256(scope_identity.encode()).hexdigest()
        ingestion_id = scope
        with self._sessions() as session, session.begin():
            _lock(session, f"ingestion:{scope}")
            existing = session.get(MarketDataIngestionRecord, ingestion_id)
            if existing is None:
                session.add(
                    MarketDataIngestionRecord(
                        id=ingestion_id,
                        provider=provider.metadata.name,
                        scope_hash=scope,
                        started_at=observed_at,
                        status="STARTED",
                        requested_start=_instant(start),
                        requested_end=_instant(end),
                        instrument_count=1,
                        row_count=0,
                    )
                )
            elif existing.status == "SUCCEEDED":
                rows = session.scalars(
                    select(MarketObservationRecord).where(
                        MarketObservationRecord.ingestion_id == ingestion_id
                    )
                )
                return IngestionResult(
                    ingestion_id, start, end, "SUCCEEDED", tuple(map(_observation, rows))
                )
        try:
            bars = provider.historical_daily(instrument.symbol, start, end)
            actions = (
                provider.corporate_actions(instrument.symbol, start, end)
                if provider.metadata.supports_actions
                else []
            )
            knowledge_time = require_utc(self.clock()) if executable_open else observed_at
            if executable_open and not self.calendar.is_executable_open_time(start, knowledge_time):
                raise DatasetInvalid(
                    "MISSED_EXECUTION_OPEN: provider response nebyla získána v XNYS open okně"
                )
            normalized = [
                self._normalize_open(
                    bar, instrument, provider.metadata.name, knowledge_time, ingestion_id
                )
                if executable_open
                else normalize_bar(
                    bar,
                    instrument,
                    provider.metadata.name,
                    observed_at,
                    ingestion_id,
                    self.calendar,
                )
                for bar in bars
            ]
            with self._sessions() as session, session.begin():
                _lock(session, f"ingestion:{scope}")
                existing = session.get(MarketDataIngestionRecord, ingestion_id)
                if session.get(InstrumentRecord, instrument.instrument_id) is None:
                    session.add(
                        InstrumentRecord(
                            instrument_id=instrument.instrument_id,
                            symbol=instrument.symbol,
                            exchange=instrument.exchange,
                            calendar=instrument.calendar,
                            currency=instrument.currency,
                            asset_type=instrument.asset_type.value,
                            active_from=_instant(instrument.active_from),
                            active_to=_instant(instrument.active_to)
                            if instrument.active_to
                            else None,
                            created_at=instrument.created_at,
                        )
                    )
                    session.flush()
                if existing is None:
                    raise RuntimeError("Ingestion audit record po provider volání chybí")
                added: list[Observation] = []
                for incoming in normalized:
                    versions = list(
                        session.scalars(
                            select(MarketObservationRecord)
                            .where(
                                MarketObservationRecord.instrument_id == incoming.instrument_id,
                                MarketObservationRecord.provider == incoming.provider,
                                MarketObservationRecord.session_date
                                == _instant(incoming.session_date),
                            )
                            .order_by(MarketObservationRecord.revision)
                            .with_for_update()
                        )
                    )
                    same_timeframe = [
                        item for item in versions if item.timeframe == incoming.timeframe
                    ]
                    if same_timeframe and same_timeframe[-1].source_hash == incoming.source_hash:
                        continue
                    item = replace(incoming, revision=len(versions) + 1)
                    session.add(
                        MarketObservationRecord(
                            observation_id=item.observation_id,
                            instrument_id=item.instrument_id,
                            ingestion_id=ingestion_id,
                            provider=item.provider,
                            timeframe=item.timeframe,
                            session_date=_instant(item.session_date),
                            timestamp=item.timestamp,
                            open=str(item.open),
                            high=str(item.high),
                            low=str(item.low),
                            close=str(item.close),
                            volume=str(item.volume),
                            observed_at=item.observed_at,
                            source_id=item.source_id,
                            source_hash=item.source_hash,
                            revision=item.revision,
                        )
                    )
                    added.append(item)
                for action in actions:
                    self._persist_action(session, provider.metadata.name, action)
                existing.status = "SUCCEEDED"
                existing.finished_at = datetime.now(UTC)
                existing.row_count = len(added)
                return IngestionResult(ingestion_id, start, end, "SUCCEEDED", tuple(added))
        except Exception as exc:
            with self._sessions() as session, session.begin():
                row = session.get(MarketDataIngestionRecord, ingestion_id)
                if row is not None:
                    row.finished_at = datetime.now(UTC)
                    row.status = "FAILED"
                    row.error_summary = str(exc)[:1000]
            return IngestionResult(ingestion_id, start, end, "FAILED", (), str(exc))

    def _normalize_open(
        self,
        bar: ProviderBar,
        instrument: Instrument,
        provider: str,
        observed_at: datetime,
        ingestion_id: str,
    ) -> Observation:
        if not self.calendar.is_session(bar.session_date):
            raise DatasetInvalid("Provider open neleží v platné XNYS session")
        if (
            not bar.open.is_finite()
            or bar.open <= 0
            or not bar.volume.is_finite()
            or bar.volume < 0
        ):
            raise DatasetInvalid("Provider vrátil neplatnou raw open cenu")
        timeframe = "open"
        source_id = f"{bar.source_id}:open"
        payload = "|".join(
            map(
                str,
                (
                    instrument.instrument_id,
                    provider,
                    timeframe,
                    bar.session_date,
                    bar.open,
                    source_id,
                ),
            )
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return Observation(
            digest,
            instrument.instrument_id,
            provider,
            timeframe,
            bar.session_date,
            self.calendar.session_open(bar.session_date),
            bar.open,
            bar.open,
            bar.open,
            bar.open,
            bar.volume,
            observed_at,
            source_id,
            digest,
            ingestion_id,
        )

    @staticmethod
    def _action_record(action: CorporateAction) -> CorporateActionRecord:
        return CorporateActionRecord(
            action_id=action.action_id,
            instrument_id=action.instrument_id,
            kind=action.kind.value,
            effective_at=action.effective_at,
            known_at=action.known_at,
            value=str(action.value) if action.value is not None else None,
            new_symbol=action.new_symbol,
        )

    @staticmethod
    def _action_payload_hash(action: CorporateAction) -> str:
        payload = {
            "instrument_id": action.instrument_id,
            "kind": action.kind.value,
            "effective_at": action.effective_at.isoformat(),
            "value": str(action.value) if action.value is not None else None,
            "new_symbol": action.new_symbol,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _persist_action(self, session: Session, provider: str, action: CorporateAction) -> None:
        provider_action_id = action.provider_action_id or action.action_id
        if len(provider_action_id) > 128:
            raise DatasetInvalid("Provider corporate-action ID překračuje persistentní limit")
        payload_hash = action.payload_hash or self._action_payload_hash(action)
        revision_id = hashlib.sha256(
            f"{provider}|{provider_action_id}|{payload_hash}".encode()
        ).hexdigest()
        revision = session.get(CorporateActionRevisionRecord, revision_id)
        values = (
            action.action_id,
            provider,
            provider_action_id,
            payload_hash,
            action.instrument_id,
            action.kind.value,
            action.effective_at,
            action.known_at,
            str(action.value) if action.value is not None else None,
            action.new_symbol,
        )
        if revision is None:
            session.add(
                CorporateActionRevisionRecord(
                    revision_id=revision_id,
                    action_id=action.action_id,
                    provider=provider,
                    provider_action_id=provider_action_id,
                    payload_hash=payload_hash,
                    instrument_id=action.instrument_id,
                    kind=action.kind.value,
                    effective_at=action.effective_at,
                    known_at=action.known_at,
                    value=str(action.value) if action.value is not None else None,
                    new_symbol=action.new_symbol,
                )
            )
        else:
            persisted = (
                revision.action_id,
                revision.provider,
                revision.provider_action_id,
                revision.payload_hash,
                revision.instrument_id,
                revision.kind,
                _database_utc(revision.effective_at),
                _database_utc(revision.known_at),
                revision.value,
                revision.new_symbol,
            )
            if persisted != values:
                raise DatasetInvalid("Corporate-action revision identity koliduje s jiným obsahem")

        current = session.get(CorporateActionRecord, action.action_id)
        if current is None:
            session.add(self._action_record(action))
            return
        current_known = _database_utc(current.known_at)
        if action.known_at < current_known:
            return
        current_values = (
            current.instrument_id,
            current.kind,
            _database_utc(current.effective_at),
            current.value,
            current.new_symbol,
        )
        new_values = (
            action.instrument_id,
            action.kind.value,
            action.effective_at,
            str(action.value) if action.value is not None else None,
            action.new_symbol,
        )
        if action.known_at == current_known:
            if current_values != new_values:
                raise DatasetInvalid("Corporate-action current version koliduje ve stejném known_at")
            return
        current.instrument_id = action.instrument_id
        current.kind = action.kind.value
        current.effective_at = action.effective_at
        current.known_at = action.known_at
        current.value = str(action.value) if action.value is not None else None
        current.new_symbol = action.new_symbol


class DatasetSnapshotService:
    def __init__(
        self, session_factory: Callable[[], Session], calendar: XNYSCalendar | None = None
    ) -> None:
        self._sessions = session_factory
        self.calendar = calendar or XNYSCalendar()

    def build(
        self,
        *,
        as_of: datetime,
        provider: str,
        universe_id: str,
        start: date,
        end: date,
        minimum_coverage: Decimal,
    ) -> DatasetSnapshot:
        cutoff = require_utc(as_of)
        logical = (
            f"{provider}|{self.calendar.identity}|{universe_id}|{start}|{end}|{cutoff.isoformat()}"
        )
        with self._sessions() as session, session.begin():
            _lock(session, f"snapshot:{logical}")
            universe = session.get(UniverseDefinitionRecord, universe_id)
            if universe is None:
                raise DatasetInvalid("Universe neexistuje")
            rows = self._authoritative_rows(session, provider, cutoff, start, end)
            memberships = tuple(
                session.scalars(
                    select(UniverseMembershipRecord).where(
                        UniverseMembershipRecord.universe_id == universe_id,
                        UniverseMembershipRecord.known_at <= cutoff,
                    )
                )
            )
            instruments = {
                row.instrument_id: row for row in session.scalars(select(InstrumentRecord))
            }
            expected = {
                (instrument_id, day)
                for day in self.calendar.sessions_between(start, end)
                for instrument_id, instrument in instruments.items()
                if instrument.active_from.date() <= day
                and (instrument.active_to is None or day < instrument.active_to.date())
                and any(
                    membership.instrument_id == instrument_id
                    and _database_utc(membership.known_at) <= self.calendar.session_close(day)
                    and membership.valid_from.date() <= day
                    and (membership.valid_to is None or day < membership.valid_to.date())
                    for membership in memberships
                )
            }
            selected = tuple(
                row for row in rows if (row.instrument_id, row.session_date.date()) in expected
            )
            present = {(row.instrument_id, row.session_date.date()) for row in selected}
            coverage = Decimal(len(present)) / Decimal(len(expected)) if expected else Decimal(1)
            canonical = [
                {"id": row.observation_id, "revision": row.revision, "hash": row.source_hash}
                for row in sorted(
                    selected, key=lambda item: (item.instrument_id, item.session_date)
                )
            ]
            selected_instruments = {row.instrument_id for row in selected}
            revision_rows = tuple(
                session.scalars(
                    select(CorporateActionRevisionRecord).where(
                        CorporateActionRevisionRecord.instrument_id.in_(selected_instruments),
                        CorporateActionRevisionRecord.provider == provider,
                        CorporateActionRevisionRecord.known_at <= cutoff,
                        CorporateActionRevisionRecord.effective_at
                        <= _instant(end + timedelta(days=1)),
                    )
                )
            )
            latest_revisions: dict[str, CorporateActionRevisionRecord] = {}
            for action in sorted(
                revision_rows,
                key=lambda item: (_database_utc(item.known_at), item.revision_id),
            ):
                latest_revisions[action.action_id] = action
            cancellations = tuple(
                session.scalars(
                    select(CorporateActionCancellationRecord).where(
                        CorporateActionCancellationRecord.action_id.in_(
                            set(latest_revisions) | selected_instruments
                        ),
                        CorporateActionCancellationRecord.known_at <= cutoff,
                    )
                )
            )
            latest_cancel: dict[str, datetime] = {}
            for cancellation in cancellations:
                when = _database_utc(cancellation.known_at)
                if when > latest_cancel.get(cancellation.action_id, datetime.min.replace(tzinfo=UTC)):
                    latest_cancel[cancellation.action_id] = when

            canonical_actions: list[dict[str, object]] = []
            for action in sorted(latest_revisions.values(), key=lambda item: item.action_id):
                known_at = _database_utc(action.known_at)
                if latest_cancel.get(action.action_id, datetime.min.replace(tzinfo=UTC)) >= known_at:
                    continue
                canonical_actions.append(
                    {
                        "action_id": action.action_id,
                        "instrument_id": action.instrument_id,
                        "kind": action.kind,
                        "effective_at": action.effective_at.isoformat(),
                        "known_at": action.known_at.isoformat(),
                        "value": action.value,
                        "new_symbol": action.new_symbol,
                    }
                )
            fallback_actions = tuple(
                session.scalars(
                    select(CorporateActionRecord).where(
                        CorporateActionRecord.instrument_id.in_(selected_instruments),
                        CorporateActionRecord.known_at <= cutoff,
                        CorporateActionRecord.effective_at <= _instant(end + timedelta(days=1)),
                    )
                )
            )
            for action in sorted(fallback_actions, key=lambda item: item.action_id):
                if action.action_id in latest_revisions:
                    continue
                known_at = _database_utc(action.known_at)
                if latest_cancel.get(action.action_id, datetime.min.replace(tzinfo=UTC)) >= known_at:
                    continue
                canonical_actions.append(
                    {
                        "action_id": action.action_id,
                        "instrument_id": action.instrument_id,
                        "kind": action.kind,
                        "effective_at": action.effective_at.isoformat(),
                        "known_at": action.known_at.isoformat(),
                        "value": action.value,
                        "new_symbol": action.new_symbol,
                    }
                )
            canonical_actions.sort(key=lambda item: str(item["action_id"]))
            immutable_content = {"observations": canonical, "corporate_actions": canonical_actions}
            content_hash = hashlib.sha256(
                json.dumps(immutable_content, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            snapshot_id = hashlib.sha256(f"{logical}|{content_hash}".encode()).hexdigest()
            status = "VALID" if expected and coverage >= minimum_coverage else "INVALID"
            manifest = json.dumps(
                {
                    "schema_version": "4",
                    "logical_identity": logical,
                    "observations": canonical,
                    "corporate_actions": canonical_actions,
                    "expected_count": len(expected),
                    "present_count": len(present),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            record = session.get(DatasetSnapshotRecord, snapshot_id)
            if record is None:
                record = DatasetSnapshotRecord(
                    snapshot_id=snapshot_id,
                    created_at=datetime.now(UTC),
                    as_of=cutoff,
                    provider=provider,
                    calendar_identity=self.calendar.identity,
                    universe_id=universe_id,
                    start_at=_instant(start),
                    end_at=_instant(end),
                    timeframe="1d",
                    content_hash=content_hash,
                    status=status,
                    coverage=str(coverage),
                    manifest_json=manifest,
                )
                session.add(record)
            return DatasetSnapshot(
                snapshot_id,
                record.created_at,
                cutoff,
                provider,
                self.calendar.identity,
                universe_id,
                start,
                end,
                "1d",
                content_hash,
                tuple(row.observation_id for row in selected),
                status,
                coverage,
            )

    @staticmethod
    def _authoritative_rows(
        session: Session, provider: str, cutoff: datetime, start: date, end: date
    ) -> Sequence[MarketObservationRecord]:
        ranked = (
            select(
                MarketObservationRecord.id.label("id"),
                func.row_number()
                .over(
                    partition_by=(
                        MarketObservationRecord.instrument_id,
                        MarketObservationRecord.provider,
                        MarketObservationRecord.session_date,
                    ),
                    order_by=(
                        MarketObservationRecord.observed_at.desc(),
                        MarketObservationRecord.revision.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                MarketObservationRecord.provider == provider,
                MarketObservationRecord.observed_at <= cutoff,
                MarketObservationRecord.timestamp <= cutoff,
                MarketObservationRecord.session_date >= _instant(start),
                MarketObservationRecord.session_date <= _instant(end),
            )
            .subquery()
        )
        statement: Select[tuple[MarketObservationRecord]] = select(MarketObservationRecord).join(
            ranked, and_(MarketObservationRecord.id == ranked.c.id, ranked.c.rank == 1)
        )
        return tuple(session.scalars(statement))
