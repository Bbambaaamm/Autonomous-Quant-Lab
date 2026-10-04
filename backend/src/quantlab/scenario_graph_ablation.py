"""Scenario graph incremental-value ablation harness — shadow research only.

Issue #271 Phase 3 (shadow forecast contribution) + Phase 4 (incremental-value
experiment). This module answers exactly one question: does a scenario-graph feature
add *measurable* out-of-sample forecast value over a baseline, on the same
opportunities, with the same target and cost assumptions?

It is a pure, deterministic evaluation harness:

- No I/O, no network, no LLM calls, no execution authority.
- It never emits an order, never touches a broker, never mutates risk limits.
- Its only output is a research verdict: ``PROMOTE`` / ``REJECT`` / ``INSUFFICIENT``.

The harness deliberately refuses to be fooled by the failure modes recorded in the
independent statistical review of issue #271:

- STAT-BLOCKER A — an uncalibrated scenario score is never treated as a probability;
  it enters the ablation only as a bounded modifier, and a scenario-derived forecast
  is only admissible with an explicit #266 forecast reference and a #269 calibration
  report reference.
- STAT-BLOCKER B — the effective sample size comes from independent real-world
  forecast opportunities (clustered), never from the number of internal simulation
  replicates.
- STAT-BLOCKER C — every materially distinct variant (prompt / model / graph policy /
  agent population / aggregation / sampling) is recorded in the trial family; failed
  variants are retained, not discarded.
- STAT-BLOCKER D — if the prompt/graph/config identity changes after holdout results
  were observed, the holdout is marked contaminated and no incremental-value claim
  may rest on it.
- STAT-BLOCKER E — the ablation is paired on identical opportunities, with the same
  target spec, funnel version and cost model, and reports a clustered uncertainty
  interval for the paired delta, not a comparison of two aggregate means.
- STAT-BLOCKER F — scenario discovery (was the relevant outcome representable at all)
  is evaluated separately from weighting quality.

Scoring uses the same Brier / log-loss definitions as issue #269's calibration engine
(``quantlab.calibration``). Once #269 is merged into ``main`` this module must be
re-pointed at ``compute_brier_score`` / ``compute_log_loss`` rather than carrying its
own copies; the duplicated formulas exist only because #269 is not yet on ``main``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "MAX_BOOTSTRAP_REPLICATES",
    "MIN_CLUSTERS_FOR_PROMOTION",
    "MAX_ABLATION_OPPORTUNITIES",
    "EPSILON",
    "AblationError",
    "InsufficientEvidenceError",
    "AblationArm",
    "AblationVerdict",
    "ScenarioVariant",
    "TrialFamilyEntry",
    "TrialFamilyLedger",
    "ForecastOpportunity",
    "PairedDelta",
    "DiscoveryReport",
    "AblationCostReport",
    "AblationResult",
    "ScenarioAblationHarness",
    "build_scenario_modifier",
    "result_to_dict",
]

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

DEFAULT_BOOTSTRAP_REPLICATES = 2000
MAX_BOOTSTRAP_REPLICATES = 20000
MIN_CLUSTERS_FOR_PROMOTION = 30
MAX_ABLATION_OPPORTUNITIES = 100000
EPSILON = Decimal("1e-12")

ONE = Decimal("1")
ZERO = Decimal("0")


class AblationError(RuntimeError):
    """Base exception for the ablation harness."""


class InsufficientEvidenceError(AblationError):
    """Raised when a promotion verdict is requested without admissible evidence."""


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


class AblationArm(StrEnum):
    BASELINE = "BASELINE"
    BASELINE_PLUS_SCENARIO = "BASELINE_PLUS_SCENARIO"


class AblationVerdict(StrEnum):
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class ScenarioVariant:
    """A materially distinct scenario-engine variant (STAT-BLOCKER C).

    Any change to one of these components makes the variant a new member of the
    experiment family and must be declared even when it loses.
    """

    variant_id: str
    prompt_hash: str
    model_id: str
    graph_policy_hash: str
    agent_population_hash: str
    aggregation_rule: str
    sampling_policy: str


@dataclass(frozen=True)
class TrialFamilyEntry:
    """One declared variant with its observed outcome (STAT-BLOCKER C/D)."""

    variant: ScenarioVariant
    evaluated: bool
    paired_delta_brier: Decimal | None
    promoted: bool
    holdout_contaminated: bool
    note: str = ""


@dataclass
class TrialFamilyLedger:
    """Complete trial-family accounting; losing variants are retained."""

    family_id: str
    target_spec_hash: str
    funnel_version: str
    cost_model_id: str
    entries: list[TrialFamilyEntry] = field(default_factory=list)

    def declare(self, entry: TrialFamilyEntry) -> None:
        """Register a variant outcome. Re-declaring the same variant is rejected."""
        if any(e.variant.variant_id == entry.variant.variant_id for e in self.entries):
            raise AblationError(f"variant {entry.variant.variant_id} already declared")
        self.entries.append(entry)

    @property
    def variant_count(self) -> int:
        return len(self.entries)

    @property
    def evaluated_count(self) -> int:
        return sum(1 for e in self.entries if e.evaluated)

    def contamination_flag(self) -> bool:
        return any(e.holdout_contaminated for e in self.entries)


@dataclass(frozen=True)
class ForecastOpportunity:
    """One paired, real-world forecast opportunity (STAT-BLOCKER E).

    ``cluster_id`` groups time-dependent / correlated opportunities (e.g. the same
    trading day or the same macro regime episode). The clustered uncertainty uses it;
    the effective sample size is the number of distinct clusters, NOT the number of
    rows and NOT the number of simulation replicates (STAT-BLOCKER B).

    ``scenario_runtime_ms`` / ``scenario_token_count`` / ``scenario_model_calls`` carry
    the *measured* cost of the scenario run that produced ``scenario_score``; they are
    summed by :class:`AblationCostReport` so latency/cost is reported, not invented.
    """

    opportunity_id: str
    decision_time: datetime
    cluster_id: str
    event_class: str
    regime: str
    baseline_probability: Decimal
    scenario_score: Decimal | None
    outcome: int
    forecast_ref: str | None = None
    calibration_report_ref: str | None = None
    scenario_run_id: str | None = None
    scenario_runtime_ms: int = 0
    scenario_token_count: int = 0
    scenario_model_calls: int = 0

    def __post_init__(self) -> None:
        if self.decision_time.tzinfo is None:
            raise AblationError("decision_time must be timezone-aware")
        if self.outcome not in (0, 1):
            raise AblationError("outcome must be 0 or 1")
        _require_probability(self.baseline_probability, "baseline_probability")
        if self.scenario_score is not None:
            _require_probability(self.scenario_score, "scenario_score")
        if self.scenario_runtime_ms < 0:
            raise AblationError("scenario_runtime_ms must be non-negative")
        if self.scenario_token_count < 0:
            raise AblationError("scenario_token_count must be non-negative")
        if self.scenario_model_calls < 0:
            raise AblationError("scenario_model_calls must be non-negative")


def _require_probability(value: Decimal, name: str) -> None:
    if not (ZERO <= value <= ONE):
        raise AblationError(f"{name} must be in [0, 1], got {value}")


def build_scenario_modifier(
    baseline_probability: Decimal,
    scenario_score: Decimal,
    max_shift: Decimal,
) -> Decimal:
    """Map an uncalibrated scenario score to a bounded baseline modifier.

    The scenario score is NOT a probability (STAT-BLOCKER A). It is applied as a
    bounded shift toward or away from the score, capped by ``max_shift`` so an
    uncalibrated signal cannot dominate the forecast.
    """
    _require_probability(baseline_probability, "baseline_probability")
    _require_probability(scenario_score, "scenario_score")
    if not (ZERO <= max_shift <= Decimal("0.5")):
        raise AblationError("max_shift must be in [0, 0.5]")
    delta = scenario_score - baseline_probability
    bounded = max(-max_shift, min(max_shift, delta))
    shifted = baseline_probability + bounded
    return max(EPSILON, min(ONE - EPSILON, shifted))


@dataclass(frozen=True)
class PairedDelta:
    """Paired delta with clustered uncertainty (STAT-BLOCKER E).

    ``brier_delta`` and ``logloss_delta`` are ``baseline - scenario``: a POSITIVE value
    means the scenario arm has lower loss (an improvement).
    """

    n_opportunities: int
    n_clusters: int
    brier_baseline: Decimal
    brier_scenario: Decimal
    brier_delta: Decimal
    logloss_baseline: Decimal
    logloss_scenario: Decimal
    logloss_delta: Decimal
    ci_low: Decimal
    ci_high: Decimal
    ci_level: Decimal
    bootstrap_replicates: int
    significant_improvement: bool
    direction_consistent: bool


@dataclass(frozen=True)
class DiscoveryReport:
    """Discovery evaluated separately from weighting (STAT-BLOCKER F)."""

    event_classes_evaluated: tuple[str, ...]
    event_classes_representable: tuple[str, ...]
    discovery_coverage: Decimal
    discovery_failed_classes: tuple[str, ...]


@dataclass(frozen=True)
class AblationCostReport:
    """Measured latency and compute/model cost (issue #271 acceptance criteria)."""

    measured: bool
    total_runtime_ms: int
    mean_runtime_ms: Decimal
    total_tokens: int
    total_model_calls: int
    cost_per_opportunity_ms: Decimal


@dataclass(frozen=True)
class AblationResult:
    """Final, typed ablation verdict."""

    ablation_id: str
    verdict: AblationVerdict
    paired: PairedDelta
    discovery: DiscoveryReport
    cost: AblationCostReport
    coverage_rate: Decimal
    temporal_knowledge_controlled: bool
    fully_reproducible: bool
    holdout_contaminated: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def may_promote(self) -> bool:
        return self.verdict is AblationVerdict.PROMOTE


def _brier(probability: Decimal, outcome: int) -> Decimal:
    target = ONE if outcome == 1 else ZERO
    return (probability - target) ** 2


def _log_loss(probability: Decimal, outcome: int) -> Decimal:
    p = max(EPSILON, min(ONE - EPSILON, probability))
    return -(Decimal(outcome) * p.ln() + Decimal(1 - outcome) * (ONE - p).ln())


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


class ScenarioAblationHarness:
    """Deterministic paired ablation: baseline vs baseline + scenario graph."""

    def __init__(
        self,
        *,
        family: TrialFamilyLedger,
        max_shift: Decimal = Decimal("0.1"),
        bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
        ci_level: Decimal = Decimal("0.95"),
        seed: int = 20261004,
    ) -> None:
        if bootstrap_replicates > MAX_BOOTSTRAP_REPLICATES:
            raise AblationError(
                f"bootstrap_replicates {bootstrap_replicates} exceeds cap "
                f"{MAX_BOOTSTRAP_REPLICATES}"
            )
        if bootstrap_replicates <= 0:
            raise AblationError("bootstrap_replicates must be positive")
        if not (Decimal("0.5") < ci_level < ONE):
            raise AblationError("ci_level must be in (0.5, 1)")
        self.family = family
        self.max_shift = max_shift
        self.bootstrap_replicates = bootstrap_replicates
        self.ci_level = ci_level
        self.seed = seed

    # -- public entry point -------------------------------------------------

    def evaluate(
        self,
        opportunities: Sequence[ForecastOpportunity],
        *,
        temporal_knowledge_controlled: bool,
        fully_reproducible: bool,
        simulation_replicates: int = 1,
    ) -> AblationResult:
        """Run the paired ablation and return a typed verdict.

        Args:
            opportunities: Paired real-world forecast opportunities. An opportunity
                without a ``scenario_score`` is an abstention: it stays in the
                denominator but contributes no scenario-derived evidence.
            temporal_knowledge_controlled: Whether every historical run in the set has
                a proven model knowledge cutoff at or before its decision time
                (issue #271 BLOCKER A).
            fully_reproducible: Whether every run has a known model revision and seed
                (issue #271 BLOCKER D).
            simulation_replicates: Internal simulation repetitions used to produce the
                scenario scores. Recorded for accounting only; it never enters the
                effective sample size (STAT-BLOCKER B).
        """
        if len(opportunities) > MAX_ABLATION_OPPORTUNITIES:
            raise AblationError(
                f"opportunity count {len(opportunities)} exceeds cap {MAX_ABLATION_OPPORTUNITIES}"
            )
        if not opportunities:
            raise InsufficientEvidenceError("no opportunities supplied")
        if simulation_replicates <= 0:
            raise AblationError("simulation_replicates must be positive")

        scored = [o for o in opportunities if o.scenario_score is not None]
        coverage_rate = Decimal(len(scored)) / Decimal(len(opportunities))
        if not scored:
            raise InsufficientEvidenceError("no scenario-scored opportunities")

        paired = self._paired_delta(scored)
        discovery = self._discovery_report(opportunities)
        cost = self._cost_report(opportunities)

        blockers: list[str] = []
        warnings: list[str] = []

        # STAT-BLOCKER A: a scenario-derived forecast is only admissible with #266
        # lineage and a #269 calibration reference.
        unlined = [o for o in scored if not o.forecast_ref or not o.calibration_report_ref]
        if unlined:
            blockers.append(
                f"{len(unlined)} scenario-scored opportunities lack #266 forecast_ref "
                f"and/or #269 calibration_report_ref lineage"
            )

        # STAT-BLOCKER D: a burned holdout may not carry an incremental-value claim.
        if self.family.contamination_flag():
            blockers.append("trial family holdout is contaminated (post-insight change)")

        # BLOCKER A: an uncontrolled model knowledge cutoff is never promotion-grade.
        if not temporal_knowledge_controlled:
            blockers.append("model temporal knowledge is UNCONTROLLED for historical runs")

        # BLOCKER D: no exact model revision / seed ⇒ not fully reproducible.
        if not fully_reproducible:
            blockers.append("run fingerprint is not fully reproducible (missing revision/seed)")

        # STAT-BLOCKER B: effective sample size is the number of real-world clusters.
        if paired.n_clusters < MIN_CLUSTERS_FOR_PROMOTION:
            blockers.append(
                f"only {paired.n_clusters} independent real-world clusters "
                f"(< {MIN_CLUSTERS_FOR_PROMOTION})"
            )

        # STAT-BLOCKER F: a good calibration over an incomplete scenario set must not
        # mask a discovery failure.
        if discovery.discovery_failed_classes:
            blockers.append(
                "scenario discovery failed for event classes: "
                f"{list(discovery.discovery_failed_classes)}"
            )

        if not paired.significant_improvement:
            warnings.append("paired Brier improvement is not significant at the configured CI")

        if not paired.direction_consistent:
            warnings.append("paired delta sign is inconsistent across event classes/regimes")

        if coverage_rate < ONE:
            warnings.append(
                f"scenario coverage {coverage_rate} < 1.0 (abstentions kept in denominator)"
            )

        if not cost.measured:
            warnings.append("scenario runtime/token cost was not measured for any opportunity")

        if blockers:
            verdict = AblationVerdict.INSUFFICIENT
        elif not paired.significant_improvement:
            verdict = AblationVerdict.REJECT
        else:
            verdict = AblationVerdict.PROMOTE

        ablation_id = _hash_payload(
            {
                "family_id": self.family.family_id,
                "target_spec_hash": self.family.target_spec_hash,
                "n": len(opportunities),
                "n_clusters": paired.n_clusters,
                "brier_delta": str(paired.brier_delta),
                "logloss_delta": str(paired.logloss_delta),
                "ci": [str(paired.ci_low), str(paired.ci_high)],
                "verdict": verdict.value,
            }
        )

        return AblationResult(
            ablation_id=ablation_id,
            verdict=verdict,
            paired=paired,
            discovery=discovery,
            cost=cost,
            coverage_rate=coverage_rate,
            temporal_knowledge_controlled=temporal_knowledge_controlled,
            fully_reproducible=fully_reproducible,
            holdout_contaminated=self.family.contamination_flag(),
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    # -- paired statistics --------------------------------------------------

    def _paired_delta(self, scored: Sequence[ForecastOpportunity]) -> PairedDelta:
        n = Decimal(len(scored))
        brier_b = sum(_brier(o.baseline_probability, o.outcome) for o in scored) / n
        logloss_b = sum(_log_loss(o.baseline_probability, o.outcome) for o in scored) / n

        scenario_probs = [
            build_scenario_modifier(
                o.baseline_probability, o.scenario_score or ZERO, self.max_shift
            )
            for o in scored
        ]
        brier_s = sum(_brier(p, o.outcome) for p, o in zip(scenario_probs, scored, strict=True)) / n
        logloss_s = (
            sum(_log_loss(p, o.outcome) for p, o in zip(scenario_probs, scored, strict=True)) / n
        )

        # Clustered block bootstrap over cluster ids (deterministic, seeded).
        per_cluster: dict[str, list[Decimal]] = {}
        for o, p in zip(scored, scenario_probs, strict=True):
            per_cluster.setdefault(o.cluster_id, []).append(
                _brier(o.baseline_probability, o.outcome) - _brier(p, o.outcome)
            )
        cluster_ids = sorted(per_cluster)
        n_clusters = len(cluster_ids)

        # Deterministický seedovaný bootstrap, nikoli security primitive (#271 STAT-BLOCKER E).
        rng = random.Random(self.seed)  # noqa: S311
        deltas: list[Decimal] = []
        for _ in range(self.bootstrap_replicates):
            picked = [cluster_ids[rng.randrange(n_clusters)] for _ in range(n_clusters)]
            total = sum(sum(per_cluster[c]) for c in picked)
            count = sum(len(per_cluster[c]) for c in picked)
            deltas.append(total / Decimal(count))
        deltas.sort()

        alpha = (ONE - self.ci_level) / Decimal(2)
        low_idx = int(alpha * Decimal(self.bootstrap_replicates))
        high_idx = int((ONE - alpha) * Decimal(self.bootstrap_replicates)) - 1
        low_idx = max(0, min(self.bootstrap_replicates - 1, low_idx))
        high_idx = max(0, min(self.bootstrap_replicates - 1, high_idx))
        ci_low = deltas[low_idx]
        ci_high = deltas[high_idx]

        brier_delta = brier_b - brier_s
        significant = ci_low > ZERO

        return PairedDelta(
            n_opportunities=len(scored),
            n_clusters=n_clusters,
            brier_baseline=brier_b,
            brier_scenario=brier_s,
            brier_delta=brier_delta,
            logloss_baseline=logloss_b,
            logloss_scenario=logloss_s,
            logloss_delta=logloss_b - logloss_s,
            ci_low=ci_low,
            ci_high=ci_high,
            ci_level=self.ci_level,
            bootstrap_replicates=self.bootstrap_replicates,
            significant_improvement=significant,
            direction_consistent=self._direction_consistent(scored, scenario_probs),
        )

    @staticmethod
    def _direction_consistent(
        scored: Sequence[ForecastOpportunity], scenario_probs: Sequence[Decimal]
    ) -> bool:
        """The paired delta must keep the same sign across event classes and regimes.

        Robustness by event class/regime (issue #271 Phase 4 acceptance criteria).
        """
        groups: dict[str, list[Decimal]] = {}
        for o, p in zip(scored, scenario_probs, strict=True):
            for key in (f"event_class:{o.event_class}", f"regime:{o.regime}"):
                groups.setdefault(key, []).append(
                    _brier(o.baseline_probability, o.outcome) - _brier(p, o.outcome)
                )
        signs = {1 if sum(v) > ZERO else -1 if sum(v) < ZERO else 0 for v in groups.values()}
        return len(signs - {0}) <= 1

    @staticmethod
    def _discovery_report(opportunities: Sequence[ForecastOpportunity]) -> DiscoveryReport:
        classes = sorted({o.event_class for o in opportunities})
        representable = sorted(
            {
                o.event_class
                for o in opportunities
                if o.scenario_score is not None and o.scenario_run_id is not None
            }
        )
        failed = tuple(c for c in classes if c not in representable)
        coverage = Decimal(len(representable)) / Decimal(len(classes)) if classes else ZERO
        return DiscoveryReport(
            event_classes_evaluated=tuple(classes),
            event_classes_representable=tuple(representable),
            discovery_coverage=coverage,
            discovery_failed_classes=failed,
        )

    @staticmethod
    def _cost_report(opportunities: Sequence[ForecastOpportunity]) -> AblationCostReport:
        """Measured latency/cost. ``measured`` is False when nothing reported runtime."""
        total_runtime = sum(o.scenario_runtime_ms for o in opportunities)
        total_tokens = sum(o.scenario_token_count for o in opportunities)
        total_model_calls = sum(o.scenario_model_calls for o in opportunities)
        mean = Decimal(total_runtime) / Decimal(len(opportunities))
        return AblationCostReport(
            measured=total_runtime > 0,
            total_runtime_ms=total_runtime,
            mean_runtime_ms=mean,
            total_tokens=total_tokens,
            total_model_calls=total_model_calls,
            cost_per_opportunity_ms=mean,
        )


def result_to_dict(result: AblationResult) -> dict[str, Any]:
    """Serialize an ablation result for evidence storage."""
    return {
        "ablation_id": result.ablation_id,
        "verdict": result.verdict.value,
        "may_promote": result.may_promote,
        "paired": {
            "n_opportunities": result.paired.n_opportunities,
            "n_clusters": result.paired.n_clusters,
            "brier_baseline": str(result.paired.brier_baseline),
            "brier_scenario": str(result.paired.brier_scenario),
            "brier_delta": str(result.paired.brier_delta),
            "logloss_baseline": str(result.paired.logloss_baseline),
            "logloss_scenario": str(result.paired.logloss_scenario),
            "logloss_delta": str(result.paired.logloss_delta),
            "ci_low": str(result.paired.ci_low),
            "ci_high": str(result.paired.ci_high),
            "ci_level": str(result.paired.ci_level),
            "bootstrap_replicates": result.paired.bootstrap_replicates,
            "significant_improvement": result.paired.significant_improvement,
            "direction_consistent": result.paired.direction_consistent,
        },
        "discovery": {
            "event_classes_evaluated": list(result.discovery.event_classes_evaluated),
            "event_classes_representable": list(result.discovery.event_classes_representable),
            "discovery_coverage": str(result.discovery.discovery_coverage),
            "discovery_failed_classes": list(result.discovery.discovery_failed_classes),
        },
        "cost": {
            "measured": result.cost.measured,
            "total_runtime_ms": result.cost.total_runtime_ms,
            "mean_runtime_ms": str(result.cost.mean_runtime_ms),
            "total_tokens": result.cost.total_tokens,
            "total_model_calls": result.cost.total_model_calls,
            "cost_per_opportunity_ms": str(result.cost.cost_per_opportunity_ms),
        },
        "coverage_rate": str(result.coverage_rate),
        "temporal_knowledge_controlled": result.temporal_knowledge_controlled,
        "fully_reproducible": result.fully_reproducible,
        "holdout_contaminated": result.holdout_contaminated,
        "blockers": list(result.blockers),
        "warnings": list(result.warnings),
    }
