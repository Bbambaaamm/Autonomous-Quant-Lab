import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from quantlab.backtest import BacktestResult, run_backtest
from quantlab.domain import Bar
from quantlab.strategy import MovingAverageStrategy
from quantlab.trading import (
    CostModel, ExecutionEngine, FixedBpsSlippage, PaperBroker, Portfolio,
    PortfolioConstructor, RiskConfig, RiskEngine,
)


def load_fixture(path: Path) -> list[Bar]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [Bar(row["symbol"], datetime.fromisoformat(row["timestamp"]), *[Decimal(row[key]) for key in ("open", "high", "low", "close", "volume", "adjusted_close")], row["source"]) for row in csv.DictReader(handle)]


def run_demo(path: Path) -> BacktestResult:
    broker = PaperBroker(CostModel(), FixedBpsSlippage())
    execution = ExecutionEngine(RiskEngine(RiskConfig()), broker)
    return run_backtest(load_fixture(path), MovingAverageStrategy(), Portfolio(Decimal("100000")), PortfolioConstructor(), execution)
