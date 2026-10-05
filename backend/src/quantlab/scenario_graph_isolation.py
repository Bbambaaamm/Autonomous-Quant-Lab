"""Scenario worker isolation, bounded concurrency and preregistered degradation.

Issue #271 (safety / governance) requires two operational guarantees that the pure
engine in :mod:`quantlab.scenario_graph` deliberately does not own:

1. a **hard concurrency cap** on scenario runs, so a scenario workload can never
   expand the runtime's resource envelope (issue #271 acceptance criteria:
   "hard cap na graph size, agents, runtime, concurrency a model budget");
2. **failure / timeout isolation**: when the scenario engine errors, times out or is
   rejected, the core QuantLab workflow must not stop unless the strategy is
   explicitly scenario-dependent — and a scenario-dependent strategy must
   fail/degrade according to a *preregistered* policy, never per-incident.

This module is the operational boundary. It is intentionally separate from the pure
engine so that ``scenario_graph.py`` stays a deterministic, clock-free, I/O-free
function; the clock read needed to enforce a runtime budget lives here.

Safety properties
-----------------
- No execution authority: this module never emits an order, never touches a broker and
  never mutates risk limits. It only returns a typed outcome to the caller.
- Fail closed: an unrecognised status or a policy/output mismatch is an error, not a
  silent downgrade.
- Preregistered: :class:`ScenarioDegradationPolicy` is validated at construction; a
  ``CORE_INDEPENDENT`` workload may only continue without the scenario feature and a
  ``SCENARIO_DEPENDENT`` workload may only fail closed. There is no per-incident
  override that could quietly widen either behaviour.
- Bounded: the in-process guard enforces ``MAX_CONCURRENCY`` (default 1, matching the
  runtime resource budget for heavy research). Cross-process admission stays with the
  PostgreSQL advisory slot owned by #190.
- Circuit breaker: after ``max_consecutive_failures`` consecutive failures the worker
  stops invoking the engine and reports the feature as unavailable, so a permanently
  broken scenario engine cannot burn the runtime budget on every cycle.

Determinism note: the engine's *output* is deterministic; this module's guard and
breaker are operational state and are therefore deliberately not part of the run
fingerprint.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from quantlab.scenario_graph import (
    MAX_CONCURRENCY,
    GraphBoundsExceeded,
    InferenceFingerprint,
    ModelKnowledgeCutoff,
    ScenarioAgent,
    ScenarioEvent,
    ScenarioGraphEngine,
    ScenarioOutput,
)

__all__ = [
    "MAX_CONSECUTIVE_FAILURES",
    "ScenarioIsolationError",
    "ScenarioConcurrencyExceeded",
    "ScenarioRunStatus",
    "ScenarioDependencyMode",
    "ScenarioDegradeAction",
    "ScenarioDegradationPolicy",
    "ScenarioRunOutcome",
    "ScenarioConcurrencyGuard",
    "ScenarioWorker",
]

#: Default consecutive-failure threshold before the breaker reports unavailability.
MAX_CONSECUTIVE_FAILURES = 3


class ScenarioIsolationError(RuntimeError):
    """Base error for the scenario isolation boundary."""


class ScenarioConcurrencyExceeded(ScenarioIsolationError):
    """Raised when the in-process scenario concurrency cap would be exceeded."""


class ScenarioRunStatus(StrEnum):
    """Terminal status of one isolated scenario run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CONCURRENCY_REJECTED = "CONCURRENCY_REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


class ScenarioDependencyMode(StrEnum):
    """Whether the consuming strategy can survive without the scenario feature."""

    #: Core workflow must never stop because of a scenario failure (issue #271).
    CORE_INDEPENDENT = "CORE_INDEPENDENT"
    #: Strategy is explicitly scenario-dependent and degrades per preregistered policy.
    SCENARIO_DEPENDENT = "SCENARIO_DEPENDENT"


