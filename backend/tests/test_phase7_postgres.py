from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_phase6_e2e_postgres import CALENDAR, _research_to_paper

from quantlab.market_data import CorporateActionKind
from quantlab.persistence import CorporateActionRecord, MarketObservationRecord
from quantlab.phase4 import PaperAccountRecord, PositionRecord
from quantlab.phase6_runtime import ValidatedCurrentDataAccessor
from quantlab.phase7 import (
    MonitoringState,
    PaperCorporateActionApplicationRecord,
    PaperCorporateActionService,
    PaperMonitoringRunRecord,
    PaperMonitoringService,
    PaperPerformanceEvaluationRecord,
    PaperPerformanceEvaluationService,
    PaperPerformanceService,
    PaperPerformanceSnapshotRecord,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


@pytest.fixture
def factory():
    from sqlalchemy import create_engine

    engine = create_engine(os.environ["DATABASE_URL"])
    return sessionmaker(engine, expire_on_commit=False)


def _concurrently(call):
    """Spustí production call nad dvěma nezávislými Sessions a connections."""
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait(timeout=10)
        return call()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(worker), pool.submit(worker))
        return tuple(future.result(timeout=60) for future in futures)


def _executed_monitoring(factory):
    engine = factory.kw["bind"]
    result = _research_to_paper(factory, engine, halted=False)
    account_id, deployment, _experiment, _snapshot, _cycle, instrument, _ = result
    with factory() as session:
        run = session.scalar(
            select(PaperMonitoringRunRecord).where(
                PaperMonitoringRunRecord.deployment_id == deployment.deployment_id
            )
        )
    return account_id, deployment, run, instrument


def _completed_as_of(factory, instrument_id: str) -> datetime:
    with factory() as session:
        observed = session.scalar(
            select(func.max(MarketObservationRecord.session_date)).where(
                MarketObservationRecord.instrument_id == instrument_id
            )
        )
    return CALENDAR.session_close(observed.date()) + timedelta(minutes=1)


def test_postgres_enrollment_race_is_service_level_exactly_once(factory) -> None:
    _account, deployment, old_run, _instrument = _executed_monitoring(factory)
    service = PaperMonitoringService(factory)
    service.transition(
        old_run.monitoring_id, MonitoringState.RETIRED, "race setup", datetime.now(UTC)
    )
    started = datetime.now(UTC) + timedelta(seconds=1)

    rows = _concurrently(
        lambda: PaperMonitoringService(factory).enroll(
            deployment.deployment_id, old_run.policy_id, started
        )
    )

    assert rows[0].monitoring_id == rows[1].monitoring_id
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperMonitoringRunRecord)
                .where(PaperMonitoringRunRecord.monitoring_id == rows[0].monitoring_id)
            )
            == 1
        )


def test_postgres_capture_and_evaluation_races_are_exactly_once(factory) -> None:
    _account, _deployment, run, instrument = _executed_monitoring(factory)
    as_of = _completed_as_of(factory, instrument.instrument_id)
    captures = _concurrently(
        lambda: PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory)).capture(
            run.monitoring_id, as_of
        )
    )
    assert captures[0].snapshot_id == captures[1].snapshot_id

    evaluations = _concurrently(
        lambda: PaperPerformanceEvaluationService(factory).evaluate(
            run.monitoring_id, captures[0].snapshot_id, as_of
        )
    )
    assert evaluations[0].evaluation_id == evaluations[1].evaluation_id
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperPerformanceSnapshotRecord)
                .where(PaperPerformanceSnapshotRecord.snapshot_id == captures[0].snapshot_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperPerformanceEvaluationRecord)
                .where(
                    PaperPerformanceEvaluationRecord.evaluation_id == evaluations[0].evaluation_id
                )
            )
            == 1
        )


def test_postgres_pause_and_resume_cannot_overwrite_safety_suspension(factory) -> None:
    account_id, _deployment, run, instrument = _executed_monitoring(factory)
    snapshot = PaperPerformanceService(factory, ValidatedCurrentDataAccessor(factory)).capture(
        run.monitoring_id, _completed_as_of(factory, instrument.instrument_id)
    )
    with factory() as session, session.begin():
        account = session.get(PaperAccountRecord, account_id)
        account.trading_state = "HALTED"

    def suspend():
        return PaperPerformanceEvaluationService(factory).evaluate(
            run.monitoring_id, snapshot.snapshot_id, datetime.now(UTC)
        )

    def pause():
        try:
            return PaperMonitoringService(factory).transition(
                run.monitoring_id, MonitoringState.PAUSED, "operator pause", datetime.now(UTC)
            )
        except ValueError:
            return None

    _concurrently(lambda: suspend() if threading.current_thread().name.endswith("_0") else pause())
    with factory() as session:
        assert session.get(PaperMonitoringRunRecord, run.monitoring_id).state == "SUSPENDED"
    with pytest.raises(ValueError):
        PaperMonitoringService(factory).transition(
            run.monitoring_id, MonitoringState.ACTIVE, "stale resume", datetime.now(UTC)
        )


def test_postgres_split_and_dividend_races_credit_ledger_once(factory) -> None:
    account_id, _deployment, _run, instrument = _executed_monitoring(factory)
    with factory() as session:
        before = session.get(PositionRecord, (account_id, instrument.instrument_id)).quantity
        cash_before = session.get(PaperAccountRecord, account_id).cash
    effective = datetime.now(UTC) + timedelta(seconds=1)
    split_id, dividend_id = f"split-{uuid4().hex}", f"dividend-{uuid4().hex}"
    with factory() as session, session.begin():
        session.add_all(
            [
                CorporateActionRecord(
                    action_id=split_id,
                    instrument_id=instrument.instrument_id,
                    kind=CorporateActionKind.SPLIT,
                    effective_at=effective,
                    known_at=effective,
                    value="2",
                    new_symbol=None,
                ),
                CorporateActionRecord(
                    action_id=dividend_id,
                    instrument_id=instrument.instrument_id,
                    kind=CorporateActionKind.CASH_DIVIDEND,
                    effective_at=effective + timedelta(seconds=1),
                    known_at=effective + timedelta(seconds=1),
                    value="1",
                    new_symbol=None,
                ),
            ]
        )
    cutoff = effective + timedelta(seconds=2)
    _concurrently(lambda: PaperCorporateActionService(factory).apply(account_id, cutoff))
    with factory() as session:
        position = session.get(PositionRecord, (account_id, instrument.instrument_id))
        assert position.quantity == before * 2
        assert session.get(PaperAccountRecord, account_id).cash == cash_before + before * 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperCorporateActionApplicationRecord)
                .where(PaperCorporateActionApplicationRecord.action_id.in_((split_id, dividend_id)))
            )
            == 2
        )
