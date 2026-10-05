"""Tests for full-universe completion state and funnel-version pooling guards (#268).

Covers the review clauses that were still open at the funnel level:

- review BLOCKER A: full-universe completion state is explicit, and a promotion-grade run
  over an incomplete Stage A universe fails closed unless partial mode is preregistered;
- review BLOCKER A acceptance: budget/timeout/resource-pressure deferral is not an economic
  rejection, and a partial Stage A universe is never reported as COMPLETE;
- review BLOCKER C: the completeness state travels into the downstream lineage next to the
  funnel version / Stage A config hash / candidate rank, and materially different funnel
  versions cannot be pooled without explicit stratification;
- purity: the completeness module performs no I/O, reads no clock, and has no execution
  authority.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.candidate_funnel import (
    CandidateFunnel,
    CompletionState,
    RankingKey,
    RejectionReason,
    StageBConfig,
    StageBEvaluator,
    StageBResult,
    deterministic_replay_check,
)
from quantlab.funnel_completeness import (
    UNIVERSE_COMPLETENESS_VERSION,
    FunnelVersionMixError,
    PartialModePolicy,
    PromotionGradeError,
    UniverseCompletionState,
    assert_single_funnel_version,
    assess_universe_completion,
    audit_funnel_lineage,
    audit_funnel_pooling,
    lineage_with_universe_completion,
    require_promotion_grade,
    stratify_by_funnel_version,
)
from quantlab.funnel_pit import (
    PITDecisionTime,
    recompute_pit_replay,
    require_pit_promotion_grade,
)
from quantlab.market_data import CorporateAction, CorporateActionKind, Observation, XNYSCalendar

CAL = XNYSCalendar()
DAYS = CAL.sessions_between(date(2026, 1, 2), date(2026, 9, 21))
NOW = CAL.session_close(DAYS[-1])


# ─── fixtures (mirroring the sibling funnel test helpers) ───────────────────────────────


def make_observations(
    instrument_id: str = "asset-1",
    symbol: str = "TEST",
    sessions: tuple[date, ...] | None = None,
    close: Decimal = Decimal("100"),
    volume: Decimal = Decimal("20000"),
) -> list[Observation]:
    sessions = sessions if sessions is not None else DAYS
    return [
        Observation(
            f"{instrument_id}-{i}",
            instrument_id,
            "alpaca:iex",
            "1d",
            d,
            CAL.session_close(d),
            close,
            close,
            close,
            close,
            volume,
            CAL.session_close(d),
            f"src-{i}",
            f"hash-{i}",
            "import",
        )
        for i, d in enumerate(sessions)
    ]


def make_instrument(instrument_id: str, symbol: str) -> dict[str, object]:
    return {"instrument_id": instrument_id, "symbol": symbol, "exchange": "XNYS"}


def make_action(instrument_id: str) -> CorporateAction:
    return CorporateAction(
        f"ca-{instrument_id}",
        instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        CAL.session_open(DAYS[-10]),
        NOW,
        Decimal("1.00"),
    )


def _universe(
    n: int,
) -> tuple[
    list[dict[str, object]],
    dict[str, Sequence[Observation]],
    dict[str, Sequence[CorporateAction]],
    dict[str, str | None],
]:
    instruments = [make_instrument(f"asset-{i:03d}", f"A{i}") for i in range(n)]
    observations: dict[str, Sequence[Observation]] = {
        f"asset-{i:03d}": make_observations(f"asset-{i:03d}", f"A{i}") for i in range(n)
    }
    actions: dict[str, Sequence[CorporateAction]] = {
        f"asset-{i:03d}": [make_action(f"asset-{i:03d}")] for i in range(n)
    }
    readiness: dict[str, str | None] = {f"asset-{i:03d}": f"r{i}" for i in range(n)}
    return instruments, observations, actions, readiness


class PassingStageBEvaluator(StageBEvaluator):
    def __init__(self, config: StageBConfig) -> None:
        super().__init__(config)

    def evaluate_one(
        self, instrument_id: str, symbol: str, stage_a_evidence: dict[str, object]
    ) -> StageBResult:
        self.record_provider_request(1)
        return StageBResult(
            instrument_id=instrument_id,
            symbol=symbol,
            passed=True,
            rejection_reason=None,
            score=Decimal("0.5"),
        )


class ResourcePressureStageBEvaluator(StageBEvaluator):
    """Defers every candidate under resource pressure without losing Stage A evidence."""

    def __init__(self, config: StageBConfig) -> None:
        super().__init__(config)

    def stop_reason(self) -> RejectionReason | None:
        return RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE


# ─── BLOCKER A: universe completion state is explicit ──────────────────────────────────


def test_complete_universe_state_when_every_member_evaluated():
    completion = assess_universe_completion(
        "snap-1", ["asset-000", "asset-001", "asset-002"], ["asset-002", "asset-000", "asset-001"]
    )
    assert completion.state is UniverseCompletionState.COMPLETE
    assert completion.is_complete
    assert completion.is_promotion_grade
    assert completion.missing_members == ()
    assert completion.completion_version == UNIVERSE_COMPLETENESS_VERSION


def test_partial_universe_state_lists_missing_members():
    completion = assess_universe_completion(
        "snap-1", ["asset-000", "asset-001", "asset-002"], ["asset-000"]
    )
    assert completion.state is UniverseCompletionState.PARTIAL_MISSING_MEMBERS
    assert not completion.is_complete
    assert not completion.is_promotion_grade
    assert completion.missing_members == ("asset-001", "asset-002")


def test_undeclared_universe_is_unknown_never_complete():
    """An empty membership declaration must not be read as COMPLETE (fail-closed default)."""
    completion = assess_universe_completion("snap-1", [], ["asset-000"])
    assert completion.state is UniverseCompletionState.UNKNOWN
    assert not completion.is_promotion_grade
    assert completion.unexpected_members == ("asset-000",)


def test_completion_records_unexpected_scope_widening():
    """An instrument evaluated outside the declared membership is recorded, not ignored."""
    completion = assess_universe_completion("snap-1", ["asset-000"], ["asset-000", "asset-999"])
    assert completion.state is UniverseCompletionState.COMPLETE
    assert completion.unexpected_members == ("asset-999",)


def test_completion_is_order_independent_and_deterministic():
    a = assess_universe_completion("snap-1", ["a", "b", "c"], ["b", "a", "c"])
    b = assess_universe_completion("snap-1", ["c", "b", "a"], ["c", "b", "a"])
    assert a.content_hash == b.content_hash
    assert a.expected_members == b.expected_members == ("a", "b", "c")
    assert a.evaluated_members == b.evaluated_members == ("a", "b", "c")


def test_completion_hash_changes_with_membership():
    a = assess_universe_completion("snap-1", ["a", "b"], ["a", "b"])
    b = assess_universe_completion("snap-1", ["a", "b", "c"], ["a", "b", "c"])
    assert a.content_hash != b.content_hash


# ─── BLOCKER A: promotion-grade fails closed over an incomplete universe ───────────────


def test_promotion_grade_passes_for_complete_universe():
    completion = assess_universe_completion("snap-1", ["a", "b"], ["a", "b"])
    require_promotion_grade(completion)  # must not raise


def test_promotion_grade_fails_closed_over_partial_universe():
    completion = assess_universe_completion("snap-1", ["a", "b"], ["a"])
    with pytest.raises(PromotionGradeError, match="PARTIAL_MISSING_MEMBERS"):
        require_promotion_grade(completion)


def test_promotion_grade_fails_closed_over_undeclared_universe():
    completion = assess_universe_completion("snap-1", [], ["a"])
    with pytest.raises(PromotionGradeError, match="UNKNOWN"):
        require_promotion_grade(completion)


def test_promotion_grade_fails_closed_when_no_completion_at_all():
    with pytest.raises(PromotionGradeError, match="UNKNOWN"):
        require_promotion_grade(None)


def test_partial_mode_preregistration_permits_partial_universe():
    completion = assess_universe_completion("snap-1", ["a", "b"], ["a"])
    policy = PartialModePolicy(
        use_case="exploratory-scan", allow_partial=True, reason="screening only"
    )
    require_promotion_grade(completion, policy)  # must not raise
    assert policy.preregistered


def test_partial_mode_never_permits_undeclared_universe():
    """Partial mode is meaningless over an undeclared universe; still fail closed."""
    completion = assess_universe_completion("snap-1", [], ["a"])
    policy = PartialModePolicy(
        use_case="exploratory-scan", allow_partial=True, reason="screening only"
    )
    with pytest.raises(PromotionGradeError, match="UNKNOWN"):
        require_promotion_grade(completion, policy)


def test_partial_mode_without_use_case_and_reason_is_rejected():
    """An unnamed partial mode is not a preregistration."""
    with pytest.raises(ValueError):
        PartialModePolicy(allow_partial=True)
    with pytest.raises(ValueError):
        PartialModePolicy(use_case="x", allow_partial=True)
    assert not PartialModePolicy().preregistered


# ─── BLOCKER A wiring: FunnelRun carries explicit completion state ─────────────────────


def test_funnel_run_records_complete_universe_state():
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=[i["instrument_id"] for i in instruments],
    )
    assert run.universe_completion_state == UniverseCompletionState.COMPLETE.value
    assert run.is_promotion_grade
    assert run.universe_completion is not None
    assert run.universe_completion.missing_members == ()


def test_funnel_run_without_declared_membership_is_never_promotion_grade():
    """Omitting the expected membership yields UNKNOWN, not an implicit COMPLETE."""
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    assert run.universe_completion is None
    assert run.universe_completion_state == UniverseCompletionState.UNKNOWN.value
    assert not run.is_promotion_grade


def test_funnel_run_partial_universe_is_not_promotion_grade():
    """A universe that silently held only a page must not look like a complete run."""
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments[:2],  # only 2 of the 3 declared members are evaluated
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=[i["instrument_id"] for i in instruments],
    )
    assert run.universe_completion_state == UniverseCompletionState.PARTIAL_MISSING_MEMBERS.value
    assert not run.is_promotion_grade
    with pytest.raises(PromotionGradeError):
        require_promotion_grade(run.universe_completion)


def test_resource_pressure_defers_stage_b_without_losing_stage_a_evidence():
    """Resource pressure is NOT an economic rejection and keeps the Stage A universe intact."""
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        ResourcePressureStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=[i["instrument_id"] for i in instruments],
    )
    assert run.completion_state is CompletionState.PARTIAL_RESOURCE_PRESSURE
    assert not run.is_promotion_grade  # Stage B deferral is not promotion-grade
    # Stage A evidence survives: the universe is still fully accounted for.
    assert run.universe_completion_state == UniverseCompletionState.COMPLETE.value
    assert len(run.stage_a_results) == 3
    assert all(
        r.rejection_reason is RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE
        for r in run.stage_b_results
    )
    assert run.rejection_counts.get("REJECTED_RULE") is None


def test_completion_state_is_part_of_replay_equality():
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    funnel = CandidateFunnel(stage_b_config=config)
    members = [str(i["instrument_id"]) for i in instruments]
    complete = funnel.run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=members,
    )
    partial = funnel.run(
        "snap-1",
        instruments[:2],
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=members,
    )
    assert not deterministic_replay_check(complete, partial)
    assert complete.universe_completion_state != partial.universe_completion_state


def test_funnel_summary_exposes_completion_state_and_promotion_grade():
    instruments, observations, actions, readiness = _universe(3)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=[i["instrument_id"] for i in instruments],
    )
    summary = run.to_summary()
    assert summary["universe_completion_state"] == "COMPLETE"
    assert summary["is_promotion_grade"] is True
    assert summary["universe_completion"]["expected_members"] == 3


# ─── BLOCKER A wiring: PIT replay carries per-decision completion state ────────────────


def _pit_folds(count: int = 3) -> tuple[PITDecisionTime, ...]:
    picks = DAYS[-60 : -60 + count]
    return tuple(
        PITDecisionTime(
            fold_index=index,
            decision_time=CAL.session_close(day),
            universe_snapshot_id="snap-1",
            expected_sessions=tuple(
                d for d in DAYS if CAL.session_close(d) <= CAL.session_close(day)
            ),
        )
        for index, day in enumerate(picks)
    )


def test_pit_replay_records_completion_state_per_decision_time():
    instruments, observations, actions, readiness = _universe(3)
    folds = _pit_folds(2)
    members = [str(i["instrument_id"]) for i in instruments]
    replay = recompute_pit_replay(
        folds,
        instruments,
        observations,
        actions,
        readiness_ids=readiness,
        expected_universe_members_by_fold={fold.fold_index: members for fold in folds},
    )
    assert replay.universe_completion_states == ("COMPLETE", "COMPLETE")
    assert replay.is_promotion_grade
    require_pit_promotion_grade(replay)  # must not raise


def test_pit_replay_without_declared_membership_is_not_promotion_grade():
    instruments, observations, actions, readiness = _universe(3)
    replay = recompute_pit_replay(
        _pit_folds(2), instruments, observations, actions, readiness_ids=readiness
    )
    assert replay.universe_completion_states == ("UNKNOWN", "UNKNOWN")
    assert not replay.is_promotion_grade
    with pytest.raises(PromotionGradeError):
        require_pit_promotion_grade(replay)


def test_pit_promotion_grade_per_fold_uses_that_folds_declared_universe():
    """Each fold is graded against the membership declared for THAT decision time."""
    instruments, observations, actions, readiness = _universe(3)
    folds = _pit_folds(2)
    members = [str(i["instrument_id"]) for i in instruments]
    replay = recompute_pit_replay(
        folds,
        instruments[:1],
        observations,
        actions,
        readiness_ids=readiness,
        expected_universe_members_by_fold={
            folds[0].fold_index: members[:1],  # fold 0: universe is one instrument, evaluated
            folds[1].fold_index: members[:1],
        },
    )
    assert replay.universe_completion_states == ("COMPLETE", "COMPLETE")
    assert replay.is_promotion_grade


def test_pit_promotion_grade_fails_closed_when_a_fold_has_missing_declared_members():
    instruments, observations, actions, readiness = _universe(3)
    folds = _pit_folds(2)
    members = [str(i["instrument_id"]) for i in instruments]
    replay = recompute_pit_replay(
        folds,
        instruments[:2],  # the third declared member is never evaluated
        observations,
        actions,
        readiness_ids=readiness,
        expected_universe_members_by_fold={fold.fold_index: members for fold in folds},
    )
    assert replay.universe_completion_states == ("PARTIAL_MISSING_MEMBERS",) * 2
    assert not replay.is_promotion_grade
    with pytest.raises(PromotionGradeError):
        require_pit_promotion_grade(replay)


def test_pit_promotion_grade_fails_closed_when_only_one_fold_is_partial():
    """A single partial decision time makes the whole replay non-promotion-grade."""
    instruments, observations, actions, readiness = _universe(3)
    folds = _pit_folds(2)
    members = [str(i["instrument_id"]) for i in instruments]
    replay = recompute_pit_replay(
        folds,
        instruments,
        observations,
        actions,
        readiness_ids=readiness,
        expected_universe_members_by_fold={
            folds[0].fold_index: members,
            folds[1].fold_index: [*members, "asset-999"],  # fold 1 declares an absent member
        },
    )
    assert replay.universe_completion_states == ("COMPLETE", "PARTIAL_MISSING_MEMBERS")
    assert not replay.is_promotion_grade
    with pytest.raises(PromotionGradeError):
        require_pit_promotion_grade(replay)


def test_pit_promotion_grade_fails_closed_when_a_fold_is_missing_members():
    instruments, observations, actions, readiness = _universe(3)
    folds = _pit_folds(2)
    members = [str(i["instrument_id"]) for i in instruments]
    replay = recompute_pit_replay(
        folds,
        instruments[:2],  # one declared member is never evaluated
        observations,
        actions,
        readiness_ids=readiness,
        expected_universe_members_by_fold={fold.fold_index: members for fold in folds},
    )
    assert replay.universe_completion_states == ("PARTIAL_MISSING_MEMBERS",) * 2
    assert not replay.is_promotion_grade
    with pytest.raises(PromotionGradeError):
        require_pit_promotion_grade(replay)


# ─── BLOCKER C: completeness travels downstream + no version pooling ───────────────────


def test_lineage_with_universe_completion_adds_completeness_state():
    completion = assess_universe_completion("snap-1", ["a", "b"], ["a", "b"])
    lineage = {"instrument_id": "a", "candidate_rank": 1, "funnel_version": "v1"}
    enriched = lineage_with_universe_completion(lineage, completion)
    assert enriched["universe_completion_state"] == "COMPLETE"
    assert enriched["universe_expected_member_count"] == 2
    assert enriched["universe_missing_member_count"] == 0
    # The original record is not mutated.
    assert "universe_completion_state" not in lineage


def test_funnel_candidate_lineage_carries_completion_state():
    instruments, observations, actions, readiness = _universe(2)
    config = StageBConfig()
    run = CandidateFunnel(stage_b_config=config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(config),
        readiness_ids=readiness,
        expected_universe_members=[i["instrument_id"] for i in instruments],
    )
    for record in run.candidate_lineage():
        assert record["universe_completion_state"] == "COMPLETE"
        assert record["funnel_version"]
        assert record["candidate_rank"] is not None


def test_audit_funnel_lineage_flags_missing_required_fields():
    complete = {
        "funnel_version": "v1",
        "stage_a_config_hash": "abc",
        "candidate_rank": 1,
        "selection_reason": "STAGE_A_SELECTED",
        "universe_completion_state": "COMPLETE",
    }
    incomplete = {"funnel_version": "v1", "candidate_rank": 2}
    gaps = audit_funnel_lineage([complete, incomplete])
    assert len(gaps) == 1
    assert gaps[0].record_index == 1
    assert "stage_a_config_hash" in gaps[0].missing_fields
    assert "universe_completion_state" in gaps[0].missing_fields


def test_assert_single_funnel_version_fails_closed_on_mixed_versions():
    assert assert_single_funnel_version(["v1", "v1", "v1"]) == "v1"
    with pytest.raises(FunnelVersionMixError):
        assert_single_funnel_version(["v1", "v2"])
    with pytest.raises(FunnelVersionMixError):
        assert_single_funnel_version([])


def test_stratify_by_funnel_version_is_the_explicit_pooling_path():
    records = [
        {"funnel_version": "v2", "instrument_id": "b"},
        {"funnel_version": "v1", "instrument_id": "a"},
        {"funnel_version": "v1", "instrument_id": "c"},
    ]
    strata = stratify_by_funnel_version(records, lambda r: r["funnel_version"])
    assert tuple(strata) == ("v1", "v2")
    assert [r["instrument_id"] for r in strata["v1"]] == ["a", "c"]
    assert [r["instrument_id"] for r in strata["v2"]] == ["b"]


def test_audit_funnel_pooling_refuses_mixed_versions_and_states():
    same = [
        {
            "funnel_version": "v1",
            "stage_a_config_hash": "h",
            "candidate_rank": 1,
            "selection_reason": "STAGE_A_SELECTED",
            "universe_completion_state": "COMPLETE",
        },
        {
            "funnel_version": "v1",
            "stage_a_config_hash": "h",
            "candidate_rank": 2,
            "selection_reason": "STAGE_A_SELECTED",
            "universe_completion_state": "COMPLETE",
        },
    ]
    audit = audit_funnel_pooling(same)
    assert audit.pooling_permitted
    assert not audit.requires_stratification

    mixed = [same[0], {**same[1], "funnel_version": "v2"}]
    audit = audit_funnel_pooling(mixed)
    assert not audit.pooling_permitted
    assert audit.requires_stratification
    assert audit.funnel_versions == ("v1", "v2")

    mixed_state = [same[0], {**same[1], "universe_completion_state": "PARTIAL_MISSING_MEMBERS"}]
    audit = audit_funnel_pooling(mixed_state)
    assert not audit.pooling_permitted
    assert audit.universe_completion_states == ("COMPLETE", "PARTIAL_MISSING_MEMBERS")


def test_audit_funnel_pooling_refuses_incomplete_lineage():
    audit = audit_funnel_pooling([{"funnel_version": "v1"}])
    assert not audit.pooling_permitted
    assert audit.lineage_gaps


def test_materially_different_funnel_versions_cannot_be_pooled():
    """Two materially different funnel versions are two different economic identities."""
    instruments, observations, actions, readiness = _universe(3)
    members = [str(i["instrument_id"]) for i in instruments]
    run_a = CandidateFunnel(stage_b_config=StageBConfig()).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(StageBConfig()),
        readiness_ids=readiness,
        expected_universe_members=members,
    )
    ranked_config = StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC)
    run_b = CandidateFunnel(stage_b_config=ranked_config).run(
        "snap-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        PassingStageBEvaluator(ranked_config),
        readiness_ids=readiness,
        expected_universe_members=members,
    )
    assert run_a.funnel_version != run_b.funnel_version
    with pytest.raises(FunnelVersionMixError):
        assert_single_funnel_version([run_a.funnel_version, run_b.funnel_version])


# ─── purity / safety ──────────────────────────────────────────────────────────────────


def _module_source() -> str:
    path = Path(__file__).resolve().parents[1] / "src" / "quantlab" / "funnel_completeness.py"
    return path.read_text(encoding="utf-8")


def test_completeness_module_performs_no_io_and_reads_no_clock():
    tree = ast.parse(_module_source())
    banned_calls = {"open", "input", "print", "exec", "eval", "__import__"}
    banned_attrs = {"now", "today", "utcnow"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in banned_calls:
                raise AssertionError(f"banned call: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in banned_attrs:
                raise AssertionError(f"banned clock read: {func.attr}")


def test_completeness_module_has_no_execution_authority():
    source = _module_source().lower()
    for token in (
        "paperbroker",
        "executionengine",
        "orderintent",
        "place_order",
        "submit_order",
        "broker",
        "trading",
    ):
        assert token not in source, f"execution-authority token present: {token}"
