# Scenario Graph Shadow Engine — Phase 3 & Phase 4

**Issue:** #271 — P2: Scenario Graph Shadow Engine
**Contract:** `scenario-graph-v2`
**Status:** Phase 3 (shadow forecast contribution) + Phase 4 (incremental-value harness)
implemented; the Phase 4 experiment itself is **not yet run** — it requires live #75 PIT-safe
events, #266 Forecast Ledger lineage and #269 calibration evidence on `main`.

## Phase 3 — shadow forecast contribution

The scenario engine has no execution authority. Its output can only enter QuantLab as one of:

- a **research feature** (`ScenarioOutput` stored as shadow evidence);
- a **forecast modifier / ensemble input** (`build_scenario_modifier`);
- a **risk-intelligence candidate** (bounded direction + uncertainty);
- **explanatory scenario evidence** (`assumptions`, `evidence_refs`, `propagation_path`).

`scenario_graph_ablation.build_scenario_modifier(baseline_probability, scenario_score, max_shift)`
is the only sanctioned bridge from scenario output into a probability forecast. It is deliberately
weak:

- the scenario quantity is **not** a probability — it is a `ScenarioScore` whose kind is
  `SCENARIO_SCORE` / `AGENT_VOTE_FRACTION` / `MODEL_CONFIDENCE`;
- the modifier is a bounded shift (`max_shift <= 0.5`) so an uncalibrated signal cannot dominate;
- `ScenarioScore(kind=CALIBRATED_PROBABILITY, ...)` cannot even be constructed without an explicit
  `calibrator_id` and a `calibration_report_ref` produced by #269.

Any scenario-derived forecast that enters the probability path must carry a #266 `forecast_ref`
and a #269 `calibration_report_ref`; the ablation harness refuses to consider a promotion claim
for opportunities that lack either.

## Phase 4 — incremental-value experiment harness

`backend/src/quantlab/scenario_graph_ablation.py` implements the mandated ablation:

```
baseline model
vs.
baseline + scenario graph
```

on **identical** opportunities, with the same target spec, funnel version and cost model
(STAT-BLOCKER E). It reports:

| Requirement | Where it is measured |
|---|---|
| Brier / log-loss delta | `PairedDelta.brier_delta`, `PairedDelta.logloss_delta` (baseline − scenario; positive = improvement) |
| Calibration delta | via #269: the harness requires `calibration_report_ref` per opportunity |
| Net forecast information gain vs baseline | `logloss_delta` (log loss is the information measure) |
| OOS/paper performance delta after costs | out of scope here — the harness consumes resolved outcomes; net P&L belongs to #268/#270 |
| Robustness by event class/regime | `PairedDelta.direction_consistent` over `event_class:*` and `regime:*` groups |
| Latency | `AblationCostReport.total_runtime_ms` / `mean_runtime_ms` |
| Token/model/API cost | `AblationCostReport.total_tokens` / `total_model_calls` |
| False-positive / hallucination failure modes | `discovery_failed_classes`, `warnings`, `blockers` |
| Reproducibility stability | `AblationResult.fully_reproducible` + deterministic clustered bootstrap |

### Verdicts

