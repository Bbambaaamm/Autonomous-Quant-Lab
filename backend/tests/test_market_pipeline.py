import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from quantlab.asset_directory import AssetDirectoryService, parse_assets
from quantlab.market_catalog import CatalogError
from quantlab.market_data import ProviderBar, ProviderMetadata, XNYSCalendar
from quantlab.market_pipeline import MarketPipeline, MarketTask
from quantlab.persistence import InstrumentRecord
from quantlab.phase4 import Phase4Repository

NOW = datetime(2026, 9, 21, 22, tzinfo=UTC)
START = date(2026, 1, 2)
END = date(2026, 9, 21)


def assets(symbols=("TESTA", "TESTB"), exchange="NASDAQ"):
    return json.dumps(
        [
            {
                "id": str(uuid4()),
                "symbol": s,
                "name": s + " company",
                "exchange": exchange,
                "class": "us_equity",
                "status": "active",
                "tradable": True,
            }
            for s in symbols
        ]
    ).encode()


@pytest.fixture
def env(tmp_path):
    repository = Phase4Repository(f"sqlite:///{tmp_path / 'market.db'}")
    factory = sessionmaker(repository.engine)
    directory = AssetDirectoryService(factory)
    identity = directory.sync(assets(), actor="test", reason="Test data", received_at=NOW)
    pipeline = MarketPipeline(factory)
    batch = pipeline.create(identity, START, END, "alpaca:iex", "test", "Test batch", NOW)
    return factory, pipeline, batch


class Provider:
    metadata = ProviderMetadata("alpaca", "test", True, True, "alpaca:iex")

    def resolve(self, symbol):
        return {"symbol": symbol, "provider_symbol": symbol}

    def historical_daily(self, symbol, start, end):
        return [
            ProviderBar(
                day, Decimal(100), Decimal(100), Decimal(100), Decimal(100), Decimal(1000), str(day)
            )
            for day in XNYSCalendar().sessions_between(start, end)
        ]

    def corporate_actions(self, symbol, start, end):
        return []


def test_entire_directory_enqueued_and_processed_without_orders(env):
    factory, pipeline, _ = env
    assert pipeline.read()["total"] == 2
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    data = pipeline.read()
    assert data["counts"] == {"DONE": 1, "PENDING": 1}
    item = next(row for row in data["items"] if row["state"] == "DONE")
    assert item["coverage"] == Decimal(1)
    assert item["momentum"] == Decimal(0)
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"DONE": 2}
    with factory() as session:
        assert len(list(session.scalars(select(InstrumentRecord)))) == 2
        record = session.scalar(select(MarketTask).where(MarketTask.state == "DONE"))
        assert json.loads(record.evidence_json)["eligible_for_promotion"] is False


def test_future_observations_do_not_leak(env):
    factory, pipeline, _ = env
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    with factory() as session:
        instrument = session.scalar(select(InstrumentRecord))
        instrument_id = instrument.instrument_id
    report = pipeline.screen(instrument_id, "alpaca:iex", START, END, NOW - timedelta(seconds=1))
    assert report["bars"] == 0
    assert report["momentum"] is None


def test_partial_bars_cannot_get_a_signal(env):
    _, pipeline, _ = env

    class Missing(Provider):
        def historical_daily(self, *args):
            return super().historical_daily(*args)[:-1]

    pipeline.step(lambda _: Missing(), clock=lambda: NOW)
    row = next(x for x in pipeline.read()["items"] if x["state"] == "DONE")
    assert row["coverage"] < 1
    assert row["momentum"] is None


def test_access_denial_stops_whole_feed_queue(env):
    _, pipeline, _ = env

    def denied(_):
        raise RuntimeError("MARKET_DATA_ACCESS_DENIED")

    pipeline.step(denied, clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"ACCESS_BLOCKED": 2}
    assert (
        pipeline.step(lambda _: pytest.fail("Must not call provider"), clock=lambda: NOW)["outcome"]
        == "NO_PENDING_MARKET_DATA"
    )


def test_retry_budget_and_restart_recovery(env):
    factory, pipeline, _ = env

    def offline(_):
        raise RuntimeError("offline")

    pipeline.step(offline, clock=lambda: NOW)
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.state == "RETRY"))
        task.state = "RUNNING"
        task.attempts = 3
        task.lease_until = NOW - timedelta(seconds=1)
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"DONE": 1, "FAILED": 1}


def test_idempotent_batch_and_future_range_rejected(env):
    factory, pipeline, batch = env
    from quantlab.market_pipeline import MarketBatch

    with factory() as session:
        snapshot = session.get(MarketBatch, batch).snapshot_id
    assert pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Same batch", NOW) == batch
    assert pipeline.read()["total"] == 2
    with pytest.raises(ValueError):
        pipeline.create(
            snapshot, START, END + timedelta(days=1), "alpaca:iex", "test", "Future", NOW
        )


