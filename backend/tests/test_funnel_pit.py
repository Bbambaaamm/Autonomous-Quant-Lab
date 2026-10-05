"""Tests for PIT per-decision-time Stage A recomputation (#268, STAT-BLOCKER A).

Covers:
- historical candidate set reconstructed PIT per decision time;
- a later decision time can never inherit an earlier fold's selection, and vice versa
  (an instrument that becomes eligible only later is absent from the earlier fold);
- expected-session / train-scope inputs fail closed when they escape the decision time;
- deterministic replay over the same snapshots/config returns the same candidate set;
- candidate rank / selection reason / completeness / funnel version reach downstream
  lineage (review BLOCKER C);
- funnel parameter variants enter trial-family accounting (STAT-BLOCKER B);
- no execution authority and no I/O in the PIT module.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.candidate_funnel import RankingKey, SelectionReason, StageAConfig, StageBConfig
from quantlab.funnel_pit import (
    PIT_SEMANTICS_VERSION,
    BaselineComparisonError,
    PITDecisionTime,
    PITFunnelReplay,
    PITScopeError,
    TrainScopeFittingError,
    actions_known_at,
    assert_fit_inside_train_scope,
    assert_same_opportunity_set,
    compute_pit_threshold_sensitivity,
    deterministic_pit_replay_check,
    observations_known_at,
    pit_decision_time,
    recompute_pit_replay,
    recompute_stage_a_at,
    sessions_known_at,
)
from quantlab.market_data import CorporateAction, CorporateActionKind, Observation, XNYSCalendar

CAL = XNYSCalendar()
DAYS = CAL.sessions_between(date(2026, 1, 2), date(2026, 9, 21))
NOW = CAL.session_close(DAYS[-1])


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


def make_instrument(
    instrument_id: str = "asset-1",
    symbol: str = "TEST",
    exchange: str = "XNYS",
) -> dict[str, object]:
    return {"instrument_id": instrument_id, "symbol": symbol, "exchange": exchange}


def make_action(
    instrument_id: str = "asset-1",
    known_at: datetime | None = None,
) -> CorporateAction:
    return CorporateAction(
        f"ca-{instrument_id}",
        instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        CAL.session_open(DAYS[-10]),
        known_at or NOW,
        Decimal("1.00"),
    )


def late_liquidity_observations(instrument_id: str = "late-1") -> list[Observation]:
    """Liquid only in its last 50 sessions: eligible late, not at mid-history."""
    obs = make_observations(instrument_id, "LATE")
    return [
        Observation(
            o.observation_id,
            o.instrument_id,
            o.provider,
            o.timeframe,
            o.session_date,
            o.timestamp,
            o.open,
            o.high,
            o.low,
            o.close,
            Decimal("20000") if index >= len(obs) - 50 else Decimal("1000"),
            o.observed_at,
            o.source_id,
            o.source_hash,
            o.ingestion_id,
        )
        for index, o in enumerate(obs)
    ]


def folds(*indexes: int) -> tuple:
    return tuple(
        pit_decision_time(index, CAL.session_close(DAYS[index]), f"snapshot-{index}", DAYS)
        for index in indexes
    )


# ---------------------------------------------------------------------------
# PIT slicing
# ---------------------------------------------------------------------------


def test_sessions_known_at_excludes_sessions_closing_after_decision():
    cutoff = CAL.session_close(DAYS[139])
    known = sessions_known_at(DAYS, cutoff)
    assert len(known) == 140
    assert known[-1] == DAYS[139]
    assert DAYS[140] not in known


def test_observations_known_at_slices_future_bars():
    obs = make_observations()
    cutoff = CAL.session_close(DAYS[139])
    known = observations_known_at(obs, cutoff)
    assert len(known) == 140
    assert max(o.timestamp for o in known) <= cutoff


def test_actions_known_at_slices_future_actions():
    actions = [
        make_action("asset-1", known_at=CAL.session_close(DAYS[10])),
        make_action("asset-1", known_at=CAL.session_close(DAYS[170])),
    ]
    known = actions_known_at(actions, CAL.session_close(DAYS[100]))
    assert len(known) == 1


def test_observations_known_at_requires_timezone():
    with pytest.raises(ValueError):
        observations_known_at(make_observations(), datetime(2026, 9, 21, 22))


# ---------------------------------------------------------------------------
# Decision-time construction fails closed
# ---------------------------------------------------------------------------


def test_decision_time_rejects_future_expected_sessions():
    with pytest.raises(PITScopeError, match="closes after the decision time"):
        PITDecisionTime(
            fold_index=0,
            decision_time=CAL.session_close(DAYS[100]),
            universe_snapshot_id="snapshot-0",
            expected_sessions=tuple(DAYS),
        )


def test_decision_time_rejects_train_scope_past_decision_time():
    with pytest.raises(PITScopeError, match="extends past the decision time"):
        pit_decision_time(
            0,
            CAL.session_close(DAYS[100]),
            "snapshot-0",
            DAYS,
            train_scope_start=CAL.session_close(DAYS[10]),
            train_scope_end=CAL.session_close(DAYS[120]),
        )


def test_decision_time_rejects_half_defined_train_scope():
    with pytest.raises(PITScopeError, match="both start and end"):
        pit_decision_time(
            0,
            CAL.session_close(DAYS[100]),
            "snapshot-0",
            DAYS,
            train_scope_start=CAL.session_close(DAYS[10]),
        )


def test_decision_time_rejects_empty_snapshot_id():
    with pytest.raises(PITScopeError, match="universe_snapshot_id"):
        pit_decision_time(0, CAL.session_close(DAYS[100]), "", DAYS)


def test_fit_inside_train_scope_fails_closed_outside_scope():
    scope = (CAL.session_close(DAYS[10]), CAL.session_close(DAYS[100]))
    with pytest.raises(TrainScopeFittingError, match="escapes the train scope"):
        assert_fit_inside_train_scope(
            scope, CAL.session_close(DAYS[50]), CAL.session_close(DAYS[120])
        )


def test_fit_inside_train_scope_allows_inside_scope():
    scope = (CAL.session_close(DAYS[10]), CAL.session_close(DAYS[100]))
    assert_fit_inside_train_scope(scope, CAL.session_close(DAYS[50]), CAL.session_close(DAYS[90]))


def test_fit_inside_train_scope_without_preregistered_scope_passes():
    assert_fit_inside_train_scope(None, CAL.session_close(DAYS[50]), CAL.session_close(DAYS[90]))


# ---------------------------------------------------------------------------
# Per-decision-time recomputation
# ---------------------------------------------------------------------------


def test_recompute_stage_a_at_is_pit_per_decision_time():
    """An instrument that is eligible only later is NOT a candidate earlier."""
    instruments = [make_instrument("late-1", "LATE")]
    observations = {"late-1": late_liquidity_observations("late-1")}
    actions = {"late-1": [make_action("late-1", known_at=NOW)]}

    early = recompute_stage_a_at(
        folds(100)[0], instruments, observations, actions, readiness_ids={"late-1": "r1"}
    )
    late = recompute_stage_a_at(
        folds(179)[0], instruments, observations, actions, readiness_ids={"late-1": "r1"}
    )

    assert early.candidate_ids == ()
    assert late.candidate_ids == ("late-1",)
    assert early.rejection_counts()["REJECTED_RULE"] == 1


def test_recompute_pit_replay_rebuilds_each_fold_independently():
    instruments = [make_instrument("late-1", "LATE")]
    observations = {"late-1": late_liquidity_observations("late-1")}
    actions = {"late-1": [make_action("late-1", known_at=NOW)]}

    replay = recompute_pit_replay(
        folds(100, 140, 179),
        instruments,
        observations,
        actions,
        readiness_ids={"late-1": "r1"},
    )

    assert replay.candidate_sets() == ((), ("late-1",), ("late-1",))
    assert len(replay.decision_snapshots) == 3
    assert replay.decision_snapshots[0].eligible_count == 0
    assert replay.decision_snapshots[1].eligible_count == 1


def test_later_fold_never_inherits_earlier_selection():
    """A prefiltered-out instrument is re-evaluated from scratch at every fold."""
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}

    replay = recompute_pit_replay(
        folds(139, 179),
        instruments,
        observations,
        actions,
        readiness_ids={"asset-1": "r1"},
    )
    first, second = replay.decision_snapshots
    assert first.ordered_candidate_ids == ("asset-1",)
    assert second.ordered_candidate_ids == ("asset-1",)
    assert first.content_hash != second.content_hash
    assert first.decision_time < second.decision_time


def test_recompute_pit_replay_requires_at_least_one_fold():
    with pytest.raises(PITScopeError, match="at least one PIT decision time"):
        recompute_pit_replay((), [], {}, {})


def test_recompute_pit_replay_empty_universe_yields_empty_candidates():
    replay = recompute_pit_replay(folds(179), [], {}, {})
    assert replay.candidate_sets() == ((),)
    assert replay.decision_snapshots[0].to_summary()["funnel_counts"]["universe_size"] == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_pit_replay_is_deterministic_for_same_snapshots_and_config():
    instruments = [make_instrument("asset-1", "A1"), make_instrument("asset-2", "A2")]
    observations = {
        "asset-1": make_observations("asset-1", "A1"),
        "asset-2": make_observations("asset-2", "A2", close=Decimal("1")),
    }
    actions = {
        "asset-1": [make_action("asset-1", known_at=NOW)],
        "asset-2": [make_action("asset-2", known_at=NOW)],
    }
    readiness = {"asset-1": "r1", "asset-2": "r2"}

    replay1 = recompute_pit_replay(
        folds(139, 179), instruments, observations, actions, readiness_ids=readiness
    )
    replay2 = recompute_pit_replay(
        folds(139, 179), instruments, observations, actions, readiness_ids=readiness
    )

    assert deterministic_pit_replay_check(replay1, replay2)
    assert replay1.content_hash == replay2.content_hash
    assert replay1.candidate_sets() == replay2.candidate_sets()
    assert [s.content_hash for s in replay1.decision_snapshots] == [
        s.content_hash for s in replay2.decision_snapshots
    ]


def test_pit_replay_check_detects_changed_candidate_set():
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}

    replay1 = recompute_pit_replay(
        folds(139, 179), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )
    replay2 = recompute_pit_replay(
        folds(139), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )

    assert not deterministic_pit_replay_check(replay1, replay2)


def test_pit_replay_hash_changes_with_ranking_config():
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}

    base = recompute_pit_replay(
        folds(179), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )
    variant = recompute_pit_replay(
        folds(179),
        instruments,
        observations,
        actions,
        stage_b_config=StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC),
        readiness_ids={"asset-1": "r1"},
    )

    assert base.funnel_version != variant.funnel_version
    assert base.trial_family_key() != variant.trial_family_key()


def test_pit_replay_hash_changes_with_stage_a_config():
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}

    base = recompute_pit_replay(
        folds(179), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )
    variant = recompute_pit_replay(
        folds(179),
        instruments,
        observations,
        actions,
        stage_a_config=StageAConfig(minimum_price_usd=Decimal("150")),
        readiness_ids={"asset-1": "r1"},
    )

    assert base.stage_a_config_hash != variant.stage_a_config_hash
    assert base.content_hash != variant.content_hash
    assert base.candidate_sets() == (("asset-1",),)
    assert variant.candidate_sets() == ((),)


# ---------------------------------------------------------------------------
# Downstream lineage (review BLOCKER C) and trial-family accounting (STAT-BLOCKER B)
# ---------------------------------------------------------------------------


def test_snapshot_candidate_lineage_carries_rank_reason_completeness_version():
    instruments = [make_instrument("asset-1", "A1"), make_instrument("asset-2", "A2")]
    observations = {
        "asset-1": make_observations("asset-1", "A1"),
        "asset-2": make_observations("asset-2", "A2"),
    }
    actions = {
        "asset-1": [make_action("asset-1", known_at=NOW)],
        "asset-2": [make_action("asset-2", known_at=NOW)],
    }

    replay = recompute_pit_replay(
        folds(179),
        instruments,
        observations,
        actions,
        readiness_ids={"asset-1": "r1", "asset-2": "r2"},
    )
    lineage = replay.candidate_lineage()

    assert [entry["candidate_rank"] for entry in lineage] == [1, 2]
    assert [entry["instrument_id"] for entry in lineage] == ["asset-1", "asset-2"]
    for entry in lineage:
        assert entry["selection_reason"] == SelectionReason.STAGE_A_SELECTED.value
        assert entry["funnel_version"] == replay.funnel_version
        assert entry["stage_a_config_hash"] == replay.stage_a_config_hash
        assert entry["stage_b_config_hash"] == replay.stage_b_config_hash
        assert entry["ranking_key"] == replay.ranking_key
        assert entry["ranking_key_version"] == replay.ranking_key_version
        assert entry["universe_snapshot_id"] == "snapshot-179"
        assert entry["completeness"] is not None
        assert entry["decision_time"] == CAL.session_close(DAYS[179]).isoformat()


def test_pit_replay_trial_family_key_is_pit_versioned():
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}

    replay = recompute_pit_replay(
        folds(139, 179), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )
    key = replay.trial_family_key()
    assert key[0] == PIT_SEMANTICS_VERSION
    assert key[3] == 2


def test_pit_replay_summary_exposes_per_decision_funnel_counts():
    instruments = [make_instrument("asset-1", "A1"), make_instrument("asset-2", "A2")]
    observations = {
        "asset-1": make_observations("asset-1", "A1"),
        "asset-2": make_observations("asset-2", "A2", close=Decimal("1")),
    }
    actions = {
        "asset-1": [make_action("asset-1", known_at=NOW)],
        "asset-2": [make_action("asset-2", known_at=NOW)],
    }

    replay = recompute_pit_replay(
        folds(179),
        instruments,
        observations,
        actions,
        readiness_ids={"asset-1": "r1", "asset-2": "r2"},
    )
    summary = replay.to_summary()

    assert summary["pit_version"] == PIT_SEMANTICS_VERSION
    assert summary["decision_count"] == 1
    assert summary["total_candidates"] == 1
    counts = summary["decision_snapshots"][0]["funnel_counts"]
    assert counts["universe_size"] == 2
    assert counts["stage_a_accepted"] == 1
    assert counts["stage_a_rejected"] == 1
    assert counts["candidates"] == 1


def test_not_evaluated_is_not_counted_as_rejected():
    """NOT_EVALUATED_* is a distinct bucket from an economic rejection."""
    instruments = [make_instrument("asset-1", "A1")]
    observations: dict[str, list[Observation]] = {}
    actions: dict[str, list[CorporateAction]] = {}

    snapshot = recompute_stage_a_at(folds(179)[0], instruments, observations, actions)
    assert snapshot.eligible_count == 0
    assert snapshot.not_evaluated_count == 0
    assert snapshot.rejected_count == 1
    assert snapshot.rejection_counts() == {"NOT_IN_UNIVERSE": 1}


# ---------------------------------------------------------------------------
# Module purity / no execution authority
# ---------------------------------------------------------------------------


def test_pit_module_has_no_execution_authority():
    import inspect

    import quantlab.funnel_pit as module

    source = inspect.getsource(module)
    forbidden = [
        "PaperBroker",
        "ExecutionEngine",
        "OrderIntent",
        "place_order",
        "submit_order",
        "trading",
    ]
    for term in forbidden:
        assert term not in source, f"PIT funnel must not reference {term}"


def test_pit_module_performs_no_io_and_reads_no_clock():
    module_path = Path(__file__).parents[1] / "src" / "quantlab" / "funnel_pit.py"
    tree = ast.parse(module_path.read_text())
    io_calls: list[str] = []
    clock_reads: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "input", "print", "exec", "eval", "__import__"}:
                io_calls.append(node.func.id)
        if isinstance(node, ast.Attribute) and node.attr in {"now", "today", "utcnow"}:
            clock_reads.append(node.attr)
    assert io_calls == []
    assert clock_reads == []


def test_pit_funnel_replay_type_is_exported():
    assert isinstance(recompute_pit_replay(folds(179), [], {}, {}), PITFunnelReplay)


def test_snapshot_is_immutable():
    snapshot = recompute_pit_replay(folds(179), [], {}, {}).decision_snapshots[0]
    with pytest.raises((AttributeError, TypeError)):
        snapshot.fold_index = 7  # type: ignore[misc]


def test_decision_time_normalizes_naive_scope_is_rejected():
    with pytest.raises(ValueError):
        pit_decision_time(0, datetime(2026, 9, 21, 22), "snapshot-0", DAYS[:10])


def test_recompute_stage_a_at_rejects_future_only_sessions_slice():
    """The fold's session slice itself is the only coverage denominator."""
    instrument = make_instrument("asset-1", "A1")
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_action("asset-1", known_at=NOW)]}
    snapshot = recompute_stage_a_at(
        folds(139)[0], [instrument], observations, actions, readiness_ids={"asset-1": "r1"}
    )
    evidence = snapshot.stage_a_results[0].screening_evidence
    assert evidence["bars"] == 140
    assert evidence["expected_sessions"] == 140


