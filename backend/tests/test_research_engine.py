from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantlab.domain import Bar, Fill, Side
from quantlab.metrics import fifo_closed_trades
from quantlab.research import ObjectiveConfig, WalkForwardConfig
from quantlab.research_engine import (
    ParameterRunStatus,
    ParameterSpace,
    ResearchConfig,
    ResearchExperimentRunner,
)
from quantlab.strategy import MovingAverageStrategyFactory


def market_bars(count: int = 44) -> list[Bar]:
    start = datetime(2024, 1, 1, 21, tzinfo=UTC)
    closes = [Decimal(100 + (index % 7) * 3 - (index % 3) * 2) for index in range(count)]
    return [
        Bar(
            "SPY",
            start + timedelta(days=index),
            close,
            close + 2,
            close - 2,
            close,
            Decimal("10000"),
            close,
            "deterministic-test",
        )
        for index, close in enumerate(closes)
    ]


def setup() -> tuple[ParameterSpace, ResearchConfig]:
    space = ParameterSpace(
        {"slow_window": (4, 5), "fast_window": (2, 4)},
        lambda config: (
            int(config["fast_window"]) < int(config["slow_window"]),
            "fast_must_be_less_than_slow",
        ),
    )
    config = ResearchConfig(
        WalkForwardConfig(12, 8, 8, 8),
        ObjectiveConfig(minimum_trades=0),
        validation_candidate_count=2,
        monte_carlo_min_trades=2,
        monte_carlo_simulations=40,
        random_seed=17,
    )
    return space, config


def test_parameter_space_is_deterministic_and_preserves_invalid_configs() -> None:
    first, _ = setup()
    second, _ = setup()
    assert first.parameter_space_id == second.parameter_space_id
    assert first.raw_count == 4 and first.valid_count == 3 and first.invalid_count == 1
    assert [item.config_id for item in first.combinations] == [
        item.config_id for item in second.combinations
    ]


def test_walk_forward_search_validation_exactly_once_oos_and_determinism() -> None:
    space, config = setup()
    runner = ResearchExperimentRunner()
    first = runner.run(market_bars(), MovingAverageStrategyFactory(), space, config)
    second = runner.run(market_bars(), MovingAverageStrategyFactory(), space, config)
    assert first == second
    assert len(first.folds) == 3
    for fold in first.folds:
        assert len(fold.train_runs) == 4
        assert sum(run.status is ParameterRunStatus.INVALID_CONFIG for run in fold.train_runs) == 1
        assert len(fold.validation_runs) == 2
        assert fold.selected_config is not None
        assert fold.oos_evaluations == 1
    timestamps = [timestamp for timestamp, _ in first.aggregate_oos_equity_curve]
    assert timestamps == sorted(set(timestamps))
    assert first.parameter_space_id == space.parameter_space_id


def test_oos_mutation_cannot_change_selection_or_prior_fold() -> None:
    space, config = setup()
    runner = ResearchExperimentRunner()
    source = market_bars()
    baseline = runner.run(source, MovingAverageStrategyFactory(), space, config)
    changed = list(source)
    # Druhý OOS fold jsou indexy 28..35; změna je za celým prvním foldem.
    for index in range(28, 36):
        bar = changed[index]
        price = bar.close + Decimal("70")
        changed[index] = replace(
            bar, open=price, high=price + 2, low=price - 2, close=price, adjusted_close=price
        )
    mutated = runner.run(changed, MovingAverageStrategyFactory(), space, config)
    before, after = baseline.folds[0], mutated.folds[0]
    assert before.train_runs == after.train_runs
    assert before.validation_runs == after.validation_runs
    assert before.selected_config == after.selected_config
    assert before.oos_backtest == after.oos_backtest
    assert before.oos_metrics == after.oos_metrics
    second_before, second_after = baseline.folds[1], mutated.folds[1]
    assert second_before.train_runs == second_after.train_runs
    assert second_before.validation_runs == second_after.validation_runs
    assert second_before.selected_config == second_after.selected_config
    assert second_before.oos_metrics != second_after.oos_metrics


def test_overlapping_oos_fails_fast() -> None:
    space, config = setup()
    overlapping = replace(config, walk_forward=WalkForwardConfig(12, 8, 8, 4))
    with pytest.raises(ValueError, match="překrývají"):
        ResearchExperimentRunner().run(
            market_bars(), MovingAverageStrategyFactory(), space, overlapping
        )


def test_fifo_ledger_handles_scale_in_and_partial_scale_out() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    fills = [
        Fill("b1", "SPY", Side.BUY, Decimal("2"), Decimal("10"), Decimal("2"), start),
        Fill(
            "b2",
            "SPY",
            Side.BUY,
            Decimal("2"),
            Decimal("12"),
            Decimal("2"),
            start + timedelta(days=1),
        ),
        Fill(
            "s1",
            "SPY",
            Side.SELL,
            Decimal("3"),
            Decimal("15"),
            Decimal("3"),
            start + timedelta(days=2),
        ),
    ]
    trades = fifo_closed_trades(fills)
    assert [(trade.quantity, trade.entry_price) for trade in trades] == [
        (Decimal("2"), Decimal("10")),
        (Decimal("1"), Decimal("12")),
    ]
    # Hrubý P&L 13 minus tři alokované vstupní a tři výstupní poplatky.
    assert sum((trade.net_pnl for trade in trades), Decimal("0")) == Decimal("7")
