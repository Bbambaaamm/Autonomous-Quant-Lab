from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_market_pipeline import END, NOW, START, assets

import quantlab.provider_factory as provider_factory
from quantlab.asset_directory import AssetDirectoryService
from quantlab.market_data import ProviderUnavailable
from quantlab.market_pipeline import MarketBatch, MarketPipeline, MarketTask
from quantlab.phase4 import Phase4Repository


def _pipeline(tmp_path):
    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'telemetry.db'}").engine)
    snapshot = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Telemetry fixture", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    batch = pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Telemetry", NOW)
    return factory, pipeline, batch


def test_budgeted_transport_counts_only_real_http_attempts(monkeypatch):
    calls = []

    def fake(url, headers, timeout):
        calls.append((url, timeout))
        return 200, {}, b"abc"

    monkeypatch.setattr(provider_factory, "alpaca_rest_transport", fake)
    transport = provider_factory._BudgetedAlpacaTransport(2)
    for _ in range(2):
        assert transport("https://data.alpaca.markets/v1/corporate-actions", {}, 1)[0] == 200
    assert transport.request_count == 2
    assert transport.response_bytes == 6
    with pytest.raises(ProviderUnavailable, match="MARKET_REQUEST_BUDGET_EXHAUSTED"):
        transport("https://data.alpaca.markets/v1/corporate-actions", {}, 1)
    assert len(calls) == 2
    assert transport.request_count == 2


def test_batch_metrics_wait_for_terminal_state_and_freeze(tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    pipeline._record_batch_metrics(batch_id, NOW)
    with factory() as session:
        assert session.get(MarketBatch, batch_id).metrics_json is None

    with factory() as session, session.begin():
        tasks = list(session.scalars(select(MarketTask).where(MarketTask.batch_id == batch_id)))
        tasks[0].state = "DONE"
        tasks[0].http_requests = 2
        tasks[0].response_bytes = 120
        tasks[0].peak_rss_kib = 111
        tasks[0].attempts = 1
        tasks[1].state = "NO_PRICE_DATA"
        tasks[1].http_requests = 1
        tasks[1].response_bytes = 20
        tasks[1].peak_rss_kib = 123
        tasks[1].attempts = 2
    completed = NOW + timedelta(minutes=7)
    pipeline._record_batch_metrics(batch_id, completed)
    report = pipeline.read()
    metrics = report["batch"]["metrics"]
    assert metrics["telemetry_complete"] is True
    assert metrics["http_requests"] == 3
    assert metrics["response_bytes"] == 140
    assert metrics["task_attempts"] == 3
    assert metrics["peak_rss_kib"] == 123
    assert metrics["task_count"] == 2
    assert metrics["database_bytes_at_start"] is None
    assert metrics["database_bytes_at_end"] is None
    assert metrics["database_growth_bytes"] is None
    assert metrics["duration_seconds"] == 420
    assert report["batch"]["completed_at"] is not None

    # Immutable-once-recorded: later calls do not rewrite the completion evidence.
    pipeline._record_batch_metrics(batch_id, completed + timedelta(hours=1))
    assert pipeline.read()["batch"]["metrics"] == metrics


def test_legacy_batch_is_explicitly_not_complete_telemetry(tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    with factory() as session, session.begin():
        batch = session.get(MarketBatch, batch_id)
        batch.telemetry_version = None
        for task in session.scalars(select(MarketTask).where(MarketTask.batch_id == batch_id)):
            task.state = "DONE"
    pipeline._record_batch_metrics(batch_id, NOW + timedelta(minutes=1))
    assert pipeline.read()["batch"]["metrics"]["telemetry_complete"] is False
