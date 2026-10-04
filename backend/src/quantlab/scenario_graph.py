"""Scenario Graph Shadow Engine — deterministic, bounded, shadow-only.

Implements a minimal MiroFish-style knowledge graph + scenario propagation engine
for issue #271. The engine is a pure function: PIT-safe events + config + declared
model knowledge → structured scenario output. No I/O, no network, no LLM calls, no
execution authority.

Design principles
-----------------
- Deterministic: same inputs → same outputs (no randomness in the core path).
- Bounded: hard caps on graph size, edges, agents, scenarios, runtime, model budget.
- Shadow-only: the output is a research feature / forecast modifier, never an order.
- PIT-safe: every node, edge and event carries ``known_at`` / ``valid_at`` semantics
  and point-in-time provenance from issue #75.
- Untrusted content is DATA, never instructions: content text never changes tool
  policy, simulation scope or permissions.
- Typed evidence: agent consensus is a ``scenario_score`` / ``agent_vote_fraction`` /
  ``model_confidence``, never a calibrated probability until #269 calibration exists.
- Auditable: every run carries a reproducibility fingerprint and full lineage.

Contract version: v2 (supersedes the v1 contract of the first #271 slice). The v2
change is motivated by the independent architecture review (BLOCKER A/B/C/D) and the
statistical review (STAT-BLOCKER A/B/C/D/E/F) recorded on issue #271:

- ``Scenario.probability`` (v1) → ``Scenario.score`` typed as ``ScenarioScore``, whose
  kind can only be ``CALIBRATED_PROBABILITY`` when an explicit #269 calibrator is
  supplied. Uncalibrated consensus is ``SCENARIO_SCORE`` / ``AGENT_VOTE_FRACTION`` /
  ``MODEL_CONFIDENCE``.
- ``RelationshipEdge`` / ``EntityNode`` (v1) now require ``provenance`` and ``known_at``.
- ``ScenarioOutput`` (v1) now carries ``temporal_knowledge_status``, ``fingerprint``,
  ``discovery`` and ``weighting``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

__all__ = [
    "MAX_NODES",
    "MAX_EDGES",
    "MAX_AGENTS",
    "MAX_SCENARIOS",
    "MAX_RUNTIME_MS",
    "MAX_EVENTS_PER_RUN",
    "MAX_PROPAGATION_DEPTH",
    "MAX_EVIDENCE_REFS",
    "MAX_SIMULATION_REPLICATES",
    "MAX_MODEL_BUDGET_TOKENS",
    "MAX_MODEL_CALLS",
    "TEMPORAL_KNOWLEDGE_UNCONTROLLED_FLAG",
    "CONTRACT_VERSION",
    "EntityType",
    "RelationshipType",
    "ScenarioDirection",
    "ProvenanceKind",
    "TemporalKnowledgeStatus",
    "ScenarioScoreKind",
    "ScenarioEvent",
    "UntrustedContent",
    "EntityNode",
    "RelationshipEdge",
    "ScenarioAgent",
    "ScenarioScore",
    "Scenario",
    "ScenarioDiscovery",
    "ScenarioWeighting",
    "ScenarioCost",
    "ScenarioLineage",
    "ModelKnowledgeCutoff",
    "InferenceFingerprint",
    "RunFingerprint",
    "ScenarioWorkerPolicy",
    "ScenarioOutput",
    "ScenarioGraphConfig",
    "ScenarioGraphEngine",
    "ScenarioGraphError",
    "GraphBoundsExceeded",
    "InvalidEventError",
    "PitViolationError",
    "SchemaValidationError",
    "scenario_output_to_dict",
    "scenario_output_from_dict",
    "validate_scenario_output_schema",
]

CONTRACT_VERSION = "scenario-graph-v2"

# ---------------------------------------------------------------------------
# Hard caps (resource bounds per #190)
# ---------------------------------------------------------------------------

MAX_NODES = 500
MAX_EDGES = 2000
MAX_AGENTS = 50
MAX_SCENARIOS = 10
MAX_RUNTIME_MS = 5000
MAX_EVENTS_PER_RUN = 1000
MAX_PROPAGATION_DEPTH = 5
MAX_EVIDENCE_REFS = 100
MAX_SIMULATION_REPLICATES = 20
MAX_MODEL_BUDGET_TOKENS = 0
MAX_MODEL_CALLS = 0

#: Required label for a historical run whose model knowledge cutoff cannot be shown
#: to be at or before the simulated decision time (issue #271 BLOCKER A).
TEMPORAL_KNOWLEDGE_UNCONTROLLED_FLAG = "MODEL_TEMPORAL_KNOWLEDGE_UNCONTROLLED"


class ScenarioGraphError(RuntimeError):
    """Base exception for scenario graph engine."""


class GraphBoundsExceeded(ScenarioGraphError):
    """Raised when a hard cap would be violated."""


class InvalidEventError(ScenarioGraphError):
    """Raised when an event fails schema validation."""


class PitViolationError(ScenarioGraphError):
    """Raised when a node, edge or event is known only after the decision time."""


class SchemaValidationError(ScenarioGraphError):
    """Raised when structured output fails schema validation (fail closed)."""


# ---------------------------------------------------------------------------
# Canonical scenario contract (Phase 2)
# ---------------------------------------------------------------------------


class EntityType(StrEnum):
    ASSET = "ASSET"
    SECTOR = "SECTOR"
    MACRO_INDICATOR = "MACRO_INDICATOR"
    EVENT = "EVENT"
    AGENT = "AGENT"


class RelationshipType(StrEnum):
    BELONGS_TO = "BELONGS_TO"
    CORRELATES_WITH = "CORRELATES_WITH"
    CAUSES = "CAUSES"
    AFFECTS = "AFFECTS"
    DEPENDS_ON = "DEPENDS_ON"


class ScenarioDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class ProvenanceKind(StrEnum):
    """Issue #271 BLOCKER B: observed source facts and model-inferred edges are
    separate types and must never be conflated in a historical world state."""

    OBSERVED_SOURCE_FACT = "OBSERVED_SOURCE_FACT"
    MODEL_INFERRED = "MODEL_INFERRED"


class TemporalKnowledgeStatus(StrEnum):
    """Issue #271 BLOCKER A."""

    CONTROLLED = "CONTROLLED"
    UNCONTROLLED = "UNCONTROLLED"


class ScenarioScoreKind(StrEnum):
    """Issue #271 STAT-BLOCKER A: only CALIBRATED_PROBABILITY is a probability.

    The other kinds are scores. They must never be reported, compared or stored as
    ``P(outcome)`` before an empirical mapping has been calibrated through #269.
    """

    SCENARIO_SCORE = "SCENARIO_SCORE"
    AGENT_VOTE_FRACTION = "AGENT_VOTE_FRACTION"
    MODEL_CONFIDENCE = "MODEL_CONFIDENCE"
    CALIBRATED_PROBABILITY = "CALIBRATED_PROBABILITY"


