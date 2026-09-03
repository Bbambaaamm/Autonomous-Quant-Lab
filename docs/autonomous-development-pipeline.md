# Autonomous Development Pipeline v1

## Purpose and boundary

This pipeline is a GitHub-native, fail-closed handoff protocol for one
human-approved implementation Issue. It does not select work, write code, fix CI,
merge, deploy, or authorize an economic/runtime change. Labels, comments, commits,
workflow runs, and an exact Issue/PR marker form the audit record; there is no
parallel state database.

```text
human-approved implementation Issue
  → agent:ready → agent:running → Draft PR → agent:pr
  → ready-for-review PR + approval + exact-head CI → agent:verified
  → HUMAN MERGE

agent:running / agent:pr → agent:needs-human
```

## Maintainer quick-start: standard happy path

Use this checklist for one already approved `type:implementation` Issue. Run all
state changes through the named GitHub Actions workflows; never edit state labels
directly.

- [ ] Before the pipeline's first repository use, run **Agent label setup** once.
- [ ] Confirm the Issue has `type:implementation` and no `agent:*` state, then run
      **Agent state transition** with `previous_state: none` and
      `next_state: agent:ready`.
- [ ] Explicitly authorize work by running **Agent state transition** again with
      `previous_state: agent:ready` and `next_state: agent:running`.
- [ ] Create a branch from the current default branch, implement only the approved
      Issue, validate it, and open a Draft PR against the default branch. Include
      exactly one standalone `- Agent-Issue: #N` marker; do not use an auto-closing
      reference.
- [ ] Before final handoff, reconcile the branch with the current default branch if
      that branch moved, and ensure authoritative CI is produced for the final PR
      head SHA that will be reviewed.
- [ ] Run **Agent state transition** with `previous_state: agent:running`,
      `next_state: agent:pr`, and the Issue and PR numbers. Confirm `agent:pr` is the
      only `agent:*` state on both objects, the Issue still retains
      `type:implementation`, unrelated labels remain intact, and matching durable
      linkage comments were written.
- [ ] Mark the PR ready for review. Have an eligible reviewer approve the current
      head SHA or, for the documented single-maintainer case, run **Agent exact-SHA
      review acknowledgement** with the PR number, full current head SHA, and
      `REVIEWED_EXACT_SHA_NOT_MERGE_AUTHORIZATION`.
- [ ] Ensure authoritative **CI** is green for that exact current head SHA. CI may
      finish before review evidence; a later trusted review/acknowledgement
      re-evaluates the existing exact-head CI. If review evidence exists first, CI
      completion provides the verification opportunity. No manual CI rerun is
      required merely to wake the verifier.
- [ ] Confirm **Agent verification gate** moved both objects to `agent:verified` on
      that exact SHA.
- [ ] Reconfirm repository rules, required checks, review requirements, and current
      base state, then perform the human merge. `agent:verified` is a handoff result,
      not merge authorization.

If work blocks in `agent:running` or `agent:pr`, use **Agent state transition** to
move to `agent:needs-human` with a non-empty reason and the PR number when leaving
`agent:pr`. Complete the required remediation while the workflow remains paused.
Then recover explicitly through `agent:needs-human` → `agent:running` →
`agent:pr`; for a durable-linked PR, recovery reconciles both Issue and PR. There is
no direct `agent:needs-human` → `agent:pr` transition.

## Classification and human opt-in

`type:implementation` is mandatory and the mutually exclusive labels
`type:epic`, `type:roadmap`, and `type:capability` deny eligibility. The
implementation Issue template supplies only the classification label. A maintainer
with `write`, `maintain`, or `admin` permission must separately run **Agent state
transition** to add `agent:ready`. Thus creating or opening an Issue never starts
work. Text/title heuristics are not used. Missing, contradictory, or multiple state
labels produce `NO WRITE`.

Roadmap/capability Issues are specifications under `docs/ROADMAP.md`, not work
items. They must be split into a concrete Issue satisfying Definition of Ready,
classified `type:implementation`, and then explicitly opted in by a human.

## State semantics

Exactly zero or one `agent:*` state label may exist on a participating object;
`agent:pr` is therefore the only `agent:*` state label on a linked Issue and PR.
It is not the object's only label: the Issue retains `type:implementation` and all
transitions preserve unrelated labels on both objects.

