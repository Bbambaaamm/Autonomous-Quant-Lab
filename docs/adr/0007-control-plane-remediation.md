# ADR 0007 — Trusted control-plane remediation

## Status and adoption boundary

Implementation candidate for Issue #126. The production controller is wired to
`agent-maintenance-runtime.cjs`; tests execute those entrypoints with simulated
GitHub responses. This is NOT a statement that the candidate has been deployed,
independently approved, or exercised with production credentials.

The current default-branch follower is defective. Updating this PR does not
update the follower running from `main`. There is no self-adoption mechanism in
this change. Under the present rules, successful `agent-verified-gate` evidence is
mandatory and only an already trusted producer may issue it. Re-running the
known-broken producer is not an adoption plan. A maintainer must explicitly
resolve that one-time trust-root adoption policy after independent review of the
exact candidate. Until then adoption is BLOCKED; this implementation neither
changes rules nor manufactures a gate to cross that boundary.

No bypass actors, force push, required-check removal, model mutation credential,
or execution of unreviewed candidate controller code with secrets is introduced.
Normal workflow use must never require recurring gate toggling.

## Request and trusted source

`agent-control-plane-remediation-request.yml` is a secret-free `workflow_dispatch`
request on the default branch. It verifies the maintainer, exact authorized Issue
specification, PR/head identity, lifecycle, complete changed-file enumeration,
current-main ancestry and newest CI. It records the real request run ID and
`GITHUB_RUN_ATTEMPT`; this field must not rely on an absent toolkit property.
The request checks out its exact `github.sha`, not a moving branch ref.

`agent-control-plane-remediation.yml` accepts only a successful completed request
from the exact request path/default branch/same repository. Trusted jobs check
out the follower's immutable `github.workflow_sha`. The request's source SHA,
current default-branch SHA and reviewed base SHA must equal that trusted source.
A changed source, new attempt, actor mismatch, revoked permission or stale
specification stops work; candidate code cannot become the policy baseline.
Maintenance runs serialize repository-wide. This does not make other GitHub
workflows or human operations part of a transaction.

## Evidence and credential domains

The trusted collector reads repository-scoped PR and Issue metadata, real
bot-authored authorization/linkage, requester permission, request-run provenance,
file scope, main ancestry, and all nine authoritative CI job results. It selects
the newest matching CI before testing its status, so a newer running or failed
run is not hidden behind an older completed success.

Ruleset visibility is a separate authority boundary. GitHub omits `bypass_actors`
without ruleset-write access. A missing or null field is UNKNOWN, never proof of
an empty list. In trusted controller steps only, the existing maintenance
credential is supplied to a fixed GET-only `rulesetReader`. It uses only the
public GitHub ruleset collection/detail endpoints, rejects redirects and failed
HTTP responses, and never returns the credential or raw response bodies in errors.
No PR-controlled code runs in these jobs. The model never receives this token.

The controller re-reads protection on EVERY pre-write snapshot; an old audit
artifact cannot stand in for the current ruleset. Actual merge authority is used
only by a separate bounded adapter for the expected-head PR merge PUT. These
adapters constrain trusted code's use of the credential; they do not pretend the
underlying token itself has only GET permissions.

The model receives a bounded local evidence bundle and trusted governance as
data, not a GitHub mutation token and not a requirement to improvise API access.
The evidence binds repository, Issue, PR, head, base, specification, request run
and attempt, producer run/attempt/source, newest CI identity/attempt, complete file
set and ruleset fingerprint. Checksum establishes integrity only; trusted
same-run immutable artifact provenance establishes the producing domain.

Candidate files and embedded Issue text remain untrusted. The reviewer keeps the
pinned Codex action, read-only profile, zero-findings PASS requirement and all
paper-only constraints. Its output validator uses only Node built-ins and cannot
import candidate repository code. Trusted mutation jobs independently validate
all output fields again with the pinned trusted implementation. BLOCK, malformed
output, missing evidence or uncertainty cannot produce gate success. Failed
review artifacts are retained for seven days; all artifact names include run
attempt. Recovery after failure uses a fresh full request, not a failed-job-only
retry that would mix outputs from different attempts.

## Nine CI jobs are not the same list as nine branch checks