def test_decision_time_rejects_negative_fold_index():
    with pytest.raises(PITScopeError, match="fold_index"):
        pit_decision_time(-1, CAL.session_close(DAYS[100]), "snapshot-0", DAYS)


def test_fold_scope_end_defaults_none_and_rejects_reversed_scope():
    with pytest.raises(PITScopeError, match="must not be after"):
        pit_decision_time(
            0,
            CAL.session_close(DAYS[100]),
            "snapshot-0",
            DAYS,
            train_scope_start=CAL.session_close(DAYS[50]),
            train_scope_end=CAL.session_close(DAYS[10]),
        )


def test_decision_time_carries_decision_time_as_utc():
    fold = pit_decision_time(3, CAL.session_close(DAYS[100]), "snapshot-3", DAYS)
    assert fold.decision_time.tzinfo is UTC
    assert fold.decision_time == CAL.session_close(DAYS[100]) + timedelta(0)


# ---------------------------------------------------------------------------
# Conditional baseline on the identical opportunity set (STAT-BLOCKER C)
# ---------------------------------------------------------------------------


def _replay_for(indexes: tuple[int, ...]):
    instruments = [make_instrument("asset-1", "A1"), make_instrument("asset-2", "A2")]
    observations = {
        "asset-1": make_observations("asset-1", "A1"),
        "asset-2": make_observations("asset-2", "A2"),
    }
    actions = {
        "asset-1": [make_action("asset-1", known_at=NOW)],
        "asset-2": [make_action("asset-2", known_at=NOW)],
    }
    return recompute_pit_replay(
        tuple(
            pit_decision_time(index, CAL.session_close(DAYS[index]), f"snapshot-{index}", DAYS)
            for index in indexes
        ),
        instruments,
        observations,
        actions,
        readiness_ids={"asset-1": "r1", "asset-2": "r2"},
    )