| State | Meaning | Explicitly does not mean |
|---|---|---|
| `agent:ready` | A maintainer explicitly authorizes this classified Issue for takeover. | Work has started, CI passed, or merge is authorized. |
| `agent:running` | Work on that Issue has been claimed/started. | A PR exists or its output is correct. |
| `agent:pr` | The Issue and its single linked open PR passed structural linkage validation. | Review/CI passed or merge is authorized. |
| `agent:needs-human` | Progress is stopped pending the reason recorded in the transition comment. | Failure is waived or the previous state is forgotten. |
| `agent:verified` | Automated handoff conditions passed for the recorded final PR head SHA. | Merge authorization, auto-merge, review/check bypass, deployment, or Issue closure. |

Labels are workflow state only. They never substitute for CI conclusions, approved
review, repository rulesets, required checks, or a human merge decision.

## Allowed transitions and actors

The manually dispatched state workflow permits only:

| From | To | Actor / additional evidence |
|---|---|---|
| no state | `agent:ready` | Human maintainer; unambiguous implementation classification. |
| `agent:ready` | `agent:running` | Human maintainer initiating/assigning a run. |
| `agent:running` | `agent:pr` | Human maintainer; open PR against the default branch with exact linkage. The workflow labels both Issue and PR. |
| `agent:running`, `agent:pr` | `agent:needs-human` | Human maintainer; non-empty escalation reason. From `agent:pr`, the linked PR number is required and both objects are updated. |
| `agent:needs-human` | `agent:ready`, `agent:running` | Human maintainer after remediation; the dispatch and comment document recovery. If durable linkage exists, recovery resolves its unique open PR, moves both objects to `agent:running`, and then continues through `agent:running` → `agent:pr`. |
| `agent:pr` | `agent:verified` | Only **Agent verification gate**, after all conditions below; partial pair writes are safely reconciled on retry. |

The transition workflow requires an explicit expected previous and next state,
verifies the dispatching actor's repository permission, mutates only those state
labels, and comments with actor, workflow run, transition,
PR/SHA where applicable, and escalation reason. Direct label edits are not valid
pipeline transitions and must be recovered through a maintainer-reviewed state
reset (remove conflicting state labels, retain the audit history, then dispatch a
valid transition). It never replaces the complete label list, so concurrent
unrelated labels are preserved. Invalid transitions perform no write.

Before first use, a maintainer runs **Agent label setup** once. It idempotently
creates only the configured classification and state labels and cannot transition
an Issue. Existing label definitions are not silently rewritten. This makes the
Issue-template classification available without coupling setup writes to an
otherwise invalid transition.

## Deterministic Issue → PR linkage

A participating PR body contains exactly one standalone marker:

```text
- Agent-Issue: #82
```

The number must identify the concrete `type:implementation` Issue. Zero, duplicate,
malformed, or conflicting markers fail closed. Title similarity, branch names, and
free-form references are never linkage. `Closes #N` is deliberately not required:
v1 never automatically closes an Issue. On `agent:pr`, both objects receive that
state and the trusted transition workflow writes a durable PR comment marker
`agent-link:v1` containing repository, Issue number, and PR number. The mutable PR
body marker and durable evidence must agree on every verification; changing the PR
body cannot rebind an already authorized PR.

## Automated verification gate

The write-capable verifier runs after the authoritative workflow named `CI`
completes successfully for a pull request and re-evaluates after a submitted native
review or the exact-SHA acknowledgement workflow. A review-triggered evaluation
queries existing completed `CI` runs and accepts only complete required-job evidence
for the same current head SHA and PR. It checks metadata through the GitHub API and
never checks out or executes PR code. It writes `agent:verified` to the
linked Issue and PR only when all of these are true:

1. the CI run refers to exactly one PR and its conclusion is `success`;
2. the PR is open, non-draft, and its current head SHA exactly equals the CI run SHA;
3. fresh exact-SHA review evidence exists: either GitHub's current review decision
   is `APPROVED` and an active approval explicitly records the current commit SHA,
   or an authorized maintainer recorded the v1 review acknowledgement for that SHA;
4. exactly one valid `Agent-Issue` marker points to a non-PR Issue and exactly
   matches the trusted durable `agent-link:v1` evidence for this repository and PR;
5. that Issue is unambiguously `type:implementation`, not epic/roadmap/capability;
6. both Issue and PR have exactly the state `agent:pr`;
7. neither object has `agent:needs-human`;
8. every configured authoritative CI job succeeded on that exact SHA:
   `agent-pipeline`, `quality`, `unit-research`, `api`, `integration-postgres`,
   `frontend`, `security`, `container-build`, and `production-smoke`; and
