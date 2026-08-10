import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from quantlab.backtest import BacktestResult, run_backtest
from quantlab.domain import Bar
from quantlab.strategy import MovingAverageStrategy
from quantlab.trading import (
    CostModel,
    ExecutionEngine,
    FixedBpsSlippage,
    PaperBroker,
    Portfolio,
    PortfolioConstructor,
    RiskConfig,
    RiskEngine,
)


def load_fixture(path: Path) -> list[Bar]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Bar(
                symbol=row["symbol"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                adjusted_close=Decimal(row["adjusted_close"]),
                source=row["source"],
            )
            for row in csv.DictReader(handle)
        ]


def run_demo(path: Path) -> BacktestResult:
    broker = PaperBroker(CostModel(), FixedBpsSlippage())
    execution = ExecutionEngine(RiskEngine(RiskConfig()), broker)
    return run_backtest(
        load_fixture(path),
        MovingAverageStrategy(),
        Portfolio(Decimal("100000")),
        PortfolioConstructor(),
        execution,
    )
