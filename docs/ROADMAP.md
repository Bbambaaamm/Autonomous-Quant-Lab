# Autonomous Quant Lab — Master Development Roadmap

This document is the repository-level source of truth for development order, promotion gates, and long-term architecture.

Detailed capability specifications live in linked GitHub Issues. An Issue may describe future capabilities, but it does not authorize implementation outside the current approved workstream.

## Constitutional invariants

Every new capability must increase research capability, validation quality, or operational safety **without weakening causality, reproducibility, independent validation, or risk governance**.

The following are non-negotiable:

- correctness over speed;
- timezone-aware UTC internally;
- no look-ahead bias;
- point-in-time safe research and universe membership;
- immutable dataset, experiment, and decision lineage;
- deterministic financial calculations;
- chronological validation only;
- failed/invalid data paths fail closed;
- strategies never bypass Portfolio, RiskEngine, or ExecutionEngine;
- research cannot weaken its own validation or risk gates;
- CI and development use `PaperBroker` only;
- no live broker, live role, live endpoint, live flag, or other live execution path in the current project.

See `AGENTS.md` for mandatory engineering rules.

## Existing implementation baseline — Phase 1 through Phase 9 complete

`docs/implementation-plan.md` is authoritative for the already completed numbered implementation phases. Phase 1 through Phase 9 are complete there, including Phase 6 market data/PIT research, Phase 7 paper monitoring, Phase 8 operator control plane, and Phase 9 security/production hardening.

The current work is a **paper-pilot / production-like acceptance workstream on top of that completed Phase 1–9 baseline**. It must finish the remaining real-session and acceptance blockers without reusing completed phase numbers.

No Phase 10+ capability work may be used to bypass an unresolved current paper-pilot blocker.

## Long-term capability order

```text
Completed baseline — Phase 1 through Phase 9
→ current paper-pilot / production-like acceptance
→ Phase 10 — Autonomous Research Factory (#74)
→ Phase 11 — News & Event Intelligence (#75)
→ Phase 12 — Model Validation, Alpha Attribution & Capacity (#76)
→ Phase 13 — Portfolio Intelligence & Capital Allocation
→ Permanent Paper Laboratory (ongoing operating model)
→ Phase 14 — Live Readiness
→ possible future Controlled Live Portfolio (separate explicit project/decision)
```

Phase 10+ numbering is reserved for future capabilities after the completed Phase 1–9 baseline. A future live execution project is not implicitly authorized by this numbering.

## Capability roadmaps

### Phase 10 / #74 — Autonomous hypothesis and strategy research

Parent roadmap for the Autonomous Research Factory:

- strategy composition from approved typed building blocks;
- hypothesis generation;
- pre-registered experiments;
- anti-overfitting budget;
- champion/challenger model;
- strategy lifecycle and retirement memory;
- permanent paper laboratory;
- separation of Research, Validation, Execution, and Risk/Governance.

Issue: https://github.com/Bbambaaamm/Autonomous-Quant-Lab/issues/74

### Phase 11 / #75 — News & Event Intelligence Layer

Dedicated point-in-time news/event capability:

- immutable source ingestion;
- `first_seen_at` / `known_at` causality;
- revision and provenance lineage;
- entity linking and event taxonomy;
- LLM-assisted extraction with explicit model/version lineage;
- news features for research and deterministic risk intelligence;
- no direct LLM-generated orders.

Issue: https://github.com/Bbambaaamm/Autonomous-Quant-Lab/issues/75

### Phase 12 / #76 — Model Validation, Alpha Attribution & Capacity Framework

Institutional validation layer that answers whether an apparent edge is real, independent, robust, and scalable:

- benchmark governance;
- alpha/factor attribution;
- execution and cost attribution;
- capacity and market-impact analysis;
- uncertainty / overfitting evidence;
- validation invalidation matrix;
- model-risk registry;
- independent data reconciliation;
- promotion scorecard and degradation monitoring.

Issue: https://github.com/Bbambaaamm/Autonomous-Quant-Lab/issues/76

## Required promotion architecture

A strategy must never jump directly from research into an execution environment.

Target lifecycle:

```text
RESEARCH
→ PAPER_CANDIDATE
→ PAPER_ACTIVE
→ PAPER_VALIDATED
→ future LIVE_CANDIDATE
```

`LIVE_CANDIDATE` is only a future governance state. It does not authorize or imply live execution support in the current repository.

Demotion lifecycle must support at least:

```text
ACTIVE
→ DEGRADED
→ SUSPENDED
→ RETIRED
```

## Permanent Paper Laboratory

Paper trading is not a temporary stage that disappears after a future live decision. It is the permanent forward-validation environment for:

- new strategies;
- new strategy versions;
- changed parameters;
- changed data/features;
- changed execution assumptions;
- changed portfolio/risk configuration where economically material.

A materially changed strategy must re-enter the appropriate validation/paper lifecycle according to the Validation Invalidation Matrix defined by #76.

## Future dual-mode design principle

If a live execution capability is ever separately approved and implemented, paper and live must use the same immutable economic strategy artifact wherever possible:

- strategy name/version;
- canonical parameters;
- code/artifact identity;
- universe lineage;
- feature/model dependencies;
- promotion lineage.

Environment-specific execution differences must be explicit and auditable.

The target operating model is:

```text
Autonomous Research Factory
→ Independent Validation
→ Independent Risk & Governance authorization gate
→ Permanent Paper Laboratory / Paper Execution
→ Post-trade monitoring, reconciliation, and governance
→ future Controlled Live Portfolio only after separate Live Readiness approval
```

At runtime, every economic order path remains `Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker`.

## Definition of Ready

An implementation Issue is ready only when it contains:

- problem statement and objective;
- explicit scope and non-goals;
- affected architecture/components;
- invariants that must remain true;
- data/causality implications;
- persistence/migration implications, if any;
- API/control-plane implications, if any;
- deterministic acceptance criteria;
- required tests;
- rollback/fail-closed expectations;
- links to parent roadmap/capability Issue.

If these are materially missing, implementation should not begin.

## Definition of Done

A critical implementation is not done merely because code is merged.

Done requires, as applicable:

1. implementation matches the authoritative specification;
2. formatting/lint/type checks pass;
3. unit and relevant integration/PostgreSQL/API tests pass;
4. explicit future-data leakage / causality regression tests pass where relevant;
5. dependency lock policies remain valid;
6. documentation is updated;
7. exact merge SHA CI is green;
8. exact merge SHA is deployed to the target staging environment;
9. production-like acceptance is executed and recorded;
10. known limitations and remaining blockers are explicit.

Use `docs/acceptance/README.md` for acceptance evidence conventions.

## Roadmap governance

- Large capability Issues (#74–#76 and successors) are epics/specifications, not implementation tickets.
- Split an epic into implementation Issues only when that capability is next in the approved sequence.
- Do not create large backlogs of speculative implementation tickets years ahead.
- Every implementation PR must link its Issue and parent capability/phase.
- Material architecture decisions must be recorded under `docs/adr/`.
- Failed experiments, rejected strategies, invalid data states, and operational incidents remain part of the audit history.
- Attractive new features must not pre-empt unresolved blockers in the current workstream.

## Planning principle

The project optimizes for the probability of discovering and safely validating durable net edge — not for producing the highest historical backtest.
