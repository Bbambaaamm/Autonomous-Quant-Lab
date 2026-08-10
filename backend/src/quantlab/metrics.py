from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt

from quantlab.domain import Bar, Fill, Side


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


def _trade_pnls(fills: list[Fill]) -> tuple[list[float], list[float]]:
    entries: dict[str, tuple[Decimal, datetime]] = {}
    pnls: list[float] = []
    holding: list[float] = []
    for fill in fills:
        if fill.side is Side.BUY:
            entries[fill.symbol] = (fill.price, fill.timestamp)
        elif fill.symbol in entries:
            price, entered = entries.pop(fill.symbol)
            pnls.append(float((fill.price - price) * fill.quantity - fill.commission))
            holding.append((fill.timestamp - entered).total_seconds() / 86400)
    return pnls, holding


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
    position = Decimal("0")
    for fill in fills:
        position += fill.quantity if fill.side is Side.BUY else -fill.quantity
        exposed += int(position != 0)
    pnls, holding = _trade_pnls(fills)
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
        exposed / len(fills) if fills else 0.0,
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
