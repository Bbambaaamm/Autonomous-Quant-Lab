"""Resumable US data acquisition. Screening is evidence, never trading approval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
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
    case,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.asset_directory import VENUES, AssetDirectoryEntry, AssetDirectorySnapshot
from quantlab.control_plane import ControlPlaneRegistryService
from quantlab.current_actions import NORMALIZATION_VERSION
from quantlab.domain import require_utc
from quantlab.market_data import (
    AlpacaProvider,
    AssetType,
    CorporateAction,
    DatasetInvalid,
    Instrument,
    MarketDataProvider,
    ProviderBar,
    XNYSCalendar,
)
from quantlab.market_data_service import PersistentMarketDataService, _lock, _observation
from quantlab.market_diagnostics import blockage_detail
from quantlab.market_screening import MarketScreening, canonical, evaluate_screen, identity
from quantlab.persistence import Base, InstrumentRecord, MarketObservationRecord


class ReceivedData:
    def __init__(
        self,
        provider: MarketDataProvider,
        symbol: str,
        start: date,
        end: date,
        action_start: date | None = None,
        *,
        raw_only: bool = False,
    ) -> None:
        self.metadata = (
            replace(provider.metadata, supports_actions=False) if raw_only else provider.metadata
        )
        self.provider = provider
        self.bars = provider.historical_daily(symbol, start, end)
        self.actions = (
            provider.corporate_actions(symbol, action_start or start, end)
            if self.metadata.supports_actions and self.bars
            else []
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


class MarketActionReceipt(Base):
    """Immutable current REST facts, never historical research readiness or SSE events."""

    __tablename__ = "market_action_receipts"
    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("market_tasks.task_id", ondelete="RESTRICT"), index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)


class MarketActionReview(Base):
    """Append-only evidence for current-receipt re-evaluation; not research readiness."""

    __tablename__ = "market_action_reviews"
    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("market_tasks.task_id", ondelete="RESTRICT"), index=True
    )
    receipt_id: Mapped[str] = mapped_column(
        ForeignKey("market_action_receipts.receipt_id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)


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
                        AssetDirectoryEntry.snapshot_id == snapshot_id,
                        AssetDirectoryEntry.status == "active",
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
                .order_by(
                    case(
                        (MarketTask.state == "RUNNING", 0),
                        (MarketTask.state == "RETRY", 1),
                        else_=2,
                    ),
                    MarketTask.retry_at,
                    MarketTask.task_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                # Release expired-lease updates before another service writes a snapshot.
                session.commit()
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
        stage = "registrace instrumentu"
        try:
            try:
                ControlPlaneRegistryService(self.sessions).register_instrument(instrument)
            except DatasetInvalid as exc:
                raise ValueError("Konflikt kanonické identity") from exc
            stage = "příprava poskytovatele"
            provider = provider_factory(instrument)
            if provider.metadata.persistent_name != expected_provider:
                raise ValueError("Provider se liší od neměnné definice dávky")
            fetch_start = self.incremental_start(
                instrument.instrument_id, expected_provider, start, end, now
            )
            stage = "stažení cen"
            received = ReceivedData(
                provider,
                instrument.symbol,
                fetch_start,
                end,
                action_start=start,
                raw_only=isinstance(provider, AlpacaProvider),
            )
            price_received_at = require_utc(clock())
            stage = "uložení cen"
            outcome = PersistentMarketDataService(self.sessions).ingest(
                received, instrument, fetch_start, end, price_received_at
            )
            if outcome.status != "SUCCEEDED":
                detail = "Poskytovatel nevrátil platná data; detail je v evidenci importu"
            else:
                # Persist price diagnostics even if action acquisition/validation later fails.
                evidence = self.screen(
                    instrument.instrument_id, expected_provider, start, end, require_utc(clock())
                )
                if not received.bars:
                    no_prices = evidence["bars"] == 0
                    state = "NO_PRICE_DATA" if no_prices else "RETRY"
                    if no_prices:
                        evidence["screening"]["reasons"].insert(0, "NO_PRICE_DATA")
                    detail = (
                        "Zdroj nevrátil ceny pro požadované období a feed"
                        if no_prices
                        else "Zdroj nevrátil požadovaný přírůstek; dříve uložené ceny zůstávají"
                    )
                    evidence.update(
                        momentum=None,
                        trend=None,
                        mean_reversion=None,
                        reason="NO_PRICE_DATA" if no_prices else "EMPTY_INCREMENTAL_RESPONSE",
                        price_receipt={
                            "ingestion_id": outcome.ingestion_id,
                            "provider": expected_provider,
                            "requested_start": fetch_start,
                            "requested_end": end,
                            "received_at": price_received_at,
                            "returned_bars": 0,
                        },
                    )
                else:
                    stage = "ověření corporate actions a screening"
                    inventory_id = None
                    inventory_received_at = None
                    if isinstance(provider, AlpacaProvider):
                        rows = provider.current_action_inventory(instrument.symbol)
                        inventory_received_at = require_utc(clock())
                        payload = {
                            "source": "alpaca_rest_current_inventory",
                            "normalization_version": NORMALIZATION_VERSION,
                            "symbol": instrument.symbol,
                            "instrument_id": instrument.instrument_id,
                            "request_start": "1970-01-01",
                            "request_end": "9999-12-31",
                            "data_quality": "all",
                            "rows": rows,
                        }
                        digest = identity(payload)
                        inventory_id = identity(
                            {
                                "task": task_id,
                                "received_at": inventory_received_at,
                                "content": digest,
                            }
                        )
                        with self.sessions() as session, session.begin():
                            _lock(session, f"market-actions:{inventory_id}")
                            if session.get(MarketActionReceipt, inventory_id) is None:
                                session.add(
                                    MarketActionReceipt(
                                        receipt_id=inventory_id,
                                        task_id=task_id,
                                        received_at=inventory_received_at,
                                        content_hash=digest,
                                        payload_json=canonical(payload),
                                    )
                                )
                        evidence["current_action_receipt_id"] = inventory_id
                        actions = provider.normalize_current_actions(
                            instrument.symbol, rows, start, end, inventory_received_at
                        )
                        readiness = None
                    else:
                        actions = received.actions
                        readiness = PersistentMarketDataService(
                            self.sessions, clock=clock
                        ).verify_corporate_action_readiness(
                            received, instrument, start, end, require_utc(clock())
                        )
                    cutoff = require_utc(clock())
                    evidence = self.screen(
                        instrument.instrument_id,
                        expected_provider,
                        start,
                        end,
                        cutoff,
                        actions=actions,
                        readiness_id=readiness,
                        inventory_id=inventory_id,
                        inventory_received_at=inventory_received_at,
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
            safe_reason = {
                "MARKET_REQUEST_BUDGET_EXHAUSTED": "Vyčerpán limit požadavků nebo 45 sekund",
                "Dočasná chyba Alpaca provideru": "Poskytovatel vrátil HTTP 5xx",
            }.get(str(exc), "Dočasná chyba")
            state, detail = (
                ("ACCESS_BLOCKED", "Datový účet nemá platný přístup")
                if str(exc) == "MARKET_DATA_ACCESS_DENIED"
                else (
                    "RETRY",
                    f"{safe_reason}: {stage} "
                    f"({type(exc).__name__[:40]}; {type(exc.__cause__).__name__[:40]})",
                )
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
        self,
        instrument_id: str,
        provider: str,
        start: date,
        end: date,
        as_of: datetime,
        *,
        actions: list[CorporateAction] | None = None,
        readiness_id: str | None = None,
        inventory_id: str | None = None,
        inventory_received_at: datetime | None = None,
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
                "screening": evaluate_screen(
                    [_observation(row) for row in ordered],
                    actions or [],
                    expected,
                    cutoff,
                    readiness_id,
                    inventory_id=inventory_id,
                    inventory_received_at=inventory_received_at,
                ),
            }

    def refresh(self, now: datetime) -> str:
        """Advance a finished universe to the next completed session; never grow backlog."""
        with self.sessions() as session:
            active = session.scalar(
                select(func.count())
                .select_from(MarketTask)
                .where(MarketTask.state.in_(("PENDING", "RETRY", "RUNNING")))
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
            if active or latest is None:
                return "NO_PENDING_MARKET_DATA"
            blocked = session.scalar(
                select(func.count())
                .select_from(MarketTask)
                .where(MarketTask.batch_id == latest.batch_id, MarketTask.state == "ACCESS_BLOCKED")
            )
            latest_id = latest.batch_id
            end = XNYSCalendar().latest_completed_session(now)
            status = "MARKET_BATCH_CREATED"
            if blocked or snapshot is None or latest.end >= end:
                status = "NO_PENDING_MARKET_DATA"
            elif now - _utc(snapshot.received_at) > timedelta(days=3):
                status = "MARKET_IDENTITIES_STALE"
            snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
            provider = latest.provider
            start = max(latest.start, end - timedelta(days=500))
        MarketScreening(self.sessions).finalize(latest_id, now)
        if status != "MARKET_BATCH_CREATED":
            return status
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
        self,
        limit: int = 50,
        offset: int = 0,
        query: str = "",
        rank: str = "symbol",
        state: str = "",
    ) -> dict[str, Any]:
        if (
            not 1 <= limit <= 200
            or offset < 0
            or len(query) > 100
            or rank not in {"symbol", "momentum", "trend", "mean_reversion"}
            or state
            not in {
                "",
                "PENDING",
                "RUNNING",
                "RETRY",
                "DONE",
                "FAILED",
                "BLOCKED",
                "DATA_BLOCKED",
                "NO_PRICE_DATA",
                "ACCESS_BLOCKED",
                "UNSUPPORTED_VENUE",
            }
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
                    "state": state,
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
            downloaded, complete = session.execute(
                select(
                    func.sum(case((MarketTask.bars > 0, 1), else_=0)),
                    func.sum(case((MarketTask.coverage == 1, 1), else_=0)),
                ).where(MarketTask.batch_id == batch.batch_id)
            ).one()
            filters = [MarketTask.batch_id == batch.batch_id]
            if state:
                filters.append(MarketTask.state == state)
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
                "coverage_summary": {
                    "downloaded": int(downloaded or 0),
                    "complete_period": int(complete or 0),
                    "period_end": batch.end,
                },
                "total": sum(counts.values()),
                "matched": matched,
                "query": query,
                "rank": rank,
                "state": state,
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
                        "detail": blockage_detail(session, row, batch),
                    }
                    for row in rows
                ],
            }
