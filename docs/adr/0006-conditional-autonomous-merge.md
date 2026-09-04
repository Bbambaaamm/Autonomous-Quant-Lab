# ADR 0006 — Conditional autonomous merge after one Issue approval

## Status

Proposed by Issue #100. This ADR supersedes the one-time bootstrap publication path from ADR 0005.

## Decision

A maintainer makes one normal pre-merge human decision: authorize one concrete `type:implementation` Issue. Trusted default-branch workflow code records a deterministic SHA-256 binding of the Issue title, body, and classification. The authorization is conditional: it permits autonomous implementation and merge only while every exact-SHA safety and governance gate remains satisfied.

The normal flow is:

1. `agent-authorize.yml` records the exact Issue authorization and advances the Issue to `agent:running`.
2. `agent-builder.yml` runs Codex read-only and emits only a bounded patch artifact.
3. A credential-free validator and fresh-runner seal independently validate paths, modes, applicability, and checksum.
4. `agent-builder-publish.yml` alone receives `AGENT_PUBLISH_TOKEN`, publishes the exact sealed commit and Draft PR, creates durable two-sided Issue↔PR linkage, moves the pair to `agent:pr`, and releases Draft.
5. Existing authoritative CI and bounded fixer logic continue to run. Safe source failures remain bounded; unsafe, ambiguous, security-sensitive, governance-sensitive, dependency, migration, deployment, broker/execution/risk/live-trading, or exhausted cases fail closed to `agent:needs-human`.
6. The independent Codex Reviewer must PASS the exact current head SHA.
7. `agent-verify.yml` re-fetches mutable state, validates the current Issue authorization, exact linkage, current-main ancestry, all authoritative CI jobs, independent Codex PASS, lifecycle, and absence of `agent:needs-human`. If `main` moved it requests a normal branch update without force-push and requires a fresh exact-SHA cycle.
8. Only after both Issue and PR are `agent:verified`, `agent-verified-gate.yml` independently revalidates the same evidence and publishes the `agent-verified-gate` success status for that exact SHA.
9. `agent-auto-merge.yml` is the only trusted merge domain. It executes no PR-controlled code, uses an expected exact head SHA, re-fetches mutable state immediately before the merge API call, and fails closed if any evidence changed.
10. `agent-ruleset-sync.yml` updates the active `Protect main` ruleset so `agent-verified-gate` is a strict required status check while preserving the existing required checks, deletion/non-fast-forward protection, and zero bypass actors.

## Trust boundaries

Model execution never receives repository write or merge credentials. Validation and sealing run without model/API or GitHub write credentials. Publisher and merge credentials exist only in trusted default-branch control-plane jobs that do not execute PR-controlled code. The temporary Issue #100 bootstrap publisher is removed once the permanent path exists.

## Authorization invalidation

`agent-authorization-invalidation.yml` watches approval-relevant Issue edits and classification changes. A title/body/classification change makes the stored authorization stale and moves the linked pair to `agent:needs-human`. A new explicit human authorization is then required.

Any new PR head SHA invalidates prior exact-SHA CI/review/verification evidence by construction. Existing synchronize invalidation keeps lifecycle state from treating old verification as current.

## Current-main reconciliation

A behind PR is updated through GitHub's normal update-branch API with the expected current head SHA. Force-push is prohibited. Conflicts fail closed into `agent:needs-human`. A successful update creates a new SHA and therefore requires fresh CI, Codex review, verification, gate, and merge evidence.

## Merge gate

`agent-verified-gate` succeeds only when all of the following are current for the exact head SHA:

- unchanged Issue authorization hash;
- concrete implementation classification;
- valid two-sided durable linkage;
- open non-Draft PR against the default branch;
- current-main ancestry;
- all configured authoritative CI jobs successful;
- independent Codex Reviewer PASS;
- exact `agent:verified` lifecycle on Issue and PR;
- exact verification evidence;
- no `agent:needs-human`.

No owner or actor bypass is introduced.

## Operational resilience

Infrastructure/transient CI failures may be retried only within a bounded retry budget. This does not make security findings fixable: a real security/dependency/governance failure remains fail-closed.

## Non-goals

No live trading, broker/execution/risk expansion, production strategy behavior changes, unlimited fixer loop, CI weakening, Reviewer weakening, branch-protection weakening, or silent authorization scope expansion.
