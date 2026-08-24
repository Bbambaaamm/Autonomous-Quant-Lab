import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from quantlab.phase7 import PaperPerformanceSnapshotRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


def test_phase7_performance_schema_supports_ordered_multi_session_series() -> None:
    """Plný research→paper flow používají Phase 6 E2E; zde ověřujeme navazující PG schema."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(PaperPerformanceSnapshotRecord).order_by(
                    PaperPerformanceSnapshotRecord.monitoring_id,
                    PaperPerformanceSnapshotRecord.session_date,
                )
            )
        )
        assert all(
            previous.session_date <= current.session_date
            for previous, current in zip(rows, rows[1:], strict=False)
            if previous.monitoring_id == current.monitoring_id
        )
