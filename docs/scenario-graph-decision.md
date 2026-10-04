# Scenario Graph Shadow Engine — Phase 1 Decision Record

**Issue:** #271 — P2: Scenario Graph Shadow Engine
**Date:** 2026-10-04
**Status:** Phase 1 complete (build-vs-integrate decision)

## Decision: Build own minimal implementation (option 3)

### MiroFish license analysis

MiroFish (https://github.com/666ghj/MiroFish) is licensed under **AGPL-3.0** (GNU Affero General Public License v3.0).

AGPL-3.0 is a strong copyleft license. Key implications:

- **Direct integration (option 1):** If MiroFish code is incorporated into QuantLab (even as a sidecar communicating over a network), the combined work may be considered a derivative work under AGPL. This would require QuantLab to be licensed under AGPL-3.0, which is incompatible with QuantLab's proprietary/paper-only model.
- **Fork/adapt (option 2):** Any fork or adaptation of MiroFish inherits AGPL-3.0. The fork must remain AGPL-3.0, making it legally incompatible with QuantLab's proprietary codebase.
- **Build own (option 3):** A clean-room implementation using general principles (knowledge graphs, multi-agent simulation, scenario propagation) without copying MiroFish source code is legally safe. No license contamination.

### Evaluation matrix

| Criterion | Direct integrate | Fork/adapt | Build own |
|---|---|---|---|
| License/compliance | FAIL (AGPL copyleft) | FAIL (AGPL inherited) | PASS |
| Reproducibility | Low (LLM-dependent, stochastic) | Low (same) | High (deterministic core) |
| Deterministic/versioned inputs | Partial | Partial | Full |
| Resource cost | High (thousands of agents, LLM calls) | High | Low (bounded, configurable) |
| Model/provider coupling | High (OpenAI SDK, Zep Cloud) | High | None (rule-based core) |
| Auditability | Low (black-box LLM) | Low | High (deterministic, traceable) |
| Security/isolation | Medium (sidecar) | Medium | High (in-process, no network) |
| Shadow capability | Partial | Partial | Full (no execution path) |
| Maintenance burden | High (external dependency) | High (fork drift) | Low (own code) |

### Decision rationale

1. **License is a hard blocker.** AGPL-3.0 is incompatible with QuantLab's proprietary model. Options 1 and 2 are legally non-starts.
2. **Determinism is required for reproducible research.** MiroFish's LLM-dependent simulation is inherently stochastic. A rule-based scenario propagation engine can be fully deterministic.
3. **Resource bounds are mandatory.** MiroFish spawns thousands of agents with LLM calls. QuantLab's resource contract (#190) requires bounded, short-lived processing.
4. **Shadow-only is the default.** The engine must have zero execution authority. A clean-room implementation makes this auditable.
5. **No external dependencies.** The engine uses only Python stdlib + existing QuantLab dependencies (SQLAlchemy, Pydantic).

### Non-goals (preserved from issue)

- No new news ingestion (uses #75's PIT-safe events)
- No live trading
- No MiroFish mandatory dependency
- No scenario probabilities without calibration verification

## Architecture

```
PIT-safe events (#75)
→ Entity/relationship graph (in-memory, bounded)
→ Scenario propagation (deterministic, rule-based)
→ Structured scenario output (canonical contract)
→ [optional] Forecast modifier → #266 → #269
```

The engine is a pure function: `events + graph_config → scenario_output`. No I/O, no network, no LLM calls in the core path.

## Hard caps (resource bounds)

| Parameter | Default | Rationale |
|---|---|---|
| max_nodes | 500 | Prevents graph explosion |
| max_edges | 2000 | Prevents relationship explosion |
| max_agents | 50 | Bounded simulation |
| max_scenarios | 10 | Bounded output |
| max_runtime_ms | 5000 | Short-lived processing |
| max_events_per_run | 1000 | Bounded input |
| max_propagation_depth | 5 | Prevents infinite chains |

## Next steps

- Phase 2: Implement canonical scenario contract (in progress)
- Phase 3: Shadow forecast contribution (future slice)
- Phase 4: Incremental-value experiment (future slice)
