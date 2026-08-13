from datetime import UTC, date, datetime
from decimal import Decimal

from quantlab.market_data import AssetType, Instrument, ProviderBar, XNYSCalendar, normalize_bar
from quantlab.multi_asset import (
    PortfolioStrategy,
    RebalanceFrequency,
    StrategyContext,
    TargetPortfolio,
    run_multi_asset,
)
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
)

CALENDAR = XNYSCalendar()


def observation(instrument_id: str, day: date, open_price: str, close_price: str):
    instrument = Instrument(
        instrument_id,
        instrument_id,
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2000, 1, 1),
    )
    return normalize_bar(
        ProviderBar(
            day,
            Decimal(open_price),
            max(Decimal(open_price), Decimal(close_price)),
            min(Decimal(open_price), Decimal(close_price)),
            Decimal(close_price),
            Decimal("10000"),
            str(day),
        ),
        instrument,
        "fixture",
        CALENDAR.session_close(day),
        "audit",
        CALENDAR,
    )


def universe(*instrument_ids: str) -> PointInTimeUniverse:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    return PointInTimeUniverse(
        UniverseDefinition("audit", "audit", UniverseKind.POINT_IN_TIME_MEMBERSHIP),
        [UniverseMembership("audit", item, known, None, known) for item in instrument_ids],
    )


class AlternatingStrategy(PortfolioStrategy):
    name = "audit_alternating"
    version = "1"
    required_lookback = 1
    rebalance_frequency = RebalanceFrequency.DAILY

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        target = (("a", Decimal("1")),) if len(context.history["a"]) == 1 else ()
        return TargetPortfolio(target, "audit")


class MonthlyStrategy(PortfolioStrategy):
    name = "audit_monthly"
    version = "1"
    required_lookback = 1
    rebalance_frequency = RebalanceFrequency.MONTHLY

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        return TargetPortfolio((("a", Decimal("1")),), "audit")


def test_close_signal_executes_at_next_session_open_and_sparse_target_liquidates():
    days = (date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    rows = [
        observation("a", day, open_price, close_price)
        for day, open_price, close_price in zip(
            days, ("10", "20", "30"), ("15", "25", "35"), strict=True
        )
    ]
    result = run_multi_asset(
        rows,
        universe("a"),
        AlternatingStrategy(),
        initial_cash=Decimal("1000"),
        commission_bps=Decimal("0"),
    )
    assert result.fills[0].timestamp == CALENDAR.session_close(days[1])
    assert result.fills[0].price == Decimal("20")
    assert result.fills[1].timestamp == CALENDAR.session_close(days[2])
    assert result.final_positions == (("a", Decimal("0")),)


def test_monthly_strategy_is_not_rebalanced_on_each_daily_session():
    days = (date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    rows = [observation("a", day, "10", "10") for day in days]
    result = run_multi_asset(rows, universe("a"), MonthlyStrategy())
    assert len(result.decisions) == 1


def test_execution_is_deferred_when_held_asset_has_no_current_open():
    days = (date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8))
    rows = [
        observation("a", days[0], "10", "10"),
        observation("a", days[1], "10", "10"),
        observation("b", days[1], "20", "20"),
        observation("b", days[2], "20", "20"),
        observation("a", days[3], "10", "10"),
        observation("b", days[3], "20", "20"),
    ]
    result = run_multi_asset(
        rows,
        universe("a", "b"),
        AlternatingStrategy(),
        initial_cash=Decimal("1000"),
        commission_bps=Decimal("0"),
        stale_sessions=3,
    )
    assert all(fill.instrument_id != "b" for fill in result.fills)
    assert ("a", "missing_rebalance_execution_bar") in result.excluded
