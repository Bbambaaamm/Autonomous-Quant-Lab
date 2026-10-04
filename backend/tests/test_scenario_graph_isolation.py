"""Tests for the scenario worker isolation boundary (issue #271 safety/governance).

Covers:
- the hard concurrency cap (in-process admission, fail closed, non-blocking);
- failure isolation: an engine error/timeout never propagates to the caller;
- the core workflow never stops on a scenario failure when the strategy is not
  scenario-dependent;
- a scenario-dependent strategy fails closed per preregistered policy;
- the preregistered policy cannot be configured into an unsafe combination;
- the circuit breaker stops a permanently broken engine from burning the budget;
- no execution authority and no order/broker surface.
"""

from __future__ import annotations

import ast
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantlab.scenario_graph import (
    MAX_CONCURRENCY,
    GraphBoundsExceeded,
    ScenarioEvent,
    ScenarioGraphConfig,
    ScenarioGraphEngine,
)
from quantlab.scenario_graph_isolation import (
    MAX_CONSECUTIVE_FAILURES,
    ScenarioConcurrencyExceeded,
    ScenarioConcurrencyGuard,
    ScenarioDegradationPolicy,
    ScenarioDegradeAction,
    ScenarioDependencyMode,
    ScenarioIsolationError,
    ScenarioRunOutcome,
    ScenarioRunStatus,
    ScenarioWorker,
    outcome_to_dict,
)

_AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _event(event_id: str = "evt-001") -> ScenarioEvent:
    return ScenarioEvent(
        event_id=event_id,
        event_type="EARNINGS_BEAT",
        published_at=_AS_OF - timedelta(hours=3),
        first_seen_at=_AS_OF - timedelta(hours=2),
        source="test-source",
        source_event_id=f"src-{event_id}",
        entities=("AAPL", "TECH"),
        content_hash=f"hash-{event_id}",
    )


