"""Herdr v1.3: isolated branches/worktrees + artifact handoff.

Bounded, offline, PAPER-only. The WorkspaceManager provisions an isolated git
worktree per coding task and binds the result to an exact artifact SHA. It never
invokes GitHub (no GH credentials inside the model job) and never pushes to
``main``. See herdr/workspace.py.
"""

from __future__ import annotations

__all__ = ["workspace"]
__version__ = "1.3.0"
