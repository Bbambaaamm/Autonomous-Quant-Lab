# ADR 0007 — Trusted control-plane remediation path

## Status

Accepted for the Autonomous Development Pipeline v2 control plane.

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

Lifecycle writes use a single `setLabels` call per object after re-reading that object's latest labels and performing a fresh state-plan check, preserving concurrently added non-agent labels and writing exactly one target `agent:*` state. Pair-level partial completion remains retryable because subsequent evaluations accept only the explicitly allowed previous/next transition and re-fetch before continuing.

A separate read-only Codex job receives the candidate as untrusted data and the default-branch governance as its trusted baseline. It must return `PASS`, zero findings, scope consistency, no test/governance weakening, and unchanged paper-only/live-trading safety. The model job receives no GitHub write credential.

Acceptance evidence is collected without checking out candidate code by a secret-bearing job that uses `AGENT_PUBLISH_TOKEN`. Its digest binds the independent review and later jobs to the exact repository, Issue, PR, head/base SHAs, CI jobs, and then-current ruleset contract. The artifact is workflow-owned audit and binding evidence, not proof that mutable repository policy is still current: its age is never used as a substitute for a live ruleset read. Candidate code is never executed with the maintenance credential.

### Serialization, gate and merge

The request workflow is serialized by exact PR/SHA. Its trusted follower uses the request run title, which is deterministically bound to that same PR/SHA, as a workflow-level concurrency key with `cancel-in-progress: false`. Duplicate valid requests therefore serialize rather than racing lifecycle or evidence publication.

After PASS and any explicitly authorized recovery, a separate trusted gate job repeatedly re-fetches mutable GitHub state. Immediately before every linkage comment, lifecycle label write, review-evidence comment, and gate status publication, it (and the recovery job) uses a dedicated `AGENT_PUBLISH_TOKEN` client only for the live `Protect main` ruleset reads and requires the complete contract: active enforcement, the exact default-branch target, no bypass actors, deletion/non-fast-forward/pull-request protections, strict required checks, every configured required context, and Actions integration ID `15368` for `agent-verified-gate`. Ruleset summary selection ignores inactive or evaluate-mode namesakes and requires exactly one active branch-targeted match. All other writes continue through the job-scoped token; candidate code is not checked out or executed with the maintenance credential. It permits only crash-recoverable `agent:pr → agent:verified` progress, rejects `agent:needs-human`, requires an exact `agent:verified` pair before publishing evidence, and requires exactly one bot-authored exact-SHA maintenance review marker after publication.

The gate publishes the same required status context used by the normal path:

`agent-verified-gate`

The ruleset therefore remains unchanged and permanently enabled.

A final merge job uses `AGENT_PUBLISH_TOKEN` only after the trusted gate succeeds; its job-scoped `GITHUB_TOKEN` remains read-only and is not the merge authority. It revalidates the exact head, current authorization, two-sided durable linkage, complete allowlisted file enumeration including rename sources, newest authoritative CI, current-main ancestry, verified lifecycle, exactly one bot-authored maintenance evidence marker, and bot-authored successful `agent-verified-gate`. It then performs the complete evaluation a second time and performs another live ruleset-contract read immediately before the exact-head merge request. Successful merges are recorded on the authorized Issue with the exact PR head and merge SHA, after one more live ruleset-contract read. Any drift, stale CI, active run, ambiguity, incomplete state, or halted condition fails closed. Successful required jobs may come from different run attempts after GitHub's rerun-failed-jobs operation; the guard binds each required context to its latest job by name and identity rather than incorrectly requiring every job to share the workflow run's latest attempt.

## Consequences

- Normal application Issues still use the one-authorization zero-click Builder → CI → Reviewer → verifier → gate → auto-merge path.
- Protected pipeline changes still cannot be generated by the autonomous Builder.
- Pipeline maintenance requires one explicit human maintenance request, but no ruleset weakening, bypass actor, manual state-transition sequence, or recurring manual gate toggling.
- Candidate workflow revisions cannot receive maintenance secrets.
- Duplicate maintenance requests cannot create duplicate exact-SHA gate evidence.
- Transient partial metadata writes are retryable without manual label surgery.
- The same permanent `agent-verified-gate` remains the branch-protection backstop for both normal autonomous delivery and trusted control-plane remediation.
- Live trading remains out of scope and paper-only invariants are unchanged.