class ScenarioDegradeAction(StrEnum):
    """Preregistered action taken when the scenario feature is unavailable."""

    CONTINUE_WITHOUT_SCENARIO = "CONTINUE_WITHOUT_SCENARIO"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class ScenarioDegradationPolicy:
    """Preregistered degradation policy (issue #271 safety / governance).

    The policy is fixed before an experiment runs. The mode and the action are
    validated as a pair so the core workflow can never be halted by an optional
    research feature and a scenario-dependent strategy can never silently proceed
    without its scenario input.
    """

    mode: ScenarioDependencyMode
    on_unavailable: ScenarioDegradeAction
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES

    def __post_init__(self) -> None:
        if self.max_consecutive_failures <= 0:
            raise ScenarioIsolationError("max_consecutive_failures must be positive")
        if self.mode is ScenarioDependencyMode.CORE_INDEPENDENT:
            if self.on_unavailable is not ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO:
                raise ScenarioIsolationError(
                    "a CORE_INDEPENDENT workload must continue without the scenario "
                    "feature; the core workflow must not stop on a scenario failure"
                )
        elif self.on_unavailable is not ScenarioDegradeAction.FAIL_CLOSED:
            raise ScenarioIsolationError(
                "a SCENARIO_DEPENDENT workload must fail closed per preregistered policy"
            )

    @classmethod
    def core_independent(cls) -> ScenarioDegradationPolicy:
        """Default: scenario research is optional; the core workflow continues."""
        return cls(
            mode=ScenarioDependencyMode.CORE_INDEPENDENT,
            on_unavailable=ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO,
        )

    @classmethod
    def scenario_dependent(cls) -> ScenarioDegradationPolicy:
        """Explicit opt-in: the strategy needs the scenario feature and fails closed."""
        return cls(
            mode=ScenarioDependencyMode.SCENARIO_DEPENDENT,
            on_unavailable=ScenarioDegradeAction.FAIL_CLOSED,
        )

    @property
    def halts_core_workflow_on_failure(self) -> bool:
        return self.on_unavailable is ScenarioDegradeAction.FAIL_CLOSED


@dataclass(frozen=True)
class ScenarioRunOutcome:
    """Typed outcome of one isolated scenario run.

    ``output`` is non-None if and only if ``status`` is ``SUCCEEDED``. An over-budget
    or failed run returns no partial output, so a caller can never consume a degraded
    scenario artifact as if it were a complete one.
    """

    status: ScenarioRunStatus
    output: ScenarioOutput | None
    degraded: bool
    action: ScenarioDegradeAction
    error: str | None
    duration_ms: int
    consecutive_failures: int

    def __post_init__(self) -> None:
        if (self.status is ScenarioRunStatus.SUCCEEDED) is not (self.output is not None):
            raise ScenarioIsolationError(
                "a scenario outcome carries output if and only if it succeeded"
            )

    @property
    def usable(self) -> bool:
        """Whether the caller may consume scenario evidence from this outcome."""
        return self.status is ScenarioRunStatus.SUCCEEDED and self.output is not None


class ScenarioConcurrencyGuard:
    """Bounded, non-blocking in-process admission for scenario runs.

    Enforces the ``MAX_CONCURRENCY`` hard cap. Admission is non-blocking: a second
    concurrent run is rejected fail-closed instead of queuing, matching the runtime
    contract's ``RESEARCH_CONCURRENCY_LIMIT`` behaviour (one heavy research child).
    """

    def __init__(self, max_concurrency: int = MAX_CONCURRENCY) -> None:
        if max_concurrency <= 0:
            raise ScenarioIsolationError("max_concurrency must be positive")
        if max_concurrency > MAX_CONCURRENCY:
            raise ScenarioIsolationError(
                f"max_concurrency {max_concurrency} exceeds hard cap {MAX_CONCURRENCY}"
            )
        self.max_concurrency = max_concurrency
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def available(self) -> bool:
        with self._lock:
            return self._in_flight < self.max_concurrency

    @contextmanager
    def admitted(self) -> Iterator[None]:
        """Admit one run or raise :class:`ScenarioConcurrencyExceeded` (fail closed)."""
        with self._lock:
            if self._in_flight >= self.max_concurrency:
                raise ScenarioConcurrencyExceeded(
                    f"SCENARIO_CONCURRENCY_LIMIT: {self._in_flight} run(s) in flight "
                    f"at cap {self.max_concurrency}"
                )
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1


