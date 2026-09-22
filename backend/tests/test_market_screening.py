from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quantlab.market_data import CorporateAction, CorporateActionKind, Observation, XNYSCalendar
from quantlab.market_screening import evaluate_screen

NOW = datetime(2026, 9, 21, 22, tzinfo=UTC)
DAYS = XNYSCalendar().sessions_between(date(2026, 1, 2), date(2026, 9, 21))


def observations():
    return [
        Observation(
            str(i),
            "asset",
            "alpaca:iex",
            "1d",
            d,
            XNYSCalendar().session_close(d),
            Decimal(100),
            Decimal(100),
            Decimal(100),
            Decimal(100),
            Decimal(20000),
            NOW,
            str(i),
            str(i),
            "import",
        )
        for i, d in enumerate(DAYS)
    ]


def test_valid_screen_is_not_a_research_or_trading_approval():
    report = evaluate_screen(observations(), [], DAYS, NOW, "receipt")
    assert report["eligible"]
    assert report["research_eligible"] is False
    assert report["momentum"] == "0"
    assert report["policy"]["volume_scope"] == "configured_feed_only"


def test_future_receipts_and_recent_gaps_are_rejected():
    rows = observations()
    rows[-1] = replace(rows[-1], observed_at=NOW + timedelta(seconds=1))
    report = evaluate_screen(rows, [], DAYS, NOW, "receipt")
    assert not report["eligible"]
    assert "RECENT_GAPS" in report["reasons"]
    assert report["momentum"] is None
    assert report["expected_sessions"] == len(DAYS)
    assert report["bars"] == len(DAYS) - 1


def test_split_uses_only_known_and_effective_adjustments():
    rows = observations()
    rows = [replace(o, close=Decimal(200)) if i < len(rows) - 10 else o for i, o in enumerate(rows)]
    action = CorporateAction(
        "split",
        "asset",
        CorporateActionKind.SPLIT,
        XNYSCalendar().session_open(DAYS[-10]),
        NOW,
        Decimal(2),
    )
    report = evaluate_screen(rows, [action], DAYS, NOW, "receipt")
    assert report["momentum"] == "0"
    future = replace(action, known_at=NOW + timedelta(seconds=1))
    assert evaluate_screen(rows, [future], DAYS, NOW, "receipt")["momentum"] == "-0.5"


def test_absent_action_evidence_and_low_liquidity_exclude():
    rows = [replace(o, volume=Decimal(1)) for o in observations()]
    report = evaluate_screen(rows, [], DAYS, NOW, None)
    assert set(report["reasons"]) == {"ACTIONS_NOT_VERIFIED", "LOW_FEED_LIQUIDITY"}
    assert report["trend"] is None


def test_current_inventory_never_becomes_historical_readiness_or_future_knowledge():
    report = evaluate_screen(
        observations(),
        [],
        DAYS,
        NOW,
        None,
        inventory_id="current-rest-receipt",
        inventory_received_at=NOW,
    )
    assert report["eligible"]
    assert report["research_eligible"] is False
    assert report["action_readiness_id"] is None
    assert report["action_evidence_source"] == "REST_CURRENT_SNAPSHOT"
    future = evaluate_screen(
        observations(),
        [],
        DAYS,
        NOW,
        None,
        inventory_id="current-rest-receipt",
        inventory_received_at=NOW + timedelta(seconds=1),
    )
    assert not future["eligible"]
    assert future["current_action_receipt_id"] is None
    assert "ACTIONS_NOT_VERIFIED" in future["reasons"]