def test_unsupported_exchange_is_visible_not_removed(tmp_path):
    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'unknown.db'}").engine)
    snapshot = AssetDirectoryService(factory).sync(
        assets(exchange="UNKNOWN"), actor="test", reason="Test unknown", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Test venue", NOW)
    assert pipeline.read()["counts"] == {"UNSUPPORTED_VENUE": 2}


@pytest.mark.parametrize("body", [b"[]", b"{}", b"not json", b'[{"id":"invalid"}]'])
def test_invalid_assets_rejected(body):
    with pytest.raises(CatalogError):
        parse_assets(body)


def test_stable_uuid_not_symbol_is_identity():
    body = json.loads(assets(("BEFORE",)))
    before = parse_assets(json.dumps(body).encode())
    body[0]["symbol"] = "AFTER"
    after = parse_assets(json.dumps(body).encode())
    assert before[0]["asset_id"] == after[0]["asset_id"]
    assert before[0]["symbol"] != after[0]["symbol"]


def test_rename_conflict_does_not_overwrite_existing_instrument(env):
    factory, pipeline, _ = env
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.state == "DONE"))
        instrument_id = task.instrument_id
        original_symbol = task.symbol
        task.state = "PENDING"
        task.symbol = "RENAMED"
        task.retry_at = NOW
    pipeline.step(
        lambda _: pytest.fail("Conflicting identity must not fetch data"), clock=lambda: NOW
    )
    with factory() as session:
        instrument = session.get(InstrumentRecord, instrument_id)
        assert instrument.symbol == original_symbol
        assert len(instrument_id) <= 40
    assert pipeline.read()["counts"].get("BLOCKED") == 1


def test_missing_corporate_action_knowledge_has_distinct_state(env):
    from quantlab.market_data import DatasetInvalid

    _, pipeline, _ = env

    class MissingEvidence(Provider):
        def corporate_actions(self, *args):
            raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")

    pipeline.step(lambda _: MissingEvidence(), clock=lambda: NOW)
    assert pipeline.read()["counts"].get("DATA_BLOCKED") == 1
    assert (
        "historická evidence"
        in next(x for x in pipeline.read()["items"] if x["state"] == "DATA_BLOCKED")["detail"]
    )


def test_incremental_fetch_keeps_overlap_and_does_not_skip_gaps(env):
    factory, pipeline, _ = env
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    with factory() as session:
        record = session.scalar(select(MarketTask).where(MarketTask.state == "DONE"))
        instrument = record.instrument_id
    days = XNYSCalendar().sessions_between(START, END)
    assert pipeline.incremental_start(instrument, "alpaca:iex", START, END, NOW) == days[-5]
    assert (
        pipeline.incremental_start(instrument, "alpaca:iex", START, END, NOW - timedelta(seconds=1))
        == days[0]
    )
    from sqlalchemy import delete

    from quantlab.persistence import MarketObservationRecord

    with factory() as session, session.begin():
        session.execute(
            delete(MarketObservationRecord).where(
                MarketObservationRecord.instrument_id == instrument,
                MarketObservationRecord.session_date
                == datetime.combine(days[30], datetime.min.time(), UTC),
            )
        )
    assert pipeline.incremental_start(instrument, "alpaca:iex", START, END, NOW) == days[25]


def test_server_filter_and_pagination_keep_denominator(env):
    _, pipeline, _ = env
    result = pipeline.read(limit=1, query="TESTA")
    assert result["total"] == 2
    assert result["matched"] == 1
    assert [x["symbol"] for x in result["items"]] == ["TESTA"]
    assert pipeline.read(limit=1, offset=1, query="TESTA")["items"] == []
    assert pipeline.read(query="%")["matched"] == 0
    with pytest.raises(ValueError):
        pipeline.read(rank="DROP TABLE")


def test_pipeline_endpoints_enforce_roles_and_validate_parameters(tmp_path, monkeypatch):
    from test_phase8_api import client

    from quantlab import api as module

    api = client(tmp_path, monkeypatch)
    api.headers["Authorization"] = f"Bearer {module.settings.api_viewer_token}"
    assert api.get("/operator/market-pipeline").status_code == 200
    assert api.get("/operator/market-pipeline?rank=invalid").status_code == 422
    assert (
        api.post(
            "/operator/market-pipeline/identities", json={"reason": "Test directory"}
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/operator/market-pipeline/batches",
            json={"reason": "Test batch", "start": "2026-01-02", "end": "2026-09-18"},
        ).status_code
        == 403
    )


