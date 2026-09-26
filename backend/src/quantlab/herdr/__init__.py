"""Herdr orchestration layer.

Bounded, offline, PAPER-only. Each sub-version is a self-contained slice that
does not alter live trading, credentials, protected-branch, or CI/merge gates.

- v1.2: dependency-aware scheduler + dynamic child-agent lifecycle (#231)
- v1.4: independent reviewer gate + validators + bounded redispatch (#233)
"""
from __future__ import annotations

__all__ = ["reviewer", "scheduler"]
__version__ = "1.4.0"
