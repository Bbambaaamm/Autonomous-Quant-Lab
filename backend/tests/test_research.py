from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.backtest import run_backtest
from quantlab.data import CSVMarketDataProvider, USExchangeCalendar, dataset_identity, inspect_bars
from quantlab.domain import Bar, CorporateAction, CorporateActionType, Fill, Side
from quantlab.metrics import benchmark_metrics, calculate_metrics
from quantlab.research import (
    EligibilityDecision,
    ExperimentIdentity,
    ParameterStability,
    WalkForwardConfig,
    chronological_split,
    cost_stress,
    evaluate_eligibility,
    monte_carlo_trade_returns,
    parameter_grid,
    parameter_stability,
    walk_forward_folds,
)
from quantlab.strategy import BuyAndHoldStrategy, DonchianBreakoutStrategy, MovingAverageStrategy
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

FIXTURE = Path(__file__).parent / "fixtures" / "sample_market_data.csv"


def bars(count: int = 20) -> list[Bar]:
    start = datetime(2025, 1, 1, 21, tzinfo=UTC)
    return [
        Bar(
            "SPY",
            start + timedelta(days=index),
            Decimal(100 + index),
            Decimal(102 + index),
            Decimal(99 + index),
            Decimal(101 + index),
            Decimal("1000"),
            Decimal(101 + index),
            "test",
        )
        for index in range(count)
    ]


def test_provider_identity_and_quality_events() -> None:
    loaded = CSVMarketDataProvider(FIXTURE).load("SPY")
    assert dataset_identity(loaded) == dataset_identity(list(loaded))
    assert len(dataset_identity(loaded)) == 64
    duplicate = inspect_bars([loaded[0], loaded[0]])
    assert {event.code for event in duplicate} >= {"duplicate_timestamp", "non_monotonic"}
    missing = inspect_bars([bars(1)[0], bars(4)[-1]], USExchangeCalendar())
    assert any(event.code == "missing_bar" and event.severity == "warning" for event in missing)
    assert not USExchangeCalendar(frozenset({date(2025, 1, 1)})).is_session(date(2025, 1, 1))


def test_split_and_walk_forward_boundaries() -> None:
    source = bars(20)
    split = chronological_split(source, 0.5, 0.25)
    assert split.train[-1].timestamp < split.validation[0].timestamp < split.test[0].timestamp
    folds = walk_forward_folds(source, WalkForwardConfig(8, 4, 4, 4))
    assert len(folds) == 2
    assert folds[0].train_end < folds[0].validation_start
    assert folds[0].validation_end < folds[0].test_start


def test_parameter_validation_identity_and_stability() -> None:
    grid = parameter_grid(
        {"slow": [3, 4], "fast": [1, 3]}, lambda item: item["fast"] < item["slow"]
    )
    assert {tuple(sorted(item.items())) for item in grid} == {
        (("fast", 1), ("slow", 3)),
        (("fast", 1), ("slow", 4)),
        (("fast", 3), ("slow", 4)),
    }
    with pytest.raises(ValueError):
        MovingAverageStrategy(4, 3)
    identity = ExperimentIdentity(
        "ma", "1", {"slow": 3}, "abc", "a", "b", {"rate": "0.1"}, {"bps": "5"}, 42
    )
    assert identity.experiment_id == identity.experiment_id
    stability = parameter_stability({(2, 5): 0.1, (2, 6): 0.2, (3, 5): -0.1}, (2, 5))
    assert stability.neighbor_count == 2
    assert stability.profitable_fraction == 0.5


def test_monte_carlo_cost_stress_and_eligibility_are_deterministic() -> None:
    first = monte_carlo_trade_returns([0.1, -0.05, 0.02], 1000, 100, 7)
    assert first == monte_carlo_trade_returns([0.1, -0.05, 0.02], 1000, 100, 7)
    stressed = cost_stress(0.10, 2, 3, 1000)
    assert stressed["adverse_combined"] < stressed["base"]
    result = evaluate_eligibility(
        25, 0.1, -0.1, stressed, 0.8, ParameterStability(2, 0.1, 0.01, 1.0)
    )
    assert result.decision is EligibilityDecision.PAPER_CANDIDATE


def test_metrics_edge_cases_and_benchmark_alignment() -> None:
    source = bars(3)
    values = [(bar.timestamp, bar.close) for bar in source]
    metrics = calculate_metrics(Decimal("101"), values, [])
    assert metrics.total_return == pytest.approx(2 / 101)
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert benchmark_metrics(source)["total_return"] == pytest.approx(2 / 101)


def test_corporate_actions_and_accounting() -> None:
    portfolio = Portfolio(Decimal("100"), {"SPY": Decimal("2")})
    portfolio.apply_corporate_action(
        CorporateAction("SPY", bars()[1].timestamp, CorporateActionType.SPLIT, Decimal("2"))
    )
    assert portfolio.positions["SPY"] == 4
    portfolio.apply_corporate_action(
        CorporateAction("SPY", bars()[2].timestamp, CorporateActionType.DIVIDEND, Decimal("1.5"))
    )
    assert portfolio.cash == 106
    fill = Fill(
        "x",
        "SPY",
        Side.BUY,
        Decimal("2"),
        Decimal("11"),
        Decimal("1"),
        bars()[1].timestamp,
        Decimal("10"),
    )
    portfolio = Portfolio(Decimal("100"))
    portfolio.apply(fill)
    assert portfolio.cash == 77 and portfolio.positions["SPY"] == 2 and fill.slippage_cost == 2


def test_baselines_and_reproducible_backtest() -> None:
    source = bars(25)
    assert BuyAndHoldStrategy().generate_target(source[:1]).weight == 1
    assert DonchianBreakoutStrategy(3, 2).generate_target(source[:4]).weight == 1

    def execute():
        engine = ExecutionEngine(
            RiskEngine(
                RiskConfig(max_position_weight=Decimal("1"), max_order_notional=Decimal("100000"))
            ),
            PaperBroker(CostModel(), FixedBpsSlippage()),
        )
        return run_backtest(
            source,
            MovingAverageStrategy(2, 3),
            Portfolio(Decimal("100000")),
            PortfolioConstructor(),
            engine,
        )

    assert execute() == execute()