9. fresh API reads immediately before each side of the paired write still observe
   the same linkage, classification, review evidence, SHA, open/default-base/non-draft
   PR, and a state safely reconcilable from `agent:pr` to `agent:verified`.

### Review acknowledged versus verified versus merge authorization

GitHub does not allow a PR author to approve their own PR. A repository operated by
one maintainer can therefore use **Agent exact-SHA review acknowledgement** after
the PR is Ready and in `agent:pr`: the maintainer supplies the full 40-character
head SHA and confirms `REVIEWED_EXACT_SHA_NOT_MERGE_AUTHORIZATION`. The workflow
checks permission and current PR metadata, then writes an audit comment containing
the exact-SHA marker, reviewer, and workflow run. A new commit invalidates it.

`review acknowledged` says only that a human examined that exact revision.
`agent:verified` additionally says that exact-revision CI, linkage, classification,
state, and review handoff checks passed. Neither is human merge authorization.
Required GitHub reviews, rulesets, checks, and branch protection remain higher
authorities: acknowledgement cannot satisfy or bypass a required GitHub approval.

Native approvals are read through the GitHub reviews API and count only when an
active `APPROVED` review has `commit_id` equal to the CI/head SHA. A general
`reviewDecision` without that exact-commit evidence is insufficient.

Thus CI may finish before review evidence without stranding the PR: the later trusted
review event reuses CI as evidence instead of rerunning CI. It neither executes PR
code with a write token nor recursively triggers CI. An unknown/ambiguous event or failed condition produces `NO WRITE`, never inferred
success. A new commit changes the head SHA; normal GitHub CI and review-dismissal
policy apply, and the PR must return to `agent:pr` before a later run can verify it.
The list in `.github/agent-pipeline.json` must be reviewed whenever authoritative
CI job names change. Repository branch protection remains the ultimate required
check/review authority.

## Verification invalidation after a push

A metadata-only `pull_request_target:synchronize` workflow runs trusted base-branch
code only; it never checks out or executes PR content. When a new commit is pushed,
it resolves the original Issue from the durable link, requires the current body
marker to agree, and idempotently reconciles both Issue and PR from
`agent:verified` to `agent:pr`. It records the new SHA in audit comments. Old native
approvals and acknowledgements cannot satisfy a later verification because both are
matched to the new SHA, and the new CI run does not itself promote the state.

Before the first durable link exists, repeated `synchronize` events on an unmanaged
PR are legitimate and produce `NO_WRITE`; they never create an agent state label.
A uniquely linked `agent:pr` pair without `agent:verified` likewise needs no
invalidation and produces `NO_WRITE`. Conflicting durable links or a linked marker
mismatch fail closed, while `agent:needs-human` always has priority and is never
automatically recovered by invalidation.

`agent:needs-human` always prevents invalidation mutations. Missing durable linkage
is escalated only when an existing linked lifecycle state makes its absence
contradictory; a clean pre-link PR remains `NO_WRITE`. Contradictory linkage is
escalated on the PR (and on the deterministically linked Issue when available)
rather than guessed. Unrelated labels are preserved.

## Recovery, rollback, and escalation

Escalate a blocked or ambiguous run with a reason rather than guessing. Recovery is
a deliberate maintainer transition out of `agent:needs-human`; comments preserve
both transitions. When the Issue audit comments contain a durable link, recovery
requires exactly one matching, open, default-base PR whose body marker and own
durable comment agree. Missing, stale, conflicting, or ambiguous evidence fails
closed. A pre-PR escalation with no durable link remains an Issue-only recovery.
Pair transitions use add/remove mutations rather than replacing
all labels. The caller supplies exact `previous`/`next` states. The combinations
`previous/previous`, `next/previous`, `previous/next`, `previous+next` (an interrupted
single-object mutation), and `next/next` are accepted only for that same operation;
a retry completes missing work idempotently. Any third state fails closed, and
`agent:needs-human` is never removed except by an explicit human recovery whose
declared previous state is `agent:needs-human`.

Rollback is disabling the five agent workflows and removing current state labels;
comments, commits, reviews, and workflow history remain immutable audit evidence.
Rollback never changes CI or branch protection. The workflows do not close Issues,
push branches, merge, or deploy.

## Security and authority

Read-only verification and state writes are separated by jobs/workflows and explicit
permissions. The verifier is triggered by `workflow_run` on trusted default-branch
code and consumes only GitHub metadata; it never uses `pull_request_target` and
never runs untrusted PR content with a write token. State management has only
`contents:read`, `issues:write`, and `pull-requests:write`; verification additionally
needs `actions:read` to inspect the exact CI jobs. Both fail closed.

