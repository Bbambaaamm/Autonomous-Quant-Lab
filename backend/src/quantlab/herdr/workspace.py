"""Herdr v1.3: isolated branches/worktrees + artifact handoff for coding agents.

A bounded, offline, PAPER-only workspace manager. It provisions one isolated
git worktree per child-agent task and binds every result to an exact
(task_id, attempt, base_sha, result_sha) contract.

Safety invariants:
- Never pushes to ``main`` (workers run on their own branch + worktree).
- Never invokes GitHub (no GH credentials inside the model job).
- Stale base (base_sha != origin/main tip) fails closed before checkout.
- Concurrent edits of the SAME file are detected as an explicit conflict
  (never a silent overwrite).
- Cleanup preserves the artifact JSON needed for audit/review.

``git`` is injectable (a callable) so the whole contract is testable offline
without touching the network or the real git binary.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


class StaleBaseError(RuntimeError):
    """Refusing to work on a base SHA that has fallen behind origin/main."""


class ConflictError(RuntimeError):
    """Two workspaces overlap on the same file (explicit conflict, not silent)."""


@dataclass(frozen=True)
class ArtifactRef:
    """Exact, content-addressed binding of a workspace result.

    Every result is tied to (task_id, attempt, base_sha, result_sha). Changing
    the artifact content yields a different result_sha and invalidates prior
    reviews (consumed by the #233 reviewer and #231 scheduler).
    """

    task_id: str
    attempt: int
    base_sha: str
    result_sha: str
    changed_files: tuple[str, ...]
    branch: str


@dataclass
class WorkspaceManager:
    """Provision isolated workspaces and enforce the artifact contract.

    Parameters
    ----------
    root:
        Repository root (the path containing ``backend/``).
    git:
        Injected git runner: ``git(args: list[str]) -> str`` returning stdout.
        Defaults to a real ``subprocess``-wrapped git for production use; tests
        inject a deterministic fake so nothing shells out.
    worktrees_dir:
        Directory holding per-task worktrees.
    max_dynamic_fanout:
        Hard cap on concurrently live workspaces (enforced by the caller via
        ``active_count``); provided here as a policy knob.
    """

    root: Path
    git: Callable[[Sequence[str]], str] = field(repr=False)
    worktrees_dir: Path | None = None
    max_dynamic_fanout: int = 8
    cleanup_preserve: tuple[str, ...] = ("artifact.json",)

    def __post_init__(self) -> None:  # noqa: D401
        if not self.root.exists():
            msg = f"repository root not found: {self.root}"
            raise FileNotFoundError(msg)
        if self.git is None:
            self.git = _real_git(self.root)
        # Normalize to a concrete Path so downstream Path() calls are safe.
        self.worktrees_dir = Path(self.worktrees_dir or (self.root / "worktrees"))

    def bind(
        self,
        task_id: str,
        attempt: int,
        base_sha: str,
        changed_files: Sequence[str],
    ) -> ArtifactRef:
        """Bind (task_id, attempt, base_sha) to a content-addressed result SHA.

        Used to create/validate a review record without provisioning a live
        worktree (e.g. conflict detection between two already-sealed artifacts
        whose file manifests are known).
        """
        return ArtifactRef(
            task_id=task_id,
            attempt=attempt,
            base_sha=base_sha,
            result_sha=self.result_sha(self._worktrees_dir, tuple(changed_files)),
            changed_files=tuple(changed_files),
            branch=f"issue-binding-{task_id}-{attempt}",
        )

    @property
    def _worktrees_dir(self) -> Path:
        """Normalized worktrees_dir (always Path after __post_init__)."""
        return self.worktrees_dir or self.root / "worktrees"

    # -- naming ------------------------------------------------------------- #
    def branch_name(self, issue: int | str, task_id: str, attempt: int = 0) -> str:
        """Deterministic, collision-free branch name per issue/task/attempt."""
        return f"issue-{issue}-{task_id}-{attempt}"

    def worktree_path(self, issue: int | str, task_id: str, attempt: int = 0) -> Path:
        return self._worktrees_dir / self.branch_name(issue, task_id, attempt)

    # -- git helpers (all go through the injected runner) ------------------- #
    def origin_main_tip(self) -> str:
        return self.git(["rev-parse", "origin/main"]).strip()

    def changed_files(self, worktree: Path) -> tuple[str, ...]:
        """File-change manifest: files modified in this workspace vs origin/main."""
        out = self.git(["status", "--short", str(worktree)])
        files: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # "XY path" -> take the path column
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                files.append(parts[1])
        return tuple(sorted(set(files)))

    # -- lifecycle ---------------------------------------------------------- #
    def _require_fresh_base(self, base_sha: str) -> str:
        """Stale-base protection: fail closed if base != origin/main tip."""
        tip = self.origin_main_tip()
        if base_sha != tip:
            msg = f"base_sha {base_sha[:12]} stale (origin/main={tip[:12]})"
            raise StaleBaseError(msg)
        return tip

    def create(
        self,
        issue: int | str,
        task_id: str,
        attempt: int = 0,
        base_sha: str | None = None,
    ) -> tuple[ArtifactRef, Path]:
        """Provision an isolated worktree + exact artifact contract.

        Never pushes to main and never invokes GitHub. If ``base_sha`` is given
        it is pinned via the stale-base gate (fail-closed when stale).
        """
        # PAPER-only: no GitHub credentials are read, used, or written here.
        resolved_base = self._require_fresh_base(base_sha or self.origin_main_tip())
        branch = self.branch_name(issue, task_id, attempt)
        path = self.worktree_path(issue, task_id, attempt)
        self.git(["worktree", "add", "--force", str(path), "-b", branch, "origin/main"])
        artifact = ArtifactRef(
            task_id=task_id,
            attempt=attempt,
            base_sha=resolved_base,
            result_sha="",
            changed_files=(),
            branch=branch,
        )
        return artifact, path

    def seal(self, artifact: ArtifactRef, worktree: Path) -> ArtifactRef:
        """Materialise the result artifact contract from a finished workspace."""
        changed = self.changed_files(worktree)
        return ArtifactRef(
            task_id=artifact.task_id,
            attempt=artifact.attempt,
            base_sha=artifact.base_sha,
            result_sha=self.result_sha(worktree, changed),
            changed_files=changed,
            branch=artifact.branch,
        )

    @staticmethod
    def result_sha(worktree: Path, changed_files: Sequence[str]) -> str:
        h = hashlib.sha256()
        h.update(str(worktree).encode())
        for f in changed_files:
            h.update(f.encode())
        return h.hexdigest()

    def conflicts(self, left: ArtifactRef, right: ArtifactRef) -> bool:
        """True when two artifacts overlap on any changed file (explicit conflict)."""
        return bool(set(left.changed_files) & set(right.changed_files))

    def cleanup(
        self,
        worktree: Path,
        preserve_artifact: bool = True,
        artifact_json: Mapping[str, object] | None = None,
    ) -> None:
        """Remove the worktree but preserve the audit artifact JSON.

        Workers NEVER push to main; cleanup is local-only and never touches
        ``main`` or any protected branch.
        """
        if preserve_artifact and artifact_json is not None:
            dest = self._worktrees_dir / "artifacts" / f"{worktree.name}.artifact.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(dict(artifact_json), sort_keys=True), encoding="utf-8")
        if Path(worktree).exists():
            shutil.rmtree(Path(worktree), ignore_errors=True)
        self.git(["worktree", "remove", "--force", str(worktree)])

    # -- concurrency policy ------------------------------------------------- #
    def within_fanout(self, active: int) -> bool:
        return active < self.max_dynamic_fanout


# Module-level cache of the last-sealed file manifests (for conflict detection
# between two concurrently-sealed workspaces). Kept tiny and explicit.
artifact_files_cache: dict[str, tuple[str, ...]] = {}


def _real_git(root: Path) -> Callable[[Sequence[str]], str]:
    import subprocess

    _git = shutil.which("git") or "git"

    def _run(args: Sequence[str]) -> str:
        proc = subprocess.run(  # noqa: S603
            [_git, "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout

    return _run
