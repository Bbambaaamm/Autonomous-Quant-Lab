"""PIT per-decision-time Stage A recomputation for the candidate funnel (STAT-BLOCKER A).

Historical funnel construction must never first pick "good tickers" over the whole
history and only then backtest Stage B on them. For every historical decision time the
Stage A candidate set is rebuilt *only* from data known at that time, and any
screening/ranking/normalisation that would be fitted or estimated from history must be
fitted inside the relevant train scope.

This module is the bounded integration layer for the #268 candidate funnel:

* it composes the existing canonical primitives — ``CandidateFunnel.run_stage_a``, which
  itself calls ``evaluate_screen`` from #164 — and never re-implements a market-data or
  screening pipeline;
* it produces one immutable Stage A candidate snapshot per decision time, with the
  funnel version, Stage A config hash, candidate rank, selection reason and completeness
  state carried into the downstream lineage (review BLOCKER C);
* it fails closed when the supplied expected-session calendar or a fitted parameter range
  escapes the decision time / train scope;
* it re-exports the conditional-baseline and threshold-sensitivity contracts from
  ``quantlab.funnel_evaluation`` under PIT semantics (STAT-BLOCKER C/D) — the
  whole-replay baseline guard plus the per-decision-time stability report that turns a
  fragile Top-K/threshold choice into #76 fragility evidence.

It is pure: no I/O, no workers, no clock reads, no database access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from quantlab.candidate_funnel import (
    CandidateFunnel,
    RejectionReason,
    SelectionReason,
    StageAConfig,
    StageAResult,
    StageBConfig,
)
from quantlab.domain import require_utc
from quantlab.funnel_completeness import (
    PartialModePolicy,
    PromotionGradeError,
    UniverseCompletion,
    UniverseCompletionState,
    assess_universe_completion,
    require_promotion_grade,
)
from quantlab.market_data import CorporateAction, Observation, XNYSCalendar
from quantlab.market_screening import identity

PIT_FUNNEL_VERSION = "pit-funnel-v1"
PIT_SEMANTICS_VERSION = "pit-v1"


class PITScopeError(RuntimeError):
    """Raised when a PIT input would leak information from after the decision time."""


class TrainScopeFittingError(RuntimeError):
    """Raised when a fitted/estimated parameter escapes its train scope (STAT-BLOCKER A)."""


class BaselineComparisonError(RuntimeError):
    """Raised when a baseline is not evaluated on the identical opportunity set.

    An unconditional whole-market base rate is not a fair benchmark after a strong
    Stage A selection (STAT-BLOCKER C): the baseline must see the same candidates at the
    same decision times, otherwise the comparison fails closed instead of reporting a
    lift computed against a different opportunity set.
    """


@lru_cache(maxsize=1)
def _calendar() -> XNYSCalendar:
    return XNYSCalendar()


def sessions_known_at(sessions: Sequence[date], decision_time: datetime) -> tuple[date, ...]:
    """Return only the sessions whose close is known at ``decision_time`` (PIT slice)."""
    cutoff = require_utc(decision_time)
    calendar = _calendar()
    return tuple(day for day in sessions if calendar.session_close(day) <= cutoff)


def observations_known_at(
    observations: Sequence[Observation], decision_time: datetime
) -> tuple[Observation, ...]:
    """Slice observations to those actually known at ``decision_time`` (PIT only).

    Both the provider-observation time (``observed_at``) and the bar timestamp must be
    at or before the decision time. The Stage A candidate set is built from this slice,
    so a later fold can never see a bar the earlier fold could not have seen.
    """
    cutoff = require_utc(decision_time)
    return tuple(o for o in observations if o.observed_at <= cutoff and o.timestamp <= cutoff)


def actions_known_at(
    actions: Sequence[CorporateAction], decision_time: datetime
) -> tuple[CorporateAction, ...]:
    """Slice corporate actions to those whose ``known_at`` is at or before the decision."""
    cutoff = require_utc(decision_time)
    return tuple(a for a in actions if a.known_at <= cutoff)


@dataclass(frozen=True)
class PITDecisionTime:
    """One historical decision time with its PIT-safe session slice and train scope.

    ``expected_sessions`` must contain only sessions already closed at
    ``decision_time``; a future session fails closed so the coverage denominator can
    never be inflated by later data. ``train_scope_*`` (when supplied) bounds the data
    range a fitted/estimated parameter may have used and may not extend past the
    decision time.
    """

    fold_index: int
    decision_time: datetime
    universe_snapshot_id: str
    expected_sessions: tuple[date, ...]
    train_scope_start: datetime | None = None
    train_scope_end: datetime | None = None

    def __post_init__(self) -> None:
        cutoff = require_utc(self.decision_time)
        object.__setattr__(self, "decision_time", cutoff)
        if self.fold_index < 0:
            raise PITScopeError("fold_index must be non-negative")
        if not self.universe_snapshot_id:
            raise PITScopeError("universe_snapshot_id must be non-empty")
        if not self.expected_sessions:
            raise PITScopeError("expected_sessions must not be empty")
        calendar = _calendar()
        for day in self.expected_sessions:
            if calendar.session_close(day) > cutoff:
                raise PITScopeError(
                    f"expected session {day.isoformat()} closes after the decision time "
                    f"{cutoff.isoformat()} — refusing to leak future sessions"
                )
        if (self.train_scope_start is None) != (self.train_scope_end is None):
            raise PITScopeError("train scope must define both start and end, or neither")
        if self.train_scope_start is not None and self.train_scope_end is not None:
            start = require_utc(self.train_scope_start)
            end = require_utc(self.train_scope_end)
            object.__setattr__(self, "train_scope_start", start)
            object.__setattr__(self, "train_scope_end", end)
            if start > end:
                raise PITScopeError("train_scope_start must not be after train_scope_end")
            if end > cutoff:
                raise PITScopeError(
                    "train scope extends past the decision time — a fitted parameter "
                    "would see the future"
                )

    @property
    def train_scope(self) -> tuple[datetime, datetime] | None:
        if self.train_scope_start is None or self.train_scope_end is None:
            return None
        return (self.train_scope_start, self.train_scope_end)


def pit_decision_time(
    fold_index: int,
    decision_time: datetime,
    universe_snapshot_id: str,
    sessions: Sequence[date],
    *,
    train_scope_start: datetime | None = None,
    train_scope_end: datetime | None = None,
) -> PITDecisionTime:
    """Build a PIT decision time, slicing ``sessions`` to those already known."""
    known = sessions_known_at(sessions, decision_time)
    if not known:
        raise PITScopeError("no session is known at the given decision time")
    return PITDecisionTime(
        fold_index=fold_index,
        decision_time=decision_time,
        universe_snapshot_id=universe_snapshot_id,
        expected_sessions=known,
        train_scope_start=train_scope_start,
        train_scope_end=train_scope_end,
    )


def assert_fit_inside_train_scope(
    train_scope: tuple[datetime, datetime] | None,
    fitted_start: datetime,
    fitted_end: datetime,
) -> None:
    """Fail closed when a fitted/estimated parameter used data outside its train scope.

    Screening/ranking/normalisation that is *fitted or estimated* from history must be
    fit inside the relevant train scope (STAT-BLOCKER A). A pass-through when no train
    scope is preregistered keeps the gate explicit rather than implicit.
    """
    start = require_utc(fitted_start)
    end = require_utc(fitted_end)
    if start > end:
        raise TrainScopeFittingError("fitted_start must not be after fitted_end")
    if train_scope is None:
        return
    scope_start, scope_end = train_scope
    if start < scope_start or end > scope_end:
        raise TrainScopeFittingError(
            "fitted parameter range "
            f"[{start.isoformat()}, {end.isoformat()}] escapes the train scope "
            f"[{scope_start.isoformat()}, {scope_end.isoformat()}]"
        )


def _is_not_evaluated(reason: RejectionReason | None) -> bool:
    return reason is not None and reason.value.startswith("NOT_EVALUATED")


@dataclass(frozen=True)
class PITDecisionSnapshot:
    """Immutable Stage A candidate snapshot for exactly one decision time."""

    fold_index: int
    decision_time: datetime
    universe_snapshot_id: str
    train_scope_start: datetime | None
    train_scope_end: datetime | None
    stage_a_results: tuple[StageAResult, ...]
    ordered_candidate_ids: tuple[str, ...]
    funnel_version: str
    stage_a_config_hash: str
    stage_b_config_hash: str
    ranking_key: str
    ranking_key_version: str
    eligible_count: int
    rejected_count: int
    not_evaluated_count: int
    content_hash: str
    universe_completion: UniverseCompletion | None = None

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Stage A candidate set in the preregistered deterministic order."""
        return self.ordered_candidate_ids

    @property
    def universe_completion_state(self) -> str:
        """Explicit full-universe completion state at this decision time (BLOCKER A)."""
        if self.universe_completion is None:
            return UniverseCompletionState.UNKNOWN.value
        return self.universe_completion.state.value

    @property
    def is_promotion_grade(self) -> bool:
        """Promotion-grade requires an explicitly COMPLETE PIT universe at this decision."""
        return self.universe_completion is not None and self.universe_completion.is_promotion_grade

    def to_summary(self) -> dict[str, Any]:
        """Funnel counts and rejection categories for one decision time.

        Reads only the immutable snapshot — no expensive full-market computation.
        """
        return {
            "fold_index": self.fold_index,
            "decision_time": self.decision_time.isoformat(),
            "universe_snapshot_id": self.universe_snapshot_id,
            "funnel_version": self.funnel_version,
            "ranking_key": self.ranking_key,
            "ranking_key_version": self.ranking_key_version,
            "universe_completion_state": self.universe_completion_state,
            "universe_completion": (
                self.universe_completion.to_summary()
                if self.universe_completion is not None
                else None
            ),
            "is_promotion_grade": self.is_promotion_grade,
            "funnel_counts": {
                "universe_size": len(self.stage_a_results),
                "stage_a_accepted": self.eligible_count,
                "stage_a_rejected": self.rejected_count,
                "not_evaluated": self.not_evaluated_count,
                "candidates": len(self.ordered_candidate_ids),
            },
            "rejection_categories": self.rejection_counts(),
        }

    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.stage_a_results:
            if result.passed or result.rejection_reason is None:
                continue
            key = result.rejection_reason.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def candidate_lineage(self) -> list[dict[str, Any]]:
        """Per-candidate downstream lineage for this decision time (BLOCKER C).

        A forecast/experiment ledger must embed this structure so Stage-B/forecast
        performance is always attributable to a specific funnel version, Stage A
        ranking and completeness state at the moment of decision.
        """
        by_id = {r.instrument_id: r for r in self.stage_a_results}
        lineage: list[dict[str, Any]] = []
        for rank, instrument_id in enumerate(self.ordered_candidate_ids, 1):
            result = by_id[instrument_id]
            lineage.append(
                {
                    "fold_index": self.fold_index,
                    "decision_time": self.decision_time.isoformat(),
                    "instrument_id": instrument_id,
                    "symbol": result.symbol,
                    "candidate_rank": rank,
                    "selection_reason": (
                        result.selection_reason.value
                        if result.selection_reason is not None
                        else SelectionReason.STAGE_A_SELECTED.value
                    ),
                    "completeness": result.screening_evidence.get("coverage"),
                    "funnel_version": self.funnel_version,
                    "stage_a_config_hash": self.stage_a_config_hash,
                    "stage_b_config_hash": self.stage_b_config_hash,
                    "ranking_key": self.ranking_key,
                    "ranking_key_version": self.ranking_key_version,
                    "universe_snapshot_id": self.universe_snapshot_id,
                    "universe_completion_state": self.universe_completion_state,
                }
            )
        return lineage


