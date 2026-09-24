# Runtime architecture contract

This document turns the target architecture from issue #190 into a development contract. It is intentionally narrower than the full design history: these are the properties that must remain true after future changes.

## Non-negotiable runtime invariants

1. **Heavy work is not an always-on responsibility.** Market-data and research-heavy processing runs in bounded, short-lived child processes or an equivalent explicitly resource-bounded execution unit.
2. **Long-lived processes stay thin.** Backend, automation worker, event listener and frontend must not accumulate workload-sized in-memory state. The automation worker keeps its soft RSS recycle guard.
3. **Runtime data access is explicitly scoped.** Provider construction requires an explicit instrument scope. Corporate-action evidence uses the scoped loader; a request/job/listener path must not silently return to full-provider-history materialization.
4. **Market work remains one-shot and backpressured.** The automation worker checks host/cgroup capacity before spawning the one-shot market task process. The child processes at most one durable market task and exits. Provider HTTP work has an explicit request budget.
5. **PostgreSQL is the durable authority.** Queue state, leases/fencing, checkpoints, immutable receipts and audit/provenance stay persistent and restart-safe. An in-memory cache may optimize reads but may not become the only source of truth.
   Core paper-trading evidence (`risk_decisions`, `paper_fills`, `audit_events`, `risk_events`, `reconciliation_results`) is append-only: PostgreSQL rejects UPDATE/DELETE and the runtime role has no mutation privilege on these tables. Mutable execution state such as accounts, positions, cycles and orders remains updateable.
6. **Paper/PIT/causality invariants stay intact.** Resource optimization must not weaken paper-only execution, point-in-time semantics, idempotence, fail-closed behavior or immutable evidence.
7. **Production services keep measured CPU/RAM ceilings.** The current resource contract is enforced in CI against `docker-compose.production.yml`. A limit change is an architecture change, not a routine tuning edit.
8. **Staging deployment consumes built artifacts.** Normal staging deployment uses CI-built images/GHCR and must not reintroduce host-side builds as the standard deployment path.
9. **Production mutations have one control-plane boundary.** HTTP writes in production are accepted only under `/operator/`. Legacy, demo, paper and generic automation mutation routes may remain for development/regression compatibility, but production rejects them before handler execution. The frontend mutation helper independently enforces the same `/operator/` allowlist.

## Current measured production ceilings

| Service | RAM | CPU |
| --- | ---: | ---: |
| PostgreSQL | 2 GiB | 2.0 |
| Backend API + research child | 1536 MiB | 1.5 |
| Automation worker + market child | 1024 MiB | 1.5 |
| Alpaca event listener | 384 MiB | 0.5 |
| Frontend | 384 MiB | 0.75 |

These values are not arbitrary constants. They are the accepted result of #190 measurements. They can change only through the process below.

## Change protocol

A change that intentionally modifies one of the invariants above must be explicit. The PR must:

- link the issue/ADR that changes the architecture;
- describe which invariant changes and why;
- include before/after resource or query evidence appropriate to the change;
- preserve paper-only, PIT/causality, provenance, idempotence and restart safety;
- update this contract, `docs/runtime-resource-budget.md` and the architecture guard test in the same PR;
- pass the complete required CI and any staging/soak acceptance required by the affected runtime path.

Silent drift is not acceptable. A feature PR that merely needs to bypass the guard is a failed design review, not a reason to delete or weaken the guard.

## CI enforcement

The executable contract is `backend/tests/test_runtime_architecture_contract.py`.

It is deliberately executed from both the required `quality` and `unit-research` CI jobs. The main branch ruleset already requires both contexts, so ordinary PRs cannot merge while the architecture contract fails.

The guard currently checks:

- explicit bounded provider scope and scoped corporate-action evidence wiring;
- one-shot market task lifecycle and bounded provider request budget;
- backpressure + subprocess isolation for market work;
- soft RSS recycling of the long-lived worker;
- bounded snapshot scope, explicit range budget and streamed authoritative observation reads;
- durable market-pipeline retry/restart/idempotence regressions through required CI;
- production Compose CPU/RAM ceilings and absence of worker/listener host ports;
- that the architecture guard stays wired into both required CI contexts.

## Architecture control-plane ownership

`.github/CODEOWNERS` assigns the architecture-control-plane files to `@Bbambaaamm`.
The intended GitHub repository setting is **Require review from Code Owners** for the
`Protect main` ruleset. CODEOWNERS alone documents ownership; GitHub must have that
ruleset option enabled for the approval to be mechanically mandatory.

The ruleset itself is audited daily by
`.github/workflows/agent-ruleset-sync.yml`. A failed audit opens or refreshes
`[architecture] Protect main ruleset drift` so changes to required checks, strict
freshness, bypass actors, PR/deletion/non-fast-forward protection or CodeQL are visible.

## Developer / agent command

From `backend/`:

```bash
uv run pytest -q tests/test_runtime_architecture_contract.py
```

Read this file together with `docs/runtime-resource-budget.md` before changing worker, provider, market-data, corporate-action, research isolation, deployment or production Compose behavior.
