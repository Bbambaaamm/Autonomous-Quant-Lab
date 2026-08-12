from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantlab.domain import Side
from quantlab.multi_asset import MultiAssetFill, MultiAssetResult
from quantlab.phase6_runtime import multi_asset_metrics


def test_multi_asset_metrics_use_time_weighted_exposure_and_hand_calculated_costs() -> None:
    start = datetime(2026, 1, 2, 21, tzinfo=UTC)
    result = MultiAssetResult(
        fills=(
            MultiAssetFill("A", Side.BUY, Decimal(10), Decimal(50), Decimal(1), start),
            MultiAssetFill(
                "A", Side.SELL, Decimal(10), Decimal(55), Decimal(1), start + timedelta(days=1)
            ),
        ),
        decisions=(),
        equity=(
            (start, Decimal(999)),
            (start + timedelta(days=1), Decimal(1048)),
            (start + timedelta(days=2), Decimal(1048)),
        ),
        requested_assets=1,
        used_assets=1,
        excluded=(),
        final_cash=Decimal(1048),
        final_positions=(("A", Decimal(0)),),
        dividend_income=Decimal(0),
        exposure=(
            (start, Decimal(500) / Decimal(999)),
            (start + timedelta(days=1), Decimal(0)),
            (start + timedelta(days=2), Decimal(0)),
        ),
    )

    metrics = multi_asset_metrics(result, Decimal(1000))

    assert metrics.total_return == Decimal("0.048")
    assert metrics.turnover == Decimal("1.05")
    assert metrics.trade_count == 2
    assert metrics.total_costs == Decimal(2)
    # První interval je investováno 500/999, druhý interval je po prodeji v hotovosti.
    assert metrics.time_weighted_exposure == (Decimal(500) / Decimal(999)) / 2
    assert metrics.max_drawdown == 0
