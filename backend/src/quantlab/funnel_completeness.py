"""Full-universe completion state and funnel-version pooling guards (#268).

Two review clauses of the #268 candidate funnel were still open at the funnel level:

* review BLOCKER A — "a promotion-grade run over an incomplete Stage A universe must be
  fail-closed unless partial mode is explicitly preregistered for the use case", plus the
  acceptance item "full-universe completion state is explicit". The existing
  ``CompletionState`` only describes whether *Stage B* ran out of budget/time/resources;
  it says nothing about whether the *Stage A universe* was complete. A run whose Stage A
  input list silently held only a page of the universe therefore looked exactly like a
  complete run.
* review BLOCKER C — the downstream lineage must carry the ``completeness state``
  alongside ``funnel_version`` / Stage A config hash / candidate rank, and calibration and
  validation must not mix forecasts from materially different funnel versions without
  explicit stratification or revalidation.

This module is a pure leaf: no I/O, no workers, no clock reads, no database access, no
execution authority. It consumes the canonical #164 universe membership
(``PointInTimeUniverse.eligible``) and the immutable ``StageAResult`` / ``PITFunnelReplay``
lineage — it never re-implements a market-data or screening pipeline.

Default posture is fail-closed: an undeclared expected universe is ``UNKNOWN`` and is never
promotion-grade, and partial mode can only be granted by an explicitly preregistered
``PartialModePolicy`` that names its use case and reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from quantlab.market_screening import identity

UNIVERSE_COMPLETENESS_VERSION = "universe-completeness-v1"


class FunnelCompletenessError(RuntimeError):
    """Base type for the completeness/pooling contracts of the candidate funnel."""


class PromotionGradeError(FunnelCompletenessError):
    """Raised when a non-promotion-grade run is used where a complete universe is required.

    Budget exhaustion, timeout and resource pressure are *not* economic rejections, and an
    undeclared or partially covered Stage A universe is not a complete universe. A
    promotion-grade decision must therefore fail closed rather than silently consume
    partial evidence (review BLOCKER A).
    """


class FunnelVersionMixError(FunnelCompletenessError):
    """Raised when evidence from materially different funnel versions would be pooled.

    Stage-B/forecast performance is conditional on the Stage A selection that produced the
    sample, so two materially different funnel versions are two different economic
    identities. Pooling them without explicit stratification/revalidation invalidates the
    comparison (review BLOCKER C).
    """


class UniverseCompletionState(StrEnum):
    """Explicit full-universe completion state for a Stage A evaluation (review BLOCKER A)."""

    #: Every declared expected universe member received a Stage A evaluation.
    COMPLETE = "COMPLETE"
    #: Some declared expected members are absent from the Stage A evaluation.
    PARTIAL_MISSING_MEMBERS = "PARTIAL_MISSING_MEMBERS"
    #: No expected membership was declared, so completeness cannot be asserted at all.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PartialModePolicy:
    """Preregistered partial-mode permission for one use case (review BLOCKER A).

    Partial mode is *not* a per-incident override: it must name the use case it was
    preregistered for and the reason, so an incomplete Stage A universe can never be
    silently promoted. The default is fail-closed.
    """

    use_case: str = ""
    allow_partial: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.allow_partial and not (self.use_case.strip() and (self.reason or "").strip()):
            raise ValueError(
                "partial mode must be preregistered with a use case and a reason; "
                "an unnamed partial mode is not a preregistration"
            )

    @property
    def preregistered(self) -> bool:
        """True only when partial mode was explicitly preregistered for a named use case."""
        return (
            self.allow_partial and bool(self.use_case.strip()) and bool((self.reason or "").strip())
        )

    @property
    def config_hash(self) -> str:
        return identity(self.__dict__)


@dataclass(frozen=True)
class UniverseCompletion:
    """Immutable full-universe completion evidence for one Stage A evaluation.

    ``evaluated_members`` is the set of instruments Stage A actually produced a result
    for; ``expected_members`` is the canonical universe membership known at the decision
    time. ``unexpected_members`` (evaluated but not declared) is recorded as scope
    evidence — a Stage-B agent may never widen the Stage A scope.
    """

    universe_snapshot_id: str
    expected_members: tuple[str, ...]
    evaluated_members: tuple[str, ...]
    missing_members: tuple[str, ...]
    unexpected_members: tuple[str, ...]
    state: UniverseCompletionState
    content_hash: str
    completion_version: str = UNIVERSE_COMPLETENESS_VERSION

    @property
    def is_complete(self) -> bool:
        return self.state is UniverseCompletionState.COMPLETE

    @property
    def is_promotion_grade(self) -> bool:
        """A promotion-grade run requires an explicitly COMPLETE Stage A universe."""
        return self.is_complete

    def to_summary(self) -> dict[str, Any]:
        """Completion counts/state for the dashboard — no full-market computation."""
        return {
            "universe_snapshot_id": self.universe_snapshot_id,
            "completion_version": self.completion_version,
            "state": self.state.value,
            "expected_members": len(self.expected_members),
            "evaluated_members": len(self.evaluated_members),
            "missing_members": len(self.missing_members),
            "unexpected_members": len(self.unexpected_members),
            "content_hash": self.content_hash,
        }


def assess_universe_completion(
    universe_snapshot_id: str,
    expected_members: Sequence[str],
    evaluated_members: Sequence[str],
) -> UniverseCompletion:
    """Compare the declared universe membership against what Stage A actually evaluated.

    Order-independent and deterministic: the same membership/evaluation pair always yields
    the same state and content hash. An empty ``expected_members`` declaration is
    ``UNKNOWN`` rather than COMPLETE — an undeclared universe can never be asserted
    complete (review BLOCKER A, fail-closed default).
    """
    if not universe_snapshot_id:
        raise FunnelCompletenessError("universe_snapshot_id must be non-empty")

    expected = tuple(sorted(set(expected_members)))
    evaluated = tuple(sorted(set(evaluated_members)))

    missing = tuple(member for member in expected if member not in set(evaluated))
    unexpected = tuple(member for member in evaluated if member not in set(expected))

    if not expected:
        state = UniverseCompletionState.UNKNOWN
    elif missing:
        state = UniverseCompletionState.PARTIAL_MISSING_MEMBERS
    else:
        state = UniverseCompletionState.COMPLETE

    content_hash = identity(
        {
            "completion_version": UNIVERSE_COMPLETENESS_VERSION,
            "universe_snapshot_id": universe_snapshot_id,
            "expected_members": list(expected),
            "evaluated_members": list(evaluated),
            "missing_members": list(missing),
            "unexpected_members": list(unexpected),
            "state": state.value,
        }
    )

    return UniverseCompletion(
        universe_snapshot_id=universe_snapshot_id,
        expected_members=expected,
        evaluated_members=evaluated,
        missing_members=missing,
        unexpected_members=unexpected,
        state=state,
        content_hash=content_hash,
    )


def require_promotion_grade(
    completion: UniverseCompletion | None,
    policy: PartialModePolicy | None = None,
) -> None:
    """Fail closed unless the run is promotion-grade for this use case.

    A COMPLETE universe always passes. A PARTIAL universe passes only under an explicitly
    preregistered ``PartialModePolicy``. An UNKNOWN universe — including a run that
    declared no membership at all (``None``) — never passes, because there is nothing to be
    partial *of*.
    """
    if completion is None:
        raise PromotionGradeError(
            "run declares no expected universe membership, so completeness is UNKNOWN; "
            "an undeclared universe cannot be promotion-grade"
        )
    if completion.is_complete:
        return
    if (
        completion.state is UniverseCompletionState.PARTIAL_MISSING_MEMBERS
        and policy is not None
        and policy.preregistered
    ):
        return
    raise PromotionGradeError(
        f"run over universe snapshot '{completion.universe_snapshot_id}' is "
        f"{completion.state.value} ({len(completion.missing_members)} missing of "
        f"{len(completion.expected_members)} declared members); promotion requires an "
        "explicitly COMPLETE universe or a preregistered partial mode"
    )


def assert_single_funnel_version(funnel_versions: Sequence[str]) -> str:
    """Return the single funnel version, failing closed when versions are mixed.

    Calibration and validation must not pool evidence produced by materially different
    funnel versions. Callers that genuinely need both must stratify explicitly with
    :func:`stratify_by_funnel_version` instead of pooling (review BLOCKER C).
    """
    distinct = {version for version in funnel_versions}
    if len(distinct) > 1:
        raise FunnelVersionMixError(
            "evidence from multiple funnel versions would be pooled without explicit "
            f"stratification: {sorted(distinct)}"
        )
    if not distinct:
        raise FunnelVersionMixError("no funnel version supplied to compare")
    return next(iter(distinct))


def stratify_by_funnel_version[T](
    items: Iterable[T],
    version_of: Callable[[T], str],
) -> dict[str, tuple[T, ...]]:
    """Explicit stratification of evidence by funnel version (review BLOCKER C).

    This is the *only* sanctioned way to combine materially different funnel versions: the
    grouping is explicit in the caller's code and the returned mapping is deterministic
    (keys sorted, item order preserved).
    """
    grouped: dict[str, list[T]] = {}
    for item in items:
        grouped.setdefault(version_of(item), []).append(item)
    return {version: tuple(grouped[version]) for version in sorted(grouped)}


def lineage_with_universe_completion(
    lineage: Mapping[str, Any],
    completion: UniverseCompletion,
) -> dict[str, Any]:
    """Add the run-level completeness state to a downstream lineage record.

    The funnel version alone is not enough: a forecast produced over a PARTIAL Stage A
    universe is materially different evidence from one produced over a COMPLETE universe.
    The resulting mapping is what belongs in ``ExperimentIdentity.funnel_lineage`` so both
    facts travel into the experiment/forecast identity (review BLOCKER C).
    """
    enriched = dict(lineage)
    enriched.update(
        {
            "universe_snapshot_id": completion.universe_snapshot_id,
            "universe_completion_state": completion.state.value,
            "universe_completeness_version": completion.completion_version,
            "universe_completion_hash": completion.content_hash,
            "universe_expected_member_count": len(completion.expected_members),
            "universe_missing_member_count": len(completion.missing_members),
        }
    )
    return enriched


#: Fields a lineage record must expose so downstream validation can detect a material
#: funnel-version or completeness mismatch without re-deriving it.
REQUIRED_FUNNEL_LINEAGE_FIELDS: tuple[str, ...] = (
    "funnel_version",
    "stage_a_config_hash",
    "candidate_rank",
    "selection_reason",
    "universe_completion_state",
)


@dataclass(frozen=True)
class FunnelLineageGap:
    """A lineage record missing one required funnel-lineage field."""

    record_index: int
    missing_fields: tuple[str, ...]


def audit_funnel_lineage(
    lineage_records: Sequence[Mapping[str, Any]],
) -> tuple[FunnelLineageGap, ...]:
    """Report lineage records that cannot support a conditional validation claim.

    A record missing ``funnel_version`` / Stage A config hash / candidate rank / selection
    reason / universe completion state cannot be stratified later, so it must not be
    admitted into a calibration or validation sample (review BLOCKER C).
    """
    gaps: list[FunnelLineageGap] = []
    for index, record in enumerate(lineage_records):
        missing = tuple(
            name for name in REQUIRED_FUNNEL_LINEAGE_FIELDS if record.get(name) in (None, "")
        )
        if missing:
            gaps.append(FunnelLineageGap(record_index=index, missing_fields=missing))
    return tuple(gaps)


@dataclass(frozen=True)
class FunnelPoolingAudit:
    """Result of auditing whether a set of lineage records may be pooled."""

    funnel_versions: tuple[str, ...]
    universe_completion_states: tuple[str, ...]
    pooling_permitted: bool
    lineage_gaps: tuple[FunnelLineageGap, ...] = field(default_factory=tuple)

    @property
    def requires_stratification(self) -> bool:
        return len(self.funnel_versions) > 1 or len(self.universe_completion_states) > 1


def audit_funnel_pooling(lineage_records: Sequence[Mapping[str, Any]]) -> FunnelPoolingAudit:
    """Audit whether lineage records from one or more funnel versions may be pooled.

    Records are poolable only when every record carries the required funnel-lineage fields
    and all records share one funnel version and one universe completion state. Anything
    else must be stratified or revalidated first (review BLOCKER C).
    """
    gaps = audit_funnel_lineage(lineage_records)
    versions = tuple(
        sorted(
            {
                str(record.get("funnel_version"))
                for record in lineage_records
                if record.get("funnel_version")
            }
        )
    )
    states = tuple(
        sorted(
            {
                str(record.get("universe_completion_state"))
                for record in lineage_records
                if record.get("universe_completion_state")
            }
        )
    )
    permitted = not gaps and len(versions) == 1 and len(states) == 1
    return FunnelPoolingAudit(
        funnel_versions=versions,
        universe_completion_states=states,
        pooling_permitted=permitted,
        lineage_gaps=gaps,
    )