@dataclass(frozen=True)
class ScenarioEvent:
    """PIT-safe canonical event input (from issue #75).

    ``published_at`` is when the source published the fact; ``first_seen_at`` is when
    QuantLab first could have known it. The engine treats ``first_seen_at`` as the
    event's ``known_at`` and never lets a later knowledge time leak into a run.
    """

    event_id: str
    event_type: str
    published_at: datetime
    first_seen_at: datetime
    source: str
    source_event_id: str
    entities: tuple[str, ...]
    content_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise InvalidEventError("event_id is required")
        if not self.entities:
            raise InvalidEventError("at least one entity is required")
        if not self.content_hash:
            raise InvalidEventError("content_hash is required")
        if self.published_at.tzinfo is None or self.first_seen_at.tzinfo is None:
            raise InvalidEventError("published_at and first_seen_at must be timezone-aware")
        if self.published_at > self.first_seen_at:
            raise InvalidEventError("published_at cannot be after first_seen_at (PIT violation)")

    @property
    def known_at(self) -> datetime:
        """Point-in-time knowledge time of this event."""
        return self.first_seen_at


@dataclass(frozen=True)
class UntrustedContent:
    """Raw third-party content (news, PDF, web page) — DATA ONLY.

    Issue #271 BLOCKER C. The engine never interprets ``text`` as instructions. The
    text is carried only so a caller can hash/quote it; it can never change tool
    policy, simulation scope, permissions or the worker's capability set.
    """

    content_id: str
    source_uri: str
    content_hash: str
    text: str

    def __post_init__(self) -> None:
        if not self.content_id:
            raise InvalidEventError("content_id is required")
        if not self.content_hash:
            raise InvalidEventError("content_hash is required")


@dataclass(frozen=True)
class EntityNode:
    """A node in the entity/relationship graph with PIT provenance."""

    entity_id: str
    entity_type: EntityType
    known_at: datetime
    provenance: ProvenanceKind
    valid_at: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None:
            raise InvalidEventError("node known_at must be timezone-aware")
        if self.valid_at is not None and self.valid_at.tzinfo is None:
            raise InvalidEventError("node valid_at must be timezone-aware")


@dataclass(frozen=True)
class RelationshipEdge:
    """A directed, PIT-provenanced edge in the entity/relationship graph."""

    source_id: str
    target_id: str
    relationship_type: RelationshipType
    weight: Decimal
    known_at: datetime
    provenance: ProvenanceKind
    valid_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_id == self.target_id:
            raise ScenarioGraphError("self-loops are not allowed")
        if not (Decimal("-1") <= self.weight <= Decimal("1")):
            raise ScenarioGraphError("edge weight must be in [-1, 1]")
        if self.known_at.tzinfo is None:
            raise InvalidEventError("edge known_at must be timezone-aware")
        if self.valid_at is not None and self.valid_at.tzinfo is None:
            raise InvalidEventError("edge valid_at must be timezone-aware")
        if self.valid_at is not None and self.valid_at < self.known_at:
            raise InvalidEventError("edge valid_at cannot precede known_at")


@dataclass(frozen=True)
class ScenarioAgent:
    """A bounded agent in the simulation."""

    agent_id: str
    entity_id: str
    stance: ScenarioDirection
    confidence: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ScenarioGraphError("agent confidence must be in [0, 1]")


@dataclass(frozen=True)
class ScenarioScore:
    """A typed scenario quantity (issue #271 STAT-BLOCKER A).

    ``CALIBRATED_PROBABILITY`` is the only kind that may be called a probability, and
    it can only be constructed with an explicit calibrator identity and a calibration
    report reference produced by #269.
    """

    kind: ScenarioScoreKind
    value: Decimal
    calibrator_id: str | None = None
    calibration_report_ref: str | None = None

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.value <= Decimal("1")):
            raise ScenarioGraphError("score value must be in [0, 1]")
        if self.kind is ScenarioScoreKind.CALIBRATED_PROBABILITY:
            if not self.calibrator_id or not self.calibration_report_ref:
                raise ScenarioGraphError(
                    "CALIBRATED_PROBABILITY requires calibrator_id and calibration_report_ref"
                )
        elif self.calibrator_id is not None or self.calibration_report_ref is not None:
            raise ScenarioGraphError(
                "calibrator identity may only be attached to CALIBRATED_PROBABILITY"
            )

    @property
    def is_calibrated_probability(self) -> bool:
        return self.kind is ScenarioScoreKind.CALIBRATED_PROBABILITY


@dataclass(frozen=True)
class Scenario:
    """A single scenario with a typed score and evidence."""

    scenario_id: str
    name: str
    direction: ScenarioDirection
    score: ScenarioScore
    model_confidence: Decimal
    agent_vote_fraction: Decimal
    affected_entities: tuple[str, ...]
    assumptions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    propagation_path: tuple[str, ...]
    edge_provenance: tuple[ProvenanceKind, ...] = ()


@dataclass(frozen=True)
class ScenarioDiscovery:
    """Issue #271 STAT-BLOCKER F: discovery is separate from weighting.

    A well-calibrated weighting over an incomplete scenario set must not mask a
    discovery failure.
    """

    candidate_outcomes: tuple[str, ...]
    representable_outcomes: tuple[str, ...]
    coverage_rate: Decimal
    discovery_failed: bool


@dataclass(frozen=True)
class ScenarioWeighting:
    """Issue #271 STAT-BLOCKER F: ranking/weighting quality conditional on the set."""

    score_kind: ScenarioScoreKind
    score_mean: Decimal
    score_spread: Decimal
    calibrated: bool


@dataclass(frozen=True)
class ScenarioCost:
    """Resource cost of the scenario run."""

    compute_ms: int
    token_count: int = 0
    model_calls: int = 0
    api_calls: int = 0


@dataclass(frozen=True)
class ScenarioLineage:
    """Provenance for auditability."""

    event_snapshot_id: str
    graph_version: str
    model_version: str
    code_version: str
    config_hash: str


