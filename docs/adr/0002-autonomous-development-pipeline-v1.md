# ADR-0002 — GitHub-native Autonomous Development Pipeline v1

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

Future development agents need a bounded, auditable handoff protocol. An open Issue
is not sufficient authorization, roadmap Issues are specifications rather than
implementation work, and an agent label must never become a substitute for CI,
review, branch protection, or human merge authority.

## Decision

Use mutually exclusive GitHub labels as the visible state machine, an explicit
`type:implementation` classification plus maintainer dispatch as human opt-in, and
one exact `Agent-Issue: #N` PR-body marker as linkage. Manual transitions run in a
least-privilege workflow. A separate `workflow_run` verifier reads authoritative CI
metadata from trusted default-branch code and may mark the exact reviewed head SHA
as `agent:verified`. Invalid or ambiguous evidence causes no write.

The complete operational contract and recovery procedure are defined in
`docs/autonomous-development-pipeline.md`.

## Consequences

- GitHub labels, comments, reviews, commits, and workflow runs remain the complete
  audit trail; no hidden state store is introduced.
- Label provisioning and transitions require GitHub write API access, but PR code is
  never executed with that token.
- CI job names are a reviewed configuration contract.
- A partial GitHub API failure can leave Issue and PR states mismatched; this fails
  closed and requires an audited maintainer recovery.
- v1 ends at human merge and does not implement a builder, fixer, deployment, Issue
  closure, or merge automation.

## Invariants protected

- explicit human authorization and human merge;
- epic/roadmap protection;
- authoritative exact-SHA CI and review;
- least privilege and no `pull_request_target` execution;
- fail-closed transitions and immutable GitHub audit evidence;
- all paper-only, causal, financial, and execution-path invariants in ADR-0001.

## Supersedes / Superseded by

- Supersedes: none.
- Superseded by: none.

## Related Issues / PRs

- #82 — Autonomous Development Pipeline v1
