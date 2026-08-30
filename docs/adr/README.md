# Architecture Decision Records

Architecture Decision Records (ADRs) capture durable decisions that affect correctness, causality, reproducibility, security, validation, execution, or project governance.

## When an ADR is required

Create an ADR when a change materially affects one or more of:

- causal / point-in-time semantics;
- dataset or experiment identity;
- strategy lifecycle or promotion rules;
- validation or risk authority;
- execution architecture;
- broker abstraction;
- paper/live separation;
- data-provider semantics;
- model/news provenance;
- critical persistence or audit lineage;
- security or fail-closed behavior.

Routine refactors and local implementation details do not require an ADR unless they change one of these guarantees.

## Format

Use sequential filenames:

```text
0001-title.md
0002-title.md
...
```

Each ADR should contain:

- Status: Proposed / Accepted / Superseded / Rejected
- Date
- Context
- Decision
- Consequences
- Invariants protected
- Supersedes / Superseded by, where applicable
- Related Issues / PRs

Accepted ADRs are historical records. Do not rewrite an old accepted decision to make current behavior look consistent. Add a new ADR that supersedes it.

## Initial constitutional decisions

The first ADR records the project-wide governance and safety invariants that all later ADRs must preserve unless an explicit future project changes them through a separately reviewed decision.
