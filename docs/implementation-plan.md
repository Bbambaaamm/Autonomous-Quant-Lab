# Implementační plán po Phase 5

**Automation & Operations Foundation Phase 5: COMPLETE v deklarovaném scope.** Persistentní
interval/daily schedules, misfire policy, logical runs, attempts, PostgreSQL claiming,
lease/heartbeat/fencing, bounded retry, dead-letter, recovery, operator API a health foundation
jsou implementované. Mimo scope zůstává auth/RBAC, plný observability stack, deployment a live
trading; tato položka neoznačuje celý master plán za dokončený.

Phase 4 Production Paper Trading & Risk Foundation je dokončena: persistentní risk, paper broker, idempotentní cycle, reconciliation, audit a API.

Stavy níže popisují skutečný stav vůči celému `CODEX_MASTER_PROMPT.md`, nikoli jen vůči
dílčímu scope dosavadního milníku. Audit Phase 3 skončil **PASS WITH FIXES**: opravil metriku
expozice, která používala počet fillů místo počtu oceňovacích období, a odstranil zastaralá
tvrzení o CI, Dockeru, PostgreSQL a Alembicu.

| Fáze / oblast | Stav | Implementováno | Konkrétní remaining scope |
|---|---|---|---|
| 0 Repository bootstrap | PARTIAL | Python package, AGENTS, Makefile, základní konfigurace a commitnutý `uv.lock` | doplnit licence a úplnou cílovou monorepo strukturu |
| 1 Domain model | PARTIAL | UTC Bar, target, order/fill, risk decision, paper account, cycle, reconciliation a audit | úplné modely quote, market calendar a multi-asset portfolio |
| 2 Market data | PARTIAL | CSV/Parquet, content hash, quality kontroly, jednoduchý kalendář | reálný provider, quote/metadata API, úplný exchange kalendář, DB quality events a point-in-time universe |
| 3 Strategy framework | PARTIAL | společný interface/factory, MA, buy-and-hold, Donchian | TSMOM, cross-sectional momentum, mean reversion, pairs, multi-asset context a deklarace podpor |
| 4 Backtesting | PARTIAL | next-open, raw fill/adjusted signal, FIFO, náklady, slippage, split/dividenda | multi-symbol portfolio, spread, limit/partial fill, další sizing a úplná sada metrik/benchmark statistik |
| 5 Validation | PARTIAL | chronologický split, walk-forward train/validation/OOS, one-shot OOS | embargo/purge dle potřeby, richer OOS reporty a statistické testy |
| 6 Research automation | PARTIAL | grid, runner, cost stress, seedované Monte Carlo, stabilita, eligibility | distribuované/background běhy, experiment queue, report artefakty a více strategií/universe |
| 7 Portfolio & Risk | COMPLETE | konfigurovatelné portfolio a denní limity, decisions, halt | short/multi-asset rozšíření je budoucí scope |
| 8 Paper Broker | COMPLETE | persistentní MARKET/LIMIT, partial fill, cancel, FIFO, costs a reconciliation | pokročilá mikrostruktura není cílem EOD modelu |
| 9 Automated trading cycle | COMPLETE | target-vs-actual, DB uniqueness, recovery-safe submission, reconciliation a audit | komplexní scheduler/worker je mimo Phase 4 |
| 10 REST API | PARTIAL | research plus paper account/portfolio/orders/risk/cycle/audit/reconciliation endpointy | auth, stabilní schemas a rozšířená OpenAPI |
| 11 Web dashboard | PARTIAL | minimální server-rendered demo stránka | Next.js/React/Tailwind aplikace, grafy a research/paper/risk obrazovky |
| 12 Observability | PARTIAL | persistentní audit s correlation/cycle/account/order identitou | metrics, alerting a centralizované logování |
| 13 Security | PARTIAL | žádný live broker, paper-only cesta, bezpečné env defaults, loopback DB | auth/RBAC, secret management, dependency/SAST scan, rate limits a produkční hardening |
| 14 CI/CD & infrastructure | PARTIAL | GitHub Actions quality/unit/API/PostgreSQL job, locked dependency sync, PostgreSQL Compose healthcheck a Alembic upgrade | aplikační image/service/healthcheck, Redis/worker a deploy pipeline |
| 15 Dokumentace | PARTIAL | README, architecture, database, risk, paper, live-safety, operations a reproducibility | domain/backtest/strategy/troubleshooting dokumenty |
| 16 End-to-end verification | PARTIAL | fixture→research, paper-cycle idempotence/concurrency/reconciliation a PostgreSQL CI | dashboard a distribuovaný provozní acceptance |

