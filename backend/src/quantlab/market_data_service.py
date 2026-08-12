from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import Select, and_, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import (
    CorporateAction,
    DatasetInvalid,
    DatasetSnapshot,
    IngestionResult,
    Instrument,
    MarketDataProvider,
    Observation,
    ProviderError,
    XNYSCalendar,
    normalize_bar,
)
from quantlab.persistence import (
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
        row.timestamp,
        Decimal(row.open),
        Decimal(row.high),
        Decimal(row.low),
        Decimal(row.close),
        Decimal(row.volume),
        row.observed_at,
        row.source_id,
        row.source_hash,
        row.ingestion_id,
        row.revision,
    )


class PersistentMarketDataService:
    """Transakční produkční ingestion; provider je jediná část mimo DB transakci."""

    def __init__(
        self, session_factory: Callable[[], Session], calendar: XNYSCalendar | None = None
    ) -> None:
        self._sessions = session_factory
        self.calendar = calendar or XNYSCalendar()

    def ingest(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        start: date,
        end: date,
        observed_at: datetime,
    ) -> IngestionResult:
        observed_at = require_utc(observed_at)
        scope = hashlib.sha256(
            f"{provider.metadata.name}|{instrument.instrument_id}|{start}|{end}|{observed_at.isoformat()}".encode()
        ).hexdigest()
        ingestion_id = scope
        try:
            bars = provider.historical_daily(instrument.symbol, start, end)
            actions = provider.corporate_actions(instrument.symbol, start, end)
            normalized = [
                normalize_bar(
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
                if existing is not None and existing.status == "SUCCEEDED":
                    rows = session.scalars(
                        select(MarketObservationRecord).where(
                            MarketObservationRecord.ingestion_id == ingestion_id
                        )
                    )
                    return IngestionResult(
                        ingestion_id, start, end, "SUCCEEDED", tuple(map(_observation, rows))
                    )
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
                    existing = MarketDataIngestionRecord(
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
                    session.add(existing)
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
                    if versions and versions[-1].source_hash == incoming.source_hash:
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
                    if session.get(CorporateActionRecord, action.action_id) is None:
                        session.add(self._action_record(action))
                existing.status = "SUCCEEDED"
                existing.finished_at = datetime.now(UTC)
                existing.row_count = len(added)
                return IngestionResult(ingestion_id, start, end, "SUCCEEDED", tuple(added))
        except (ProviderError, ValueError, IntegrityError) as exc:
            with self._sessions() as session, session.begin():
                row = session.get(MarketDataIngestionRecord, ingestion_id)
                if row is None:
                    session.add(
                        MarketDataIngestionRecord(
                            id=ingestion_id,
                            provider=provider.metadata.name,
                            scope_hash=scope,
                            started_at=observed_at,
                            finished_at=datetime.now(UTC),
                            status="FAILED",
                            requested_start=_instant(start),
                            requested_end=_instant(end),
                            instrument_count=1,
                            row_count=0,
                            error_summary=str(exc)[:1000],
                        )
                    )
            return IngestionResult(ingestion_id, start, end, "FAILED", (), str(exc))

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
            content_hash = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            snapshot_id = hashlib.sha256(f"{logical}|{content_hash}".encode()).hexdigest()
            status = "VALID" if expected and coverage >= minimum_coverage else "INVALID"
            manifest = json.dumps(
                {
                    "schema_version": "2",
                    "logical_identity": logical,
                    "observations": canonical,
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
                tuple(item["id"] for item in canonical),
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
                MarketObservationRecord.session_date >= _instant(start),
                MarketObservationRecord.session_date <= _instant(end),
            )
            .subquery()
        )
        statement: Select[tuple[MarketObservationRecord]] = select(MarketObservationRecord).join(
            ranked, and_(MarketObservationRecord.id == ranked.c.id, ranked.c.rank == 1)
        )
        return tuple(session.scalars(statement))
