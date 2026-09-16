# ADR 0008: One-time Issue #132 control-plane bootstrap

## Status

Temporary migration mechanism. Remove the workflow and its helper/test in the first normal
protected pull request after PR #131 is adopted.

## Decision

Issue #132 authorizes one trusted-default-branch workflow solely for repository
`Bbambaaamm/Autonomous-Quant-Lab`, Issue #130, PR #131, head
`76694eef519c0057750b170c55fe94673e7960e0`, and authorization specification
`7b2b5c30a3d55643512159bda237a97259e800ec200cebf92799f98633a88d81`.
The trusted baseline is `94611601bcd3fd683bc54f9f5d6023e4abc5951c`; changes between that baseline and
the dispatching `main` commit must be only this workflow, helper, test, its CI test wiring,
and this ADR.

The workflow reads, but never modifies, the active `Protect main` ruleset. It requires the
exact `main` target, strict status checks, the permanent `agent-verified-gate` GitHub Actions
integration (15368), pull-request/deletion/non-fast-forward rules, and no bypass actors. It
also requires the newest exact-head CI attempt and all nine configured jobs to be successful.

Candidate content is downloaded as an untrusted diff for a read-only independent Codex
review. The ruleset credential is confined to trusted jobs with no candidate checkout or
candidate execution. Metadata recovery uses the normal GitHub token, reloads mutable guards
between paired writes, and records identical
two-sided `agent-link:v1` evidence. A genuine exact-head Codex PASS is recorded using the
permanent review marker; the existing verifier, `agent-verified-gate`, and exact-head merge
path remain authoritative. There is no alternate status context, ruleset edit, bypass actor,
force push, or manual/synthetic PASS.

## Non-reusability

All repository, Issue, PR, head, authorization, baseline, ruleset, CI, lifecycle, and linkage
identities are constants and fail closed. After PR #131 closes or its head changes, the
workflow cannot pass. Its narrow baseline-to-main file allowlist also prevents repurposing it
after subsequent main changes. Removal is intentionally performed by a later ordinary,
protected pull request.
