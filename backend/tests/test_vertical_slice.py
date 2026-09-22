from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import quantlab.api as api_module
from quantlab.api import app
from quantlab.data import DataValidationError, validate_bars
from quantlab.demo import load_fixture, run_demo
from quantlab.domain import Bar
from quantlab.market_data import IngestionResult
from quantlab.strategy import MovingAverageStrategy

FIXTURE = Path(__file__).parent / "fixtures" / "sample_market_data.csv"


def test_vertical_slice_executes_on_next_bar_with_adverse_slippage() -> None:
    result = run_demo(FIXTURE)
    assert len(result.fills) >= 2
    first = result.fills[0]
    assert first.timestamp == datetime(2025, 1, 9, 21, tzinfo=UTC)
    assert first.price > Decimal("106")
    assert first.commission > Decimal("1")
    assert result.final_value > Decimal("0")
    assert result.fills[-1].price < Decimal("92")  # Sell slippage zhoršuje cenu.


def test_future_change_does_not_change_earlier_signal() -> None:
    bars = load_fixture(FIXTURE)
    strategy = MovingAverageStrategy()
    before = strategy.generate_target(bars[:6])
    changed = list(bars)
    last = changed[-1]
    changed[-1] = Bar(
        last.symbol,
        last.timestamp,
        Decimal("900"),
        Decimal("1001"),
        Decimal("899"),
        Decimal("1000"),
        last.volume,
        Decimal("1000"),
    )
    assert strategy.generate_target(changed[:6]) == before


def test_invalid_ohlc_fails_closed() -> None:
    bars = load_fixture(FIXTURE)
    original = bars[0]
    bars[0] = Bar(
        original.symbol,
        original.timestamp,
        original.open,
        Decimal("90"),
        original.low,
        original.close,
        original.volume,
        original.adjusted_close,
    )
    with pytest.raises(DataValidationError):
        validate_bars(bars)


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        Bar("SPY", datetime(2025, 1, 1), *(Decimal("1") for _ in range(6)))


def test_api_and_dashboard() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["trading_mode"] == "paper"
    assert "Autonomous Quant Lab" in client.get("/").text
    response = client.post("/api/backtests/demo")
    assert response.status_code == 200
    assert response.json()["fills"]


