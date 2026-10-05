"""Funnel trial-family accounting (#268, review STAT-BLOCKER B).

Review clause still open at the funnel level:

* STAT-BLOCKER B — "top-K is a selection operator, not neutral preprocessing". If more
  than one Stage-A threshold, ranking score, K value, feature combination or candidate cap
  is tested, that whole set is part of the multiple-testing family, and "the winning funnel
  must not be evaluated as if it had been given in advance". Acceptance items:
  "funnel parameter variants enter trial-family accounting" and "candidate ranking must not
  be chosen in one shot according to the best full-history result".

The funnel already *identifies* a variant (``FunnelRun.trial_family_key()`` /
``PITFunnelReplay.trial_family_key()``) and already makes the ordering key a versioned,
preregistered part of the config (review BLOCKER B). What was missing is the *accounting*:
nothing forced the set of tested variants to be declared before any of them was observed,
nothing recorded the family size a downstream p-value correction needs, and nothing failed
closed when the "winner" was picked post-hoc from the best full-history metric.

This module supplies exactly that, and nothing else:

* :class:`FunnelVariant` — one immutable, deterministically identified parameter
  combination, constructible from a real ``FunnelRun`` / ``PITFunnelReplay`` so it is not a
  parallel description of the funnel;
* :class:`TrialFamilyDeclaration` — the preregistered family: every variant that will be
  tried, the selection policy, and the optional pre-specified primary variant. A family is
  never a per-incident convenience: an empty or unnamed declaration is refused;
* :class:`TrialFamilyAccounting` — which declared variants were actually observed or
  explicitly excluded with a reason. An undeclared variant cannot be observed
  (post-hoc), and a declared variant cannot simply vanish from the accounting;
* :func:`assert_selection_is_not_full_history` — the fail-closed guard that a variant
  selected on the best full-history metric is only legitimate when the declaration
  pre-specified it as the primary *before* observation.

It is a pure leaf: no I/O, no workers, no clock reads, no database access, no execution
authority. Timestamps are supplied by the caller as ISO strings. It re-implements no
market-data or screening pipeline; it consumes the funnel's own immutable lineage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from quantlab.market_screening import identity

FUNNEL_TRIAL_FAMILY_VERSION = "funnel-trial-family-v1"


class TrialFamilyError(RuntimeError):
    """Base type for the funnel trial-family accounting contracts (STAT-BLOCKER B)."""


class TrialFamilyDeclarationError(TrialFamilyError):
    """Raised when a trial-family declaration is not a usable preregistration."""


class PostHocVariantError(TrialFamilyError):
    """Raised when a variant outside the preregistered family would be observed.

    A variant that was not declared before the run is a *new* member of the
    multiple-testing family, so observing it silently would understate the family size.
    """


class UnpreregisteredWinnerError(TrialFamilyError):
    """Raised when a variant is selected that the declared family never contained."""


class FullHistorySelectionError(TrialFamilyError):
    """Raised when the winner was chosen by the best full-history result (STAT-BLOCKER B).

    Picking the top variant of a family by its full-history metric and then evaluating it
    as if it had been given in advance is exactly the selection bias this clause forbids.
    """


class SelectionBasis(StrEnum):
    """How the selected variant was chosen. Part of the declaration, not an afterthought."""

    #: The declaration pre-specified this variant as primary; no metric chose it.
    PREREGISTERED_PRIMARY = "PREREGISTERED_PRIMARY"
    #: Chosen because it had the best metric over the whole history (must be preregistered).
    BEST_FULL_HISTORY_METRIC = "BEST_FULL_HISTORY_METRIC"
    #: Chosen on data the selection was not fitted to; still requires a declaration.
    BEST_OUT_OF_SAMPLE_METRIC = "BEST_OUT_OF_SAMPLE_METRIC"


@dataclass(frozen=True)
class FunnelVariant:
    """One immutable funnel parameter combination — a member of the trial family.

    The identity is derived from the preregistered parameters only, so two runs of the
    same variant have the same ``variant_id`` and two materially different funnels never
    collide. ``max_candidates`` is the *preregistered cap*; it is deliberately kept
    distinct from the realized final-candidate count of a run, because a family must be
    declared from the parameters that were fixed in advance.

    ``selection_operator_id`` is the first element of the source ``trial_family_key()``.
    ``FunnelRun`` and ``PITFunnelReplay`` use different namespaces there (the funnel
    version versus the PIT semantics version), so the namespace is recorded explicitly
    instead of being silently collapsed — a PIT variant and a live variant must never be
    mistaken for the same member of a family.
    """

    funnel_version: str
    stage_a_config_hash: str
    stage_b_config_hash: str
    ranking_key: str
    ranking_key_version: str
    max_candidates: int
    universe_snapshot_id: str
    key_namespace: str = "funnel"
    selection_operator_id: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        for name in (
            "funnel_version",
            "stage_a_config_hash",
            "stage_b_config_hash",
            "ranking_key",
            "ranking_key_version",
            "universe_snapshot_id",
            "key_namespace",
        ):
            if not str(getattr(self, name)).strip():
                raise TrialFamilyDeclarationError(f"{name} must be non-empty")
        if not self.selection_operator_id.strip():
            object.__setattr__(self, "selection_operator_id", self.funnel_version)
        if self.max_candidates < 1:
            raise TrialFamilyDeclarationError("max_candidates must be positive")

    @property
    def variant_id(self) -> str:
        """Deterministic identity of this parameter combination (order-independent)."""
        return identity(
            {
                "trial_family_version": FUNNEL_TRIAL_FAMILY_VERSION,
                "key_namespace": self.key_namespace,
                "selection_operator_id": self.selection_operator_id,
                "funnel_version": self.funnel_version,
                "stage_a_config_hash": self.stage_a_config_hash,
                "stage_b_config_hash": self.stage_b_config_hash,
                "ranking_key": self.ranking_key,
                "ranking_key_version": self.ranking_key_version,
                "max_candidates": self.max_candidates,
                "universe_snapshot_id": self.universe_snapshot_id,
            }
        )

    @property
    def trial_family_key(self) -> tuple[str, str, str, int]:
        """The funnel-level selection-operator key this variant corresponds to.

        Reproduces the source key verbatim (``FunnelRun.trial_family_key()`` or
        ``PITFunnelReplay.trial_family_key()``), so the accounting is described by the
        funnel's own key rather than by a parallel vocabulary.
        """
        return (
            self.selection_operator_id,
            self.ranking_key,
            self.ranking_key_version,
            self.max_candidates,
        )

    def to_lineage(self) -> dict[str, Any]:
        """Downstream lineage record for this variant (no full-market computation)."""
        return {
            "trial_family_version": FUNNEL_TRIAL_FAMILY_VERSION,
            "variant_id": self.variant_id,
            "key_namespace": self.key_namespace,
            "selection_operator_id": self.selection_operator_id,
            "funnel_version": self.funnel_version,
            "stage_a_config_hash": self.stage_a_config_hash,
            "stage_b_config_hash": self.stage_b_config_hash,
            "ranking_key": self.ranking_key,
            "ranking_key_version": self.ranking_key_version,
            "max_candidates": self.max_candidates,
            "universe_snapshot_id": self.universe_snapshot_id,
            "label": self.label,
        }

    @classmethod
    def from_trial_family_key(
        cls,
        key: tuple[str, str, str, int],
        *,
        funnel_version: str,
        stage_a_config_hash: str,
        stage_b_config_hash: str,
        universe_snapshot_id: str,
        key_namespace: str = "funnel",
        label: str = "",
    ) -> FunnelVariant:
        """Build a variant directly from a funnel ``trial_family_key()`` tuple.

        This is the primary bridge: the key is stored verbatim, so a variant can always be
        traced back to the exact selection operator the funnel reported.
        """
        selection_operator_id, ranking_key, ranking_key_version, max_candidates = key
        return cls(
            funnel_version=funnel_version,
            stage_a_config_hash=stage_a_config_hash,
            stage_b_config_hash=stage_b_config_hash,
            ranking_key=ranking_key,
            ranking_key_version=ranking_key_version,
            max_candidates=int(max_candidates),
            universe_snapshot_id=universe_snapshot_id,
            key_namespace=key_namespace,
            selection_operator_id=selection_operator_id,
            label=label,
        )

    @classmethod
    def from_run(cls, run: Any, *, max_candidates: int, label: str = "") -> FunnelVariant:
        """Build the variant that produced ``run`` (a ``FunnelRun``).

        ``max_candidates`` is the preregistered cap from the run's ``StageBConfig``; it is
        required explicitly so a realized candidate count can never be mistaken for the
        preregistered parameter.
        """
        return cls(
            funnel_version=run.funnel_version,
            stage_a_config_hash=run.stage_a_config_hash,
            stage_b_config_hash=run.stage_b_config_hash,
            ranking_key=run.ranking_key,
            ranking_key_version=run.ranking_key_version,
            max_candidates=max_candidates,
            universe_snapshot_id=run.universe_snapshot_id,
            key_namespace="funnel",
            selection_operator_id=run.funnel_version,
            label=label,
        )

    @classmethod
    def from_replay(cls, replay: Any, *, max_candidates: int, label: str = "") -> FunnelVariant:
        """Build the variant that produced ``replay`` (a ``PITFunnelReplay``).

        The universe snapshot id is taken from the first decision snapshot, because a PIT
        replay spans several decision times; ``max_candidates`` stays the preregistered cap.
        The PIT namespace keeps ``pit_version`` as the selection-operator id, matching
        ``PITFunnelReplay.trial_family_key()``.
        """
        snapshots = replay.decision_snapshots
        if not snapshots:
            raise TrialFamilyDeclarationError(
                "a PIT replay with no decision snapshots cannot identify a variant"
            )
        return cls(
            funnel_version=replay.funnel_version,
            stage_a_config_hash=replay.stage_a_config_hash,
            stage_b_config_hash=replay.stage_b_config_hash,
            ranking_key=replay.ranking_key,
            ranking_key_version=replay.ranking_key_version,
            max_candidates=max_candidates,
            universe_snapshot_id=snapshots[0].universe_snapshot_id,
            key_namespace="pit",
            selection_operator_id=replay.pit_version,
            label=label,
        )


@dataclass(frozen=True)
class TrialFamilyDeclaration:
    """The preregistered set of funnel variants that will be tested (STAT-BLOCKER B).

    The declaration is the whole point: the family size a multiple-testing correction
    needs is only knowable if the variants were fixed before their results were seen.
    ``primary_variant_id`` is the sanctioned way to pre-specify which variant is expected
    to win — without it, choosing the best full-history variant is post-hoc.
    """

    family_id: str
    use_case: str
    selection_policy: str
    variants: tuple[FunnelVariant, ...]
    primary_variant_id: str | None = None
    declared_before_observation: bool = True
    version: str = FUNNEL_TRIAL_FAMILY_VERSION

    def __post_init__(self) -> None:
        if not self.family_id.strip():
            raise TrialFamilyDeclarationError("family_id must be non-empty")
        if not self.use_case.strip():
            raise TrialFamilyDeclarationError(
                "a trial family must name the use case it was preregistered for"
            )
        if not self.selection_policy.strip():
            raise TrialFamilyDeclarationError(
                "a trial family must preregister its selection policy"
            )
        if not self.variants:
            raise TrialFamilyDeclarationError(
                "a trial family with no variants is not a preregistration"
            )
        ids = [variant.variant_id for variant in self.variants]
        if len(set(ids)) != len(ids):
            raise TrialFamilyDeclarationError("a trial family may not declare a variant twice")
        if self.primary_variant_id is not None and self.primary_variant_id not in set(ids):
            raise TrialFamilyDeclarationError(
                "primary_variant_id must name a declared variant of this family"
            )

    @property
    def family_size(self) -> int:
        """Number of variants in the multiple-testing family."""
        return len(self.variants)

    @property
    def variant_ids(self) -> tuple[str, ...]:
        return tuple(variant.variant_id for variant in self.variants)

    def variant(self, variant_id: str) -> FunnelVariant:
        """Return the declared variant, failing closed when it is not in the family."""
        for candidate in self.variants:
            if candidate.variant_id == variant_id:
                return candidate
        raise UnpreregisteredWinnerError(
            f"variant {variant_id} is not a declared member of trial family "
            f"'{self.family_id}' (family size {self.family_size})"
        )

    def contains(self, variant_id: str) -> bool:
        return variant_id in set(self.variant_ids)

    @property
    def config_hash(self) -> str:
        return identity(
            {
                "version": self.version,
                "family_id": self.family_id,
                "use_case": self.use_case,
                "selection_policy": self.selection_policy,
                "variant_ids": list(self.variant_ids),
                "primary_variant_id": self.primary_variant_id,
                "declared_before_observation": self.declared_before_observation,
            }
        )

    def to_lineage(self) -> dict[str, Any]:
        """Trial-family facts a downstream forecast/experiment ledger must carry."""
        return {
            "trial_family_version": self.version,
            "trial_family_id": self.family_id,
            "trial_family_size": self.family_size,
            "trial_family_hash": self.config_hash,
            "trial_family_selection_policy": self.selection_policy,
            "trial_family_primary_variant_id": self.primary_variant_id,
            "trial_family_declared_before_observation": self.declared_before_observation,
        }


@dataclass(frozen=True)
class VariantObservation:
    """One declared variant whose result was actually observed."""

    variant_id: str
    observed_at: str
    basis: SelectionBasis
    metric: float | None = None

    def __post_init__(self) -> None:
        if not self.variant_id.strip():
            raise TrialFamilyDeclarationError("observation variant_id must be non-empty")
        if not self.observed_at.strip():
            raise TrialFamilyDeclarationError("observation observed_at must be non-empty")

    def to_lineage(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "observed_at": self.observed_at,
            "basis": self.basis.value,
            "metric": self.metric,
        }


@dataclass(frozen=True)
class VariantExclusion:
    """A declared variant that was not observed, with the reason it was dropped."""

    variant_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.variant_id.strip():
            raise TrialFamilyDeclarationError("exclusion variant_id must be non-empty")
        if not self.reason.strip():
            raise TrialFamilyDeclarationError("an unexplained exclusion is not an accounting entry")


@dataclass(frozen=True)
class TrialFamilyAccounting:
    """Which declared variants were observed or explicitly excluded (STAT-BLOCKER B).

    Immutable: every mutation returns a new instance, so a partially built accounting is
    never silently reused. Observing a variant that the declaration does not contain fails
    closed, which is what makes a post-hoc variant impossible rather than merely
    discouraged.
    """

    declaration: TrialFamilyDeclaration
    observations: tuple[VariantObservation, ...] = ()
    exclusions: tuple[VariantExclusion, ...] = ()
    version: str = FUNNEL_TRIAL_FAMILY_VERSION

    def __post_init__(self) -> None:
        observed = [observation.variant_id for observation in self.observations]
        if len(set(observed)) != len(observed):
            raise TrialFamilyDeclarationError("a variant may only be observed once")
        excluded = [exclusion.variant_id for exclusion in self.exclusions]
        if len(set(excluded)) != len(excluded):
            raise TrialFamilyDeclarationError("a variant may only be excluded once")
        overlap = set(observed) & set(excluded)
        if overlap:
            raise TrialFamilyDeclarationError(
                f"a variant cannot be both observed and excluded: {sorted(overlap)}"
            )
        for variant_id in observed + excluded:
            if not self.declaration.contains(variant_id):
                raise PostHocVariantError(
                    f"variant {variant_id} is not a declared member of trial family "
                    f"'{self.declaration.family_id}'"
                )

    @property
    def family_size(self) -> int:
        return self.declaration.family_size

    @property
    def observed_variant_ids(self) -> tuple[str, ...]:
        return tuple(observation.variant_id for observation in self.observations)

    @property
    def excluded_variant_ids(self) -> tuple[str, ...]:
        return tuple(exclusion.variant_id for exclusion in self.exclusions)

    @property
    def accounted_variant_ids(self) -> tuple[str, ...]:
        return self.observed_variant_ids + self.excluded_variant_ids

    @property
    def unaccounted_variant_ids(self) -> tuple[str, ...]:
        """Declared variants that are neither observed nor excluded."""
        accounted = set(self.accounted_variant_ids)
        return tuple(
            variant_id for variant_id in self.declaration.variant_ids if variant_id not in accounted
        )

    @property
    def is_fully_accounted(self) -> bool:
        return not self.unaccounted_variant_ids

    def observation_for(self, variant_id: str) -> VariantObservation | None:
        for observation in self.observations:
            if observation.variant_id == variant_id:
                return observation
        return None

    def observe(self, observation: VariantObservation) -> TrialFamilyAccounting:
        """Record an observation of a declared variant (fails closed when undeclared)."""
        if not self.declaration.contains(observation.variant_id):
            raise PostHocVariantError(
                f"variant {observation.variant_id} was not declared in trial family "
                f"'{self.declaration.family_id}' before observation; a variant that is "
                "not in the family may not be observed as if it were"
            )
        if observation.variant_id in set(self.accounted_variant_ids):
            raise TrialFamilyDeclarationError(
                f"variant {observation.variant_id} is already accounted for"
            )
        return TrialFamilyAccounting(
            declaration=self.declaration,
            observations=(*self.observations, observation),
            exclusions=self.exclusions,
            version=self.version,
        )

    def exclude(self, exclusion: VariantExclusion) -> TrialFamilyAccounting:
        """Record a declared variant that was not observed, with its reason."""
        if not self.declaration.contains(exclusion.variant_id):
            raise PostHocVariantError(
                f"variant {exclusion.variant_id} is not a declared member of trial family "
                f"'{self.declaration.family_id}'"
            )
        if exclusion.variant_id in set(self.accounted_variant_ids):
            raise TrialFamilyDeclarationError(
                f"variant {exclusion.variant_id} is already accounted for"
            )
        return TrialFamilyAccounting(
            declaration=self.declaration,
            observations=self.observations,
            exclusions=(*self.exclusions, exclusion),
            version=self.version,
        )

    def to_lineage(self, selected_variant_id: str | None = None) -> dict[str, Any]:
        """Trial-family accounting lineage for the downstream report/ledger."""
        lineage = dict(self.declaration.to_lineage())
        lineage.update(
            {
                "trial_family_accounting_version": self.version,
                "trial_family_observed_variant_ids": list(self.observed_variant_ids),
                "trial_family_excluded_variant_ids": list(self.excluded_variant_ids),
                "trial_family_unaccounted_variant_ids": list(self.unaccounted_variant_ids),
                "trial_family_is_fully_accounted": self.is_fully_accounted,
                "trial_family_selected_variant_id": selected_variant_id,
            }
        )
        return lineage


def assert_family_fully_accounted(accounting: TrialFamilyAccounting) -> None:
    """Fail closed when a declared variant vanished from the accounting.

    The family size is only a correct multiple-testing denominator if every declared
    variant was either observed or explicitly excluded with a reason. A variant that is
    simply absent would understate the family and silently inflate significance.
    """
    unaccounted = accounting.unaccounted_variant_ids
    if unaccounted:
        raise TrialFamilyDeclarationError(
            f"trial family '{accounting.declaration.family_id}' has {len(unaccounted)} "
            f"declared variant(s) neither observed nor excluded: {list(unaccounted)}"
        )


def assert_selection_is_not_full_history(
    accounting: TrialFamilyAccounting,
    selected_variant_id: str,
    basis: SelectionBasis,
    *,
    require_fully_accounted: bool = True,
) -> None:
    """Fail closed unless the selected variant's selection basis is legitimate.

    Selecting the best variant of a family on the full history and then evaluating it as
    if it had been given in advance is the selection bias of STAT-BLOCKER B. The only
    sanctioned full-history path is a declaration that pre-specified that exact variant as
    primary *before* observation; a best-out-of-sample choice still requires a
    preregistered family, and any selection must come from a declared variant.
    """
    declaration = accounting.declaration
    if not declaration.contains(selected_variant_id):
        raise UnpreregisteredWinnerError(
            f"selected variant {selected_variant_id} is not a declared member of trial "
            f"family '{declaration.family_id}' (family size {declaration.family_size})"
        )

    if not declaration.declared_before_observation:
        raise FullHistorySelectionError(
            "the trial family was not declared before observation, so no variant of it "
            "may be selected as the winner"
        )

    if basis is SelectionBasis.PREREGISTERED_PRIMARY:
        if declaration.primary_variant_id is None:
            raise FullHistorySelectionError(
                "selection claims a preregistered primary, but the declaration names none"
            )
        if declaration.primary_variant_id != selected_variant_id:
            raise FullHistorySelectionError(
                "selection claims a preregistered primary, but the declaration preregistered "
                f"{declaration.primary_variant_id}"
            )
    elif basis is SelectionBasis.BEST_FULL_HISTORY_METRIC:
        if declaration.primary_variant_id is None:
            raise FullHistorySelectionError(
                "choosing the best full-history variant without a preregistered primary "
                "is a post-hoc selection and may not be evaluated as if it were given in advance"
            )
        if declaration.primary_variant_id != selected_variant_id:
            raise FullHistorySelectionError(
                "the best full-history variant is not the preregistered primary "
                f"({declaration.primary_variant_id}); the winner may not be chosen from "
                "the full-history metric"
            )
    # BEST_OUT_OF_SAMPLE_METRIC: selection did not use the full history, so it is
    # admissible as long as the family was preregistered (checked above).

    if require_fully_accounted:
        assert_family_fully_accounted(accounting)


def declaration_from_variants(
    family_id: str,
    use_case: str,
    selection_policy: str,
    variants: Sequence[FunnelVariant],
    *,
    primary_variant_id: str | None = None,
    declared_before_observation: bool = True,
) -> TrialFamilyDeclaration:
    """Build a declaration from an explicit, already-enumerated variant set.

    The variant order is normalised by ``variant_id`` so the declaration hash is
    independent of the order in which the caller listed the variants.
    """
    ordered = tuple(sorted(variants, key=lambda variant: variant.variant_id))
    return TrialFamilyDeclaration(
        family_id=family_id,
        use_case=use_case,
        selection_policy=selection_policy,
        variants=ordered,
        primary_variant_id=primary_variant_id,
        declared_before_observation=declared_before_observation,
    )


def declaration_from_trial_family_keys(
    family_id: str,
    use_case: str,
    selection_policy: str,
    keys: Iterable[tuple[str, str, str, int]],
    *,
    stage_a_config_hash: str,
    stage_b_config_hash: str,
    universe_snapshot_id: str,
    primary_variant_id: str | None = None,
    declared_before_observation: bool = True,
) -> TrialFamilyDeclaration:
    """Build a declaration directly from funnel ``trial_family_key()`` tuples.

    This is the bridge from the funnel's own selection-operator identity
    (``FunnelRun.trial_family_key()`` / ``PITFunnelReplay.trial_family_key()``) into
    multiple-testing accounting, so the family is described by the funnel's key rather
    than by a parallel vocabulary.
    """
    variants = [
        FunnelVariant.from_trial_family_key(
            (key[0], key[1], key[2], int(key[3])),
            funnel_version=key[0],
            stage_a_config_hash=stage_a_config_hash,
            stage_b_config_hash=stage_b_config_hash,
            universe_snapshot_id=universe_snapshot_id,
        )
        for key in keys
    ]
    return declaration_from_variants(
        family_id,
        use_case,
        selection_policy,
        variants,
        primary_variant_id=primary_variant_id,
        declared_before_observation=declared_before_observation,
    )


def trial_family_lineage(
    declaration: TrialFamilyDeclaration,
    selected_variant_id: str | None = None,
) -> dict[str, Any]:
    """Trial-family lineage for a report that has no per-variant observations yet.

    Kept separate from :meth:`TrialFamilyAccounting.to_lineage` so a robustness report can
    carry the family size even before every variant has been run — the family size must not
    become known only after the winner is.
    """
    lineage = dict(declaration.to_lineage())
    lineage["trial_family_accounting_version"] = FUNNEL_TRIAL_FAMILY_VERSION
    lineage["trial_family_selected_variant_id"] = selected_variant_id
    return lineage


def funnel_trial_family_required_fields() -> tuple[str, ...]:
    """Fields a downstream record must expose to support trial-family accounting."""
    return (
        "trial_family_id",
        "trial_family_size",
        "trial_family_hash",
        "trial_family_selection_policy",
    )


def audit_trial_family_lineage(
    records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Report lineage records that cannot support multiple-testing accounting.

    A forecast or experiment record that does not carry its trial-family size cannot be
    corrected for the family it was selected from, so it must not enter a pooled
    validation sample.
    """
    required = funnel_trial_family_required_fields()
    gaps: list[tuple[int, tuple[str, ...]]] = []
    for index, record in enumerate(records):
        missing = tuple(name for name in required if record.get(name) in (None, ""))
        if missing:
            gaps.append((index, missing))
    return tuple(gaps)


@dataclass(frozen=True)
class TrialFamilySummary:
    """Counts for the dashboard/report — reads only immutable accounting, no computation."""

    family_id: str
    family_size: int
    observed: int
    excluded: int
    unaccounted: int
    selected_variant_id: str | None
    version: str = FUNNEL_TRIAL_FAMILY_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> dict[str, Any]:
        payload = {
            "trial_family_version": self.version,
            "trial_family_id": self.family_id,
            "trial_family_size": self.family_size,
            "observed_variants": self.observed,
            "excluded_variants": self.excluded,
            "unaccounted_variants": self.unaccounted,
            "selected_variant_id": self.selected_variant_id,
        }
        payload.update(self.extra)
        return payload


def summarise_trial_family(
    accounting: TrialFamilyAccounting,
    selected_variant_id: str | None = None,
) -> TrialFamilySummary:
    """Summarise the accounting for a dashboard/report (no full-market computation)."""
    return TrialFamilySummary(
        family_id=accounting.declaration.family_id,
        family_size=accounting.family_size,
        observed=len(accounting.observations),
        excluded=len(accounting.exclusions),
        unaccounted=len(accounting.unaccounted_variant_ids),
        selected_variant_id=selected_variant_id,
    )