| Verdict | Meaning |
|---|---|
| `PROMOTE` | No blockers; the paired clustered CI excludes zero in favour of the scenario arm. |
| `REJECT` | Evidence is admissible and well-powered, but the improvement is not significant. |
| `INSUFFICIENT` | The evidence itself is not promotion-grade (uncontrolled model knowledge, missing #266/#269 lineage, contaminated holdout, too few independent clusters, discovery failure). |

Promotion therefore requires **all** of: a controlled model knowledge cutoff, a fully
reproducible fingerprint, an uncontaminated holdout, at least
`MIN_CLUSTERS_FOR_PROMOTION = 30` independent real-world clusters, complete scenario discovery
coverage for the evaluated event classes, and a paired clustered confidence interval that
excludes zero.

## Blockers from the independent reviews and how they are answered

### Architecture review

| Blocker | Answer |
|---|---|
| **A — hidden temporal leakage from parametric LLM knowledge** | `ModelKnowledgeCutoff` + `TemporalKnowledgeStatus`. The default is `UNCONTROLLED`; only a provider-declared `cutoff_at <= as_of` yields `CONTROLLED`. An instruction to "use only this context" is explicitly *not* evidence (tested). `ScenarioOutput.temporal_knowledge_flag` carries `MODEL_TEMPORAL_KNOWLEDGE_UNCONTROLLED`, and `is_promotion_grade` is False for such runs. |
| **B — PIT knowledge graph, not modern backfill** | Every `EntityNode`/`RelationshipEdge` requires `known_at` (and optional `valid_at`) plus a `ProvenanceKind`. `_assert_pit_graph` rejects any node/edge known after `as_of`. Observed source-fact edges and model-inferred edges are typed apart and asserted disjoint in tests. |
| **C — untrusted content / prompt-injection boundary** | `UntrustedContent` carries text as data only. `ScenarioWorkerPolicy` is a module constant with every authority flag False and an allowlist of exactly one read-only resource. An AST test asserts the module imports no `socket`/`subprocess`/`urllib`/`os`/HTTP machinery. Structured output is schema-validated by `validate_scenario_output_schema`, which fails closed on missing keys, unknown keys, wrong types and invalid score kinds. |
| **D — reproducibility fingerprint** | `RunFingerprint` records contract version, prompt hash, system-instruction hash, model/provider/revision, temperature/top-p/seed/sampling policy, source snapshot hashes, graph builder version, graph content hash, graph config hash, agent config hash, raw output hashes and runtime version. `drift_components()` names exactly which components differ. `fully_reproducible` is False when the exact model revision or seed is unknown. |
| **E — licence/integration boundary** | `docs/adr/0009-scenario-graph-license-and-integration-boundary.md` records the decision *before* any code import: mode 1 only (clean-room principle use); modes 2/3/4 (sidecar, dependency/fork, distribution) are not authorized. A test asserts the ADR exists and that no module imports an upstream engine. |

### Statistical review

| Blocker | Answer |
|---|---|
| **A — agent consensus is not an empirical probability** | `ScenarioScore` with `ScenarioScoreKind`. Only `CALIBRATED_PROBABILITY` may be called a probability, and it cannot be constructed without a calibrator id and a #269 report reference. The engine always emits `SCENARIO_SCORE`; `weighting.calibrated` is False. |
| **B — Monte Carlo replicates are not market observations** | `simulation_replicates` is recorded on the output but `real_world_opportunities` is a separate field; the harness's effective sample size is `PairedDelta.n_clusters` (independent real-world clusters). A test proves that 20 replicates and 1 replicate give identical `n_clusters` and identical deltas. |
| **C — prompt/model/graph search is multiple testing** | `TrialFamilyLedger` + `TrialFamilyEntry`; every materially distinct `ScenarioVariant` (prompt, model, graph policy, agent population, aggregation rule, sampling policy) is declared, including losers. `evaluated_count` and `variant_count` are both reported. |
| **D — holdout burn for prompt engineering** | `TrialFamilyEntry.holdout_contaminated` propagates to `TrialFamilyLedger.contamination_flag()`, which becomes a hard `INSUFFICIENT` blocker — no incremental-value claim may rest on a burned holdout. |
| **E — paired ablation + uncertainty** | The harness is paired by construction on one opportunity list; the uncertainty is a **clustered block bootstrap** over `cluster_id` with a deterministic seed, reporting `ci_low`/`ci_high` at the configured level. A test proves the CI is deterministic and that the cluster count (not the row count) drives the uncertainty. |
| **F — scenario discovery vs probability estimation** | `ScenarioDiscovery` (coverage of candidate outcomes) and `ScenarioWeighting` (score mean/spread conditional on the set) are separate types. At the harness level, `DiscoveryReport` reports per-event-class coverage and a discovery failure is a hard blocker, so good calibration over an incomplete scenario set cannot mask a discovery failure. |

## Safety / governance — isolation, concurrency and preregistered degradation

`backend/src/quantlab/scenario_graph_isolation.py` is the operational boundary around the
pure engine. It exists because the engine is deliberately clock-free and I/O-free, while the
issue's safety clause requires timeout handling and a concurrency cap that need a clock and
mutable operational state.

| Issue requirement | Where it is enforced |
|---|---|
| hard cap on **concurrency** | `MAX_CONCURRENCY = 1` + `ScenarioConcurrencyGuard`; a second concurrent run fails closed with `SCENARIO_CONCURRENCY_LIMIT` (non-blocking, no queue) |
| hard cap on **model budget** | `MAX_MODEL_BUDGET_TOKENS = 0`, `MAX_MODEL_CALLS = 0`; `ScenarioGraphConfig` rejects any widening |
| error / timeout does not stop the core workflow | `ScenarioWorker.run` converts any engine error, over-budget runtime or rejection into a typed `ScenarioRunOutcome`; it never propagates |
| scenario-dependent strategy fails/degrades per preregistered policy | `ScenarioDegradationPolicy` — a `CORE_INDEPENDENT` workload may only `CONTINUE_WITHOUT_SCENARIO`, a `SCENARIO_DEPENDENT` workload may only `FAIL_CLOSED`; the unsafe pair is rejected at construction |
| no execution authority | the boundary adds no order/broker/risk surface (AST- and attribute-asserted) |
| bounded blast radius | a circuit breaker opens after `max_consecutive_failures` consecutive failures so a permanently broken engine stops consuming the runtime budget |

An over-budget or failed run returns **no partial output** (`output is None` unless the status is
`SUCCEEDED`), so a caller can never consume a degraded scenario artifact as a complete one.
The guard mirrors the runtime contract's heavy-research concurrency (1) and is asserted by
`test_runtime_architecture_contract.py`.

## Hard caps

| Parameter | Default | Purpose |
|---|---|---|
| `max_nodes` | 500 | graph explosion |
| `max_edges` | 2000 | relationship explosion |
| `max_agents` | 50 | bounded agent population |
| `max_scenarios` | 10 | bounded output |
| `max_runtime_ms` | 5000 | short-lived processing (#190) |
| `max_events_per_run` | 1000 | bounded input |
| `max_propagation_depth` | 5 | bounded propagation chains |
| `max_simulation_replicates` | 20 | bounded internal simulation |
| `max_model_budget_tokens` | 0 | no model budget in the shadow engine (cannot be widened) |
| `max_model_calls` | 0 | no model calls in the shadow engine (cannot be widened) |
| `MAX_CONCURRENCY` | 1 | in-process scenario concurrency (fail closed on contention) |
| `MAX_EVIDENCE_REFS` | 100 | bounded evidence payload |
| `MAX_BOOTSTRAP_REPLICATES` | 20000 | bounded uncertainty computation |

## How to run the experiment (future slice, once dependencies are on `main`)

1. Read the PIT-safe canonical event snapshot from #75 at a fixed `as_of`.
2. Run `ScenarioGraphEngine.run(...)` with a `ModelKnowledgeCutoff` whose `cutoff_at` is proven
   `<= as_of` (otherwise the run is `UNCONTROLLED` and cannot be promotion-grade).
3. Emit the resulting probability through #266 so it gets a `forecast_id` (→ `forecast_ref`).
4. Resolve outcomes and score through #269 (→ `calibration_report_ref`).
5. Feed the paired opportunity list into `ScenarioAblationHarness.evaluate(...)`.
6. Declare the variant in a `TrialFamilyLedger` **before** looking at the holdout.

## Not in scope for this slice

- Live or paper order generation — the engine has no execution path.
- A new news ingestion pipeline — input is #75's canonical snapshot only.
- A MiroFish dependency — see ADR 0009.
- Using scenario probabilities without calibration — blocked by `ScenarioScore`.