def test_failed_ingestion_api_returns_json_502_with_iso_dates(monkeypatch) -> None:
    suffix = uuid4().hex
    instrument_id = f"failed-ingestion-{suffix}"
    client = TestClient(app)
    instrument = client.post(
        "/operator/instruments",
        json={
            "instrument_id": instrument_id,
            "symbol": f"F{suffix[:7]}".upper(),
            "active_from": "2020-01-01",
            "reason": "příprava failed ingestion regrese",
        },
    )
    assert instrument.status_code == 200, instrument.text
    failed = IngestionResult(
        f"failed-ingestion-{suffix}",
        date(2026, 6, 1),
        date(2026, 8, 28),
        "FAILED",
        (),
        "provider unavailable",
    )
    monkeypatch.setattr(api_module, "build_market_data_provider", lambda settings, engine: object())
    monkeypatch.setattr(api_module.market_data_service, "ingest", lambda *args: failed)

    response = client.post(
        "/operator/market-data/ingestions",
        json={
            "instrument_id": instrument_id,
            "start": "2026-06-01",
            "end": "2026-08-28",
            "reason": "ověření JSON chyby ingestion",
        },
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["requested_start"] == "2026-06-01"
    assert response.json()["detail"]["requested_end"] == "2026-08-28"
    assert response.json()["detail"]["error"] == "provider unavailable"


def test_research_api_persists_experiment_and_exposes_report() -> None:
    client = TestClient(app)
    created = client.post("/demo/research/experiments")
    assert created.status_code == 200
    experiment_id = created.json()["id"]
    fetched = client.get(f"/research/experiments/{experiment_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == experiment_id
    report = client.get(f"/research/experiments/{experiment_id}/report")
    assert report.status_code == 200
    assert report.json()["id"] == experiment_id
    assert "Research report" in report.json()["report"]


def _receipt_payload(rows, symbol="FULT", instrument_id="asset-test"):
    import hashlib
    import json

    payload = {
        "source": "alpaca_rest_current_inventory",
        "symbol": symbol,
        "instrument_id": instrument_id,
        "request_start": "1970-01-01",
        "request_end": "9999-12-31",
        "data_quality": "all",
        "rows": rows,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize(
    "kind,label",
    [
        ("stock_dividends", "akciová dividenda"),
        ("spin_offs", "spin-off"),
        ("unit_splits", "unit split"),
        ("stock_mergers", "fúze s akciovým"),
        ("provider-secret-token", "jiný dosud nepodporovaný typ"),
    ],
)
def test_market_blockage_explains_unsupported_inventory_without_echoing_provider(kind, label):
    from quantlab.market_diagnostics import inventory_blockage

    raw, digest = _receipt_payload([[kind, {"id": "ca1", "ex_date": "2026-08-10"}]])
    message = inventory_blockage(
        raw,
        digest,
        symbol="FULT",
        instrument_id="asset-test",
        start=date(2025, 8, 17),
        end=date(2026, 9, 21),
    )
    assert label in message and "2026-08-10" in message
    assert "provider-secret-token" not in message


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("hash", "otisk"),
        ("scope", "neodpovídá"),
        ("missing_date", "chybí"),
        ("invalid_date", "neplatné datum"),
        ("duplicate", "duplicitní"),
        ("malformed", "nelze ověřit"),
        ("too_large", "limit"),
    ],
)
def test_market_blockage_rejects_unverifiable_receipts(fault, expected):
    from quantlab.market_diagnostics import MAX_RECEIPT_CHARS, inventory_blockage

    row = {"id": "ca1", "ex_date": "2026-08-10"}
    if fault == "missing_date":
        row.pop("ex_date")
    if fault == "invalid_date":
        row["ex_date"] = "provider-secret-token"
    rows = [["cash_dividends", row]]
    if fault == "duplicate":
        rows = rows * 2
    raw, digest = _receipt_payload(rows)
    if fault == "hash":
        digest = "0" * 64
    if fault == "malformed":
        raw = "not-json-provider-secret-token"
    if fault == "too_large":
        raw = "x" * (MAX_RECEIPT_CHARS + 1)
    message = inventory_blockage(
        raw,
        digest,
        symbol="OTHER" if fault == "scope" else "FULT",
        instrument_id="asset-test",
        start=date(2025, 8, 17),
        end=date(2026, 9, 21),
    )
    assert expected in message
    assert "provider-secret-token" not in message


@pytest.mark.parametrize(
    "kind,day",
    [
        ("cash_dividends", "2026-08-10"),
        ("stock_dividends", "2020-01-01"),
        ("stock_dividends", "2027-01-01"),
    ],
)
def test_market_blockage_does_not_invent_a_cause_or_clear_existing_state(kind, day):
    from quantlab.market_diagnostics import inventory_blockage

    raw, digest = _receipt_payload([[kind, {"id": "ca1", "ex_date": day}]])
    assert (
        inventory_blockage(
            raw,
            digest,
            symbol="FULT",
            instrument_id="asset-test",
            start=date(2025, 8, 17),
            end=date(2026, 9, 21),
        )
        is None
    )


@pytest.mark.parametrize("fault", ["none", "foreign_task", "future", "hash", "missing", "large"])
def test_market_pipeline_read_explains_old_blockage_without_mutating_data(tmp_path, fault):
    import json
    from datetime import timedelta

    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker
    from test_market_pipeline import END, NOW, START, assets

    from quantlab.asset_directory import AssetDirectoryService
    from quantlab.market_pipeline import MarketActionReceipt, MarketPipeline, MarketTask
    from quantlab.phase4 import Phase4Repository

    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'receipt.db'}").engine)
    directory = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Diagnostic fixture", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    pipeline.create(directory, START, END, "alpaca:iex", "test", "Diagnostic test", NOW)
    receipt_id = "a" * 64
    original = "Zdroj neposkytl požadovanou validní evidenci tržních dat"
    with factory() as session, session.begin():
        tasks = list(session.scalars(select(MarketTask).order_by(MarketTask.task_id)))
        task = tasks[0]
        task.state, task.detail, task.bars = "DATA_BLOCKED", original, 275
        task.coverage = Decimal(1)
        task.evidence_json = json.dumps({"current_action_receipt_id": receipt_id})
        raw, digest = _receipt_payload(
            [["stock_dividends", {"id": "ca1", "ex_date": "2026-08-10"}]],
            symbol=task.symbol,
            instrument_id=task.instrument_id,
        )
        task_id, symbol = task.task_id, task.symbol
        if fault != "missing":
            session.add(
                MarketActionReceipt(
                    receipt_id=receipt_id,
                    task_id=tasks[1].task_id if fault == "foreign_task" else task_id,
                    received_at=datetime.now(UTC) + timedelta(days=1) if fault == "future" else NOW,
                    content_hash="0" * 64 if fault == "hash" else digest,
                    payload_json="x" * 262145 if fault == "large" else raw,
                )
            )
    result = pipeline.read(query=symbol, state="DATA_BLOCKED")
    assert result["total"] == 2 and result["matched"] == 1
    item = result["items"][0]
    assert item["state"] == "DATA_BLOCKED" and item["bars"] == 275
    assert item["coverage"] == 1
    assert ("akciová dividenda" in item["detail"]) is (fault == "none")
    with factory() as session:
        persisted = session.get(MarketTask, task_id)
        assert persisted.detail == original and persisted.state == "DATA_BLOCKED"
        assert persisted.bars == 275 and persisted.attempts == 0
        assert json.loads(persisted.evidence_json) == {"current_action_receipt_id": receipt_id}
        receipt = session.get(MarketActionReceipt, receipt_id)
        if fault == "missing":
            assert receipt is None
        else:
            assert receipt.payload_json == ("x" * 262145 if fault == "large" else raw)
    assert pipeline.read(limit=1, offset=1, state="DATA_BLOCKED")["items"] == []
    assert pipeline.read(state="PENDING")["matched"] == 1


