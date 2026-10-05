"""Tests for the scenario-graph replay / reproducibility verifier (issue #271).

Covers the last open acceptance criterion:

    deterministic/reproducible replay v rozsahu, který použitý model umožňuje,
    nebo explicitní stochastic-run evidence

and the BLOCKER D requirement that a run fingerprint make model / prompt / graph /
config drift *distinguishable*.

The tests deliberately attack the boundary rather than the happy path:

- a replay that cannot execute must fail closed with NO replayed hash (never a
  fabricated agreement);
- a non-deterministic inference must not receive a deterministic verdict and a single
  stochastic execution is not evidence;
- replay repeats are simulation noise, never real-world sample size;
- replay can never upgrade an UNCONTROLLED (temporally leaking) run to promotion grade;
- a substituted engine still runs behind the shared concurrency guard.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.scenario_graph import (
    CONTRACT_VERSION,
    MAX_EVENTS_PER_RUN,
    MAX_SIMULATION_REPLICATES,
    GraphBoundsExceeded,
    InferenceFingerprint,
    ModelKnowledgeCutoff,
    ScenarioAgent,
    ScenarioDirection,
    ScenarioEvent,
    ScenarioGraphConfig,
    ScenarioGraphEngine,
    ScenarioOutput,
    scenario_output_from_dict,
    scenario_output_to_dict,
)
from quantlab.scenario_graph_isolation import (
    ScenarioConcurrencyGuard,
    ScenarioDegradationPolicy,
    ScenarioRunStatus,
    ScenarioWorker,
)
from quantlab.scenario_graph_replay import (
    DETERMINISTIC_SAMPLING_POLICY,
    MAX_REPLAY_EVENTS,
    MAX_STOCHASTIC_REPEATS,
    MIN_STOCHASTIC_REPEATS,
    ReplayBundle,
    ReplayError,
    ReplayResult,
    ReplayVerdict,
    ScenarioReplayVerifier,
    StochasticRunEvidence,
    replay_result_to_dict,
    scenario_output_content_hash,
)

_AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "quantlab" / "scenario_graph_replay.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    index: int,
    event_type: str = "EARNINGS_BEAT",
    entities: tuple[str, ...] = ("AAPL", "TECH"),
) -> ScenarioEvent:
    return ScenarioEvent(
        event_id=f"evt-{index:03d}",
        event_type=event_type,
        published_at=_AS_OF - timedelta(hours=3),
        first_seen_at=_AS_OF - timedelta(hours=2),
        source="test-source",
        source_event_id=f"src-{index:03d}",
        entities=entities,
        content_hash=f"hash-{index:03d}",
    )


def _events() -> tuple[ScenarioEvent, ...]:
    return (
        _event(1, "EARNINGS_BEAT", ("AAPL", "TECH")),
        _event(2, "MARKET_CRASH", ("SPY", "MACRO")),
        _event(3, "REGULATORY_PROBE", ("BANK", "FINANCIALS")),
    )


def _agents() -> tuple[ScenarioAgent, ...]:
    return (
        ScenarioAgent(
            agent_id="agent-1",
            entity_id="AAPL",
            stance=ScenarioDirection.BULLISH,
            confidence=Decimal("0.7"),
        ),
        ScenarioAgent(
            agent_id="agent-2",
            entity_id="SPY",
            stance=ScenarioDirection.BEARISH,
            confidence=Decimal("0.6"),
        ),
    )


def _controlled_knowledge() -> ModelKnowledgeCutoff:
    return ModelKnowledgeCutoff(
        model_id="test-model",
        provider="test-provider",
        cutoff_at=_AS_OF - timedelta(days=1),
        source="provider-documentation",
        evidence_ref="evid-cutoff-1",
    )


def _deterministic_inference() -> InferenceFingerprint:
    """Deterministic *and* fully reproducible (pinned revision + seed)."""
    return replace(
        InferenceFingerprint.rule_based("2026-10-04"),
        model_id="test-model",
        provider="test-provider",
        model_revision="rev-1",
        seed=7,
    )


def _stochastic_inference() -> InferenceFingerprint:
    return replace(
        InferenceFingerprint.rule_based("2026-10-04"),
        model_id="test-model",
        provider="test-provider",
        model_revision="rev-1",
        seed=3,
        temperature=Decimal("0.7"),
        sampling_policy="seeded-stochastic",
    )


def _run(
    *,
    events: tuple[ScenarioEvent, ...] | None = None,
    config: ScenarioGraphConfig | None = None,
    knowledge: ModelKnowledgeCutoff | None = None,
    inference: InferenceFingerprint | None = None,
    agents: tuple[ScenarioAgent, ...] = (),
    as_of: datetime = _AS_OF,
) -> ScenarioOutput:
    return ScenarioGraphEngine(config=config).run(
        events if events is not None else _events(),
        as_of=as_of,
        event_snapshot_id="snap-001",
        knowledge=knowledge,
        inference=inference,
        agents=agents,
    )


def _bundle(
    *,
    output: ScenarioOutput | None = None,
    events: tuple[ScenarioEvent, ...] | None = None,
    config: ScenarioGraphConfig | None = None,
    knowledge: ModelKnowledgeCutoff | None = None,
    inference: InferenceFingerprint | None = None,
    agents: tuple[ScenarioAgent, ...] = (),
    stochastic_repeats: int = 1,
) -> ReplayBundle:
    resolved_events = events if events is not None else _events()
    resolved_config = config or ScenarioGraphConfig()
    if output is None:
        output = _run(
            events=resolved_events,
            config=resolved_config,
            knowledge=knowledge,
            inference=inference,
            agents=agents,
        )
    return ReplayBundle(
        recorded_output=output,
        events=resolved_events,
        as_of=_AS_OF,
        event_snapshot_id="snap-001",
        config=resolved_config,
        knowledge=knowledge,
        inference=inference,
        agents=agents,
        stochastic_repeats=stochastic_repeats,
    )


class _FailingEngine(ScenarioGraphEngine):
    """Engine stub that always raises, to exercise the fail-closed boundary."""

    def run(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise RuntimeError("scenario engine exploded")


class _OverBudgetEngine(ScenarioGraphEngine):
    """Engine stub that reports an over-budget runtime."""

    def run(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise GraphBoundsExceeded("runtime 99999ms exceeds cap 5000ms")


def _drifted_engine_factory(
    drift: Callable[[ScenarioOutput], ScenarioOutput],
) -> Callable[[ScenarioGraphConfig], ScenarioGraphEngine]:
    """Deterministic engine whose *output* differs in exactly one component.

    Used to prove that a content difference is detected and attributed. It cannot widen
    the isolation boundary: the substitute still runs behind the worker's guard, runtime
    cap and degradation policy.
    """

    class _Drifted(ScenarioGraphEngine):
        def run(self, *args: object, **kwargs: object) -> ScenarioOutput:  # type: ignore[override]
            return drift(super().run(*args, **kwargs))  # type: ignore[arg-type]

    return _Drifted


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    """The deterministic path reproduces the recorded content exactly."""

    def test_replay_reproduces_recorded_content(self) -> None:
        bundle = _bundle()
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MATCH
        assert result.content_reproduced is True
        assert result.replayed_content_hash == result.recorded_content_hash
        assert result.drift_components == ()
        assert result.error is None
        assert result.deterministic_inference is True

    def test_replay_is_repeatable(self) -> None:
        bundle = _bundle()
        verifier = ScenarioReplayVerifier()
        first = verifier.replay(bundle)
        second = verifier.replay(bundle)
        assert first.verdict is second.verdict
        assert first.replayed_content_hash == second.replayed_content_hash
        assert first.bundle_id == second.bundle_id

    def test_content_hash_ignores_run_id_and_runtime(self) -> None:
        """Two independent runs of the same inputs share a content hash.

        ``scenario_run_id`` is a uuid4 and ``runtime_ms`` is wall-clock; a replay must
        not be able to claim agreement (or disagreement) on either.
        """
        first = _run()
        second = _run()
        assert first.scenario_run_id != second.scenario_run_id
        assert scenario_output_content_hash(first) == scenario_output_content_hash(second)

    def test_content_hash_changes_with_inputs(self) -> None:
        baseline = scenario_output_content_hash(_run())
        changed = scenario_output_content_hash(
            _run(events=(_event(1, "MARKET_CRASH", ("SPY", "MACRO")),))
        )
        assert baseline != changed

    def test_recorded_output_round_trips_through_canonical_serialization(self) -> None:
        """The replay bundle can be rebuilt from stored evidence."""
        output = _run(knowledge=_controlled_knowledge(), inference=_deterministic_inference())
        restored = scenario_output_from_dict(scenario_output_to_dict(output))
        assert scenario_output_content_hash(restored) == scenario_output_content_hash(output)

    def test_replay_of_controlled_run_is_promotion_grade(self) -> None:
        bundle = _bundle(
            knowledge=_controlled_knowledge(),
            inference=_deterministic_inference(),
            agents=_agents(),
        )
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.recorded_promotion_grade is True
        assert result.fully_reproducible is True
        assert result.promotion_grade_replay is True

    def test_replay_never_upgrades_an_uncontrolled_run(self) -> None:
        """A temporally leaking run stays non-promotion-grade no matter how cleanly it
        replays (issue #271 BLOCKER A)."""
        bundle = _bundle()  # no knowledge cutoff -> UNCONTROLLED
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.content_reproduced is True
        assert result.recorded_promotion_grade is False
        assert result.promotion_grade_replay is False

    def test_replay_does_not_upgrade_a_run_without_pinned_revision(self) -> None:
        inference = replace(
            InferenceFingerprint.rule_based("2026-10-04"), model_revision=None, seed=None
        )
        bundle = _bundle(knowledge=_controlled_knowledge(), inference=inference)
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.content_reproduced is True
        assert result.fully_reproducible is False
        assert result.promotion_grade_replay is False


# ---------------------------------------------------------------------------
# Fail-closed: no fabricated agreement
# ---------------------------------------------------------------------------


class TestFailClosed:
    """A replay that cannot execute never reports a replayed hash."""

    def test_engine_failure_is_not_replayable(self) -> None:
        bundle = _bundle()
        result = ScenarioReplayVerifier().replay(
            bundle, engine_factory=lambda config: _FailingEngine(config)
        )
        assert result.verdict is ReplayVerdict.NOT_REPLAYABLE
        assert result.replayed_content_hash is None
        assert result.content_reproduced is False
        assert result.error is not None and "RuntimeError" in result.error
        assert result.drift_components == ()

    def test_over_budget_run_is_not_replayable(self) -> None:
        bundle = _bundle()
        result = ScenarioReplayVerifier().replay(
            bundle, engine_factory=lambda config: _OverBudgetEngine(config)
        )
        assert result.verdict is ReplayVerdict.NOT_REPLAYABLE
        assert result.replayed_content_hash is None
        assert result.error is not None and ScenarioRunStatus.TIMED_OUT.value in result.error

    def test_concurrency_rejection_is_not_replayable(self) -> None:
        """Replay shares the live worker's admission slot (MAX_CONCURRENCY = 1)."""
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        verifier = ScenarioReplayVerifier.for_worker(worker)
        with worker.guard.admitted():
            result = verifier.replay(_bundle())
        assert result.verdict is ReplayVerdict.NOT_REPLAYABLE
        assert result.replayed_content_hash is None
        assert result.error is not None
        assert ScenarioRunStatus.CONCURRENCY_REJECTED.value in result.error

    def test_look_ahead_event_is_not_replayable(self) -> None:
        """A PIT violation in the bundle fails closed rather than replaying."""
        events = _events()
        future = replace(events[0], first_seen_at=_AS_OF + timedelta(hours=1))
        bundle = replace(_bundle(), events=(future, *events[1:]))
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.NOT_REPLAYABLE
        assert result.replayed_content_hash is None
        assert result.error is not None and "PitViolationError" in result.error

    def test_not_replayable_result_cannot_carry_a_hash(self) -> None:
        with pytest.raises(ReplayError, match="must not carry a replayed content hash"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.NOT_REPLAYABLE,
                recorded_content_hash="x",
                replayed_content_hash="x",
                deterministic_inference=True,
                fully_reproducible=True,
                recorded_promotion_grade=True,
                drift_components=(),
                error="boom",
                repeats=1,
                stochastic_evidence=None,
                duration_ms=0,
            )

    def test_not_replayable_result_requires_an_error(self) -> None:
        with pytest.raises(ReplayError, match="must carry an error"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.NOT_REPLAYABLE,
                recorded_content_hash="x",
                replayed_content_hash=None,
                deterministic_inference=True,
                fully_reproducible=True,
                recorded_promotion_grade=True,
                drift_components=(),
                error=None,
                repeats=1,
                stochastic_evidence=None,
                duration_ms=0,
            )

    def test_match_verdict_requires_identical_hashes(self) -> None:
        with pytest.raises(ReplayError, match="requires identical content hashes"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.DETERMINISTIC_MATCH,
                recorded_content_hash="a",
                replayed_content_hash="b",
                deterministic_inference=True,
                fully_reproducible=True,
                recorded_promotion_grade=True,
                drift_components=(),
                error=None,
                repeats=1,
                stochastic_evidence=None,
                duration_ms=0,
            )

    def test_deterministic_verdict_requires_deterministic_inference(self) -> None:
        with pytest.raises(ReplayError, match="requires a deterministic inference"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.DETERMINISTIC_MATCH,
                recorded_content_hash="a",
                replayed_content_hash="a",
                deterministic_inference=False,
                fully_reproducible=True,
                recorded_promotion_grade=True,
                drift_components=(),
                error=None,
                repeats=1,
                stochastic_evidence=None,
                duration_ms=0,
            )


# ---------------------------------------------------------------------------
# Drift is distinguishable (BLOCKER D)
# ---------------------------------------------------------------------------


class TestFingerprintDrift:
    """Every fingerprint component that can drift is named by the replay."""

    @pytest.mark.parametrize(
        ("component", "drifted"),
        [
            ("prompt_hash", "drifted-prompt"),
            ("system_instructions_hash", "drifted-system"),
            ("model_id", "other-model"),
            ("provider", "other-provider"),
            ("model_revision", "rev-2"),
            ("temperature", Decimal("0.5")),
            ("seed", 99),
            ("sampling_policy", "seeded-stochastic"),
            ("graph_builder_version", "graph-builder-v9"),
            ("graph_content_hash", "deadbeef"),
            ("graph_config_hash", "cafebabe"),
            ("agent_config_hash", "0badf00d"),
            ("runtime_version", "2099-01-01"),
        ],
    )
    def test_recorded_fingerprint_drift_is_named(self, component: str, drifted: object) -> None:
        inference = _deterministic_inference()
        bundle = _bundle(knowledge=_controlled_knowledge(), inference=inference)
        drifted_output = replace(
            bundle.recorded_output,
            fingerprint=replace(bundle.recorded_output.fingerprint, **{component: drifted}),
        )
        result = ScenarioReplayVerifier().replay(replace(bundle, recorded_output=drifted_output))
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH
        assert component in result.drift_components
        assert result.replayed_content_hash is not None
        assert result.replayed_content_hash != result.recorded_content_hash

    def test_source_snapshot_drift_is_named(self) -> None:
        bundle = _bundle()
        drifted_output = replace(
            bundle.recorded_output,
            fingerprint=replace(
                bundle.recorded_output.fingerprint,
                source_snapshot_hashes=("hash-999",),
            ),
        )
        result = ScenarioReplayVerifier().replay(replace(bundle, recorded_output=drifted_output))
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH
        assert "source_snapshot_hashes" in result.drift_components

    def test_agent_configuration_drift_is_named(self) -> None:
        """A changed agent population is drift, not a silent difference."""
        recorded = _run(agents=_agents())
        bundle = _bundle(output=recorded, agents=())  # replay with no agents
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH
        assert "agent_config_hash" in result.drift_components

    def test_graph_config_drift_is_named(self) -> None:
        recorded = _run()
        drifted_config = ScenarioGraphConfig(propagation_decay=Decimal("0.5"))
        bundle = _bundle(output=recorded, config=drifted_config)
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH
        assert "graph_config_hash" in result.drift_components

    def test_output_drift_without_fingerprint_drift_is_still_detected(self) -> None:
        """A changed output whose fingerprint components are identical is still a
        mismatch — replay never claims agreement it did not observe."""
        bundle = _bundle()
        drifted = _drifted_engine_factory(lambda output: replace(output, assumptions=("tampered",)))
        result = ScenarioReplayVerifier().replay(bundle, engine_factory=drifted)
        assert result.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH
        assert result.drift_components == ()
        assert result.error is not None and "identical" in result.error


class TestBundleIdentity:
    """The bundle identity is content-derived and sensitive to every input."""

    def test_bundle_id_is_stable(self) -> None:
        assert _bundle().bundle_id == _bundle().bundle_id

    def test_bundle_id_changes_with_events(self) -> None:
        assert _bundle().bundle_id != _bundle(events=(_event(1),)).bundle_id

    def test_bundle_id_changes_with_config(self) -> None:
        assert _bundle().bundle_id != _bundle(config=ScenarioGraphConfig(max_nodes=10)).bundle_id

    def test_bundle_id_changes_with_knowledge_declaration(self) -> None:
        assert _bundle().bundle_id != _bundle(knowledge=_controlled_knowledge()).bundle_id

    def test_bundle_id_changes_with_inference(self) -> None:
        assert _bundle().bundle_id != _bundle(inference=_deterministic_inference()).bundle_id

    def test_bundle_id_changes_with_repeat_count(self) -> None:
        assert _bundle().bundle_id != _bundle(stochastic_repeats=2).bundle_id


# ---------------------------------------------------------------------------
# Stochastic-run evidence
# ---------------------------------------------------------------------------


class TestStochasticEvidence:
    """A non-deterministic inference must carry explicit repeat evidence."""

    def test_single_stochastic_execution_is_not_evidence(self) -> None:
        bundle = _bundle(
            knowledge=_controlled_knowledge(),
            inference=_stochastic_inference(),
            stochastic_repeats=1,
        )
        assert bundle.deterministic_inference is False
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.NOT_REPLAYABLE
        assert result.replayed_content_hash is None
        assert result.stochastic_evidence is None
        assert result.error is not None and "stochastic" in result.error

    def test_repeated_stochastic_run_records_evidence(self) -> None:
        bundle = _bundle(
            knowledge=_controlled_knowledge(),
            inference=_stochastic_inference(),
            stochastic_repeats=3,
        )
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.verdict is ReplayVerdict.STOCHASTIC_EVIDENCE_RECORDED
        assert result.explicit_stochastic_evidence is True
        evidence = result.stochastic_evidence
        assert evidence is not None
        assert evidence.repeats == 3
        assert len(evidence.content_hashes) == 3
        assert evidence.distinct_content_hashes == 1
        assert evidence.stable_under_repeat is True
        assert evidence.sampling_policy == "seeded-stochastic"
        assert evidence.seed == 3
        assert result.deterministic_inference is False

    def test_repeats_are_not_real_world_opportunities(self) -> None:
        """Issue #271 STAT-BLOCKER B: repeat count is simulation noise, not sample size."""
        bundle = _bundle(inference=_stochastic_inference(), stochastic_repeats=5)
        result = ScenarioReplayVerifier().replay(bundle)
        assert result.stochastic_evidence is not None
        assert result.stochastic_evidence.contributes_real_world_opportunities is False
        payload = replay_result_to_dict(result)
        assert payload["stochastic_evidence"]["contributes_real_world_opportunities"] is False

    def test_seeded_stochastic_inference_is_not_deterministic(self) -> None:
        """A seed alone does not make a stochastic sampler deterministic."""
        bundle = _bundle(inference=replace(_stochastic_inference(), seed=12345))
        assert bundle.deterministic_inference is False

    def test_zero_temperature_is_deterministic(self) -> None:
        inference = replace(
            InferenceFingerprint.rule_based("2026-10-04"),
            temperature=Decimal("0"),
            sampling_policy=DETERMINISTIC_SAMPLING_POLICY,
        )
        assert _bundle(inference=inference).deterministic_inference is True

    def test_stochastic_evidence_needs_at_least_two_repeats(self) -> None:
        with pytest.raises(ReplayError, match="at least"):
            StochasticRunEvidence(
                repeats=1,
                distinct_content_hashes=1,
                content_hashes=("h",),
                stable_under_repeat=True,
                sampling_policy="seeded-stochastic",
                seed=1,
            )

    def test_stochastic_evidence_rejects_mismatched_hash_count(self) -> None:
        with pytest.raises(ReplayError, match="one hash per repeat"):
            StochasticRunEvidence(
                repeats=3,
                distinct_content_hashes=2,
                content_hashes=("a", "b"),
                stable_under_repeat=False,
                sampling_policy="seeded-stochastic",
                seed=1,
            )

    def test_stochastic_evidence_rejects_inconsistent_stability(self) -> None:
        with pytest.raises(ReplayError, match="stable_under_repeat"):
            StochasticRunEvidence(
                repeats=2,
                distinct_content_hashes=1,
                content_hashes=("a", "a"),
                stable_under_repeat=False,
                sampling_policy="seeded-stochastic",
                seed=1,
            )

    def test_stochastic_evidence_only_on_a_stochastic_verdict(self) -> None:
        with pytest.raises(ReplayError, match="only be attached to a stochastic verdict"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.DETERMINISTIC_MATCH,
                recorded_content_hash="a",
                replayed_content_hash="a",
                deterministic_inference=True,
                fully_reproducible=True,
                recorded_promotion_grade=True,
                drift_components=(),
                error=None,
                repeats=1,
                stochastic_evidence=StochasticRunEvidence(
                    repeats=2,
                    distinct_content_hashes=1,
                    content_hashes=("a", "a"),
                    stable_under_repeat=True,
                    sampling_policy="seeded-stochastic",
                    seed=1,
                ),
                duration_ms=0,
            )

    def test_stochastic_verdict_requires_evidence(self) -> None:
        with pytest.raises(ReplayError, match="requires explicit stochastic-run evidence"):
            ReplayResult(
                bundle_id="b",
                verdict=ReplayVerdict.STOCHASTIC_EVIDENCE_RECORDED,
                recorded_content_hash="a",
                replayed_content_hash="a",
                deterministic_inference=False,
                fully_reproducible=False,
                recorded_promotion_grade=False,
                drift_components=(),
                error=None,
                repeats=2,
                stochastic_evidence=None,
                duration_ms=0,
            )

    def test_repeat_cap_is_enforced(self) -> None:
        with pytest.raises(ReplayError, match="exceeds cap"):
            _bundle(
                inference=_stochastic_inference(),
                stochastic_repeats=MAX_STOCHASTIC_REPEATS + 1,
            )

    def test_repeat_floor_is_positive(self) -> None:
        with pytest.raises(ReplayError, match="must be positive"):
            _bundle(stochastic_repeats=0)

    def test_min_repeats_is_two(self) -> None:
        assert MIN_STOCHASTIC_REPEATS == 2


# ---------------------------------------------------------------------------
# Bundle bounds and validation
# ---------------------------------------------------------------------------


class TestBundleValidation:
    """A bundle is bounded and validated before any execution."""

    def test_event_cap_is_enforced(self) -> None:
        events = tuple(_event(i) for i in range(MAX_REPLAY_EVENTS + 1))
        with pytest.raises(ReplayError, match="exceeds replay cap"):
            ReplayBundle(
                recorded_output=_run(events=(_event(1),)),
                events=events,
                as_of=_AS_OF,
                event_snapshot_id="snap-001",
            )

    def test_event_cap_matches_engine_cap(self) -> None:
        assert MAX_REPLAY_EVENTS == MAX_EVENTS_PER_RUN

    def test_empty_events_rejected(self) -> None:
        with pytest.raises(ReplayError, match="at least one event"):
            ReplayBundle(
                recorded_output=_run(),
                events=(),
                as_of=_AS_OF,
                event_snapshot_id="snap-001",
            )

    def test_naive_as_of_rejected(self) -> None:
        with pytest.raises(ReplayError, match="timezone-aware"):
            ReplayBundle(
                recorded_output=_run(),
                events=_events(),
                as_of=datetime(2026, 6, 1, 12, 0),
                event_snapshot_id="snap-001",
            )

    def test_missing_snapshot_id_rejected(self) -> None:
        with pytest.raises(ReplayError, match="event_snapshot_id"):
            ReplayBundle(
                recorded_output=_run(),
                events=_events(),
                as_of=_AS_OF,
                event_snapshot_id="",
            )

    def test_contract_version_mismatch_rejected(self) -> None:
        with pytest.raises(ReplayError, match="contract_version"):
            ReplayBundle(
                recorded_output=_run(),
                events=_events(),
                as_of=_AS_OF,
                event_snapshot_id="snap-001",
                contract_version="scenario-graph-v1",
            )

    def test_current_contract_version_is_v2(self) -> None:
        assert CONTRACT_VERSION == "scenario-graph-v2"

    def test_simulation_replicate_cap_is_enforced(self) -> None:
        with pytest.raises(ReplayError, match="exceeds cap"):
            ReplayBundle(
                recorded_output=_run(),
                events=_events(),
                as_of=_AS_OF,
                event_snapshot_id="snap-001",
                simulation_replicates=MAX_SIMULATION_REPLICATES + 1,
            )

    def test_zero_simulation_replicates_rejected(self) -> None:
        with pytest.raises(ReplayError, match="must be positive"):
            ReplayBundle(
                recorded_output=_run(),
                events=_events(),
                as_of=_AS_OF,
                event_snapshot_id="snap-001",
                simulation_replicates=0,
            )


# ---------------------------------------------------------------------------
# Isolation reuse and shadow-only guarantees
# ---------------------------------------------------------------------------


class TestIsolationBoundaryReuse:
    """Replay reuses the existing operational boundary; it does not widen it."""

    def test_for_worker_shares_the_guard_and_policy(self) -> None:
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        verifier = ScenarioReplayVerifier.for_worker(worker)
        assert verifier.guard is worker.guard
        assert verifier.policy is worker.policy

    def test_default_verifier_guard_uses_the_hard_cap(self) -> None:
        verifier = ScenarioReplayVerifier()
        assert verifier.guard.max_concurrency == 1

    def test_guard_cannot_be_raised_above_the_hard_cap(self) -> None:
        with pytest.raises(Exception, match="exceeds hard cap"):
            ScenarioConcurrencyGuard(max_concurrency=2)

    def test_verifier_defaults_to_core_independent_policy(self) -> None:
        verifier = ScenarioReplayVerifier()
        assert verifier.policy.halts_core_workflow_on_failure is False
        assert verifier.policy == ScenarioDegradationPolicy.core_independent()

    def test_replay_result_has_no_order_surface(self) -> None:
        result = ScenarioReplayVerifier().replay(_bundle())
        payload = replay_result_to_dict(result)
        for forbidden in (
            "order",
            "side",
            "quantity",
            "broker",
            "execution",
            "action_id",
            "trade",
            "risk",
        ):
            assert forbidden not in payload

    def test_verifier_exposes_no_execution_method(self) -> None:
        verifier = ScenarioReplayVerifier()
        for name in dir(verifier):
            lowered = name.lower()
            for forbidden in ("order", "execute", "broker", "submit", "trade", "risk"):
                assert forbidden not in lowered, f"verifier exposes {name}"

    def test_module_never_imports_network_or_subprocess(self) -> None:
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        forbidden = {
            "socket",
            "subprocess",
            "requests",
            "httpx",
            "urllib",
            "shutil",
            "os",
            "http",
        }
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden)

    def test_module_has_no_io_calls(self) -> None:
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        io_names = {"open", "read", "write", "input", "print", "exec", "eval", "compile"}
        offenders = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in io_names
        ]
        assert offenders == []

    def test_module_does_not_reference_model_or_agent_sdk(self) -> None:
        source = _MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("openai", "anthropic", "langchain", "mirofish"):
            assert forbidden not in source.lower()


def test_replay_result_to_dict_round_trips_the_verdict() -> None:
    bundle = _bundle()
    payload = replay_result_to_dict(ScenarioReplayVerifier().replay(bundle))
    assert payload["verdict"] == ReplayVerdict.DETERMINISTIC_MATCH.value
    assert payload["content_reproduced"] is True
    assert payload["explicit_stochastic_evidence"] is False
    assert payload["recorded_content_hash"] == payload["replayed_content_hash"]
