import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_market_pipeline import END, NOW, START, assets

import quantlab.provider_factory as provider_factory
from quantlab.asset_directory import AssetDirectoryService
from quantlab.market_data import AlpacaProvider, ProviderUnavailable, XNYSCalendar
from quantlab.market_pipeline import MarketBatch, MarketBatchMetric, MarketPipeline, MarketTask
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
    persisted = []
    transport = provider_factory._BudgetedAlpacaTransport(2)
    transport.set_telemetry_sink(
        lambda requests, response_bytes: persisted.append((requests, response_bytes))
    )
    for _ in range(2):
        assert transport("https://data.alpaca.markets/v1/corporate-actions", {}, 1)[0] == 200
    assert transport.request_count == 2
    assert transport.response_bytes == 6
    assert persisted == [(1, 0), (0, 3), (1, 0), (0, 3)]
    with pytest.raises(ProviderUnavailable, match="MARKET_REQUEST_BUDGET_EXHAUSTED"):
        transport("https://data.alpaca.markets/v1/corporate-actions", {}, 1)
    assert len(calls) == 2
    assert transport.request_count == 2
    assert len(persisted) == 4


def test_batch_metrics_wait_for_terminal_state_and_freeze(tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    pipeline._record_batch_metrics(batch_id, NOW)
    with factory() as session:
        assert session.get(MarketBatchMetric, batch_id) is None

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
    assert pipeline.read()["batch"]["metrics"] is None


def test_incomplete_reclaimed_attempt_hides_network_and_rss_totals(tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    with factory() as session, session.begin():
        tasks = list(session.scalars(select(MarketTask).where(MarketTask.batch_id == batch_id)))
        for task in tasks:
            task.state = "DONE"
            task.http_requests = 2
            task.response_bytes = 100
            task.peak_rss_kib = 200
        tasks[0].telemetry_complete = False
    pipeline._record_batch_metrics(batch_id, NOW + timedelta(minutes=2))
    metrics = pipeline.read()["batch"]["metrics"]
    assert metrics["telemetry_complete"] is False
    assert metrics["http_requests"] is None
    assert metrics["response_bytes"] is None
    assert metrics["peak_rss_kib"] is None
    assert metrics["task_attempts"] == 0


def test_alpaca_redirects_fail_closed():
    handler = provider_factory._AlpacaRedirectHandler()
    with pytest.raises(ProviderUnavailable, match="redirect není povolen"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://data.alpaca.markets/v1/corporate-actions",
        )


def test_transport_does_not_send_when_request_counter_cannot_persist(monkeypatch):
    sent = []
    monkeypatch.setattr(
        provider_factory,
        "alpaca_rest_transport",
        lambda *args: sent.append(args) or (200, {}, b"ok"),
    )
    transport = provider_factory._BudgetedAlpacaTransport(1)

    def fail_sink(requests, response_bytes):
        raise RuntimeError("db unavailable")

    transport.set_telemetry_sink(fail_sink)
    with pytest.raises(ProviderUnavailable, match="MARKET_TELEMETRY_PERSIST_FAILED"):
        transport("https://data.alpaca.markets/v1/corporate-actions", {}, 1)
    assert sent == []
    assert transport.telemetry_complete is False


def test_transport_delta_is_durable_before_task_finishes(tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.batch_id == batch_id))
        task.state = "RUNNING"
        task.lease_token = "lease"
        task_id = task.task_id
    pipeline._persist_transport_delta(task_id, "lease", 1, 0)
    pipeline._persist_transport_delta(task_id, "lease", 0, 321)
    with factory() as session:
        saved = session.get(MarketTask, task_id)
        assert saved.http_requests == 1
        assert saved.response_bytes == 321
        assert saved.peak_rss_kib > 0


def test_pipeline_persists_exact_alpaca_transport_counters(monkeypatch, tmp_path):
    factory, pipeline, batch_id = _pipeline(tmp_path)
    with factory() as session, session.begin():
        tasks = list(
            session.scalars(
                select(MarketTask)
                .where(MarketTask.batch_id == batch_id)
                .order_by(MarketTask.task_id)
            )
        )
        tasks[1].state = "UNSUPPORTED_VENUE"
    response_sizes = []

    def fake_transport(url, headers, timeout):
        if "/bars?" in url:
            body = {
                "bars": [
                    {
                        "t": f"{day.isoformat()}T20:00:00Z",
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100,
                        "v": 10000,
                    }
                    for day in XNYSCalendar().sessions_between(START, END)
                ],
                "next_page_token": None,
            }
        else:
            body = {"corporate_actions": {}, "next_page_token": None}
        payload = json.dumps(body).encode()
        response_sizes.append(len(payload))
        return 200, {}, payload

    monkeypatch.setattr(provider_factory, "alpaca_rest_transport", fake_transport)

    def make_provider(instrument):
        transport = provider_factory._BudgetedAlpacaTransport(12)
        return AlpacaProvider(
            "key",
            "secret",
            lambda _: (),
            {instrument.symbol: instrument.instrument_id},
            transport,
            feed="iex",
        )

    pipeline.step(make_provider, clock=lambda: NOW)
    with factory() as session:
        task = session.scalar(
            select(MarketTask).where(
                MarketTask.batch_id == batch_id,
                MarketTask.state == "DONE",
            )
        )
        assert task is not None
        assert task.http_requests == 2
        assert task.response_bytes == sum(response_sizes)
        assert task.telemetry_complete is True
        metric = session.get(MarketBatchMetric, batch_id)
        assert metric is not None
        assert json.loads(metric.metrics_json)["telemetry_complete"] is True