@pytest.mark.parametrize("fault", ["none", "hash", "future", "foreign_task", "unsupported"])
def test_current_receipt_recovery_is_atomic_audited_and_never_changes_prices(tmp_path, fault):
    import json
    from datetime import timedelta

    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker
    from test_market_pipeline import END, NOW, START, assets, current_provider

    from quantlab.asset_directory import AssetDirectoryService
    from quantlab.current_action_recovery import recheck_current_receipts
    from quantlab.market_pipeline import (
        MarketActionReceipt,
        MarketActionReview,
        MarketPipeline,
        MarketTask,
    )
    from quantlab.market_screening import canonical, identity
    from quantlab.persistence import CorporateActionReadinessRecord, MarketObservationRecord
    from quantlab.phase4 import Phase4Repository

    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'review.db'}").engine)
    directory = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Review fixture", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    batch = pipeline.create(directory, START, END, "alpaca:iex", "test", "Review batch", NOW)
    pipeline.step(
        lambda inst: current_provider(
            inst,
            {
                "stock_dividends": [
                    {
                        "id": "div-1",
                        "symbol": inst.symbol,
                        "ex_date": "2026-08-10",
                        "rate": "0.05",
                    }
                ]
            },
        ),
        clock=lambda: NOW,
    )
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.state == "DONE"))
        assert task is not None
        receipt = session.scalar(
            select(MarketActionReceipt).where(MarketActionReceipt.task_id == task.task_id)
        )
        old = json.loads(task.evidence_json)
        old["current_action_receipt_id"] = receipt.receipt_id
        old["screening"]["eligible"] = False
        task.state, task.detail = "DATA_BLOCKED", "Legacy unsupported event"
        task.evidence_json = canonical(old)
        symbol, task_id = task.symbol, task.task_id
        if fault == "hash":
            receipt.content_hash = "0" * 64
        if fault == "future":
            receipt.received_at = NOW + timedelta(days=2)
        if fault == "foreign_task":
            receipt.task_id = session.scalar(
                select(MarketTask.task_id).where(MarketTask.task_id != task_id)
            )
        if fault == "unsupported":
            payload = json.loads(receipt.payload_json)
            payload["rows"][0][0] = "unit_splits"
            receipt.payload_json, receipt.content_hash = canonical(payload), identity(payload)
        raw_before, hash_before = receipt.payload_json, receipt.content_hash
        attempts, bars_before = task.attempts, task.bars
        prices_before = list(session.scalars(select(MarketObservationRecord.observation_id)))
    result = recheck_current_receipts(
        factory,
        batch,
        [symbol],
        "test-admin",
        "Review after parser update",
        NOW + timedelta(hours=1),
    )
    assert result["resolved"] == (1 if fault == "none" else 0)
    with factory() as session:
        task = session.get(MarketTask, task_id)
        assert task.state == ("DONE" if fault == "none" else "DATA_BLOCKED")
        assert task.attempts == attempts and task.bars == bars_before
        assert (
            list(session.scalars(select(MarketObservationRecord.observation_id))) == prices_before
        )
        receipt = session.scalar(select(MarketActionReceipt))
        assert receipt.payload_json == raw_before and receipt.content_hash == hash_before
        assert session.scalar(select(CorporateActionReadinessRecord)) is None
        reviews = list(session.scalars(select(MarketActionReview)))
        assert len(reviews) == (1 if fault == "none" else 0)
        if reviews:
            audit = json.loads(reviews[0].payload_json)
            assert identity(audit) == reviews[0].content_hash
            assert audit["previous_state"] == "DATA_BLOCKED" and audit["next_state"] == "DONE"
            assert audit["previous_evidence"] == old
            assert audit["next_evidence"]["screening"]["research_eligible"] is False
            assert audit["next_evidence"]["as_of"] == str(NOW)
    again = recheck_current_receipts(
        factory, batch, [symbol], "test-admin", "Repeated review", NOW + timedelta(hours=1)
    )
    assert again["resolved"] == 0


