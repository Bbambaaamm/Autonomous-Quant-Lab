# ADR 0009 — Scenario graph: licence and integration boundary

## Status

Accepted (2026-10-04). Issue #271 Phase 1 decision record.

## Context

Issue #271 asks whether a MiroFish-style knowledge-graph + multi-agent scenario simulation adds
measurable incremental value to existing QuantLab forecasts. The independent architecture review of
#271 (BLOCKER E) requires that the licence/integration boundary be decided **before** any code is
imported, forked, vendored or linked, and that the spike keep four distinct integration modes
separated:

1. using a principle / protocol (no code);
2. running a subprocess or network sidecar;
3. taking a direct code dependency or fork;
4. distributing or modifying the upstream code.

The upstream public MiroFish repository is licensed **AGPL-3.0** (strong copyleft, network-use
clause). QuantLab is a proprietary, PAPER-only research and paper-trading platform whose licence
model is incompatible with AGPL-3.0 obligations for a combined/derivative work.

## Decision

**Mode 1 only — clean-room use of the principle/protocol.** QuantLab implements its own minimal,
deterministic scenario graph engine using general, non-copyrightable principles (entity/relationship
graphs, bounded propagation, bounded agent population, structured scenario output). No MiroFish
source code is read, copied, translated, vendored, forked, linked, subprocessed or networked.

Modes 2, 3 and 4 are **not authorized** and must not be started without a superseding ADR:

- mode 2 (sidecar subprocess/network) — a separate process does not by itself avoid AGPL
  obligations for a combined work, and additionally introduces an unaudited network/execution
  surface, so it is refused for the same reason as mode 3;
- mode 3 (direct dependency / fork) — refused: AGPL-3.0 copyleft would propagate to the combined
  work;
- mode 4 (distribution / modification) — refused: same copyleft propagation, plus publication
  obligations.

Any future change of mode requires a new ADR that records the licence analysis, the compliance
position, the isolation/security review, and the exact import boundary.

## Consequences

- The scenario engine is a first-party, in-process, deterministic, resource-bounded leaf module with
  no I/O, no network, no LLM calls and no execution authority. It can be audited line by line.
- Determinism and reproducibility are achievable, unlike an LLM-dependent external simulator
  (see BLOCKER A and BLOCKER D).
- QuantLab gains no upstream maintenance burden, no model/provider coupling and no AGPL exposure.
- Capability coverage of the upstream demo (multi-agent, event propagation, scenario set) is
  reproduced in a bounded, rule-based form; the *value* question is answered by the paired ablation
  in the Phase 4 harness, not by the integration.

## Invariants protected

- PAPER-only trading boundary (no live broker, live role, live endpoint, live flag).
- No new news ingestion outside issue #75; scenario input is the PIT-safe canonical event snapshot.
- No scenario-derived forecast may reach a probability forecast path without #266 lineage and #269
  calibration evidence.
- The scenario engine cannot mutate RiskEngine limits, expand Herdr parent authority, or place
  orders.
- Point-in-time causality: no node, edge or event known after `as_of` may enter a historical run.

## Related issues / PRs

- Issue #271 (this decision), #75 (PIT-safe canonical events), #266 (Forecast Ledger),
  #269 (calibration/scoring), #272 (statistical trial registry), #190 (resource-bounded runtime).
