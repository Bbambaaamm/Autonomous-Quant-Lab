import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.backtest import run_backtest
from quantlab.data import CSVMarketDataProvider, USExchangeCalendar, dataset_identity, inspect_bars
from quantlab.domain import Bar, CorporateAction, CorporateActionType, Fill, Side
from quantlab.metrics import benchmark_metrics, calculate_metrics
from quantlab.persistence import RunRepository
from quantlab.research import (
    AnalysisStatus,
    CostScenario,
    EligibilityDecision,
    ExperimentIdentity,
    ParameterStability,
    WalkForwardConfig,
    chronological_split,
    cost_stress,
    evaluate_eligibility,
    guarded_monte_carlo,
    monte_carlo_trade_returns,
    objective_score,
    parameter_grid,
    parameter_run_sort_key,
    parameter_stability,
    run_cost_stress_backtests,
    walk_forward_folds,
)
from quantlab.research_service import ResearchService
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


def test_aggregate_benchmark_uses_only_oos_bars() -> None:
    source = bars(15)
    split = chronological_split(source, 0.4, 0.2)
    baseline = benchmark_metrics(list(split.test))
    changed_train = list(source)
    first = changed_train[0]
    changed_train[0] = Bar(
        first.symbol,
        first.timestamp,
        Decimal("1"),
        Decimal("2"),
        Decimal("1"),
        Decimal("1"),
        first.volume,
        Decimal("1"),
        first.source,
    )
    assert benchmark_metrics(list(chronological_split(changed_train, 0.4, 0.2).test)) == baseline
    changed_oos = list(source)
    last = changed_oos[-1]
    changed_oos[-1] = Bar(
        last.symbol,
        last.timestamp,
        Decimal("500"),
        Decimal("501"),
        Decimal("499"),
        Decimal("500"),
        last.volume,
        Decimal("500"),
        last.source,
    )
    assert benchmark_metrics(list(chronological_split(changed_oos, 0.4, 0.2).test)) != baseline


def test_eligibility_thresholds_are_inclusive() -> None:
    from quantlab.research import EligibilityConfig

    config = EligibilityConfig(
        minimum_trades=3,
        minimum_oos_return=0.1,
        maximum_drawdown=0.2,
        minimum_profitable_folds=0.6,
        minimum_profitable_neighbors=0.5,
    )
    result = evaluate_eligibility(
        3,
        0.1,
        -0.2,
        {"base": 0.01},
        0.6,
        ParameterStability(2, 0.1, 0.0, 0.5),
        config,
    )
    assert result.decision is EligibilityDecision.PAPER_CANDIDATE


def test_objective_policy_handles_missing_sharpe_and_ties_deterministically() -> None:
    from quantlab.research import ObjectiveConfig

    assert objective_score(0.1, None, -0.2, 3) == pytest.approx(0.0)
    assert objective_score(0.1, 1.0, -0.2, 2, ObjectiveConfig(minimum_trades=3)) is None
    ordered = sorted(
        [(0.5, {"slow": 4}), (0.5, {"slow": 3})],
        key=lambda item: parameter_run_sort_key(*item),
        reverse=True,
    )
    assert ordered == [(0.5, {"slow": 4}), (0.5, {"slow": 3})]


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


def test_unified_hand_calculated_fifo_accounting_and_equity_invariant() -> None:
    source = bars(6)
    portfolio = Portfolio(Decimal("1000"))
    fills = [
        Fill(
            "b1",
            "SPY",
            Side.BUY,
            Decimal("10"),
            Decimal("11"),
            Decimal("2"),
            source[0].timestamp,
            Decimal("10"),
        ),
        Fill(
            "b2",
            "SPY",
            Side.BUY,
            Decimal("10"),
            Decimal("12"),
            Decimal("2"),
            source[1].timestamp,
            Decimal("11"),
        ),
    ]
    for fill in fills:
        portfolio.apply(fill)
        assert (
            portfolio.equity({"SPY": fill.price})
            == portfolio.cash + portfolio.positions["SPY"] * fill.price
        )
    split = CorporateAction("SPY", source[2].timestamp, CorporateActionType.SPLIT, Decimal("2"))
    portfolio.apply_corporate_action(split)
    portfolio.apply_corporate_action(split)  # stejná akce je idempotentní
    assert portfolio.positions["SPY"] == Decimal("40")
    assert [(lot.quantity, lot.unit_basis) for lot in portfolio.lots["SPY"]] == [
        (Decimal("20"), Decimal("5.5")),
        (Decimal("20"), Decimal("6")),
    ]
    dividend = CorporateAction(
        "SPY", source[3].timestamp, CorporateActionType.DIVIDEND, Decimal("1")
    )
    portfolio.apply_corporate_action(dividend)
    portfolio.apply_corporate_action(dividend)
    assert portfolio.dividend_income == Decimal("40")
    exits = [
        Fill(
            "s1",
            "SPY",
            Side.SELL,
            Decimal("15"),
            Decimal("7.5"),
            Decimal("2"),
            source[4].timestamp,
            Decimal("8"),
        ),
        Fill(
            "s2",
            "SPY",
            Side.SELL,
            Decimal("25"),
            Decimal("8.5"),
            Decimal("3"),
            source[5].timestamp,
            Decimal("9"),
        ),
    ]
    portfolio.apply(exits[0])
    assert [(lot.quantity, lot.unit_basis) for lot in portfolio.lots["SPY"]] == [
        (Decimal("5"), Decimal("5.5")),
        (Decimal("20"), Decimal("6")),
    ]
    portfolio.apply(exits[1])
    portfolio.apply_corporate_action(
        CorporateAction("SPY", source[5].timestamp, CorporateActionType.DIVIDEND, Decimal("2"))
    )
    assert portfolio.cash == Decimal("1126")
    assert portfolio.realized_pnl == Decimal("95")
    assert portfolio.dividend_income == Decimal("40")
    assert portfolio.total_commissions == Decimal("9")
    assert portfolio.total_slippage == Decimal("40")
    assert portfolio.equity({"SPY": Decimal("9")}) == Decimal("1126")
    # Reference-price trading P&L 135 - slippage 40 - commissions 9 + dividends 40.
    assert portfolio.cash - Decimal("1000") == Decimal("135") - Decimal("40") - Decimal(
        "9"
    ) + Decimal("40")


