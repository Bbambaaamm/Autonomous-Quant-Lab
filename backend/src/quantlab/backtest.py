import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal

from quantlab.data import validate_bars
from quantlab.domain import Bar, CorporateAction, CorporateActionType, Fill
from quantlab.strategy import Strategy
from quantlab.trading import ExecutionEngine, Portfolio, PortfolioConstructor


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: Decimal
    final_value: Decimal
    total_return: Decimal
    fills: list[Fill]
    equity_curve: list[dict[str, object]]


def run_backtest(
    bars: list[Bar],
    strategy: Strategy,
    portfolio: Portfolio,
    constructor: PortfolioConstructor,
    execution: ExecutionEngine,
    corporate_actions: list[CorporateAction] | None = None,
) -> BacktestResult:
    validate_bars(bars)
    initial_cash = portfolio.cash
    fills: list[Fill] = []
    curve: list[dict[str, object]] = []
    actions: dict[datetime, list[CorporateAction]] = {}
    for action in corporate_actions or []:
        actions.setdefault(action.effective_at, []).append(action)
    # Rozhodnutí na close i se všemi příznaky z T se plní výhradně na open T+1.
    for index in range(strategy.required_lookback - 1, len(bars) - 1):
        effective_actions = sorted(
            actions.get(bars[index + 1].timestamp, []),
            key=lambda item: item.action_type is CorporateActionType.DIVIDEND,
        )
        for action in effective_actions:
            portfolio.apply_corporate_action(action)
        target = strategy.generate_target(bars[: index + 1])
        order = constructor.create_order(
            target, portfolio, bars[index].close, bars[index].timestamp
        )
        if order is not None:
            stable_id = hashlib.sha256(
                f"{strategy.name}|{bars[index].timestamp.isoformat()}|{order.symbol}|{order.side}|{order.quantity}".encode()
            ).hexdigest()
            order = replace(order, id=stable_id)
            fill = execution.execute(order, bars[index + 1])
            portfolio.apply(fill)
            fills.append(fill)
        market_value = (
            portfolio.positions.get(bars[index + 1].symbol, Decimal("0")) * bars[index + 1].close
        )
        value = portfolio.cash + market_value
        curve.append(
            {
                "timestamp": bars[index + 1].timestamp,
                "cash": portfolio.cash,
                "market_value": market_value,
                "portfolio_value": value,
            }
        )
    final_value = curve[-1]["portfolio_value"] if curve else initial_cash
    assert isinstance(final_value, Decimal)
    return BacktestResult(initial_cash, final_value, final_value / initial_cash - 1, fills, curve)


def serialize_result(result: BacktestResult) -> dict[str, object]:
    return asdict(result)
