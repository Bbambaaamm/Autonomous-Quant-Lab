from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantlab.market_data import DatasetInvalid, XNYSCalendar
from quantlab.persistence import (
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
)
from quantlab.phase6_runtime import ValidatedCurrentDataAccessor

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)
CALENDAR = XNYSCalendar()


@pytest.fixture
def factory():
    return sessionmaker(create_engine(os.environ["DATABASE_URL"]), expire_on_commit=False)


def _observation(
    factory,
    session_day: date,
    *,
    status: str = "SUCCEEDED",
    revision: int = 1,
    instrument_id: str | None = None,
) -> str:
    suffix = uuid4().hex
    instrument_id = instrument_id or f"current-{suffix}"
    ingestion_id = f"ingestion-{suffix}"
    timestamp = CALENDAR.session_close(session_day)
    with factory() as session, session.begin():
        if session.get(InstrumentRecord, instrument_id) is None:
            session.add(
                InstrumentRecord(
                    instrument_id=instrument_id,
                    symbol=f"C{suffix[:7]}",
                    exchange="XNYS",
                    calendar="XNYS",
                    currency="USD",
                    asset_type="EQUITY",
                    active_from=datetime(2020, 1, 1, tzinfo=UTC),
                    active_to=None,
                    created_at=datetime.now(UTC),
                )
            )
        session.add(
            MarketDataIngestionRecord(
                id=ingestion_id,
                provider="current-fixture",
                scope_hash=suffix.ljust(64, "0"),
                started_at=timestamp,
                finished_at=timestamp if status != "STARTED" else None,
                status=status,
                requested_start=timestamp,
                requested_end=timestamp,
                instrument_count=1,
                row_count=1,
            )
        )
        session.add(
            MarketObservationRecord(
                observation_id=f"observation-{suffix}",
                instrument_id=instrument_id,
                ingestion_id=ingestion_id,
                provider="current-fixture",
                timeframe="1d",
                session_date=datetime.combine(session_day, datetime.min.time(), UTC),
                timestamp=timestamp,
                open="100",
                high="101",
                low="99",
                close="100",
                volume="1000",
                observed_at=timestamp.replace(microsecond=revision),
                source_id=suffix,
                source_hash=suffix.ljust(64, "0"),
                revision=revision,
            )
        )
    return instrument_id


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 1, 10, 12, tzinfo=UTC), date(2026, 1, 9)),  # sobota
        (datetime(2026, 1, 19, 20, tzinfo=UTC), date(2026, 1, 16)),  # MLK holiday
        (datetime(2026, 1, 6, 15, tzinfo=UTC), date(2026, 1, 5)),  # před close
        (datetime(2026, 1, 6, 22, tzinfo=UTC), date(2026, 1, 6)),  # po close
        (datetime(2026, 11, 27, 17, 59, tzinfo=UTC), date(2026, 11, 25)),  # early close před
        (datetime(2026, 11, 27, 18, 1, tzinfo=UTC), date(2026, 11, 27)),  # early close po
    ],
)
def test_current_data_uses_latest_completed_xnys_session(factory, now, expected) -> None:
    instrument_id = _observation(factory, expected)
    result = ValidatedCurrentDataAccessor(factory).latest([instrument_id], now)
    assert result[0].session_date == expected


def test_current_data_rejects_missing_expected_session(factory) -> None:
    with pytest.raises(DatasetInvalid):
        ValidatedCurrentDataAccessor(factory).latest(
            [f"missing-{uuid4().hex}"], datetime(2026, 1, 6, 22, tzinfo=UTC)
        )


def test_current_data_rejects_stale_older_session(factory) -> None:
    instrument_id = _observation(factory, date(2026, 1, 5))
    with pytest.raises(DatasetInvalid):
        ValidatedCurrentDataAccessor(factory).latest(
            [instrument_id], datetime(2026, 1, 6, 22, tzinfo=UTC)
        )


@pytest.mark.parametrize("status", ["STARTED", "FAILED"])
def test_current_data_rejects_latest_non_succeeded_revision(factory, status: str) -> None:
    instrument_id = _observation(factory, date(2026, 1, 6), status="SUCCEEDED", revision=1)
    _observation(factory, date(2026, 1, 6), status=status, revision=2, instrument_id=instrument_id)
    with pytest.raises(DatasetInvalid):
        ValidatedCurrentDataAccessor(factory).latest(
            [instrument_id], datetime(2026, 1, 6, 22, tzinfo=UTC)
        )


def test_current_data_accepts_latest_succeeded_revision(factory) -> None:
    instrument_id = _observation(factory, date(2026, 1, 6), revision=1)
    _observation(factory, date(2026, 1, 6), revision=2, instrument_id=instrument_id)
    result = ValidatedCurrentDataAccessor(factory).latest(
        [instrument_id], datetime(2026, 1, 6, 22, tzinfo=UTC)
    )
    assert result[0].revision == 2


def test_execution_data_requires_started_exact_session(factory) -> None:
    execution_session = date(2026, 1, 6)
    instrument_id = _observation(factory, execution_session)
    accessor = ValidatedCurrentDataAccessor(factory)

    with pytest.raises(DatasetInvalid, match="ještě nezačala"):
        accessor.for_execution_session(
            [instrument_id],
            execution_session,
            accessor.calendar.session_open(execution_session) - timedelta(microseconds=1),
        )


def test_execution_data_never_falls_back_to_previous_raw_open(factory) -> None:
    signal_session = date(2026, 1, 5)
    execution_session = date(2026, 1, 6)
    instrument_id = _observation(factory, signal_session)
    accessor = ValidatedCurrentDataAccessor(factory)

    with pytest.raises(DatasetInvalid, match="raw open"):
        accessor.for_execution_session(
            [instrument_id],
            execution_session,
            accessor.calendar.session_open(execution_session) + timedelta(minutes=1),
        )


@pytest.mark.parametrize("ids", [(), ("duplicate", "duplicate")])
def test_current_data_rejects_empty_or_duplicate_requests(factory, ids) -> None:
    with pytest.raises(DatasetInvalid):
        ValidatedCurrentDataAccessor(factory).latest(ids, datetime(2026, 1, 6, 22, tzinfo=UTC))