def test_cost_stress_rebacktest_changes_later_quantity_and_equity_path() -> None:
    class AlternatingStrategy:
        name = "alternating"
        version = "1"
        required_lookback = 1

        def generate_target(self, available_bars: list[Bar]):
            from quantlab.domain import TargetPosition

            weights = {
                Decimal("1"): Decimal("1"),
                Decimal("2"): Decimal("0"),
                Decimal("3"): Decimal("1"),
            }
            return TargetPosition("SPY", weights[available_bars[-1].adjusted_close], "test")

    start = datetime(2025, 1, 1, 21, tzinfo=UTC)
    opens = list(map(Decimal, ["100", "100", "100", "83"]))
    closes = list(map(Decimal, ["100", "100", "83", "83"]))
    source = [
        Bar(
            "SPY",
            start + timedelta(days=i),
            open_price,
            max(open_price, closes[i]),
            min(open_price, closes[i]),
            closes[i],
            Decimal("1000"),
            Decimal(i + 1),
            "test",
        )
        for i, open_price in enumerate(opens)
    ]
    results = run_cost_stress_backtests(
        source,
        AlternatingStrategy(),
        Decimal("1000"),
        CostModel(fixed=Decimal("1"), rate=Decimal("0"), minimum=Decimal("1")),
        FixedBpsSlippage(Decimal("0")),
        (CostScenario("base", 1, 1), CostScenario("stressed", 20, 1)),
    )
    assert results["base"].fills[-1].quantity == Decimal("3")
    assert results["stressed"].fills[-1].quantity == Decimal("2")
    assert results["base"].equity_curve[1]["cash"] == Decimal("998")
    assert results["stressed"].equity_curve[1]["cash"] == Decimal("960")
    assert results["base"].equity_curve != results["stressed"].equity_curve
    for result in results.values():
        assert all(
            point["portfolio_value"] == point["cash"] + point["market_value"]
            for point in result.equity_curve
        )


@pytest.mark.parametrize("trade_count", [0, 1, 2])
def test_monte_carlo_guardrail_below_minimum(trade_count: int) -> None:
    result = guarded_monte_carlo([0.01] * trade_count, 1000, min_trades=3, simulations=10)
    assert result.status is AnalysisStatus.NOT_EVALUATED
    assert result.reason == "insufficient_closed_trades"


def test_monte_carlo_runs_at_minimum_boundary() -> None:
    result = guarded_monte_carlo([0.01] * 3, 1000, min_trades=3, simulations=10)
    assert result.status is AnalysisStatus.COMPLETED and result.result is not None


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


def test_research_report_is_complete_and_experiment_is_deterministic() -> None:
    service = ResearchService(RunRepository())
    first = service.create_demo_experiment(FIXTURE)
    second = service.create_demo_experiment(FIXTURE)
    assert first == second
    assert service.get(str(first["id"])) == service.get(str(second["id"]))
    report = str(first["result"]["report"])
    payload = json.loads(report.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert {
        "dataset",
        "strategy",
        "version",
        "parameter_space",
        "selected_parameters",
        "walk_forward",
        "metrics",
        "benchmark",
        "cost_stress",
        "monte_carlo",
        "parameter_stability",
        "eligibility",
    } <= payload.keys()
    assert payload["monte_carlo"]["status"] == "NOT_EVALUATED"
    assert payload["monte_carlo"]["reason"] == "insufficient_closed_trades"
    assert len(payload["walk_forward"]) == 1
    assert payload["walk_forward"][0]["validation_runs"] == 2