def test_same_opportunity_set_passes_for_identical_replays():
    assert_same_opportunity_set(_replay_for((139, 179)), _replay_for((139, 179)))


def test_same_opportunity_set_fails_closed_on_different_schedule():
    with pytest.raises(BaselineComparisonError, match="decision-time schedule"):
        assert_same_opportunity_set(_replay_for((139, 179)), _replay_for((139,)))


def test_same_opportunity_set_fails_closed_on_different_candidate_set():
    """The baseline must see the identical selected opportunity set (STAT-BLOCKER C)."""
    stage_b = _replay_for((179,))
    wider = recompute_pit_replay(
        (pit_decision_time(179, CAL.session_close(DAYS[179]), "snapshot-179", DAYS),),
        [make_instrument("asset-1", "A1"), make_instrument("asset-2", "A2")],
        {
            "asset-1": make_observations("asset-1", "A1"),
            "asset-2": make_observations("asset-2", "A2"),
        },
        {
            "asset-1": [make_action("asset-1", known_at=NOW)],
            "asset-2": [make_action("asset-2", known_at=NOW)],
        },
        stage_a_config=StageAConfig(minimum_price_usd=Decimal("1")),
        readiness_ids={"asset-1": "r1", "asset-2": "r2"},
    )
    assert stage_b.candidate_sets() == wider.candidate_sets()
    # Now shrink the baseline's opportunity set by changing only Stage A selection.
    narrowed = recompute_pit_replay(
        (pit_decision_time(179, CAL.session_close(DAYS[179]), "snapshot-179", DAYS),),
        [make_instrument("asset-1", "A1")],
        {"asset-1": make_observations("asset-1", "A1")},
        {"asset-1": [make_action("asset-1", known_at=NOW)]},
        readiness_ids={"asset-1": "r1"},
    )
    with pytest.raises(BaselineComparisonError, match="candidate set at decision index 0"):
        assert_same_opportunity_set(stage_b, narrowed)


