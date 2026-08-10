from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from quantlab.data import CSVMarketDataProvider, dataset_identity
from quantlab.demo import run_demo
from quantlab.metrics import benchmark_metrics, calculate_metrics
from quantlab.persistence import RunRepository
from quantlab.research import (
    EligibilityDecision,
    ExperimentIdentity,
    ParameterStability,
    StrategyEligibilityResult,
    cost_stress,
    markdown_report,
)


class ResearchService:
    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def create_demo_experiment(self, fixture: Path) -> dict[str, object]:
        bars = CSVMarketDataProvider(fixture).load("SPY")
        backtest = run_demo(fixture)
        identity = ExperimentIdentity(
            "moving_average",
            "1.0.0",
            {"fast_window": 3, "slow_window": 5},
            dataset_identity(bars),
            bars[0].timestamp.isoformat(),
            bars[-1].timestamp.isoformat(),
            {"fixed": "1", "rate": "0.001", "minimum": "1"},
            {"basis_points": "5"},
            42,
        )
        values = [
            (cast(datetime, item["timestamp"]), cast(Decimal, item["portfolio_value"]))
            for item in backtest.equity_curve
        ]
        metrics = calculate_metrics(backtest.initial_cash, values, backtest.fills)
        gross_return = metrics.total_return + (
            metrics.total_commissions + metrics.total_slippage_cost
        ) / float(backtest.initial_cash)
        stress = cost_stress(
            gross_return,
            metrics.total_commissions,
            metrics.total_slippage_cost,
            float(backtest.initial_cash),
        )
        stability = ParameterStability(0, None, None, None)
        result: dict[str, object] = {
            "metrics": asdict(metrics),
            "benchmark": benchmark_metrics(bars),
            "excess_return": metrics.total_return - (benchmark_metrics(bars)["total_return"] or 0),
            "cost_stress": stress,
            "walk_forward": [],
            "monte_carlo": None,
            "parameter_stability": asdict(stability),
            "eligibility": {"decision": "RESEARCH_ONLY", "reasons": ["insufficient_fixture"]},
        }
        result["report"] = markdown_report(
            identity,
            asdict(metrics),
            benchmark_metrics(bars),
            stress,
            None,
            stability,
            # Demo fixture je záměrně příliš malá pro způsobilost.
            StrategyEligibilityResult(
                EligibilityDecision.RESEARCH_ONLY,
                {},
                ("insufficient_fixture",),
            ),
            [],
        )
        self.repository.save_experiment(
            identity.experiment_id, asdict(identity), result, datetime.now(UTC)
        )
        return {"id": identity.experiment_id, "config": asdict(identity), "result": result}

    def get(self, experiment_id: str) -> dict[str, object] | None:
        return self.repository.get_experiment(experiment_id)

    def list(self) -> list[dict[str, object]]:
        return self.repository.list_experiments()
