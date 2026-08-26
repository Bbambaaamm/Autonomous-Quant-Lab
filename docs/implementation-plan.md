# Aktuální implementation status

Tento dokument popisuje současný stav projektu po dokončení **Phase 1–9**. Historické zadávací specifikace a detailní completion evidence zůstávají v `docs/codex/`; aktuální projektový verdikt je v [`docs/project-audit.md`](project-audit.md).

## Souhrnný stav

| Fáze | Oblast | Stav |
|---|---|---|
| Phase 1 | Core domain, deterministic vertical slice, backtest foundation | **COMPLETE** |
| Phase 2 | Research foundation, validation, robustness, experiment evidence | **COMPLETE** |
| Phase 3 | Production research data platform, registry, PostgreSQL, Alembic | **COMPLETE** |
| Phase 4 | Persistent paper trading, portfolio risk, broker, reconciliation | **COMPLETE — AUDIT PASSED WITH FIXES** |
| Phase 5 | Automation & operations, scheduler/worker, leases, fencing, retry | **COMPLETE — AUDIT PASSED WITH FIXES** |
| Phase 6 | Market data, XNYS, PIT universe, immutable snapshots, multi-asset research | **COMPLETE** |
| Phase 7 | Paper performance monitoring and strategy lifecycle | **COMPLETE — AUDIT PASSED** |
| Phase 8 | Operator control plane / Next.js dashboard | **COMPLETE — AUDIT PASSED WITH FIXES** |
| Phase 9 | Security & production hardening | **COMPLETE — FINAL POST-MERGE VERIFICATION PASSED** |

## Aktuální autoritativní runtime

Research a paper runtime jsou oddělené. Autoritativní tok je:

```text
provider
→ validation / immutable observation revisions
→ XNYS calendar / corporate actions
→ PIT universe
→ immutable dataset snapshot
→ research experiment
→ explicit PAPER_CANDIDATE promotion
→ explicit deployment approval
→ validated current data
→ Strategy
→ Portfolio
→ RiskEngine
→ ExecutionEngine
→ PersistentPaperBroker
→ reconciliation
→ immutable performance monitoring
→ operator control plane
```

Žádný research nebo dashboard komponent neobchází `RiskEngine`, `ExecutionEngine`, persisted `HALTED` stav ani reconciliation gates.

## Phase 1–3 — research/data foundation

Implementováno a nadále regresně kryto:

- UTC-aware domain model a deterministické finanční výpočty;
- close T → nejdříve raw open následující session;
- adjusted prices pro signály, raw executable prices pro fills;
- FIFO accounting, commissions, slippage, split/dividend semantics;
- chronologický train/validation/OOS proces bez random splitu;
- robustness, cost stress, Monte Carlo a parameter stability;
- immutable experiment evidence a structured parameter/eligibility records;
- dataset/strategy/experiment registry, lineage, leaderboard a comparison;
- PostgreSQL production target a Alembic-only production schema bootstrap.

## Phase 4 — production paper trading & risk

Implementováno:

- persistentní paper account, orders, fills, positions a cash;
- MARKET/LIMIT lifecycle a deterministic partial fills;
- target-vs-actual portfolio flow;
- persisted risk decisions, notional/exposure/concentration/daily limits;
- persistentní HALT/RESUME safety state;
- idempotentní trading cycles, leases, concurrency protection a recovery-safe submission;
- reconciliation a immutable audit evidence.

Auditní opravy jsou zdokumentovány v [`phase4-audit.md`](phase4-audit.md).

## Phase 5 — automation & operations

Implementováno:

- persistentní schedules a deterministic occurrence identity;
- scheduler → `JobRun` materialization bez přímého ekonomického execution;
- PostgreSQL worker claim přes row locking / `SKIP LOCKED`;
- lease, heartbeat, fencing token a attempts;
- bounded retry, dead-letter a restart recovery;
- safe dispatch pouze do existujících Phase 4 paper služeb;
- operations API a worker/job health evidence.

