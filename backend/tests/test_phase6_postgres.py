import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from quantlab.market_data import (
    AssetType,
    CorporateAction,
    Instrument,
    ProviderBar,
    ProviderMetadata,
    XNYSCalendar,
)
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.persistence import (
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase6_runtime import ValidatedCurrentDataAccessor

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL"
)


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


def seed(session):
    now = datetime(2024, 1, 1, tzinfo=UTC)
    if session.get(InstrumentRecord, "i") is None:
        session.add(
            InstrumentRecord(
                instrument_id="i",
                symbol="AAA",
                exchange="XNYS",
                calendar="XNYS",
                currency="USD",
                asset_type="EQUITY",
                active_from=now,
                created_at=now,
            )
        )
    if session.get(UniverseDefinitionRecord, "u") is None:
        session.add(
            UniverseDefinitionRecord(
                universe_id="u", name="pit-phase6", kind="POINT_IN_TIME_MEMBERSHIP", created_at=now
            )
        )
    if session.get(MarketDataIngestionRecord, "g") is None:
        session.add(
            MarketDataIngestionRecord(
                id="g",
                provider="fixture",
                scope_hash="x" * 64,
                started_at=now,
                finished_at=now,
                status="SUCCEEDED",
                requested_start=now,
                requested_end=now,
                instrument_count=1,
                row_count=1,
            )
        )
    session.commit()


def test_persistent_ingest_publishes_causal_raw_open(engine) -> None:
    session_day = date(2026, 1, 6)
    calendar = XNYSCalendar()
    opened_at = calendar.session_open(session_day)
    instrument = Instrument(
        f"open-{uuid4().hex[:20]}",
        f"O{uuid4().hex[:7]}",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2020, 1, 1),
    )

    class OpenProvider:
        def __init__(self, name: str = "open-fixture") -> None:
            self.metadata = ProviderMetadata(name, "1", False, False)

        def resolve(self, symbol: str) -> dict[str, str]:
            return {"symbol": symbol}

        def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
            return [
                ProviderBar(
                    session_day,
                    Decimal("101"),
                    Decimal("102"),
                    Decimal("100"),
                    Decimal("101.5"),
                    Decimal("1000"),
                    f"{symbol}:{session_day}",
                )
            ]

        def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
            return []

    factory = sessionmaker(engine, expire_on_commit=False)
    response_time = opened_at + timedelta(milliseconds=500)
    service = PersistentMarketDataService(factory, calendar, clock=lambda: response_time)
    observed_at = opened_at
    result = service.ingest_open(OpenProvider(), instrument, session_day, observed_at)

    assert result.status == "SUCCEEDED"
    observation = ValidatedCurrentDataAccessor(factory, calendar).for_execution_session(
        [instrument.instrument_id], session_day, response_time
    )[0]
    assert observation.timestamp == opened_at
    assert observation.observed_at == response_time
    assert observation.timeframe == "open"
    assert observation.open == Decimal("101")

    # Úspěšný scope je immutable a jeho idempotentní replay nesmí znovu volat provider.
    def unexpected_clock() -> datetime:
        raise AssertionError("Idempotentní replay nesmí znovu číst provider response time")

    replay = PersistentMarketDataService(
        factory,
        calendar,
        clock=unexpected_clock,
    ).ingest_open(OpenProvider(), instrument, session_day, opened_at)
    assert replay.status == "SUCCEEDED"
    assert replay.ingestion_id == result.ingestion_id

    late_service = PersistentMarketDataService(
        factory, calendar, clock=lambda: opened_at + timedelta(minutes=5)
    )
    # Jiný scope simuluje nový provider request; nesmí se zaměnit za replay úspěšné ingestion.
    late = late_service.ingest_open(
        OpenProvider("late-open-fixture"), instrument, session_day, opened_at
    )
    assert late.status == "FAILED"
    assert late.error is not None and "MISSED_EXECUTION_OPEN" in late.error


