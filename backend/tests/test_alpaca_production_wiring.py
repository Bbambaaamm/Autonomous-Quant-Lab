import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

import quantlab.alpaca_event_worker as event_worker
from quantlab.config import Settings
from quantlab.market_data import AlpacaProvider, ProviderUnavailable

REPOSITORY_ROOT = Path(__file__).parents[2]


def _compose_service(name: str) -> str:
    lines = (REPOSITORY_ROOT / "docker-compose.production.yml").read_text().splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_alpaca_feed_defaults_to_iex_and_accepts_explicit_iex() -> None:
    assert Settings().alpaca_feed == "iex"
    assert Settings(alpaca_feed="iex").alpaca_feed == "iex"


def test_alpaca_feed_rejects_value_outside_allowlist() -> None:
    with pytest.raises(ValidationError, match="ALPACA_FEED není na allowlistu"):
        Settings(alpaca_feed="automatic")


def test_alpaca_feed_is_part_of_persistent_provider_lineage() -> None:
    common = (
        "key",
        "secret",
        lambda _: (),
        {"AAPL": "instrument-aapl"},
        lambda *_: (200, {}, b"{}"),
    )

    iex = AlpacaProvider(*common, feed="iex")
    sip = AlpacaProvider(*common, feed="sip")

    assert iex.metadata.name == sip.metadata.name == "alpaca"
    assert iex.metadata.version == sip.metadata.version == "5"
    assert iex.metadata.persistent_name == "alpaca:iex"
    assert sip.metadata.persistent_name == "alpaca:sip"


def test_production_compose_hardens_alpaca_event_worker() -> None:
    service = _compose_service("alpaca-events")

    assert "image: quantlab-backend" in service
    assert 'command: ["/app/backend/.venv/bin/quantlab-alpaca-events"]' in service
    assert "env_file: [.env.production]" in service
    assert "depends_on: {postgres: {condition: service_healthy}}" in service
    assert "networks: [data, market-data-egress]" in service
    assert "application" not in service
    assert "ingress" not in service
    assert "ports:" not in service
    assert "read_only: true" in service
    assert "tmpfs: [/tmp]" in service
    assert "cap_drop: [ALL]" in service
    assert "security_opt: [no-new-privileges:true]" in service
    assert 'restart: "on-failure:5"' in service


def test_production_worker_uses_bounded_idle_polling() -> None:
    service = _compose_service("worker")

    assert 'WORKER_POLL_INTERVAL: "5"' in service
    assert 'WORKER_POLL_INTERVAL: "0.2"' not in service


def test_alpaca_event_worker_exits_successfully_for_stooq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        event_worker, "get_settings", lambda: Settings(market_data_provider="stooq")
    )

    assert event_worker.main() is None


def test_alpaca_event_worker_uses_bounded_cursor_without_loading_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = type("Engine", (), {"dispose": lambda self: None})()
    cursors: list[str | None] = []

    class Service:
        def latest_corporate_action_event_id(self, provider: str) -> str | None:
            assert provider == "alpaca"
            return "event-999"

        def corporate_action_events(self, provider: str):  # type: ignore[no-untyped-def]
            pytest.fail(f"full event history must not be loaded for startup: {provider}")

        def record_corporate_action_event(self, provider, event):  # type: ignore[no-untyped-def]
            return True

    stream = type("Stream", (), {"run": lambda self, cursor: cursors.append(cursor)})()
    monkeypatch.setattr(
        event_worker,
        "get_settings",
        lambda: Settings(
            market_data_provider="alpaca", alpaca_key_id="key", alpaca_secret_key="secret"
        ),
    )
    monkeypatch.setattr(event_worker, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(event_worker, "sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(event_worker, "PersistentMarketDataService", lambda factory: Service())
    monkeypatch.setattr(event_worker, "AlpacaCorporateActionStream", lambda *args, **kwargs: stream)

    with pytest.raises(ProviderUnavailable, match="vyčerpal povolené reconnect pokusy"):
        event_worker.main()

    assert cursors == ["event-999"]


def test_listener_telemetry_reports_resource_and_event_counters() -> None:
    ticks = iter((10.0, 11.0, 16.0))
    telemetry = event_worker._ListenerTelemetry(
        logging.getLogger("test.alpaca.telemetry"),
        monotonic=lambda: next(ticks),
        cpu_clock=lambda: 2.5,
        rss_reader=lambda: 123,
        peak_reader=lambda: 456,
    )

    telemetry.record_received()
    telemetry.record_result(True)
    telemetry.record_received()
    telemetry.record_result(False)
    metrics = telemetry.snapshot()

    assert metrics == {
        "rss_current_kib": 123,
        "rss_peak_kib": 456,
        "cpu_seconds": 2.5,
        "events_received": 2,
        "events_written": 1,
        "events_duplicate": 1,
        "idle_seconds": 5.0,
    }
