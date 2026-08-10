from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from quantlab.data import CSVMarketDataProvider
from quantlab.persistence import RunRepository
from quantlab.research import EligibilityConfig, ObjectiveConfig, WalkForwardConfig
from quantlab.research_engine import ParameterSpace, ResearchConfig, ResearchExperimentRunner
from quantlab.strategy import MovingAverageStrategyFactory


class ResearchService:
    """Aplikační adaptér; veškerou research business logiku deleguje runneru."""

    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def create_demo_experiment(self, fixture: Path) -> dict[str, object]:
        bars = CSVMarketDataProvider(fixture).load("SPY")
        space = ParameterSpace(
            {"fast_window": (2, 3), "slow_window": (3, 5)},
            lambda item: int(item["fast_window"]) < int(item["slow_window"]),
        )
        config = ResearchConfig(
            WalkForwardConfig(4, 3, 3, 3),
            ObjectiveConfig(minimum_trades=0),
            validation_candidate_count=2,
            initial_cash=Decimal("100000"),
            monte_carlo_min_trades=20,
            monte_carlo_simulations=100,
            eligibility=EligibilityConfig(minimum_trades=20),
        )
        result = ResearchExperimentRunner().run(bars, MovingAverageStrategyFactory(), space, config)
        snapshot = asdict(result)
        self.repository.save_experiment(
            result.experiment_id,
            {"research_config": asdict(config), "parameter_space": space.to_dict()},
            snapshot,
            datetime.now(UTC),
        )
        return {"id": result.experiment_id, "config": asdict(config), "result": snapshot}

    def get(self, experiment_id: str) -> dict[str, object] | None:
        return self.repository.get_experiment(experiment_id)

    def list(self) -> list[dict[str, object]]:
        return self.repository.list_experiments()
