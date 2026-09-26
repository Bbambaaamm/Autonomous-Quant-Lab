"""Offline, fail-closed acceptance tests for Herdr v1.2 scheduler (#231).

All tests are PAPER-only and use injected clock/audit-log/temp paths — no live
host probe, no network, no live broker, no credential.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantlab.herdr.scheduler import (
    LIVE_TRADING_TOOL_PREFIXES,
    ROLE_TOOL_ALLOWLIST,
    AllowDecision,
    AuditLog,
    DenyDecision,
    DenyReason,
    DynamicChildScheduler,
    LifecycleState,
    SchedulerBudget,
    SubtaskProposal,
    TaskGraph,
    TaskNode,
)


def _node(node_id: str, prereqs=(), role="reader", tools=(), **kw) -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task=f"task:{node_id}",
        prereqs=tuple(prereqs),
        role=role,
        tools=tools,
        **kw,
    )


def _idle_scheduler(tmp_path: Path, clock=None) -> DynamicChildScheduler:
    return DynamicChildScheduler(
        budget=SchedulerBudget(max_global_concurrency=1),
        clock=clock or (lambda: 1_000_000.0),
        audit_log=AuditLog(tmp_path / "machine_city.jsonl"),
    )


def _parallel_graph() -> TaskGraph:
    """Two independent root nodes (no prereqs) — candidate for parallel run."""
    return TaskGraph(nodes=(_node("a"), _node("b")), name="parallel")


def _dependent_graph() -> TaskGraph:
    """B depends on A (B.prereqs=(A,))."""
    return TaskGraph(
        nodes=(_node("a"), _node("b", prereqs=("a",))),
        name="dependent",
    )


# --- Acceptance: planner cannot bypass limits via larger graph output ----- #


def test_planner_runaway_graph_is_denied_before_submit(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    graph = TaskGraph(nodes=tuple(_node(f"n{i}") for i in range(300)), name="runaway")
    with pytest.raises(ValueError):
        sched.submit(graph)
    events = sched.audit_log.replay()  # type: ignore[union-attr]
    denies = [e for e in events if e["event"] == "deny"]
    assert any(e["reason"] == DenyReason.DAG_NODE_LIMIT.value for e in denies)


def test_planner_cycles_are_rejected(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    graph = TaskGraph(nodes=(_node("a", prereqs=("b",)), _node("b", prereqs=("a",))))
    with pytest.raises(ValueError):
        sched.submit(graph)
    events = sched.audit_log.replay()  # type: ignore[union-attr]
    assert any(e["event"] == "deny" and e["reason"] == DenyReason.DAG_CYCLE.value for e in events)


# --- Acceptance: two independent nodes run in parallel -------------------- #


def test_two_independent_nodes_run_in_parallel(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path, clock=lambda: 1_000_000.0)
    sched.submit(_parallel_graph(), repo="QuantLab", issue="231")
    # Bump concurrency cap so both leaf nodes can be dispatched in one batch.
    sched.budget = SchedulerBudget(max_global_concurrency=4)
    leases = sched.dispatch()
    assert {lease.task_id for lease in leases} == {"a", "b"}
    assert all(s.state == LifecycleState.RUNNING for s in (sched._tasks["a"], sched._tasks["b"]))


def test_concurrency_cap_serializes_dispatch(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)  # max_global_concurrency=1
    sched.submit(_parallel_graph())
    leases = sched.dispatch()
    assert len(leases) == 1  # backpressure: second node waits


# --- Acceptance: dependent node starts only after prerequisite PASSED ----- #


def test_dependent_node_starts_only_after_prereq_done(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    sched.submit(_dependent_graph())
    assert [n.node_id for n in sched.ready()] == ["a"]  # A is a ready leaf; B still blocked on A
    # A must be dispatched and completed first.
    leases = sched.dispatch()
    assert leases and leases[0].task_id == "a"
    assert sched.complete("a", result="ok-a")
    # Now B becomes ready and can dispatch.
    assert [n.node_id for n in sched.ready()] == ["b"]
    leases = sched.dispatch()
    assert leases and leases[0].task_id == "b"


def test_blocker_propagation_when_prereq_fails(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    sched.submit(_dependent_graph())
    leases = sched.dispatch()  # dispatch A
    assert leases[0].task_id == "a"
    for _ in range(5):  # exceeding max_retries (3) -> FAILED
        sched.fail("a")
    assert sched._tasks["a"].state == LifecycleState.FAILED
    assert sched._tasks["b"].state == LifecycleState.BLOCKED


# --- Acceptance: lost worker reclaim without double commit ---------------- #


def test_lost_worker_reclaim_without_double_commit(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path, clock=lambda: 1_000_000.0)
    sched.submit(_parallel_graph())
    sched.budget = SchedulerBudget(max_global_concurrency=4)
    sched.dispatch()
    # Simulate a host clock advance past the lease TTL + lost worker.
    holder = "agent:a"
    reclaimed = sched.reclaim(holder, now=1_000_000.0 + 1_000.0)
    assert reclaimed == ["a"]
    assert sched._tasks["a"].state == LifecycleState.PLANNED  # back to ready
    # Completing twice with the same result SHA is idempotent (no double commit).
    assert sched.complete("a", result="ok-a") is True
    assert sched.complete("a", result="ok-a") is False  # idempotent no-op


# --- Acceptance: child cannot escalate tools / permissions beyond parent --- #


def test_child_can_not_escalate_tools_beyond_parent(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "search_files", "patch", "write_file"),
        child_role="writer",
        child_tools=("read_file", "search_files", "patch", "write_file", "delete_file"),
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.CHILD_TOOL_ESCALATION
    events = sched.audit_log.replay()  # type: ignore[union-attr]
    assert any(e["event"] == "deny" for e in events)


def test_child_can_not_escalate_role_beyond_parent(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch", "write_file"),
        child_role="operator",  # operator > writer rank
        child_tools=("read_file", "git_push"),  # git_push is operator-only
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason in (DenyReason.CHILD_ROLE_ESCALATION, DenyReason.NON_SPAWNABLE_ROLE)


def test_child_proposal_without_parent_tools_is_bounded_by_role_allowlist(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=(),
        child_role="reader",
        child_tools=("read_file", "git_push"),  # git_push not in reader allowlist
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.TOOL_NOT_IN_ROLE_ALLOWLIST


def test_operator_role_is_not_auto_spawnable(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch", "write_file"),
        child_role="operator",
        child_tools=("read_file", "patch", "write_file"),  # within writer allowlist
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason in (DenyReason.NON_SPAWNABLE_ROLE, DenyReason.CHILD_ROLE_ESCALATION)


# --- Acceptance: model/fallback choice recorded in telemetry ------------- #


def test_model_choice_recorded_in_telemetry(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    sched.submit(
        TaskGraph(nodes=(_node("a", model="gpt-4o-mini", fallback_model="o3-mini"),)),
        repo="QuantLab",
        issue="231",
    )
    sched.budget = SchedulerBudget(max_global_concurrency=4)
    sched.dispatch()
    rec = sched._tasks["a"]
    assert rec.model_used == "gpt-4o-mini"
    assert rec.fallback_used == "o3-mini"
    assert any(
        t["model"] == "gpt-4o-mini" and t["fallback_model"] == "o3-mini" for t in rec.telemetry
    )


def test_subtask_proposal_records_model_telemetry(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch"),
        child_role="writer",
        child_tools=("read_file", "patch"),
        child_model="o3",
        child_fallback_model="gpt-4o-mini",
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, AllowDecision)
    assert decision.model == "o3"
    assert decision.fallback_model == "gpt-4o-mini"
    events = sched.audit_log.replay()  # type: ignore[union-attr]
    assert any(e["event"] == "subtask_allow" and e["model"] == "o3" for e in events)


# --- Acceptance: integration test crash/restart (durable recovery) --------- #


def test_crash_restart_recovery_idempotent(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "machine_city.jsonl")
    sched = DynamicChildScheduler(
        budget=SchedulerBudget(max_global_concurrency=4), clock=lambda: 1_000_000.0, audit_log=log
    )
    sched.submit(_dependent_graph(), repo="QuantLab", issue="231")
    sched.dispatch()
    assert sched.complete("a", result="ok-a")
    sched.cancel("a", reason="manual-test")  # durable cancel event
    events = log.replay()
    assert any(e["event"] == "complete" for e in events)
    assert any(e["event"] == "cancel" for e in events)

    # Simulate crash/restart: rebuild scheduler from the audit log.
    recovered = DynamicChildScheduler(
        budget=SchedulerBudget(max_global_concurrency=4), clock=lambda: 1_000_010.0, audit_log=log
    )
    applied = recovered.replay()
    assert applied >= 2  # complete + cancel replayed
    assert recovered._tasks["a"].state == LifecycleState.CANCELLED


# --- Acceptance: PAPER-only invariant (no live broker permissions) --------- #


@pytest.mark.parametrize(
    "tool",
    ["alpaca-order.submit", "broker.place", "trade.now", "execution.run", "risk-engine.eval"],
)
def test_paper_only_blocks_live_trading_tool(tool: str, tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch", tool),
        child_role="writer",
        child_tools=(tool,),
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.LIVE_TRADING_TOOL


@pytest.mark.parametrize(
    "path_tool",
    [
        "patch backend/src/quantlab/trading.py",
        "read backend/src/quantlab/phase4.py",
        "search_files backend/src/quantlab/security.py",
    ],
)
def test_protected_paths_denied(path_tool: str, tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch"),
        child_role="writer",
        child_tools=("read_file", path_tool),
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.PROTECTED_PATH


@pytest.mark.parametrize("secret_tool", ["shell export API_KEY=xxx", "patch alpaca_secret_key"])
def test_secret_access_denied(secret_tool: str, tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch"),
        child_role="writer",
        child_tools=("read_file", secret_tool),
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.SECRET_ACCESS


def test_non_paper_only_spawn_is_denied(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file", "patch"),
        child_role="writer",
        child_tools=("read_file",),
        paper_only=False,
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    assert decision.reason == DenyReason.LIVE_TRADING_TOOL


def test_no_live_broker_tools_in_any_role_allowlist() -> None:
    for role, tools in ROLE_TOOL_ALLOWLIST.items():
        for tool in tools:
            assert not any(tool.startswith(p) for p in LIVE_TRADING_TOOL_PREFIXES), (role, tool)


# --- Acceptance: durable cancellation records to Machine City log -------- --


def test_durable_cancellation_is_recorded(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    sched.submit(_parallel_graph())
    sched.cancel("a", reason="runaway-subtask")
    events = sched.audit_log.replay()  # type: ignore[union-attr]
    cancels = [e for e in events if e["event"] == "cancel" and e["task_id"] == "a"]
    assert cancels and cancels[-1]["reason"] == "runaway-subtask"


def test_denial_is_audited_and_visible(tmp_path: Path) -> None:
    sched = _idle_scheduler(tmp_path)
    proposal = SubtaskProposal(
        parent_role="writer",
        parent_tools=("read_file",),
        child_role="writer",
        child_tools=("read_file", "alpaca-order.submit"),
    )
    decision = sched.evaluate_child_proposal(proposal)
    assert isinstance(decision, DenyDecision)
    raw = (tmp_path / "machine_city.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert all(json.loads(line) for line in raw)
    denies = [json.loads(line) for line in raw if json.loads(line)["event"] == "deny"]
    assert any(d["reason"] == DenyReason.LIVE_TRADING_TOOL.value for d in denies)
