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

Exactly zero or one `agent:*` state label may exist on a participating object.

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
| `agent:needs-human` | `agent:ready`, `agent:running`, `agent:pr` | Human maintainer after remediation; the dispatch and comment document recovery. `agent:pr` again requires linkage. |
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

The write-capable verifier runs only after the authoritative workflow named `CI`
completes successfully for a pull request. It checks metadata through the GitHub
API and never checks out or executes PR code. It writes `agent:verified` to the
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

An unknown/ambiguous event or failed condition produces `NO WRITE`, never inferred
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

`agent:needs-human` always prevents invalidation mutations. Missing or contradictory
durable linkage is escalated on the PR (and on the deterministically linked Issue
when available) rather than guessed. Unrelated labels are preserved.

## Recovery, rollback, and escalation

Escalate a blocked or ambiguous run with a reason rather than guessing. Recovery is
a deliberate maintainer transition out of `agent:needs-human`; comments preserve
both transitions. Pair transitions use add/remove mutations rather than replacing
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
