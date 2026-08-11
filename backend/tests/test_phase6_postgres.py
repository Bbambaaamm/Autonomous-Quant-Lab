import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantlab.persistence import (
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)

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