def test_phase6_constraints_snapshot_and_pit_query(engine):
    now = datetime(2024, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        seed(session)
        membership = session.scalar(
            select(UniverseMembershipRecord).where(UniverseMembershipRecord.universe_id == "u")
        )
        if membership is None:
            session.add(
                UniverseMembershipRecord(
                    universe_id="u", instrument_id="i", valid_from=now, valid_to=None, known_at=now
                )
            )
        snap = session.get(DatasetSnapshotRecord, "s")
        if snap is None:
            session.add(
                DatasetSnapshotRecord(
                    snapshot_id="s",
                    created_at=now,
                    as_of=now,
                    provider="fixture",
                    calendar_identity="XNYS-v1",
                    universe_id="u",
                    start_at=now,
                    end_at=now,
                    timeframe="1d",
                    content_hash="h" * 64,
                    status="VALID",
                    coverage="1",
                    manifest_json="{}",
                )
            )
        session.commit()
        assert session.get(DatasetSnapshotRecord, "s").content_hash == "h" * 64


def _insert_observation(engine, observation_id):
    now = datetime(2024, 1, 2, tzinfo=UTC)
    try:
        with Session(engine) as session:
            seed(session)
            session.add(
                MarketObservationRecord(
                    observation_id=observation_id,
                    instrument_id="i",
                    ingestion_id="g",
                    provider="fixture",
                    timeframe="1d",
                    session_date=now,
                    timestamp=now,
                    open="1",
                    high="1",
                    low="1",
                    close="1",
                    volume="1",
                    observed_at=now,
                    source_id="x",
                    source_hash="z" * 64,
                    revision=1,
                )
            )
            session.commit()
        return True
    except IntegrityError:
        return False


def test_concurrent_observation_is_unique(engine):
    with Session(engine) as session:
        seed(session)
        session.query(MarketObservationRecord).filter(
            MarketObservationRecord.observation_id == "concurrent"
        ).delete()
        session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _insert_observation(engine, "concurrent"), range(2)))
    assert sorted(results) == [False, True]


class _BarrierProvider:
    metadata = ProviderMetadata("phase6-race-fixture", "1", False, False)

    def __init__(self, close: str, barrier: threading.Barrier | None = None) -> None:
        self.close = Decimal(close)
        self.barrier = barrier

    def resolve(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol}

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        return [
            ProviderBar(
                date(2024, 1, 2),
                self.close,
                self.close,
                self.close,
                self.close,
                Decimal("100"),
                f"{symbol}-{self.close}",
            )
        ]

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        return []


def test_production_ingestion_and_correction_are_exactly_once_under_race(engine) -> None:
    factory = sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    instrument = Instrument(
        f"race-{suffix}",
        f"R{suffix[:8]}",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2020, 1, 1),
    )

    def concurrent_ingest(close: str, observed_at: datetime):
        barrier = threading.Barrier(2)

        def worker(_worker: int):
            return PersistentMarketDataService(factory).ingest(
                _BarrierProvider(close, barrier),
                instrument,
                date(2024, 1, 2),
                date(2024, 1, 2),
                observed_at,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    first = concurrent_ingest("10", datetime(2024, 1, 2, 22, tzinfo=UTC))
    assert {result.status for result in first} == {"SUCCEEDED"}
    correction = concurrent_ingest("11", datetime(2024, 1, 3, 22, tzinfo=UTC))
    assert {result.status for result in correction} == {"SUCCEEDED"}
    with factory() as session:
        rows = tuple(
            session.scalars(
                select(MarketObservationRecord)
                .where(MarketObservationRecord.instrument_id == instrument.instrument_id)
                .order_by(MarketObservationRecord.revision)
            )
        )
    assert [row.revision for row in rows] == [1, 2]
    assert [row.close for row in rows] == ["10", "11"]


def test_production_snapshot_build_is_exactly_once_under_race(engine) -> None:
    factory = sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    instrument = Instrument(
        f"snapshot-{suffix}",
        f"S{suffix[:8]}",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2020, 1, 1),
    )
    PersistentMarketDataService(factory).ingest(
        _BarrierProvider("10"),
        instrument,
        date(2024, 1, 2),
        date(2024, 1, 2),
        datetime(2024, 1, 2, 22, tzinfo=UTC),
    )
    universe_id = f"universe-{suffix}"
    with factory() as session, session.begin():
        session.add(
            UniverseDefinitionRecord(
                universe_id=universe_id,
                name=universe_id,
                kind="POINT_IN_TIME_MEMBERSHIP",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            UniverseMembershipRecord(
                universe_id=universe_id,
                instrument_id=instrument.instrument_id,
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                valid_to=None,
                known_at=datetime(2024, 1, 1, tzinfo=UTC),
            )
        )
    barrier = threading.Barrier(2)

    def worker(_worker: int):
        barrier.wait(timeout=10)
        return DatasetSnapshotService(factory).build(
            as_of=datetime(2024, 1, 3, tzinfo=UTC),
            provider="phase6-race-fixture",
            universe_id=universe_id,
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            minimum_coverage=Decimal("1"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(worker, range(2)))
    assert len({item.snapshot_id for item in snapshots}) == 1
    assert len({item.content_hash for item in snapshots}) == 1
    with factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(DatasetSnapshotRecord)
            .where(DatasetSnapshotRecord.snapshot_id == snapshots[0].snapshot_id)
        )
    assert count == 1