def test_receipt_recheck_endpoint_requires_real_admin_role(tmp_path, monkeypatch):
    from test_phase8_api import client

    from quantlab import api as module

    api = client(tmp_path, monkeypatch)
    api.headers["Authorization"] = f"Bearer {module.settings.api_viewer_token}"
    body = {"batch_id": "a" * 64, "symbols": ["TEST"], "reason": "Unauthorized review"}
    assert api.post("/operator/market-pipeline/recheck", json=body).status_code == 403
    api.headers["Authorization"] = f"Bearer {module.settings.api_admin_token}"
    assert api.post("/operator/market-pipeline/recheck", json=body).status_code == 409
    body["symbols"] = ["TEST"] * 51
    assert api.post("/operator/market-pipeline/recheck", json=body).status_code == 422


def _empty_queue_env(tmp_path):
    from sqlalchemy.orm import sessionmaker
    from test_market_pipeline import END, NOW, START, assets

    from quantlab.asset_directory import AssetDirectoryService
    from quantlab.market_pipeline import MarketPipeline
    from quantlab.phase4 import Phase4Repository

    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'empty-queue.db'}").engine)
    snapshot = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Queue fixture", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    batch = pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Empty queue", NOW)
    return factory, pipeline, batch


def test_empty_prices_remain_counted_without_inventing_actions_or_signal(tmp_path):
    import json

    from sqlalchemy import func, select
    from test_market_pipeline import NOW, Provider

    from quantlab.market_pipeline import MarketTask
    from quantlab.market_screening import MarketScreening, MarketScreenRun
    from quantlab.persistence import MarketDataIngestionRecord, MarketObservationRecord

    factory, pipeline, batch = _empty_queue_env(tmp_path)

    class Empty(Provider):
        def historical_daily(self, *args):
            return []

        def corporate_actions(self, *args):
            pytest.fail("No prices must not pretend to verify corporate actions")

    for _ in range(2):
        pipeline.step(lambda _: Empty(), clock=lambda: NOW)
    report = pipeline.read(state="NO_PRICE_DATA")
    assert report["total"] == report["matched"] == 2
    assert report["counts"] == {"NO_PRICE_DATA": 2}
    assert report["coverage_summary"]["downloaded"] == 0
    assert all(r["bars"] == 0 and r["coverage"] == 0 for r in report["items"])
    assert all(r["momentum"] is None for r in report["items"])
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MarketObservationRecord)) == 0
        tasks = list(session.scalars(select(MarketTask)))
        for task in tasks:
            evidence = json.loads(task.evidence_json)
            assert not evidence["eligible_for_promotion"]
            assert not evidence["screening"]["eligible"]
            assert not evidence["screening"]["research_eligible"]
            assert "NO_PRICE_DATA" in evidence["screening"]["reasons"]
            receipt = evidence["price_receipt"]
            ingest = session.get(MarketDataIngestionRecord, receipt["ingestion_id"])
            assert ingest.status == "SUCCEEDED" and ingest.row_count == 0
            assert receipt["returned_bars"] == 0
            assert task.attempts == 1
    run = MarketScreening(factory).finalize(batch, NOW)
    with factory() as session:
        saved = session.get(MarketScreenRun, run)
        assert saved.total == 2 and saved.eligible == 0


