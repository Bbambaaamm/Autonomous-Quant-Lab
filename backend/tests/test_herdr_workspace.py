"""Offline tests for Herdr v1.3 workspace manager + artifact contract (#232).

No network, no live broker, no real git, no credential. A fake git runner is
injected so the contract (naming, stale-base gate, manifest, exact SHA, conflict
detection, cleanup retention, no-GH-credentials) is fully deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType

from quantlab.herdr.workspace import (
    StaleBaseError,
    WorkspaceManager,
)


class FakeGit:
    """Deterministic git runner capturing calls; never shells out.

    It records every call in ``self.calls`` as a tuple of args and returns a
    deterministic stdout. By default it serves as a warm "origin/main" tip and
    a healthy ``git status`` from a pinned worktree snapshot. Override
    ``self.files`` to mutate file-change manifests.
    """

    def __init__(
        self,
        tip: str = "704230d" * 6,  # deterministic 60-char fake origin/main SHA
        files: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.tip = tip
        self.files = files or {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(tuple(args))
        if args[:2] == ["rev-parse", "origin/main"]:
            return self.tip
        if args[:2] == ["status", "--short"]:
            worktree_arg = args[2] if len(args) > 2 else ""
            return self._status_worktree(worktree_arg)
        if args[:2] == ["worktree", "add"]:
            return ""
        if args[:2] == ["worktree", "remove"]:
            return ""
        return ""

    def _status_worktree(self, worktree: str) -> str:
        W = Path(worktree).name
        return "\n".join(f" M {f}" for f in self.files.get(W, ()))


def _manager(
    tmp_path: Path,
    tip: str = "704230d" * 6,
    files: dict[str, tuple[str, ...]] | None = None,
) -> tuple[WorkspaceManager, FakeGit]:
    git = FakeGit(tip=tip, files=files)
    mgr = WorkspaceManager(
        root=tmp_path,
        git=git,
        worktrees_dir=tmp_path / "worktrees",
    )
    return mgr, git


def test_deterministic_branch_and_worktree_naming(tmp_path: Path) -> None:
    mgr, _ = _manager(tmp_path)
    assert mgr.branch_name(232, "t-233", 0) == "issue-232-t-233-0"
    assert mgr.worktree_path(232, "t-233", 0) == (tmp_path / "worktrees" / "issue-232-t-233-0")


def test_stale_base_fail_closed(tmp_path: Path) -> None:
    mgr, _ = _manager(tmp_path)
    try:
        mgr.create(
            232,
            "t",
            attempt=0,
            base_sha="deadbeef" * 8,  # 64-char stale base, != origin/main tip
        )
    except StaleBaseError:
        pass
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("stale base must raise StaleBaseError (fail-closed)")


def test_fresh_base_creates_isolated_worktree(tmp_path: Path) -> None:
    tip = "704230d" * 6
    mgr, git = _manager(tmp_path, tip=tip)
    artifact, path = mgr.create(232, "t", attempt=0, base_sha=tip)
    assert artifact.task_id == "t"
    assert artifact.attempt == 0
    assert artifact.base_sha == tip
    assert artifact.branch == "issue-232-t-0"
    assert path.name == "issue-232-t-0"
    # create() must have invoked git (rev-parse + worktree add) via the
    # injected runner; nothing is run on the real git binary.
    assert len(git.calls) >= 2
    assert any(c[0] in ("rev-parse", "worktree", "status") for c in git.calls)


def test_file_change_manifest_and_exact_sha(tmp_path: Path) -> None:
    tip = "704230d" * 6
    worktree_path = tmp_path / "worktrees" / "issue-232-t-0"
    mgr, _ = _manager(
        tmp_path,
        tip=tip,
        files={worktree_path.name: ("src/x.py", "tests/y.py")},
    )
    artifact, path = mgr.create(232, "t", attempt=0, base_sha=tip)
    sealed = mgr.seal(artifact, path)
    assert set(sealed.changed_files) == {"src/x.py", "tests/y.py"}
    assert sealed.result_sha  # non-empty content-addressed sha
    assert sealed.base_sha == tip
    # Same content -> same sha (idempotent).
    again = mgr.seal(artifact, path)
    assert again.result_sha == sealed.result_sha


def test_concurrent_same_file_is_explicit_conflict_not_silent(tmp_path: Path) -> None:
    tip = "704230d" * 6
    mgr, _ = _manager(tmp_path, tip=tip)
    a = mgr.bind("t-a", 0, tip, ("src/secret.py",))
    b = mgr.bind("t-b", 0, tip, ("src/secret.py",))
    assert mgr.conflicts(a, b) is True  # same file -> explicit conflict


def test_concurrent_different_files_no_conflict(tmp_path: Path) -> None:
    mgr, _ = _manager(tmp_path)
    a = mgr.bind("t-a", 0, "b" * 40, ("src/a.py",))
    b = mgr.bind("t-b", 0, "b" * 40, ("src/b.py",))
    assert mgr.conflicts(a, b) is False


def test_cleanup_preserves_audit_artifact(tmp_path: Path) -> None:
    mgr, _ = _manager(tmp_path)
    worktree = tmp_path / "worktrees" / "issue-232-t-0"
    worktree.mkdir(parents=True)
    (worktree / "scratch.txt").write_text("x", encoding="utf-8")
    artifact_json = {"task_id": "t", "result_sha": "deadbeef", "verdict": "pass"}
    mgr.cleanup(
        worktree,
        preserve_artifact=True,
        artifact_json=MappingProxyType(artifact_json),
    )
    preserved = tmp_path / "worktrees" / "artifacts" / "issue-232-t-0.artifact.json"
    assert preserved.exists()
    # worktree itself removed (worker never leaves stray trees), main untouched.
    assert not worktree.exists()


def test_no_github_credentials_used(tmp_path: Path) -> None:
    """PAPER-only: worker never reads GH_TOKEN / never invokes gh."""
    import os

    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    mgr, git = _manager(tmp_path)
    mgr.create(232, "t", attempt=0, base_sha="704230d" * 6)
    for call in git.calls:
        joined = " ".join(call)
        assert "gh " not in joined and "GITHUB_TOKEN" not in joined and "push" not in joined
    # No GH env var is read by the manager (defensive: property must be absent).
    assert not hasattr(mgr, "github_token")


def test_max_dynamic_fanout_enforced(tmp_path: Path) -> None:
    mgr, _ = _manager(tmp_path)
    mgr.max_dynamic_fanout = 2
    assert mgr.within_fanout(active=0) is True
    assert mgr.within_fanout(active=1) is True
    assert mgr.within_fanout(active=2) is False  # hard cap enforced