The unchanged `.github/agent-pipeline.json` lists nine authoritative CI jobs,
including `agent-pipeline`. All must succeed. The current reviewed `Protect main`
baseline independently requires eight CI contexts plus `agent-verified-gate`,
all bound to GitHub Actions App 15368. It does not contain an additional required
branch check named `agent-pipeline`. The controller validates these two contracts
separately rather than accidentally demanding ten branch checks.

Protection must remain active, target exactly main with no exclusions, enforce
strict branch freshness, preserve PR/deletion/non-fast-forward restrictions,
explicitly contain zero bypass actors, and retain the CodeQL high-or-higher/error
restriction. The sealed fingerprint includes all additional rules and parameters.
ANY change after review, even a newly added restriction, requires fresh evidence
rather than silently substituting a different baseline. No ruleset is written.

## Production entrypoints and pre-write semantics

All production phases call `run` in the trusted runtime:

* `prepare` collects and seals bounded current API evidence without changing
  GitHub state.
* `recover` runs `link` and `recover`. Each missing durable link comment and each
  Issue/PR state write gets a new complete validated snapshot. Both partial
  recovery orders are supported. An initially unmanaged PR is admitted only for
  an explicitly requested needs-human recovery. Conflicts never get reset.
* `gate` separately validates before each state write, review-evidence comment
  and success status. It requires exact verified state before publishing the
  gate, zero findings, the same reviewed evidence, and a unique trusted marker.
* `merge` checks the newest bot-authored gate bound to this run, performs a fresh
  full snapshot, confirms the gate did not change, and calls merge with the exact
  expected head. It verifies the returned merge against the closed PR. No
  follow-up mutation is performed after merge.

Recovery never downgrades a legitimate partially verified pair. Gate does not
accept a new needs-human state. Repeated link/recovery operations are idempotent.
Foreign labels from the freshly read object are preserved.

GitHub does not offer a transaction across Issue authorization, labels, CI,
protection and PR merge. These checks narrow, but cannot eliminate, the interval
between the last read and a write. Atomic per-object label replacement also
cannot guarantee retention of a label added concurrently after that read. The
expected-head merge condition and GitHub's independently enforced rules remain
backstops. No transactional guarantee is claimed.

## Scope and tests

Maintenance remains limited to allowlisted agent workflows/scripts/configuration
and the explicitly listed pipeline ADRs. Renames must have both paths allowed;
API enumeration must equal `pr.changed_files` and not exceed the API ceiling.
Application code, dependencies/lockfiles, AGENTS, trading, live execution,
strategy deployment, and general infrastructure changes remain excluded.

The authoritative `ci.yml` is restored byte-for-byte to the trusted base. The
already-required `agent-autonomy.test.cjs` entrypoint loads the existing pure
regressions and new runtime regressions; an optional unprivileged runner is not
a substitute for the nine-job CI. Existing tests are retained.

Runtime tests use actual project authorization/linkage/job helpers and mutable
simulated API fixtures. They exercise successful link/recover/gate/merge,
pre-first-write denial, between-write head/auth/CI/protection/permission drift,
both partial orderings, missing/null bypass information, wrong Apps, retained
CodeQL, HTTP failure, source/attempt provenance, BLOCK, and exact-head merge.
Local Node results, GitHub-hosted PR CI, independent code/security review, a real
credential-isolated canary, and actual adoption are separate acceptance records.
None may be inferred from the others.

## Review closure: merge ownership, request wrapper and attempt caller

Every request and every pre-write snapshot now requires the PR's `auto_merge`
field to be explicitly null. Enabled or missing auto-merge evidence is rejected,
not disabled automatically. This prevents knowingly publishing the gate for a
PR already configured to merge outside the isolated expected-head adapter.
Concurrent maintainer changes can still race an API read, as documented above.

The actual request wrapper, not only the runtime, admits an unmanaged PR when
and only when the Issue is exactly `agent:needs-human`, classification and
authorization are valid, and the PR has no agent state. Other ambiguous states
remain rejected. Behavioral tests execute the workflow's real inline request
script and then exercise link/recovery with the emitted binding.

Request retries require `github.triggering_actor` to equal the original
`github.actor`. The live originating run is revalidated before each write with
both `actor.login` and `triggering_actor.login` equal to the bound requester.
A different or missing attempt caller stops the run; the original actor is not
silently used as a substitute for the person initiating a later attempt. A new
maintainer can create a fresh explicitly authorized request instead.
