# ADR 0007 — Trusted control-plane remediation

## Status and adoption boundary

Implementation candidate for Issue #126. The production controller is wired to
`agent-maintenance-runtime.cjs`; tests execute those entrypoints with simulated
GitHub responses. This is NOT a statement that the candidate has been deployed,
independently approved, or exercised with production credentials.

The current maintenance follower is defective, but it is not the only trusted
adoption route. At base `94611601bcd3fd683bc54f9f5d6023e4abc5951c`, the existing
`agent-state-transition.yml` and `agent-codex-review.yml` provide a separate
path for this exact diff. The normal reviewer excludes four protected paths:
`AGENTS.md`, `.github/agent-pipeline.json`, `docs/autonomous-development-pipeline.md`
and `docs/adr/0003-autonomous-development-pipeline-v2.md`. This candidate changes
none of them. Do not generalize this route to candidates that change those files.

After current Issue authorization, green exact-head CI and code review, a
maintainer can use the default-branch Agent state transition workflow to move
an unlinked needs-human Issue to `agent:running` (without a PR input), then to
`agent:pr` with this PR number. The second transition labels and durably links
the unmanaged PR and attempts to dispatch the existing normal reviewer. Make
the PR non-draft before verification. If automatic dispatch does not occur, the
existing default-branch Agent Codex review workflow accepts `pr_number` and the
current exact `head_sha`; do not alter lifecycle or fabricate review markers.

A real exact-head zero-finding review must pass the existing verifier, gate and
expected-head merge controls. A standalone Codex comment or green PR CI is not
that acceptance. The existing reviewer may still return BLOCK for code or
unavailable evidence; diagnose the actual result without substituting an
exception. No new trust-root policy decision is required merely to use this
already-trusted route. Updating this PR alone does not adopt it. The repaired
maintenance follower still needs real runtime acceptance after adoption.

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
and attempt, the authorization comment ID/actor/run/specification hash, producer
run/attempt/source, newest CI identity/attempt, complete file set and ruleset
fingerprint. A replacement authorization with the same specification hash cannot
adopt an earlier request or review. Missing identity fields fail closed. Checksum establishes integrity only; trusted
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

## Nine CI jobs are not the same list as eight branch checks

The unchanged `.github/agent-pipeline.json` lists nine authoritative CI jobs,
including `agent-pipeline`. All must succeed. The current reviewed `Protect main`
baseline independently requires eight CI contexts,
all bound to GitHub Actions App 15368. It does not contain an additional required
branch check named `agent-pipeline`. The controller validates these two contracts
separately without restoring the removed mandatory `agent-verified-gate` branch check.
The automatic maintenance path retains its own internal gate and evidence checks.

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
  an explicitly requested needs-human recovery. The PR state is updated before
  the Issue state, keeping the Issue at needs-human if the first write's response
  is lost. A fresh request can resume that exact partial state. Conflicts never
  get reset; an arbitrary agent:pr Issue with an unmanaged PR remains rejected.
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
Recovery also remembers each object observed or written in the recovered state
during the complete operation, starting with the state sealed by `prepare` and
continuing through every linkage and label snapshot. If that object is subsequently
returned to `agent:needs-human`, the next linkage or label write is refused rather
than overwriting the new escalation. The sealed progress also prevents a newly
constructed runtime from forgetting a pre-existing restored state. Legitimate
partial recovery and a genuinely new, fully bound request remain supported.

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

## Fresh-request interruption regression

The recovery test executes the real request wrapper, interrupts the first label
write both before and after the mock API applies it, then submits a different
request run. It rejects the old producer bundle, collects new evidence, resumes
through the production runtime, preserves foreign labels and proves idempotence.
The after-write case fails on the Issue-first implementation and passes when the
PR is written first. These are mocked API tests, not live recovery acceptance.

## Review 34842655515 closure and outstanding acceptance

The regression suite now changes a recovered PR or Issue back to
`agent:needs-human` between the two production recovery writes and proves that
only the first label mutation occurs. It also replaces the authorization comment
with a different comment ID while retaining the exact specification hash, both
before the first write and between writes. The trusted request wrapper emits the
complete authorization identity, and the runtime re-derives and compares it on
every snapshot. These tests use simulated GitHub API objects; they are not a
production canary.

For the earlier canary-isolation revision, local verification recorded 385
passing tests and 22 workflow YAML documents. These historical numbers do not
apply to later revisions. The scope-correction results are recorded below;
exact-head hosted CI remains separately required.

