from pathlib import Path

import pytest
from pydantic import ValidationError

import quantlab.alpaca_event_worker as event_worker
from quantlab.config import Settings

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


def test_alpaca_event_worker_exits_successfully_for_stooq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        event_worker, "get_settings", lambda: Settings(market_data_provider="stooq")
    )

    assert event_worker.main() is None