Existing CI remains authoritative; v1 only adds its dedicated validation job and
does not weaken any existing job. In particular, pipeline labels do
not bypass the dedicated `agent-pipeline` validation, quality, backend, PostgreSQL,
frontend, security, container, or production-smoke gates. There is no automatic merge and `agent:verified` always
ends at **HUMAN MERGE** under repository branch protection.

## Version boundary and future compatibility

v1 provides classification, opt-in, transitions, deterministic linkage, escalation,
and verified handoff only. It intentionally omits autonomous selection/building,
fixer loops, independent AI review, staging deployment/acceptance, automatic Issue
closure, and merge automation. Later versions may add bounded failure
classification/review (v2), exact-merge-SHA staging evidence (v3), or a role-based
ready queue (v4) while retaining the same GitHub evidence and fail-closed state
contract. Those capabilities require their own reviewed implementation; none is
authorized by this document.

## V2: bounded remediation and independent review

V2 preserves every v1 state and human gate and adds exact-SHA operational evidence. A trusted
classifier binds the job list to its containing exact-SHA workflow run and normalizes failed-step
metadata into a fixed taxonomy. Unknown, multiple, infrastructure-transient, security, PostgreSQL,
image, production, governance, schema, authentication,
broker/execution, or live-trading failures fail closed to maintainer intervention. The free-form
fix budget is two commits per linked PR; an exact `(source SHA, failure evidence)` marker makes
retries idempotent. Exhaustion is not waived.

The fixer is split into three trust domains: Codex patch generation has `OPENAI_API_KEY`, a
read-only GitHub permission and no repository command execution; validation has neither model
secret nor write credential and verifies checksums, paths, patch bounds and prescribed checks;
publishing has only GitHub write permission, repeats linkage/state/head/base checks, and applies
only the checksum- and exact-path-bound validated artifact without force-push or pushes to `main`.
Validation commands come from a hard-coded failure-class map, never from model output. Both linked
objects must have exactly `agent:pr`, and two-sided durable comments must agree with the PR marker.
Configuration and denylist
are versioned in `.github/agent-pipeline.json`.

After green authoritative CI, a separate read-only Codex invocation reviews AGENTS/governance,
the trusted resolver's bounded linked-Issue scope, base-to-exact-head diff, test weakening,
security and paper-only/live implications. Its strict schema records the reviewed SHA, findings,
scope consistency and safety assessments.
It records structured `PASS` or `BLOCK` evidence bound to the current SHA. Missing
`OPENAI_API_KEY`, malformed output, stale SHA or TOCTOU always fails closed; the secret value is
never stored in the repository. `agent:verified` now requires both a fresh v2 Reviewer PASS and
the existing exact-SHA human/native evidence. Neither substitutes for the other or authorizes
merge. Reviewer BLOCK and unsafe, ambiguous or exhausted work require a human. The `agent:pr`
transition dispatches a trusted re-evaluation, so green CI that finished before linkage is reused
without a manual CI rerun; a PASS explicitly invokes the reusable verifier with PR number and SHA.

### V2 trusted diagnostics and Reviewer BLOCK routing

The CI controller downloads only the single failed authoritative job log, aggressively redacts and
truncates it, and binds the excerpt checksum, source workflow run ID, job ID, and run attempt to the
exact head SHA. If that bounded diagnostic cannot be produced, or when the failure is
`dependency-lock`, the controller fails closed to `agent:needs-human`; dependency files never enter
the free-form fixer.

Both Codex invocations receive deterministic prompt files inside the checked-out workspace. Trusted
instructions frame bounded Issue or diagnostic JSON as untrusted data, rather than referring to a
runner-temporary path that the action may not read. The bot allowance is restricted to
`github-actions[bot]`. A Reviewer BLOCK calls a dedicated trusted escalation workflow with the
already validated PR number and exact head SHA. That workflow has no repository-content write
permission and, immediately before changing labels, independently re-fetches the current open PR,
default-branch base, exact head SHA, concrete implementation Issue, exact lifecycle on both objects,
and both durable linkage directions. Stale, ambiguous, partially transitioned, or conflicting state
performs no write; a valid BLOCK moves both linked objects to `agent:needs-human` and records a
bounded redacted reason. The failed-CI fixer and its isolated publication credential are not involved.

### v2 crash, trust, and pre-link guarantees

