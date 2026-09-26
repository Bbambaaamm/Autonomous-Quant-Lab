"""Herdr v1.2 — dependency-aware scheduler + dynamic child-agent lifecycle (#231).

Parent: #229. Reuses the TaskGraph/DAG semantics from the #230 foundation and
the fail-closed admission policy from #236 as a thin inline PolicyGate.

PAPER-only invariant: a child agent may never escalate tools/permissions beyond
its parent and is never admitted with a live-trading / broker tool. All admission
decisions and cancellations are durably audited to an append-only JSONL log so
they are visible in Machine City and survive restart.

This module is stdlib + quantlab imports only, so the contract is fully
offline-testable (clock and audit log are injectable).
"""

from __future__ import annotations
