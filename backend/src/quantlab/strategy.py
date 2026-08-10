from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from quantlab.domain import Bar, TargetPosition


class Strategy(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def required_lookback(self) -> int: ...

    def generate_target(self, available_bars: list[Bar]) -> TargetPosition: ...


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


@dataclass(frozen=True)
class BuyAndHoldStrategy:
    name: str = "buy_and_hold"
    version: str = "1.0.0"
    rebalance_frequency: str = "once"

    @property
    def required_lookback(self) -> int:
        return 1

    def generate_target(self, available_bars: list[Bar]) -> TargetPosition:
        return TargetPosition(available_bars[-1].symbol, Decimal("1"), "buy-and-hold")


@dataclass(frozen=True)
class DonchianBreakoutStrategy:
    entry_lookback: int = 20
    exit_lookback: int = 10
    name: str = "donchian_breakout"
    version: str = "1.0.0"
    rebalance_frequency: str = "daily"

    def __post_init__(self) -> None:
        if (
            self.entry_lookback < 2
            or self.exit_lookback < 1
            or self.exit_lookback >= self.entry_lookback
        ):
            raise ValueError("Platí 1 <= exit_lookback < entry_lookback")

    @property
    def required_lookback(self) -> int:
        return self.entry_lookback + 1

    def generate_target(self, available_bars: list[Bar]) -> TargetPosition:
        current = available_bars[-1].adjusted_close
        history = available_bars[:-1]
        entry = max(bar.adjusted_close for bar in history[-self.entry_lookback :])
        exit_price = min(bar.adjusted_close for bar in history[-self.exit_lookback :])
        weight = (
            Decimal("1")
            if current > entry
            else Decimal("0")
            if current < exit_price
            else Decimal("1")
        )
        return TargetPosition(available_bars[-1].symbol, weight, f"entry={entry};exit={exit_price}")