@dataclass(frozen=True)
class PITFunnelReplay:
    """Immutable sequence of per-decision-time Stage A snapshots."""

    decision_snapshots: tuple[PITDecisionSnapshot, ...]
    pit_version: str
    funnel_version: str
    stage_a_config_hash: str
    stage_b_config_hash: str
    ranking_key: str
    ranking_key_version: str
    content_hash: str

    @property
    def decision_times(self) -> tuple[datetime, ...]:
        return tuple(snapshot.decision_time for snapshot in self.decision_snapshots)

    def candidate_sets(self) -> tuple[tuple[str, ...], ...]:
        """The per-decision-time candidate sets in fold order."""
        return tuple(snapshot.candidate_ids for snapshot in self.decision_snapshots)

    @property
    def universe_completion_states(self) -> tuple[str, ...]:
        """Explicit full-universe completion state per decision time (review BLOCKER A)."""
        return tuple(snapshot.universe_completion_state for snapshot in self.decision_snapshots)

    @property
    def is_promotion_grade(self) -> bool:
        """Promotion-grade only when EVERY decision time saw an explicitly COMPLETE universe."""
        return bool(self.decision_snapshots) and all(
            snapshot.is_promotion_grade for snapshot in self.decision_snapshots
        )

    def to_summary(self) -> dict[str, Any]:
        """PIT funnel counts per decision time for dashboard/report."""
        return {
            "pit_version": self.pit_version,
            "funnel_version": self.funnel_version,
            "ranking_key": self.ranking_key,
            "ranking_key_version": self.ranking_key_version,
            "decision_count": len(self.decision_snapshots),
            "total_candidates": sum(len(s.candidate_ids) for s in self.decision_snapshots),
            "universe_completion_states": list(self.universe_completion_states),
            "is_promotion_grade": self.is_promotion_grade,
            "decision_snapshots": [s.to_summary() for s in self.decision_snapshots],
        }

    def candidate_lineage(self) -> list[dict[str, Any]]:
        lineage: list[dict[str, Any]] = []
        for snapshot in self.decision_snapshots:
            lineage.extend(snapshot.candidate_lineage())
        return lineage

    def trial_family_key(self) -> tuple[str, str, str, int]:
        """Identity of the PIT selection operator for multiple-testing accounting.

        Every distinct PIT funnel variant (PIT semantics version, ranking key,
        ranking-key version, decision-time schedule) is a separate member of the
        multiple-testing family (STAT-BLOCKER B); downstream validation must stratify
        on this key and never pool materially different funnel versions.
        """
        return (
            self.pit_version,
            self.ranking_key,
            self.ranking_key_version,
            len(self.decision_snapshots),
        )

    def opportunity_key(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The exact (decision time, candidate set) pairs this replay selected.

        A conditional baseline must be evaluated against this identical opportunity
        set (STAT-BLOCKER C).
        """
        return tuple(
            (snapshot.decision_time.isoformat(), snapshot.candidate_ids)
            for snapshot in self.decision_snapshots
        )


def assert_same_opportunity_set(
    stage_b: PITFunnelReplay,
    baseline: PITFunnelReplay,
) -> None:
    """Fail closed unless the baseline saw the identical opportunity set.

    The baseline must be evaluated on the same candidate set at the same decision
    timestamps as Stage B. A different number of decision times, a different schedule,
    or a different candidate set at any decision time is a material mismatch: the
    comparison would otherwise benchmark Stage B against a different opportunity set
    (STAT-BLOCKER C).
    """
    if stage_b.pit_version != baseline.pit_version:
        raise BaselineComparisonError(
            "baseline uses different PIT semantics "
            f"({baseline.pit_version} != {stage_b.pit_version})"
        )
    if stage_b.decision_times != baseline.decision_times:
        raise BaselineComparisonError(
            "baseline decision-time schedule differs from Stage B; a conditional "
            "baseline must use the same opportunity timestamps"
        )
    for index, (stage_b_set, baseline_set) in enumerate(
        zip(stage_b.candidate_sets(), baseline.candidate_sets(), strict=True)
    ):
        if stage_b_set != baseline_set:
            raise BaselineComparisonError(
                f"baseline candidate set at decision index {index} differs from Stage B"
            )


def require_pit_promotion_grade(
    replay: PITFunnelReplay,
    policy: PartialModePolicy | None = None,
) -> None:
    """Fail closed unless every decision time is promotion-grade (review BLOCKER A).

    A promotion-grade PIT run requires an explicitly COMPLETE Stage A universe at every
    decision time, unless partial mode was preregistered for the use case. A fold whose
    universe is UNKNOWN (no declared membership) never passes.
    """
    for snapshot in replay.decision_snapshots:
        if snapshot.universe_completion is None:
            raise PromotionGradeError(
                f"PIT decision {snapshot.decision_time.isoformat()} declares no universe "
                "membership, so completeness is UNKNOWN; an undeclared universe cannot be "
                "promotion-grade"
            )
        require_promotion_grade(snapshot.universe_completion, policy)


@dataclass(frozen=True)
class PITStabilityPoint:
    """Per-decision-time candidate-set stability between two PIT funnel variants."""

    decision_time: datetime
    baseline_candidates: tuple[str, ...]
    perturbed_candidates: tuple[str, ...]
    jaccard_index: float
    changed: bool


@dataclass(frozen=True)
class PITThresholdSensitivity:
    """Threshold/K sensitivity of the PIT selection (STAT-BLOCKER D).

    If the edge disappears under a small change of Top-K / threshold, that is
    fragility evidence for #76 — so the per-decision-time stability must be part of
    the robustness report rather than an aggregate-only number.
    """

    parameter: str
    baseline_value: str
    perturbed_value: str
    points: tuple[PITStabilityPoint, ...]
    mean_jaccard: float
    stable_decisions: int
    changed_decisions: int

    @property
    def changed(self) -> bool:
        return self.changed_decisions > 0


def compute_pit_threshold_sensitivity(
    baseline: PITFunnelReplay,
    perturbed: PITFunnelReplay,
    *,
    parameter: str = "max_candidates",
    baseline_value: str | None = None,
    perturbed_value: str | None = None,
) -> PITThresholdSensitivity:
    """Compare PIT candidate sets per decision time under a threshold/K perturbation.

    Both replays must share the decision-time schedule; only the selection parameters
    (threshold / K) may differ, so the sensitivity is attributable to the parameter
    rather than to a different opportunity schedule.
    """
    if baseline.decision_times != perturbed.decision_times:
        raise BaselineComparisonError(
            "PIT threshold sensitivity requires the same decision-time schedule in both variants"
        )

    points: list[PITStabilityPoint] = []
    for decision_time, baseline_set, perturbed_set in zip(
        baseline.decision_times,
        baseline.candidate_sets(),
        perturbed.candidate_sets(),
        strict=True,
    ):
        union = set(baseline_set) | set(perturbed_set)
        intersection = set(baseline_set) & set(perturbed_set)
        jaccard = len(intersection) / len(union) if union else 1.0
        points.append(
            PITStabilityPoint(
                decision_time=decision_time,
                baseline_candidates=baseline_set,
                perturbed_candidates=perturbed_set,
                jaccard_index=jaccard,
                changed=baseline_set != perturbed_set,
            )
        )

    mean_jaccard = sum(point.jaccard_index for point in points) / len(points) if points else 1.0
    changed_decisions = sum(1 for point in points if point.changed)
    return PITThresholdSensitivity(
        parameter=parameter,
        baseline_value=(
            baseline_value if baseline_value is not None else str(len(baseline.candidate_sets()))
        ),
        perturbed_value=(
            perturbed_value if perturbed_value is not None else str(len(perturbed.candidate_sets()))
        ),
        points=tuple(points),
        mean_jaccard=mean_jaccard,
        stable_decisions=len(points) - changed_decisions,
        changed_decisions=changed_decisions,
    )


def _pit_funnel_version(
    stage_a_config: StageAConfig,
    stage_b_config: StageBConfig,
) -> str:
    return identity(
        {
            "module_version": PIT_FUNNEL_VERSION,
            "pit_version": PIT_SEMANTICS_VERSION,
            "ranking_key": stage_b_config.ranking_key.value,
            "ranking_key_version": stage_b_config.ranking_key_version,
            "stage_a_config": stage_a_config.config_hash,
            "stage_b_config": stage_b_config.config_hash,
        }
    )


def recompute_stage_a_at(
    fold: PITDecisionTime,
    instruments: Sequence[dict[str, Any]],
    observations: Mapping[str, Sequence[Observation]],
    actions: Mapping[str, Sequence[CorporateAction]],
    *,
    stage_a_config: StageAConfig | None = None,
    stage_b_config: StageBConfig | None = None,
    readiness_ids: Mapping[str, str | None] | None = None,
    inventory_ids: Mapping[str, str | None] | None = None,
    inventory_received_at: Mapping[str, datetime | None] | None = None,
    expected_universe_members: Sequence[str] | None = None,
) -> PITDecisionSnapshot:
    """Recompute the Stage A candidate set at one decision time, PIT only.

    The candidate set is derived exclusively from the caller's PIT inputs plus the
    fold's already-known session slice. When the caller passes the *full-history*
    observation/action series, only the bars and actions known at the decision time are
    used (``observations_known_at`` / ``actions_known_at``), so the snapshot can never
    contain data from after the decision time. ``CandidateFunnel.run_stage_a`` applies
    the canonical ``evaluate_screen`` primitives; the ordering key and its version come
    from the preregistered Stage B config, so the cap/rank is fixed before any Stage B
    work (review BLOCKER B) and the resulting identity is deterministic.
    """
    config_a = stage_a_config or StageAConfig()
    config_b = stage_b_config or StageBConfig()
    funnel = CandidateFunnel(stage_a_config=config_a, stage_b_config=config_b)

    pit_observations: dict[str, Sequence[Observation]] = {
        instrument_id: observations_known_at(series, fold.decision_time)
        for instrument_id, series in observations.items()
    }
    pit_actions: dict[str, Sequence[CorporateAction]] = {
        instrument_id: actions_known_at(series, fold.decision_time)
        for instrument_id, series in actions.items()
    }

    stage_a_results = funnel.run_stage_a(
        fold.universe_snapshot_id,
        instruments,
        pit_observations,
        pit_actions,
        fold.expected_sessions,
        fold.decision_time,
        dict(readiness_ids) if readiness_ids is not None else {},
        dict(inventory_ids) if inventory_ids is not None else {},
        dict(inventory_received_at) if inventory_received_at is not None else {},
    )

    ordered = config_b.order_candidates([r for r in stage_a_results if r.passed])
    ordered_candidate_ids = tuple(r.instrument_id for r in ordered)

    eligible = sum(1 for r in stage_a_results if r.passed)
    not_evaluated = sum(1 for r in stage_a_results if _is_not_evaluated(r.rejection_reason))
    rejected = sum(
        1 for r in stage_a_results if not r.passed and not _is_not_evaluated(r.rejection_reason)
    )

    funnel_version = _pit_funnel_version(config_a, config_b)

    # Explicit full-universe completion state at this decision time (BLOCKER A). The
    # expected membership must already be PIT-sliced by the caller; an undeclared
    # universe yields UNKNOWN and is never promotion-grade.
    universe_completion: UniverseCompletion | None = None
    if expected_universe_members is not None:
        universe_completion = assess_universe_completion(
            fold.universe_snapshot_id,
            expected_universe_members,
            [r.instrument_id for r in stage_a_results],
        )

    content = {
        "fold_index": fold.fold_index,
        "decision_time": fold.decision_time.isoformat(),
        "universe_snapshot_id": fold.universe_snapshot_id,
        "expected_sessions": [d.isoformat() for d in fold.expected_sessions],
        "train_scope_start": fold.train_scope_start.isoformat() if fold.train_scope_start else None,
        "train_scope_end": fold.train_scope_end.isoformat() if fold.train_scope_end else None,
        "stage_a_config_hash": config_a.config_hash,
        "stage_b_config_hash": config_b.config_hash,
        "funnel_version": funnel_version,
        "stage_a_results": [
            {
                "instrument_id": r.instrument_id,
                "symbol": r.symbol,
                "passed": r.passed,
                "rejection_reason": r.rejection_reason.value if r.rejection_reason else None,
                "rejection_details": r.rejection_details,
            }
            for r in stage_a_results
        ],
        "ordered_candidate_ids": list(ordered_candidate_ids),
        "universe_completion": (
            universe_completion.content_hash if universe_completion is not None else None
        ),
    }

    return PITDecisionSnapshot(
        fold_index=fold.fold_index,
        decision_time=fold.decision_time,
        universe_snapshot_id=fold.universe_snapshot_id,
        train_scope_start=fold.train_scope_start,
        train_scope_end=fold.train_scope_end,
        stage_a_results=stage_a_results,
        ordered_candidate_ids=ordered_candidate_ids,
        funnel_version=funnel_version,
        stage_a_config_hash=config_a.config_hash,
        stage_b_config_hash=config_b.config_hash,
        ranking_key=config_b.ranking_key.value,
        ranking_key_version=config_b.ranking_key_version,
        eligible_count=eligible,
        rejected_count=rejected,
        not_evaluated_count=not_evaluated,
        content_hash=identity(content),
        universe_completion=universe_completion,
    )


def recompute_pit_replay(
    folds: Sequence[PITDecisionTime],
    instruments: Sequence[dict[str, Any]],
    observations: Mapping[str, Sequence[Observation]],
    actions: Mapping[str, Sequence[CorporateAction]],
    *,
    stage_a_config: StageAConfig | None = None,
    stage_b_config: StageBConfig | None = None,
    readiness_ids: Mapping[str, str | None] | None = None,
    inventory_ids: Mapping[str, str | None] | None = None,
    inventory_received_at: Mapping[str, datetime | None] | None = None,
    expected_universe_members_by_fold: Mapping[int, Sequence[str]] | None = None,
) -> PITFunnelReplay:
    """Rebuild the Stage A candidate set independently for every decision time.

    Nothing is carried over between folds: each snapshot is computed from the same
    immutable PIT inputs and its own session slice, so a later fold can never inherit an
    earlier fold's selection (STAT-BLOCKER A).

    ``expected_universe_members_by_fold`` maps ``fold_index`` to the canonical universe
    membership known at that fold's decision time. A fold without an entry is UNKNOWN and
    is never promotion-grade (review BLOCKER A).
    """
    if not folds:
        raise PITScopeError("at least one PIT decision time is required")

    config_a = stage_a_config or StageAConfig()
    config_b = stage_b_config or StageBConfig()
    expected_by_fold = (
        expected_universe_members_by_fold if expected_universe_members_by_fold is not None else {}
    )

    snapshots = tuple(
        recompute_stage_a_at(
            fold,
            instruments,
            observations,
            actions,
            stage_a_config=config_a,
            stage_b_config=config_b,
            readiness_ids=readiness_ids,
            inventory_ids=inventory_ids,
            inventory_received_at=inventory_received_at,
            expected_universe_members=expected_by_fold.get(fold.fold_index),
        )
        for fold in folds
    )

    funnel_version = _pit_funnel_version(config_a, config_b)
    content_hash = identity(
        {
            "pit_version": PIT_SEMANTICS_VERSION,
            "funnel_version": funnel_version,
            "stage_a_config_hash": config_a.config_hash,
            "stage_b_config_hash": config_b.config_hash,
            "snapshot_hashes": [s.content_hash for s in snapshots],
            "universe_completion_hashes": [
                s.universe_completion.content_hash if s.universe_completion is not None else None
                for s in snapshots
            ],
        }
    )

    return PITFunnelReplay(
        decision_snapshots=snapshots,
        pit_version=PIT_SEMANTICS_VERSION,
        funnel_version=funnel_version,
        stage_a_config_hash=config_a.config_hash,
        stage_b_config_hash=config_b.config_hash,
        ranking_key=config_b.ranking_key.value,
        ranking_key_version=config_b.ranking_key_version,
        content_hash=content_hash,
    )


def deterministic_pit_replay_check(
    replay1: PITFunnelReplay,
    replay2: PITFunnelReplay,
) -> bool:
    """Two PIT replays over the same snapshots/config produce the same candidate sets.

    Equality covers the per-decision-time ordering and lineage, not only the aggregate
    set (review BLOCKER B/C).
    """
    return (
        replay1.content_hash == replay2.content_hash
        and replay1.pit_version == replay2.pit_version
        and replay1.funnel_version == replay2.funnel_version
        and replay1.stage_a_config_hash == replay2.stage_a_config_hash
        and replay1.stage_b_config_hash == replay2.stage_b_config_hash
        and replay1.ranking_key == replay2.ranking_key
        and replay1.ranking_key_version == replay2.ranking_key_version
        and replay1.candidate_sets() == replay2.candidate_sets()
        and replay1.decision_times == replay2.decision_times
        and replay1.universe_completion_states == replay2.universe_completion_states
        and [s.content_hash for s in replay1.decision_snapshots]
        == [s.content_hash for s in replay2.decision_snapshots]
    )