## Review 5199699417: keep collection inside the authorized follower

The separately added `agent-control-plane-remediation-canary.yml` was outside
Issue #126's enumerated workflow scope and has been removed. No Issue text,
authorization or allowed-file policy is expanded. Its read-only collection now
lives in the `canary` job of the already-authorized
`agent-control-plane-remediation.yml`, behind the existing request and route.

The request UI carries the Boolean `canary_only` through
`toJSON(inputs.canary_only)` into strict JSON Boolean parsing. The string-valued
`github.event.inputs` payload is not an alternative authority. The artifact
records both the enum `mode` (`production` or `canary_only`) and the exact Boolean.
Missing, null, string-valued, unknown, legacy or contradictory values fail closed.

The follower's credential-free routing job validates the originating request's
run ID/attempt, repository, workflow path, source revision and actor identities.
`canary` requires route success, `canary_route == 'true'` AND
`production_route == 'false'`. Every production job retains its explicit
production-route condition and original prerequisites. A successful route with
missing outputs cannot select either path. Runtime mode validation remains a
separate check: production entrypoints reject canary-only artifacts.

The collector checks out `github.workflow_sha` with credentials not persisted,
downloads the request artifact named by the actual workflow_run event's ID and
attempt, and passes that unmodified event to the production runtime. There is no
separate UI-selected run or synthesized workflow_run context. The runtime checks
that the artifact matches that origin, authorization, target and current source.
The ordinary job token grants only Actions/contents/Issue/PR read permissions.
The existing ruleset audit credential is step-scoped to the trusted fixed-GET
collector; no candidate checkout, model credential or merge client is involved.
The unchanged read-only API facade rejects maintenance mutation methods.

### Invocation after independently reviewed default-branch adoption

Do not run the old follower on main to validate candidate-only code. After the
reviewed request/follower revision is legitimately adopted, use a dedicated,
eligible synthetic non-Draft PR and its independently authorized synthetic Issue,
with green exact-head CI and a current base. On GitHub, dispatch the existing
**Agent control-plane remediation request** from `main`, fill its exact Issue,
PR, head and reason, and select **canary_only**. The equivalent CLI invocation is:

```bash
gh workflow run agent-control-plane-remediation-request.yml --ref main \
  -f issue_number=<synthetic-issue> -f pr_number=<synthetic-pr> \
  -f head_sha=<exact-head> -f reason='Authorized read-only snapshot acceptance' \
  -F canary_only=true
```

No second workflow dispatch is required. The request's successful completion
starts the existing follower, which must run `route` then `canary`; `prepare`,
`independent-review`, `recover`, `gate` and `merge` must be skipped. Missing or
ambiguous routing data must not expose production credentials or enable writers.

The follower uploads
`maintenance-read-only-canary-<follower-run>-<attempt>/canary-report.json`. Its
unchanged version-2 schema includes `result: "SNAPSHOT_COLLECTED"`,
`mode: "read-only-snapshot"`, `operationalAcceptance: "PENDING"`, repository,
Issue/PR/head/base, complete authorization identity, request and CI run/attempt
identities and protection/file hashes. It emits no review or gate PASS. Missing
authorization, omitted bypass_actors, API permission failures or mismatched
request/source identities fail collection without a valid report.

This measures a bounded live metadata/CI/protection snapshot, not execution of
production publication, review, linkage, recovery, gate or merge. Required live
denial-path evidence and separately authorized end-to-end operational acceptance
remain outstanding. Mocked fault injection must never be reported as live proof.

### Regression evidence for the scope correction

The original two-suite command passed 397 tests on the inspected source. Nine new
scope/integration checks failed before the correction: the unauthorized file
still existed and the existing follower had no canary job. Tests now execute the
real request wrapper, real route script and real collector script with simulated
network responses. They verify snapshot reporting, zero mutations, request
run/attempt substitution, missing/production mode, replacement authorization,
explicit mutually exclusive routing, trusted source and credential boundaries.
The obsolete standalone-workflow contract test was migrated to the follower job;
its read-only and request-provenance assertions remain. No existing behavioral
regression was removed, and authoritative CI definitions remain unchanged.

The scope-corrected implementation passes 406 tests with zero failures or skips
on Node 22.16.0. All 21 remaining workflow YAML files parse, JavaScript syntax
checks pass, and git diff --check is clean. These are local results.

These local regressions are not a live canary. Publication, new hosted CI,
independent review, trusted-main adoption, actual live collection/denial-path
results and operational acceptance must be recorded separately for the final
revision. No live request, gate, merge, deployment or real label mutation is
performed as part of this scope correction.

