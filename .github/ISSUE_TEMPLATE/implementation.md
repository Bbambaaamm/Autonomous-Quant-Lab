---
name: Implementation task
about: Implementation work derived from an approved phase or capability roadmap
title: ""
labels: ["type:implementation"]
assignees: []
---

## Parent / phase

- Parent roadmap/capability:
- Phase:
- Blocking dependency Issues/PRs:

> `type:implementation` pouze strojově odlišuje tento konkrétní ticket od epicu.
> Zahájení práce musí navíc výslovně autorizovat maintainer přechodem na
> `agent:ready`; samotné otevření Issue práci neautorizuje.

## Problem

<!-- What concrete problem must be solved? -->

## Objective

<!-- What observable outcome must exist when this is complete? -->

## Scope

### In scope

- 

### Non-goals

- 

## Architecture affected

- Components/services:
- Database/persistence:
- APIs/control plane:
- Market/news data:
- Strategy/research:
- Portfolio/risk/execution:

## Invariants that must remain true

- [ ] no look-ahead bias
- [ ] timezone-aware UTC internally
- [ ] PIT / causal knowledge semantics preserved
- [ ] immutable lineage preserved
- [ ] chronological validation only
- [ ] fail closed on invalid critical evidence
- [ ] Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker preserved
- [ ] PaperBroker-only current execution preserved
- [ ] no live execution capability added
- [ ] Research cannot weaken Validation or Risk/Governance gates
- [ ] secrets and RBAC controls preserved
- [ ] dependency lock policies preserved

Add task-specific invariants:

- 

## Data / causality design

<!-- Explicitly describe timestamps, `known_at`/`observed_at`/`as_of`, revisions, PIT rules, or state why not applicable. -->

## Persistence / migration design

<!-- Tables, migrations, immutability/idempotency constraints, or N/A. -->

## Failure and rollback behavior

<!-- What conditions must fail closed? What is the safe rollback path? -->

## Acceptance criteria

- [ ] 
- [ ] 
- [ ] 

Acceptance criteria must be deterministic and testable; avoid subjective statements such as "works well".

## Required tests

- [ ] unit
- [ ] regression for reported defect, if applicable
- [ ] integration
- [ ] PostgreSQL, if persistence is affected
- [ ] API, if control plane is affected
- [ ] future-data leakage / causality tests, if data/research semantics are affected
- [ ] idempotency/replay tests, if jobs/events/execution are affected
- [ ] production-like staging acceptance, if runtime behavior is affected

## Validation invalidation

Does this change invalidate prior research/paper evidence?

- [ ] No
- [ ] Partial revalidation required
- [ ] Full research revalidation required
- [ ] New paper forward test required
- [ ] Not yet defined — must be resolved before implementation

Reason:

## Definition of Done

This task is complete only when:

- implementation matches this Issue and authoritative linked specifications;
- required checks/tests pass;
- documentation is updated;
- exact merge SHA CI is green;
- required staging acceptance is recorded under `docs/acceptance/`;
- remaining limitations/blockers are explicit.
