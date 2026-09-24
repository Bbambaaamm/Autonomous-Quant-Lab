from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from quantlab.market_data import CorporateActionEvent, CorporateActionEventType
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import (
    CorporateActionEventCursorRecord,
    CorporateActionEventRecord,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def _event(event_id: str, received_at: datetime) -> CorporateActionEvent:
    return CorporateActionEvent(
        event_id=event_id,
        at=datetime(2026, 9, 24, 7, 0, tzinfo=UTC),
        action=CorporateActionEventType.INSERT,
        provider_action_id="ca-" + event_id,
        payload_hash="a" * 64,
        received_at=received_at,
        symbols=("AAPL",),
        scope_date=date(2026, 9, 25),
    )


def test_event_cursor_is_atomic_idempotent_and_preserves_stream_order() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, expire_on_commit=False)
    service = PersistentMarketDataService(factory)
    suffix = uuid4().hex[:16]
    provider = f"alpaca-cursor-{suffix}"
    received_at = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
    first = _event(f"z-{suffix}", received_at)
    second = _event(f"a-{suffix}", received_at)

    assert service.latest_corporate_action_event_id(provider) is None
    assert service.record_corporate_action_event(provider, first) is True
    assert service.latest_corporate_action_event_id(provider) == first.event_id

    replay = CorporateActionEvent(
        event_id=first.event_id,
        at=first.at,
        action=first.action,
        provider_action_id=first.provider_action_id,
        payload_hash=first.payload_hash,
        received_at=received_at + timedelta(minutes=5),
        symbols=first.symbols,
        scope_date=first.scope_date,
    )
    assert service.record_corporate_action_event(provider, replay) is False
    assert service.latest_corporate_action_event_id(provider) == first.event_id

    # Both receipts have the same causal receipt time. The durable cursor must follow
    # actual stream arrival order, not lexicographic event_id ordering.
    assert service.record_corporate_action_event(provider, second) is True
    assert service.latest_corporate_action_event_id(provider) == second.event_id

    with factory() as session:
        cursor = session.get(CorporateActionEventCursorRecord, provider)
        count = session.scalar(
            select(func.count())
            .select_from(CorporateActionEventRecord)
            .where(CorporateActionEventRecord.provider == provider)
        )
        index_names = set(
            session.scalars(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'corporate_action_events'
                    """
                )
            )
        )

    assert cursor is not None
    assert cursor.last_event_id == second.event_id
    assert count == 2
    assert "ix_corporate_action_events_provider_occurred_event" in index_names
    engine.dispose()