@dataclass(frozen=True)
class ModelKnowledgeCutoff:
    """Declared model knowledge cutoff (issue #271 BLOCKER A).

    Instructing a model to "use only this context" is NOT evidence that memorised
    future information is absent. Only a provider-declared cutoff at or before the
    simulated decision time makes a historical run controlled.
    """

    model_id: str
    provider: str
    cutoff_at: datetime | None
    source: str
    evidence_ref: str | None = None

    @classmethod
    def uncontrolled(
        cls, model_id: str = "unknown", provider: str = "unknown"
    ) -> ModelKnowledgeCutoff:
        """Fail-closed default: no cutoff evidence ⇒ uncontrolled."""
        return cls(
            model_id=model_id,
            provider=provider,
            cutoff_at=None,
            source="no-cutoff-evidence",
            evidence_ref=None,
        )

    def status_for(self, decision_time: datetime) -> TemporalKnowledgeStatus:
        if decision_time.tzinfo is None:
            raise InvalidEventError("decision_time must be timezone-aware")
        if self.cutoff_at is None:
            return TemporalKnowledgeStatus.UNCONTROLLED
        if self.cutoff_at.tzinfo is None:
            raise InvalidEventError("cutoff_at must be timezone-aware")
        if self.cutoff_at > decision_time:
            return TemporalKnowledgeStatus.UNCONTROLLED
        return TemporalKnowledgeStatus.CONTROLLED


@dataclass(frozen=True)
class InferenceFingerprint:
    """Inference identity and sampling parameters (issue #271 BLOCKER D)."""

    model_id: str
    provider: str
    prompt_hash: str
    system_instructions_hash: str
    temperature: Decimal
    sampling_policy: str
    runtime_version: str
    model_revision: str | None = None
    top_p: Decimal | None = None
    seed: int | None = None
    raw_output_hashes: tuple[str, ...] = ()

    @classmethod
    def rule_based(cls, engine_code_version: str) -> InferenceFingerprint:
        """Fingerprint for the built-in deterministic, model-free engine."""
        return cls(
            model_id="rule-based:scenario-graph",
            provider="in-process",
            prompt_hash=hashlib.sha256(b"rule-based:no-prompt").hexdigest(),
            system_instructions_hash=hashlib.sha256(b"rule-based:no-system").hexdigest(),
            temperature=Decimal("0"),
            sampling_policy="deterministic",
            runtime_version=engine_code_version,
            model_revision=engine_code_version,
            top_p=None,
            seed=0,
            raw_output_hashes=(),
        )

    @property
    def fully_reproducible(self) -> bool:
        """A run is only fully reproducible when the exact model revision and the
        sampling seed are both known (issue #271 BLOCKER D)."""
        return self.model_revision is not None and self.seed is not None


@dataclass(frozen=True)
class RunFingerprint:
    """Full reproducibility fingerprint of a scenario run (issue #271 BLOCKER D)."""

    contract_version: str
    prompt_hash: str
    system_instructions_hash: str
    model_id: str
    provider: str
    model_revision: str | None
    temperature: Decimal
    top_p: Decimal | None
    seed: int | None
    sampling_policy: str
    source_snapshot_hashes: tuple[str, ...]
    graph_builder_version: str
    graph_content_hash: str
    graph_config_hash: str
    agent_config_hash: str
    raw_output_hashes: tuple[str, ...]
    runtime_version: str

    @property
    def fully_reproducible(self) -> bool:
        return self.model_revision is not None and self.seed is not None

    def drift_components(self, other: RunFingerprint) -> tuple[str, ...]:
        """Which fingerprint components differ between two runs."""
        components = (
            "contract_version",
            "prompt_hash",
            "system_instructions_hash",
            "model_id",
            "provider",
            "model_revision",
            "temperature",
            "top_p",
            "seed",
            "sampling_policy",
            "source_snapshot_hashes",
            "graph_builder_version",
            "graph_content_hash",
            "graph_config_hash",
            "agent_config_hash",
            "raw_output_hashes",
            "runtime_version",
        )
        return tuple(name for name in components if getattr(self, name) != getattr(other, name))


@dataclass(frozen=True)
class ScenarioWorkerPolicy:
    """Capability ceiling of the scenario worker (issue #271 BLOCKER C).

    This is a constant. It is never derived from untrusted content, so prompt
    injection inside an input document cannot change tool policy or simulation scope.
    """

    has_execution_authority: bool = False
    has_order_authority: bool = False
    has_broker_credentials: bool = False
    has_shell_authority: bool = False
    has_network_authority: bool = False
    has_db_mutation_authority: bool = False
    has_github_write_authority: bool = False
    allowed_resources: tuple[str, ...] = ("read:canonical-event-snapshot",)


@dataclass(frozen=True)
class ScenarioOutput:
    """Canonical structured scenario output (Phase 2 contract).

    This is the ONLY output of the engine. It is a research feature / forecast
    modifier — never an order.
    """

    scenario_run_id: str
    as_of: datetime
    event_snapshot_id: str
    graph_version: str
    model_version: str
    affected_entities: tuple[str, ...]
    scenarios: tuple[Scenario, ...]
    assumptions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    uncertainty: Decimal
    runtime_ms: int
    cost: ScenarioCost
    lineage: ScenarioLineage
    temporal_knowledge_status: TemporalKnowledgeStatus
    fingerprint: RunFingerprint
    discovery: ScenarioDiscovery
    weighting: ScenarioWeighting
    worker_policy: ScenarioWorkerPolicy
    graph_content_hash: str
    simulation_replicates: int
    real_world_opportunities: int
    schema_validated: bool = True

    @property
    def is_promotion_grade(self) -> bool:
        """Only a temporally controlled, fully reproducible run may be offered as
        evidence of predictive value (issue #271 BLOCKER A + D)."""
        return (
            self.temporal_knowledge_status is TemporalKnowledgeStatus.CONTROLLED
            and self.fingerprint.fully_reproducible
            and self.schema_validated
        )

    @property
    def temporal_knowledge_flag(self) -> str | None:
        if self.temporal_knowledge_status is TemporalKnowledgeStatus.UNCONTROLLED:
            return TEMPORAL_KNOWLEDGE_UNCONTROLLED_FLAG
        return None


# ---------------------------------------------------------------------------
# Scenario graph engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioGraphConfig:
    """Configuration for the scenario propagation engine."""

    max_nodes: int = MAX_NODES
    max_edges: int = MAX_EDGES
    max_agents: int = MAX_AGENTS
    max_scenarios: int = MAX_SCENARIOS
    max_runtime_ms: int = MAX_RUNTIME_MS
    max_events_per_run: int = MAX_EVENTS_PER_RUN
    max_propagation_depth: int = MAX_PROPAGATION_DEPTH
    max_simulation_replicates: int = MAX_SIMULATION_REPLICATES
    max_model_budget_tokens: int = MAX_MODEL_BUDGET_TOKENS
    max_model_calls: int = MAX_MODEL_CALLS
    default_edge_weight: Decimal = Decimal("0.5")
    propagation_decay: Decimal = Decimal("0.8")
    confidence_floor: Decimal = Decimal("0.1")
    probability_floor: Decimal = Decimal("0.05")
    probability_cap: Decimal = Decimal("0.95")

    def __post_init__(self) -> None:
        if self.max_nodes <= 0 or self.max_edges <= 0:
            raise ScenarioGraphError("max_nodes and max_edges must be positive")
        if self.max_agents <= 0 or self.max_scenarios <= 0:
            raise ScenarioGraphError("max_agents and max_scenarios must be positive")
        if self.max_propagation_depth <= 0:
            raise ScenarioGraphError("max_propagation_depth must be positive")
        if self.max_simulation_replicates <= 0:
            raise ScenarioGraphError("max_simulation_replicates must be positive")