def test_market_job_control_cannot_change_trading_jobs(tmp_path, monkeypatch):
    from test_phase8_api import client

    from quantlab import api as module

    api = client(tmp_path, monkeypatch)
    response = api.post("/operator/market-coverage/schedule", json={"reason": "Test daily job"})
    assert response.status_code == 200
    assert (
        api.post(
            "/operator/market-pipeline/control",
            json={
                "job_id": "market-catalog-daily",
                "enabled": False,
                "reason": "Pause acquisition",
            },
        ).json()["enabled"]
        is False
    )
    assert (
        api.post(
            "/operator/market-pipeline/control",
            json={"job_id": "paper-main", "enabled": False, "reason": "Invalid scope"},
        ).status_code
        == 422
    )
    api.headers["Authorization"] = f"Bearer {module.settings.api_viewer_token}"
    assert (
        api.post(
            "/operator/market-pipeline/control",
            json={"job_id": "market-catalog-daily", "enabled": True, "reason": "Reject viewer"},
        ).status_code
        == 403
    )


def test_old_access_failure_does_not_block_recovered_batch(env):
    factory, pipeline, _ = env
    pipeline.step(
        lambda _: (_ for _ in ()).throw(RuntimeError("MARKET_DATA_ACCESS_DENIED")),
        clock=lambda: NOW,
    )
    later = NOW + timedelta(minutes=1)
    snapshot = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Credentials recovered", received_at=later
    )
    pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Recovery batch", later)
    pipeline.step(lambda _: Provider(), clock=lambda: later)
    pipeline.step(lambda _: Provider(), clock=lambda: later)
    tomorrow = NOW + timedelta(days=1)
    assert pipeline.refresh(tomorrow) == "MARKET_BATCH_CREATED"
    assert pipeline.read()["counts"] == {"PENDING": 2}


def test_screening_snapshot_preserves_failed_instruments_and_denominator(env):
    from quantlab.market_screening import MarketScreening

    factory, pipeline, batch = env
    screens = MarketScreening(factory)
    assert screens.finalize(batch, NOW) is None
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    pipeline.step(lambda _: Provider(), clock=lambda: NOW)
    run = screens.finalize(batch, NOW)
    assert run == screens.finalize(batch, NOW + timedelta(seconds=1))
    report = screens.read(limit=1, query="TESTA")
    assert report["run"]["total"] == 2
    assert report["matched"] == 1
    assert report["run"]["eligible"] == 0
    assert report["items"][0]["reasons"] == ["LOW_FEED_LIQUIDITY"]
    # Replaying a mutable task must not change the historical selection.
    with factory() as session, session.begin():
        task = session.scalar(select(MarketTask).where(MarketTask.batch_id == batch))
        task.evidence_json = None
    assert screens.read(limit=1, query="TESTA") == report


def test_directory_change_history_never_invents_ipo_or_delisting(env):
    factory, _, _ = env
    service = AssetDirectoryService(factory)
    body = json.loads(assets(("BEFORE", "REMOVED")))
    t1 = NOW + timedelta(minutes=1)
    t2 = NOW + timedelta(minutes=2)
    service.sync(json.dumps(body).encode(), actor="test", reason="History start", received_at=t1)
    body.pop()
    body[0]["symbol"] = "AFTER"
    service.sync(json.dumps(body).encode(), actor="test", reason="History change", received_at=t2)
    assert service.latest(t1)["total"] == 2
    changes = service.latest(t2)["changes"]
    assert changes["symbol_or_venue_changed"] == 1
    assert changes["no_longer_present"] == 1
    assert changes["first_seen"] == 0


def test_screening_streams_multiple_pages_including_all_unsupported_assets(tmp_path):
    from quantlab.market_screening import MarketScreening

    factory = sessionmaker(Phase4Repository(f"sqlite:///{tmp_path / 'stream.db'}").engine)
    snapshot = AssetDirectoryService(factory).sync(
        assets(tuple(f"T{i:04}" for i in range(205)), exchange="UNKNOWN"),
        actor="test",
        reason="Large universe",
        received_at=NOW,
    )
    batch = MarketPipeline(factory).create(
        snapshot, START, END, "alpaca:iex", "test", "Stream all", NOW
    )
    screening = MarketScreening(factory)
    assert screening.finalize(batch, NOW)
    data = screening.read(limit=200, offset=200)
    assert data["run"]["total"] == 205
    assert data["run"]["eligible"] == 0
    assert len(data["items"]) == 5
    assert data["items"][-1]["symbol"] == "T0204"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("MARKET_REQUEST_BUDGET_EXHAUSTED", "Vyčerpán limit požadavků nebo 45 sekund"),
        ("Dočasná chyba Alpaca provideru", "Poskytovatel vrátil HTTP 5xx"),
        ("private-secret", "Dočasná chyba"),
    ],
)
def test_retry_distinguishes_budget_and_provider_failure_without_raw_messages(
    env, message, expected
):
    from quantlab.market_data import ProviderUnavailable

    _, pipeline, _ = env

    class Failure(Provider):
        def historical_daily(self, *args):
            raise ProviderUnavailable(message)

    pipeline.step(lambda _: Failure(), clock=lambda: NOW)
    item = next(row for row in pipeline.read()["items"] if row["state"] == "RETRY")
    assert item["detail"].startswith(expected)
    assert "private-secret" not in item["detail"]
