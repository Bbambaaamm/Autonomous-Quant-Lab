"""Herdr v1.4: independent reviewer gate + deterministic validators.

A reviewer does NOT trust a worker's text claim of success. It validates real
artifacts/evidence and is bound to an exact artifact SHA. The reviewer itself
never mutates the candidate artifact (read-only input) and never escalates
beyond its role/model policy.

Bounded + offline + PAPER-only:
- No live trading, no credential handling, no subprocess/shell, no network.
- Deterministic validators; PASS / BLOCK / NEEDS_REPLAN verdicts.
- Bounded fix attempts -> blocked/needs-human when exhausted.
- Exact artifact/SHA binding; a changed artifact invalidates prior review.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class ReviewVerdict(StrEnum):
    """Reviewer decision."""

    PASS = "pass"
    BLOCK = "block"
    NEEDS_REPLAN = "needs_replan"


class ReviewReason(StrEnum):
    """Deterministic reason codes for a non-PASS verdict."""

    WORKER_TEXT_ONLY = "worker_text_pass_without_evidence"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_INVALID = "evidence_invalid"
    VALIDATOR_FAILED = "validator_failed"
    STALE_ARTIFACT = "stale_artifact_invalidates_prior_review"
    FIX_ATTEMPTS_EXHAUSTED = "fix_attempts_exhausted"


# A validator inspects the immutable evidence map and returns None when it
# passes, or a ReviewReason describing the specific failure otherwise.
Validator = Callable[[Mapping[str, str]], ReviewReason | None]


@dataclass(frozen=True)
class ArtifactRef:
    """Immutable binding of a review to a task attempt + exact artifact SHA.

    Every review MUST be tied to (task_id, attempt, base_sha, result_sha).
    A new result_sha invalidates any prior review (stale-artifact rejection).
    """

    task_id: str
    attempt: int
    base_sha: str
    result_sha: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class ReviewInput:
    """Read-only review input. The reviewer never mutates the candidate.

    ``evidence`` maps a category -> string verdict, e.g.
    {"tests": "PASSED 28 in 0.10s", "lint": "clean", "build": "ok"}.
    ``worker_claim`` is the worker's own text claim and is NEVER trusted on its
    own.
    """

    spec: str
    artifacts: Mapping[str, str]  # path -> content snapshot (immutable view)
    evidence: Mapping[str, str]
    worker_claim: str
    actor_sha: str | None = None


@dataclass(frozen=True)
class ReviewRecord:
    """A single review decision bound to an exact artifact."""

    verdict: ReviewVerdict
    reason: ReviewReason | str
    artifact: ArtifactRef
    reviewer: str  # role/model policy token
    details: tuple[str, ...] = ()


@dataclass
class ReviewerGate:
    """Independent reviewer gate for child-agent outputs (#233).

    Conservative fail-closed defaults. ``clock`` is injectable for offline
    tests; the default never shells out.
    """

    max_fix_attempts: int = 3
    required_evidence: tuple[str, ...] = ("tests", "lint", "build")
    # Nous routing policy for the reviewer role (no single hardcoded model).
    reviewer_models: dict[str, str] = field(
        default_factory=lambda: {
            "longcat": "longcat-reviewer",
            "step": "step-reviewer",
            "solar": "solar-reviewer",
        }
    )

    def resolve_model(self, role: str) -> str:
        """Role/task -> model policy adapter (delegates, never hardcodes)."""
        return self.reviewer_models.get(role, "longcat-reviewer")

    def bind(
        self,
        task_id: str,
        attempt: int,
        base_sha: str,
        artifacts: Mapping[str, str],
        changed_files: Sequence[str],
    ) -> ArtifactRef:
        """Create the exact artifact binding (content-addressed result SHA)."""
        return ArtifactRef(
            task_id=task_id,
            attempt=attempt,
            base_sha=base_sha,
            result_sha=self.result_sha(artifacts),
            changed_files=tuple(changed_files),
        )

    @staticmethod
    def result_sha(artifacts: Mapping[str, str]) -> str:
        h = hashlib.sha256()
        for key in sorted(artifacts):
            h.update(key.encode())
            h.update(artifacts[key].encode())
        return h.hexdigest()

    def review(
        self,
        inp: ReviewInput,
        artifact: ArtifactRef,
        validators: Iterable[Validator] = (),
        prev_artifact: ArtifactRef | None = None,
    ) -> ReviewRecord:
        """Run deterministic review.

        - exact SHA binding: a stale artifact (different result_sha than prev)
          invalidates the prior review; a worker may not re-review a moved target.
        - worker text "PASS" without real evidence -> BLOCK (fault-injection guard).
        - required evidence categories must be present AND non-empty.
        - each deterministic validator runs; first failure -> BLOCK.
        """
        reviewer = self.resolve_model("longcat")

        # Exact artifact/SHA binding: stale artifact invalidates prior review.
        if prev_artifact is not None and prev_artifact.result_sha != artifact.result_sha:
            return ReviewRecord(
                ReviewVerdict.BLOCK,
                ReviewReason.STALE_ARTIFACT,
                artifact,
                reviewer,
                (
                    f"prior artifact {prev_artifact.task_id}#{prev_artifact.attempt}"
                    f"@{prev_artifact.result_sha[:8]}",
                    f"current artifact {artifact.task_id}#{artifact.attempt}"
                    f"@{artifact.result_sha[:8]}",
                    "changed artifact invalidates prior review",
                ),
            )

        # Worker text-only "PASS" with no evidence is never trusted.
        claim = (inp.worker_claim or "").strip().lower()
        if claim in {"pass", "ok", "success", "done"} and not inp.evidence:
            return ReviewRecord(
                ReviewVerdict.BLOCK,
                ReviewReason.WORKER_TEXT_ONLY,
                artifact,
                reviewer,
                (
                    "worker claimed success but provided NO evidence; "
                    "text claim alone is insufficient",
                ),
            )

        # Required evidence categories must be present and non-empty.
        missing = [k for k in self.required_evidence if not inp.evidence.get(k)]
        if missing:
            return ReviewRecord(
                ReviewVerdict.BLOCK,
                ReviewReason.EVIDENCE_MISSING,
                artifact,
                reviewer,
                (f"missing required evidence categories: {missing}",),
            )

        # Deterministic validators — value-level checks (key presence handled above).
        for v in validators:
            failed = v(inp.evidence)
            if failed is not None:
                reason = failed if failed in ReviewReason._value2member_map_.values() else ReviewReason.VALIDATOR_FAILED  # noqa: E501
                return ReviewRecord(
                    ReviewVerdict.BLOCK,
                    reason,
                    artifact,
                    reviewer,
                    (f"validator failed: {failed}",),
                )

        return ReviewRecord(ReviewVerdict.PASS, "", artifact, reviewer, ())

    def redispatch(self, attempt: int) -> ReviewVerdict | None:
        """Bounded fix attempts. Returns NEEDS_REPLAN when budget is exhausted.

        Returns None while attempts remain within budget (re-dispatch allowed);
        when the budget is exhausted the gate fails closed to human review.
        """
        if attempt >= self.max_fix_attempts:
            return ReviewVerdict.NEEDS_REPLAN
        return None

    # -- built-in deterministic validators ----------------------------------- #
    def tests_pass(self, min_passed: int = 1) -> Validator:
        """EVIDENCE_INVALID when the tests evidence is present but not a PASSED line."""
        def _v(evidence: Mapping[str, str]) -> ReviewReason | None:
            line = (evidence.get("tests") or "").upper()
            if "PASSED" not in line:
                return ReviewReason.EVIDENCE_INVALID
            digits = [c for c in line if c.isdigit()]
            if digits and min_passed > 0 and int("".join(digits)) < min_passed:
                return ReviewReason.EVIDENCE_INVALID
            return None
        return _v

    def lint_clean(self) -> Validator:
        def _v(evidence: Mapping[str, str]) -> ReviewReason | None:
            val = (evidence.get("lint") or "").strip().lower()
            return None if val in {"clean", "ok", "passed"} else ReviewReason.EVIDENCE_INVALID
        return _v
