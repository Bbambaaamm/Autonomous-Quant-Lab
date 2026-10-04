"""Purged/embargoed chronological splitting — issue #272 workflow C.

Deterministic, pure-function splitter that turns ordered ``decision_time ->
label_resolution_time`` intervals into a frozen train/validation/final-OOS
plan with explicit purge and embargo evidence.

The module never reads the clock, the filesystem, or a database. It emits the
exact JSON shapes required by ``statistical_split_plans`` (``partitions_json``,
``purge_policy_json``, ``embargo_policy_json``, ``final_holdout_hash``) so a
caller can persist the frozen plan through ``SplitPlanInput`` unchanged.

Semantics
---------
* **Purge** — a sample of an earlier partition is removed when its information
  interval ``[decision_time, label_resolution_time]`` overlaps the validation or
  the final-OOS window (train samples) or the final-OOS window (validation
  samples). ``INTERVAL_OVERLAP`` uses closed intervals, so a label that
  resolves exactly at the later window's first decision time is purged.
  ``NO_LABEL_OVERLAP`` declares that no sample carries a label horizon: it
  fails closed when any sample resolves later than its decision time and purges
  nothing by construction.
* **Embargo** — a fixed guard band after the earlier window's resolved
  information. At every boundary ``A -> B`` the samples of ``B`` whose
  ``decision_time`` is strictly before ``A.info_end + duration`` are removed
  from ``B``. A sample exactly at ``A.info_end + duration`` is retained.
* No sample is ever dropped silently: every input sample ends up in exactly one
  retained partition, in ``purged_sample_ids``, or in ``embargoed_sample_ids``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from quantlab.statistical_registry import (
    SPLIT_METHODS,
    canonical_json,
    identity_hash,
)

DEFAULT_ROLES: tuple[str, str, str] = ("TRAIN", "VALIDATION", "FINAL_OOS")

PURGE_KINDS = frozenset({"NO_LABEL_OVERLAP", "INTERVAL_OVERLAP"})

EMBARGO_KINDS = frozenset({"NONE", "FIXED_DURATION"})

CSCV_GROUP_ROLE = "SELECTION_POOL_GROUP"


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class SampleInterval:
    """One candidate sample with its decision and label-resolution times."""

    sample_id: str
    decision_time: datetime
    label_resolution_time: datetime

    def __post_init__(self) -> None:
        if not self.sample_id:
            raise ValueError("sample_id must be non-empty")
        _require_aware("decision_time", self.decision_time)
        _require_aware("label_resolution_time", self.label_resolution_time)
        if self.label_resolution_time < self.decision_time:
            raise ValueError("label_resolution_time must not precede decision_time")

    @property
    def has_label_horizon(self) -> bool:
        """True when the label resolves strictly after the decision."""
        return self.label_resolution_time > self.decision_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "decision_time": self.decision_time.isoformat(),
            "label_resolution_time": self.label_resolution_time.isoformat(),
        }


@dataclass(frozen=True)
class PurgePolicy:
    """Declared purge policy for a frozen split plan."""

    kind: str = "INTERVAL_OVERLAP"

    def __post_init__(self) -> None:
        if self.kind not in PURGE_KINDS:
            raise ValueError(f"Unsupported purge kind: {self.kind}")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind}


@dataclass(frozen=True)
class EmbargoPolicy:
    """Declared embargo policy for a frozen split plan."""

    kind: str = "NONE"
    duration: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.kind not in EMBARGO_KINDS:
            raise ValueError(f"Unsupported embargo kind: {self.kind}")
        if self.duration < timedelta(0):
            raise ValueError("embargo duration must not be negative")
        if self.kind == "NONE" and self.duration != timedelta(0):
            raise ValueError("NONE embargo must not declare a duration")
        if self.kind == "FIXED_DURATION" and self.duration <= timedelta(0):
            raise ValueError("FIXED_DURATION embargo requires a positive duration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "duration_seconds": self.duration.total_seconds(),
        }


@dataclass(frozen=True)
class Partition:
    """One retained chronological partition."""

    partition_id: str
    role: str
    sample_ids: tuple[str, ...]
    start: datetime
    info_end: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "role": self.role,
            "sample_ids": list(self.sample_ids),
            "start": self.start.isoformat(),
            "info_end": self.info_end.isoformat(),
        }


@dataclass(frozen=True)
class PurgedSplitPlan:
    """Immutable, hash-addressed result of a purged/embargoed split."""

    method: str
    roles: tuple[str, str, str]
    partitions: tuple[Partition, ...]
    purged_sample_ids: tuple[str, ...]
    embargoed_sample_ids: tuple[str, ...]
    purge_policy: PurgePolicy
    embargo_policy: EmbargoPolicy
    seed: int
    sample_hash: str
    integrity_hash: str

    @property
    def final_holdout_hash(self) -> str:
        """Content hash of the final-OOS partition, for the frozen split plan."""
        final = next(p for p in self.partitions if p.role == self.roles[2])
        return identity_hash(list(final.sample_ids))

    def partition(self, role: str) -> Partition:
        """Return the retained partition for ``role``."""
        for candidate in self.partitions:
            if candidate.role == role:
                return candidate
        raise KeyError(role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "roles": list(self.roles),
            "partitions": [p.to_dict() for p in self.partitions],
            "purged_sample_ids": list(self.purged_sample_ids),
            "embargoed_sample_ids": list(self.embargoed_sample_ids),
            "purge_policy": self.purge_policy.to_dict(),
            "embargo_policy": self.embargo_policy.to_dict(),
            "seed": self.seed,
            "sample_hash": self.sample_hash,
            "final_holdout_hash": self.final_holdout_hash,
        }

    def to_split_plan_fields(self) -> dict[str, Any]:
        """Return the exact fields consumed by ``SplitPlanInput``."""
        return {
            "method": self.method,
            "purge_policy": self.purge_policy.to_dict(),
            "embargo_policy": self.embargo_policy.to_dict(),
            "partitions": [p.to_dict() for p in self.partitions],
            "final_holdout_hash": self.final_holdout_hash,
            "seed": self.seed,
        }


def _validate_fractions(train_fraction: float, validation_fraction: float) -> None:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be strictly between 0 and 1")
    if not 0 <= validation_fraction < 1:
        raise ValueError("validation_fraction must be in [0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must be below 1")


def _ordered_samples(samples: Sequence[SampleInterval]) -> list[SampleInterval]:
    if not samples:
        raise ValueError("split requires at least one sample")
    seen: set[str] = set()
    for sample in samples:
        if sample.sample_id in seen:
            raise ValueError(f"duplicate sample_id: {sample.sample_id}")
        seen.add(sample.sample_id)
    return sorted(samples, key=lambda s: (s.decision_time, s.sample_id))


def _window(samples: Sequence[SampleInterval]) -> tuple[datetime, datetime]:
    return samples[0].decision_time, max(s.label_resolution_time for s in samples)


def _overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a <= end_b and end_a >= start_b


def build_purged_split(
    samples: Sequence[SampleInterval],
    *,
    train_fraction: float,
    validation_fraction: float,
    purge_policy: PurgePolicy | None = None,
    embargo_policy: EmbargoPolicy | None = None,
    method: str = "PURGED_WALK_FORWARD_V1",
    seed: int = 42,
    roles: tuple[str, str, str] = DEFAULT_ROLES,
) -> PurgedSplitPlan:
    """Build a deterministic purged/embargoed chronological split.

    Fails closed when a declared policy is violated (label horizons under
    ``NO_LABEL_OVERLAP``), when the chronology cannot produce three non-empty
    raw partitions, or when purge/embargo would empty a retained partition.
    """
    if method not in SPLIT_METHODS:
        raise ValueError(f"Unsupported split method: {method}")
    purge = purge_policy or PurgePolicy()
    embargo = embargo_policy or EmbargoPolicy()
    _validate_fractions(train_fraction, validation_fraction)
    ordered = _ordered_samples(samples)

    if purge.kind == "NO_LABEL_OVERLAP":
        offenders = [s.sample_id for s in ordered if s.has_label_horizon]
        if offenders:
            raise ValueError(
                "NO_LABEL_OVERLAP purge declared but samples carry a label "
                f"horizon: {sorted(offenders)}"
            )

    total = len(ordered)
    first = int(total * train_fraction)
    second = first + int(total * validation_fraction)
    if first < 1 or second <= first or second >= total:
        raise ValueError("each chronological partition must contain data")

    raw_train = ordered[:first]
    raw_validation = ordered[first:second]
    raw_final = ordered[second:]
    # Purge decisions use the FROZEN raw windows so the outcome cannot depend on
    # which samples an earlier purge step happened to remove.
    validation_window = _window(raw_validation)
    final_window = _window(raw_final)

    purged: list[str] = []

    def _purge_overlaps(
        candidates: Sequence[SampleInterval], windows: Sequence[tuple[datetime, datetime]]
    ) -> list[SampleInterval]:
        """Drop samples whose information interval touches a later window."""
        kept: list[SampleInterval] = []
        for sample in candidates:
            if purge.kind == "INTERVAL_OVERLAP" and any(
                _overlaps(
                    sample.decision_time,
                    sample.label_resolution_time,
                    window_start,
                    window_end,
                )
                for window_start, window_end in windows
            ):
                purged.append(sample.sample_id)
            else:
                kept.append(sample)
        return kept

    retained_train = _purge_overlaps(raw_train, (validation_window, final_window))
    retained_validation = _purge_overlaps(raw_validation, (final_window,))

    embargoed: list[str] = []

    def _apply_embargo(
        earlier: tuple[datetime, datetime], later: Sequence[SampleInterval]
    ) -> list[SampleInterval]:
        if embargo.kind == "NONE":
            return list(later)
        cutoff = earlier[1] + embargo.duration
        kept: list[SampleInterval] = []
        for sample in later:
            if sample.decision_time < cutoff:
                embargoed.append(sample.sample_id)
            else:
                kept.append(sample)
        return kept

    if retained_train:
        retained_validation = _apply_embargo(_window(retained_train), retained_validation)
    if retained_validation:
        retained_final = _apply_embargo(_window(retained_validation), raw_final)
    else:
        retained_final = list(raw_final)

    if not retained_train or not retained_validation or not retained_final:
        raise ValueError("purge/embargo removed an entire chronological partition")

    partitions = (
        _partition("train", roles[0], retained_train),
        _partition("validation", roles[1], retained_validation),
        _partition("final", roles[2], retained_final),
    )

    sample_hash = identity_hash([s.to_dict() for s in ordered])
    purged_ids = tuple(sorted(purged))
    embargoed_ids = tuple(sorted(embargoed))
    payload = {
        "method": method,
        "roles": list(roles),
        "partitions": [p.to_dict() for p in partitions],
        "purged_sample_ids": list(purged_ids),
        "embargoed_sample_ids": list(embargoed_ids),
        "purge_policy": purge.to_dict(),
        "embargo_policy": embargo.to_dict(),
        "seed": seed,
        "sample_hash": sample_hash,
    }
    return PurgedSplitPlan(
        method=method,
        roles=roles,
        partitions=partitions,
        purged_sample_ids=purged_ids,
        embargoed_sample_ids=embargoed_ids,
        purge_policy=purge,
        embargo_policy=embargo,
        seed=seed,
        sample_hash=sample_hash,
        integrity_hash=identity_hash(payload),
    )


def _partition(partition_id: str, role: str, samples: Sequence[SampleInterval]) -> Partition:
    return Partition(
        partition_id=partition_id,
        role=role,
        sample_ids=tuple(s.sample_id for s in samples),
        start=samples[0].decision_time,
        info_end=max(s.label_resolution_time for s in samples),
    )


def cscv_groups(samples: Sequence[SampleInterval], n_groups: int) -> tuple[Partition, ...]:
    """Split a selection research pool into ``S`` contiguous CSCV groups.

    Group sizes differ by at most one and the extra samples are assigned to the
    earliest groups, so the grouping is deterministic for a given ordered pool.
    """
    if n_groups < 2:
        raise ValueError("CSCV requires at least two contiguous groups")
    ordered = _ordered_samples(samples)
    if n_groups > len(ordered):
        raise ValueError("CSCV groups must not exceed the number of samples")
    base, remainder = divmod(len(ordered), n_groups)
    groups: list[Partition] = []
    cursor = 0
    for index in range(n_groups):
        size = base + (1 if index < remainder else 0)
        chunk = ordered[cursor : cursor + size]
        cursor += size
        groups.append(_partition(f"cscv-group-{index}", CSCV_GROUP_ROLE, chunk))
    return tuple(groups)


def split_plan_payload(plan: PurgedSplitPlan) -> str:
    """Canonical JSON of the persistable split-plan evidence."""
    return canonical_json(plan.to_dict())
