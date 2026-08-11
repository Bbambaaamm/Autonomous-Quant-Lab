from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from quantlab.domain import (
    Bar,
    CorporateAction,
    CorporateActionType,
    Fill,
    OrderIntent,
    Side,
    TargetPosition,
)


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
    lots: dict[str, list["Lot"]] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    dividend_income: Decimal = Decimal("0")
    total_commissions: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    _applied_actions: set[tuple[str, datetime, CorporateActionType, Decimal]] = field(
        default_factory=set, repr=False
    )

    def apply(self, fill: Fill) -> None:
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
        current = self.positions.get(fill.symbol, Decimal("0"))
        if fill.side is Side.SELL and fill.quantity > current:
            raise ValueError("Nelze prodat více akcií, než portfolio drží")
        self.positions[fill.symbol] = current + signed
        self.cash -= signed * fill.price + fill.commission
        self.total_commissions += fill.commission
        self.total_slippage += fill.slippage_cost
        symbol_lots = self.lots.setdefault(fill.symbol, [])
        if fill.side is Side.BUY:
            symbol_lots.append(Lot(fill.quantity, fill.price, fill.timestamp))
            return
        remaining = fill.quantity
        while remaining:
            lot = symbol_lots[0]
            allocated = min(remaining, lot.quantity)
            self.realized_pnl += allocated * (fill.price - lot.unit_basis)
            lot.quantity -= allocated
            remaining -= allocated
            if lot.quantity == 0:
                symbol_lots.pop(0)

    def apply_corporate_action(self, action: CorporateAction) -> None:
        key = (action.symbol, action.effective_at, action.action_type, action.value)
        if key in self._applied_actions:
            return
        quantity = self.positions.get(action.symbol, Decimal("0"))
        if action.action_type is CorporateActionType.SPLIT:
            self.positions[action.symbol] = quantity * action.value
            for lot in self.lots.get(action.symbol, []):
                lot.quantity *= action.value
                lot.unit_basis /= action.value
        else:
            income = quantity * action.value
            self.cash += income
            self.dividend_income += income
        self._applied_actions.add(key)

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        return self.cash + sum(
            quantity * prices[symbol] for symbol, quantity in self.positions.items()
        )


@dataclass
class Lot:
    quantity: Decimal
    unit_basis: Decimal
    opened_at: datetime


class PortfolioConstructor:
    def __init__(self, max_target_weight: Decimal = Decimal("0.25")) -> None:
        if not Decimal("0") <= max_target_weight <= Decimal("1"):
            raise ValueError("Maximální target weight musí být v intervalu 0 až 1")
        self.max_target_weight = max_target_weight

    def create_order(
        self, target: TargetPosition, portfolio: Portfolio, price: Decimal, when: object
    ) -> OrderIntent | None:
        capped_weight = min(target.weight, self.max_target_weight)
        equity = portfolio.cash + portfolio.positions.get(target.symbol, Decimal("0")) * price
        desired = (equity * capped_weight / price).to_integral_value(rounding=ROUND_DOWN)
        current = portfolio.positions.get(target.symbol, Decimal("0"))
        delta = desired - current
        if delta == 0:
            return None
        if not isinstance(when, datetime):
            raise TypeError("Čas rozhodnutí musí být datetime")
        return OrderIntent(
            target.symbol, Side.BUY if delta > 0 else Side.SELL, abs(delta), when, target.reason
        )


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
        return Fill(
            order.id,
            order.symbol,
            order.side,
            order.quantity,
            price,
            fee,
            next_bar.timestamp,
            next_bar.open,
        )


class ExecutionEngine:
    def __init__(self, risk: RiskEngine, broker: PaperBroker) -> None:
        self.risk = risk
        self.broker = broker

    def execute(self, order: OrderIntent, next_bar: Bar) -> Fill:
        self.risk.approve(order, next_bar.open)
        return self.broker.execute(order, next_bar)