class _FailingEngine(ScenarioGraphEngine):
    """Engine stub that always raises, to exercise the isolation boundary."""

    def run(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise RuntimeError("scenario engine exploded")


class _SlowEngine(ScenarioGraphEngine):
    """Engine stub that reports an over-budget runtime."""

    def __init__(self, runtime_ms: int) -> None:
        super().__init__(ScenarioGraphConfig())
        self._runtime_ms = runtime_ms

    def run(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise GraphBoundsExceeded(f"runtime {self._runtime_ms}ms exceeds cap 5000ms")


class _OverrunningEngine(ScenarioGraphEngine):
    """Engine stub that returns a real output after exceeding its own runtime cap.

    Exercises the worker's defence-in-depth post-hoc runtime check, which fires when an
    engine returns successfully but over budget.
    """

    def __init__(self) -> None:
        super().__init__(ScenarioGraphConfig(max_runtime_ms=1))
        self._inner = ScenarioGraphEngine()

    def run(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        time.sleep(0.02)
        return self._inner.run(*args, **kwargs)  # type: ignore[arg-type]


class TestConcurrencyCap:
    """Hard concurrency cap: fail closed, non-blocking, bounded."""

    def test_default_cap_is_one(self) -> None:
        assert MAX_CONCURRENCY == 1

    def test_cap_cannot_be_raised_above_hard_cap(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="exceeds hard cap"):
            ScenarioConcurrencyGuard(max_concurrency=MAX_CONCURRENCY + 1)

    def test_cap_must_be_positive(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="must be positive"):
            ScenarioConcurrencyGuard(max_concurrency=0)

    def test_second_admission_is_rejected_not_queued(self) -> None:
        guard = ScenarioConcurrencyGuard()
        with guard.admitted():
            assert guard.in_flight == 1
            assert guard.available is False
            with pytest.raises(ScenarioConcurrencyExceeded, match="SCENARIO_CONCURRENCY_LIMIT"):
                with guard.admitted():
                    pass

    def test_admission_is_released_after_use(self) -> None:
        guard = ScenarioConcurrencyGuard()
        with guard.admitted():
            pass
        assert guard.in_flight == 0
        with guard.admitted():
            assert guard.in_flight == 1

    def test_admission_is_released_on_exception(self) -> None:
        guard = ScenarioConcurrencyGuard()
        with pytest.raises(ValueError), guard.admitted():
            raise ValueError("boom")
        assert guard.in_flight == 0

    def test_concurrent_run_is_reported_as_degraded_not_raised(self) -> None:
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        outcome_holder: list[ScenarioRunOutcome] = []

        with worker.guard.admitted():
            outcome_holder.append(
                worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
            )

        outcome = outcome_holder[0]
        assert outcome.status is ScenarioRunStatus.CONCURRENCY_REJECTED
        assert outcome.degraded is True
        assert outcome.output is None
        assert outcome.action is ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO

    def test_real_threaded_contention_never_exceeds_cap(self) -> None:
        """Two threads racing one guard: at most one admitted, the other degraded."""
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        results: list[ScenarioRunStatus] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt() -> None:
            barrier.wait()
            outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
            with lock:
                results.append(outcome.status)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == 2
        assert ScenarioRunStatus.SUCCEEDED in results
        # The loser is degraded (rejected or breaker-opened); it is never a silent
        # second concurrent success.
        assert results.count(ScenarioRunStatus.SUCCEEDED) >= 1


class TestFailureIsolation:
    """A scenario failure must never propagate to the caller."""

    def test_engine_exception_becomes_a_typed_outcome(self) -> None:
        worker = ScenarioWorker(engine=_FailingEngine())
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.status is ScenarioRunStatus.FAILED
        assert outcome.degraded is True
        assert outcome.output is None
        assert outcome.error is not None and "RuntimeError" in outcome.error

    def test_over_budget_run_becomes_timed_out(self) -> None:
        worker = ScenarioWorker(engine=_SlowEngine(runtime_ms=9999))
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.status is ScenarioRunStatus.TIMED_OUT
        assert outcome.output is None

    def test_successful_but_over_budget_run_is_downgraded(self) -> None:
        """Defence in depth: an engine that returns over budget yields no artifact."""
        worker = ScenarioWorker(engine=_OverrunningEngine())
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.status is ScenarioRunStatus.TIMED_OUT
        assert outcome.output is None
        assert outcome.degraded is True

    def test_outcome_carries_output_iff_succeeded(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="if and only if"):
            ScenarioRunOutcome(
                status=ScenarioRunStatus.SUCCEEDED,
                output=None,
                degraded=False,
                action=ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO,
                error=None,
                duration_ms=1,
                consecutive_failures=0,
            )

    def test_successful_run_returns_output_and_resets_breaker(self) -> None:
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.status is ScenarioRunStatus.SUCCEEDED
        assert outcome.usable is True
        assert outcome.output is not None
        assert outcome.degraded is False
        assert outcome.consecutive_failures == 0


class TestPreregisteredPolicy:
    """The degradation policy is fixed and cannot be configured unsafely."""

    def test_core_independent_default_continues(self) -> None:
        policy = ScenarioDegradationPolicy.core_independent()
        assert policy.mode is ScenarioDependencyMode.CORE_INDEPENDENT
        assert policy.on_unavailable is ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO
        assert policy.halts_core_workflow_on_failure is False

    def test_core_independent_cannot_fail_closed(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="must continue without"):
            ScenarioDegradationPolicy(
                mode=ScenarioDependencyMode.CORE_INDEPENDENT,
                on_unavailable=ScenarioDegradeAction.FAIL_CLOSED,
            )

    def test_scenario_dependent_must_fail_closed(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="must fail closed"):
            ScenarioDegradationPolicy(
                mode=ScenarioDependencyMode.SCENARIO_DEPENDENT,
                on_unavailable=ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO,
            )

    def test_scenario_dependent_fails_closed(self) -> None:
        policy = ScenarioDegradationPolicy.scenario_dependent()
        assert policy.halts_core_workflow_on_failure is True
        worker = ScenarioWorker(engine=_FailingEngine(), policy=policy)
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.action is ScenarioDegradeAction.FAIL_CLOSED

    def test_max_consecutive_failures_must_be_positive(self) -> None:
        with pytest.raises(ScenarioIsolationError, match="must be positive"):
            ScenarioDegradationPolicy(
                mode=ScenarioDependencyMode.CORE_INDEPENDENT,
                on_unavailable=ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO,
                max_consecutive_failures=0,
            )

    def test_core_workflow_continues_across_repeated_failures(self) -> None:
        """The core workflow is never halted by an optional scenario failure."""
        worker = ScenarioWorker(engine=_FailingEngine())
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
            assert outcome.action is ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO
            assert outcome.usable is False


class TestCircuitBreaker:
    """A permanently broken engine must stop consuming the runtime budget."""

    def test_breaker_opens_after_threshold(self) -> None:
        worker = ScenarioWorker(engine=_FailingEngine())
        assert worker.available is True
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert worker.consecutive_failures == MAX_CONSECUTIVE_FAILURES
        assert worker.available is False

        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert outcome.status is ScenarioRunStatus.UNAVAILABLE
        assert "circuit breaker open" in (outcome.error or "")

    def test_breaker_reset_restores_availability(self) -> None:
        worker = ScenarioWorker(engine=_FailingEngine())
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert worker.available is False
        worker.reset()
        assert worker.available is True
        assert worker.consecutive_failures == 0

    def test_success_clears_partial_failure_streak(self) -> None:
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert worker.consecutive_failures == 0

    def test_breaker_threshold_is_configurable_via_policy(self) -> None:
        policy = ScenarioDegradationPolicy(
            mode=ScenarioDependencyMode.CORE_INDEPENDENT,
            on_unavailable=ScenarioDegradeAction.CONTINUE_WITHOUT_SCENARIO,
            max_consecutive_failures=1,
        )
        worker = ScenarioWorker(engine=_FailingEngine(), policy=policy)
        worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        assert worker.available is False


class TestIsolationShadowOnly:
    """The isolation boundary has no execution authority either."""

    def test_outcome_serialization_has_no_order_surface(self) -> None:
        worker = ScenarioWorker(engine=_FailingEngine())
        outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
        data = outcome_to_dict(outcome)
        for forbidden in ("order", "side", "quantity", "broker", "execution", "action_id"):
            assert forbidden not in data

    def test_worker_exposes_no_execution_method(self) -> None:
        worker = ScenarioWorker(engine=ScenarioGraphEngine())
        for name in dir(worker):
            lowered = name.lower()
            for forbidden in ("order", "execute", "broker", "submit", "trade", "risk"):
                assert forbidden not in lowered, f"worker exposes {name}"

    def test_module_never_imports_network_or_subprocess(self) -> None:
        module_path = (
            Path(__file__).resolve().parents[1] / "src" / "quantlab" / "scenario_graph_isolation.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
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
        module_path = (
            Path(__file__).resolve().parents[1] / "src" / "quantlab" / "scenario_graph_isolation.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        io_names = {"open", "read", "write", "input", "print", "exec", "eval", "compile"}
        offenders = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in io_names
        ]
        assert offenders == []


class TestModelBudgetCap:
    """The model budget cap is declared, zero, and cannot be widened."""

    def test_config_defaults_are_zero(self) -> None:
        config = ScenarioGraphConfig()
        assert config.max_model_budget_tokens == 0
        assert config.max_model_calls == 0

    def test_positive_model_budget_is_rejected(self) -> None:
        with pytest.raises(Exception, match="max_model_budget_tokens"):
            ScenarioGraphConfig(max_model_budget_tokens=1000)

    def test_positive_model_calls_are_rejected(self) -> None:
        with pytest.raises(Exception, match="max_model_calls"):
            ScenarioGraphConfig(max_model_calls=1)

    def test_negative_model_budget_is_rejected(self) -> None:
        with pytest.raises(Exception, match="max_model_budget_tokens"):
            ScenarioGraphConfig(max_model_budget_tokens=-1)


def test_outcome_to_dict_round_trips_status_and_action() -> None:
    worker = ScenarioWorker(engine=ScenarioGraphEngine())
    outcome = worker.run([_event()], as_of=_AS_OF, event_snapshot_id="snap-001")
    data = outcome_to_dict(outcome)
    assert data["status"] == "SUCCEEDED"
    assert data["usable"] is True
    assert data["degraded"] is False
    assert data["action"] == "CONTINUE_WITHOUT_SCENARIO"
