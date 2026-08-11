from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt

from quantlab.domain import Bar, Fill, Side


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    quantity: Decimal
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    net_pnl: Decimal

    @property
    def net_return(self) -> float:
        basis = self.entry_price * self.quantity
        return float(self.net_pnl / basis) if basis else 0.0


@dataclass(frozen=True)
class PerformanceMetrics:
    total_return: float
    cagr: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    maximum_drawdown: float
    drawdown_duration: int
    calmar_ratio: float | None
    win_rate: float | None
    profit_factor: float | None
    average_win: float | None
    average_loss: float | None
    expectancy: float | None
    exposure: float
    turnover: float
    number_of_trades: int
    average_holding_period: float | None
    total_commissions: float
    total_slippage_cost: float


def fifo_closed_trades(fills: list[Fill]) -> list[ClosedTrade]:
    """Autoritativní FIFO ledger uzavřených částí lotů včetně alokovaných komisí."""
    entries: dict[str, list[tuple[Decimal, Decimal, datetime, Decimal]]] = {}
    trades: list[ClosedTrade] = []
    for fill in fills:
        if fill.side is Side.BUY:
            entries.setdefault(fill.symbol, []).append(
                (fill.quantity, fill.price, fill.timestamp, fill.commission)
            )
            continue
        remaining = fill.quantity
        lots = entries.get(fill.symbol, [])
        if remaining > sum((lot[0] for lot in lots), Decimal("0")):
            raise ValueError("FIFO ledger zjistil prodej bez dostatečného otevřeného lotu")
        while remaining:
            quantity, price, opened_at, entry_commission = lots[0]
            allocated = min(remaining, quantity)
            entry_fee = entry_commission * allocated / quantity
            exit_fee = fill.commission * allocated / fill.quantity
            trades.append(
                ClosedTrade(
                    fill.symbol,
                    allocated,
                    opened_at,
                    fill.timestamp,
                    price,
                    fill.price,
                    allocated * (fill.price - price) - entry_fee - exit_fee,
                )
            )
            remaining -= allocated
            quantity -= allocated
            if quantity:
                lots[0] = (quantity, price, opened_at, entry_commission - entry_fee)
            else:
                lots.pop(0)
    return trades


def calculate_metrics(
    initial: Decimal, values: list[tuple[datetime, Decimal]], fills: list[Fill]
) -> PerformanceMetrics:
    final = values[-1][1] if values else initial
    total_return = float(final / initial - 1)
    returns = [
        float(current / previous - 1)
        for (_, previous), (_, current) in zip(values, values[1:], strict=False)
        if previous != 0
    ]
    years = (
        (values[-1][0] - values[0][0]).total_seconds() / (365.25 * 86400) if len(values) > 1 else 0
    )
    cagr = (
        float((final / initial) ** (Decimal(1) / Decimal(str(years))) - 1)
        if years > 0 and final > 0
        else None
    )
    mean = sum(returns) / len(returns) if returns else None
    variance = (
        sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        if mean is not None and len(returns) > 1
        else None
    )
    volatility = sqrt(variance) * sqrt(252) if variance is not None else None
    sharpe = (
        mean / sqrt(variance) * sqrt(252)
        if mean is not None and variance and variance > 0
        else None
    )
    downside = [min(item, 0.0) ** 2 for item in returns]
    downside_deviation = sqrt(sum(downside) / len(downside)) if downside else 0
    sortino = (
        mean / downside_deviation * sqrt(252)
        if mean is not None and downside_deviation > 0
        else None
    )
    peak = initial
    max_dd = 0.0
    duration = longest = 0
    exposed = 0
    for _, value in values:
        peak = max(peak, value)
        drawdown = float(value / peak - 1)
        max_dd = min(max_dd, drawdown)
        duration = duration + 1 if drawdown < 0 else 0
        longest = max(longest, duration)
    # Expozice je podíl oceňovacích období s otevřenou pozicí, nikoli podíl fillů.
    # Původní výpočet dával například buy-and-hold bez výstupu vždy 100 %, i kdyby
    # vstup nastal až na posledním baru, a scale-in jej dále zkresloval.
    positions: dict[str, Decimal] = {}
    fill_index = 0
    chronological_fills = sorted(fills, key=lambda fill: fill.timestamp)
    for timestamp, _ in values:
        while (
            fill_index < len(chronological_fills)
            and chronological_fills[fill_index].timestamp <= timestamp
        ):
            fill = chronological_fills[fill_index]
            signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
            positions[fill.symbol] = positions.get(fill.symbol, Decimal("0")) + signed
            fill_index += 1
        exposed += int(any(quantity != 0 for quantity in positions.values()))
    closed_trades = fifo_closed_trades(fills)
    pnls = [float(trade.net_pnl) for trade in closed_trades]
    holding = [
        (trade.closed_at - trade.opened_at).total_seconds() / 86400 for trade in closed_trades
    ]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    return PerformanceMetrics(
        total_return,
        cagr,
        volatility,
        sharpe,
        sortino,
        max_dd,
        longest,
        cagr / abs(max_dd) if cagr is not None and max_dd < 0 else None,
        len(wins) / len(pnls) if pnls else None,
        gross_profit / gross_loss if gross_loss > 0 else None,
        sum(wins) / len(wins) if wins else None,
        sum(losses) / len(losses) if losses else None,
        sum(pnls) / len(pnls) if pnls else None,
        exposed / len(values) if values else 0.0,
        sum(float(fill.price * fill.quantity) for fill in fills) / float(initial),
        len(pnls),
        sum(holding) / len(holding) if holding else None,
        sum(float(fill.commission) for fill in fills),
        sum(float(fill.slippage_cost) for fill in fills),
    )


def benchmark_metrics(bars: list[Bar]) -> dict[str, float | None]:

    if len(bars) < 2:
        return {"total_return": None, "cagr": None, "maximum_drawdown": None}
    values = [(bar.timestamp, bar.adjusted_close) for bar in bars]
    result = calculate_metrics(values[0][1], values, [])
    return {
        "total_return": result.total_return,
        "cagr": result.cagr,
        "maximum_drawdown": result.maximum_drawdown,
    }
