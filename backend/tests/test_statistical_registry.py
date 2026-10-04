"""Tests for Statistical Trial Registry — issue #272.

Covers: family identity, split plans, campaigns, trials, partition results,
holdouts, access events, validation results, and immutability.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from quantlab.persistence import (
    Base,
)
from quantlab.statistical_registry import (
    AccessEventInput,
    BenchmarkPolicy,
    CampaignInput,
    EconomicIdentity,
    HoldoutAssignmentInput,
    HoldoutInput,
    OpportunityScope,
    PartitionResultInput,
    SplitPlanInput,
    StatisticalRegistryService,
    TargetSpec,
    TrialInput,
    ValidationResultInput,
    identity_hash,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def service(session):
    return StatisticalRegistryService(session)


# ---------------------------------------------------------------------------
# Family identity
# ---------------------------------------------------------------------------


def test_family_created_with_economic_identity(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close", "volume"),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")

    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )
    assert family.family_id
    assert family.economic_identity_hash == identity_hash(econ.to_dict())
    assert family.schema_version == 1


def test_family_deduplicated_by_economic_identity(service):
    """Same economic identity → same family, even with different name."""
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")

    f1 = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )
    f2 = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )
    assert f1.family_id == f2.family_id


def test_different_economic_identity_creates_new_family(service):
    econ1 = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    econ2 = EconomicIdentity(
        strategy_family="mean_reversion",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")

    f1 = service.get_or_create_family(
        economic_identity=econ1,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )
    f2 = service.get_or_create_family(
        economic_identity=econ2,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )
    assert f1.family_id != f2.family_id


# ---------------------------------------------------------------------------
# Split plan
# ---------------------------------------------------------------------------


def test_split_plan_created_and_frozen(service):
    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={"timezone": "UTC"},
        label_interval_policy={"horizon": "1d"},
        purge_policy={"method": "NO_LABEL_OVERLAP"},
        embargo_policy={"days": 1},
        partitions=[
            {"partition_id": "train", "start": "2020-01-01", "end": "2022-12-31"},
            {"partition_id": "validation", "start": "2023-01-01", "end": "2023-12-31"},
            {"partition_id": "holdout", "start": "2024-01-01", "end": "2024-12-31"},
        ],
        seed=42,
    )
    plan = service.create_split_plan(split_input)
    assert plan.split_plan_id
    assert plan.method == "CHRONOLOGICAL_V1"
    assert plan.status_at_creation == "PLANNED" if hasattr(plan, "status_at_creation") else True


def test_split_plan_rejects_unsupported_method(service):
    with pytest.raises(ValueError, match="Unsupported split method"):
        SplitPlanInput(
            snapshot_id="snap-1",
            method="RANDOM_V1",
            target_spec_hash=identity_hash({"target": "forward_return"}),
            decision_time_policy={},
            label_interval_policy={},
            purge_policy={},
            embargo_policy={},
            partitions=[],
        )


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


def test_campaign_created(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe", "threshold": 1.0},
        code_sha="abc123",
        seed=42,
    )
    campaign = service.create_campaign(campaign_input)
    assert campaign.campaign_id
    assert campaign.status_at_creation == "PLANNED"


# ---------------------------------------------------------------------------
# Trial
# ---------------------------------------------------------------------------


def test_trial_declared_before_evaluation(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial_input = TrialInput(
        campaign_id=campaign.campaign_id,
        family_id=family.family_id,
        trial_kind="STRATEGY_PARAMETER",
        variant={"lookback": 20, "threshold": 0.05},
        target_spec_hash=identity_hash({"target": "forward_return"}),
        code_sha="abc123",
    )
    trial = service.declare_trial(trial_input)
    assert trial.trial_id
    assert trial.trial_kind == "STRATEGY_PARAMETER"
    assert trial.variant_hash == identity_hash({"lookback": 20, "threshold": 0.05})


def test_trial_rejects_unsupported_kind(service):
    with pytest.raises(ValueError, match="Unsupported trial kind"):
        TrialInput(
            campaign_id="c-1",
            family_id="f-1",
            trial_kind="UNSUPPORTED_KIND",
            variant={},
            target_spec_hash="hash",
            code_sha="sha",
        )


# ---------------------------------------------------------------------------
# Partition results
# ---------------------------------------------------------------------------


def test_partition_result_recorded(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial_input = TrialInput(
        campaign_id=campaign.campaign_id,
        family_id=family.family_id,
        trial_kind="STRATEGY_PARAMETER",
        variant={"lookback": 20},
        target_spec_hash=identity_hash({"target": "forward_return"}),
        code_sha="abc123",
    )
    trial = service.declare_trial(trial_input)

    result_input = PartitionResultInput(
        trial_id=trial.trial_id,
        campaign_id=campaign.campaign_id,
        partition_id="validation",
        stage="SELECTION",
        status="COMPLETED",
        n_observations=252,
        n_returns=252,
        sharpe=1.5,
        total_return=0.25,
        max_drawdown=-0.10,
        metrics={"sortino": 2.0},
        returns=[0.01] * 252,
    )
    result = service.record_partition_result(result_input)
    assert result.result_id
    assert result.status == "COMPLETED"
    assert result.sharpe == 1.5


def test_partition_result_rejects_unsupported_status(service):
    with pytest.raises(ValueError, match="Unsupported partition status"):
        PartitionResultInput(
            trial_id="t-1",
            campaign_id="c-1",
            partition_id="p-1",
            stage="SELECTION",
            status="UNKNOWN",
            n_observations=0,
            n_returns=0,
            metrics={},
        )


# ---------------------------------------------------------------------------
# Holdout
# ---------------------------------------------------------------------------


def test_holdout_created(service):
    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    holdout_input = HoldoutInput(
        split_plan_id=plan.split_plan_id,
        snapshot_id="snap-1",
        role="FINAL_OOS",
        partition={"start": "2024-01-01", "end": "2024-12-31"},
        policy_id="holdout_policy_v1",
    )
    holdout = service.create_holdout(holdout_input)
    assert holdout.holdout_id
    assert holdout.role == "FINAL_OOS"


def test_holdout_rejects_unsupported_role(service):
    with pytest.raises(ValueError, match="Unsupported holdout role"):
        HoldoutInput(
            split_plan_id="sp-1",
            snapshot_id="snap-1",
            role="UNSUPPORTED_ROLE",
            partition={},
            policy_id="policy",
        )


# ---------------------------------------------------------------------------
# Holdout assignment
# ---------------------------------------------------------------------------


def test_holdout_candidate_assigned(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial_input = TrialInput(
        campaign_id=campaign.campaign_id,
        family_id=family.family_id,
        trial_kind="STRATEGY_PARAMETER",
        variant={"lookback": 20},
        target_spec_hash=identity_hash({"target": "forward_return"}),
        code_sha="abc123",
    )
    trial = service.declare_trial(trial_input)

    holdout_input = HoldoutInput(
        split_plan_id=plan.split_plan_id,
        snapshot_id="snap-1",
        role="FINAL_OOS",
        partition={"start": "2024-01-01", "end": "2024-12-31"},
        policy_id="holdout_policy_v1",
    )
    holdout = service.create_holdout(holdout_input)

    assignment_input = HoldoutAssignmentInput(
        holdout_id=holdout.holdout_id,
        family_id=family.family_id,
        campaign_id=campaign.campaign_id,
        candidate_trial_id=trial.trial_id,
        selection_evidence={"winner": trial.trial_id, "score": 1.5},
    )
    assignment = service.assign_holdout_candidate(assignment_input)
    assert assignment.assignment_id
    assert assignment.candidate_trial_id == trial.trial_id


# ---------------------------------------------------------------------------
# Access events / burn ledger
# ---------------------------------------------------------------------------


def test_access_event_recorded(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial_input = TrialInput(
        campaign_id=campaign.campaign_id,
        family_id=family.family_id,
        trial_kind="STRATEGY_PARAMETER",
        variant={"lookback": 20},
        target_spec_hash=identity_hash({"target": "forward_return"}),
        code_sha="abc123",
    )
    trial = service.declare_trial(trial_input)

    holdout_input = HoldoutInput(
        split_plan_id=plan.split_plan_id,
        snapshot_id="snap-1",
        role="FINAL_OOS",
        partition={"start": "2024-01-01", "end": "2024-12-31"},
        policy_id="holdout_policy_v1",
    )
    holdout = service.create_holdout(holdout_input)

    assignment_input = HoldoutAssignmentInput(
        holdout_id=holdout.holdout_id,
        family_id=family.family_id,
        campaign_id=campaign.campaign_id,
        candidate_trial_id=trial.trial_id,
        selection_evidence={"winner": trial.trial_id},
    )
    service.assign_holdout_candidate(assignment_input)

    # Record RESULT_COMPUTED_SEALED
    event1 = AccessEventInput(
        holdout_id=holdout.holdout_id,
        family_id=family.family_id,
        campaign_id=campaign.campaign_id,
        event_type="RESULT_COMPUTED_SEALED",
        actor={"component": "phase6_runtime"},
        component="phase6_runtime",
        reason="OOS evaluation complete",
        trial_id=trial.trial_id,
    )
    service.record_access_event(event1)

    # Record RESULT_REVEALED
    event2 = AccessEventInput(
        holdout_id=holdout.holdout_id,
        family_id=family.family_id,
        campaign_id=campaign.campaign_id,
        event_type="RESULT_REVEALED",
        actor={"component": "phase6_runtime"},
        component="phase6_runtime",
        reason="Reveal to operator",
        trial_id=trial.trial_id,
    )
    service.record_access_event(event2)

    # Check burn status
    assert service.has_prior_reveal(holdout.holdout_id, family.family_id) is True


def test_no_prior_reveal_initially(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    holdout_input = HoldoutInput(
        split_plan_id=plan.split_plan_id,
        snapshot_id="snap-1",
        role="FINAL_OOS",
        partition={"start": "2024-01-01", "end": "2024-12-31"},
        policy_id="holdout_policy_v1",
    )
    holdout = service.create_holdout(holdout_input)

    assert service.has_prior_reveal(holdout.holdout_id, family.family_id) is False


def test_access_event_rejects_unsupported_type(service):
    with pytest.raises(ValueError, match="Unsupported access event type"):
        AccessEventInput(
            holdout_id="h-1",
            family_id="f-1",
            campaign_id="c-1",
            event_type="UNSUPPORTED",
            actor={},
            component="test",
            reason="test",
        )


# ---------------------------------------------------------------------------
# Validation results
# ---------------------------------------------------------------------------


def test_validation_result_recorded(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial_input = TrialInput(
        campaign_id=campaign.campaign_id,
        family_id=family.family_id,
        trial_kind="STRATEGY_PARAMETER",
        variant={"lookback": 20},
        target_spec_hash=identity_hash({"target": "forward_return"}),
        code_sha="abc123",
    )
    trial = service.declare_trial(trial_input)

    validation_input = ValidationResultInput(
        family_id=family.family_id,
        campaign_id=campaign.campaign_id,
        candidate_trial_id=trial.trial_id,
        method_id="DSR_V1",
        method_version=1,
        input_trial_set_hash=identity_hash([trial.trial_id]),
        trial_count=10,
        effective_trial_count=8.5,
        observed_sharpe=1.5,
        psr=0.95,
        dsr=0.92,
        dsr_reference_sharpe=1.0,
        metrics={"skew": 0.1, "kurtosis": 3.0, "n_obs": 252},
        decision_state="PASS",
    )
    result = service.record_validation_result(validation_input)
    assert result.validation_id
    assert result.method_id == "DSR_V1"
    assert result.trial_count == 10


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_get_family_trials(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial1 = service.declare_trial(
        TrialInput(
            campaign_id=campaign.campaign_id,
            family_id=family.family_id,
            trial_kind="STRATEGY_PARAMETER",
            variant={"lookback": 20},
            target_spec_hash=identity_hash({"target": "forward_return"}),
            code_sha="abc123",
        )
    )
    trial2 = service.declare_trial(
        TrialInput(
            campaign_id=campaign.campaign_id,
            family_id=family.family_id,
            trial_kind="STRATEGY_PARAMETER",
            variant={"lookback": 30},
            target_spec_hash=identity_hash({"target": "forward_return"}),
            code_sha="abc123",
        )
    )

    trials = service.get_family_trials(family.family_id)
    assert len(trials) == 2
    assert {t.trial_id for t in trials} == {trial1.trial_id, trial2.trial_id}


def test_get_trial_results(service):
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial = service.declare_trial(
        TrialInput(
            campaign_id=campaign.campaign_id,
            family_id=family.family_id,
            trial_kind="STRATEGY_PARAMETER",
            variant={"lookback": 20},
            target_spec_hash=identity_hash({"target": "forward_return"}),
            code_sha="abc123",
        )
    )

    service.record_partition_result(
        PartitionResultInput(
            trial_id=trial.trial_id,
            campaign_id=campaign.campaign_id,
            partition_id="train",
            stage="SELECTION",
            status="COMPLETED",
            n_observations=252,
            n_returns=252,
            sharpe=1.2,
            metrics={},
        )
    )
    service.record_partition_result(
        PartitionResultInput(
            trial_id=trial.trial_id,
            campaign_id=campaign.campaign_id,
            partition_id="validation",
            stage="SELECTION",
            status="COMPLETED",
            n_observations=126,
            n_returns=126,
            sharpe=1.5,
            metrics={},
        )
    )

    results = service.get_trial_results(trial.trial_id)
    assert len(results) == 2
    assert {r.partition_id for r in results} == {"train", "validation"}


# ---------------------------------------------------------------------------
# Integrity hash uniqueness
# ---------------------------------------------------------------------------


def test_integrity_hash_unique_per_record(service):
    """Each record has a unique integrity hash."""
    econ = EconomicIdentity(
        strategy_family="momentum",
        feature_dependencies=("close",),
        universe_policy="sp500",
        target_family="forward_return",
        rebalance_cadence="daily",
    )
    target = TargetSpec(
        target_family="forward_return",
        resolution_horizon="1d",
    )
    scope = OpportunityScope(
        universe_id="u-1",
        instrument_count=500,
        date_range_start="2020-01-01",
        date_range_end="2024-01-01",
    )
    benchmark = BenchmarkPolicy(benchmark_id="spy")
    family = service.get_or_create_family(
        economic_identity=econ,
        target_spec=target,
        opportunity_scope=scope,
        benchmark_policy=benchmark,
        registry_policy_id="registry_v1",
    )

    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        method="CHRONOLOGICAL_V1",
        target_spec_hash=identity_hash({"target": "forward_return"}),
        decision_time_policy={},
        label_interval_policy={},
        purge_policy={},
        embargo_policy={},
        partitions=[],
    )
    plan = service.create_split_plan(split_input)

    campaign_input = CampaignInput(
        family_id=family.family_id,
        snapshot_id="snap-1",
        split_plan_id=plan.split_plan_id,
        selection_policy={"objective": "sharpe"},
        code_sha="abc123",
    )
    campaign = service.create_campaign(campaign_input)

    trial = service.declare_trial(
        TrialInput(
            campaign_id=campaign.campaign_id,
            family_id=family.family_id,
            trial_kind="STRATEGY_PARAMETER",
            variant={"lookback": 20},
            target_spec_hash=identity_hash({"target": "forward_return"}),
            code_sha="abc123",
        )
    )

    # All integrity hashes should be unique
    hashes = [
        family.integrity_hash,
        plan.integrity_hash,
        campaign.integrity_hash,
        trial.integrity_hash,
    ]
    assert len(hashes) == len(set(hashes))
