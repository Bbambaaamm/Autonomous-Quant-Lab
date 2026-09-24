## Summary

<!-- What changed and why? -->

## Linked work

- Issue:
- Agent-Issue: #<implementation-issue-number>
- Parent roadmap/capability:
- Phase:

## Scope

### In scope

- 

### Explicit non-goals

- 

## Architecture / invariants

Check all that apply and explain material impacts below.

- [ ] No look-ahead bias introduced
- [ ] PIT / `known_at` / `observed_at` semantics preserved
- [ ] Immutable dataset / experiment / decision lineage preserved
- [ ] Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker preserved
- [ ] PaperBroker-only current execution preserved
- [ ] No live broker, role, endpoint, flag, or live execution path added
- [ ] Research cannot bypass Validation or Risk/Governance gates
- [ ] Fail-closed behavior preserved for invalid critical evidence
- [ ] No secrets committed or logged
- [ ] Dependency lock policies respected
- [ ] #190 runtime architecture contract preserved, or intentional contract change linked below
- [ ] Runtime data access remains explicitly bounded; no new full-history/materialize-all hot path
- [ ] Heavy runtime work remains short-lived/resource-bounded/backpressured where applicable
- [ ] Production resource ceilings unchanged, or benchmark/evidence + contract update included

### Material invariant impact

<!-- Describe any changed causal, validation, risk, execution, persistence, or security semantics. If none, say none. -->

### Runtime architecture impact

- Contract impact: none / compatible implementation / intentional contract change
- Linked architecture issue / ADR (required for intentional change):
- Resource/query evidence (required when runtime shape or budget changes):

## Data / persistence

- DB migration: yes / no
- Dataset/snapshot identity affected: yes / no
- Existing validation invalidated: yes / no / not applicable
- Revalidation required: none / partial / full research / new paper forward test / not yet defined

Explain:

## Tests executed

- [ ] formatting
- [ ] lint
- [ ] type-check
- [ ] unit tests
- [ ] relevant integration tests
- [ ] PostgreSQL tests where relevant
- [ ] API tests where relevant
- [ ] explicit causality/future-data regression tests where relevant
- [ ] `uv lock --check` / locked sync if backend dependencies changed
- [ ] `npm ci` and frontend checks if frontend dependencies changed

Commands/results:

```text
<insert concise evidence>
```

## Staging / acceptance

- Merge SHA:
- CI run:
- Deployed staging SHA:
- Acceptance required: yes / no
- Acceptance record:
- Result: PASS / FAIL / BLOCKED / NOT YET RUN

## Rollback / failure behavior

<!-- How does this fail closed and how can it be safely rolled back? -->

## Remaining limitations / follow-ups

- 

## Reviewer focus

<!-- Point reviewers to the highest-risk assumptions or files. -->