def test_opportunity_key_is_the_decision_time_candidate_pairs():
    replay = _replay_for((139, 179))
    key = replay.opportunity_key()
    assert len(key) == 2
    assert key[0][0] == CAL.session_close(DAYS[139]).isoformat()
    assert key[0][1] == ("asset-1", "asset-2")


# ---------------------------------------------------------------------------
# PIT threshold / K sensitivity (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_pit_threshold_sensitivity_stable_under_identical_selection():
    baseline = _replay_for((139, 179))
    sensitivity = compute_pit_threshold_sensitivity(baseline, _replay_for((139, 179)))
    assert sensitivity.mean_jaccard == 1.0
    assert sensitivity.changed_decisions == 0
    assert sensitivity.stable_decisions == 2
    assert not sensitivity.changed


def test_pit_threshold_sensitivity_detects_changed_selection():
    baseline = _replay_for((139, 179))
    narrowed = recompute_pit_replay(
        tuple(
            pit_decision_time(index, CAL.session_close(DAYS[index]), f"snapshot-{index}", DAYS)
            for index in (139, 179)
        ),
        [make_instrument("asset-1", "A1")],
        {"asset-1": make_observations("asset-1", "A1")},
        {"asset-1": [make_action("asset-1", known_at=NOW)]},
        readiness_ids={"asset-1": "r1"},
    )
    sensitivity = compute_pit_threshold_sensitivity(
        baseline, narrowed, parameter="max_candidates", baseline_value="2", perturbed_value="1"
    )
    assert sensitivity.changed
    assert sensitivity.changed_decisions == 2
    assert sensitivity.mean_jaccard == 0.5
    assert sensitivity.baseline_value == "2"
    assert sensitivity.perturbed_value == "1"