Auditní opravy jsou zdokumentovány v [`phase5-audit.md`](phase5-audit.md).

## Phase 6 — market data, PIT & strategy expansion

Implementováno:

- provider abstraction a production current-data provider allowlist;
- canonical instruments a XNYS calendar přes `exchange-calendars`;
- append-only market observation revisions;
- causally-known corporate actions;
- point-in-time universes a coverage;
- immutable content-addressed dataset snapshots;
- multi-asset target portfolios;
- exactly-once ingestion/snapshot/experiment identities v PostgreSQL;
- explicit research → paper promotion/deployment boundary;
- validated current-data accessor oddělený od research replay;
- PostgreSQL E2E research → approved paper execution coverage.

Detailní invariants jsou v [`market-data.md`](market-data.md) a [`strategy-research.md`](strategy-research.md).

## Phase 7 — paper monitoring & lifecycle

Implementováno:

- explicit monitoring enrollment schváleného deploymentu;
- immutable OOS baseline lineage;
- XNYS daily paper performance snapshots;
- expected-vs-realized drift evaluation;
- lifecycle `ACTIVE`, `PAUSED`, `SUSPENDED`, `RETIRED`;
- fail-closed execution gate pro non-active monitoring;
- corporate-action handling pro paper ledger;
- žádný auto-retune, auto-experiment ani auto-deployment.

Autoritativní audit evidence: [`codex/phase7-complete.md`](codex/phase7-complete.md).

## Phase 8 — operator control plane

Implementováno:

- typed `OperatorReadModel` a `/operator/*` API;
- Next.js dashboard pro portfolio, monitoring, strategies, research, risk, data, operations a audit;
- HALT/RESUME a monitoring actions přes existující service boundaries;
- stable pagination a UTC-normalized filters;
- XNYS-aware data health;
- frontend lockfile guard a production build verification.

Autoritativní audit evidence: [`codex/phase8-complete.md`](codex/phase8-complete.md).

## Phase 9 — security & production hardening

Implementováno a post-merge ověřeno:

- threat model;
- bearer auth a backend-authoritative VIEWER/OPERATOR/ADMIN RBAC;
- signed expiring dashboard session a server-only role credentials;
- CSRF/Origin/Host/CORS boundary;
- rate limiting pro auth/read/mutation/HALT/RESUME;
- actor evidence v audit trailu;
- PostgreSQL 17 password auth a oddělená migrator/runtime role;
- runtime least privilege bez DDL;
- distroless non-root backend/frontend images;
- strict Trivy HIGH/CRITICAL blocking gates bez blanket `--ignore-unfixed`;
- SBOM generation;
- portable SHA-256 backup/restore a recovery proof;
- production-like PAPER smoke test.

PR #47 uzavřel poslední post-merge mezery v runtime image minimalizaci, strict container security a přenositelnosti DB backupu. Následný CI na výsledném `main` prošel všemi osmi GitHub Actions joby.

Autoritativní completion evidence: [`codex/phase9-complete.md`](codex/phase9-complete.md).

## Aktuální omezení / budoucí scope

Tyto body nejsou nedokončenými požadavky Phase 1–9; jsou explicitně mimo jejich deklarovaný scope:

- live trading, live broker, live credentials a live order path nejsou implementovány;
- process-local rate limiter předpokládá jednu backend repliku;
- HTTPS ingress je deployment responsibility;
- off-site backup transport/scheduling není automatizován;
- další fáze po Phase 9 zatím nemá autoritativní specifikaci.

## Verifikační baseline po Phase 9

Výsledný `main` po merge PR #47 je ověřen GitHub Actions CI s výsledkem PASS pro:

- `quality`
- `unit-research`
- `api`
- `frontend`
- `security`
- `integration-postgres`
- `container-build`
- `production-smoke`

Container gate provedl backend i frontend build, non-root/minimal-runtime kontroly, strict Trivy HIGH/CRITICAL scans a CycloneDX SBOM. PR #47 měl navíc úspěšný GitGuardian check bez detekovaných secrets.
