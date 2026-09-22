import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantlab.m7_validation import (
    MEMBERSHIP_EVIDENCE,
    SYMBOLS,
    UNIVERSE_ID,
    EqualWeightMonthly,
    _memberships,
    _receipt_rows,
    canonical,
)
from quantlab.market_pipeline import MarketActionReceipt, MarketBatch, MarketTask
from quantlab.multi_asset import StrategyContext


def task(symbol: str) -> MarketTask:
    return MarketTask(
        task_id="t-" + symbol,
        batch_id="b",
        asset_id="a-" + symbol,
        instrument_id="i-" + symbol,
        symbol=symbol,
        exchange="XNYS",
        state="DONE",
        attempts=1,
        retry_at=datetime(2026, 9, 22, tzinfo=UTC),
        lease_until=None,
        lease_token=None,
        detail=None,
        bars=275,
        coverage=Decimal(1),
    )


def batch() -> MarketBatch:
    return MarketBatch(
        batch_id="b",
        snapshot_id="s",
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
        start=date(2025, 8, 17),
        end=date(2026, 9, 21),
        provider="alpaca:iex",
        actor="test",
        reason="test",
    )


def receipt(symbol: str, rows: list[list[object]]) -> MarketActionReceipt:
    payload = {
        "source": "alpaca_rest_current_inventory",
        "symbol": symbol,
        "instrument_id": "i-" + symbol,
        "request_start": "1970-01-01",
        "request_end": "9999-12-31",
        "data_quality": "all",
        "rows": rows,
    }
    raw = canonical(payload)
    return MarketActionReceipt(
        receipt_id="r-" + symbol,
        task_id="t-" + symbol,
        received_at=datetime(2026, 9, 22, tzinfo=UTC),
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
        payload_json=raw,
    )


def test_price_return_guard_accepts_only_cash_dividends_in_interval():
    row = ["cash_dividends", {"id": "d1", "ex_date": "2026-06-18", "rate": "1"}]
    assert _receipt_rows(receipt("SPY", [row]), task("SPY"), batch()) == [row]


@pytest.mark.parametrize("kind", ["forward_splits", "name_changes", "worthless_removals"])
def test_price_return_guard_rejects_discontinuities(kind):
    row = [kind, {"id": "x", "ex_date": "2026-06-18"}]
    with pytest.raises(ValueError, match="PRICE_RETURN_DISCONTINUITY"):
        _receipt_rows(receipt("SPY", [row]), task("SPY"), batch())


def test_membership_lineage_is_known_before_research_period():
    tasks = {symbol: task(symbol) for symbol in SYMBOLS}
    memberships = _memberships(tasks)
    assert {item.universe_id for item in memberships} == {UNIVERSE_ID}
    assert all(item.known_at == item.valid_from for item in memberships)
    assert all(item.valid_from.date() < date(2025, 8, 18) for item in memberships)
    assert all(MEMBERSHIP_EVIDENCE[symbol][1].startswith("https://") for symbol in SYMBOLS)


def test_equal_weight_benchmark_is_deterministic():
    strategy = EqualWeightMonthly()
    context = StrategyContext(
        datetime(2026, 1, 2, 21, tzinfo=UTC),
        {},
        ("i-SPY", "i-DIA", "i-IWM", "i-QQQ"),
        {},
    )
    target = strategy.generate_targets(context)
    assert target.weights == (
        ("i-DIA", Decimal("0.25")),
        ("i-IWM", Decimal("0.25")),
        ("i-QQQ", Decimal("0.25")),
        ("i-SPY", Decimal("0.25")),
    )


def test_receipt_hash_mismatch_fails_closed():
    item = receipt("SPY", [])
    item.content_hash = "0" * 64
    with pytest.raises(ValueError, match="ACTION_RECEIPT_HASH_MISMATCH"):
        _receipt_rows(item, task("SPY"), batch())