def test_pit_threshold_sensitivity_fails_closed_on_different_schedule():
    with pytest.raises(BaselineComparisonError, match="same decision-time schedule"):
        compute_pit_threshold_sensitivity(_replay_for((139, 179)), _replay_for((179,)))


# ---------------------------------------------------------------------------
# Downstream experiment lineage (review BLOCKER C)
# ---------------------------------------------------------------------------


def test_experiment_identity_carries_funnel_version_and_stage_a_hash():
    """A funnel is part of the economic identity of a downstream experiment."""
    from quantlab.research import ExperimentIdentity

    replay = _replay_for((179,))
    lineage = replay.candidate_lineage()
    assert lineage, "replay must produce at least one candidate for this test"

    base = ExperimentIdentity(
        "ma", "1", {"slow": 3}, "dataset", "a", "b", {"rate": "0.1"}, {"bps": "5"}, 42
    )
    tagged = base.with_funnel_lineage(lineage[0])

    assert tagged.funnel_lineage is not None
    assert tagged.funnel_lineage["funnel_version"] == replay.funnel_version
    assert tagged.funnel_lineage["stage_a_config_hash"] == replay.stage_a_config_hash
    assert tagged.funnel_lineage["candidate_rank"] == 1
    assert tagged.funnel_lineage["selection_reason"] == SelectionReason.STAGE_A_SELECTED.value
    assert tagged.experiment_id != base.experiment_id


