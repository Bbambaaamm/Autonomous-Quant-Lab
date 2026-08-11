from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from quantlab.domain import Side, require_utc
from quantlab.market_data import CorporateAction, CorporateActionKind, Observation
from quantlab.universe import PointInTimeUniverse


class RebalanceFrequency(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass(frozen=True)
class StrategyContext:
    decision_time: datetime
    history: Mapping[str, tuple[Observation, ...]]
    eligible_instruments: tuple[str, ...]

    def __post_init__(self) -> None:
        cutoff = require_utc(self.decision_time)
        if any(bar.timestamp > cutoff for bars in self.history.values() for bar in bars):
            raise ValueError("Strategy context obsahuje budoucí observation")
        if any(symbol not in self.eligible_instruments for symbol in self.history):
            raise ValueError("Strategy context obsahuje asset mimo PIT universe")


@dataclass(frozen=True)
class TargetPortfolio:
    weights: tuple[tuple[str, Decimal], ...]
    reason: str

    def __post_init__(self) -> None:
        if tuple(sorted(self.weights)) != self.weights or len(dict(self.weights)) != len(
            self.weights
        ):
            raise ValueError("Target weights musí být unikátní a deterministicky seřazené")
        if any(not weight.is_finite() or weight < 0 for _, weight in self.weights):
            raise ValueError("Long-only weights musí být konečné a nezáporné")
        if sum((weight for _, weight in self.weights), Decimal("0")) > 1:
            raise ValueError("Target překračuje povolenou gross exposure")


class PortfolioStrategy:
    name: str
    version: str
    rebalance_frequency: RebalanceFrequency

    @property
    def required_lookback(self) -> int:
        raise NotImplementedError

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        raise NotImplementedError


@dataclass(frozen=True)
class TrendStrategy(PortfolioStrategy):
    fast: int = 20
    slow: int = 100
    name: str = "multi_asset_trend"
    version: str = "1.0.0"
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.MONTHLY

    def __post_init__(self) -> None:
        if not 0 < self.fast < self.slow:
            raise ValueError("Platí 0 < fast < slow")

    @property
    def required_lookback(self) -> int:
        return self.slow

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        selected = []
        for instrument in context.eligible_instruments:
            prices = [bar.close for bar in context.history.get(instrument, ())]
            if (
                len(prices) >= self.slow
                and sum(prices[-self.fast :]) / self.fast > sum(prices[-self.slow :]) / self.slow
            ):
                selected.append(instrument)
        weight = Decimal("1") / len(selected) if selected else Decimal("0")
        return TargetPortfolio(tuple((item, weight) for item in sorted(selected)), "MA trend")


@dataclass(frozen=True)
class CrossSectionalMomentumStrategy(PortfolioStrategy):
    lookback: int = 126
    top_n: int = 3
    name: str = "cross_sectional_momentum"
    version: str = "1.0.0"
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.MONTHLY

    def __post_init__(self) -> None:
        if self.lookback < 2 or self.top_n < 1 or self.top_n > 100:
            raise ValueError("Momentum parametry jsou mimo bezpečné meze")

    @property
    def required_lookback(self) -> int:
        return self.lookback + 1

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        ranks: list[tuple[Decimal, str]] = []
        for instrument in context.eligible_instruments:
            bars = context.history.get(instrument, ())
            if len(bars) >= self.required_lookback:
                ranks.append((bars[-1].close / bars[-self.required_lookback].close - 1, instrument))
        # Vyšší výnos první, ticker-independent canonical ID řeší tie deterministicky.
        chosen = [
            instrument for _, instrument in sorted(ranks, key=lambda x: (-x[0], x[1]))[: self.top_n]
        ]
        weight = Decimal("1") / len(chosen) if chosen else Decimal("0")
        return TargetPortfolio(
            tuple(sorted((item, weight) for item in chosen)), "cross-sectional momentum"
        )


@dataclass(frozen=True)
class MeanReversionStrategy(PortfolioStrategy):
    lookback: int = 20
    threshold: Decimal = Decimal("0.95")
    name: str = "multi_asset_mean_reversion"
    version: str = "1.0.0"
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.WEEKLY

    def __post_init__(self) -> None:
        if self.lookback < 2 or not Decimal("0.5") <= self.threshold < 1:
            raise ValueError("Mean-reversion parametry jsou mimo bezpečné meze")

    @property
    def required_lookback(self) -> int:
        return self.lookback

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        selected = []
        for instrument in context.eligible_instruments:
            bars = context.history.get(instrument, ())
            if len(bars) >= self.lookback:
                mean = (
                    sum((bar.close for bar in bars[-self.lookback :]), Decimal("0")) / self.lookback
                )
                if bars[-1].close / mean <= self.threshold:
                    selected.append(instrument)
        weight = Decimal("1") / len(selected) if selected else Decimal("0")
        return TargetPortfolio(
            tuple((item, weight) for item in sorted(selected)), "distance from mean"
        )


STRATEGY_REGISTRY: Mapping[str, type[PortfolioStrategy]] = {
    "multi_asset_trend": TrendStrategy,
    "cross_sectional_momentum": CrossSectionalMomentumStrategy,
    "multi_asset_mean_reversion": MeanReversionStrategy,
}


@dataclass(frozen=True)
class MultiAssetFill:
    instrument_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    timestamp: datetime


@dataclass
class MultiAssetPortfolio:
    cash: Decimal
    currency: str = "USD"
    positions: dict[str, Decimal] = field(default_factory=dict)
    cost_basis: dict[str, Decimal] = field(default_factory=dict)
    dividend_income: Decimal = Decimal("0")
    applied_actions: set[str] = field(default_factory=set)

    def apply_fill(self, fill: MultiAssetFill) -> None:
        quantity = self.positions.get(fill.instrument_id, Decimal("0"))
        if fill.side is Side.SELL and fill.quantity > quantity:
            raise ValueError("Short selling není povolen")
        notional = fill.quantity * fill.price
        if fill.side is Side.BUY:
            if notional + fill.commission > self.cash:
                raise ValueError("Nedostatečná hotovost")
            previous_basis = self.cost_basis.get(fill.instrument_id, Decimal("0")) * quantity
            self.positions[fill.instrument_id] = quantity + fill.quantity
            self.cost_basis[fill.instrument_id] = (previous_basis + notional + fill.commission) / (
                quantity + fill.quantity
            )
            self.cash -= notional + fill.commission
        else:
            self.positions[fill.instrument_id] = quantity - fill.quantity
            self.cash += notional - fill.commission

    def apply_action(self, action: CorporateAction) -> None:
        if action.action_id in self.applied_actions:
            return
        quantity = self.positions.get(action.instrument_id, Decimal("0"))
        if action.kind is CorporateActionKind.SPLIT:
            ratio = action.value or Decimal("1")
            self.positions[action.instrument_id] = quantity * ratio
            if action.instrument_id in self.cost_basis:
                self.cost_basis[action.instrument_id] /= ratio
        elif action.kind is CorporateActionKind.CASH_DIVIDEND:
            amount = quantity * (action.value or Decimal("0"))
            self.cash += amount
            self.dividend_income += amount
        elif action.kind is CorporateActionKind.DELISTING and quantity:
            raise RuntimeError("Delisting bez explicitní executable ceny zůstává unresolved")
        self.applied_actions.add(action.action_id)


@dataclass(frozen=True)
class MultiAssetResult:
    fills: tuple[MultiAssetFill, ...]
    decisions: tuple[tuple[datetime, TargetPortfolio], ...]
    equity: tuple[tuple[datetime, Decimal], ...]
    requested_assets: int
    used_assets: int
    excluded: tuple[tuple[str, str], ...]


def _rebalance(day: datetime, previous: datetime | None, frequency: RebalanceFrequency) -> bool:
    if previous is None or frequency is RebalanceFrequency.DAILY:
        return True
    if frequency is RebalanceFrequency.WEEKLY:
        return day.isocalendar()[:2] != previous.isocalendar()[:2]
    return (day.year, day.month) != (previous.year, previous.month)


def run_multi_asset(
    observations: Sequence[Observation],
    universe: PointInTimeUniverse,
    strategy: PortfolioStrategy,
    initial_cash: Decimal = Decimal("100000"),
    commission_bps: Decimal = Decimal("1"),
    stale_sessions: int = 1,
    currencies: Mapping[str, str] | None = None,
) -> MultiAssetResult:
    if currencies and len(set(currencies.values())) > 1:
        raise ValueError("Multi-currency portfolio bez FX konverze není podporováno")
    by_time: dict[datetime, dict[str, Observation]] = {}
    for row in observations:
        by_time.setdefault(row.timestamp, {})[row.instrument_id] = row
    times = sorted(by_time)
    portfolio = MultiAssetPortfolio(initial_cash)
    history: dict[str, list[Observation]] = {}
    pending: TargetPortfolio | None = None
    fills: list[MultiAssetFill] = []
    decisions: list[tuple[datetime, TargetPortfolio]] = []
    equity: list[tuple[datetime, Decimal]] = []
    last_rebalance: datetime | None = None
    last_prices: dict[str, tuple[Decimal, int]] = {}
    excluded: dict[str, str] = {}
    for index, when in enumerate(times):
        current = by_time[when]
        # Pending close T targets se realizují až na raw open další dostupné společné session.
        if pending is not None:
            prices = {instrument: bar.open for instrument, bar in current.items()}
            values = {
                instrument: portfolio.positions.get(instrument, Decimal("0")) * price
                for instrument, price in prices.items()
            }
            total = portfolio.cash + sum(values.values())
            desired = {
                instrument: (total * weight / prices[instrument]).to_integral_value(
                    rounding=ROUND_DOWN
                )
                for instrument, weight in pending.weights
                if instrument in prices
            }
            # Pozice, které již nejsou v targetu (včetně PIT leaverů), se bezpečně uzavřou
            # pouze tehdy, když existuje čerstvý executable open; cenu nikdy nedopočítáváme.
            for instrument, quantity in portfolio.positions.items():
                if quantity and instrument in prices:
                    desired.setdefault(instrument, Decimal("0"))
            deltas = [
                (instrument, target - portfolio.positions.get(instrument, Decimal("0")))
                for instrument, target in desired.items()
            ]
            for instrument, delta in sorted(deltas, key=lambda x: (x[1] > 0, x[0])):
                if delta == 0:
                    continue
                side = Side.BUY if delta > 0 else Side.SELL
                price = prices[instrument]
                quantity = abs(delta)
                fee = quantity * price * commission_bps / Decimal("10000")
                if side is Side.BUY:
                    affordable = (
                        portfolio.cash
                        / (price * (Decimal("1") + commission_bps / Decimal("10000")))
                    ).to_integral_value(rounding=ROUND_DOWN)
                    quantity = min(quantity, affordable)
                    fee = quantity * price * commission_bps / Decimal("10000")
                if quantity:
                    fill = MultiAssetFill(instrument, side, quantity, price, fee, when)
                    portfolio.apply_fill(fill)
                    fills.append(fill)
            pending = None
        for instrument, bar in current.items():
            history.setdefault(instrument, []).append(bar)
            last_prices[instrument] = (bar.close, index)
        eligible = universe.eligible(when)
        visible = {instrument: tuple(history.get(instrument, ())) for instrument in eligible}
        if _rebalance(when, last_rebalance, strategy.rebalance_frequency):
            fresh = tuple(instrument for instrument in eligible if instrument in current)
            for instrument in eligible:
                if instrument not in fresh:
                    excluded[instrument] = "stale_or_missing_execution_bar"
            context = StrategyContext(when, {k: visible[k] for k in fresh}, fresh)
            pending = strategy.generate_targets(context)
            decisions.append((when, pending))
            last_rebalance = when
        value = portfolio.cash
        for instrument, quantity in portfolio.positions.items():
            if not quantity:
                continue
            price_info = last_prices.get(instrument)
            if price_info is None or index - price_info[1] > stale_sessions:
                raise RuntimeError("Existing position nelze ocenit kvůli stale datům")
            value += quantity * price_info[0]
        equity.append((when, value))
    requested = len({m for when in times for m in universe.eligible(when)})
    used = len({fill.instrument_id for fill in fills})
    return MultiAssetResult(
        tuple(fills),
        tuple(decisions),
        tuple(equity),
        requested,
        used,
        tuple(sorted(excluded.items())),
    )
