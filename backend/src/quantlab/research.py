import hashlib
import itertools
import json
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from statistics import median, pvariance

from quantlab.backtest import BacktestResult, run_backtest
from quantlab.domain import Bar
from quantlab.strategy import Strategy
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


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[Bar, ...]
    validation: tuple[Bar, ...]
    test: tuple[Bar, ...]


def chronological_split(
    bars: list[Bar], train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> ChronologicalSplit:
    if (
        not 0 < train_fraction < 1
        or not 0 <= validation_fraction < 1
        or train_fraction + validation_fraction >= 1
    ):
        raise ValueError("Neplatné poměry chronologického splitu")
    first = int(len(bars) * train_fraction)
    second = first + int(len(bars) * validation_fraction)
    if first == 0 or second == first or second == len(bars):
        raise ValueError("Každá část splitu musí obsahovat data")
    return ChronologicalSplit(tuple(bars[:first]), tuple(bars[first:second]), tuple(bars[second:]))


@dataclass(frozen=True)
class WalkForwardConfig:
    training_window: int
    validation_window: int
    test_window: int
    step_size: int

    def __post_init__(self) -> None:
        if min(self.training_window, self.validation_window, self.test_window, self.step_size) <= 0:
            raise ValueError("Okna walk-forward musí být kladná")


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime


def walk_forward_folds(bars: list[Bar], config: WalkForwardConfig) -> list[WalkForwardFold]:
    size = config.training_window + config.validation_window + config.test_window
    folds: list[WalkForwardFold] = []
    for start in range(0, len(bars) - size + 1, config.step_size):
        train_end = start + config.training_window
        validation_end = train_end + config.validation_window
        test_end = validation_end + config.test_window
        folds.append(
            WalkForwardFold(
                len(folds),
                bars[start].timestamp,
                bars[train_end - 1].timestamp,
                bars[train_end].timestamp,
                bars[validation_end - 1].timestamp,
                bars[validation_end].timestamp,
                bars[test_end - 1].timestamp,
            )
        )
    return folds


def parameter_grid(
    parameters: dict[str, list[int]], validator: Callable[[dict[str, int]], bool]
) -> list[dict[str, int]]:
    names = sorted(parameters)
    combinations = [
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(parameters[name] for name in names))
    ]
    return [combination for combination in combinations if validator(combination)]


@dataclass(frozen=True)
class ExperimentIdentity:
    strategy_name: str
    strategy_version: str
    strategy_config: dict[str, object]
    dataset_id: str
    start: str
    end: str
    commission_model: dict[str, object]
    slippage_model: dict[str, object]
    random_seed: int
    engine_version: str = "2.0.0"

    @property
    def experiment_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CostScenario:
    name: str
    commission_multiplier: float
    slippage_multiplier: float


DEFAULT_COST_SCENARIOS = (
    CostScenario("base", 1, 1),
    CostScenario("double_commission", 2, 1),
    CostScenario("double_slippage", 1, 2),
    CostScenario("triple_slippage", 1, 3),
    CostScenario("adverse_combined", 2, 3),
)


def run_cost_stress_backtests(
    bars: list[Bar],
    strategy: Strategy,
    initial_cash: Decimal,
    costs: CostModel,
    slippage: FixedBpsSlippage,
    scenarios: tuple[CostScenario, ...] = DEFAULT_COST_SCENARIOS,
) -> dict[str, BacktestResult]:
    """Každý scénář znovu spustí celý path-dependent backtest."""
    results: dict[str, BacktestResult] = {}
    for scenario in scenarios:
        scenario_costs = CostModel(
            fixed=costs.fixed * Decimal(str(scenario.commission_multiplier)),
            rate=costs.rate * Decimal(str(scenario.commission_multiplier)),
            minimum=costs.minimum * Decimal(str(scenario.commission_multiplier)),
        )
        scenario_slippage = FixedBpsSlippage(
            slippage.basis_points * Decimal(str(scenario.slippage_multiplier))
        )
        execution = ExecutionEngine(
            RiskEngine(
                RiskConfig(
                    max_position_weight=Decimal("1"),
                    max_order_notional=initial_cash * Decimal("2"),
                    allowed_symbols=frozenset({bars[0].symbol}),
                )
            ),
            PaperBroker(scenario_costs, scenario_slippage),
        )
        results[scenario.name] = run_backtest(
            bars,
            strategy,
            Portfolio(initial_cash),
            PortfolioConstructor(),
            execution,
        )
    return results


def cost_stress(
    gross_return: float,
    commission_cost: float,
    slippage_cost: float,
    capital: float,
    scenarios: tuple[CostScenario, ...] = DEFAULT_COST_SCENARIOS,
) -> dict[str, float]:
    if capital <= 0:
        raise ValueError("Kapitál musí být kladný")
    return {
        scenario.name: gross_return
        - (
            commission_cost * scenario.commission_multiplier
            + slippage_cost * scenario.slippage_multiplier
        )
        / capital
        for scenario in scenarios
    }


