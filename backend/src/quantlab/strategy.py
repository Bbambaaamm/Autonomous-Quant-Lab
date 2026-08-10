from dataclasses import dataclass
from decimal import Decimal

from quantlab.domain import Bar, TargetPosition


@dataclass(frozen=True)
class MovingAverageStrategy:
    fast_window: int = 3
    slow_window: int = 5
    name: str = "moving_average"
    version: str = "1.0.0"
    rebalance_frequency: str = "daily"

    def __post_init__(self) -> None:
        if self.fast_window <= 0 or self.fast_window >= self.slow_window:
            raise ValueError("Platí 0 < fast_window < slow_window")

    @property
    def required_lookback(self) -> int:
        return self.slow_window

    def generate_target(self, available_bars: list[Bar]) -> TargetPosition:
        if len(available_bars) < self.required_lookback:
            return TargetPosition(available_bars[-1].symbol, Decimal("0"), "nedostatečný lookback")
        prices = [bar.adjusted_close for bar in available_bars]
        fast = sum(prices[-self.fast_window :]) / self.fast_window
        slow = sum(prices[-self.slow_window :]) / self.slow_window
        weight = Decimal("1") if fast > slow else Decimal("0")
        return TargetPosition(available_bars[-1].symbol, weight, f"fast={fast};slow={slow}")
