"""Two-stage candidate funnel: cheap prefilter → bounded strategy evaluation.

Immutable lineage: universe snapshot → Stage A config/version → Stage A candidates
+ rejection reasons → Stage B config/version → final candidates + evidence.

This module does NOT replace screening from #164. It uses existing canonical
market data, universe, and immutable screening evidence as its foundation and
adds explicit multi-level computation budgeting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantlab.funnel_completeness import (
    UniverseCompletion,
    UniverseCompletionState,
    assess_universe_completion,
)
from quantlab.market_data import CorporateAction, Observation
from quantlab.market_screening import evaluate_screen, identity

FUNNEL_VERSION = "funnel-v1"
RANKING_KEY_VERSION = "ranking-v1"


class RankingKey(StrEnum):
    """Versioned, preregistered deterministic ordering key (review BLOCKER B).

    The ordering key lives on StageBConfig, therefore it is part of
    ``stage_b_config_hash`` and of ``funnel_version``. A cap/top-K must be
    applied to this ordering *before* Stage B runs, so the cap can never be
    chosen post-hoc from a Stage-B result.
    """

    INSTRUMENT_ID_ASC = "instrument_id:asc"
    MOMENTUM_DESC = "momentum:desc"
    TREND_DESC = "trend:desc"
    MEAN_REVERSION_DESC = "mean_reversion:desc"
    FEED_DOLLAR_VOLUME_20_DESC = "feed_dollar_volume_20:desc"


#: Preregistered mapping from an ordering key to the canonical Stage A evidence
#: field it ranks by. A metric-based key fails closed when the field is absent.
_RANKING_METRICS: dict[RankingKey, str] = {
    RankingKey.MOMENTUM_DESC: "momentum",
    RankingKey.TREND_DESC: "trend",
    RankingKey.MEAN_REVERSION_DESC: "mean_reversion",
    RankingKey.FEED_DOLLAR_VOLUME_20_DESC: "feed_dollar_volume_20",
}


class SelectionReason(StrEnum):
    """Why an instrument appears in the ordered candidate lineage (BLOCKER C)."""

    STAGE_A_SELECTED = "STAGE_A_SELECTED"
    STAGE_B_SELECTED = "STAGE_B_SELECTED"


class FunnelRankingError(RuntimeError):
    """Raised when a preregistered ordering key cannot be evaluated (fail-closed)."""


class FunnelConfigMismatch(RuntimeError):
    """Raised when a Stage B evaluator would alter the preregistered Stage A ranking."""


class RejectionReason(StrEnum):
    # Canonical status values (review BLOCKER A)
    ELIGIBLE = "ELIGIBLE"
    REJECTED_RULE = "REJECTED_RULE"
    INVALID_DATA = "INVALID_DATA"
    NOT_EVALUATED_BUDGET = "NOT_EVALUATED_BUDGET"
    NOT_EVALUATED_TIMEOUT = "NOT_EVALUATED_TIMEOUT"
    NOT_EVALUATED_RESOURCE_PRESSURE = "NOT_EVALUATED_RESOURCE_PRESSURE"
    # Backward-compatible aliases (deprecated, map to canonical values)
    NOT_IN_UNIVERSE = "NOT_IN_UNIVERSE"
    STAGE_A_FILTERED = "REJECTED_RULE"
    STAGE_B_BUDGET_EXHAUSTED = "NOT_EVALUATED_BUDGET"
    STAGE_B_REJECTED = "REJECTED_RULE"
    INSUFFICIENT_DATA = "INVALID_DATA"
    FUTURE_DATA = "INVALID_DATA"


class CompletionState(StrEnum):
    """Explicit full-universe completion state (review BLOCKER A)."""

    COMPLETE = "COMPLETE"
    PARTIAL_BUDGET = "PARTIAL_BUDGET"
    PARTIAL_TIMEOUT = "PARTIAL_TIMEOUT"
    PARTIAL_RESOURCE_PRESSURE = "PARTIAL_RESOURCE_PRESSURE"


class FunnelStage(StrEnum):
    STAGE_A = "STAGE_A"
    STAGE_B = "STAGE_B"


@dataclass(frozen=True)
class StageAConfig:
    """Versioned typed config for cheap deterministic prefilter.

    All filters are preregistered and deterministic. No strategy-specific
    logic is hardcoded into the general funnel.
    """

    version: str = "stage-a-v1"
    minimum_sessions: int = 127
    minimum_coverage: Decimal = Decimal("0.98")
    minimum_price_usd: Decimal = Decimal("5")
    minimum_feed_dollar_volume_20: Decimal = Decimal("1000000")
    max_price_usd: Decimal | None = None
    min_momentum: Decimal | None = None
    max_momentum: Decimal | None = None
    min_trend: Decimal | None = None
    max_trend: Decimal | None = None
    min_mean_reversion: Decimal | None = None
    max_mean_reversion: Decimal | None = None
    require_corporate_actions_verified: bool = True
    allowed_exchanges: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.minimum_sessions < 1:
            raise ValueError("minimum_sessions must be positive")
        if not Decimal("0") < self.minimum_coverage <= Decimal("1"):
            raise ValueError("minimum_coverage must be in (0, 1]")
        if self.minimum_price_usd <= 0:
            raise ValueError("minimum_price_usd must be positive")
        if self.minimum_feed_dollar_volume_20 < 0:
            raise ValueError("minimum_feed_dollar_volume_20 must be non-negative")

    @property
    def config_hash(self) -> str:
        return identity(self.__dict__)


@dataclass(frozen=True)
class StageBConfig:
    """Versioned typed config for bounded strategy evaluation.

    Stage B has explicit hard budgets for candidates, runtime, provider/API
    requests, LLM/model calls, and concurrency.
    """

    version: str = "stage-b-v1"
    max_candidates: int = 50
    max_runtime_seconds: float = 300.0
    max_provider_requests: int = 100
    max_llm_calls: int = 0
    max_concurrency: int = 1
    min_score_threshold: Decimal | None = None
    ranking_key: RankingKey = RankingKey.INSTRUMENT_ID_ASC
    ranking_key_version: str = RANKING_KEY_VERSION
    max_rank_variants: int = 1

    def __post_init__(self) -> None:
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_provider_requests < 0:
            raise ValueError("max_provider_requests must be non-negative")
        if self.max_llm_calls < 0:
            raise ValueError("max_llm_calls must be non-negative")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if not isinstance(self.ranking_key, RankingKey):
            raise ValueError("ranking_key must be a RankingKey member")
        if not self.ranking_key_version:
            raise ValueError("ranking_key_version must be non-empty")
        if self.max_rank_variants < 1:
            raise ValueError("max_rank_variants must be positive")

    @property
    def config_hash(self) -> str:
        return identity(self.__dict__)

    def order_candidates(self, candidates: Sequence[StageAResult]) -> list[StageAResult]:
        """Return Stage A candidates in the preregistered deterministic order.

        A cap applied to this ordering is deterministic and independent of
        async completion order (review BLOCKER B). The tie-break is always
        ``instrument_id`` ascending, so two candidates with an equal metric
        rank in the same order.
        """

        def metric(result: StageAResult) -> Decimal:
            field_name = _RANKING_METRICS[self.ranking_key]
            raw = result.screening_evidence.get(field_name)
            if raw is None:
                raise FunnelRankingError(
                    f"ranking key {self.ranking_key.value} requires Stage A evidence "
                    f"field '{field_name}' for {result.instrument_id}"
                )
            try:
                return Decimal(str(raw))
            except (ArithmeticError, ValueError) as exc:
                raise FunnelRankingError(
                    f"ranking field '{field_name}' for {result.instrument_id} is not numeric"
                ) from exc

        if self.ranking_key is RankingKey.INSTRUMENT_ID_ASC:
            return sorted(candidates, key=lambda r: r.instrument_id)
        return sorted(
            candidates,
            key=lambda r: (-metric(r), r.instrument_id),
        )


@dataclass(frozen=True)
class StageAResult:
    """Immutable result of Stage A evaluation for one instrument."""

    instrument_id: str
    symbol: str
    passed: bool
    rejection_reason: RejectionReason | None
    rejection_details: dict[str, Any] = field(default_factory=dict)
    screening_evidence: dict[str, Any] = field(default_factory=dict)
    selection_reason: SelectionReason | None = None


@dataclass(frozen=True)
class StageBResult:
    """Immutable result of Stage B evaluation for one candidate."""

    instrument_id: str
    symbol: str
    passed: bool
    rejection_reason: RejectionReason | None
    score: Decimal | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    candidate_rank: int | None = None
    selection_reason: SelectionReason | None = None


@dataclass(frozen=True)
class FunnelRun:
    """Immutable lineage record for a complete funnel run."""

    run_id: str
    universe_snapshot_id: str
    stage_a_config_hash: str
    stage_b_config_hash: str
    funnel_version: str
    completion_state: CompletionState
    created_at: datetime
    stage_a_results: tuple[StageAResult, ...]
    stage_b_results: tuple[StageBResult, ...]
    final_candidates: tuple[str, ...]
    rejection_counts: dict[str, int]
    content_hash: str
    ranking_key: str = ""
    ranking_key_version: str = RANKING_KEY_VERSION
    rank_variants: int = 1
    stage_a_candidate_ranks: tuple[str, ...] = ()
    #: Full-universe completion evidence for the Stage A input (review BLOCKER A). When
    #: no expected membership was declared this is ``UNKNOWN`` and the run is not
    #: promotion-grade, so an incomplete universe can never masquerade as a complete one.
    universe_completion: UniverseCompletion | None = None

    @property
    def universe_completion_state(self) -> str:
        """Explicit full-universe completion state (review BLOCKER A)."""
        if self.universe_completion is None:
            return UniverseCompletionState.UNKNOWN.value
        return self.universe_completion.state.value

    @property
    def is_promotion_grade(self) -> bool:
        """Promotion-grade requires an explicitly COMPLETE Stage A universe and completion."""
        return (
            self.completion_state is CompletionState.COMPLETE
            and self.universe_completion is not None
            and self.universe_completion.is_promotion_grade
        )

    def to_summary(self) -> dict[str, Any]:
        """Return funnel counts and rejection categories for dashboard/report.

        Reads only from the immutable FunnelRun lineage — no expensive
        full-market HTTP computation is performed.
        """
        stage_a_accepted = sum(1 for r in self.stage_a_results if r.passed)
        stage_a_rejected = sum(1 for r in self.stage_a_results if not r.passed)
        stage_b_accepted = sum(1 for r in self.stage_b_results if r.passed)
        stage_b_rejected = sum(1 for r in self.stage_b_results if not r.passed)
        return {
            "run_id": self.run_id,
            "universe_snapshot_id": self.universe_snapshot_id,
            "stage_a_config_hash": self.stage_a_config_hash,
            "stage_b_config_hash": self.stage_b_config_hash,
            "funnel_version": self.funnel_version,
            "completion_state": self.completion_state.value,
            "created_at": self.created_at.isoformat(),
            "ranking_key": self.ranking_key,
            "ranking_key_version": self.ranking_key_version,
            "rank_variants": self.rank_variants,
            "universe_completion_state": self.universe_completion_state,
            "universe_completion": (
                self.universe_completion.to_summary()
                if self.universe_completion is not None
                else None
            ),
            "is_promotion_grade": self.is_promotion_grade,
            "funnel_counts": {
                "universe_size": len(self.stage_a_results),
                "stage_a_accepted": stage_a_accepted,
                "stage_a_rejected": stage_a_rejected,
                "stage_b_candidates_evaluated": len(self.stage_b_results),
                "stage_b_accepted": stage_b_accepted,
                "stage_b_rejected": stage_b_rejected,
                "final_candidates": len(self.final_candidates),
            },
            "rejection_categories": dict(self.rejection_counts),
        }

    def candidate_lineage(self) -> list[dict[str, Any]]:
        """Per-candidate downstream lineage: rank, selection reason, completeness.

        This is the structure a forecast/experiment ledger must embed so that
        Stage-B/forecast performance is always attributable to a specific
        funnel version and Stage A ranking (review BLOCKER C).
        """
        stage_a_rank = {r.instrument_id: r for r in self.stage_a_results if r.passed}
        ordered_ids = list(self.stage_a_candidate_ranks) or [
            r.instrument_id for r in stage_a_rank.values()
        ]
        rank_of = {instrument_id: index for index, instrument_id in enumerate(ordered_ids, 1)}
        stage_b_by_id = {r.instrument_id: r for r in self.stage_b_results}
        lineage: list[dict[str, Any]] = []
        for instrument_id in ordered_ids:
            stage_a = stage_a_rank[instrument_id]
            stage_b = stage_b_by_id.get(instrument_id)
            completeness = stage_a.screening_evidence.get("coverage")
            lineage.append(
                {
                    "instrument_id": instrument_id,
                    "symbol": stage_a.symbol,
                    "candidate_rank": rank_of[instrument_id],
                    "selection_reason": (
                        stage_b.selection_reason.value
                        if stage_b is not None and stage_b.selection_reason is not None
                        else SelectionReason.STAGE_A_SELECTED.value
                    ),
                    "completeness": completeness,
                    "stage_b_passed": None if stage_b is None else stage_b.passed,
                    "funnel_version": self.funnel_version,
                    "stage_a_config_hash": self.stage_a_config_hash,
                    "stage_b_config_hash": self.stage_b_config_hash,
                    "ranking_key": self.ranking_key,
                    "ranking_key_version": self.ranking_key_version,
                    "rank_variants": self.rank_variants,
                    "universe_snapshot_id": self.universe_snapshot_id,
                    "universe_completion_state": self.universe_completion_state,
                }
            )
        return lineage

    def trial_family_key(self) -> tuple[str, str, str, int]:
        """Identity of the selection operator for trial-family accounting.

        Every distinct funnel variant (ranking key, ranking-key version,
        candidate cap) is a separate member of the multiple-testing family
        (STAT-BLOCKER B); downstream validation must stratify on this key.
        """
        return (
            self.funnel_version,
            self.ranking_key,
            self.ranking_key_version,
            len(self.final_candidates),
        )


class StageBBudgetExhausted(RuntimeError):
    pass


class StageBEvaluator:
    """Bounded Stage B evaluator with explicit budget enforcement.

    This is a protocol-like base class. Concrete evaluators must implement
    `evaluate_one` and may override `should_stop` for early termination.
    """

    def __init__(self, config: StageBConfig) -> None:
        self.config = config
        self._candidates_processed = 0
        self._provider_requests = 0
        self._llm_calls = 0
        self._start_time: datetime | None = None

    def start(self) -> None:
        self._start_time = datetime.now(UTC)

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (datetime.now(UTC) - self._start_time).total_seconds()

    @property
    def remaining_candidates(self) -> int:
        return self.config.max_candidates - self._candidates_processed

    @property
    def remaining_runtime_seconds(self) -> float:
        return self.config.max_runtime_seconds - self.elapsed_seconds

    @property
    def remaining_provider_requests(self) -> int:
        return self.config.max_provider_requests - self._provider_requests

    @property
    def remaining_llm_calls(self) -> int:
        return self.config.max_llm_calls - self._llm_calls

    def should_stop(self) -> bool:
        return self.stop_reason() is not None

    def stop_reason(self) -> RejectionReason | None:
        """Return the specific reason for stopping, or None if not stopped.

        Distinguishes budget exhaustion from timeout (review BLOCKER A).
        """
        if self._candidates_processed >= self.config.max_candidates:
            return RejectionReason.NOT_EVALUATED_BUDGET
        if self.elapsed_seconds >= self.config.max_runtime_seconds:
            return RejectionReason.NOT_EVALUATED_TIMEOUT
        if (
            self.config.max_provider_requests > 0
            and self._provider_requests >= self.config.max_provider_requests
        ):
            return RejectionReason.NOT_EVALUATED_BUDGET
        if self.config.max_llm_calls > 0 and self._llm_calls >= self.config.max_llm_calls:
            return RejectionReason.NOT_EVALUATED_BUDGET
        return None

    def evaluate_one(
        self,
        instrument_id: str,
        symbol: str,
        stage_a_evidence: dict[str, Any],
    ) -> StageBResult:
        """Evaluate a single candidate. Must be implemented by subclasses."""
        raise NotImplementedError

    def record_provider_request(self, count: int = 1) -> None:
        self._provider_requests += count

    def record_llm_call(self, count: int = 1) -> None:
        self._llm_calls += count

    def record_candidate_processed(self, count: int = 1) -> None:
        self._candidates_processed += count


class CandidateFunnel:
    """Two-stage candidate funnel orchestrator.

    Stage A: cheap deterministic prefilter using existing screening primitives.
    Stage B: bounded strategy evaluation with explicit budgets.

    The funnel never expands scope beyond the Stage A candidate set.
    """

    def __init__(
        self,
        stage_a_config: StageAConfig | None = None,
        stage_b_config: StageBConfig | None = None,
    ) -> None:
        self.stage_a_config = stage_a_config or StageAConfig()
        self.stage_b_config = stage_b_config or StageBConfig()

    def run_stage_a(
        self,
        universe_snapshot_id: str,
        instruments: Sequence[dict[str, Any]],
        observations: dict[str, Sequence[Observation]],
        actions: dict[str, Sequence[CorporateAction]],
        expected_sessions: Sequence[Any],
        as_of: datetime,
        readiness_ids: dict[str, str | None] | None = None,
        inventory_ids: dict[str, str | None] | None = None,
        inventory_received_at: dict[str, datetime | None] | None = None,
    ) -> tuple[StageAResult, ...]:
        """Run Stage A cheap deterministic prefilter.

        Uses existing evaluate_screen() from market_screening. Never creates
        a parallel market-data pipeline.
        """
        as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of
        readiness_ids = readiness_ids if readiness_ids is not None else {}
        inventory_ids = inventory_ids if inventory_ids is not None else {}
        inventory_received_at = inventory_received_at if inventory_received_at is not None else {}

        results: list[StageAResult] = []
        for inst in instruments:
            inst_id = inst["instrument_id"]
            symbol = inst["symbol"]

            if inst_id not in observations:
                results.append(
                    StageAResult(
                        instrument_id=inst_id,
                        symbol=symbol,
                        passed=False,
                        rejection_reason=RejectionReason.NOT_IN_UNIVERSE,
                        rejection_details={"reason": "instrument has no observations"},
                    )
                )
                continue

            obs = observations[inst_id]
            act = actions.get(inst_id, [])

            # Check for future data (fail-closed)
            future_obs = [o for o in obs if o.observed_at > as_of or o.timestamp > as_of]
            if future_obs:
                results.append(
                    StageAResult(
                        instrument_id=inst_id,
                        symbol=symbol,
                        passed=False,
                        rejection_reason=RejectionReason.FUTURE_DATA,
                        rejection_details={
                            "future_count": len(future_obs),
                            "max_future_timestamp": str(max(o.timestamp for o in future_obs)),
                        },
                    )
                )
                continue

            # Check exchange allowlist
            if self.stage_a_config.allowed_exchanges is not None:
                exchange = inst.get("exchange", "")
                if exchange not in self.stage_a_config.allowed_exchanges:
                    results.append(
                        StageAResult(
                            instrument_id=inst_id,
                            symbol=symbol,
                            passed=False,
                            rejection_reason=RejectionReason.STAGE_A_FILTERED,
                            rejection_details={"exchange_not_allowed": exchange},
                        )
                    )
                    continue

            # Use existing evaluate_screen primitive
            screening = evaluate_screen(
                obs,
                act,
                expected_sessions,
                as_of,
                readiness_ids.get(inst_id),
                inventory_id=inventory_ids.get(inst_id),
                inventory_received_at=inventory_received_at.get(inst_id),
            )

            # Apply additional Stage A filters from config
            rejection_details: dict[str, Any] = {}
            passed = screening["eligible"]

            # The Stage A config is part of funnel_version/stage_a_config_hash, so every
            # declared bound must be a real contract. These are *additional* tightenings
            # on top of the canonical #164 policy — they can never loosen it.
            if passed and screening.get("bars") is not None:
                bars = int(screening["bars"])
                if bars < self.stage_a_config.minimum_sessions:
                    passed = False
                    rejection_details["sessions_too_few"] = bars

            if passed and screening.get("coverage") is not None:
                coverage = Decimal(str(screening["coverage"]))
                if coverage < self.stage_a_config.minimum_coverage:
                    passed = False
                    rejection_details["coverage_too_low"] = str(coverage)

            if passed and obs:
                last_close = obs[-1].close
                if last_close < self.stage_a_config.minimum_price_usd:
                    passed = False
                    rejection_details["price_too_low"] = str(last_close)

            if passed and self.stage_a_config.max_price_usd is not None:
                # Check last close price
                if obs:
                    last_close = obs[-1].close
                    if last_close > self.stage_a_config.max_price_usd:
                        passed = False
                        rejection_details["price_too_high"] = str(last_close)

            if passed and self.stage_a_config.min_momentum is not None:
                momentum = screening.get("momentum")
                if momentum is not None and Decimal(momentum) < self.stage_a_config.min_momentum:
                    passed = False
                    rejection_details["momentum_too_low"] = momentum

            if passed and self.stage_a_config.max_momentum is not None:
                momentum = screening.get("momentum")
                if momentum is not None and Decimal(momentum) > self.stage_a_config.max_momentum:
                    passed = False
                    rejection_details["momentum_too_high"] = momentum

            if passed and self.stage_a_config.min_trend is not None:
                trend = screening.get("trend")
                if trend is not None and Decimal(trend) < self.stage_a_config.min_trend:
                    passed = False
                    rejection_details["trend_too_low"] = trend

            if passed and self.stage_a_config.max_trend is not None:
                trend = screening.get("trend")
                if trend is not None and Decimal(trend) > self.stage_a_config.max_trend:
                    passed = False
                    rejection_details["trend_too_high"] = trend

            if passed and self.stage_a_config.min_mean_reversion is not None:
                mr = screening.get("mean_reversion")
                if mr is not None and Decimal(mr) < self.stage_a_config.min_mean_reversion:
                    passed = False
                    rejection_details["mean_reversion_too_low"] = mr

            if passed and self.stage_a_config.max_mean_reversion is not None:
                mr = screening.get("mean_reversion")
                if mr is not None and Decimal(mr) > self.stage_a_config.max_mean_reversion:
                    passed = False
                    rejection_details["mean_reversion_too_high"] = mr

            if passed and self.stage_a_config.require_corporate_actions_verified:
                if not screening.get("action_readiness_id") and not screening.get(
                    "current_action_receipt_id"
                ):
                    passed = False
                    rejection_details["corporate_actions_not_verified"] = True

            results.append(
                StageAResult(
                    instrument_id=inst_id,
                    symbol=symbol,
                    passed=passed,
                    rejection_reason=None if passed else RejectionReason.STAGE_A_FILTERED,
                    rejection_details=rejection_details,
                    screening_evidence=screening,
                    selection_reason=SelectionReason.STAGE_A_SELECTED if passed else None,
                )
            )

        return tuple(results)

    def run_stage_b(
        self,
        stage_a_results: Sequence[StageAResult],
        evaluator: StageBEvaluator,
    ) -> tuple[StageBResult, ...]:
        """Run Stage B bounded strategy evaluation.

        Only candidates that passed Stage A are evaluated, in the
        preregistered deterministic order. The evaluator enforces hard
        budgets for candidates, runtime, requests, and LLM calls.

        The evaluator may not change the preregistered Stage B config (which
        owns the ranking key) — a mismatch fails closed (review BLOCKER C).
        """
        if evaluator.config.config_hash != self.stage_b_config.config_hash:
            raise FunnelConfigMismatch(
                "Stage B evaluator config does not match the funnel's preregistered "
                "Stage B config; a Stage-B agent may not change ranking or budgets"
            )
        evaluator.start()
        results: list[StageBResult] = []

        # Preregistered, versioned deterministic ordering (review BLOCKER B).
        # The cap/top-K is applied to THIS order, so async completion order can
        # never change candidate selection and the cap is never post-hoc.
        ordered = self.stage_b_config.order_candidates([ar for ar in stage_a_results if ar.passed])

        for rank, ar in enumerate(ordered, 1):
            stop = evaluator.stop_reason()
            if stop is not None:
                results.append(
                    StageBResult(
                        instrument_id=ar.instrument_id,
                        symbol=ar.symbol,
                        passed=False,
                        rejection_reason=stop,
                        candidate_rank=rank,
                        selection_reason=SelectionReason.STAGE_A_SELECTED,
                    )
                )
                continue

            evaluator.record_candidate_processed()
            try:
                result = evaluator.evaluate_one(
                    ar.instrument_id,
                    ar.symbol,
                    ar.screening_evidence,
                )
                results.append(
                    replace(
                        result,
                        candidate_rank=rank,
                        selection_reason=(
                            SelectionReason.STAGE_B_SELECTED
                            if result.passed
                            else SelectionReason.STAGE_A_SELECTED
                        ),
                    )
                )
            except Exception as exc:
                results.append(
                    StageBResult(
                        instrument_id=ar.instrument_id,
                        symbol=ar.symbol,
                        passed=False,
                        rejection_reason=RejectionReason.STAGE_B_REJECTED,
                        evidence={"error": type(exc).__name__, "message": str(exc)[:500]},
                        candidate_rank=rank,
                        selection_reason=SelectionReason.STAGE_A_SELECTED,
                    )
                )

        return tuple(results)

    def run(
        self,
        universe_snapshot_id: str,
        instruments: Sequence[dict[str, Any]],
        observations: dict[str, Sequence[Observation]],
        actions: dict[str, Sequence[CorporateAction]],
        expected_sessions: Sequence[Any],
        as_of: datetime,
        stage_b_evaluator: StageBEvaluator,
        readiness_ids: dict[str, str | None] | None = None,
        inventory_ids: dict[str, str | None] | None = None,
        inventory_received_at: dict[str, datetime | None] | None = None,
        expected_universe_members: Sequence[str] | None = None,
    ) -> FunnelRun:
        """Run complete two-stage funnel and return immutable lineage record.

        ``expected_universe_members`` is the canonical universe membership known at
        ``as_of`` (e.g. ``PointInTimeUniverse.eligible``). When supplied, the run carries
        explicit full-universe completion evidence and a promotion-grade claim is only
        possible for a COMPLETE universe (review BLOCKER A). When omitted, the completion
        state is UNKNOWN and the run is never promotion-grade.
        """
        stage_a_results = self.run_stage_a(
            universe_snapshot_id,
            instruments,
            observations,
            actions,
            expected_sessions,
            as_of,
            readiness_ids if readiness_ids is not None else {},
            inventory_ids if inventory_ids is not None else {},
            inventory_received_at if inventory_received_at is not None else {},
        )

        # Preregistered Stage A candidate ordering (review BLOCKER B). Computed
        # BEFORE Stage B so the cap is applied to a fixed order and is never
        # chosen post-hoc from a Stage B result. Fails closed on a missing metric.
        ordered_candidates = self.stage_b_config.order_candidates(
            [r for r in stage_a_results if r.passed]
        )
        stage_a_candidate_ranks = tuple(r.instrument_id for r in ordered_candidates)

        # Explicit full-universe completion state (review BLOCKER A). Only the canonical
        # universe membership the caller declared at `as_of` can make the run
        # promotion-grade; omitting it yields UNKNOWN, never an implicit COMPLETE.
        universe_completion: UniverseCompletion | None = None
        if expected_universe_members is not None:
            universe_completion = assess_universe_completion(
                universe_snapshot_id,
                expected_universe_members,
                [r.instrument_id for r in stage_a_results],
            )

        stage_b_results = self.run_stage_b(stage_a_results, stage_b_evaluator)

        final_candidates = tuple(r.instrument_id for r in stage_b_results if r.passed)

        # Compute completion state (review BLOCKER A)
        completion_state = CompletionState.COMPLETE
        for br in stage_b_results:
            if br.rejection_reason is RejectionReason.NOT_EVALUATED_BUDGET:
                completion_state = CompletionState.PARTIAL_BUDGET
                break
            if br.rejection_reason is RejectionReason.NOT_EVALUATED_TIMEOUT:
                completion_state = CompletionState.PARTIAL_TIMEOUT
                break
            if br.rejection_reason is RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE:
                completion_state = CompletionState.PARTIAL_RESOURCE_PRESSURE
                break

        # Funnel version is part of downstream lineage (review BLOCKER C)
        funnel_version = identity(
            {
                "module_version": FUNNEL_VERSION,
                "ranking_key_version": self.stage_b_config.ranking_key_version,
                "ranking_key": self.stage_b_config.ranking_key.value,
                "stage_a_config": self.stage_a_config.config_hash,
                "stage_b_config": self.stage_b_config.config_hash,
            }
        )

        rejection_counts: dict[str, int] = {}
        for r in stage_a_results:
            if not r.passed and r.rejection_reason:
                key = r.rejection_reason.value
                rejection_counts[key] = rejection_counts.get(key, 0) + 1
        for br in stage_b_results:
            if not br.passed and br.rejection_reason:
                key = br.rejection_reason.value
                rejection_counts[key] = rejection_counts.get(key, 0) + 1

        content = {
            "universe_snapshot_id": universe_snapshot_id,
            "stage_a_config": self.stage_a_config.__dict__,
            "stage_b_config": self.stage_b_config.__dict__,
            "funnel_version": funnel_version,
            "completion_state": completion_state.value,
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
            "stage_a_candidate_ranks": list(stage_a_candidate_ranks),
            "stage_b_results": [
                {
                    "instrument_id": r.instrument_id,
                    "symbol": r.symbol,
                    "passed": r.passed,
                    "rejection_reason": r.rejection_reason.value if r.rejection_reason else None,
                    "score": str(r.score) if r.score is not None else None,
                    "candidate_rank": r.candidate_rank,
                    "selection_reason": r.selection_reason.value if r.selection_reason else None,
                }
                for r in stage_b_results
            ],
            "final_candidates": list(final_candidates),
            "rejection_counts": rejection_counts,
            "universe_completion": (
                universe_completion.content_hash if universe_completion is not None else None
            ),
        }
        content_hash = identity(content)
        run_id = identity(
            {
                "universe": universe_snapshot_id,
                "stage_a_config": self.stage_a_config.config_hash,
                "stage_b_config": self.stage_b_config.config_hash,
                "funnel_version": funnel_version,
                "content": content_hash,
            }
        )

        return FunnelRun(
            run_id=run_id,
            universe_snapshot_id=universe_snapshot_id,
            stage_a_config_hash=self.stage_a_config.config_hash,
            stage_b_config_hash=self.stage_b_config.config_hash,
            funnel_version=funnel_version,
            completion_state=completion_state,
            created_at=datetime.now(UTC),
            stage_a_results=stage_a_results,
            stage_b_results=stage_b_results,
            final_candidates=final_candidates,
            rejection_counts=rejection_counts,
            content_hash=content_hash,
            ranking_key=self.stage_b_config.ranking_key.value,
            ranking_key_version=self.stage_b_config.ranking_key_version,
            rank_variants=self.stage_b_config.max_rank_variants,
            stage_a_candidate_ranks=stage_a_candidate_ranks,
            universe_completion=universe_completion,
        )


def deterministic_replay_check(
    run1: FunnelRun,
    run2: FunnelRun,
) -> bool:
    """Verify that two funnel runs with same inputs produce same candidate set.

    Replay equality covers the preregistered ordering and the candidate rank
    lineage, not only the final set (review BLOCKER B/C).
    """
    return (
        run1.universe_snapshot_id == run2.universe_snapshot_id
        and run1.stage_a_config_hash == run2.stage_a_config_hash
        and run1.stage_b_config_hash == run2.stage_b_config_hash
        and run1.funnel_version == run2.funnel_version
        and run1.completion_state == run2.completion_state
        and run1.final_candidates == run2.final_candidates
        and run1.rejection_counts == run2.rejection_counts
        and run1.ranking_key == run2.ranking_key
        and run1.ranking_key_version == run2.ranking_key_version
        and run1.stage_a_candidate_ranks == run2.stage_a_candidate_ranks
        and run1.universe_completion_state == run2.universe_completion_state
    )
