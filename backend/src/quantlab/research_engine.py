import hashlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantlab.backtest import BacktestResult, run_backtest
from quantlab.data import dataset_identity, validate_bars
from quantlab.domain import Bar, Fill
from quantlab.metrics import (
    PerformanceMetrics,
    benchmark_metrics,
    calculate_metrics,
    fifo_closed_trades,
)
from quantlab.research import (
    DEFAULT_COST_SCENARIOS,
    AnalysisResult,
    CostScenario,
    EligibilityConfig,
    ObjectiveConfig,
    ParameterStability,
    StrategyEligibilityResult,
    WalkForwardConfig,
    evaluate_eligibility,
    guarded_monte_carlo,
    objective_score,
    parameter_stability,
    run_cost_stress_backtests,
)
from quantlab.strategy import StrategyFactory
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

type ParameterValue = int | float | str | bool
type ParameterConfig = Mapping[str, ParameterValue]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ParameterCombination:
    config: tuple[tuple[str, ParameterValue], ...]
    valid: bool
    reason: str | None

    @property
    def as_dict(self) -> dict[str, ParameterValue]:
        return dict(self.config)

    @property
    def config_id(self) -> str:
        return _identity(self.as_dict)


class ParameterSpace:
    """Neměnný, kanonicky seřazený prostor; neplatné kombinace zůstávají auditovatelné."""

    def __init__(
        self,
        dimensions: Mapping[str, Sequence[ParameterValue]],
        validator: Callable[[ParameterConfig], bool | tuple[bool, str | None]],
    ) -> None:
        if not dimensions or any(not values for values in dimensions.values()):
            raise ValueError("Parameter space musí mít neprázdné dimenze")
        names = sorted(dimensions)
        combinations: list[ParameterCombination] = []
        for values in itertools.product(*(dimensions[name] for name in names)):
            config = dict(zip(names, values, strict=True))
            try:
                verdict = validator(config)
                valid, reason = verdict if isinstance(verdict, tuple) else (verdict, None)
                reason = reason if valid or reason else "validator_rejected"
            except (KeyError, TypeError, ValueError) as exc:
                valid, reason = False, f"{type(exc).__name__}: {exc}"
            combinations.append(
                ParameterCombination(tuple(sorted(config.items())), bool(valid), reason)
            )
        self._combinations = tuple(combinations)

    @property
    def combinations(self) -> tuple[ParameterCombination, ...]:
        return self._combinations

    @property
    def raw_count(self) -> int:
        return len(self._combinations)

    @property
    def valid_count(self) -> int:
        return sum(item.valid for item in self._combinations)

    @property
    def invalid_count(self) -> int:
        return self.raw_count - self.valid_count

    @property
    def parameter_space_id(self) -> str:
        return _identity(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {"combinations": [asdict(item) for item in self._combinations]}


class ParameterRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INVALID_CONFIG = "INVALID_CONFIG"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    INSUFFICIENT_TRADES = "INSUFFICIENT_TRADES"


@dataclass(frozen=True)
class ParameterRun:
    run_id: str
    strategy_name: str
    strategy_version: str
    parameter_config: tuple[tuple[str, ParameterValue], ...]
    dataset_slice_id: str
    start: datetime
    end: datetime
    status: ParameterRunStatus
    metrics: PerformanceMetrics | None
    objective_score: float | None
    failure_reason: str | None
    closed_trades: int
    backtest: BacktestResult | None


class FoldStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WalkForwardFoldResult:
    fold_id: str
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    oos_start: datetime
    oos_end: datetime
    train_runs: tuple[ParameterRun, ...]
    validation_runs: tuple[ParameterRun, ...]
    selected_config: tuple[tuple[str, ParameterValue], ...] | None
    selection_score: float | None
    selection_reason: str
    oos_backtest: BacktestResult | None
    oos_metrics: PerformanceMetrics | None
    oos_equity_id: str | None
    oos_evaluations: int
    status: FoldStatus
    failure_reason: str | None = None


@dataclass(frozen=True)
class ResearchConfig:
    walk_forward: WalkForwardConfig
    objective: ObjectiveConfig = ObjectiveConfig()
    validation_candidate_count: int = 3
    initial_cash: Decimal = Decimal("100000")
    costs: CostModel = CostModel()
    slippage: FixedBpsSlippage = FixedBpsSlippage()
    cost_scenarios: tuple[CostScenario, ...] = DEFAULT_COST_SCENARIOS
    monte_carlo_min_trades: int = 20
    monte_carlo_simulations: int = 1000
    random_seed: int = 42
    eligibility: EligibilityConfig = EligibilityConfig()
    engine_version: str = "2.7.0"

    def __post_init__(self) -> None:
        if self.validation_candidate_count <= 0 or self.initial_cash <= 0:
            raise ValueError("Research config obsahuje neplatný limit nebo kapitál")


@dataclass(frozen=True)
class ResearchExperimentResult:
    experiment_id: str
    dataset_id: str
    strategy_name: str
    strategy_version: str
    parameter_space_id: str
    parameter_space_raw_count: int
    parameter_space_valid_count: int
    folds: tuple[WalkForwardFoldResult, ...]
    aggregate_oos_equity_curve: tuple[tuple[datetime, Decimal], ...]
    aggregate_oos_metrics: PerformanceMetrics
    aggregate_oos_fills: tuple[Fill, ...]
    benchmark: Mapping[str, float | None]
    cost_stress: Mapping[str, float]
    monte_carlo: AnalysisResult[object]
    parameter_stability: ParameterStability
    eligibility: StrategyEligibilityResult
    report: str


class WalkForwardRunner:
    def _backtest(
        self,
        bars: list[Bar],
        factory: StrategyFactory,
        config: ParameterConfig,
        settings: ResearchConfig,
    ) -> BacktestResult:
        execution = ExecutionEngine(
            RiskEngine(
                RiskConfig(
                    max_position_weight=Decimal("1"),
                    max_order_notional=settings.initial_cash * Decimal("2"),
                    allowed_symbols=frozenset({bars[0].symbol}),
                )
            ),
            PaperBroker(settings.costs, settings.slippage),
        )
        return run_backtest(
            bars,
            factory.create(config),
            Portfolio(settings.initial_cash),
            PortfolioConstructor(),
            execution,
        )

    def _run_parameter(
        self,
        bars: list[Bar],
        factory: StrategyFactory,
        combination: ParameterCombination,
        settings: ResearchConfig,
        scope: str,
    ) -> ParameterRun:
        slice_id = dataset_identity(bars)
        run_id = _identity(
            {
                "slice": slice_id,
                "config": combination.as_dict,
                "scope": scope,
                "strategy": factory.strategy_name,
                "version": factory.strategy_version,
                "objective": asdict(settings.objective),
                "costs": asdict(settings.costs),
                "slippage": asdict(settings.slippage),
            }
        )
        if not combination.valid:
            return ParameterRun(
                run_id,
                factory.strategy_name,
                factory.strategy_version,
                combination.config,
                slice_id,
                bars[0].timestamp,
                bars[-1].timestamp,
                ParameterRunStatus.INVALID_CONFIG,
                None,
                None,
                combination.reason,
                0,
                None,
            )
        try:
            result = self._backtest(bars, factory, combination.as_dict, settings)
            values = [
                (point["timestamp"], point["portfolio_value"]) for point in result.equity_curve
            ]
            metrics = calculate_metrics(result.initial_cash, values, result.fills)  # type: ignore[arg-type]
            score = objective_score(
                metrics.total_return,
                metrics.sharpe_ratio,
                metrics.maximum_drawdown,
                metrics.number_of_trades,
                settings.objective,
            )
            status = (
                ParameterRunStatus.COMPLETED
                if score is not None
                else ParameterRunStatus.INSUFFICIENT_TRADES
            )
            return ParameterRun(
                run_id,
                factory.strategy_name,
                factory.strategy_version,
                combination.config,
                slice_id,
                bars[0].timestamp,
                bars[-1].timestamp,
                status,
                metrics,
                score,
                None if score is not None else "insufficient_closed_trades",
                metrics.number_of_trades,
                result,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            return ParameterRun(
                run_id,
                factory.strategy_name,
                factory.strategy_version,
                combination.config,
                slice_id,
                bars[0].timestamp,
                bars[-1].timestamp,
                ParameterRunStatus.BACKTEST_FAILED,
                None,
                None,
                f"{type(exc).__name__}: {exc}",
                0,
                None,
            )

    def run(
        self,
        bars: list[Bar],
        factory: StrategyFactory,
        space: ParameterSpace,
        settings: ResearchConfig,
    ) -> tuple[WalkForwardFoldResult, ...]:
        window = settings.walk_forward
        size = window.training_window + window.validation_window + window.test_window
        starts = list(range(0, len(bars) - size + 1, window.step_size))
        if not starts:
            raise ValueError("Dataset neobsahuje ani jeden celý walk-forward fold")
        intervals = [
            (start + window.training_window + window.validation_window, start + size)
            for start in starts
        ]
        if any(
            current[0] < previous[1]
            for previous, current in zip(intervals, intervals[1:], strict=False)
        ):
            raise ValueError("OOS intervaly walk-forward se překrývají")
        folds: list[WalkForwardFoldResult] = []
        for index, start in enumerate(starts):
            train_end = start + window.training_window
            validation_end = train_end + window.validation_window
            test_end = validation_end + window.test_window
            train, validation, oos = (
                bars[start:train_end],
                bars[train_end:validation_end],
                bars[validation_end:test_end],
            )
            fold_id = _identity(
                {
                    "index": index,
                    "train": dataset_identity(train),
                    "validation": dataset_identity(validation),
                    "oos_bounds": [oos[0].timestamp, oos[-1].timestamp],
                }
            )
            train_runs = tuple(
                self._run_parameter(train, factory, item, settings, "train")
                for item in space.combinations
            )
            ranked = sorted(
                (run for run in train_runs if run.objective_score is not None),
                key=lambda run: (-(run.objective_score or 0.0), _canonical(run.parameter_config)),
            )
            candidates = ranked[: settings.validation_candidate_count]
            validation_runs = tuple(
                self._run_parameter(
                    validation,
                    factory,
                    ParameterCombination(candidate.parameter_config, True, None),
                    settings,
                    "validation",
                )
                for candidate in candidates
            )
            selected = sorted(
                (run for run in validation_runs if run.objective_score is not None),
                key=lambda run: (-(run.objective_score or 0.0), _canonical(run.parameter_config)),
            )
            if not selected:
                folds.append(
                    WalkForwardFoldResult(
                        fold_id,
                        train[0].timestamp,
                        train[-1].timestamp,
                        validation[0].timestamp,
                        validation[-1].timestamp,
                        oos[0].timestamp,
                        oos[-1].timestamp,
                        train_runs,
                        validation_runs,
                        None,
                        None,
                        "no_eligible_validation_candidate",
                        None,
                        None,
                        None,
                        0,
                        FoldStatus.FAILED,
                        "selection_failed",
                    )
                )
                continue
            winner = selected[0]
            # Parametry jsou zde zamčené; následuje jediná OOS evaluace.
            oos_result = self._backtest(oos, factory, dict(winner.parameter_config), settings)
            values = [
                (point["timestamp"], point["portfolio_value"]) for point in oos_result.equity_curve
            ]
            oos_metrics = calculate_metrics(oos_result.initial_cash, values, oos_result.fills)  # type: ignore[arg-type]
            equity_id = _identity(values)
            folds.append(
                WalkForwardFoldResult(
                    fold_id,
                    train[0].timestamp,
                    train[-1].timestamp,
                    validation[0].timestamp,
                    validation[-1].timestamp,
                    oos[0].timestamp,
                    oos[-1].timestamp,
                    train_runs,
                    validation_runs,
                    winner.parameter_config,
                    winner.objective_score,
                    "best_validation_objective_after_train_top_k",
                    oos_result,
                    oos_metrics,
                    equity_id,
                    1,
                    FoldStatus.COMPLETED,
                )
            )
        return tuple(folds)


class ResearchExperimentRunner:
    def __init__(self, walk_forward_runner: WalkForwardRunner | None = None) -> None:
        self.walk_forward_runner = walk_forward_runner or WalkForwardRunner()

    def run(
        self,
        dataset: list[Bar],
        strategy_factory: StrategyFactory,
        parameter_space: ParameterSpace,
        config: ResearchConfig,
    ) -> ResearchExperimentResult:
        validate_bars(dataset)
        dataset_id = dataset_identity(dataset)
        identity_payload = {
            "dataset": dataset_id,
            "strategy": strategy_factory.strategy_name,
            "version": strategy_factory.strategy_version,
            "parameter_space": parameter_space.parameter_space_id,
            "config": asdict(config),
        }
        experiment_id = _identity(identity_payload)
        folds = self.walk_forward_runner.run(dataset, strategy_factory, parameter_space, config)
        completed = [fold for fold in folds if fold.status is FoldStatus.COMPLETED]
        if not completed:
            raise ValueError("Žádný walk-forward fold nebyl dokončen")
        curve: list[tuple[datetime, Decimal]] = []
        fills: list[Fill] = []
        capital = config.initial_cash
        for fold in completed:
            assert fold.oos_backtest is not None
            for point in fold.oos_backtest.equity_curve:
                relative = point["portfolio_value"] / fold.oos_backtest.initial_cash  # type: ignore[operator]
                curve.append((point["timestamp"], capital * relative))  # type: ignore[arg-type]
            capital = curve[-1][1] if curve else capital
            fills.extend(fold.oos_backtest.fills)
        aggregate_metrics = calculate_metrics(config.initial_cash, curve, fills)
        oos_bars = [
            bar
            for fold in completed
            for bar in dataset
            if fold.oos_start <= bar.timestamp <= fold.oos_end
        ]
        benchmark = benchmark_metrics(oos_bars)
        stress_values: dict[str, float] = {}
        for scenario in config.cost_scenarios:
            stressed_capital = config.initial_cash
            for fold in completed:
                selected = dict(fold.selected_config or ())
                section = [
                    bar for bar in dataset if fold.oos_start <= bar.timestamp <= fold.oos_end
                ]
                result = run_cost_stress_backtests(
                    section,
                    strategy_factory.create(selected),
                    config.initial_cash,
                    config.costs,
                    config.slippage,
                    (scenario,),
                )[scenario.name]
                stressed_capital *= result.final_value / result.initial_cash
            stress_values[scenario.name] = float(stressed_capital / config.initial_cash - 1)
        closed = fifo_closed_trades(fills)
        monte_carlo = guarded_monte_carlo(
            [trade.net_return for trade in closed],
            float(config.initial_cash),
            config.monte_carlo_min_trades,
            config.monte_carlo_simulations,
            config.random_seed,
        )
        # Stabilita je lokální k jednomu validation slice; runy různých dataset slices nemícháme.
        selected_runs = [
            run
            for run in completed[0].validation_runs
            if run.metrics is not None and run.objective_score is not None
        ]
        chosen = completed[0].selected_config or ()
        keys = sorted(name for name, value in chosen if isinstance(value, int))
        stability_input = {
            tuple(int(dict(run.parameter_config)[key]) for key in keys): (
                run.objective_score or 0.0
            )
            for run in selected_runs
            if all(key in dict(run.parameter_config) for key in keys)
        }
        stability = (
            parameter_stability(stability_input, tuple(int(dict(chosen)[key]) for key in keys))
            if keys
            else ParameterStability(0, None, None, None)
        )
        profitable_folds = sum(
            bool(fold.oos_metrics and fold.oos_metrics.total_return > 0) for fold in completed
        ) / len(completed)
        eligibility = evaluate_eligibility(
            aggregate_metrics.number_of_trades,
            aggregate_metrics.total_return,
            aggregate_metrics.maximum_drawdown,
            stress_values,
            profitable_folds,
            stability,
            config.eligibility,
        )
        report_payload = {
            "experiment": experiment_id,
            "engine_config": identity_payload["config"],
            "dataset": {
                "identity": dataset_id,
                "source": dataset[0].source,
                "timeframe": dataset[0].timeframe,
                "start": dataset[0].timestamp,
                "end": dataset[-1].timestamp,
            },
            "strategy": {
                "name": strategy_factory.strategy_name,
                "version": strategy_factory.strategy_version,
            },
            "version": strategy_factory.strategy_version,
            "parameter_space": {
                "identity": parameter_space.parameter_space_id,
                "raw": parameter_space.raw_count,
                "valid": parameter_space.valid_count,
                "invalid": parameter_space.invalid_count,
            },
            "walk_forward": [
                {
                    "fold_id": fold.fold_id,
                    "train_runs": len(fold.train_runs),
                    "validation_runs": len(fold.validation_runs),
                    "selected": fold.selected_config,
                    "oos_metrics": asdict(fold.oos_metrics) if fold.oos_metrics else None,
                }
                for fold in folds
            ],
            "aggregate_oos": asdict(aggregate_metrics),
            "metrics": asdict(aggregate_metrics),
            "selected_parameters": [fold.selected_config for fold in completed],
            "benchmark": benchmark,
            "accounting": {
                "closed_fifo_trades": len(closed),
                "commissions": aggregate_metrics.total_commissions,
                "slippage": aggregate_metrics.total_slippage_cost,
            },
            "cost_stress": stress_values,
            "monte_carlo": asdict(monte_carlo),
            "parameter_stability": asdict(stability),
            "eligibility": asdict(eligibility),
            "known_limitations": [
                "single-symbol long-only",
                "empirical bootstrap nezachovává režimy",
            ],
        }
        report = (
            "# Research report\n\n```json\n"
            + json.dumps(report_payload, default=str, sort_keys=True, indent=2)
            + "\n```"
        )
        return ResearchExperimentResult(
            experiment_id,
            dataset_id,
            strategy_factory.strategy_name,
            strategy_factory.strategy_version,
            parameter_space.parameter_space_id,
            parameter_space.raw_count,
            parameter_space.valid_count,
            folds,
            tuple(curve),
            aggregate_metrics,
            tuple(fills),
            benchmark,
            stress_values,
            monte_carlo,
            stability,
            eligibility,
            report,
        )
