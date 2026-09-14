# ADR 0007 — Trusted control-plane remediation path

## Status

Candidate implementation complete; trust-root adoption is blocked pending an
explicit, independently reviewed maintainer decision.

## Context

The normal autonomous Builder deliberately cannot modify `.github`, agent governance, credentials, deployment controls, security policy, or other protected control-plane surfaces. That is a required safety property. A consequence is a bootstrap deadlock when the trusted pipeline itself needs repair: the ordinary autonomous verifier and `agent-verified-gate` cannot safely certify a candidate that changes the machinery used to produce that certification.

Temporarily removing `agent-verified-gate` from the `main` ruleset is not an acceptable recurring maintenance procedure. It is easy to forget to restore, creates unnecessary operational risk, and makes pipeline repair dependent on repeated repository-admin configuration changes.

## Decision

Introduce a two-workflow trusted maintenance path:

1. `.github/workflows/agent-control-plane-remediation-request.yml` — a secret-free manual request workflow;
2. `.github/workflows/agent-control-plane-remediation.yml` — the secret-bearing trusted follower, triggered only by `workflow_run` after a successful request run from the default branch.

The manual request binds an already authorized concrete `type:implementation` Issue, exact PR number, exact 40-character head SHA, and non-empty human maintenance reason. The dispatching actor must have `write`, `maintain`, or `admin` repository permission.

The request workflow has no `OPENAI_API_KEY` or `AGENT_PUBLISH_TOKEN`. The follower rejects any request run that did not execute from the repository default branch, did not originate from the exact request workflow path, or whose actor/run identity does not match the request artifact. Consequently a collaborator cannot modify a PR-branch copy of the maintenance workflow and then use that candidate revision to receive secrets.

### Allowed scope

The maintenance path is fail-closed and accepts only bounded agent control-plane surfaces:

- `.github/workflows/agent-*.yml`;
- `.github/scripts/agent-*.cjs`;
- `.github/agent-pipeline.json`;
- `docs/autonomous-development-pipeline.md`;
- the autonomous-pipeline ADRs explicitly allowlisted by the workflow.

For renamed files, both the destination path and `previous_filename` must be allowlisted. The workflow also requires GitHub's enumerated file count to equal `pr.changed_files` and rejects PRs above the pull-files API's 3,000-file ceiling, so a truncated file list can never be treated as complete.

The maintenance path does not authorize application, trading, broker/execution, database migration, dependency, deployment, or general repository changes.

### Required evidence

The secret-free request and the trusted follower independently require:

1. current exact Issue authorization;
2. open non-draft PR against the default branch;
3. exact unchanged PR head SHA and Issue marker;
4. no conflicting durable Issue ↔ PR linkage;
5. current-main ancestry (the PR may not be behind `main`);
6. the newest authoritative pull-request CI run for the exact SHA to be completed successfully with all nine required jobs green — active newer runs are never hidden by pre-filtering for completed runs;
7. a complete, non-truncated changed-file enumeration with every source and destination path inside the maintenance allowlist;
8. an exact or crash-recoverable maintenance lifecycle.

The request may bind either an existing `agent:pr` pair or an `agent:needs-human` recovery. If a previous API attempt changed only one side, the next request may resume the allowed previous→next partial state and finish it deterministically. `agent:needs-human` is never silently overwritten: only after the independent review returns PASS does the trusted follower perform the explicit, audited recovery to `agent:pr`. Any unrelated/conflicting agent state fails closed.

Lifecycle writes use a single `setLabels` call per object after a fresh state-plan check, preserving all non-agent labels and writing exactly one target `agent:*` state. Pair-level partial completion remains retryable because subsequent evaluations accept only the explicitly allowed previous/next transition and re-fetch before continuing.

A separate read-only Codex job receives the candidate as untrusted data and the default-branch governance as its trusted baseline. It must return `PASS`, zero findings, scope consistency, no test/governance weakening, and unchanged paper-only/live-trading safety. The model job receives no GitHub write credential.

GitHub may omit `bypass_actors` from ruleset responses when the caller lacks
ruleset-write authority. Therefore a dedicated audit job uses the existing
maintenance authority for two fixed GET endpoints only. It checks out and
executes no repository or candidate code while that credential is present. The
result is a provenance-bound artifact; missing or null `bypass_actors` is
unknown and blocks. The Codex reviewer receives only this bounded evidence, not
the credential. All later mutation decisions bind the evidence to the request
run and attempt and retain every configured check name, GitHub App binding, and
additional ruleset restriction.

### Serialization, gate and merge

The request workflow is serialized by exact PR/SHA. Its trusted follower uses the request run title, which is deterministically bound to that same PR/SHA, as a workflow-level concurrency key with `cancel-in-progress: false`. Duplicate valid requests therefore serialize rather than racing lifecycle or evidence publication.

After PASS and any explicitly authorized recovery, a separate trusted gate job repeatedly re-fetches mutable GitHub state. It permits only crash-recoverable `agent:pr → agent:verified` progress, rejects `agent:needs-human`, requires an exact `agent:verified` pair before publishing evidence, and requires exactly one bot-authored exact-SHA maintenance review marker after publication.

The gate publishes the same required status context used by the normal path:

`agent-verified-gate`

The ruleset therefore remains unchanged and permanently enabled.

A final merge job uses `AGENT_PUBLISH_TOKEN` only after the trusted gate succeeds. It revalidates the exact head, current authorization, two-sided durable linkage, complete allowlisted file enumeration including rename sources, newest authoritative CI, current-main ancestry, verified lifecycle, exactly one bot-authored maintenance evidence marker, and bot-authored successful `agent-verified-gate`. It then performs the complete evaluation a second time immediately before the exact-head merge request. Any stale, active, ambiguous, incomplete, or halted condition fails closed.

## Consequences

- Normal application Issues still use the one-authorization zero-click Builder → CI → Reviewer → verifier → gate → auto-merge path.
- Protected pipeline changes still cannot be generated by the autonomous Builder.
- Pipeline maintenance requires one explicit human maintenance request, but no ruleset weakening, bypass actor, manual state-transition sequence, or recurring manual gate toggling.
- Candidate workflow revisions cannot receive maintenance secrets.
- Duplicate maintenance requests cannot create duplicate exact-SHA gate evidence.
- Transient partial metadata writes are retryable without manual label surgery.
- The same permanent `agent-verified-gate` remains the branch-protection backstop for both normal autonomous delivery and trusted control-plane remediation.
- Live trading remains out of scope and paper-only invariants are unchanged.

## Verification and adoption status

Local Node tests are mocked controller/guard regressions only. GitHub-hosted CI,
the credential-isolated ruleset GET, and real Codex review are separate evidence
and cannot be inferred from local success. A PASS review artifact is required
before gate evidence can exist; a BLOCK review is retained for diagnosis but
cannot create PASS or gate evidence.

This candidate cannot repair the follower already executing from `main`:
`workflow_run` deliberately loads that trusted default-branch revision. The
legitimate bootstrap is an independently reviewed maintainer trust-root change
that adopts this follower onto `main` through the repository's protected change
process. Until a maintainer explicitly selects and performs that one-time
adoption without forged statuses, bypass actors, removed checks, force pushes,
or candidate execution with secrets, **adoption remains BLOCKED**. No candidate
workflow run or this ADR self-certifies adoption.
