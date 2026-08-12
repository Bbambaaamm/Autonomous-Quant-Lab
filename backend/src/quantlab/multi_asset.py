from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from quantlab.domain import Side, require_utc
from quantlab.market_data import (
    CorporateAction,
    CorporateActionKind,
    Observation,
    causal_adjusted_close,
)
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
    signal_prices: Mapping[str, tuple[Decimal, ...]]

    def __post_init__(self) -> None:
        cutoff = require_utc(self.decision_time)
        if any(
            bar.timestamp > cutoff or bar.observed_at > cutoff
            for bars in self.history.values()
            for bar in bars
        ):
            raise ValueError("Strategy context obsahuje budoucí observation")
        if any(symbol not in self.eligible_instruments for symbol in self.history):
            raise ValueError("Strategy context obsahuje asset mimo PIT universe")
        if set(self.signal_prices) != set(self.history) or any(
            len(self.signal_prices[instrument]) != len(bars)
            for instrument, bars in self.history.items()
        ):
            raise ValueError("Adjusted signal series neodpovídá causal observation history")


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
            prices = context.signal_prices.get(instrument, ())
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
            prices = context.signal_prices.get(instrument, ())
            if len(prices) >= self.required_lookback:
                ranks.append((prices[-1] / prices[-self.required_lookback] - 1, instrument))
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
            prices = context.signal_prices.get(instrument, ())
            if len(prices) >= self.lookback:
                mean = sum(prices[-self.lookback :], Decimal("0")) / self.lookback
                if prices[-1] / mean <= self.threshold:
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
    final_cash: Decimal
    final_positions: tuple[tuple[str, Decimal], ...]
    dividend_income: Decimal
    exposure: tuple[tuple[datetime, Decimal], ...] = ()


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
    corporate_actions: Sequence[CorporateAction] = (),
) -> MultiAssetResult:
    if currencies and len(set(currencies.values())) > 1:
        raise ValueError("Multi-currency portfolio bez FX konverze není podporováno")
    revisions: dict[tuple[str, datetime], list[Observation]] = {}
    for row in sorted(
        observations, key=lambda item: (item.timestamp, item.observed_at, item.revision)
    ):
        revisions.setdefault((row.instrument_id, row.timestamp), []).append(row)
    times = sorted({row.timestamp for row in observations})
    portfolio = MultiAssetPortfolio(initial_cash)
    pending: TargetPortfolio | None = None
    fills: list[MultiAssetFill] = []
    decisions: list[tuple[datetime, TargetPortfolio]] = []
    equity: list[tuple[datetime, Decimal]] = []
    exposure: list[tuple[datetime, Decimal]] = []
    last_rebalance: datetime | None = None
    last_prices: dict[str, tuple[Decimal, int]] = {}
    excluded: dict[str, str] = {}
    ordered_actions = sorted(
        corporate_actions, key=lambda action: (action.effective_at, action.action_id)
    )
    for index, when in enumerate(times):
        known_rows = {
            key: max(
                (row for row in rows if row.observed_at <= when),
                key=lambda row: (row.observed_at, row.revision),
            )
            for key, rows in revisions.items()
            if key[1] <= when and any(row.observed_at <= when for row in rows)
        }
        current = {
            instrument: row
            for (instrument, timestamp), row in known_rows.items()
            if timestamp == when
        }
        for action in ordered_actions:
            if action.effective_at <= when and action.known_at <= when:
                portfolio.apply_action(action)
        # Pending close T targets se realizují až na raw open další dostupné společné session.
        if pending is not None:
            prices = {instrument: bar.open for instrument, bar in current.items()}
            missing_positions = [
                instrument
                for instrument, quantity in portfolio.positions.items()
                if quantity and instrument not in prices
            ]
            if missing_positions:
                for instrument in missing_positions:
                    excluded[instrument] = "missing_rebalance_execution_bar"
            total = portfolio.cash + sum(
                quantity * prices[instrument]
                for instrument, quantity in portfolio.positions.items()
                if quantity and instrument in prices
            )
            desired = (
                {}
                if missing_positions
                else {
                    instrument: (total * weight / prices[instrument]).to_integral_value(
                        rounding=ROUND_DOWN
                    )
                    for instrument, weight in pending.weights
                    if instrument in prices
                }
            )
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
            if not missing_positions:
                pending = None
        for instrument, bar in current.items():
            last_prices[instrument] = (bar.close, index)
        history: dict[str, list[Observation]] = {}
        for (instrument, _), bar in sorted(known_rows.items(), key=lambda item: item[0][1]):
            history.setdefault(instrument, []).append(bar)
        eligible = universe.eligible(when)
        visible = {instrument: tuple(history.get(instrument, ())) for instrument in eligible}
        if pending is None and _rebalance(when, last_rebalance, strategy.rebalance_frequency):
            fresh = tuple(instrument for instrument in eligible if instrument in current)
            for instrument in eligible:
                if instrument not in fresh:
                    excluded[instrument] = "stale_or_missing_execution_bar"
            causal_history = {k: visible[k] for k in fresh}
            signal_prices = {
                instrument: tuple(
                    causal_adjusted_close(bars, ordered_actions, when)[bar.session_date]
                    for bar in bars
                )
                for instrument, bars in causal_history.items()
            }
            context = StrategyContext(when, causal_history, fresh, signal_prices)
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
        exposure.append((when, (value - portfolio.cash) / value if value else Decimal("0")))
    requested = len({m for when in times for m in universe.eligible(when)})
    used = len({fill.instrument_id for fill in fills})
    return MultiAssetResult(
        tuple(fills),
        tuple(decisions),
        tuple(equity),
        requested,
        used,
        tuple(sorted(excluded.items())),
        portfolio.cash,
        tuple(sorted(portfolio.positions.items())),
        portfolio.dividend_income,
        tuple(exposure),
    )
