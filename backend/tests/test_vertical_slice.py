from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api import app
from quantlab.data import DataValidationError, validate_bars
from quantlab.demo import load_fixture, run_demo
from quantlab.domain import Bar
from quantlab.strategy import MovingAverageStrategy

FIXTURE = Path(__file__).parent / "fixtures" / "sample_market_data.csv"


def test_vertical_slice_executes_on_next_bar_with_adverse_slippage() -> None:
    result = run_demo(FIXTURE)
    assert len(result.fills) >= 2
    first = result.fills[0]
    assert first.timestamp == datetime(2025, 1, 9, 21, tzinfo=UTC)
    assert first.price > Decimal("106")
    assert first.commission > Decimal("1")
    assert result.fills[-1].price < Decimal("92")  # Sell slippage zhoršuje cenu.
    assert result.final_value > Decimal("0")


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


def test_research_api_persists_experiment_and_exposes_report() -> None:
    client = TestClient(app)
    created = client.post("/research/experiments")
    assert created.status_code == 200
    experiment_id = created.json()["id"]
    fetched = client.get(f"/research/experiments/{experiment_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == experiment_id
    report = client.get(f"/research/experiments/{experiment_id}/report")
    assert report.status_code == 200
    assert report.json()["id"] == experiment_id
    assert "Research report" in report.json()["report"]