@dataclass
class ScenarioWorker:
    """Isolation boundary around :class:`ScenarioGraphEngine`.

    Wraps the pure engine with the concurrency cap, the runtime budget check and the
    preregistered degradation policy. The worker never raises a scenario failure at
    the caller: it converts it into a typed :class:`ScenarioRunOutcome` and applies the
    policy. It has no execution authority.
    """

    engine: ScenarioGraphEngine
    guard: ScenarioConcurrencyGuard = field(default_factory=ScenarioConcurrencyGuard)
    policy: ScenarioDegradationPolicy = field(
        default_factory=ScenarioDegradationPolicy.core_independent
    )
    _consecutive_failures: int = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def available(self) -> bool:
        """The feature is available unless the breaker has tripped or the cap is full."""
        return (
            self._consecutive_failures < self.policy.max_consecutive_failures
            and self.guard.available
        )

    def reset(self) -> None:
        """Clear breaker state (operator action after the root cause is resolved)."""
        self._consecutive_failures = 0

    def run(
        self,
        events: Sequence[ScenarioEvent],
        *,
        as_of: datetime,
        event_snapshot_id: str,
        graph_version: str = "graph-v2",
        knowledge: ModelKnowledgeCutoff | None = None,
        inference: InferenceFingerprint | None = None,
        agents: Sequence[ScenarioAgent] = (),
        candidate_outcomes: Sequence[str] = (),
        simulation_replicates: int = 1,
    ) -> ScenarioRunOutcome:
        """Run the scenario engine inside the isolation boundary.

        Returns a typed outcome; never propagates an engine failure. The caller decides
        what to do with a degraded outcome by reading :attr:`ScenarioRunOutcome.action`.
        """
        if self._consecutive_failures >= self.policy.max_consecutive_failures:
            return self._degraded(
                status=ScenarioRunStatus.UNAVAILABLE,
                error=(
                    f"scenario engine circuit breaker open after "
                    f"{self._consecutive_failures} consecutive failures"
                ),
                duration_ms=0,
            )

        start = time.monotonic()
        try:
            with self.guard.admitted():
                output = self.engine.run(
                    events,
                    as_of=as_of,
                    event_snapshot_id=event_snapshot_id,
                    graph_version=graph_version,
                    knowledge=knowledge,
                    inference=inference,
                    agents=agents,
                    candidate_outcomes=candidate_outcomes,
                    simulation_replicates=simulation_replicates,
                )
        except ScenarioConcurrencyExceeded as exc:
            return self._degraded(
                status=ScenarioRunStatus.CONCURRENCY_REJECTED,
                error=str(exc),
                duration_ms=self._elapsed_ms(start),
            )
        except GraphBoundsExceeded as exc:
            # The engine refuses to return an over-budget run; treat it as a timeout so
            # no partial scenario artifact reaches the decision path.
            return self._degraded(
                status=ScenarioRunStatus.TIMED_OUT,
                error=str(exc),
                duration_ms=self._elapsed_ms(start),
            )
        except Exception as exc:  # noqa: BLE001 - the boundary must never propagate
            return self._degraded(
                status=ScenarioRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=self._elapsed_ms(start),
            )

        duration_ms = self._elapsed_ms(start)
        if duration_ms > self.engine.config.max_runtime_ms:
            return self._degraded(
                status=ScenarioRunStatus.TIMED_OUT,
                error=(
                    f"scenario run took {duration_ms}ms > cap {self.engine.config.max_runtime_ms}ms"
                ),
                duration_ms=duration_ms,
            )

        self._consecutive_failures = 0
        return ScenarioRunOutcome(
            status=ScenarioRunStatus.SUCCEEDED,
            output=output,
            degraded=False,
            action=self.policy.on_unavailable,
            error=None,
            duration_ms=duration_ms,
            consecutive_failures=0,
        )

    def _degraded(
        self,
        *,
        status: ScenarioRunStatus,
        error: str,
        duration_ms: int,
    ) -> ScenarioRunOutcome:
        self._consecutive_failures += 1
        return ScenarioRunOutcome(
            status=status,
            output=None,
            degraded=True,
            action=self.policy.on_unavailable,
            error=error,
            duration_ms=duration_ms,
            consecutive_failures=self._consecutive_failures,
        )

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)


def outcome_to_dict(outcome: ScenarioRunOutcome) -> dict[str, Any]:
    """Serialize an isolation outcome for evidence storage (no secrets, no output body)."""
    return {
        "status": outcome.status.value,
        "degraded": outcome.degraded,
        "action": outcome.action.value,
        "error": outcome.error,
        "duration_ms": outcome.duration_ms,
        "consecutive_failures": outcome.consecutive_failures,
        "usable": outcome.usable,
    }