class ScenarioGraphEngine:
    """Deterministic, bounded scenario propagation engine.

    Pure function: events + config + declared knowledge → ScenarioOutput. No I/O, no
    network, no LLM calls, no execution authority.
    """

    MODEL_VERSION = CONTRACT_VERSION
    CODE_VERSION = "2026-10-04"
    GRAPH_BUILDER_VERSION = "graph-builder-v2"

    def __init__(self, config: ScenarioGraphConfig | None = None) -> None:
        self.config = config or ScenarioGraphConfig()

    def run(
        self,
        events: Sequence[ScenarioEvent],
        as_of: datetime,
        event_snapshot_id: str,
        graph_version: str = "graph-v2",
        knowledge: ModelKnowledgeCutoff | None = None,
        inference: InferenceFingerprint | None = None,
        agents: Sequence[ScenarioAgent] = (),
        candidate_outcomes: Sequence[str] = (),
        simulation_replicates: int = 1,
    ) -> ScenarioOutput:
        """Run scenario propagation on a set of PIT-safe events.

        Args:
            events: PIT-safe canonical events from issue #75.
            as_of: Decision time (must be >= every event ``first_seen_at``).
            event_snapshot_id: Identifier for the event snapshot.
            graph_version: Version of the graph configuration.
            knowledge: Declared model knowledge cutoff. Defaults to the fail-closed
                ``UNCONTROLLED`` status.
            inference: Inference identity/sampling fingerprint. Defaults to the
                deterministic rule-based fingerprint.
            agents: Bounded agent population (issue #271 Phase 1 spike component).
            candidate_outcomes: Outcomes that a discovery-complete scenario set would
                have to be able to represent (issue #271 STAT-BLOCKER F).
            simulation_replicates: Number of internal simulation repetitions. These
                are simulation noise reduction, NOT independent real-world evidence.

        Returns:
            Canonical structured scenario output.

        Raises:
            GraphBoundsExceeded: If a hard cap would be violated.
            InvalidEventError: If an event fails validation.
            PitViolationError: If an event/node/edge is known only after ``as_of``.
        """
        start = datetime.now(UTC)
        if as_of.tzinfo is None:
            raise InvalidEventError("as_of must be timezone-aware")
        if simulation_replicates <= 0:
            raise GraphBoundsExceeded("simulation_replicates must be positive")
        if simulation_replicates > self.config.max_simulation_replicates:
            raise GraphBoundsExceeded(
                f"simulation_replicates {simulation_replicates} exceeds cap "
                f"{self.config.max_simulation_replicates}"
            )
        if len(agents) > self.config.max_agents:
            raise GraphBoundsExceeded(
                f"agent count {len(agents)} exceeds cap {self.config.max_agents}"
            )

        self._validate_events(events, as_of)

        knowledge = knowledge or ModelKnowledgeCutoff.uncontrolled()
        inference = inference or InferenceFingerprint.rule_based(self.CODE_VERSION)
        temporal_status = knowledge.status_for(as_of)

        # Build the PIT-safe entity/relationship graph from events.
        nodes, edges = self._build_graph(events, as_of)

        # Run bounded propagation.
        scenarios = self._propagate(nodes, edges, events, agents)

        uncertainty = self._compute_uncertainty(scenarios, events)

        runtime_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
        if runtime_ms > self.config.max_runtime_ms:
            raise GraphBoundsExceeded(
                f"runtime {runtime_ms}ms exceeds cap {self.config.max_runtime_ms}ms"
            )

        affected: set[str] = set()
        for scenario in scenarios:
            affected.update(scenario.affected_entities)

        evidence: set[str] = set()
        for event in events:
            evidence.add(event.event_id)
        for scenario in scenarios:
            evidence.update(scenario.evidence_refs)

        graph_content_hash = self._hash_graph(nodes, edges)
        agent_config_hash = self._hash_agents(agents)
        config_hash = self._hash_config()
        lineage = ScenarioLineage(
            event_snapshot_id=event_snapshot_id,
            graph_version=graph_version,
            model_version=self.MODEL_VERSION,
            code_version=self.CODE_VERSION,
            config_hash=config_hash,
        )
        fingerprint = RunFingerprint(
            contract_version=CONTRACT_VERSION,
            prompt_hash=inference.prompt_hash,
            system_instructions_hash=inference.system_instructions_hash,
            model_id=inference.model_id,
            provider=inference.provider,
            model_revision=inference.model_revision,
            temperature=inference.temperature,
            top_p=inference.top_p,
            seed=inference.seed,
            sampling_policy=inference.sampling_policy,
            source_snapshot_hashes=tuple(sorted(e.content_hash for e in events)),
            graph_builder_version=self.GRAPH_BUILDER_VERSION,
            graph_content_hash=graph_content_hash,
            graph_config_hash=config_hash,
            agent_config_hash=agent_config_hash,
            raw_output_hashes=inference.raw_output_hashes,
            runtime_version=inference.runtime_version,
        )

        discovery = self._compute_discovery(scenarios, candidate_outcomes)
        weighting = self._compute_weighting(scenarios)

        return ScenarioOutput(
            scenario_run_id=str(uuid4()),
            as_of=as_of,
            event_snapshot_id=event_snapshot_id,
            graph_version=graph_version,
            model_version=self.MODEL_VERSION,
            affected_entities=tuple(sorted(affected)),
            scenarios=tuple(scenarios),
            assumptions=self._collect_assumptions(events, temporal_status),
            evidence_refs=tuple(sorted(evidence)[:MAX_EVIDENCE_REFS]),
            uncertainty=uncertainty,
            runtime_ms=runtime_ms,
            cost=ScenarioCost(compute_ms=runtime_ms),
            lineage=lineage,
            temporal_knowledge_status=temporal_status,
            fingerprint=fingerprint,
            discovery=discovery,
            weighting=weighting,
            worker_policy=ScenarioWorkerPolicy(),
            graph_content_hash=graph_content_hash,
            simulation_replicates=simulation_replicates,
            real_world_opportunities=0,
        )

    # -- validation ---------------------------------------------------------

    def _validate_events(self, events: Sequence[ScenarioEvent], as_of: datetime) -> None:
        if len(events) > self.config.max_events_per_run:
            raise GraphBoundsExceeded(
                f"event count {len(events)} exceeds cap {self.config.max_events_per_run}"
            )
        for event in events:
            if event.published_at > as_of:
                raise PitViolationError(
                    f"event {event.event_id} published_at {event.published_at} "
                    f"is after as_of {as_of} (look-ahead violation)"
                )
            if event.known_at > as_of:
                raise PitViolationError(
                    f"event {event.event_id} known_at {event.known_at} "
                    f"is after as_of {as_of} (PIT knowledge violation)"
                )

    # -- graph construction -------------------------------------------------

    def _build_graph(
        self, events: Sequence[ScenarioEvent], as_of: datetime
    ) -> tuple[dict[str, EntityNode], list[RelationshipEdge]]:
        """Build a PIT-safe entity/relationship graph from events.

        Every node and edge records ``known_at`` and ``provenance``. An element whose
        ``known_at`` is after ``as_of`` is rejected (issue #271 BLOCKER B).
        """
        nodes: dict[str, EntityNode] = {}
        edges: list[RelationshipEdge] = []
        edge_set: set[tuple[str, str, RelationshipType]] = set()

        for event in events:
            event_node_id = f"event:{event.event_id}"
            if event_node_id not in nodes:
                nodes[event_node_id] = EntityNode(
                    entity_id=event_node_id,
                    entity_type=EntityType.EVENT,
                    known_at=event.known_at,
                    provenance=ProvenanceKind.OBSERVED_SOURCE_FACT,
                    attributes={
                        "event_type": event.event_type,
                        "source": event.source,
                        "published_at": event.published_at.isoformat(),
                    },
                )

            for entity_id in event.entities:
                if entity_id not in nodes:
                    nodes[entity_id] = EntityNode(
                        entity_id=entity_id,
                        entity_type=EntityType.ASSET,
                        known_at=event.known_at,
                        provenance=ProvenanceKind.OBSERVED_SOURCE_FACT,
                    )
                edge_key = (event_node_id, entity_id, RelationshipType.AFFECTS)
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append(
                        RelationshipEdge(
                            source_id=event_node_id,
                            target_id=entity_id,
                            relationship_type=RelationshipType.AFFECTS,
                            weight=self.config.default_edge_weight,
                            known_at=event.known_at,
                            provenance=ProvenanceKind.OBSERVED_SOURCE_FACT,
                            evidence_refs=(event.event_id,),
                        )
                    )

            # Co-occurrence edges are MODEL-INFERRED, never observed facts.
            entity_list = list(event.entities)
            for i, source in enumerate(entity_list):
                for target in entity_list[i + 1 :]:
                    edge_key = (source, target, RelationshipType.CORRELATES_WITH)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        edges.append(
                            RelationshipEdge(
                                source_id=source,
                                target_id=target,
                                relationship_type=RelationshipType.CORRELATES_WITH,
                                weight=self.config.default_edge_weight,
                                known_at=event.known_at,
                                provenance=ProvenanceKind.MODEL_INFERRED,
                                evidence_refs=(event.event_id,),
                            )
                        )

        self._assert_pit_graph(nodes, edges, as_of)

        if len(nodes) > self.config.max_nodes:
            raise GraphBoundsExceeded(
                f"node count {len(nodes)} exceeds cap {self.config.max_nodes}"
            )
        if len(edges) > self.config.max_edges:
            raise GraphBoundsExceeded(
                f"edge count {len(edges)} exceeds cap {self.config.max_edges}"
            )

        return nodes, edges

    def _assert_pit_graph(
        self,
        nodes: Mapping[str, EntityNode],
        edges: Sequence[RelationshipEdge],
        as_of: datetime,
    ) -> None:
        """Reject any graph element known only after the decision time."""
        for node in nodes.values():
            if node.known_at > as_of:
                raise PitViolationError(
                    f"node {node.entity_id} known_at {node.known_at} is after as_of {as_of}"
                )
        for edge in edges:
            if edge.known_at > as_of:
                raise PitViolationError(
                    f"edge {edge.source_id}->{edge.target_id} known_at {edge.known_at} "
                    f"is after as_of {as_of}"
                )

    # -- propagation --------------------------------------------------------

    def _propagate(
        self,
        nodes: Mapping[str, EntityNode],
        edges: Sequence[RelationshipEdge],
        events: Sequence[ScenarioEvent],
        agents: Sequence[ScenarioAgent],
    ) -> list[Scenario]:
        """Run deterministic scenario propagation up to ``max_propagation_depth``."""
        scenarios: list[Scenario] = []
        adjacency = self._build_adjacency(edges)
        edge_provenance = {(edge.source_id, edge.target_id): edge.provenance for edge in edges}

        for event in events:
            if len(scenarios) >= self.config.max_scenarios:
                break

            event_node_id = f"event:{event.event_id}"
            if event_node_id not in nodes:
                continue

            visited: dict[str, tuple[Decimal, list[str]]] = {}
            queue: list[tuple[str, Decimal, list[str]]] = [
                (event_node_id, Decimal("1"), [event_node_id])
            ]

            while queue and len(visited) < self.config.max_nodes:
                current, strength, path = queue.pop(0)
                if current in visited:
                    continue
                if len(path) > self.config.max_propagation_depth:
                    continue
                visited[current] = (strength, path)

                for neighbor, edge_weight in adjacency.get(current, []):
                    if neighbor not in visited:
                        new_strength = strength * edge_weight * self.config.propagation_decay
                        if new_strength >= self.config.confidence_floor:
                            queue.append((neighbor, new_strength, [*path, neighbor]))

            if len(visited) > 1:
                scenario = self._make_scenario(event, visited, adjacency, edge_provenance, agents)
                if scenario is not None:
                    scenarios.append(scenario)

        return scenarios

    def _build_adjacency(
        self, edges: Sequence[RelationshipEdge]
    ) -> dict[str, list[tuple[str, Decimal]]]:
        adjacency: dict[str, list[tuple[str, Decimal]]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source_id, []).append((edge.target_id, edge.weight))
        return adjacency

    def _make_scenario(
        self,
        event: ScenarioEvent,
        visited: Mapping[str, tuple[Decimal, list[str]]],
        adjacency: Mapping[str, list[tuple[str, Decimal]]],
        edge_provenance: Mapping[tuple[str, str], ProvenanceKind],
        agents: Sequence[ScenarioAgent],
    ) -> Scenario | None:
        if len(visited) < 2:
            return None

        strengths = [s for s, _ in visited.values()]
        avg_strength = sum(strengths) / Decimal(len(strengths))
        score_value = min(
            max(avg_strength, self.config.probability_floor),
            self.config.probability_cap,
        )

        affected = [node_id for node_id in visited if not node_id.startswith("event:")]

        longest_path = max(visited.values(), key=lambda x: len(x[1]))[1]

        assumptions = [
            f"event_type={event.event_type}",
            f"propagation_depth={len(longest_path) - 1}",
            f"visited_nodes={len(visited)}",
        ]

        scenario_id = hashlib.sha256(
            f"{event.event_id}:{':'.join(longest_path)}".encode()
        ).hexdigest()[:16]

        path_provenance = tuple(
            edge_provenance[(longest_path[i], longest_path[i + 1])]
            for i in range(len(longest_path) - 1)
            if (longest_path[i], longest_path[i + 1]) in edge_provenance
        )

        return Scenario(
            scenario_id=scenario_id,
            name=f"{event.event_type}_scenario",
            direction=self._infer_direction(event),
            score=ScenarioScore(
                kind=ScenarioScoreKind.SCENARIO_SCORE,
                value=score_value,
            ),
            model_confidence=avg_strength,
            agent_vote_fraction=self._agent_vote_fraction(affected, agents),
            affected_entities=tuple(affected),
            assumptions=tuple(assumptions),
            evidence_refs=(event.event_id,),
            propagation_path=tuple(longest_path),
            edge_provenance=path_provenance,
        )

    def _agent_vote_fraction(
        self, affected: Sequence[str], agents: Sequence[ScenarioAgent]
    ) -> Decimal:
        """Fraction of the bounded agent population aligned with the affected set.

        This is an ``agent_vote_fraction`` (issue #271 STAT-BLOCKER A) and is NOT a
        probability. Agents are always bounded by ``max_agents``.
        """
        if not agents:
            return Decimal("0")
        affected_set = set(affected)
        relevant = [a for a in agents if a.entity_id in affected_set]
        if not relevant:
            return Decimal("0")
        bullish = sum(1 for a in relevant if a.stance is ScenarioDirection.BULLISH)
        bearish = sum(1 for a in relevant if a.stance is ScenarioDirection.BEARISH)
        aligned = max(bullish, bearish)
        return Decimal(aligned) / Decimal(len(agents))

    def _infer_direction(self, event: ScenarioEvent) -> ScenarioDirection:
        """Infer scenario direction from event type. Deterministic, no LLM."""
        event_type = event.event_type.upper()
        if "CRASH" in event_type or "RECESSION" in event_type or "DEFAULT" in event_type:
            return ScenarioDirection.BEARISH
        if "RALLY" in event_type or "GROWTH" in event_type or "EARNINGS_BEAT" in event_type:
            return ScenarioDirection.BULLISH
        return ScenarioDirection.NEUTRAL

    def _compute_uncertainty(
        self, scenarios: Sequence[Scenario], events: Sequence[ScenarioEvent]
    ) -> Decimal:
        if not scenarios:
            return Decimal("1")
        values = [s.score.value for s in scenarios]
        count = Decimal(len(values))
        mean = sum(values) / count
        variance = sum((p - mean) ** 2 for p in values) / count
        return min(Decimal("1"), variance.sqrt() + Decimal("0.1"))

    def _compute_discovery(
        self, scenarios: Sequence[Scenario], candidate_outcomes: Sequence[str]
    ) -> ScenarioDiscovery:
        """Issue #271 STAT-BLOCKER F: discovery quality, separate from weighting."""
        if not candidate_outcomes:
            return ScenarioDiscovery(
                candidate_outcomes=(),
                representable_outcomes=(),
                coverage_rate=Decimal("0"),
                discovery_failed=False,
            )
        observed_directions = {s.direction.value for s in scenarios}
        representable = tuple(
            outcome for outcome in candidate_outcomes if outcome in observed_directions
        )
        coverage = Decimal(len(representable)) / Decimal(len(candidate_outcomes))
        return ScenarioDiscovery(
            candidate_outcomes=tuple(candidate_outcomes),
            representable_outcomes=representable,
            coverage_rate=coverage,
            discovery_failed=coverage < Decimal("1"),
        )

    def _compute_weighting(self, scenarios: Sequence[Scenario]) -> ScenarioWeighting:
        """Issue #271 STAT-BLOCKER F: weighting quality conditional on the set."""
        if not scenarios:
            return ScenarioWeighting(
                score_kind=ScenarioScoreKind.SCENARIO_SCORE,
                score_mean=Decimal("0"),
                score_spread=Decimal("0"),
                calibrated=False,
            )
        values = [s.score.value for s in scenarios]
        count = Decimal(len(values))
        mean = sum(values) / count
        spread = (max(values) - min(values)) if len(values) > 1 else Decimal("0")
        return ScenarioWeighting(
            score_kind=scenarios[0].score.kind,
            score_mean=mean,
            score_spread=spread,
            calibrated=all(s.score.is_calibrated_probability for s in scenarios),
        )

    def _collect_assumptions(
        self, events: Sequence[ScenarioEvent], status: TemporalKnowledgeStatus
    ) -> tuple[str, ...]:
        assumptions: set[str] = set()
        for event in events:
            assumptions.add(f"event_type={event.event_type}")
            assumptions.add(f"source={event.source}")
        assumptions.add(f"temporal_knowledge_status={status.value}")
        if status is TemporalKnowledgeStatus.UNCONTROLLED:
            assumptions.add(TEMPORAL_KNOWLEDGE_UNCONTROLLED_FLAG)
        return tuple(sorted(assumptions))

    # -- hashing ------------------------------------------------------------

    def _hash_config(self) -> str:
        config_dict = {
            "contract_version": CONTRACT_VERSION,
            "max_nodes": self.config.max_nodes,
            "max_edges": self.config.max_edges,
            "max_agents": self.config.max_agents,
            "max_scenarios": self.config.max_scenarios,
            "max_runtime_ms": self.config.max_runtime_ms,
            "max_events_per_run": self.config.max_events_per_run,
            "max_propagation_depth": self.config.max_propagation_depth,
            "max_simulation_replicates": self.config.max_simulation_replicates,
            "max_model_budget_tokens": self.config.max_model_budget_tokens,
            "max_model_calls": self.config.max_model_calls,
            "default_edge_weight": str(self.config.default_edge_weight),
            "propagation_decay": str(self.config.propagation_decay),
        }
        return hashlib.sha256(json.dumps(config_dict, sort_keys=True).encode()).hexdigest()[:16]

    def _hash_graph(
        self,
        nodes: Mapping[str, EntityNode],
        edges: Sequence[RelationshipEdge],
    ) -> str:
        """Content hash of the PIT graph (issue #271 BLOCKER D)."""
        payload = {
            "nodes": [
                {
                    "id": node.entity_id,
                    "type": node.entity_type.value,
                    "provenance": node.provenance.value,
                    "known_at": node.known_at.isoformat(),
                }
                for node in sorted(nodes.values(), key=lambda n: n.entity_id)
            ],
            "edges": [
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "type": edge.relationship_type.value,
                    "weight": str(edge.weight),
                    "provenance": edge.provenance.value,
                    "known_at": edge.known_at.isoformat(),
                }
                for edge in sorted(
                    edges, key=lambda e: (e.source_id, e.target_id, e.relationship_type.value)
                )
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def _hash_agents(self, agents: Sequence[ScenarioAgent]) -> str:
        payload = [
            {
                "agent_id": a.agent_id,
                "entity_id": a.entity_id,
                "stance": a.stance.value,
                "confidence": str(a.confidence),
            }
            for a in sorted(agents, key=lambda a: a.agent_id)
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Serialization + schema validation (fail closed)
# ---------------------------------------------------------------------------

_SCENARIO_OUTPUT_SCHEMA: Mapping[str, type] = {
    "scenario_run_id": str,
    "as_of": str,
    "event_snapshot_id": str,
    "graph_version": str,
    "model_version": str,
    "affected_entities": list,
    "scenarios": list,
    "assumptions": list,
    "evidence_refs": list,
    "uncertainty": str,
    "runtime_ms": int,
    "cost": dict,
    "lineage": dict,
    "temporal_knowledge_status": str,
    "fingerprint": dict,
    "discovery": dict,
    "weighting": dict,
    "worker_policy": dict,
    "graph_content_hash": str,
    "simulation_replicates": int,
    "real_world_opportunities": int,
    "schema_validated": bool,
}

_SCENARIO_SCHEMA: Mapping[str, type] = {
    "scenario_id": str,
    "name": str,
    "direction": str,
    "score": dict,
    "model_confidence": str,
    "agent_vote_fraction": str,
    "affected_entities": list,
    "assumptions": list,
    "evidence_refs": list,
    "propagation_path": list,
    "edge_provenance": list,
}


def scenario_output_to_dict(output: ScenarioOutput) -> dict[str, Any]:
    """Convert ScenarioOutput to a JSON-serializable dict."""
    return {
        "scenario_run_id": output.scenario_run_id,
        "as_of": output.as_of.isoformat(),
        "event_snapshot_id": output.event_snapshot_id,
        "graph_version": output.graph_version,
        "model_version": output.model_version,
        "affected_entities": list(output.affected_entities),
        "scenarios": [
            {
                "scenario_id": s.scenario_id,
                "name": s.name,
                "direction": s.direction.value,
                "score": {
                    "kind": s.score.kind.value,
                    "value": str(s.score.value),
                    "calibrator_id": s.score.calibrator_id,
                    "calibration_report_ref": s.score.calibration_report_ref,
                },
                "model_confidence": str(s.model_confidence),
                "agent_vote_fraction": str(s.agent_vote_fraction),
                "affected_entities": list(s.affected_entities),
                "assumptions": list(s.assumptions),
                "evidence_refs": list(s.evidence_refs),
                "propagation_path": list(s.propagation_path),
                "edge_provenance": [p.value for p in s.edge_provenance],
            }
            for s in output.scenarios
        ],
        "assumptions": list(output.assumptions),
        "evidence_refs": list(output.evidence_refs),
        "uncertainty": str(output.uncertainty),
        "runtime_ms": output.runtime_ms,
        "cost": {
            "compute_ms": output.cost.compute_ms,
            "token_count": output.cost.token_count,
            "model_calls": output.cost.model_calls,
            "api_calls": output.cost.api_calls,
        },
        "lineage": {
            "event_snapshot_id": output.lineage.event_snapshot_id,
            "graph_version": output.lineage.graph_version,
            "model_version": output.lineage.model_version,
            "code_version": output.lineage.code_version,
            "config_hash": output.lineage.config_hash,
        },
        "temporal_knowledge_status": output.temporal_knowledge_status.value,
        "fingerprint": {
            "contract_version": output.fingerprint.contract_version,
            "prompt_hash": output.fingerprint.prompt_hash,
            "system_instructions_hash": output.fingerprint.system_instructions_hash,
            "model_id": output.fingerprint.model_id,
            "provider": output.fingerprint.provider,
            "model_revision": output.fingerprint.model_revision,
            "temperature": str(output.fingerprint.temperature),
            "top_p": None if output.fingerprint.top_p is None else str(output.fingerprint.top_p),
            "seed": output.fingerprint.seed,
            "sampling_policy": output.fingerprint.sampling_policy,
            "source_snapshot_hashes": list(output.fingerprint.source_snapshot_hashes),
            "graph_builder_version": output.fingerprint.graph_builder_version,
            "graph_content_hash": output.fingerprint.graph_content_hash,
            "graph_config_hash": output.fingerprint.graph_config_hash,
            "agent_config_hash": output.fingerprint.agent_config_hash,
            "raw_output_hashes": list(output.fingerprint.raw_output_hashes),
            "runtime_version": output.fingerprint.runtime_version,
        },
        "discovery": {
            "candidate_outcomes": list(output.discovery.candidate_outcomes),
            "representable_outcomes": list(output.discovery.representable_outcomes),
            "coverage_rate": str(output.discovery.coverage_rate),
            "discovery_failed": output.discovery.discovery_failed,
        },
        "weighting": {
            "score_kind": output.weighting.score_kind.value,
            "score_mean": str(output.weighting.score_mean),
            "score_spread": str(output.weighting.score_spread),
            "calibrated": output.weighting.calibrated,
        },
        "worker_policy": {
            "has_execution_authority": output.worker_policy.has_execution_authority,
            "has_order_authority": output.worker_policy.has_order_authority,
            "has_broker_credentials": output.worker_policy.has_broker_credentials,
            "has_shell_authority": output.worker_policy.has_shell_authority,
            "has_network_authority": output.worker_policy.has_network_authority,
            "has_db_mutation_authority": output.worker_policy.has_db_mutation_authority,
            "has_github_write_authority": output.worker_policy.has_github_write_authority,
            "allowed_resources": list(output.worker_policy.allowed_resources),
        },
        "graph_content_hash": output.graph_content_hash,
        "simulation_replicates": output.simulation_replicates,
        "real_world_opportunities": output.real_world_opportunities,
        "schema_validated": output.schema_validated,
    }


def validate_scenario_output_schema(payload: Mapping[str, Any]) -> None:
    """Validate a serialized scenario output; raise on any deviation (fail closed).

    Issue #271 BLOCKER C: structured output must be schema-validated before it is
    returned into QuantLab, and an invalid payload must fail closed.
    """
    if not isinstance(payload, Mapping):
        raise SchemaValidationError("scenario output must be a mapping")

    missing = sorted(set(_SCENARIO_OUTPUT_SCHEMA) - set(payload))
    if missing:
        raise SchemaValidationError(f"missing required keys: {missing}")

    unknown = sorted(set(payload) - set(_SCENARIO_OUTPUT_SCHEMA))
    if unknown:
        raise SchemaValidationError(f"unknown keys: {unknown}")

    for key, expected in _SCENARIO_OUTPUT_SCHEMA.items():
        value = payload[key]
        if expected is int and isinstance(value, bool):
            raise SchemaValidationError(f"{key} must be {expected.__name__}")
        if not isinstance(value, expected):
            raise SchemaValidationError(
                f"{key} must be {expected.__name__}, got {type(value).__name__}"
            )

    for scenario in payload["scenarios"]:
        if not isinstance(scenario, Mapping):
            raise SchemaValidationError("scenario entries must be mappings")
        scenario_missing = sorted(set(_SCENARIO_SCHEMA) - set(scenario))
        if scenario_missing:
            raise SchemaValidationError(f"scenario missing keys: {scenario_missing}")
        score = scenario["score"]
        if not isinstance(score, Mapping):
            raise SchemaValidationError("scenario score must be a mapping")
        if score.get("kind") not in {k.value for k in ScenarioScoreKind}:
            raise SchemaValidationError(f"invalid scenario score kind: {score.get('kind')!r}")


def scenario_output_from_dict(payload: Mapping[str, Any]) -> ScenarioOutput:
    """Rebuild a ScenarioOutput from a serialized dict after strict validation."""
    validate_scenario_output_schema(payload)

    def _dec(value: object) -> Decimal:
        return Decimal(str(value))

    scenarios = tuple(
        Scenario(
            scenario_id=str(item["scenario_id"]),
            name=str(item["name"]),
            direction=ScenarioDirection(str(item["direction"])),
            score=ScenarioScore(
                kind=ScenarioScoreKind(str(item["score"]["kind"])),
                value=_dec(item["score"]["value"]),
                calibrator_id=item["score"]["calibrator_id"],
                calibration_report_ref=item["score"]["calibration_report_ref"],
            ),
            model_confidence=_dec(item["model_confidence"]),
            agent_vote_fraction=_dec(item["agent_vote_fraction"]),
            affected_entities=tuple(str(x) for x in item["affected_entities"]),
            assumptions=tuple(str(x) for x in item["assumptions"]),
            evidence_refs=tuple(str(x) for x in item["evidence_refs"]),
            propagation_path=tuple(str(x) for x in item["propagation_path"]),
            edge_provenance=tuple(ProvenanceKind(str(x)) for x in item["edge_provenance"]),
        )
        for item in payload["scenarios"]
    )

    fp = payload["fingerprint"]
    lineage = payload["lineage"]
    discovery = payload["discovery"]
    weighting = payload["weighting"]
    policy = payload["worker_policy"]
    cost = payload["cost"]

    return ScenarioOutput(
        scenario_run_id=str(payload["scenario_run_id"]),
        as_of=datetime.fromisoformat(str(payload["as_of"])),
        event_snapshot_id=str(payload["event_snapshot_id"]),
        graph_version=str(payload["graph_version"]),
        model_version=str(payload["model_version"]),
        affected_entities=tuple(str(x) for x in payload["affected_entities"]),
        scenarios=scenarios,
        assumptions=tuple(str(x) for x in payload["assumptions"]),
        evidence_refs=tuple(str(x) for x in payload["evidence_refs"]),
        uncertainty=_dec(payload["uncertainty"]),
        runtime_ms=int(payload["runtime_ms"]),
        cost=ScenarioCost(
            compute_ms=int(cost["compute_ms"]),
            token_count=int(cost["token_count"]),
            model_calls=int(cost["model_calls"]),
            api_calls=int(cost["api_calls"]),
        ),
        lineage=ScenarioLineage(
            event_snapshot_id=str(lineage["event_snapshot_id"]),
            graph_version=str(lineage["graph_version"]),
            model_version=str(lineage["model_version"]),
            code_version=str(lineage["code_version"]),
            config_hash=str(lineage["config_hash"]),
        ),
        temporal_knowledge_status=TemporalKnowledgeStatus(
            str(payload["temporal_knowledge_status"])
        ),
        fingerprint=RunFingerprint(
            contract_version=str(fp["contract_version"]),
            prompt_hash=str(fp["prompt_hash"]),
            system_instructions_hash=str(fp["system_instructions_hash"]),
            model_id=str(fp["model_id"]),
            provider=str(fp["provider"]),
            model_revision=None if fp["model_revision"] is None else str(fp["model_revision"]),
            temperature=_dec(fp["temperature"]),
            top_p=None if fp["top_p"] is None else _dec(fp["top_p"]),
            seed=None if fp["seed"] is None else int(fp["seed"]),
            sampling_policy=str(fp["sampling_policy"]),
            source_snapshot_hashes=tuple(str(x) for x in fp["source_snapshot_hashes"]),
            graph_builder_version=str(fp["graph_builder_version"]),
            graph_content_hash=str(fp["graph_content_hash"]),
            graph_config_hash=str(fp["graph_config_hash"]),
            agent_config_hash=str(fp["agent_config_hash"]),
            raw_output_hashes=tuple(str(x) for x in fp["raw_output_hashes"]),
            runtime_version=str(fp["runtime_version"]),
        ),
        discovery=ScenarioDiscovery(
            candidate_outcomes=tuple(str(x) for x in discovery["candidate_outcomes"]),
            representable_outcomes=tuple(str(x) for x in discovery["representable_outcomes"]),
            coverage_rate=_dec(discovery["coverage_rate"]),
            discovery_failed=bool(discovery["discovery_failed"]),
        ),
        weighting=ScenarioWeighting(
            score_kind=ScenarioScoreKind(str(weighting["score_kind"])),
            score_mean=_dec(weighting["score_mean"]),
            score_spread=_dec(weighting["score_spread"]),
            calibrated=bool(weighting["calibrated"]),
        ),
        worker_policy=ScenarioWorkerPolicy(
            has_execution_authority=bool(policy["has_execution_authority"]),
            has_order_authority=bool(policy["has_order_authority"]),
            has_broker_credentials=bool(policy["has_broker_credentials"]),
            has_shell_authority=bool(policy["has_shell_authority"]),
            has_network_authority=bool(policy["has_network_authority"]),
            has_db_mutation_authority=bool(policy["has_db_mutation_authority"]),
            has_github_write_authority=bool(policy["has_github_write_authority"]),
            allowed_resources=tuple(str(x) for x in policy["allowed_resources"]),
        ),
        graph_content_hash=str(payload["graph_content_hash"]),
        simulation_replicates=int(payload["simulation_replicates"]),
        real_world_opportunities=int(payload["real_world_opportunities"]),
        schema_validated=bool(payload["schema_validated"]),
    )
