# Acceptance Evidence Ledger

Critical work in Autonomous Quant Lab is not complete at merge time. This directory defines how production-like staging acceptance evidence is recorded.

## Required evidence

For every critical phase, capability, or defect fix that requires staging validation, record:

- linked Issue / PR;
- merge commit SHA;
- final CI workflow/run and result;
- deployed staging SHA;
- database migration/version state, when relevant;
- service/runtime state, when relevant;
- exact acceptance scenario;
- expected result;
- observed evidence;
- PASS / FAIL / BLOCKED outcome;
- timestamp in UTC;
- known limitations / remaining blockers;
- whether prior validation was invalidated by a later code/data/config change.

## Core rule

**Acceptance evidence is bound to the exact artifact that was tested.**

A staging PASS for SHA A is not evidence for SHA B when the changed code can affect the tested behavior.

Similarly, a research or paper validation cannot be silently inherited after a material strategy/data/execution/risk change. See #76 and `docs/ROADMAP.md`.

## Suggested record format

Create one Markdown record per material acceptance event, for example:

```text
docs/acceptance/2026-08-31-phase6-paper-open.md
```

Template:

```markdown
# Acceptance — <name>

- Issue/PR:
- Merge SHA:
- CI run:
- Staging SHA:
- UTC timestamp:
- Result: PASS | FAIL | BLOCKED

## Preconditions

## Scenario

## Expected

## Observed evidence

## Invariants checked

## Remaining blockers / limitations

## Invalidation notes
```

## Do not record secrets

Acceptance records must never contain credentials, raw tokens, passwords, private keys, or other secrets. Record only presence/status or safe identifiers where needed.

## Paper-only invariant

Current acceptance uses PaperBroker only. No acceptance record may be treated as authorization for live execution.
