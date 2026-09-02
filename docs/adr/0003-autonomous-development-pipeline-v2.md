# ADR 0003: Bounded CI remediation and independent Codex gate

- Status: Accepted
- Date: 2026-09-02
- Issue: #88

## Context

V1 provides trusted linkage, exact-SHA CI and human review but intentionally cannot classify or
repair failures and has no independent automated review. Combining model credentials, untrusted
execution and repository write access would create an unacceptable supply-chain boundary.

## Decision

Adopt versioned v2 configuration and deterministic exact-evidence markers. Permit at most two
free-form code-fix commits. Split generation, validation and publication into separate jobs with
mutually exclusive credentials, immutable action pins, exact checkouts and final TOCTOU reads.
Deny governance, dependencies, security, schema, deployment and trading execution paths. Bind the
trusted publisher to a validated checksum and exact path set, and select validation commands only
from a code-owned normalized failure-class map.

Run independent Codex review after exact-head green CI and require its structured exact-SHA PASS
in addition to—not instead of—the v1 human/native exact-SHA review. Any missing secret, ambiguous
classification, stale evidence, BLOCK outside the safe budget, or validation mismatch fails
closed. State labels remain the v1 machine; comments are the v2 audit ledger. Merge remains
human-only.

The official Codex Action is immutably pinned to
`86365089eb2b84e0a8fb0717b304f8bdcb13b20e`. Its declared `permission-profile`, `output-file`,
`output-schema`, `openai-api-key`, and narrow `allow-bot-users` inputs are used directly. Reviewer scope
is resolved from matching PR-body and two-sided durable v1 linkage while both objects are exactly
`agent:pr`. Transition into that state dispatches review re-evaluation; a PASS calls the verifier
with explicit PR/SHA inputs rather than interpreting a nested `workflow_run` payload.

## Consequences

The pipeline can remediate narrow code failures without granting one domain all three powers.
It adds GitHub Actions usage and requires the repository secret `OPENAI_API_KEY`. A missing secret
stops review/fixing and therefore verification. No auto-merge, deployment, staging, v3/v4 or live
execution capability is introduced.

## Second blocking-audit hardening

CI diagnosis is now a bounded, aggressively redacted failed-job log excerpt whose checksum and
source run/job/attempt are part of exact-SHA evidence. Missing safe evidence and dependency-lock
failures require a human. Codex reads workspace-local deterministic prompt files only. Reviewer
BLOCK uses an explicit reusable-controller call with PR/SHA inputs and repeats all trusted
lifecycle/linkage checks; no second-level `workflow_run.pull_requests` inference is permitted. Both
Codex actions narrowly allow only `github-actions[bot]` through `allow-bot-users`.

## Third security audit decisions

The controller distinguishes exact-head CI success, failure, in-progress, and ambiguous/no-evidence states after linkage. Fix publication rejects forks, treats the validated head ref only as a checked environment argument, includes staged additions in the artifact, uses a non-`GITHUB_TOKEN` publication credential to trigger the next CI cycle, and reconciles the two-commit budget from durable commit trailers. Protected paths and test/log patterns prevent mixed safety jobs from entering free-form repair. Trusted default-branch governance plus the exact base SHA bound the independent review, and exact-head finalizers escalate generation, validation, or publication failures without allowing stale runs to affect newer commits.
