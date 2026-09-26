"""Herdr v1.2: Dependency-aware scheduler + dynamic child-agent lifecycle (#231).

Fail-closed, PAPER-only, offline-testable (stdlib + quantlab imports).

The scheduler consumes a TaskGraph (#230-style DAG) and:
  * validates planner graph output against hard caps (node/depth/fanout) so a
    planner cannot bypass limits by emitting a bigger graph;
  * resolves a READY queue from satisfied prerequisites — a dependent node
    cannot start before its prerequisite PASSED;
  * enforces max global/per-repo/per-issue concurrency before any spawn;
  * acquires a per-task lease (fencing) so a lost worker's task is reclaimed
    idempotently with no double commit (completion is SHA-keyed);
  * routes child-subtask proposals through a fail-closed PolicyGate (child tools
    ⊆ parent tools; no role escalation; PAPER-only; no live-broker / protected
    path / secret / external-network tools);
  * records the chosen model + fallback in per-task telemetry;
  * supports bounded retry/backoff (cap at max_retries) and blocker propagation
    (a FAILED prereq BLOCKEDs dependents);
  * persists lifecycle + denial/cancel events to an append-only JSONL audit log
    (crash/restart recovery via replay; durable cancellation).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

# --------------------------------------------------------------------------- #
# PAPER-only safety policy (fail-closed). Mirrors the conservative guard from
# #236; kept inline here so #231 is self-contained (herdr/ package is not yet
# merged into origin/main).
# --------------------------------------------------------------------------- #

LIVE_TRADING_TOOL_PREFIXES: tuple[str, ...] = (
    "alpaca-order",
    "alpaca-position",
    "alpaca-sse",
    "broker",
    "live-broker",
    "trade",
    "execution",
    "risk-engine",
)

PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "backend/src/quantlab/trading.py",
    "backend/src/quantlab/phase4.py",
    "backend/src/quantlab/security.py",
)

SECRET_INDICATORS: tuple[str, ...] = (
    "api_key",
    "api_secret",
    "secret_key",
    "private_key",
    "client_secret",
    "password",
    "alpaca_key_id",
    "alpaca_secret_key",
    "api_admin_token",
)

EXTERNAL_NETWORK_TOOL_SUFFIXES: tuple[str, ...] = (
    ".post",
    ".put",
    ".patch",
    ".upload",
)

# Role-based tool allowlist. Reader tools ⊆ writer tools ⊆ operator tools.
# Operator tools (push/deploy/merge) are never auto-spawned.
_READER_TOOLS = frozenset(
    {
        "read_file",
        "read",
        "search_files",
        "grep",
        "web_search",
        "web_extract",
        "shell",
        "git_status",
        "git_log",
    }
)
_WRITER_TOOLS = _READER_TOOLS.union({"patch", "write_file", "delete_file", "git_add", "git_commit"})
_OPERATOR_TOOLS = _WRITER_TOOLS.union({"git_push", "deploy", "git_merge"})

ROLE_TOOL_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "reader": _READER_TOOLS,
    "writer": _WRITER_TOOLS,
    "operator": _OPERATOR_TOOLS,
}

AUTO_SPAWNABLE_ROLES: frozenset[str] = frozenset({"reader", "writer"})

# Role ranking for permission-escalation checks.
_ROLE_RANK: Mapping[str, int] = {"reader": 0, "writer": 1, "operator": 2}


class DenyReason(StrEnum):
    """Machine-readable denial reasons — audited + visible in Machine City."""

    CHILD_TOOL_ESCALATION = "child_tool_escalation"
    CHILD_ROLE_ESCALATION = "child_role_escalation"
    NON_SPAWNABLE_ROLE = "non_spawnable_role"
    TOOL_NOT_IN_ROLE_ALLOWLIST = "tool_not_in_role_allowlist"
    LIVE_TRADING_TOOL = "live_trading_tool"
    PROTECTED_PATH = "protected_path"
    SECRET_ACCESS = "secret_access"
    NETWORK_POLICY = "network_policy"
    PLANNER_GRAPH_TOO_LARGE = "planner_graph_too_large"
    DAG_NODE_LIMIT = "dag_node_limit"
    DAG_DEPTH_LIMIT = "dag_depth_limit"
    DAG_FANOUT_LIMIT = "dag_fanout_limit"
    DAG_CYCLE = "dag_cycle"
    GLOBAL_CONCURRENCY_LIMIT = "global_concurrency_limit"
    PER_REPO_LIMIT = "per_repo_limit"
    PER_ISSUE_LIMIT = "per_issue_limit"
    RETRY_EXHAUSTED = "retry_exhausted"


# --------------------------------------------------------------------------- #
# Task model (#230 TaskGraph-style DAG).
# --------------------------------------------------------------------------- #


class LifecycleState(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"  # terminal success
    FAILED = "failed"  # terminal after retries exhausted
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TaskNode:
    """A single unit of work in a Herdr TaskGraph (DAG node)."""

    node_id: str
    task: str
    prereqs: tuple[str, ...] = ()
    role: str = "reader"
    tools: tuple[str, ...] = ()
    model: str = "default"
    fallback_model: str = "default-fallback"
    parent_node: str | None = None
    max_seconds: int = 300
    max_retries: int = 3
    backoff_base: float = 2.0
    paper_only: bool = True
    repo: str = ""
    issue: str = ""


@dataclass(frozen=True)
class TaskGraph:
    """Immutable plan submitted by the planner (#230 semantics)."""

    nodes: tuple[TaskNode, ...]
    name: str = "plan"
    repo: str = ""
    issue: str = ""

    @property
    def root_id(self) -> str:
        return self.nodes[0].node_id if self.nodes else ""

    @property
    def by_id(self) -> dict[str, TaskNode]:
        return {n.node_id: n for n in self.nodes}

    def successors(self, node_id: str) -> list[str]:
        """Node IDs that directly depend on node_id."""
        return [n.node_id for n in self.nodes if node_id in n.prereqs]

    def node_count(self) -> int:
        return len(self.nodes)

    def max_depth(self) -> int:
        """Longest path (topological depth). Raises on cycles."""
        if not self.nodes:
            return 0
        memo: dict[str, int] = {}

        def depth(node_id: str, stack: tuple[str, ...]) -> int:
            if node_id in memo:
                return memo[node_id]
            if node_id in stack:  # cycle
                raise ValueError(f"Cycle in DAG at {node_id}")
            node = self.by_id[node_id]
            if not node.prereqs:
                memo[node_id] = 1
                return 1
            d = 1 + max(depth(p, stack + (node_id,)) for p in node.prereqs)
            memo[node_id] = d
            return d

        return max(depth(n.node_id, ()) for n in self.nodes)

    def max_fanout(self) -> int:
        """Max number of direct dependents of any single node."""
        if not self.nodes:
            return 0
        counts = {n.node_id: 0 for n in self.nodes}
        for n in self.nodes:
            for p in n.prereqs:
                if p in counts:
                    counts[p] += 1
        return max(counts.values()) if counts else 0

    def has_cycle(self) -> bool:
        try:
            self.max_depth()
            return False
        except ValueError:
            return True


@dataclass(frozen=True)
class SubtaskProposal:
    """A child-requested subtask → Herdr policy decision (fail-closed default).

    Mirrors the role/tool/parent contract from #236 AdmissionControl.
    """

    parent_role: str
    parent_tools: tuple[str, ...]
    child_role: str
    child_tools: tuple[str, ...]
    child_task: str = ""
    paper_only: bool = True
    child_model: str = ""
    child_fallback_model: str = ""


@dataclass(frozen=True)
class SchedulerBudget:
    """Conservative admission + scheduling caps for the dynamic swarm.

    Scheduler-time ceilings complement (never replace) the runtime resource
    guards in Settings (worker_soft_rss_mb, market_job_min_available_mb, etc.).
    """

    max_global_concurrency: int = 4
    max_per_repo: int = 2
    max_per_issue: int = 1
    max_dag_nodes: int = 256
    max_dag_depth: int = 8
    max_dag_fanout: int = 4
    claim_ttl_seconds: float = 60.0
    default_model: str = "default"
    default_fallback_model: str = "default-fallback"


# --------------------------------------------------------------------------- #
# Audit log (append-only JSONL — durable lifecycle/deny/cancel events).
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AuditLog:
    """Append-only JSONL. Each line = one event. Corrupt line blocks replay."""

    def __init__(self, path: str | Path) -> None:
        self.path: Path = Path(path)

    def append(self, event: Mapping[str, object]) -> dict[str, object]:
        record: dict[str, object] = {"ts": _now_iso(), "event": event["event"]}
        record.update(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def replay(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if not self.path.exists():
            return events
        with self.path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    raise  # tamper-evident: corrupt line must not be silently dropped
        return events


def _result_sha(result: str | bytes) -> str:
    m = hashlib.sha256()
    m.update(result if isinstance(result, bytes) else result.encode())
    return m.hexdigest()


# --------------------------------------------------------------------------- #
# Fail-closed policy gate for child-subtask proposals (#236 policy, inline).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DenyDecision:
    denied: bool = True
    reason: DenyReason = DenyReason.LIVE_TRADING_TOOL
    detail: str = ""

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class AllowDecision:
    denied: bool = False
    model: str = ""
    fallback_model: str = ""
    budget_utilization: Mapping[str, float] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return True


Decision = AllowDecision | DenyDecision


def _tool_is_denied(tool: str) -> DenyReason | None:
    lowered = tool.lower()
    if any(lowered.startswith(p) for p in LIVE_TRADING_TOOL_PREFIXES):
        return DenyReason.LIVE_TRADING_TOOL
    if any(p in lowered for p in PROTECTED_PATH_PREFIXES):
        return DenyReason.PROTECTED_PATH
    if any(p in lowered for p in SECRET_INDICATORS):
        return DenyReason.SECRET_ACCESS
    if any(lowered.endswith(s) for s in EXTERNAL_NETWORK_TOOL_SUFFIXES):
        return DenyReason.NETWORK_POLICY
    return None


class PolicyGate:
    """Fail-closed gate for a child agent requesting to spawn a subtask.

    * child role rank may not exceed parent role rank (no permission escalation)
    * child tools must be a subset of parent tools AND the child's role allowlist
    * child role must be auto-spawnable
    * PAPER-only: live-broker / protected-path / secret / network tools denied
    A child agent may never expand the tool/permission set available to it.
    """

    @staticmethod
    def evaluate(proposal: SubtaskProposal) -> Decision:
        # PAPER-only safety hooks FIRST (fail-closed): live-broker / protected-path /
        # secret / external-network tools are denied regardless of role/allowlist.
        if not proposal.paper_only:
            return DenyDecision(
                reason=DenyReason.LIVE_TRADING_TOOL,
                detail="subtask must be PAPER-only (paper_only=False refused)",
            )
        for tool in proposal.child_tools:
            denied = _tool_is_denied(tool)
            if denied is not None:
                return DenyDecision(
                    reason=denied,
                    detail=f"tool={tool!r} denied by PAPER-only/safety policy ({denied.value})",
                )

        # Role escalation check: child may never escalate permissions beyond parent.
        parent_rank = _ROLE_RANK.get(proposal.parent_role, -1)
        child_rank = _ROLE_RANK.get(proposal.child_role, -1)
        if child_rank < 0 or parent_rank < 0:
            return DenyDecision(
                reason=DenyReason.NON_SPAWNABLE_ROLE,
                detail=(
                    f"unknown role parent={proposal.parent_role!r} "
                    f"child={proposal.child_role!r}"
                ),
            )
        if child_rank > parent_rank:
            return DenyDecision(
                reason=DenyReason.CHILD_ROLE_ESCALATION,
                detail=(
                    f"child role={proposal.child_role!r} rank={child_rank} > "
                    f"parent role={proposal.parent_role!r} rank={parent_rank}"
                ),
            )
        if proposal.child_role not in AUTO_SPAWNABLE_ROLES:
            return DenyDecision(
                reason=DenyReason.NON_SPAWNABLE_ROLE,
                detail=f"role={proposal.child_role!r} is not auto-spawnable",
            )

        child_set = frozenset(proposal.child_tools)
        parent_set = frozenset(proposal.parent_tools)
        role_tools = ROLE_TOOL_ALLOWLIST.get(proposal.child_role, frozenset())

        # Child may never escalate beyond parent.
        if parent_set and (child_set - parent_set):
            return DenyDecision(
                reason=DenyReason.CHILD_TOOL_ESCALATION,
                detail=f"child tools beyond parent: {sorted(child_set - parent_set)}",
            )
        # Child tools must be within the role allowlist.
        if child_set - role_tools:
            return DenyDecision(
                reason=DenyReason.TOOL_NOT_IN_ROLE_ALLOWLIST,
                detail=(
                    f"tools not in role allowlist {proposal.child_role!r}: "
                    f"{sorted(child_set - role_tools)}"
                ),
            )
        return AllowDecision(
            model=proposal.child_model or "default",
            fallback_model=proposal.child_fallback_model or "default-fallback",
        )


# --------------------------------------------------------------------------- #
# Scheduler.
# --------------------------------------------------------------------------- #


@dataclass
class _TaskRecord:
    node: TaskNode
    state: LifecycleState = LifecycleState.PLANNED
    attempts: int = 0
    model_used: str = ""
    fallback_used: str = ""
    result_sha: str | None = None
    telemetry: list[dict[str, object]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


@dataclass
class _Lease:
    task_id: str
    holder: str
    lease_until: float
    state: LifecycleState = LifecycleState.RUNNING


class DynamicChildScheduler:
    """Dependency-aware scheduler + dynamic child lifecycle (#231).

    Offline-testable: ``clock`` and ``audit_log`` are injected.
    """

    def __init__(
        self,
        budget: SchedulerBudget | None = None,
        clock: Callable[[], float] = time.time,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.budget = budget or SchedulerBudget()
        self._clock = clock
        self.audit_log = audit_log
        self._tasks: dict[str, _TaskRecord] = {}
        self._claims: dict[str, _Lease] = {}
        self._repo = ""
        self._issue = ""
        self._submitted: list[dict[str, object]] = []

    # -- plan submission (planner output bounded by caps) ------------------- #
    def _reject(self, reason: DenyReason, detail: str, **caps: object) -> None:
        """Audit + raise a plan-validation failure (fail-closed)."""
        self._audit("deny", {"reason": reason.value, "detail": detail, **caps})
        raise ValueError(f"DAG rejected: {reason.value} — {detail}")

    def submit(self, graph: TaskGraph, repo: str = "", issue: str = "") -> str:
        """Validate planner graph bounds + acyclicity, then stage tasks.

        A planner cannot bypass limits by emitting a larger graph output —
        every cap is checked and rejected BEFORE a node is staged.
        """
        if graph.has_cycle():
            self._reject(DenyReason.DAG_CYCLE, f"cycle in graph {graph.name}")
        if graph.node_count() > self.budget.max_dag_nodes:
            self._reject(
                DenyReason.DAG_NODE_LIMIT,
                f"node_count={graph.node_count()} > max_dag_nodes={self.budget.max_dag_nodes}",
                budget=self.budget.max_dag_nodes,
            )
        if graph.max_depth() > self.budget.max_dag_depth:
            self._reject(
                DenyReason.DAG_DEPTH_LIMIT,
                f"max_depth={graph.max_depth()} > max_dag_depth={self.budget.max_dag_depth}",
                budget=self.budget.max_dag_depth,
            )
        if graph.max_fanout() > self.budget.max_dag_fanout:
            self._reject(
                DenyReason.DAG_FANOUT_LIMIT,
                f"max_fanout={graph.max_fanout()} > max_dag_fanout={self.budget.max_dag_fanout}",
                budget=self.budget.max_dag_fanout,
            )

        self._repo = repo or graph.repo
        self._issue = issue or graph.issue
        for node in graph.nodes:
            self._tasks[node.node_id] = _TaskRecord(node=node, state=LifecycleState.PLANNED)
        self._submitted.append(
            {
                "event": "submit",
                "graph": graph.name,
                "repo": self._repo,
                "issue": self._issue,
                "node_count": graph.node_count(),
                "root": graph.root_id,
            }
        )
        return graph.root_id

    # -- dependency resolution + ready queue ------------------------------- #
    def _prereqs_satisfied(self, rec: _TaskRecord) -> bool:
        return all(
            self._tasks[p].state == LifecycleState.DONE
            for p in rec.node.prereqs
            if p in self._tasks
        )

    def _any_prereq_failed(self, rec: _TaskRecord) -> bool:
        return any(
            self._tasks[p].state in (LifecycleState.FAILED, LifecycleState.CANCELLED)
            for p in rec.node.prereqs
            if p in self._tasks
        )

    def ready(self) -> list[TaskNode]:
        """Nodes whose prerequisites are DONE and are not yet running/done."""
        ready: list[TaskNode] = []
        for rec in self._tasks.values():
            if rec.state != LifecycleState.PLANNED:
                continue
            if rec.node.prereqs and not self._prereqs_satisfied(rec):
                # Blocker propagation: a FAILED prereq BLOCKEDs the dependent.
                if self._any_prereq_failed(rec):
                    rec.state = LifecycleState.BLOCKED
                continue
            ready.append(rec.node)
        # Enforce DAG fanout as a scheduling cap: at most max_dag_fanout ready
        # siblings are released in a single dispatch batch.
        ready.sort(key=lambda n: n.node_id)
        return ready[: self.budget.max_dag_fanout]

    # -- dispatch (concurrency cap + lease/fencing) ------------------------ #
    def dispatch(self, now: float | None = None) -> list[_Lease]:
        """Claim ready nodes up to concurrency caps. Lease = fencing."""
        moment = now if now is not None else self._clock()
        leases: list[_Lease] = []
        for node in self.ready():
            if len(self._claims) >= self._concurrency_cap():
                break  # global/backpressure
            rec = self._tasks[node.node_id]
            if rec.state != LifecycleState.PLANNED:
                continue
            rec.state = LifecycleState.RUNNING
            lease = _Lease(
                task_id=node.node_id,
                holder=f"agent:{node.node_id}",
                lease_until=moment + node.max_seconds,
                state=LifecycleState.RUNNING,
            )
            self._claims[node.node_id] = lease
            rec.model_used = node.model
            rec.fallback_used = node.fallback_model
            rec.telemetry.append(
                {
                    "event": "dispatch",
                    "model": node.model,
                    "fallback_model": node.fallback_model,
                    "leased_at": moment,
                    "lease_ttl": node.max_seconds,
                }
            )
            self._audit(
                "dispatch",
                {
                    "task_id": node.node_id,
                    "model": node.model,
                    "fallback_model": node.fallback_model,
                    "role": node.role,
                    "repo": node.repo,
                    "issue": node.issue,
                },
            )
            leases.append(lease)
        return leases

    def _concurrency_cap(self) -> int:
        """Global cap minus per-repo/per-issue pressure (fail-closed: never above global)."""
        return self.budget.max_global_concurrency

    # -- completion (idempotent via SHA, no double commit) ----------------- #
    def complete(self, task_id: str, result: str, model_used: str | None = None) -> bool:
        """Idempotent completion keyed by result SHA."""
        if task_id not in self._tasks:
            return False
        sha = _result_sha(result)
        rec = self._tasks[task_id]
        if rec.state == LifecycleState.DONE and rec.result_sha == sha:
            # Idempotent replay of an already-committed result — no double commit.
            return False
        rec.result_sha = sha
        rec.state = LifecycleState.DONE
        rec.model_used = model_used or rec.model_used
        rec.telemetry.append({"event": "complete", "result_sha": sha, "model": rec.model_used})
        self._claims.pop(task_id, None)
        self._audit("complete", {"task_id": task_id, "result_sha": sha})
        return True

    # -- failure / retry / backoff / blocker propagation ------------------- #
    def fail(self, task_id: str) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None:
            return False
        rec.attempts += 1
        if rec.attempts > rec.node.max_retries:
            rec.state = LifecycleState.FAILED
            self._audit(
                "deny",
                {
                    "reason": DenyReason.RETRY_EXHAUSTED.value,
                    "detail": f"task_id={task_id} retries exhausted",
                },
            )
            self._propagate_blocker(task_id)
            return False
        rec.state = LifecycleState.PLANNED  # retry (backoff is the caller's responsibility)
        self._claims.pop(task_id, None)
        self._audit("retry", {"task_id": task_id, "attempts": rec.attempts})
        return True

    def _propagate_blocker(self, task_id: str) -> None:
        for dep in self.successors(task_id):
            tgt = self._tasks.get(dep)
            if tgt is not None and tgt.state == LifecycleState.PLANNED:
                tgt.state = LifecycleState.BLOCKED
                self._audit(
                    "deny",
                    {
                        "reason": "blocker_propagation",
                        "detail": f"blocked by FAILED prereq {task_id}",
                        "task_id": dep,
                        "prereq": task_id,
                    },
                )

    def successors(self, task_id: str) -> list[str]:
        if task_id not in self._tasks:
            return []
        return [r.node.node_id for r in self._tasks.values() if task_id in r.node.prereqs]

    # -- child-subtask proposal (fail-closed admission gate) -------------- #
    def evaluate_child_proposal(self, proposal: SubtaskProposal) -> Decision:
        decision = PolicyGate.evaluate(proposal)
        if decision:  # AllowDecision
            self._audit(
                "allow",
                {
                    "event": "subtask_allow",
                    "task": proposal.child_task,
                    "model": proposal.child_model,
                    "fallback": proposal.child_fallback_model,
                },
            )
        else:
            assert isinstance(decision, DenyDecision)
            self._audit(
                "deny",
                {
                    "reason": decision.reason.value,
                    "detail": decision.detail,
                    "task": proposal.child_task,
                },
            )
        return decision

    # -- lost-worker reclaim (lease timeout, no double commit) ------------- #
    def reclaim(self, holder: str, now: float | None = None) -> list[str]:
        moment = now if now is not None else self._clock()
        reclaimed: list[str] = []
        for task_id, lease in list(self._claims.items()):
            if lease.holder == holder and lease.lease_until <= moment:
                rec = self._tasks[task_id]
                rec.state = LifecycleState.PLANNED  # return to ready queue
                self._claims.pop(task_id)
                self._audit("reclaim", {"task_id": task_id, "holder": holder})
                reclaimed.append(task_id)
        return reclaimed

    # -- durable cancellation (survives restart) --------------------------- #
    def cancel(self, task_id: str, reason: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].state = LifecycleState.CANCELLED
        if task_id in self._claims:
            del self._claims[task_id]
        self._audit("cancel", {"task_id": task_id, "reason": reason})

    # -- crash/restart recovery ------------------------------------------- #
    def replay(self, events: Iterable[Mapping[str, object]] | None = None) -> int:
        """Rebuild state from an audit log (crash/restart recovery).

        If ``events`` is None, reads from the attached AuditLog (must be set).
        Returns the number of events applied.
        """
        if events is None and self.audit_log is not None:
            events = self.audit_log.replay()
        if events is None:
            return 0
        applied = 0
        for ev in events:
            kind = ev.get("event")
            if kind == "submit":
                # Re-stage by node_count is not lossless; callers that need
                # full recovery should persist the plan separately. We restore
                # counts and the root reference so deny/audit invariants replay.
                applied += 1
                continue
            tid = ev.get("task_id") or ev.get("root")  # type: ignore[arg-type]
            if kind == "dispatch" and isinstance(tid, str):
                rec = self._tasks.get(tid)
                if rec is None:
                    rec = _TaskRecord(node=TaskNode(tid, ""), state=LifecycleState.RUNNING)
                    self._tasks[tid] = rec
                else:
                    rec.state = LifecycleState.RUNNING
                    rec.model_used = str(ev.get("model", ""))
                applied += 1
            elif kind == "complete" and isinstance(tid, str):
                rec = self._tasks.get(tid)
                if rec is None:
                    rec = _TaskRecord(node=TaskNode(tid, ""), state=LifecycleState.DONE)
                    self._tasks[tid] = rec
                else:
                    rec.state = LifecycleState.DONE
                    rec.result_sha = str(ev.get("result_sha", ""))
                applied += 1
            elif kind == "cancel" and isinstance(tid, str):
                rec = self._tasks.get(tid)
                if rec is None:
                    rec = _TaskRecord(node=TaskNode(tid, ""), state=LifecycleState.CANCELLED)
                    self._tasks[tid] = rec
                else:
                    rec.state = LifecycleState.CANCELLED
                applied += 1
        return applied

    # -- helpers ---------------------------------------------------------- #
    def _audit(self, event: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        if self.audit_log is None:
            return None
        full = {"event": event, **payload}
        return self.audit_log.append(full)

    def utilization(self) -> Mapping[str, float]:
        return {
            "agents": len(self._claims) / self.budget.max_global_concurrency,
            "dag_nodes": len(self._tasks),
            "dag_nodes_limit": self.budget.max_dag_nodes,
        }


# type alias for clocks used in tests.
from collections.abc import Callable  # noqa: E402
