import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from enum import StrEnum

from quantlab.persistence import RunRepository
from quantlab.research_engine import ResearchExperimentResult

logger = logging.getLogger(__name__)


class ReproductionStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"


def deterministic_result_identity(result: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(result, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_reproduction(
    repository: RunRepository,
    experiment_id: str,
    reproduce: Callable[[dict[str, object]], ResearchExperimentResult] | None,
) -> tuple[ReproductionStatus, str]:
    persisted = repository.get_experiment(experiment_id)
    lineage = repository.lineage(experiment_id)
    if persisted is None or lineage is None:
        return ReproductionStatus.NOT_REPRODUCIBLE, "missing_experiment"
    dataset = lineage.get("dataset")
    if not dataset:
        return ReproductionStatus.NOT_REPRODUCIBLE, "missing_dataset"
    if reproduce is None:
        return ReproductionStatus.NOT_REPRODUCIBLE, "missing_strategy_version"
    config = persisted["config"]
    expected = persisted["result"]
    if not isinstance(config, dict) or not isinstance(expected, dict):
        return ReproductionStatus.NOT_REPRODUCIBLE, "invalid_snapshot"
    produced = asdict(reproduce(config))
    status = (
        ReproductionStatus.MATCH
        if deterministic_result_identity(produced) == deterministic_result_identity(expected)
        else ReproductionStatus.MISMATCH
    )
    logger.info("experiment_reproduction", extra={"experiment_id": experiment_id, "status": status})
    return status, "deterministic_identity_compared"
