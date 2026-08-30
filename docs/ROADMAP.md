# Autonomous Quant Lab — Master Development Roadmap

This document is the repository-level source of truth for development order, promotion gates, and long-term architecture.

Detailed capability specifications live in linked GitHub Issues. An Issue may describe future capabilities, but it does not authorize implementation outside the current phase.

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

## Current phase — Phase 6: stable PIT research + paper execution

Goal: complete and operationally validate the causal market-data, immutable research, point-in-time universe, strategy, eligibility, paper deployment, and autonomous paper-execution pipeline.

Phase 6 is complete only when the final exact deployed SHA has passed CI and production-like staging acceptance, including real-session paper execution acceptance where required.

No Phase 7+ work may be used to bypass a Phase 6 blocker.

## Long-term capability order

```text
Phase 6 — Stable PIT Research + Paper Execution
→ Phase 7 — Autonomous Research Factory (#74)
→ Phase 8 — News & Event Intelligence (#75)
→ Phase 9 — Model Validation, Alpha Attribution & Capacity (#76)
→ Phase 9/10 — Portfolio Intelligence & Capital Allocation
→ Permanent Paper Laboratory
→ Live Readiness
→ possible future Controlled Live Portfolio (separate explicit project/decision)
```

The numbering after Phase 9 may be refined when those phases are formally specified. Ordering and gates matter more than provisional numbers.

## Capability roadmaps

### #74 — Autonomous hypothesis and strategy research

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

### #75 — News & Event Intelligence Layer

Dedicated point-in-time news/event capability:

- immutable source ingestion;
- `first_seen_at` / `known_at` causality;
- revision and provenance lineage;
- entity linking and event taxonomy;
- LLM-assisted extraction with explicit model/version lineage;
- news features for research and deterministic risk intelligence;
- no direct LLM-generated orders.

Issue: https://github.com/Bbambaaamm/Autonomous-Quant-Lab/issues/75

### #76 — Model Validation, Alpha Attribution & Capacity Framework

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
→ Permanent Paper Laboratory
→ Independent Risk & Governance
→ future Controlled Live Portfolio
```

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
- Attractive new features must not pre-empt unresolved blockers in the current phase.

## Planning principle

The project optimizes for the probability of discovering and safely validating durable net edge — not for producing the highest historical backtest.
