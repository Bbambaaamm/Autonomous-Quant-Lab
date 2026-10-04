"""Tests for the purged/embargoed splitter — issue #272 workflow C.

Covers: purge overlap semantics, embargo boundaries, adversarial leakage cases,
fail-closed policy violations, determinism, and CSCV grouping.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantlab.purged_validation import (
    EmbargoPolicy,
    PurgedSplitPlan,
    PurgePolicy,
    SampleInterval,
    build_purged_split,
    cscv_groups,
    split_plan_payload,
)
from quantlab.statistical_registry import SplitPlanInput

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _samples(count: int, *, horizon_days: int = 0, step_days: int = 1):
    """Ordered daily samples; ``horizon_days`` sets the label resolution lag."""
    return [
        SampleInterval(
            sample_id=f"s{index:03d}",
            decision_time=BASE + timedelta(days=index * step_days),
            label_resolution_time=BASE + timedelta(days=index * step_days + horizon_days),
        )
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_split_is_deterministic():
    samples = _samples(30)
    first = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    second = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    assert first.integrity_hash == second.integrity_hash
    assert first.to_dict() == second.to_dict()
    assert split_plan_payload(first) == split_plan_payload(second)


def test_different_sample_horizon_changes_hash():
    zero = build_purged_split(
        _samples(30, horizon_days=0), train_fraction=0.6, validation_fraction=0.2
    )
    lagged = build_purged_split(
        _samples(30, horizon_days=1), train_fraction=0.6, validation_fraction=0.2
    )
    assert zero.integrity_hash != lagged.integrity_hash
    assert zero.sample_hash != lagged.sample_hash


def test_policy_change_changes_hash():
    samples = _samples(30)
    none = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    embargoed = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=2)),
    )
    assert none.integrity_hash != embargoed.integrity_hash


# ---------------------------------------------------------------------------
# Purge semantics
# ---------------------------------------------------------------------------


def test_zero_horizon_samples_are_not_purged():
    plan = build_purged_split(_samples(30), train_fraction=0.6, validation_fraction=0.2)
    assert plan.purged_sample_ids == ()
    assert plan.embargoed_sample_ids == ()


def test_long_horizon_train_sample_is_purged_by_overlap():
    samples = _samples(30, horizon_days=3)
    plan = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    raw_validation_start = samples[18].decision_time  # int(30 * 0.6)
    by_id = {s.sample_id: s for s in samples}
    assert plan.purged_sample_ids
    assert plan.partition("VALIDATION").sample_ids
    for sample_id in plan.partition("TRAIN").sample_ids:
        assert by_id[sample_id].label_resolution_time < raw_validation_start
    for sample_id in plan.purged_sample_ids:
        assert by_id[sample_id].label_resolution_time >= raw_validation_start
    retained = {sid for p in plan.partitions for sid in p.sample_ids}
    assert retained.isdisjoint(plan.purged_sample_ids)


def test_label_resolving_exactly_at_validation_start_is_purged():
    """Closed intervals: touching the validation window is an overlap."""
    samples = _samples(10)
    # train = s000..s005, validation starts at s006 (day 6).
    samples[5] = SampleInterval(
        sample_id="s005",
        decision_time=BASE + timedelta(days=5),
        label_resolution_time=BASE + timedelta(days=6),
    )
    plan = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    assert plan.partition("VALIDATION").start == BASE + timedelta(days=6)
    assert "s005" in plan.purged_sample_ids
    assert "s005" not in plan.partition("TRAIN").sample_ids


def test_label_resolving_before_validation_window_is_retained():
    samples = _samples(10)
    samples[5] = SampleInterval(
        sample_id="s005",
        decision_time=BASE + timedelta(days=5),
        label_resolution_time=BASE + timedelta(days=5, hours=12),
    )
    plan = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    assert "s005" not in plan.purged_sample_ids
    assert "s005" in plan.partition("TRAIN").sample_ids


def test_validation_label_bleeding_into_final_oos_is_purged():
    """Adversarial: a validation label resolving inside final-OOS is purged."""
    samples = _samples(10)
    # train = s000..s005, validation = s006..s007, final starts at s008 (day 8).
    samples[7] = SampleInterval(
        sample_id="s007",
        decision_time=BASE + timedelta(days=7),
        label_resolution_time=BASE + timedelta(days=8),
    )
    plan = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    assert plan.partition("FINAL_OOS").start == BASE + timedelta(days=8)
    assert "s007" in plan.purged_sample_ids
    assert "s007" not in plan.partition("VALIDATION").sample_ids


def test_no_label_overlap_policy_passes_for_zero_horizon():
    plan = build_purged_split(
        _samples(30),
        train_fraction=0.6,
        validation_fraction=0.2,
        purge_policy=PurgePolicy(kind="NO_LABEL_OVERLAP"),
    )
    assert plan.purged_sample_ids == ()


def test_no_label_overlap_policy_fails_closed_on_label_horizon():
    with pytest.raises(ValueError, match="NO_LABEL_OVERLAP"):
        build_purged_split(
            _samples(30, horizon_days=1),
            train_fraction=0.6,
            validation_fraction=0.2,
            purge_policy=PurgePolicy(kind="NO_LABEL_OVERLAP"),
        )


# ---------------------------------------------------------------------------
# Embargo semantics
# ---------------------------------------------------------------------------


def test_purge_set_is_independent_of_embargo_policy():
    """Purge uses frozen raw windows; embargo must not change who is purged."""
    samples = _samples(30, horizon_days=3)
    none = build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)
    embargoed = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=2)),
    )
    assert none.purged_sample_ids == embargoed.purged_sample_ids
    assert embargoed.embargoed_sample_ids


def test_embargo_removes_only_the_guard_band():
    samples = _samples(30)
    duration = timedelta(days=3)
    plan = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=duration),
    )
    train_end = plan.partition("TRAIN").info_end
    validation_end = plan.partition("VALIDATION").info_end
    by_id = {s.sample_id: s for s in samples}
    for sample_id in plan.embargoed_sample_ids:
        sample = by_id[sample_id]
        assert sample.decision_time < train_end + duration or (
            sample.decision_time < validation_end + duration
        )
    for sample_id in plan.partition("VALIDATION").sample_ids:
        assert by_id[sample_id].decision_time >= train_end + duration
    for sample_id in plan.partition("FINAL_OOS").sample_ids:
        assert by_id[sample_id].decision_time >= validation_end + duration


def test_embargo_boundary_sample_exactly_at_cutoff_is_retained():
    samples = _samples(10)
    plan = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=1)),
    )
    # train info_end = day 5; cutoff = day 6 -> s006 is the first retained sample.
    assert plan.partition("TRAIN").info_end == BASE + timedelta(days=5)
    assert "s006" in plan.partition("VALIDATION").sample_ids
    assert "s006" not in plan.embargoed_sample_ids


def test_none_embargo_removes_nothing():
    plan = build_purged_split(
        _samples(30),
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="NONE"),
    )
    assert plan.embargoed_sample_ids == ()
    assert plan.embargo_policy.to_dict() == {"kind": "NONE", "duration_seconds": 0.0}


# ---------------------------------------------------------------------------
# Conservation / adversarial cases
# ---------------------------------------------------------------------------


def test_every_sample_lands_in_exactly_one_bucket():
    samples = _samples(40, horizon_days=2)
    plan = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=1)),
    )
    retained = [sid for p in plan.partitions for sid in p.sample_ids]
    accounted = retained + list(plan.purged_sample_ids) + list(plan.embargoed_sample_ids)
    assert sorted(accounted) == sorted(s.sample_id for s in samples)
    assert len(accounted) == len(set(accounted))


def test_no_lookahead_across_retained_partitions():
    samples = _samples(40, horizon_days=1)
    plan = build_purged_split(
        samples,
        train_fraction=0.6,
        validation_fraction=0.2,
        embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=2)),
    )
    train, validation, final = (
        plan.partition(role) for role in ("TRAIN", "VALIDATION", "FINAL_OOS")
    )
    assert train.info_end < validation.start
    assert validation.info_end < final.start


def test_embargo_that_empties_a_partition_fails_closed():
    with pytest.raises(ValueError, match="removed an entire chronological partition"):
        build_purged_split(
            _samples(10),
            train_fraction=0.8,
            validation_fraction=0.1,
            embargo_policy=EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(days=30)),
        )


def test_purge_that_empties_train_fails_closed():
    with pytest.raises(ValueError, match="removed an entire chronological partition"):
        build_purged_split(
            _samples(10, horizon_days=30), train_fraction=0.6, validation_fraction=0.2
        )


def test_split_requires_enough_samples_for_three_partitions():
    with pytest.raises(ValueError, match="each chronological partition must contain data"):
        build_purged_split(_samples(3), train_fraction=0.9, validation_fraction=0.05)


def test_duplicate_sample_ids_rejected():
    samples = _samples(10)
    samples[1] = SampleInterval(
        sample_id="s000",
        decision_time=samples[1].decision_time,
        label_resolution_time=samples[1].label_resolution_time,
    )
    with pytest.raises(ValueError, match="duplicate sample_id"):
        build_purged_split(samples, train_fraction=0.6, validation_fraction=0.2)


def test_empty_sample_set_rejected():
    with pytest.raises(ValueError, match="at least one sample"):
        build_purged_split([], train_fraction=0.6, validation_fraction=0.2)


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        SampleInterval(
            sample_id="s000",
            decision_time=datetime(2024, 1, 1),
            label_resolution_time=datetime(2024, 1, 2),
        )


def test_label_before_decision_rejected():
    with pytest.raises(ValueError, match="must not precede"):
        SampleInterval(
            sample_id="s000",
            decision_time=BASE + timedelta(days=1),
            label_resolution_time=BASE,
        )


def test_invalid_fractions_rejected():
    samples = _samples(10)
    with pytest.raises(ValueError, match="train_fraction"):
        build_purged_split(samples, train_fraction=0.0, validation_fraction=0.2)
    with pytest.raises(ValueError, match="below 1"):
        build_purged_split(samples, train_fraction=0.9, validation_fraction=0.2)


def test_unsupported_method_rejected():
    with pytest.raises(ValueError, match="Unsupported split method"):
        build_purged_split(
            _samples(10),
            train_fraction=0.6,
            validation_fraction=0.2,
            method="RANDOM_SHUFFLE_V1",
        )


def test_unsupported_policy_kinds_rejected():
    with pytest.raises(ValueError, match="Unsupported purge kind"):
        PurgePolicy(kind="DROP_EVERYTHING")
    with pytest.raises(ValueError, match="Unsupported embargo kind"):
        EmbargoPolicy(kind="ADAPTIVE")
    with pytest.raises(ValueError, match="FIXED_DURATION"):
        EmbargoPolicy(kind="FIXED_DURATION", duration=timedelta(0))
    with pytest.raises(ValueError, match="NONE embargo"):
        EmbargoPolicy(kind="NONE", duration=timedelta(days=1))


# ---------------------------------------------------------------------------
# Persistence bridge
# ---------------------------------------------------------------------------


def test_split_plan_fields_feed_the_registry_input():
    plan = build_purged_split(
        _samples(30, horizon_days=2), train_fraction=0.6, validation_fraction=0.2
    )
    fields = plan.to_split_plan_fields()
    split_input = SplitPlanInput(
        snapshot_id="snap-1",
        target_spec_hash="a" * 64,
        decision_time_policy={"kind": "DECISION_AT_CLOSE"},
        label_interval_policy={"kind": "RESOLUTION_HORIZON"},
        **fields,
    )
    assert split_input.method == "PURGED_WALK_FORWARD_V1"
    assert split_input.final_holdout_hash == plan.final_holdout_hash
    roles = [p["role"] for p in split_input.partitions]
    assert roles == ["TRAIN", "VALIDATION", "FINAL_OOS"]


def test_final_holdout_hash_tracks_only_the_final_partition():
    base = build_purged_split(_samples(30), train_fraction=0.6, validation_fraction=0.2)
    longer = build_purged_split(_samples(31), train_fraction=0.6, validation_fraction=0.2)
    assert base.final_holdout_hash != longer.final_holdout_hash
    assert (
        base.final_holdout_hash
        == build_purged_split(
            _samples(30), train_fraction=0.6, validation_fraction=0.2
        ).final_holdout_hash
    )


def test_custom_role_names_are_honoured():
    plan = build_purged_split(
        _samples(30),
        train_fraction=0.6,
        validation_fraction=0.2,
        roles=("SELECTION_POOL_A", "SELECTION_POOL_B", "FINAL_OOS"),
    )
    assert plan.partition("SELECTION_POOL_A").role == "SELECTION_POOL_A"
    assert plan.to_dict()["roles"] == [
        "SELECTION_POOL_A",
        "SELECTION_POOL_B",
        "FINAL_OOS",
    ]


# ---------------------------------------------------------------------------
# CSCV grouping
# ---------------------------------------------------------------------------


def test_cscv_groups_are_contiguous_and_balanced():
    samples = _samples(10)
    groups = cscv_groups(samples, 4)
    assert len(groups) == 4
    sizes = [len(g.sample_ids) for g in groups]
    assert sum(sizes) == 10
    assert max(sizes) - min(sizes) <= 1
    flattened = [sid for g in groups for sid in g.sample_ids]
    assert flattened == [s.sample_id for s in samples]
    for group in groups:
        assert group.role == "SELECTION_POOL_GROUP"
        assert group.info_end >= group.start


def test_cscv_groups_reject_degenerate_counts():
    samples = _samples(4)
    with pytest.raises(ValueError, match="at least two"):
        cscv_groups(samples, 1)
    with pytest.raises(ValueError, match="must not exceed"):
        cscv_groups(samples, 5)


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_module_performs_no_io_and_reads_no_clock():
    """The splitter must stay a deterministic pure function."""
    import quantlab.purged_validation as module

    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    forbidden_calls = {"open", "input", "print", "exec", "eval"}
    forbidden_attrs = {"now", "today", "utcnow", "time", "system", "run"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in forbidden_calls, f"I/O call: {func.id}"
            if isinstance(func, ast.Attribute):
                assert func.attr not in forbidden_attrs, f"impure call: {func.attr}"
    assert isinstance(
        build_purged_split(_samples(10), train_fraction=0.6, validation_fraction=0.2),
        PurgedSplitPlan,
    )
