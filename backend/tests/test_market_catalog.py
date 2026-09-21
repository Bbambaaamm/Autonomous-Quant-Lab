from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from quantlab.market_catalog import (
    CatalogError,
    CatalogListing,
    CatalogSnapshot,
    MarketCatalogService,
    parse_directory,
)
from quantlab.persistence import Base

NOW = datetime(2026, 9, 21, 20, tzinfo=UTC)


def directory(source, symbols=("IBM",)):
    if source == "nasdaqlisted":
        header = "Symbol|Security Name|ETF|Test Issue"
        rows = [f"{symbol}|{symbol} company|N|N" for symbol in symbols]
    else:
        header = "ACT Symbol|Security Name|ETF|Test Issue|Exchange"
        rows = ["SPY|S&P fund|Y|N|P", "TEST|Test security|N|Y|N"]
    return ("\n".join([header, *rows, "File Creation Time: 0921202616:00||||"]) + "\n").encode()


@pytest.fixture
def service():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[CatalogSnapshot.__table__, CatalogListing.__table__])
    return MarketCatalogService(sessionmaker(engine))


def sync(service, symbols=("IBM",), at=NOW):
    return service.sync(
        actor="test-admin",
        reason="Test katalogu",
        clock=lambda: at,
        fetch=lambda source: directory(source, symbols),
    )


def test_complete_snapshot_excludes_tests_and_keeps_unknown_price_coverage(service):
    sync(service)
    result = service.read(NOW)
    assert result["snapshot"]["listing_count"] == 2
    assert result["snapshot"]["test_count"] == 1
    assert result["price_coverage"] is None
    assert result["global_coverage"] == "NOT_IMPLEMENTED"
    assert result["items"][0]["security_type"] == "OTHER_LISTED_SECURITY"
    assert result["status"] == "RECEIVED"


def test_pagination_and_search_preserve_global_denominator(service):
    sync(service, tuple(f"S{i:05d}" for i in range(1500)))
    result = service.read(NOW, query="s000", offset=50, limit=20)
    assert result["total"] == 100
    assert len(result["items"]) == 20
    assert result["items"][0]["symbol"] == "S00050"
    assert result["snapshot"]["listing_count"] == 1501
    assert service.read(NOW, query="%")["total"] == 0


def test_partial_sync_keeps_old_snapshot(service):
    old = sync(service)

    def fail(source):
        return directory(source, ("NEW",)) if source == "nasdaqlisted" else b"broken"

    with pytest.raises(CatalogError):
        service.sync(
            actor="test", reason="Fail test", fetch=fail, clock=lambda: NOW + timedelta(hours=1)
        )
    assert service.read(NOW + timedelta(hours=2))["snapshot"]["id"] == old


def test_no_future_leak_and_no_backdated_membership(service):
    old = sync(service)
    new = sync(service, ("NEW",), NOW + timedelta(days=1))
    assert service.read(NOW - timedelta(seconds=1))["snapshot"] is None
    assert service.read(NOW)["snapshot"]["id"] == old
    assert service.read(NOW + timedelta(days=1))["snapshot"]["id"] == new
    assert service.read(NOW, query="NEW")["total"] == 0
    assert service.read(NOW + timedelta(days=5))["status"] == "STALE"


def test_retry_same_receipt_is_idempotent(service):
    assert sync(service) == sync(service)
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(CatalogSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(CatalogListing)) == 2


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>outage</html>",
        directory("nasdaqlisted").replace(b"File Creation Time:", b"Truncated:"),
        directory("nasdaqlisted", ("IBM", "IBM")),
        directory("nasdaqlisted").replace(b"|N|N", b"|INVALID|N"),
        directory("nasdaqlisted").replace(b"ETF", b"ChangedSchema"),
    ],
)
def test_invalid_responses_fail_closed(body):
    with pytest.raises(CatalogError):
        parse_directory("nasdaqlisted", body)


def test_unknown_exchange_is_not_silently_dropped():
    rows, _, _ = parse_directory("otherlisted", directory("otherlisted").replace(b"|P", b"|X"))
    assert rows[0]["exchange"] == "Neznámá (X)"


@pytest.mark.parametrize(
    "args", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"query": "x" * 101}]
)
def test_page_bounds(service, args):
    with pytest.raises(CatalogError):
        service.read(NOW, **args)


def test_old_source_file_is_stale_even_after_new_download(service):
    sync(service, at=NOW + timedelta(days=10))
    assert service.read(NOW + timedelta(days=10))["status"] == "STALE"


def test_api_rbac_and_actual_sync(tmp_path, monkeypatch):
    from test_phase8_api import client

    from quantlab.security import limiter

    api = client(tmp_path, monkeypatch)
    import quantlab.api as module

    original = MarketCatalogService.sync

    def local_sync(self, **kwargs):
        return original(self, **kwargs, fetch=directory, clock=lambda: NOW)

    monkeypatch.setattr(MarketCatalogService, "sync", local_sync)
    limiter.events.clear()
    api.headers["Authorization"] = f"Bearer {module.settings.api_viewer_token}"
    assert api.get("/operator/market-coverage").status_code == 200
    assert (
        api.post(
            "/operator/market-coverage/sync", json={"reason": "Test synchronizace"}
        ).status_code
        == 403
    )
    api.headers["Authorization"] = f"Bearer {module.settings.api_admin_token}"
    assert (
        api.post(
            "/operator/market-coverage/sync", json={"reason": "Test synchronizace"}
        ).status_code
        == 200
    )
    with module.session_factory() as db:
        record = db.scalar(select(CatalogSnapshot))
        assert record.actor == "api-admin"
    assert api.get("/operator/market-coverage?limit=201").status_code == 422
    first = api.post("/operator/market-coverage/schedule", json={"reason": "Denní katalog"})
    second = api.post("/operator/market-coverage/schedule", json={"reason": "Denní katalog"})
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_scheduler_execution_updates_catalog_without_trading(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from quantlab.automation import AutomationRepository, JobExecutor, JobType, ScheduleType
    from quantlab.phase4 import Phase4Repository

    url = f"sqlite:///{tmp_path / 'scheduler.db'}"
    phase4 = Phase4Repository(url)
    phase4.seed_account()
    repository = AutomationRepository(url)
    job = repository.create_job(
        job_type=JobType.SYNC_MARKET_CATALOG,
        account_id="paper-main",
        schedule_type=ScheduleType.DAILY,
        daily_time="01:00",
        next_run_at=NOW,
    )
    original = MarketCatalogService.sync

    def local_sync(self, **kwargs):
        return original(self, **kwargs, fetch=directory)

    monkeypatch.setattr(MarketCatalogService, "sync", local_sync)
    executor = JobExecutor(repository, clock=lambda: NOW)
    run = SimpleNamespace(
        id="scheduled-test",
        config_snapshot_json=json.dumps(
            {
                "snapshot_version": 1,
                "identity": {
                    "account_id": "paper-main",
                    "job_type": "SYNC_MARKET_CATALOG",
                    "strategy_id": None,
                },
                "config": {},
            }
        ),
    )
    result = executor(job, run)
    assert result == {
        "trading_cycle_id": None,
        "reconciliation_id": None,
        "outcome": "CATALOG_RECEIVED",
    }
    assert MarketCatalogService(sessionmaker(repository.engine)).read(NOW)["total"] == 2