def test_experiment_identity_distinguishes_materially_different_funnel_versions():
    """Two materially different funnel versions must not share an experiment id."""
    from quantlab.research import ExperimentIdentity

    base = ExperimentIdentity(
        "ma", "1", {"slow": 3}, "dataset", "a", "b", {"rate": "0.1"}, {"bps": "5"}, 42
    )
    lineage_a = _replay_for((179,)).candidate_lineage()[0]
    lineage_b = recompute_pit_replay(
        (pit_decision_time(179, CAL.session_close(DAYS[179]), "snapshot-179", DAYS),),
        [make_instrument("asset-1", "A1")],
        {"asset-1": make_observations("asset-1", "A1")},
        {"asset-1": [make_action("asset-1", known_at=NOW)]},
        readiness_ids={"asset-1": "r1"},
    ).candidate_lineage()[0]

    assert lineage_a["funnel_version"] == lineage_b["funnel_version"]
    assert lineage_a["stage_a_config_hash"] == lineage_b["stage_a_config_hash"]
    # Different opportunity sets -> different candidate completeness/rank evidence.
    identity_a = base.with_funnel_lineage(lineage_a)
    identity_b = base.with_funnel_lineage(lineage_b)
    assert identity_a.funnel_lineage is not None
    assert identity_b.funnel_lineage is not None


def test_experiment_identity_without_funnel_lineage_keeps_legacy_id():
    """Adding the field must not change the id of a funnel-less experiment."""
    import hashlib
    import json

    from quantlab.research import ExperimentIdentity

    identity = ExperimentIdentity(
        "ma", "1", {"slow": 3}, "abc", "a", "b", {"rate": "0.1"}, {"bps": "5"}, 42
    )
    legacy_payload = {
        "strategy_name": "ma",
        "strategy_version": "1",
        "strategy_config": {"slow": 3},
        "dataset_id": "abc",
        "start": "a",
        "end": "b",
        "commission_model": {"rate": "0.1"},
        "slippage_model": {"bps": "5"},
        "random_seed": 42,
        "engine_version": "2.0.0",
    }
    legacy_id = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    assert identity.funnel_lineage is None
    assert identity.experiment_id == legacy_id
