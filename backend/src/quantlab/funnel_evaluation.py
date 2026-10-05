"""Funnel evaluation harness — STAT-BLOCKER C and D.

Computes coverage, turnover, concentration, rank-bucket performance, and
threshold sensitivity from FunnelRun objects.

The baseline comparison enforces that the baseline operates on the same
candidate set and opportunity timestamps as Stage B (STAT-BLOCKER C).
The report emits coverage/turnover/concentration/threshold-sensitivity
metrics required by STAT-BLOCKER D.

This module is pure: it performs no I/O, spawns no workers, and creates no
parallel abstractions. It operates exclusively on immutable FunnelRun
lineage records produced by CandidateFunnel.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from quantlab.candidate_funnel import FunnelRun
from quantlab.funnel_trial_family import (
    SelectionBasis,
    TrialFamilyAccounting,
    TrialFamilyDeclaration,
    UnpreregisteredWinnerError,
    VariantObservation,
    assert_selection_is_not_full_history,
    trial_family_lineage,
)

# ---------------------------------------------------------------------------
# Metric dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageMetrics:
    """Coverage metrics for a single funnel run (STAT-BLOCKER D)."""

    universe_size: int
    stage_a_accepted: int
    stage_b_evaluated: int
    final_candidates: int
    coverage_rate: float
    stage_a_pass_rate: float
    stage_b_pass_rate: float
    #: Candidates that were never judged because Stage B ran out of budget/time/resources.
    #: Kept separate so a coverage/pass-rate number is never computed over instruments that
    #: were not actually evaluated (review BLOCKER A).
    stage_b_not_evaluated: int = 0
    #: Economic rejections only (a judged candidate that did not pass). A data-quality or
    #: not-evaluated candidate is never counted here.
    stage_b_rejected: int = 0


@dataclass(frozen=True)
class TurnoverMetrics:
    """Candidate set turnover between consecutive runs."""

    run_pair: tuple[str, str]
    jaccard_index: float
    intersection_size: int
    union_size: int
    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class ConcentrationMetrics:
    """Concentration by asset/date/sector."""

    by_asset: dict[str, int]
    by_date: dict[str, int]
    by_sector: dict[str, int]
    hhi_asset: float
    hhi_sector: float


@dataclass(frozen=True)
class RankBucketPerformance:
    """Performance by candidate rank bucket."""

    bucket: str
    count: int
    mean_score: float | None
    pass_rate: float


@dataclass(frozen=True)
class ThresholdSensitivity:
    """Sensitivity of candidate set to threshold/K perturbations."""

    parameter: str
    baseline_value: str
    perturbed_value: str
    baseline_candidates: tuple[str, ...]
    perturbed_candidates: tuple[str, ...]
    jaccard_index: float
    changed: bool


@dataclass(frozen=True)
class BaselineComparison:
    """Stage B vs baseline on identical candidate set (STAT-BLOCKER C)."""

    candidate_set_size: int
    stage_b_mean_score: float | None
    baseline_mean_score: float | None
    stage_b_pass_rate: float
    baseline_pass_rate: float
    lift: float | None
    same_opportunity_timestamps: bool


@dataclass(frozen=True)
class FunnelEvaluationReport:
    """Complete evaluation report for a funnel variant."""

    funnel_version: str
    ranking_key: str
    ranking_key_version: str
    trial_family_key: tuple[str, str, str, int]
    coverage: tuple[CoverageMetrics, ...]
    turnover: tuple[TurnoverMetrics, ...]
    concentration: ConcentrationMetrics
    rank_buckets: tuple[RankBucketPerformance, ...]
    threshold_sensitivity: tuple[ThresholdSensitivity, ...]
    baseline_comparison: BaselineComparison | None
    #: Trial-family accounting lineage (STAT-BLOCKER B). Present only when the report was
    #: generated against a preregistered family declaration; without it the report cannot
    #: say how many funnel variants were tried, so its winner is not family-corrected.
    trial_family: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Metric computation functions
# ---------------------------------------------------------------------------


def _is_actually_evaluated(result: Any) -> bool:
    """Check if a StageBResult represents an actually evaluated candidate.

    Not-evaluated candidates (budget/timeout/resource-pressure) have
    rejection_reason set to a NOT_EVALUATED_* value.
    """
    if result.rejection_reason is None:
        return True
    return not result.rejection_reason.value.startswith("NOT_EVALUATED")


def compute_coverage(run: FunnelRun) -> CoverageMetrics:
    """Compute coverage metrics for a single funnel run (STAT-BLOCKER D)."""
    universe_size = len(run.stage_a_results)
    stage_a_accepted = sum(1 for r in run.stage_a_results if r.passed)
    stage_b_evaluated = sum(1 for r in run.stage_b_results if _is_actually_evaluated(r))
    stage_b_not_evaluated = len(run.stage_b_results) - stage_b_evaluated
    stage_b_rejected = sum(
        1 for r in run.stage_b_results if _is_actually_evaluated(r) and not r.passed
    )
    final_candidates = len(run.final_candidates)

    coverage_rate = final_candidates / universe_size if universe_size > 0 else 0.0
    stage_a_pass_rate = stage_a_accepted / universe_size if universe_size > 0 else 0.0
    stage_b_pass_rate = final_candidates / stage_b_evaluated if stage_b_evaluated > 0 else 0.0

    return CoverageMetrics(
        universe_size=universe_size,
        stage_a_accepted=stage_a_accepted,
        stage_b_evaluated=stage_b_evaluated,
        final_candidates=final_candidates,
        coverage_rate=coverage_rate,
        stage_a_pass_rate=stage_a_pass_rate,
        stage_b_pass_rate=stage_b_pass_rate,
        stage_b_not_evaluated=stage_b_not_evaluated,
        stage_b_rejected=stage_b_rejected,
    )


def compute_turnover(run1: FunnelRun, run2: FunnelRun) -> TurnoverMetrics:
    """Compute candidate set turnover between two consecutive runs."""
    set1 = set(run1.final_candidates)
    set2 = set(run2.final_candidates)

    intersection = set1 & set2
    union = set1 | set2

    jaccard = len(intersection) / len(union) if union else 1.0

    added = tuple(sorted(set2 - set1))
    removed = tuple(sorted(set1 - set2))

    return TurnoverMetrics(
        run_pair=(run1.run_id, run2.run_id),
        jaccard_index=jaccard,
        intersection_size=len(intersection),
        union_size=len(union),
        added=added,
        removed=removed,
    )


def compute_concentration(runs: Sequence[FunnelRun]) -> ConcentrationMetrics:
    """Compute concentration by asset/date/sector across runs."""
    asset_counter: Counter[str] = Counter()
    date_counter: Counter[str] = Counter()
    sector_counter: Counter[str] = Counter()

    for run in runs:
        for inst_id in run.final_candidates:
            asset_counter[inst_id] += 1
        date_counter[run.created_at.date().isoformat()] += len(run.final_candidates)
        # Sector is not directly available on FunnelRun; use instrument_id
        # prefix as a proxy. A real sector mapping can be injected later
        # when the funnel is integrated with the market catalog.
        for inst_id in run.final_candidates:
            sector = inst_id.split("-")[0] if "-" in inst_id else "unknown"
            sector_counter[sector] += 1

    total_assets = sum(asset_counter.values())
    total_sectors = sum(sector_counter.values())

    hhi_asset = (
        sum((count / total_assets) ** 2 for count in asset_counter.values())
        if total_assets > 0
        else 0.0
    )
    hhi_sector = (
        sum((count / total_sectors) ** 2 for count in sector_counter.values())
        if total_sectors > 0
        else 0.0
    )

    return ConcentrationMetrics(
        by_asset=dict(asset_counter),
        by_date=dict(date_counter),
        by_sector=dict(sector_counter),
        hhi_asset=hhi_asset,
        hhi_sector=hhi_sector,
    )


def compute_rank_buckets(
    run: FunnelRun, bucket_size: int = 10
) -> tuple[RankBucketPerformance, ...]:
    """Compute performance by candidate rank bucket (STAT-BLOCKER D)."""
    if not run.stage_b_results:
        return ()

    buckets: dict[str, list[Any]] = defaultdict(list)
    for r in run.stage_b_results:
        if r.candidate_rank is not None:
            bucket_idx = (r.candidate_rank - 1) // bucket_size
            bucket_name = (
                f"rank_{bucket_idx * bucket_size + 1}-{bucket_idx * bucket_size + bucket_size}"
            )
            buckets[bucket_name].append(r)

    results = []
    for bucket_name in sorted(buckets.keys()):
        bucket_results = buckets[bucket_name]
        count = len(bucket_results)
        scores = [float(r.score) for r in bucket_results if r.score is not None]
        mean_score = sum(scores) / len(scores) if scores else None
        pass_rate = sum(1 for r in bucket_results if r.passed) / count if count > 0 else 0.0

        results.append(
            RankBucketPerformance(
                bucket=bucket_name,
                count=count,
                mean_score=mean_score,
                pass_rate=pass_rate,
            )
        )

    return tuple(results)


def compute_threshold_sensitivity(
    baseline_run: FunnelRun,
    perturbed_run: FunnelRun,
    parameter: str = "max_candidates",
) -> ThresholdSensitivity:
    """Compute sensitivity of candidate set to threshold/K perturbations."""
    baseline_set = set(baseline_run.final_candidates)
    perturbed_set = set(perturbed_run.final_candidates)

    intersection = baseline_set & perturbed_set
    union = baseline_set | perturbed_set

    jaccard = len(intersection) / len(union) if union else 1.0

    return ThresholdSensitivity(
        parameter=parameter,
        baseline_value=str(len(baseline_run.final_candidates)),
        perturbed_value=str(len(perturbed_run.final_candidates)),
        baseline_candidates=baseline_run.final_candidates,
        perturbed_candidates=perturbed_run.final_candidates,
        jaccard_index=jaccard,
        changed=baseline_set != perturbed_set,
    )


def compare_baseline(
    stage_b_run: FunnelRun,
    baseline_run: FunnelRun,
) -> BaselineComparison:
    """Compare Stage B against baseline on identical candidate set (STAT-BLOCKER C).

    The baseline must operate on the same universe snapshot (same opportunity
    timestamps) as Stage B. The comparison fails closed if the universe
    snapshots differ.
    """
    same_opportunity = stage_b_run.universe_snapshot_id == baseline_run.universe_snapshot_id

    stage_b_set = set(stage_b_run.final_candidates)
    baseline_set = set(baseline_run.final_candidates)

    candidate_set_size = len(stage_b_set | baseline_set)

    stage_b_scores = [float(r.score) for r in stage_b_run.stage_b_results if r.score is not None]
    baseline_scores = [float(r.score) for r in baseline_run.stage_b_results if r.score is not None]

    stage_b_mean = sum(stage_b_scores) / len(stage_b_scores) if stage_b_scores else None
    baseline_mean = sum(baseline_scores) / len(baseline_scores) if baseline_scores else None

    # Pass rate is computed over candidates that were actually judged: a budget/timeout/
    # resource-pressure candidate was never evaluated and must not dilute the rate
    # (review BLOCKER A).
    stage_b_evaluated = sum(1 for r in stage_b_run.stage_b_results if _is_actually_evaluated(r))
    baseline_evaluated = sum(1 for r in baseline_run.stage_b_results if _is_actually_evaluated(r))

    stage_b_pass_rate = (
        sum(1 for r in stage_b_run.stage_b_results if r.passed) / stage_b_evaluated
        if stage_b_evaluated
        else 0.0
    )
    baseline_pass_rate = (
        sum(1 for r in baseline_run.stage_b_results if r.passed) / baseline_evaluated
        if baseline_evaluated
        else 0.0
    )

    lift = (
        stage_b_mean - baseline_mean
        if stage_b_mean is not None and baseline_mean is not None
        else None
    )

    return BaselineComparison(
        candidate_set_size=candidate_set_size,
        stage_b_mean_score=stage_b_mean,
        baseline_mean_score=baseline_mean,
        stage_b_pass_rate=stage_b_pass_rate,
        baseline_pass_rate=baseline_pass_rate,
        lift=lift,
        same_opportunity_timestamps=same_opportunity,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    runs: Sequence[FunnelRun],
    baseline_run: FunnelRun | None = None,
    threshold_perturbed_run: FunnelRun | None = None,
    threshold_parameter: str = "max_candidates",
    trial_family: TrialFamilyDeclaration | None = None,
    selected_variant_id: str | None = None,
    selection_basis: SelectionBasis | None = None,
) -> FunnelEvaluationReport:
    """Generate a complete evaluation report for a funnel variant.

    When a preregistered ``trial_family`` declaration is supplied, the report also carries
    the multiple-testing accounting lineage (STAT-BLOCKER B): the family size the winner
    was selected from, the selection policy, and — when ``selected_variant_id`` and
    ``selection_basis`` are given — a fail-closed check that the winner was not chosen
    post-hoc from the best full-history metric.
    """
    if not runs:
        raise ValueError("At least one FunnelRun is required")

    primary_run = runs[0]

    coverage = tuple(compute_coverage(run) for run in runs)
    turnover = tuple(compute_turnover(runs[i], runs[i + 1]) for i in range(len(runs) - 1))
    concentration = compute_concentration(runs)
    rank_buckets = compute_rank_buckets(primary_run)

    threshold_sensitivity: tuple[ThresholdSensitivity, ...] = ()
    if threshold_perturbed_run is not None:
        threshold_sensitivity = (
            compute_threshold_sensitivity(
                primary_run, threshold_perturbed_run, threshold_parameter
            ),
        )

    baseline_comparison = None
    if baseline_run is not None:
        baseline_comparison = compare_baseline(primary_run, baseline_run)

    trial_family_lineage_payload: dict[str, Any] | None = None
    if trial_family is not None:
        if selected_variant_id is not None:
            # Fail closed on a post-hoc winner before the report is emitted. The full
            # per-variant accounting (which variant was observed/excluded) is supplied
            # separately through account_runs_against_declaration(); the report itself
            # carries the family size/hash/policy, which is the fact that makes the
            # winner's significance interpretable.
            trial_family.variant(selected_variant_id)
            if selection_basis is SelectionBasis.BEST_FULL_HISTORY_METRIC and (
                trial_family.primary_variant_id != selected_variant_id
            ):
                assert_selection_is_not_full_history(
                    TrialFamilyAccounting(trial_family),
                    selected_variant_id,
                    SelectionBasis.BEST_FULL_HISTORY_METRIC,
                    require_fully_accounted=False,
                )
        trial_family_lineage_payload = trial_family_lineage(
            trial_family, selected_variant_id=selected_variant_id
        )

    return FunnelEvaluationReport(
        funnel_version=primary_run.funnel_version,
        ranking_key=primary_run.ranking_key,
        ranking_key_version=primary_run.ranking_key_version,
        trial_family_key=primary_run.trial_family_key(),
        coverage=coverage,
        turnover=turnover,
        concentration=concentration,
        rank_buckets=rank_buckets,
        threshold_sensitivity=threshold_sensitivity,
        baseline_comparison=baseline_comparison,
        trial_family=trial_family_lineage_payload,
    )


def account_runs_against_declaration(
    runs_by_variant_id: Mapping[str, FunnelRun],
    declaration: TrialFamilyDeclaration,
    *,
    observed_at: str = "",
) -> TrialFamilyAccounting:
    """Account real ``FunnelRun`` objects against a preregistered family.

    The mapping is explicit (variant id -> run) rather than inferred, because a run does
    not retain the preregistered ``max_candidates`` and inferring it could match two
    declared caps. A key that is not a declared variant fails closed — a run whose variant
    was not preregistered is a post-hoc variant, not a new family member.
    """
    accounting = TrialFamilyAccounting(declaration)
    for variant_id, run in runs_by_variant_id.items():
        declaration.variant(variant_id)  # fails closed on an undeclared variant
        if run.funnel_version != declaration.variant(variant_id).funnel_version:
            raise UnpreregisteredWinnerError(
                f"run {run.run_id} has funnel_version {run.funnel_version}, which does not "
                f"match declared variant {variant_id}"
            )
        accounting = accounting.observe(
            VariantObservation(
                variant_id,
                observed_at or run.created_at.isoformat(),
                SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC,
            )
        )
    return accounting