The post-link controller re-queries a unique authoritative CI run for the exact PR head. A completed success routes to independent review, a completed failure routes to the classifier with its exact run ID, an active run is left to its normal completion event, and missing or ambiguous evidence performs no write. Fixer publication is limited to same-repository PR heads, validates the ref as data, and uses the narrowly scoped `AGENT_PUBLISH_TOKEN` so the resulting push deterministically emits the next pull-request CI cycle.

Validated patches are exported from the staged index, including new files. Concrete trading, risk, execution, security, paper-only, authentication, and RBAC surfaces are protected by machine-readable path and diagnostic patterns. Fix commits carry a durable `Agent-Fix-Attempt` trailer, allowing budget reconciliation even if post-push audit recording crashes. All model, validation, and publication failures use an exact-head fail-closed finalizer; stale failures never mutate a newer head. Reviewer governance and the base SHA come from the trusted default-branch preparation checkout.

### v2 fourth-audit artifact and secret boundary

Before any repository check runs, credential-free validation stages the applied patch, compares the
actual cached path set with the model declaration, applies the deny policy to that actual set, and
rejects symlinks, gitlinks, executable additions, and mode transitions. Trusted publication repeats
the cached path and index-mode policy, stages additions before comparison, verifies the remote head
is still the classified source SHA immediately before push, and verifies the resulting remote SHA.
Manual dispatch accepts a CI run only when its completed failed `CI`/`pull_request` identity, exact
SHA, single attached PR, and PR number all agree.

Patch generation has an explicit secret preflight and no repository checkout. A preceding
credential-free job serializes bounded repairable source files as non-executable JSON; the
secret-bearing job contains only that source context and diagnostic artifact, so repository scripts
and tests are mechanically absent while `OPENAI_API_KEY` is present. The immutable Codex action pin
and read-only profile are wiring-tested. Publication exposes and requires `AGENT_PUBLISH_TOKEN` only in the final push step and never falls back to `GITHUB_TOKEN`.

### Trusted policy, classification, and fix scope

All v2 security decisions are loaded from a separate default-branch checkout. The pull-request
checkout is candidate data only: it never supplies classifier, path/mode rules, or publisher code.
Candidate checkout credentials are not persisted, and `AGENT_PUBLISH_TOKEN` is exposed only to the
final checked push operation after trusted linkage, artifact, path, mode, and scope checks.

Every exact failed authoritative CI run produces one idempotent `agent-ci-classification:v2`
record. Its encoded payload binds repository, Issue, PR, exact SHA, CI run and attempt, failed
jobs, normalized class, eligibility, budget state, and producing workflow/run. Unsafe outcomes are
recorded before escalation. Repair scope comes from trusted GitHub metadata: existing files must be
in the PR baseline file set and additions are limited to configured test paths. Validation binds
that scope into the artifact and publication checks it again.

### Fifth independent audit hardening

Reusable fixer calls now carry an explicit trusted invocation mode rather than inferring reusable-call identity from the inherited GitHub event context. Both automatic reviewer entry points and dispatched review require one completed, successful, pull-request-triggered authoritative `CI` run for the exact PR and head; failed-CI fixer entry likewise requires the complete failed identity contract. Timeout and runner evidence takes precedence over source-failure classification.

Credential-free patch validation installs the same pinned Python 3.12, uv 0.12.3, Node 24, and npm 11.17.0 toolchain as authoritative CI and synchronizes locked dependencies before the relevant checks. After those untrusted checks succeed, a separate fresh-runner `seal-patch` job downloads the original server-side generated artifact, freshly checks out trusted default-branch policy and the exact candidate SHA, and independently re-derives paths, modes, scope, patch bytes, checksum, and metadata without executing repository code. Publication binds source SHA, failure class, and authorized scope back to classifier outputs rather than trusting artifact metadata alone. Generation receives bounded trusted default-branch governance separately from untrusted diagnostics and source. Structured fixer `BLOCK` reasons and reviewer credential/action failures use exact-SHA, lifecycle, and two-sided-linkage rechecks before fail-closed human escalation; neither path can synthesize review PASS. Reusable verification and BLOCK-routing jobs explicitly receive minimal caller-side permissions, and governance escalation freshly revalidates both durable linkage directions immediately before label changes.

The fixer job-level guard treats an explicit reusable `review-block` invocation as authoritative
regardless of the caller's inherited event context. Direct `workflow_run` entry remains limited to
failed authoritative CI, while `workflow_dispatch` is limited to an explicit failed-CI replay; the
inner trusted invocation decision and exact linkage, lifecycle, SHA, and CI checks remain mandatory.
