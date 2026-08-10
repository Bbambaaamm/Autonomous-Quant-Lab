import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantlab.domain import Bar, Fill, Side
from quantlab.metrics import fifo_closed_trades
from quantlab.persistence import RunRepository
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


def test_structured_persistence_is_complete_idempotent_and_immutable() -> None:
    space, config = setup()
    runner = ResearchExperimentRunner()
    result = runner.run(market_bars(), MovingAverageStrategyFactory(), space, config)
    repository = RunRepository()
    snapshot = asdict(result)
    persisted_config = {"research_config": asdict(config), "parameter_space": space.to_dict()}

    repository.save_experiment(result.experiment_id, persisted_config, snapshot, datetime.now(UTC))
    expected_counts = {
        "experiments": 1,
        "folds": 3,
        "parameter_runs": 18,
        "eligibility_checks": 6,
    }
    assert repository.persistence_counts() == expected_counts

    structure = repository.get_experiment_structure(result.experiment_id)
    assert structure is not None
    assert structure["experiment"] == {
        "id": result.experiment_id,
        "dataset_id": result.dataset_id,
        "strategy_name": result.strategy_name,
        "strategy_version": result.strategy_version,
        "parameter_space_id": result.parameter_space_id,
        "decision": result.eligibility.decision,
    }
    assert len(structure["folds"]) == len(result.folds)
    source_runs = {
        (fold.fold_id, stage, run.run_id): run
        for fold in result.folds
        for stage, runs in (("TRAIN", fold.train_runs), ("VALIDATION", fold.validation_runs))
        for run in runs
    }
    assert len(structure["parameter_runs"]) == len(source_runs)
    for persisted_run in structure["parameter_runs"]:
        source = source_runs[
            (persisted_run["fold_id"], persisted_run["stage"], persisted_run["run_id"])
        ]
        assert persisted_run["experiment_id"] == result.experiment_id
        assert persisted_run["parameter_config"] == json.loads(json.dumps(source.parameter_config))
        canonical_config = json.dumps(
            source.parameter_config, sort_keys=True, separators=(",", ":")
        )
        assert (
            persisted_run["parameter_config_id"]
            == hashlib.sha256(canonical_config.encode()).hexdigest()
        )
        assert persisted_run["status"] == source.status
        assert persisted_run["objective_score"] == source.objective_score
        assert persisted_run["metrics"] == (
            json.loads(json.dumps(asdict(source.metrics), default=str)) if source.metrics else None
        )
        assert persisted_run["closed_trade_count"] == source.closed_trades
        assert persisted_run["failure_reason"] == source.failure_reason
    for persisted_fold, source_fold in zip(structure["folds"], result.folds, strict=True):
        assert persisted_fold["selected_config"] == json.loads(
            json.dumps(source_fold.selected_config)
        )
    assert structure["eligibility_checks"] == [
        {
            "name": check.name,
            "status": check.status,
            "observed_value": float(check.observed_value)
            if check.observed_value is not None
            else None,
            "threshold": float(check.threshold) if check.threshold is not None else None,
            "reason": check.reason,
        }
        for check in result.eligibility.checks
    ]
    assert repository.get_experiment(result.experiment_id)["result"] == json.loads(
        json.dumps(snapshot, default=str)
    )

    repository.save_experiment(result.experiment_id, persisted_config, snapshot, datetime.now(UTC))
    assert repository.persistence_counts() == expected_counts

    conflicting = deepcopy(snapshot)
    conflicting["dataset_id"] = "conflicting-content"
    with pytest.raises(ValueError, match="koliduje"):
        repository.save_experiment(
            result.experiment_id, persisted_config, conflicting, datetime.now(UTC)
        )
    assert repository.persistence_counts() == expected_counts

    other_config = replace(config, random_seed=config.random_seed + 1)
    other = runner.run(market_bars(), MovingAverageStrategyFactory(), space, other_config)
    assert other.experiment_id != result.experiment_id
    repository.save_experiment(
        other.experiment_id,
        {"research_config": asdict(other_config), "parameter_space": space.to_dict()},
        asdict(other),
        datetime.now(UTC),
    )
    assert repository.persistence_counts() == {
        "experiments": 2,
        "folds": 6,
        "parameter_runs": 36,
        "eligibility_checks": 12,
    }


def test_structured_experiment_materialization_is_transactional() -> None:
    space, config = setup()
    result = ResearchExperimentRunner().run(
        market_bars(), MovingAverageStrategyFactory(), space, config
    )
    malformed = asdict(result)
    malformed["eligibility"]["checks"] = "invalid"
    repository = RunRepository()

    with pytest.raises(TypeError, match="Eligibility checks"):
        repository.save_experiment(
            result.experiment_id,
            {"research_config": asdict(config), "parameter_space": space.to_dict()},
            malformed,
            datetime.now(UTC),
        )

    assert repository.persistence_counts() == {
        "experiments": 0,
        "folds": 0,
        "parameter_runs": 0,
        "eligibility_checks": 0,
    }


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