## Uzavření Phase 3

**Phase 3 research data platform: COMPLETE v deklarovaném scope.** Registry obsahuje neměnnou
dataset/strategy/experiment identitu, normalizované foldy, parameter runy a eligibility checks,
lineage, comparison a deterministic leaderboard. Alembic initial migration vytváří schema na
prázdné PostgreSQL DB a CI má samostatný PostgreSQL job. SQLite je pouze testovací adapter.

Phase 3 neznamená dokončení celého master plánu. Registry neukládá bary a repository nemá
worker, Redis, trading scheduler ani aplikační Docker image. Lokální Compose vystavuje
PostgreSQL pouze na loopback a používá trust autentizaci výhradně pro development.

## Verification audit po Phase 3

Audit znovu potvrdil next-open invariant: strategie dostane pouze prefix končící close T a fill
použije raw open T+1; signály používají adjusted close. Train, validation a OOS jsou chronologické
a OOS konfigurace se vybírá bez OOS dat. FIFO alokuje vstupní i výstupní komise a split upravuje
množství i jednotkovou bázi bez vytvoření P&L.

Opravená regrese metrik počítá exposure jako podíl oceňovacích timestampů, ve kterých existuje
otevřená pozice. Dříve jej odvozovala z počtu fillů, takže pozdní jediný vstup nesprávně vykázal
100% exposure a scale-in/out měnil jmenovatel bez vztahu k času.

## Doporučená Phase 4 – Production Paper Trading & Risk Foundation

Další etapa nemá rozšiřovat research strategie. Přesný doporučený scope je:

1. zavést typované `RiskDecision`, `Order`, `PaperAccount`, `TradingCycle` a `AuditEvent`;
2. rozšířit RiskEngine o portfolio exposure/leverage, concentration, denní loss/drawdown,
   order-count a daily-notional limity, vždy fail-closed;
3. odstranit hardcoded portfolio weight a propojit konstrukci s explicitními risk limity;
4. persistovat paper orders/fills/positions/cash/cycles s FK, immutable identitou a transakcemi;
5. implementovat idempotency keys, DB/advisory lock jednoho cycle a retry-safe submission;
6. doplnit PaperBroker lifecycle minimálně pro market order, reject/cancel a deterministický
   partial-fill model bez jakéhokoli live adapteru;
7. implementovat trading cycle výhradně v toku Strategy → Portfolio → RiskEngine →
   ExecutionEngine → PaperBroker, včetně stale-data guardu a restart reconciliation;
8. přidat unit, SQLite integration, PostgreSQL integration a crash/retry/concurrency E2E testy;
9. zdokumentovat risk pravidla, paper trading, provoz a live-safety boundary.

Mimo Phase 4 zůstávají live broker, live credentials, Next.js dashboard a nové strategie.

## Phase 6
Phase 6 je implementována jako provider → validace/immutable revisions → XNYS calendar/corporate actions → PIT universe → immutable snapshot → multi-asset target portfolio. Detailní invariants jsou v `docs/market-data.md` a `docs/strategy-research.md`. Žádná část nevytváří live execution path; automatický data refresh zatím není allowlistovaný job a refresh se provádí odděleně od trading cycle.

## Aktuální Phase 6 completion stav
Dokončen je persistentní transakční ingestion, DB snapshot builder s PIT coverage, read API, current-data accessor a forward schema pro experiment/deployment lineage. Zbývá produkční exchange-calendar dependency, kompletní IS/validation/exactly-once OOS multi-asset runner, PostgreSQL master/concurrency evidence a paper integration E2E. Phase 6 je proto `INCOMPLETE`; starý pre-Phase6 seznam výše není aktuálním remaining scope.