def test_due_retry_is_not_starved_by_older_pending_backfill(tmp_path):
    from datetime import timedelta

    from test_market_pipeline import NOW, Provider

    from quantlab.market_data import ProviderUnavailable

    _, pipeline, _ = _empty_queue_env(tmp_path)
    calls = []

    class Failing(Provider):
        def historical_daily(self, symbol, start, end):
            calls.append(symbol)
            raise ProviderUnavailable("fixture outage")

    class Recovered(Provider):
        def historical_daily(self, symbol, start, end):
            calls.append(symbol)
            return super().historical_daily(symbol, start, end)

    pipeline.step(lambda _: Failing(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"RETRY": 1, "PENDING": 1}
    pipeline.step(lambda _: Recovered(), clock=lambda: NOW + timedelta(minutes=6))
    assert calls[0] == calls[1]
    assert pipeline.read()["counts"] == {"DONE": 1, "PENDING": 1}
    pipeline.step(lambda _: Recovered(), clock=lambda: NOW + timedelta(minutes=6))
    assert calls[-1] != calls[0]
    assert pipeline.read()["counts"] == {"DONE": 2}


def test_retry_backoff_and_attempt_limit_still_apply(tmp_path):
    from datetime import timedelta

    from sqlalchemy import select
    from test_market_pipeline import NOW, Provider

    from quantlab.market_data import ProviderUnavailable
    from quantlab.market_pipeline import MarketTask

    factory, pipeline, _ = _empty_queue_env(tmp_path)
    calls = []

    class Failing(Provider):
        def historical_daily(self, symbol, start, end):
            calls.append(symbol)
            raise ProviderUnavailable("fixture outage")

    pipeline.step(lambda _: Failing(), clock=lambda: NOW)
    pipeline.step(lambda _: Failing(), clock=lambda: NOW + timedelta(seconds=1))
    assert calls[0] != calls[1]  # Not due yet: first retry cannot bypass backoff.
    for minutes in (6, 6, 17, 17):
        pipeline.step(
            lambda _: Failing(), clock=lambda minutes=minutes: NOW + timedelta(minutes=minutes)
        )
    assert pipeline.read()["counts"] == {"FAILED": 2}
    with factory() as session:
        assert all(t.attempts == 3 for t in session.scalars(select(MarketTask)))


def test_empty_increment_does_not_erase_or_reapprove_cached_prices(tmp_path):
    from datetime import timedelta

    from sqlalchemy import func, select
    from test_market_pipeline import NOW, Provider

    from quantlab.market_pipeline import MarketTask
    from quantlab.persistence import MarketObservationRecord

    factory, pipeline, _ = _empty_queue_env(tmp_path)
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.state == "DONE"))
        task.state = "RETRY"
        task.retry_at = NOW
        expected_bars, symbol = task.bars, task.symbol
        original_count = session.scalar(select(func.count()).select_from(MarketObservationRecord))

    class Empty(Provider):
        def historical_daily(self, *args):
            return []

        def corporate_actions(self, *args):
            pytest.fail("Empty incremental response cannot prove fresh actions")

    pipeline.step(lambda _: Empty(), clock=lambda: NOW + timedelta(minutes=1))
    row = pipeline.read(query=symbol)["items"][0]
    assert row["state"] == "RETRY" and row["bars"] == expected_bars
    assert row["momentum"] is None
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(MarketObservationRecord))
            == original_count
        )


def test_no_price_data_is_reconsidered_in_next_daily_batch(tmp_path):
    from datetime import timedelta

    from test_market_pipeline import NOW, Provider

    _, pipeline, _ = _empty_queue_env(tmp_path)

    class Empty(Provider):
        def historical_daily(self, *args):
            return []

    for _ in range(2):
        pipeline.step(lambda _: Empty(), clock=lambda: NOW)
    result = pipeline.step(lambda _: Empty(), clock=lambda: NOW + timedelta(days=1))
    assert result["outcome"] == "MARKET_BATCH_CREATED"
    assert pipeline.read()["counts"] == {"PENDING": 2}
    assert pipeline.read()["total"] == 2