## Diagnostic 34869646359: linkage, recovery audit and pre-merge observations

This follow-up is an implementation candidate, not a published result or merge
authorization. Existing adoption restrictions and required evidence still apply.

Prepare now seals the ID and body hash of each existing durable linkage comment
and the initial lifecycle states. Every following snapshot carries those link
identities monotonically; removing, editing, replacing or retiring a previously
observed link stops the next write. A successful link-create receipt is tracked
before the next snapshot. An initially absent link remains a legitimate case,
but it cannot be confused with one removed during the operation. Existing
linkage/authorization conflict checks are retained.

Recovery writes a bound INTENT record on both objects before state changes and
COMPLETED records only after both states pass validation. Records bind requester,
reason, authorization, source, request/producer run and attempt, head/base,
original and target state, bundle and current linkage identities. Completion
also binds the intention comment IDs. Repeated operations are idempotent; missing,
changed or replaced observed audit records stop subsequent writes. Interrupted
operations must not claim completion. Gate/merge require the matching audit;
these comments themselves are not review PASS or gate authority. GitHub still
provides no cross-resource atomic transaction.

The existing optional unprivileged `agent-maintenance-guard-tests.yml` also adds
a pull-request-triggered API integration canary. It uses only a read-only
repository job token, no repository secrets, no production dispatcher and no
model/publisher credentials. It reads an exact target, current authorization,
CI metadata and available protection fields, then re-reads the live PR with
intentionally mismatched expected head/base values to prove identity denials.
This is a candidate API integration test, NOT the trusted production collector.

Its report states `productionEvidence: false` and `operationalAcceptance: PENDING`.
It never synthesizes bypass authority when GitHub omits `bypass_actors`; the
visibility is UNKNOWN. It truthfully reports in-progress CI rather than calling
it green. It cannot issue a status, gate, merge, label change or workflow dispatch.
Local tests replace network callbacks and are expressly simulated. A hosted run
of this integration job can observe real reads and identity denials before
merge, but cannot certify production recovery, the isolated privileged ruleset
collector, or end-to-end adoption. Those acceptance requirements remain.

### Immutable recovery receipt across follower jobs

Recovery now produces `recovery-receipt.json` only after both lifecycle
postconditions and all four recovery-audit comments have been re-read. The
receipt binds the started and completed comment IDs and SHA-256 body hashes for
both the Issue and PR, both durable-link identities, repository and Issue/PR,
exact head and base, complete authorization and requester identity, the original
evidence-bundle digest, and the trusted follower workflow/source/run/attempt.
The recover job uploads that bounded file once as the immutable
`maintenance-recovery-receipt-<run>-<attempt>` artifact. Gate and merge download
that same-run artifact; neither job republishes or overwrites it.

Each gate or merge runtime validates the exact receipt schema, binding and
digest before its first write, seeds its audit baseline from the receipt, and
then compares fresh API comment IDs and body hashes to that baseline. Missing,
edited, duplicated, deleted, or verbatim-recreated started or completed comments
therefore fail closed. Wrong source, producer run/attempt, authorization,
evidence digest, linkage, target, or malformed receipts also fail closed. A
same-attempt continuation consumes the original receipt. A new attempt must
perform a freshly authorized recovery and create a new receipt; it cannot reuse
the prior attempt's artifact.

The regressions construct separate recover, gate, and merge runtime instances.
At both recover-to-gate and gate-to-merge boundaries, they exercise deletion,
verbatim recreation with a new ID, editing, and duplication of every started
and completed comment on both objects. They also cover legitimate continuation,
partial/lost-response recovery already exercised by the recovery suite,
same-attempt provenance, and rejection of wrong run, attempt, source,
authorization, bundle, hash, or missing receipts. The workflow regression proves
there is exactly one producer upload and that both consumers download its
run/attempt-bound name.

These are local mocked behavioral proofs of the producer/consumer contract, not
normal-reviewer evidence on `main` and not completed operational acceptance.
The limited premerge live reads remain untrusted candidate integration evidence;
they do not establish trusted production-controller receipt provenance.

No additional workflow filename or main/protection exception is introduced.
The original authoritative CI command loads the new local regressions through
its existing entrypoint. The supplemental live-read job is not a replacement
for any of the nine mandatory jobs or independent review. The CI artifact must
be checked for exact target/producer identity and limitations before using it
as supporting evidence; candidate-authored report text alone is not authority.
