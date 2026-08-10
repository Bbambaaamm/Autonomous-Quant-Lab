from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from quantlab.domain import Bar, Fill, OrderIntent, Side, TargetPosition


@dataclass(frozen=True)
class RiskConfig:
    max_position_weight: Decimal = Decimal("0.25")
    max_order_notional: Decimal = Decimal("30000")
    allowed_symbols: frozenset[str] = frozenset({"SPY"})
    kill_switch: bool = False


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def approve(self, order: OrderIntent, reference_price: Decimal) -> None:
        if self.config.kill_switch:
            raise PermissionError("Risk kill switch je aktivní")
        if order.symbol not in self.config.allowed_symbols:
            raise PermissionError("Instrument není na allowlistu")
        if order.quantity <= 0 or order.quantity * reference_price > self.config.max_order_notional:
            raise PermissionError("Příkaz překračuje risk limit")


@dataclass
class Portfolio:
    cash: Decimal
    positions: dict[str, Decimal] = field(default_factory=dict)

    def apply(self, fill: Fill) -> None:
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
        self.positions[fill.symbol] = self.positions.get(fill.symbol, Decimal("0")) + signed
        self.cash -= signed * fill.price + fill.commission


class PortfolioConstructor:
    def create_order(
        self, target: TargetPosition, portfolio: Portfolio, price: Decimal, when: object
    ) -> OrderIntent | None:
        capped_weight = min(target.weight, Decimal("0.25"))
        equity = portfolio.cash + portfolio.positions.get(target.symbol, Decimal("0")) * price
        desired = (equity * capped_weight / price).to_integral_value(rounding=ROUND_DOWN)
        current = portfolio.positions.get(target.symbol, Decimal("0"))
        delta = desired - current
        if delta == 0:
            return None
        from datetime import datetime

        if not isinstance(when, datetime):
            raise TypeError("Čas rozhodnutí musí být datetime")
        return OrderIntent(target.symbol, Side.BUY if delta > 0 else Side.SELL, abs(delta), when, target.reason)


@dataclass(frozen=True)
class CostModel:
    fixed: Decimal = Decimal("1")
    rate: Decimal = Decimal("0.001")
    minimum: Decimal = Decimal("1")

    def commission(self, notional: Decimal) -> Decimal:
        return max(self.minimum, self.fixed + notional * self.rate)


@dataclass(frozen=True)
class FixedBpsSlippage:
    basis_points: Decimal = Decimal("5")

    def apply(self, price: Decimal, side: Side) -> Decimal:
        direction = Decimal("1") if side is Side.BUY else Decimal("-1")
        return price * (Decimal("1") + direction * self.basis_points / Decimal("10000"))


class PaperBroker:
    def __init__(self, costs: CostModel, slippage: FixedBpsSlippage) -> None:
        self.costs = costs
        self.slippage = slippage

    def execute(self, order: OrderIntent, next_bar: Bar) -> Fill:
        if next_bar.timestamp <= order.decision_time:
            raise ValueError("Fill musí nastat po čase rozhodnutí")
        price = self.slippage.apply(next_bar.open, order.side)
        fee = self.costs.commission(price * order.quantity)
        return Fill(order.id, order.symbol, order.side, order.quantity, price, fee, next_bar.timestamp)


class ExecutionEngine:
    def __init__(self, risk: RiskEngine, broker: PaperBroker) -> None:
        self.risk = risk
        self.broker = broker

    def execute(self, order: OrderIntent, next_bar: Bar) -> Fill:
        self.risk.approve(order, next_bar.open)
        return self.broker.execute(order, next_bar)
