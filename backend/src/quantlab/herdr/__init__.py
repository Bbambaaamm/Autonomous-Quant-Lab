"""Herdr v1.x — durable task orchestration primitives.

This package hosts the Herdr swarm primitives that live *alongside* the
existing QuantLab runtime.  The QuantLab backend (api.py / trading.py /
phase4.py / security.py) is intentionally **untouched** here — see
``.github/agent-pipeline.json`` ``protectedPaths``.

Issue #230: Herdr v1.1 — Durable TaskGraph schema + deterministic planner
contract.  This module is a bounded, paper-trading-only implementation slice:
it defines the canonical TaskGraph/DAG contract, a deterministic graph hash,
validation (cycle / unknown-dependency / duplicate-node rejection), bounded
max nodes/depth/fanout, and restart-recovery from an immutable event log.
"""