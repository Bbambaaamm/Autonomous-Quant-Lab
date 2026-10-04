"""Statistical Trial Registry service — issue #272.

Append-only registry for research variants, holdout ledger, and
purged-validation foundation. All evidence is immutable; no UPDATE/DELETE.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.persistence import (
    StatisticalHoldoutAccessEventRecord,
    StatisticalHoldoutAssignmentRecord,
    StatisticalHoldoutRecord,
    StatisticalSplitPlanRecord,
    StatisticalTrialCampaignRecord,
    StatisticalTrialFamilyRecord,
    StatisticalTrialPartitionResultRecord,
    StatisticalTrialRecord,
    StatisticalValidationResultRecord,
)

# ---------------------------------------------------------------------------
# Canonical identity helpers
# ---------------------------------------------------------------------------


def canonical_json(value: object) -> str:
    """Deterministic JSON serialization for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def identity_hash(value: object) -> str:
    """SHA-256 of canonical JSON."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

TRIAL_KINDS = frozenset(
    {
        "STRATEGY_PARAMETER",
        "FUNNEL_VARIANT",
        "FORECAST_MODEL",
        "CALIBRATOR",
        "SIZING_POLICY",
        "SCENARIO_GRAPH",
        "PROMPT_MODEL_VARIANT",
    }
)

PARTITION_STATUSES = frozenset(
    {
        "COMPLETED",
        "INVALID_CONFIG",
        "FAILED",
        "INSUFFICIENT_DATA",
        "NOT_EVALUATED_BUDGET",
    }
)

HOLDOUT_ROLES = frozenset({"FINAL_OOS", "FORWARD_PAPER"})

ACCESS_EVENT_TYPES = frozenset(
    {
        "RESULT_COMPUTED_SEALED",
        "RESULT_REVEALED",
        "FEEDBACK_USED_FOR_ADAPTATION",
        "PROMOTION_EVALUATED",
    }
)

SPLIT_METHODS = frozenset(
    {
        "CHRONOLOGICAL_V1",
        "PURGED_WALK_FORWARD_V1",
        "CSCV_V1",
    }
)


# ---------------------------------------------------------------------------
# Dataclasses for service inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EconomicIdentity:
    """Canonical economic/statistical identity of a trial family."""

    strategy_family: str
    feature_dependencies: tuple[str, ...]
    universe_policy: str
    target_family: str
    rebalance_cadence: str
    risk_overlays: tuple[str, ...] = ()
    execution_overlays: tuple[str, ...] = ()
    funnel_dependency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_family": self.strategy_family,
            "feature_dependencies": list(self.feature_dependencies),
            "universe_policy": self.universe_policy,
            "target_family": self.target_family,
            "rebalance_cadence": self.rebalance_cadence,
            "risk_overlays": list(self.risk_overlays),
            "execution_overlays": list(self.execution_overlays),
            "funnel_dependency": self.funnel_dependency,
        }


@dataclass(frozen=True)
class TargetSpec:
    """Target/label specification for a family."""

    target_family: str
    resolution_horizon: str
    label_transform: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_family": self.target_family,
            "resolution_horizon": self.resolution_horizon,
            "label_transform": self.label_transform,
        }


@dataclass(frozen=True)
class OpportunityScope:
    """Opportunity scope for a family."""

    universe_id: str | None
    instrument_count: int | None
    date_range_start: str | None
    date_range_end: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "instrument_count": self.instrument_count,
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
        }


@dataclass(frozen=True)
class BenchmarkPolicy:
    """Benchmark/reference policy for a family."""

    benchmark_id: str | None
    benchmark_transform: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_transform": self.benchmark_transform,
        }


@dataclass(frozen=True)
class SplitPlanInput:
    """Input for creating a frozen split plan."""

    snapshot_id: str
    method: str
    target_spec_hash: str
    decision_time_policy: dict[str, Any]
    label_interval_policy: dict[str, Any]
    purge_policy: dict[str, Any]
    embargo_policy: dict[str, Any]
    partitions: list[dict[str, Any]]
    final_holdout_hash: str | None = None
    seed: int = 42

    def __post_init__(self) -> None:
        if self.method not in SPLIT_METHODS:
            raise ValueError(f"Unsupported split method: {self.method}")


@dataclass(frozen=True)
class CampaignInput:
    """Input for creating a trial campaign."""

    family_id: str
    snapshot_id: str
    split_plan_id: str
    selection_policy: dict[str, Any]
    code_sha: str
    seed: int = 42
    experiment_id: str | None = None
    parent_campaign_id: str | None = None
    adaptive_reason: str | None = None


@dataclass(frozen=True)
class TrialInput:
    """Input for declaring a trial before evaluation."""

    campaign_id: str
    family_id: str
    trial_kind: str
    variant: dict[str, Any]
    target_spec_hash: str
    code_sha: str
    counts_policy_version: int = 1
    parent_trial_id: str | None = None
    strategy_identity: str | None = None
    model_artifact: dict[str, Any] | None = None
    funnel_version_hash: str | None = None

    def __post_init__(self) -> None:
        if self.trial_kind not in TRIAL_KINDS:
            raise ValueError(f"Unsupported trial kind: {self.trial_kind}")


@dataclass(frozen=True)
class PartitionResultInput:
    """Input for recording a partition result."""

    trial_id: str
    campaign_id: str
    partition_id: str
    stage: str
    status: str
    n_observations: int
    n_returns: int
    metrics: dict[str, Any]
    sample_start: datetime | None = None
    sample_end: datetime | None = None
    mean_return: float | None = None
    variance_return: float | None = None
    skewness: float | None = None
    excess_kurtosis: float | None = None
    sharpe: float | None = None
    selection_score: float | None = None
    total_return: float | None = None
    max_drawdown: float | None = None
    returns: list[float] | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PARTITION_STATUSES:
            raise ValueError(f"Unsupported partition status: {self.status}")


@dataclass(frozen=True)
class HoldoutInput:
    """Input for creating a holdout definition."""

    split_plan_id: str
    snapshot_id: str
    role: str
    partition: dict[str, Any]
    policy_id: str
    policy_version: int = 1

    def __post_init__(self) -> None:
        if self.role not in HOLDOUT_ROLES:
            raise ValueError(f"Unsupported holdout role: {self.role}")


@dataclass(frozen=True)
class HoldoutAssignmentInput:
    """Input for assigning a candidate to a holdout."""

    holdout_id: str
    family_id: str
    campaign_id: str
    candidate_trial_id: str
    selection_evidence: dict[str, Any]


@dataclass(frozen=True)
class AccessEventInput:
    """Input for recording a holdout access event."""

    holdout_id: str
    family_id: str
    campaign_id: str
    event_type: str
    actor: dict[str, Any]
    component: str
    reason: str
    trial_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.event_type not in ACCESS_EVENT_TYPES:
            raise ValueError(f"Unsupported access event type: {self.event_type}")


@dataclass(frozen=True)
class ValidationResultInput:
    """Input for recording a statistical validation result."""

    family_id: str
    campaign_id: str
    candidate_trial_id: str
    method_id: str
    method_version: int
    input_trial_set_hash: str
    trial_count: int
    metrics: dict[str, Any]
    decision_state: str
    holdout_assignment_id: str | None = None
    effective_trial_count: float | None = None
    observed_sharpe: float | None = None
    psr: float | None = None
    dsr: float | None = None
    dsr_reference_sharpe: float | None = None
    pbo: float | None = None
    cscv_split_count: int | None = None


# ---------------------------------------------------------------------------
# Integrity hash
# ---------------------------------------------------------------------------


def _integrity_hash(record_type: str, fields: Mapping[str, Any]) -> str:
    """Compute integrity hash for a record."""
    payload = {"_type": record_type, **fields}
    return identity_hash(payload)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StatisticalRegistryService:
    """Service for the statistical trial registry.

    All operations are append-only. No UPDATE or DELETE is performed.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- Family -----------------------------------------------------------

    def get_or_create_family(
        self,
        economic_identity: EconomicIdentity,
        target_spec: TargetSpec,
        opportunity_scope: OpportunityScope,
        benchmark_policy: BenchmarkPolicy,
        registry_policy_id: str,
        registry_policy_version: int = 1,
        parent_family_id: str | None = None,
        created_by: dict[str, Any] | None = None,
    ) -> StatisticalTrialFamilyRecord:
        """Get existing family by economic identity or create new one.

        A new name or commit does NOT create a new family if economic
        identity remains the same.
        """
        econ_hash = identity_hash(economic_identity.to_dict())
        existing = self.session.execute(
            select(StatisticalTrialFamilyRecord).where(
                StatisticalTrialFamilyRecord.economic_identity_hash == econ_hash
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        family_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        record = StatisticalTrialFamilyRecord(
            family_id=family_id,
            created_at=now,
            schema_version=SCHEMA_VERSION,
            economic_identity_hash=econ_hash,
            economic_identity_json=canonical_json(economic_identity.to_dict()),
            target_spec_hash=identity_hash(target_spec.to_dict()),
            target_spec_json=canonical_json(target_spec.to_dict()),
            opportunity_scope_hash=identity_hash(opportunity_scope.to_dict()),
            opportunity_scope_json=canonical_json(opportunity_scope.to_dict()),
            benchmark_policy_hash=identity_hash(benchmark_policy.to_dict()),
            benchmark_policy_json=canonical_json(benchmark_policy.to_dict()),
            registry_policy_id=registry_policy_id,
            registry_policy_version=registry_policy_version,
            parent_family_id=parent_family_id,
            created_by_json=canonical_json(created_by or {}),
            integrity_hash=_integrity_hash(
                "statistical_trial_families",
                {
                    "family_id": family_id,
                    "economic_identity_hash": econ_hash,
                    "target_spec_hash": identity_hash(target_spec.to_dict()),
                    "opportunity_scope_hash": identity_hash(opportunity_scope.to_dict()),
                    "benchmark_policy_hash": identity_hash(benchmark_policy.to_dict()),
                    "registry_policy_id": registry_policy_id,
                    "registry_policy_version": registry_policy_version,
                    "parent_family_id": parent_family_id,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Split plan -------------------------------------------------------

    def create_split_plan(self, input: SplitPlanInput) -> StatisticalSplitPlanRecord:
        """Create a frozen split plan. Split is fixed before evaluation."""
        split_plan_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        record = StatisticalSplitPlanRecord(
            split_plan_id=split_plan_id,
            created_at=now,
            snapshot_id=input.snapshot_id,
            method=input.method,
            target_spec_hash=input.target_spec_hash,
            decision_time_policy=canonical_json(input.decision_time_policy),
            label_interval_policy=canonical_json(input.label_interval_policy),
            purge_policy_json=canonical_json(input.purge_policy),
            embargo_policy_json=canonical_json(input.embargo_policy),
            partitions_json=canonical_json(input.partitions),
            final_holdout_hash=input.final_holdout_hash,
            seed=input.seed,
            integrity_hash=_integrity_hash(
                "statistical_split_plans",
                {
                    "split_plan_id": split_plan_id,
                    "snapshot_id": input.snapshot_id,
                    "method": input.method,
                    "target_spec_hash": input.target_spec_hash,
                    "seed": input.seed,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Campaign ---------------------------------------------------------

    def create_campaign(self, input: CampaignInput) -> StatisticalTrialCampaignRecord:
        """Create a trial campaign."""
        campaign_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        record = StatisticalTrialCampaignRecord(
            campaign_id=campaign_id,
            family_id=input.family_id,
            created_at=now,
            snapshot_id=input.snapshot_id,
            experiment_id=input.experiment_id,
            split_plan_id=input.split_plan_id,
            selection_policy_hash=identity_hash(input.selection_policy),
            selection_policy_json=canonical_json(input.selection_policy),
            code_sha=input.code_sha,
            seed=input.seed,
            status_at_creation="PLANNED",
            parent_campaign_id=input.parent_campaign_id,
            adaptive_reason=input.adaptive_reason,
            integrity_hash=_integrity_hash(
                "statistical_trial_campaigns",
                {
                    "campaign_id": campaign_id,
                    "family_id": input.family_id,
                    "snapshot_id": input.snapshot_id,
                    "split_plan_id": input.split_plan_id,
                    "selection_policy_hash": identity_hash(input.selection_policy),
                    "code_sha": input.code_sha,
                    "seed": input.seed,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Trial ------------------------------------------------------------

    def declare_trial(self, input: TrialInput) -> StatisticalTrialRecord:
        """Declare a trial BEFORE evaluation. Failed trials are not deleted."""
        trial_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        variant_hash = identity_hash(input.variant)
        record = StatisticalTrialRecord(
            trial_id=trial_id,
            campaign_id=input.campaign_id,
            family_id=input.family_id,
            parent_trial_id=input.parent_trial_id,
            declared_at=now,
            trial_kind=input.trial_kind,
            variant_hash=variant_hash,
            variant_json=canonical_json(input.variant),
            strategy_identity=input.strategy_identity,
            model_artifact_json=(
                canonical_json(input.model_artifact) if input.model_artifact is not None else None
            ),
            funnel_version_hash=input.funnel_version_hash,
            target_spec_hash=input.target_spec_hash,
            code_sha=input.code_sha,
            counts_policy_version=input.counts_policy_version,
            integrity_hash=_integrity_hash(
                "statistical_trials",
                {
                    "trial_id": trial_id,
                    "campaign_id": input.campaign_id,
                    "family_id": input.family_id,
                    "variant_hash": variant_hash,
                    "trial_kind": input.trial_kind,
                    "target_spec_hash": input.target_spec_hash,
                    "code_sha": input.code_sha,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Partition results ------------------------------------------------

    def record_partition_result(
        self, input: PartitionResultInput
    ) -> StatisticalTrialPartitionResultRecord:
        """Record terminal evidence for a trial in a partition."""
        result_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        returns_hash = identity_hash(input.returns) if input.returns is not None else None
        record = StatisticalTrialPartitionResultRecord(
            result_id=result_id,
            trial_id=input.trial_id,
            campaign_id=input.campaign_id,
            partition_id=input.partition_id,
            stage=input.stage,
            status=input.status,
            evaluated_at=now,
            sample_start=input.sample_start,
            sample_end=input.sample_end,
            n_observations=input.n_observations,
            n_returns=input.n_returns,
            mean_return=input.mean_return,
            variance_return=input.variance_return,
            skewness=input.skewness,
            excess_kurtosis=input.excess_kurtosis,
            sharpe=input.sharpe,
            selection_score=input.selection_score,
            total_return=input.total_return,
            max_drawdown=input.max_drawdown,
            returns_hash=returns_hash,
            returns_json=(canonical_json(input.returns) if input.returns is not None else None),
            metrics_json=canonical_json(input.metrics),
            failure_reason=input.failure_reason,
            integrity_hash=_integrity_hash(
                "statistical_trial_partition_results",
                {
                    "result_id": result_id,
                    "trial_id": input.trial_id,
                    "campaign_id": input.campaign_id,
                    "partition_id": input.partition_id,
                    "stage": input.stage,
                    "status": input.status,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Holdout ----------------------------------------------------------

    def create_holdout(self, input: HoldoutInput) -> StatisticalHoldoutRecord:
        """Create an immutable holdout definition."""
        holdout_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        partition_hash = identity_hash(input.partition)
        record = StatisticalHoldoutRecord(
            holdout_id=holdout_id,
            split_plan_id=input.split_plan_id,
            snapshot_id=input.snapshot_id,
            role=input.role,
            created_at=now,
            partition_hash=partition_hash,
            partition_json=canonical_json(input.partition),
            policy_id=input.policy_id,
            policy_version=input.policy_version,
            integrity_hash=_integrity_hash(
                "statistical_holdouts",
                {
                    "holdout_id": holdout_id,
                    "split_plan_id": input.split_plan_id,
                    "snapshot_id": input.snapshot_id,
                    "role": input.role,
                    "partition_hash": partition_hash,
                    "policy_id": input.policy_id,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def assign_holdout_candidate(
        self, input: HoldoutAssignmentInput
    ) -> StatisticalHoldoutAssignmentRecord:
        """Assign a winning candidate to a holdout.

        Must be done BEFORE computing/revealing the final holdout.
        """
        assignment_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        selection_evidence_hash = identity_hash(input.selection_evidence)
        record = StatisticalHoldoutAssignmentRecord(
            assignment_id=assignment_id,
            holdout_id=input.holdout_id,
            family_id=input.family_id,
            campaign_id=input.campaign_id,
            candidate_trial_id=input.candidate_trial_id,
            assigned_at=now,
            selection_evidence_hash=selection_evidence_hash,
            selection_evidence_json=canonical_json(input.selection_evidence),
            integrity_hash=_integrity_hash(
                "statistical_holdout_assignments",
                {
                    "assignment_id": assignment_id,
                    "holdout_id": input.holdout_id,
                    "family_id": input.family_id,
                    "campaign_id": input.campaign_id,
                    "candidate_trial_id": input.candidate_trial_id,
                    "selection_evidence_hash": selection_evidence_hash,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def has_prior_reveal(self, holdout_id: str, family_id: str) -> bool:
        """Check if a RESULT_REVEALED event already exists for this holdout/family."""
        result = self.session.execute(
            select(StatisticalHoldoutAccessEventRecord).where(
                StatisticalHoldoutAccessEventRecord.holdout_id == holdout_id,
                StatisticalHoldoutAccessEventRecord.family_id == family_id,
                StatisticalHoldoutAccessEventRecord.event_type == "RESULT_REVEALED",
            )
        ).first()
        return result is not None

    def record_access_event(self, input: AccessEventInput) -> StatisticalHoldoutAccessEventRecord:
        """Record a holdout access event (single-use/burn ledger).

        No UPDATE on status. First RESULT_REVEALED means the holdout is
        burned for any new/adapted variant of the family.
        """
        access_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        payload_hash = identity_hash(input.payload or {})
        record = StatisticalHoldoutAccessEventRecord(
            access_id=access_id,
            holdout_id=input.holdout_id,
            family_id=input.family_id,
            campaign_id=input.campaign_id,
            trial_id=input.trial_id,
            occurred_at=now,
            event_type=input.event_type,
            actor_json=canonical_json(input.actor),
            component=input.component,
            reason=input.reason,
            correlation_id=input.correlation_id,
            payload_hash=payload_hash,
            integrity_hash=_integrity_hash(
                "statistical_holdout_access_events",
                {
                    "access_id": access_id,
                    "holdout_id": input.holdout_id,
                    "family_id": input.family_id,
                    "campaign_id": input.campaign_id,
                    "event_type": input.event_type,
                    "occurred_at": now.isoformat(),
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Validation results -----------------------------------------------

    def record_validation_result(
        self, input: ValidationResultInput
    ) -> StatisticalValidationResultRecord:
        """Record an append-only statistical validation result."""
        validation_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        record = StatisticalValidationResultRecord(
            validation_id=validation_id,
            family_id=input.family_id,
            campaign_id=input.campaign_id,
            candidate_trial_id=input.candidate_trial_id,
            holdout_assignment_id=input.holdout_assignment_id,
            method_id=input.method_id,
            method_version=input.method_version,
            evaluated_at=now,
            input_trial_set_hash=input.input_trial_set_hash,
            trial_count=input.trial_count,
            effective_trial_count=input.effective_trial_count,
            observed_sharpe=input.observed_sharpe,
            psr=input.psr,
            dsr=input.dsr,
            dsr_reference_sharpe=input.dsr_reference_sharpe,
            pbo=input.pbo,
            cscv_split_count=input.cscv_split_count,
            metrics_json=canonical_json(input.metrics),
            decision_state=input.decision_state,
            integrity_hash=_integrity_hash(
                "statistical_validation_results",
                {
                    "validation_id": validation_id,
                    "family_id": input.family_id,
                    "campaign_id": input.campaign_id,
                    "candidate_trial_id": input.candidate_trial_id,
                    "method_id": input.method_id,
                    "method_version": input.method_version,
                    "input_trial_set_hash": input.input_trial_set_hash,
                    "trial_count": input.trial_count,
                },
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- Queries -----------------------------------------------------------

    def get_family_trials(self, family_id: str) -> list[StatisticalTrialRecord]:
        """Get all trials for a family."""
        return list(
            self.session.execute(
                select(StatisticalTrialRecord).where(StatisticalTrialRecord.family_id == family_id)
            )
            .scalars()
            .all()
        )

    def get_campaign_trials(self, campaign_id: str) -> list[StatisticalTrialRecord]:
        """Get all trials for a campaign."""
        return list(
            self.session.execute(
                select(StatisticalTrialRecord).where(
                    StatisticalTrialRecord.campaign_id == campaign_id
                )
            )
            .scalars()
            .all()
        )

    def get_trial_results(self, trial_id: str) -> list[StatisticalTrialPartitionResultRecord]:
        """Get all partition results for a trial."""
        return list(
            self.session.execute(
                select(StatisticalTrialPartitionResultRecord).where(
                    StatisticalTrialPartitionResultRecord.trial_id == trial_id
                )
            )
            .scalars()
            .all()
        )

    def get_holdout_access_events(
        self, holdout_id: str, family_id: str
    ) -> list[StatisticalHoldoutAccessEventRecord]:
        """Get all access events for a holdout/family."""
        return list(
            self.session.execute(
                select(StatisticalHoldoutAccessEventRecord).where(
                    StatisticalHoldoutAccessEventRecord.holdout_id == holdout_id,
                    StatisticalHoldoutAccessEventRecord.family_id == family_id,
                )
            )
            .scalars()
            .all()
        )