@dataclass(frozen=True)
class MonteCarloResult:
    simulations: int
    seed: int
    median_terminal_equity: float
    percentile_5_terminal_equity: float
    percentile_95_max_drawdown: float
    probability_of_loss: float


class AnalysisStatus(StrEnum):
    COMPLETED = "COMPLETED"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class AnalysisResult[T]:
    status: AnalysisStatus
    result: T | None
    reason: str | None = None


def guarded_monte_carlo(
    trade_returns: list[float],
    initial_equity: float,
    min_trades: int,
    simulations: int = 1000,
    seed: int = 42,
) -> AnalysisResult[MonteCarloResult]:
    if len(trade_returns) < min_trades:
        return AnalysisResult(AnalysisStatus.NOT_EVALUATED, None, "insufficient_closed_trades")
    return AnalysisResult(
        AnalysisStatus.COMPLETED,
        monte_carlo_trade_returns(trade_returns, initial_equity, simulations, seed),
    )


@dataclass(frozen=True)
class ObjectiveConfig:
    total_return_weight: float = 1.0
    sharpe_weight: float = 0.25
    drawdown_weight: float = 0.5
    minimum_trades: int = 0


def objective_score(
    total_return: float,
    sharpe: float | None,
    maximum_drawdown: float,
    number_of_trades: int,
    config: ObjectiveConfig | None = None,
) -> float | None:
    """Transparentní ranking heuristika; komponenty nejsou statisticky srovnatelné."""
    config = config or ObjectiveConfig()
    if number_of_trades < config.minimum_trades:
        return None
    return (
        config.total_return_weight * total_return
        + config.sharpe_weight * (sharpe or 0.0)
        - config.drawdown_weight * abs(maximum_drawdown)
    )


def parameter_run_sort_key(score: float | None, parameters: Mapping[str, int]) -> tuple[float, str]:
    """Vyšší skóre vyhraje; kanonické parametry deterministicky rozbijí shodu."""
    return (score if score is not None else float("-inf"), json.dumps(parameters, sort_keys=True))


def monte_carlo_trade_returns(
    trade_returns: list[float], initial_equity: float, simulations: int = 1000, seed: int = 42
) -> MonteCarloResult:
    if not trade_returns or simulations <= 0 or initial_equity <= 0:
        raise ValueError("Monte Carlo vyžaduje obchody, kapitál a simulace")
    rng = random.Random(seed)
    terminals, drawdowns = [], []
    for _ in range(simulations):
        equity = peak = initial_equity
        maximum_drawdown = 0.0
        for result in rng.choices(trade_returns, k=len(trade_returns)):
            equity *= 1 + result
            peak = max(peak, equity)
            maximum_drawdown = min(maximum_drawdown, equity / peak - 1)
        terminals.append(equity)
        drawdowns.append(abs(maximum_drawdown))
    terminals.sort()
    drawdowns.sort()

    def percentile(values: list[float], probability: float) -> float:
        return values[min(len(values) - 1, int((len(values) - 1) * probability))]

    return MonteCarloResult(
        simulations,
        seed,
        median(terminals),
        percentile(terminals, 0.05),
        percentile(drawdowns, 0.95),
        sum(value < initial_equity for value in terminals) / simulations,
    )


@dataclass(frozen=True)
class ParameterStability:
    neighbor_count: int
    median_performance: float | None
    performance_variance: float | None
    profitable_fraction: float | None


def parameter_stability(
    results: dict[tuple[int, ...], float], selected: tuple[int, ...]
) -> ParameterStability:
    neighbors = [
        value
        for key, value in results.items()
        if key != selected
        and all(abs(left - right) <= 1 for left, right in zip(key, selected, strict=True))
    ]
    return ParameterStability(
        len(neighbors),
        median(neighbors) if neighbors else None,
        pvariance(neighbors) if len(neighbors) > 1 else None,
        sum(value > 0 for value in neighbors) / len(neighbors) if neighbors else None,
    )


class EligibilityDecision(StrEnum):
    REJECTED = "REJECTED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PAPER_CANDIDATE = "PAPER_CANDIDATE"


class EligibilityCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class EligibilityCheck:
    name: str
    status: EligibilityCheckStatus
    observed_value: int | float | bool | None
    threshold: int | float | bool | None
    reason: str | None = None


@dataclass(frozen=True)
class EligibilityConfig:
    minimum_trades: int = 20
    minimum_oos_return: float = 0.0
    maximum_drawdown: float = 0.25
    minimum_profitable_folds: float = 0.6
    minimum_profitable_neighbors: float = 0.5


@dataclass(frozen=True)
class StrategyEligibilityResult:
    decision: EligibilityDecision
    checks: tuple[EligibilityCheck, ...]
    reasons: tuple[str, ...]

    @property
    def passed(self) -> dict[str, bool]:
        """Zpětně kompatibilní odvozený pohled; autoritativní jsou typované kontroly."""
        return {check.name: check.status is EligibilityCheckStatus.PASSED for check in self.checks}


