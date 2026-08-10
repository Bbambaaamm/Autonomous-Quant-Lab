import hashlib
import itertools
import json
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from statistics import median, pvariance

from quantlab.domain import Bar


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


@dataclass(frozen=True)
class EligibilityConfig:
    minimum_trades: int = 20
    maximum_drawdown: float = 0.25
    minimum_profitable_folds: float = 0.6
    minimum_profitable_neighbors: float = 0.5


@dataclass(frozen=True)
class StrategyEligibilityResult:
    decision: EligibilityDecision
    passed: dict[str, bool]
    reasons: tuple[str, ...]


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
    checks = {
        "minimum_trades": number_of_trades >= config.minimum_trades,
        "positive_oos": oos_return > 0,
        "drawdown": abs(maximum_drawdown) <= config.maximum_drawdown,
        "cost_stress": all(value > 0 for value in stressed_returns.values()),
        "walk_forward": profitable_folds >= config.minimum_profitable_folds,
        "parameter_stability": stability.profitable_fraction is not None
        and stability.profitable_fraction >= config.minimum_profitable_neighbors,
    }
    passed_count = sum(checks.values())
    decision = (
        EligibilityDecision.PAPER_CANDIDATE
        if passed_count == len(checks)
        else EligibilityDecision.RESEARCH_ONLY
        if checks["minimum_trades"] and checks["positive_oos"]
        else EligibilityDecision.REJECTED
    )
    return StrategyEligibilityResult(
        decision, checks, tuple(name for name, passed in checks.items() if not passed)
    )


def markdown_report(
    identity: ExperimentIdentity,
    metrics: dict[str, object],
    benchmark: Mapping[str, object],
    stress: dict[str, float],
    monte_carlo: MonteCarloResult | None,
    stability: ParameterStability,
    eligibility: StrategyEligibilityResult,
    walk_forward: list[dict[str, object]],
) -> str:
    payload = {
        "strategy": identity.strategy_name,
        "version": identity.strategy_version,
        "config": identity.strategy_config,
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
        "monte_carlo": asdict(monte_carlo) if monte_carlo else None,
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
