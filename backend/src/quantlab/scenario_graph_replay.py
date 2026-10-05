"""Scenario-graph replay and reproducibility verification — shadow research only.

Issue #271 acceptance criterion (the last one still unimplemented):

    deterministic/reproducible replay v rozsahu, který použitý model umožňuje,
    nebo explicitní stochastic-run evidence

and BLOCKER D (reproducibility fingerprint), which requires that a run fingerprint
make model / prompt / graph / config drift *distinguishable* — not merely recorded.

This module is the operational verification boundary around the pure engine in
:mod:`quantlab.scenario_graph`. It answers exactly one question:

    given the exact recorded inputs of a scenario run, does re-executing the engine
    reproduce the recorded output — and if the declared inference is not
    deterministic, is there explicit stochastic-run evidence instead?

Safety properties
-----------------
- **No execution authority.** The module never emits an order, never touches a broker
  and never mutates risk limits. Its only output is a typed :class:`ReplayResult`.
- **Replay never invents a match.** A verdict of ``DETERMINISTIC_MATCH`` is only
  possible when the replayed content hash equals the recorded one. An engine error,
  an over-budget run or a concurrency rejection fails closed into ``NOT_REPLAYABLE``
  with the error text and **no** replayed hash — never a fabricated agreement.
- **Determinism is a property of the inference, not of the caller's hope.** A bundle
  whose declared inference is not deterministic (``temperature != 0`` or a non
  ``deterministic`` sampling policy) cannot receive a deterministic verdict; it must
  carry explicit stochastic-run evidence (>= :data:`MIN_STOCHASTIC_REPEATS` repeats),
  otherwise it is ``NOT_REPLAYABLE``.
- **Bounded.** The number of events is capped by :data:`MAX_REPLAY_EVENTS` and the
  number of stochastic repeats by :data:`MAX_STOCHASTIC_REPEATS`; every replay
  execution goes through :class:`~quantlab.scenario_graph_isolation.ScenarioWorker`,
  so the runtime cap, the process concurrency guard and the preregistered degradation
  policy apply unchanged. Use :meth:`ScenarioReplayVerifier.for_worker` in production
  wiring so the live worker's guard is shared and ``MAX_CONCURRENCY`` stays
  process-wide rather than per verifier.
- **No I/O, no network, no model calls.** AST-asserted by the test suite.

Statistical note (issue #271 STAT-BLOCKER B): the repeat count in
:class:`StochasticRunEvidence` is a *simulation* repeat count. It reduces internal
noise and is explicitly **not** an out-of-sample sample size. Nothing in this module
produces, counts or implies independent real-world forecast opportunities, and no
verdict here is a promotion recommendation.

Replay cannot make an UNCONTROLLED run promotion-grade: the recorded run's own
``is_promotion_grade`` gate (temporal knowledge cutoff + full reproducibility +
schema validation) is carried on the result and combined with the replay verdict in
:attr:`ReplayResult.promotion_grade_replay`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantlab.scenario_graph import (
    CONTRACT_VERSION,
    MAX_EVENTS_PER_RUN,
    MAX_SIMULATION_REPLICATES,
    InferenceFingerprint,
    ModelKnowledgeCutoff,
    ScenarioAgent,
    ScenarioEvent,
    ScenarioGraphConfig,
    ScenarioGraphEngine,
    ScenarioOutput,
    scenario_output_to_dict,
)
from quantlab.scenario_graph_isolation import (
    ScenarioConcurrencyGuard,
    ScenarioDegradationPolicy,
    ScenarioRunStatus,
    ScenarioWorker,
)

__all__ = [
    "MAX_REPLAY_EVENTS",
    "MIN_STOCHASTIC_REPEATS",
    "MAX_STOCHASTIC_REPEATS",
    "DETERMINISTIC_SAMPLING_POLICY",
    "ReplayError",
    "ReplayVerdict",
    "StochasticRunEvidence",
    "ReplayBundle",
    "ReplayResult",
    "ScenarioReplayVerifier",
    "scenario_output_content_hash",
    "replay_result_to_dict",
]

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

#: Maximum number of events a replay bundle may carry. Mirrors the engine's own
#: ``max_events_per_run`` cap so a replay can never be a wider workload than the run
#: it verifies.
MAX_REPLAY_EVENTS = MAX_EVENTS_PER_RUN

#: A non-deterministic inference needs at least this many repeats before the run may
#: be reported as carrying explicit stochastic-run evidence. A single stochastic
#: execution is not evidence of anything.
MIN_STOCHASTIC_REPEATS = 2

#: Hard ceiling on stochastic repeats per replay. Mirrors the engine's
#: ``MAX_SIMULATION_REPLICATES`` so replay stays inside the same resource envelope.
MAX_STOCHASTIC_REPEATS = MAX_SIMULATION_REPLICATES

#: The only sampling policy under which a run may be called deterministic.
DETERMINISTIC_SAMPLING_POLICY = "deterministic"

#: Output fields that are wall-clock or run-identity dependent and therefore excluded
#: from the replay-comparable content hash. Everything else in the canonical contract
#: is a pure function of the bundle inputs.
_NON_DETERMINISTIC_OUTPUT_FIELDS = ("scenario_run_id", "runtime_ms")


class ReplayError(RuntimeError):
    """Raised when a replay bundle or result violates the replay contract."""


class ReplayVerdict(StrEnum):
    """Terminal verdict of one replay verification.

    There is deliberately no ``PROMOTE`` member: a replay verdict is evidence about
    reproducibility, never a promotion recommendation.
    """

    #: Deterministic inference; the replayed content hash equals the recorded one.
    DETERMINISTIC_MATCH = "DETERMINISTIC_MATCH"
    #: Deterministic inference; the replayed content hash differs (drift is named).
    DETERMINISTIC_MISMATCH = "DETERMINISTIC_MISMATCH"
    #: Non-deterministic inference; explicit repeat evidence was recorded instead.
    STOCHASTIC_EVIDENCE_RECORDED = "STOCHASTIC_EVIDENCE_RECORDED"
    #: The engine did not reproduce, or the bundle cannot support any verdict.
    NOT_REPLAYABLE = "NOT_REPLAYABLE"


# ---------------------------------------------------------------------------
# Deterministic content identity
# ---------------------------------------------------------------------------


def scenario_output_content_hash(output: ScenarioOutput) -> str:
    """Deterministic content hash of a scenario output.

    The hash covers the whole canonical contract *except* the two fields that are not
    functions of the inputs: ``scenario_run_id`` (a ``uuid4`` run identifier) and the
    wall-clock ``runtime_ms`` / ``cost.compute_ms`` pair. Two runs of the same engine
    on the same inputs therefore share a content hash, which is exactly what a replay
    compares.

    A replayed run is never allowed to claim agreement on a hash that includes a
    run identifier or a duration.
    """
    payload = scenario_output_to_dict(output)
    for name in _NON_DETERMINISTIC_OUTPUT_FIELDS:
        payload.pop(name, None)
    cost = payload.get("cost")
    if isinstance(cost, dict):
        cost.pop("compute_ms", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _config_payload(config: ScenarioGraphConfig) -> dict[str, Any]:
    """Canonical, complete serialization of the engine configuration.

    Every configurable field is included, so a bundle is sensitive to *any* policy
    change — not only to the caps that happen to appear in the engine's config hash.
    """
    return {
        "max_nodes": config.max_nodes,
        "max_edges": config.max_edges,
        "max_agents": config.max_agents,
        "max_scenarios": config.max_scenarios,
        "max_runtime_ms": config.max_runtime_ms,
        "max_events_per_run": config.max_events_per_run,
        "max_propagation_depth": config.max_propagation_depth,
        "max_simulation_replicates": config.max_simulation_replicates,
        "max_model_budget_tokens": config.max_model_budget_tokens,
        "max_model_calls": config.max_model_calls,
        "default_edge_weight": str(config.default_edge_weight),
        "propagation_decay": str(config.propagation_decay),
        "confidence_floor": str(config.confidence_floor),
        "probability_floor": str(config.probability_floor),
        "probability_cap": str(config.probability_cap),
    }


def _knowledge_payload(knowledge: ModelKnowledgeCutoff | None) -> dict[str, Any] | None:
    if knowledge is None:
        return None
    return {
        "model_id": knowledge.model_id,
        "provider": knowledge.provider,
        "cutoff_at": None if knowledge.cutoff_at is None else knowledge.cutoff_at.isoformat(),
        "source": knowledge.source,
        "evidence_ref": knowledge.evidence_ref,
    }


def _inference_payload(inference: InferenceFingerprint | None) -> dict[str, Any] | None:
    if inference is None:
        return None
    return {
        "model_id": inference.model_id,
        "provider": inference.provider,
        "model_revision": inference.model_revision,
        "prompt_hash": inference.prompt_hash,
        "system_instructions_hash": inference.system_instructions_hash,
        "temperature": str(inference.temperature),
        "top_p": None if inference.top_p is None else str(inference.top_p),
        "seed": inference.seed,
        "sampling_policy": inference.sampling_policy,
        "runtime_version": inference.runtime_version,
        "raw_output_hashes": list(inference.raw_output_hashes),
    }


def _event_payload(event: ScenarioEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "published_at": event.published_at.isoformat(),
        "first_seen_at": event.first_seen_at.isoformat(),
        "source": event.source,
        "source_event_id": event.source_event_id,
        "entities": list(event.entities),
        "content_hash": event.content_hash,
    }


def _agent_payload(agent: ScenarioAgent) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "entity_id": agent.entity_id,
        "stance": agent.stance.value,
        "confidence": str(agent.confidence),
    }


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayBundle:
    """The immutable inputs of a recorded scenario run plus its recorded output.

    A bundle is the *only* thing a replay needs: the engine configuration, the PIT-safe
    event set, the declared model knowledge cutoff, the inference identity and the
    recorded canonical output. It carries no credentials, no broker reference and no
    execution surface.

    ``recorded_output`` is the canonical :class:`ScenarioOutput` produced by the
    original run. It can be reconstructed from stored evidence with
    :func:`quantlab.scenario_graph.scenario_output_from_dict`.
    """

    recorded_output: ScenarioOutput
    events: tuple[ScenarioEvent, ...]
    as_of: datetime
    event_snapshot_id: str
    config: ScenarioGraphConfig = field(default_factory=ScenarioGraphConfig)
    graph_version: str = "graph-v2"
    knowledge: ModelKnowledgeCutoff | None = None
    inference: InferenceFingerprint | None = None
    agents: tuple[ScenarioAgent, ...] = ()
    candidate_outcomes: tuple[str, ...] = ()
    simulation_replicates: int = 1
    stochastic_repeats: int = 1
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ReplayError(
                f"bundle contract_version {self.contract_version!r} is not the current "
                f"contract {CONTRACT_VERSION!r}; a run recorded under a different contract "
                "cannot be replayed as if it were equivalent"
            )
        if self.as_of.tzinfo is None:
            raise ReplayError("as_of must be timezone-aware")
        if not self.event_snapshot_id:
            raise ReplayError("event_snapshot_id is required")
        if not self.events:
            raise ReplayError("a replay bundle must carry at least one event")
        if len(self.events) > MAX_REPLAY_EVENTS:
            raise ReplayError(
                f"event count {len(self.events)} exceeds replay cap {MAX_REPLAY_EVENTS}"
            )
        if self.simulation_replicates <= 0:
            raise ReplayError("simulation_replicates must be positive")
        if self.simulation_replicates > MAX_SIMULATION_REPLICATES:
            raise ReplayError(
                f"simulation_replicates {self.simulation_replicates} exceeds cap "
                f"{MAX_SIMULATION_REPLICATES}"
            )
        if self.stochastic_repeats <= 0:
            raise ReplayError("stochastic_repeats must be positive")
        if self.stochastic_repeats > MAX_STOCHASTIC_REPEATS:
            raise ReplayError(
                f"stochastic_repeats {self.stochastic_repeats} exceeds cap {MAX_STOCHASTIC_REPEATS}"
            )

    @property
    def recorded_content_hash(self) -> str:
        """Replay-comparable content hash of the recorded output."""
        return scenario_output_content_hash(self.recorded_output)

    @property
    def deterministic_inference(self) -> bool:
        """Whether the declared inference may be called deterministic.

        The built-in rule-based engine (``inference is None``) is deterministic. An
        explicitly declared inference is deterministic only at zero temperature with a
        ``deterministic`` sampling policy — a seed alone is not sufficient, because a
        seeded stochastic sampler is still a stochastic sampler.
        """
        inference = self.inference
        if inference is None:
            return True
        return (
            inference.temperature == Decimal("0")
            and inference.sampling_policy == DETERMINISTIC_SAMPLING_POLICY
        )

    @property
    def bundle_id(self) -> str:
        """Content hash of the full bundle (inputs + recorded content hash)."""
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "as_of": self.as_of.isoformat(),
            "event_snapshot_id": self.event_snapshot_id,
            "graph_version": self.graph_version,
            "config": _config_payload(self.config),
            "knowledge": _knowledge_payload(self.knowledge),
            "inference": _inference_payload(self.inference),
            "events": [_event_payload(event) for event in self.events],
            "agents": [_agent_payload(agent) for agent in self.agents],
            "candidate_outcomes": list(self.candidate_outcomes),
            "simulation_replicates": self.simulation_replicates,
            "stochastic_repeats": self.stochastic_repeats,
            "recorded_content_hash": self.recorded_content_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class StochasticRunEvidence:
    """Explicit stochastic-run evidence for a non-deterministic inference.

    Recorded instead of a deterministic verdict when the declared inference cannot be
    replayed byte-identically. ``repeats`` is a *simulation* repeat count: it is not an
    out-of-sample sample size and must never be reported as one (issue #271
    STAT-BLOCKER B).
    """

    repeats: int
    distinct_content_hashes: int
    content_hashes: tuple[str, ...]
    stable_under_repeat: bool
    sampling_policy: str
    seed: int | None

    def __post_init__(self) -> None:
        if self.repeats < MIN_STOCHASTIC_REPEATS:
            raise ReplayError(
                f"stochastic-run evidence needs at least {MIN_STOCHASTIC_REPEATS} repeats"
            )
        if self.repeats > MAX_STOCHASTIC_REPEATS:
            raise ReplayError(f"repeats {self.repeats} exceeds cap {MAX_STOCHASTIC_REPEATS}")
        if len(self.content_hashes) != self.repeats:
            raise ReplayError("content_hashes must carry one hash per repeat")
        if self.distinct_content_hashes != len(set(self.content_hashes)):
            raise ReplayError("distinct_content_hashes must match the recorded hashes")
        if self.stable_under_repeat is not (self.distinct_content_hashes == 1):
            raise ReplayError("stable_under_repeat must agree with the distinct hash count")

    @property
    def contributes_real_world_opportunities(self) -> bool:
        """Always ``False``: repeats are simulation noise reduction, not observations."""
        return False


@dataclass(frozen=True)
class ReplayResult:
    """Typed result of one replay verification.

    ``replayed_content_hash`` is present if and only if the engine actually
    re-executed; a ``NOT_REPLAYABLE`` result therefore cannot be mistaken for a
    verified reproduction.
    """

    bundle_id: str
    verdict: ReplayVerdict
    recorded_content_hash: str
    replayed_content_hash: str | None
    deterministic_inference: bool
    fully_reproducible: bool
    recorded_promotion_grade: bool
    drift_components: tuple[str, ...]
    error: str | None
    repeats: int
    stochastic_evidence: StochasticRunEvidence | None
    duration_ms: int

    def __post_init__(self) -> None:
        if self.repeats <= 0:
            raise ReplayError("repeats must be positive")
        if self.verdict is ReplayVerdict.NOT_REPLAYABLE:
            if self.replayed_content_hash is not None:
                raise ReplayError("a NOT_REPLAYABLE result must not carry a replayed content hash")
            if not self.error:
                raise ReplayError("a NOT_REPLAYABLE result must carry an error")
        elif self.replayed_content_hash is None:
            raise ReplayError("a replayed result must carry a replayed content hash")

        if self.verdict is ReplayVerdict.DETERMINISTIC_MATCH:
            if self.replayed_content_hash != self.recorded_content_hash:
                raise ReplayError("DETERMINISTIC_MATCH requires identical content hashes")
            if not self.deterministic_inference:
                raise ReplayError("a deterministic verdict requires a deterministic inference")
        if self.verdict is ReplayVerdict.DETERMINISTIC_MISMATCH:
            if self.replayed_content_hash == self.recorded_content_hash:
                raise ReplayError("DETERMINISTIC_MISMATCH requires different content hashes")
            if not self.deterministic_inference:
                raise ReplayError("a deterministic verdict requires a deterministic inference")

        if self.verdict is ReplayVerdict.STOCHASTIC_EVIDENCE_RECORDED:
            if self.stochastic_evidence is None:
                raise ReplayError("a stochastic verdict requires explicit stochastic-run evidence")
            if self.deterministic_inference:
                raise ReplayError("a stochastic verdict requires a non-deterministic inference")
        elif self.stochastic_evidence is not None:
            raise ReplayError(
                "stochastic-run evidence may only be attached to a stochastic verdict"
            )

    @property
    def content_reproduced(self) -> bool:
        """Whether the recorded output content was reproduced exactly."""
        return self.verdict is ReplayVerdict.DETERMINISTIC_MATCH

    @property
    def explicit_stochastic_evidence(self) -> bool:
        """Whether this result carries explicit stochastic-run evidence."""
        return (
            self.verdict is ReplayVerdict.STOCHASTIC_EVIDENCE_RECORDED
            and self.stochastic_evidence is not None
        )

    @property
    def promotion_grade_replay(self) -> bool:
        """Whether this replay may be offered as promotion-grade reproducibility
        evidence: the content must be reproduced *and* the run must itself be
        promotion-grade (controlled knowledge cutoff, pinned model revision and seed,
        schema-validated output). A replay never upgrades an uncontrolled run.
        """
        return self.content_reproduced and self.fully_reproducible and self.recorded_promotion_grade


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclass
class ScenarioReplayVerifier:
    """Re-executes recorded scenario runs through the isolation boundary.

    The verifier holds no engine of its own: each replay builds an engine from the
    configuration carried by the bundle, so a bundle is replayed under exactly the
    policy it was recorded with (a widened or narrowed config surfaces as drift, never
    as a silent difference).

    ``guard`` and ``policy`` are shared with the live worker when the verifier is built
    with :meth:`for_worker`. Production wiring must use :meth:`for_worker`: the
    in-process ``MAX_CONCURRENCY`` cap is per guard, so an independently constructed
    verifier would admit one additional concurrent scenario run.
    """

    guard: ScenarioConcurrencyGuard = field(default_factory=ScenarioConcurrencyGuard)
    policy: ScenarioDegradationPolicy = field(
        default_factory=ScenarioDegradationPolicy.core_independent
    )

    @classmethod
    def for_worker(cls, worker: ScenarioWorker) -> ScenarioReplayVerifier:
        """Share the live worker's concurrency guard and degradation policy.

        This keeps the process-wide ``MAX_CONCURRENCY`` cap intact: a replay occupies
        the same admission slot as a live scenario run.
        """
        return cls(guard=worker.guard, policy=worker.policy)

    def replay(
        self,
        bundle: ReplayBundle,
        *,
        engine_factory: Callable[[ScenarioGraphConfig], ScenarioGraphEngine] | None = None,
    ) -> ReplayResult:
        """Re-execute the bundle and compare against the recorded content.

        Returns a typed result; never raises an engine failure at the caller. An engine
        error, an over-budget run or a concurrency rejection fails closed into
        ``NOT_REPLAYABLE`` with no replayed hash.

        ``engine_factory`` exists so a verification harness can substitute a
        deterministic stub engine (e.g. to prove that a content difference is detected
        and named as drift). It is not a production extension point and it cannot widen
        the isolation boundary: the substitute engine still runs behind the worker's
        concurrency guard, runtime cap and degradation policy, and every result is still
        derived from a real re-execution.
        """
        start = time.monotonic()
        recorded_hash = bundle.recorded_content_hash
        recorded_promotion_grade = bundle.recorded_output.is_promotion_grade
        recorded_fully_reproducible = bundle.recorded_output.fingerprint.fully_reproducible
        deterministic = bundle.deterministic_inference

        if not deterministic and bundle.stochastic_repeats < MIN_STOCHASTIC_REPEATS:
            return self._not_replayable(
                bundle,
                recorded_hash,
                error=(
                    "non-deterministic inference requires explicit stochastic-run evidence "
                    f"(>= {MIN_STOCHASTIC_REPEATS} repeats); a single stochastic execution "
                    "is not evidence of reproducibility"
                ),
                recorded_fully_reproducible=recorded_fully_reproducible,
                recorded_promotion_grade=recorded_promotion_grade,
                start=start,
            )

        repeats = 1 if deterministic else bundle.stochastic_repeats
        factory = engine_factory or ScenarioGraphEngine
        engine = factory(bundle.config)
        worker = ScenarioWorker(engine=engine, guard=self.guard, policy=self.policy)

        hashes: list[str] = []
        first_output: ScenarioOutput | None = None
        for _ in range(repeats):
            outcome = worker.run(
                bundle.events,
                as_of=bundle.as_of,
                event_snapshot_id=bundle.event_snapshot_id,
                graph_version=bundle.graph_version,
                knowledge=bundle.knowledge,
                inference=bundle.inference,
                agents=bundle.agents,
                candidate_outcomes=bundle.candidate_outcomes,
                simulation_replicates=bundle.simulation_replicates,
            )
            if outcome.status is not ScenarioRunStatus.SUCCEEDED or outcome.output is None:
                return self._not_replayable(
                    bundle,
                    recorded_hash,
                    error=(
                        f"scenario engine did not reproduce the run: {outcome.status.value}: "
                        f"{outcome.error}"
                    ),
                    recorded_fully_reproducible=recorded_fully_reproducible,
                    recorded_promotion_grade=recorded_promotion_grade,
                    start=start,
                )
            if first_output is None:
                first_output = outcome.output
            hashes.append(scenario_output_content_hash(outcome.output))

        assert first_output is not None  # noqa: S101 - repeats >= 1 guarantees an output
        replayed_hash = hashes[0]
        fully_reproducible = (
            recorded_fully_reproducible and first_output.fingerprint.fully_reproducible
        )
        duration_ms = self._elapsed_ms(start)

        if not deterministic:
            evidence = StochasticRunEvidence(
                repeats=repeats,
                distinct_content_hashes=len(set(hashes)),
                content_hashes=tuple(hashes),
                stable_under_repeat=len(set(hashes)) == 1,
                sampling_policy=(
                    first_output.fingerprint.sampling_policy
                    if bundle.inference is not None
                    else DETERMINISTIC_SAMPLING_POLICY
                ),
                seed=first_output.fingerprint.seed,
            )
            return ReplayResult(
                bundle_id=bundle.bundle_id,
                verdict=ReplayVerdict.STOCHASTIC_EVIDENCE_RECORDED,
                recorded_content_hash=recorded_hash,
                replayed_content_hash=replayed_hash,
                deterministic_inference=False,
                fully_reproducible=fully_reproducible,
                recorded_promotion_grade=recorded_promotion_grade,
                drift_components=(),
                error=None,
                repeats=repeats,
                stochastic_evidence=evidence,
                duration_ms=duration_ms,
            )

        if replayed_hash == recorded_hash:
            return ReplayResult(
                bundle_id=bundle.bundle_id,
                verdict=ReplayVerdict.DETERMINISTIC_MATCH,
                recorded_content_hash=recorded_hash,
                replayed_content_hash=replayed_hash,
                deterministic_inference=True,
                fully_reproducible=fully_reproducible,
                recorded_promotion_grade=recorded_promotion_grade,
                drift_components=(),
                error=None,
                repeats=1,
                stochastic_evidence=None,
                duration_ms=duration_ms,
            )

        drift = bundle.recorded_output.fingerprint.drift_components(first_output.fingerprint)
        if drift:
            mismatch_error = (
                "replayed content differs from the recorded content; fingerprint drift "
                f"components: {list(drift)}"
            )
        else:
            mismatch_error = (
                "replayed content differs from the recorded content and every run "
                "fingerprint component is identical (graph/event/output drift)"
            )
        return ReplayResult(
            bundle_id=bundle.bundle_id,
            verdict=ReplayVerdict.DETERMINISTIC_MISMATCH,
            recorded_content_hash=recorded_hash,
            replayed_content_hash=replayed_hash,
            deterministic_inference=True,
            fully_reproducible=fully_reproducible,
            recorded_promotion_grade=recorded_promotion_grade,
            drift_components=drift,
            error=mismatch_error,
            repeats=1,
            stochastic_evidence=None,
            duration_ms=duration_ms,
        )

    def _not_replayable(
        self,
        bundle: ReplayBundle,
        recorded_hash: str,
        *,
        error: str,
        recorded_fully_reproducible: bool,
        recorded_promotion_grade: bool,
        start: float,
    ) -> ReplayResult:
        return ReplayResult(
            bundle_id=bundle.bundle_id,
            verdict=ReplayVerdict.NOT_REPLAYABLE,
            recorded_content_hash=recorded_hash,
            replayed_content_hash=None,
            deterministic_inference=bundle.deterministic_inference,
            fully_reproducible=recorded_fully_reproducible,
            recorded_promotion_grade=recorded_promotion_grade,
            drift_components=(),
            error=error,
            repeats=1,
            stochastic_evidence=None,
            duration_ms=self._elapsed_ms(start),
        )

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)


def replay_result_to_dict(result: ReplayResult) -> dict[str, Any]:
    """Serialize a replay result for evidence storage.

    Contains no order/broker/execution surface and no output body — only the verdict,
    the content hashes and the bounded stochastic evidence.
    """
    evidence = result.stochastic_evidence
    return {
        "bundle_id": result.bundle_id,
        "verdict": result.verdict.value,
        "recorded_content_hash": result.recorded_content_hash,
        "replayed_content_hash": result.replayed_content_hash,
        "deterministic_inference": result.deterministic_inference,
        "fully_reproducible": result.fully_reproducible,
        "recorded_promotion_grade": result.recorded_promotion_grade,
        "drift_components": list(result.drift_components),
        "error": result.error,
        "repeats": result.repeats,
        "duration_ms": result.duration_ms,
        "content_reproduced": result.content_reproduced,
        "explicit_stochastic_evidence": result.explicit_stochastic_evidence,
        "promotion_grade_replay": result.promotion_grade_replay,
        "stochastic_evidence": (
            None
            if evidence is None
            else {
                "repeats": evidence.repeats,
                "distinct_content_hashes": evidence.distinct_content_hashes,
                "content_hashes": list(evidence.content_hashes),
                "stable_under_repeat": evidence.stable_under_repeat,
                "sampling_policy": evidence.sampling_policy,
                "seed": evidence.seed,
                "contributes_real_world_opportunities": (
                    evidence.contributes_real_world_opportunities
                ),
            }
        ),
    }