def evaluate_eligibility(
    number_of_trades: int,
    oos_return: float,
    maximum_drawdown: float,
    stressed_returns: dict[str, float],
    profitable_folds: float,
    stability: ParameterStability,
    config: EligibilityConfig | None = None,
) -> StrategyEligibilityResult:
    config = config or EligibilityConfig()
    stability_status = (
        EligibilityCheckStatus.NOT_EVALUATED
        if stability.profitable_fraction is None
        else EligibilityCheckStatus.PASSED
        if stability.profitable_fraction >= config.minimum_profitable_neighbors
        else EligibilityCheckStatus.FAILED
    )
    checks = (
        EligibilityCheck(
            "minimum_trades",
            EligibilityCheckStatus.PASSED
            if number_of_trades >= config.minimum_trades
            else EligibilityCheckStatus.FAILED,
            number_of_trades,
            config.minimum_trades,
        ),
        EligibilityCheck(
            "positive_oos",
            EligibilityCheckStatus.PASSED
            if oos_return >= config.minimum_oos_return
            else EligibilityCheckStatus.FAILED,
            oos_return,
            config.minimum_oos_return,
        ),
        EligibilityCheck(
            "drawdown",
            EligibilityCheckStatus.PASSED
            if abs(maximum_drawdown) <= config.maximum_drawdown
            else EligibilityCheckStatus.FAILED,
            abs(maximum_drawdown),
            config.maximum_drawdown,
        ),
        EligibilityCheck(
            "cost_stress",
            EligibilityCheckStatus.PASSED
            if stressed_returns and all(value > 0 for value in stressed_returns.values())
            else EligibilityCheckStatus.FAILED,
            min(stressed_returns.values()) if stressed_returns else None,
            0.0,
            None if stressed_returns else "missing_cost_stress_scenarios",
        ),
        EligibilityCheck(
            "walk_forward",
            EligibilityCheckStatus.PASSED
            if profitable_folds >= config.minimum_profitable_folds
            else EligibilityCheckStatus.FAILED,
            profitable_folds,
            config.minimum_profitable_folds,
        ),
        EligibilityCheck(
            "parameter_stability",
            stability_status,
            stability.profitable_fraction,
            config.minimum_profitable_neighbors,
            "no_parameter_neighbors" if stability.profitable_fraction is None else None,
        ),
    )
    check_by_name = {check.name: check for check in checks}
    passed_count = sum(check.status is EligibilityCheckStatus.PASSED for check in checks)
    decision = (
        EligibilityDecision.PAPER_CANDIDATE
        if passed_count == len(checks)
        else EligibilityDecision.RESEARCH_ONLY
        if check_by_name["minimum_trades"].status is EligibilityCheckStatus.PASSED
        and check_by_name["positive_oos"].status is EligibilityCheckStatus.PASSED
        else EligibilityDecision.REJECTED
    )
    return StrategyEligibilityResult(
        decision,
        checks,
        tuple(check.name for check in checks if check.status is not EligibilityCheckStatus.PASSED),
    )


def markdown_report(
    identity: ExperimentIdentity,
    metrics: dict[str, object],
    benchmark: Mapping[str, object],
    stress: dict[str, float],
    monte_carlo: MonteCarloResult | AnalysisResult[MonteCarloResult] | None,
    stability: ParameterStability,
    eligibility: StrategyEligibilityResult,
    walk_forward: list[dict[str, object]] | Mapping[str, object],
    parameter_space_id: str | None = None,
    selected_parameters: list[dict[str, object]] | None = None,
) -> str:
    payload = {
        "strategy": identity.strategy_name,
        "version": identity.strategy_version,
        "config": identity.strategy_config,
        "parameter_space": parameter_space_id
        or hashlib.sha256(
            json.dumps(identity.strategy_config, sort_keys=True).encode()
        ).hexdigest(),
        "selected_parameters": selected_parameters or [identity.strategy_config],
        "dataset": identity.dataset_id,
        "period": [identity.start, identity.end],
        "assumptions": {
            "execution": "signal close T, fill raw open T+1",
            "survivorship_bias_status": "unknown/not controlled",
        },
        "metrics": metrics,
        "benchmark": benchmark,
        "cost_stress": stress,
        "walk_forward": walk_forward,
        "monte_carlo": (
            asdict(monte_carlo)
            if monte_carlo is not None
            else {
                "status": AnalysisStatus.NOT_EVALUATED,
                "result": None,
                "reason": "insufficient_closed_trades",
            }
        ),
        "parameter_stability": asdict(stability),
        "eligibility": asdict(eligibility),
        "known_limitations": [
            "Výsledek není predikcí budoucího výnosu.",
            "Data neprokazují absenci survivorship bias.",
        ],
    }
    return (
        "# Research report\n\n```json\n"
        + json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        + "\n```\n"
    )
