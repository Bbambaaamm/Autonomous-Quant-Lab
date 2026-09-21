"""Resumable US data acquisition. Screening is evidence, never trading approval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.asset_directory import VENUES, AssetDirectoryEntry, AssetDirectorySnapshot
from quantlab.control_plane import ControlPlaneRegistryService
from quantlab.domain import require_utc
from quantlab.market_data import (
    AssetType,
    CorporateAction,
    DatasetInvalid,
    Instrument,
    MarketDataProvider,
    ProviderBar,
    XNYSCalendar,
)
from quantlab.market_data_service import PersistentMarketDataService, _lock
from quantlab.persistence import Base, InstrumentRecord, MarketObservationRecord


class ReceivedData:
    def __init__(self, provider: MarketDataProvider, symbol: str, start: date, end: date) -> None:
        self.metadata = provider.metadata
        self.provider = provider
        self.bars = provider.historical_daily(symbol, start, end)
        self.actions = (
            provider.corporate_actions(symbol, start, end) if self.metadata.supports_actions else []
        )

    def resolve(self, symbol: str) -> dict[str, str]:
        return self.provider.resolve(symbol)

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        return self.bars

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        return self.actions


class MarketBatch(Base):
    __tablename__ = "market_batches"
    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(1000))


class MarketTask(Base):
    __tablename__ = "market_tasks"
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("market_batches.batch_id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[str] = mapped_column(String(36))
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    exchange: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(String(200))
    bars: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    momentum: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    trend: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    mean_reversion: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    evidence_json: Mapped[str | None] = mapped_column(Text)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else require_utc(value)


class MarketPipeline:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def create(
        self,
        snapshot_id: str,
        start: date,
        end: date,
        provider: str,
        actor: str,
        reason: str,
        now: datetime,
    ) -> str:
        now = require_utc(now)
        if (
            start > end
            or (end - start).days > 730
            or end > XNYSCalendar().latest_completed_session(now)
        ):
            raise ValueError("Rozsah musí být uzavřený, nejvýše dva roky a bez budoucích seancí")
        if not provider.startswith("alpaca:"):
            raise ValueError("Široký sběr vyžaduje explicitní Alpaca feed")
        if not actor or not 3 <= len(reason.strip()) <= 1000:
            raise ValueError("Chybí identita nebo důvod")
        identity = hashlib.sha256(f"{snapshot_id}|{start}|{end}|{provider}".encode()).hexdigest()
        with self.sessions() as session, session.begin():
            _lock(session, f"market-batch:{identity}")
            snapshot = session.get(AssetDirectorySnapshot, snapshot_id)
            if snapshot is None or _utc(snapshot.received_at) > now:
                raise ValueError("Adresář identit není dostupný k tomuto času")
            if now - _utc(snapshot.received_at) > timedelta(days=3):
                raise ValueError("Nejdříve aktualizujte adresář identit")
            if session.get(MarketBatch, identity):
                return identity
            rows = list(
                session.scalars(
                    select(AssetDirectoryEntry).where(
                        AssetDirectoryEntry.snapshot_id == snapshot_id
                    )
                )
            )
            if not rows:
                raise ValueError("Adresář neobsahuje instrumenty")
            session.add(
                MarketBatch(
                    batch_id=identity,
                    snapshot_id=snapshot_id,
                    created_at=now,
                    start=start,
                    end=end,
                    provider=provider,
                    actor=actor,
                    reason=reason,
                )
            )
            session.flush()
            for row in rows:
                supported = row.exchange in VENUES
                session.add(
                    MarketTask(
                        task_id=hashlib.sha256(f"{identity}|{row.asset_id}".encode()).hexdigest(),
                        batch_id=identity,
                        asset_id=row.asset_id,
                        instrument_id="alpaca-" + row.asset_id.replace("-", ""),
                        symbol=row.symbol,
                        exchange=VENUES.get(row.exchange, row.exchange),
                        state="PENDING" if supported else "UNSUPPORTED_VENUE",
                        attempts=0,
                        retry_at=now,
                        bars=0,
                        detail=None if supported else "Burza nemá ověřený kalendář",
                    )
                )
        return identity

    def step(
        self,
        provider_factory: Callable[[Instrument], MarketDataProvider],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> dict[str, str | None]:
        now = require_utc(clock())
        token = str(uuid4())
        with self.sessions() as session, session.begin():
            session.execute(
                update(MarketTask)
                .where(
                    MarketTask.state == "RUNNING",
                    MarketTask.lease_until <= now,
                    MarketTask.attempts >= 3,
                )
                .values(
                    state="FAILED", detail="Vyčerpány pokusy po přerušení procesu", lease_token=None
                )
            )
            task = session.scalar(
                select(MarketTask)
                .where(
                    MarketTask.attempts < 3,
                    MarketTask.retry_at <= now,
                    or_(
                        MarketTask.state.in_(("PENDING", "RETRY")),
                        (MarketTask.state == "RUNNING") & (MarketTask.lease_until <= now),
                    ),
                )
                .order_by(MarketTask.retry_at, MarketTask.task_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return {"outcome": self.refresh(now), "trading_cycle_id": None}
            task.state = "RUNNING"
            task.attempts += 1
            task.lease_until = now + timedelta(minutes=10)
            task.lease_token = token
            batch = session.get(MarketBatch, task.batch_id)
            if batch is None:
                raise RuntimeError("Chybí dávka")
            snapshot = session.get(AssetDirectorySnapshot, batch.snapshot_id)
            if snapshot is None:
                raise RuntimeError("Chybí adresář identit")
            existing = session.get(InstrumentRecord, task.instrument_id)
            first_day = (
                existing.active_from.date() if existing else _utc(snapshot.received_at).date()
            )
            instrument = Instrument(
                task.instrument_id,
                task.symbol,
                task.exchange,
                "XNYS",
                "USD",
                AssetType.EQUITY,
                first_day,
                created_at=_utc(existing.created_at) if existing else _utc(snapshot.received_at),
            )
            task_id, start, end, expected_provider = (
                task.task_id,
                batch.start,
                batch.end,
                batch.provider,
            )
        state, detail, evidence = "RETRY", "Sběr se nezdařil", None
        try:
            try:
                ControlPlaneRegistryService(self.sessions).register_instrument(instrument)
            except DatasetInvalid as exc:
                raise ValueError("Konflikt kanonické identity") from exc
            provider = provider_factory(instrument)
            if provider.metadata.persistent_name != expected_provider:
                raise ValueError("Provider se liší od neměnné definice dávky")
            fetch_start = self.incremental_start(
                instrument.instrument_id, expected_provider, start, end, now
            )
            received = ReceivedData(provider, instrument.symbol, fetch_start, end)
            outcome = PersistentMarketDataService(self.sessions).ingest(
                received, instrument, fetch_start, end, require_utc(clock())
            )
            if outcome.status != "SUCCEEDED":
                detail = "Poskytovatel nevrátil platná data; detail je v evidenci importu"
            else:
                evidence = self.screen(
                    instrument.instrument_id, expected_provider, start, end, require_utc(clock())
                )
                state, detail = "DONE", str(evidence["reason"])
        except DatasetInvalid as exc:
            state = "DATA_BLOCKED"
            detail = (
                "Chybí historická evidence přijetí dividend nebo splitů"
                if str(exc) == "CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"
                else "Zdroj neposkytl požadovanou validní evidenci tržních dat"
            )
        except ValueError:
            state, detail = (
                "BLOCKED",
                "Konflikt identity, nepodporovaný provider nebo neplatná konfigurace",
            )
        except Exception as exc:
            # Raw exception/provider body may contain credentials; do not propagate it to UI.
            state, detail = (
                ("ACCESS_BLOCKED", "Datový účet nemá platný přístup")
                if str(exc) == "MARKET_DATA_ACCESS_DENIED"
                else ("RETRY", "Zdroj nebo databáze nejsou dostupné")
            )
        with self.sessions() as session, session.begin():
            task = session.scalar(
                select(MarketTask).where(MarketTask.task_id == task_id).with_for_update()
            )
            if task is None or task.lease_token != token:
                return {"outcome": "MARKET_DATA_LEASE_LOST", "trading_cycle_id": None}
            task.state = "FAILED" if state == "RETRY" and task.attempts >= 3 else state
            task.retry_at = require_utc(clock()) + timedelta(minutes=task.attempts * 5)
            task.lease_until = None
            task.lease_token = None
            task.detail = detail
            if state == "ACCESS_BLOCKED":
                batches = select(MarketBatch.batch_id).where(
                    MarketBatch.provider == expected_provider
                )
                session.execute(
                    update(MarketTask)
                    .where(
                        MarketTask.batch_id.in_(batches), MarketTask.state.in_(("PENDING", "RETRY"))
                    )
                    .values(state="ACCESS_BLOCKED", detail=detail)
                )
            if evidence is not None:
                task.bars = evidence["bars"]
                task.coverage = Decimal(evidence["coverage"])
                task.momentum = evidence["momentum"]
                task.trend = evidence["trend"]
                task.mean_reversion = evidence["mean_reversion"]
                task.evidence_json = json.dumps(evidence, default=str, sort_keys=True)
        return {"outcome": "MARKET_DATA_TASK_PROCESSED", "trading_cycle_id": None}

    def incremental_start(
        self, instrument_id: str, provider: str, start: date, end: date, as_of: datetime
    ) -> date:
        """Refetch from first missing session, keeping five sessions of revision overlap."""
        expected = XNYSCalendar().sessions_between(start, end)
        if not expected:
            return start
        with self.sessions() as session:
            days = {
                row.date()
                for row in session.scalars(
                    select(MarketObservationRecord.session_date)
                    .where(
                        MarketObservationRecord.instrument_id == instrument_id,
                        MarketObservationRecord.provider == provider,
                        MarketObservationRecord.timeframe == "1d",
                        MarketObservationRecord.observed_at <= require_utc(as_of),
                        MarketObservationRecord.timestamp <= as_of,
                        MarketObservationRecord.session_date
                        >= datetime.combine(start, datetime.min.time(), UTC),
                        MarketObservationRecord.session_date
                        <= datetime.combine(end, datetime.min.time(), UTC),
                    )
                    .distinct()
                )
            }
        first_missing = next(
            (i for i, day in enumerate(expected) if day not in days), len(expected)
        )
        return expected[max(0, first_missing - 5)]

    def screen(
        self, instrument_id: str, provider: str, start: date, end: date, as_of: datetime
    ) -> dict[str, Any]:
        """Current technical diagnostics on raw closes, not research eligibility/alpha."""
        cutoff = require_utc(as_of)
        expected = XNYSCalendar().sessions_between(start, end)
        with self.sessions() as session:
            rows = session.scalars(
                select(MarketObservationRecord)
                .where(
                    MarketObservationRecord.instrument_id == instrument_id,
                    MarketObservationRecord.provider == provider,
                    MarketObservationRecord.timeframe == "1d",
                    MarketObservationRecord.session_date
                    >= datetime.combine(start, datetime.min.time(), UTC),
                    MarketObservationRecord.session_date
                    <= datetime.combine(end, datetime.min.time(), UTC),
                    MarketObservationRecord.observed_at <= cutoff,
                    MarketObservationRecord.timestamp <= cutoff,
                )
                .order_by(
                    MarketObservationRecord.session_date,
                    MarketObservationRecord.observed_at,
                    MarketObservationRecord.revision,
                )
            )
            by_day = {row.session_date.date(): row for row in rows}
            ordered = [by_day[day] for day in expected if day in by_day]
            closes = [Decimal(row.close) for row in ordered]
            complete_recent = bool(expected) and all(day in by_day for day in expected[-127:])
            sufficient = len(closes) >= 127 and complete_recent
            return {
                "instrument_id": instrument_id,
                "provider": provider,
                "as_of": cutoff,
                "bars": len(ordered),
                "expected_sessions": len(expected),
                "coverage": str(Decimal(len(ordered)) / Decimal(len(expected)))
                if expected
                else "0",
                "momentum": closes[-1] / closes[-127] - 1 if sufficient else None,
                "trend": (sum(closes[-20:], Decimal(0)) / 20)
                / (sum(closes[-100:], Decimal(0)) / 100)
                - 1
                if sufficient
                else None,
                "mean_reversion": 1 - closes[-1] / (sum(closes[-20:], Decimal(0)) / 20)
                if sufficient
                else None,
                "reason": "RAW_PRICE_DIAGNOSTIC_ONLY"
                if sufficient
                else "INSUFFICIENT_HISTORY_OR_GAPS",
                "eligible_for_promotion": False,
                "limitations": [
                    "Raw prices: corporate actions can distort rankings",
                    "Current constituent sample; not historical market membership",
                    "Feed volume is not necessarily whole-market volume",
                ],
                "observation_ids": [row.observation_id for row in ordered],
            }

    def refresh(self, now: datetime) -> str:
        """Advance a finished universe to the next completed session; never grow backlog."""
        with self.sessions() as session:
            active = session.scalar(
                select(func.count())
                .select_from(MarketTask)
                .where(MarketTask.state.in_(("PENDING", "RETRY", "RUNNING", "ACCESS_BLOCKED")))
            )
            latest = session.scalar(
                select(MarketBatch).order_by(MarketBatch.created_at.desc()).limit(1)
            )
            snapshot = session.scalar(
                select(AssetDirectorySnapshot)
                .where(AssetDirectorySnapshot.received_at <= now)
                .order_by(AssetDirectorySnapshot.received_at.desc())
                .limit(1)
            )
            end = XNYSCalendar().latest_completed_session(now)
            if active or latest is None or snapshot is None or latest.end >= end:
                return "NO_PENDING_MARKET_DATA"
            if now - _utc(snapshot.received_at) > timedelta(days=3):
                return "MARKET_IDENTITIES_STALE"
            snapshot_id, provider = snapshot.snapshot_id, latest.provider
            start = max(latest.start, end - timedelta(days=500))
        self.create(
            snapshot_id,
            start,
            end,
            provider,
            "scheduled-worker",
            "Průběžná aktualizace uzavřených seancí",
            now,
        )
        return "MARKET_BATCH_CREATED"

    def read(
        self, limit: int = 50, offset: int = 0, query: str = "", rank: str = "symbol"
    ) -> dict[str, Any]:
        if (
            not 1 <= limit <= 200
            or offset < 0
            or len(query) > 100
            or rank not in {"symbol", "momentum", "trend", "mean_reversion"}
        ):
            raise ValueError("Neplatný filtr přehledu")
        with self.sessions() as session:
            batch = session.scalar(
                select(MarketBatch).order_by(MarketBatch.created_at.desc()).limit(1)
            )
            if batch is None:
                return {
                    "batch": None,
                    "items": [],
                    "counts": {},
                    "total": 0,
                    "matched": 0,
                    "query": query,
                    "rank": rank,
                    "limit": limit,
                    "offset": offset,
                }
            counts = {
                state: count
                for state, count in session.execute(
                    select(MarketTask.state, func.count())
                    .where(MarketTask.batch_id == batch.batch_id)
                    .group_by(MarketTask.state)
                )
            }
            filters = [MarketTask.batch_id == batch.batch_id]
            if query.strip():
                filters.append(MarketTask.symbol.icontains(query.strip(), autoescape=True))
            ordering = (
                MarketTask.symbol
                if rank == "symbol"
                else getattr(MarketTask, rank).desc().nulls_last()
            )
            matched = session.scalar(select(func.count()).select_from(MarketTask).where(*filters))
            rows = session.scalars(
                select(MarketTask)
                .where(*filters)
                .order_by(ordering, MarketTask.symbol)
                .offset(offset)
                .limit(limit)
            )
            return {
                "batch": {
                    "id": batch.batch_id,
                    "start": batch.start,
                    "end": batch.end,
                    "provider": batch.provider,
                },
                "counts": counts,
                "total": sum(counts.values()),
                "matched": matched,
                "query": query,
                "rank": rank,
                "offset": offset,
                "limit": limit,
                "items": [
                    {
                        "symbol": row.symbol,
                        "state": row.state,
                        "bars": row.bars,
                        "coverage": row.coverage,
                        "momentum": row.momentum,
                        "trend": row.trend,
                        "mean_reversion": row.mean_reversion,
                        "detail": row.detail,
                    }
                    for row in rows
                ],
            }
