# ADR-0001 — Governance and Safety Invariants

- **Status:** Accepted
- **Date:** 2026-08-30

## Context

Autonomous Quant Lab is intended to evolve from deterministic quantitative research into an increasingly autonomous research and paper-trading laboratory. As autonomy increases, the project must prevent a research component from weakening the controls used to judge or execute its own outputs.

The repository already relies on point-in-time data, immutable research lineage, chronological validation, RiskEngine-mediated execution, and paper-only development. Future roadmap capabilities such as autonomous hypothesis generation, news/event intelligence, model validation, portfolio allocation, and possible later live-readiness work must preserve these guarantees.

## Decision

The project adopts the following constitutional separation of powers:

```text
Data / Intelligence
→ Research Engine
→ Independent Validation
→ Independent Risk & Governance authorization gate
→ Paper Execution
→ Post-trade monitoring / reconciliation / governance
```

`Independent Risk & Governance authorization gate` is a pre-execution authority. It must not be interpreted as a post-trade-only check. Runtime economic execution remains subject to the mandatory path `Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker`. Post-trade monitoring, reconciliation, incident handling, degradation, and suspension are additional controls after execution; they do not replace the pre-execution risk gate.

The following invariants are mandatory:

1. **Causality** — no component may use information before it was causally available at the relevant decision time.
2. **Reproducibility** — material research and execution decisions must be reconstructable from immutable/versioned inputs and code/artifact lineage.
3. **Independent validation** — Research Engine must not be able to approve its own hypotheses by changing eligibility, validation, benchmark, or anti-overfitting rules.
4. **Independent risk authority** — research/strategy logic cannot bypass, disable, or increase hard Risk/Governance limits; required risk authorization occurs before execution/broker submission.
5. **Controlled execution path** — every order path remains Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker.
6. **Paper-only current project** — CI, development, staging acceptance, and autonomous execution use PaperBroker only. No live broker, live role, live endpoint, live flag, or other live execution path may be introduced under the current project scope.
7. **Immutable audit history** — failed experiments, rejected strategies, incidents, revisions, and prior approvals are historical evidence and must not be silently rewritten.
8. **Fail closed** — missing, stale, contradictory, non-causal, or otherwise invalid critical evidence must block the dependent operation rather than be fabricated or silently ignored.
9. **Material change invalidation** — a materially changed strategy, data dependency, model, execution assumption, universe, or risk configuration must not automatically inherit prior validation. Revalidation requirements will be formalized by the framework tracked in #76.
10. **Permanent paper validation** — paper trading remains a permanent forward-testing environment for new and changed strategy artifacts, including after any hypothetical future live project.

## Consequences

### Positive

- Autonomous research can become more creative without becoming self-authorizing.
- Paper and later readiness decisions remain auditable.
- New capabilities cannot silently weaken causal or safety guarantees.
- Future reviewers can distinguish architectural policy from implementation detail.

### Costs / constraints

- Some apparently useful strategies will be rejected because evidence is incomplete or non-causal.
- Material changes may require repeated validation or paper testing.
- Research throughput is intentionally constrained by independent gates.
- Future live execution, if ever desired, requires a separate explicit project and architecture decision rather than emerging incrementally from paper code.

## Invariants protected

- no look-ahead bias;
- point-in-time safety;
- immutable lineage;
- chronological validation;
- deterministic decision auditability;
- PaperBroker-only current execution;
- independent pre-execution Risk/Governance authority;
- post-trade monitoring and reconciliation remain additional independent controls;
- no direct strategy-to-broker path.

## Related roadmap

- #74 — Autonomous hypothesis and strategy research
- #75 — News & Event Intelligence Layer
- #76 — Model Validation, Alpha Attribution & Capacity Framework
- `docs/ROADMAP.md`
- `AGENTS.md`
