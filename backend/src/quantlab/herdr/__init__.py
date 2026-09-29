"""Herdr v1.7 — swarm admission control, permissions & resource budgets.

FAIL-CLOSED, PAPER-only invariant layer over the Herdr swarm (#236, parent #229).

This sub-package is intentionally self-contained (stdlib-only) so it can be
unit-tested in PAPER isolation without coupling to the live QuantLab runtime or
to any prior Herdr worktree artifacts. The admission controller exposes:

* PlanBudget — conservative default caps (global/per-repo/per-issue agent caps,
  DAG node/depth/fanout limits, CPU/RAM/task-time/queue budgets). These are
  *admission-time ceilings*; they never weaken the runtime resource guards
  documented in docs/runtime-resource-budget.md (rss recycle, cgroup headroom).
* Tool/role allowlist — role-based tool allowlist; a child agent may only ever
  use tools that are a subset of its parent's tools AND within its role
  allowlist. Any tool unknown to both is denied (fail-closed).
* PAPER-only safety hooks — live-broker / trading / protected-path / secret /
  external-network tools are never admitted to a dynamic swarm regardless of role.
* Durable audit — every denial and cancellation is appended to an append-only
  JSONL log so denials are visible in Machine City and cancellations survive
  restart (durable cancellation).

See: docs/runtime-architecture-contract.md, docs/runtime-resource-budget.md
"""
