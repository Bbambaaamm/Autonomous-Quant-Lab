# B2 — Approved deployment worker integration

**Datum:** 2026-08-26  
**Rozsah:** pouze B2; bez H1, M3, Phase 10 a bez live tradingu.

## Původní mezera

Produkční worker podporoval `RUN_PAPER_CYCLE`. Immutable snapshot nesl lokální `dataset_path`,
caller-supplied `symbol` a hotové `target_weights`; executor četl CSV a volal Phase 4
`TradingCycleService` bez deploymentu, experimentu, allowlisted strategie, PIT universe a Phase 7
monitoringu. Schválený Phase 6 deployment proto nebyl dosažitelný z workeru.

## Nová produkční cesta a contract

Jediný podporovaný autonomous PAPER contract je `RUN_PAPER_DEPLOYMENT` s přesným payloadem
`{"deployment_id": "<immutable deployment identity>"}`. Operator mutation
`POST /operator/deployments/{deployment_id}/jobs` vyžaduje serverově odvozeného ADMIN principal,
reason a APPROVED deployment; account se odvodí z deploymentu. Obecná automation mutation nesmí
vytvořit žádný paper execution job.

Tok je `ScheduledJob → JobRun → deployment_id → Phase6PaperExecutionService → Strategy →
Portfolio → RiskEngine → ExecutionEngine → PersistentPaperBroker`. Worker nečte soubor, nepřijímá
symbol, parametry, ceny ani target weights a nevolá broker.

## Gates a klasifikace

Phase 6 znovu ověřuje APPROVED stav, experiment/strategy/parameters/snapshot/PIT universe/account
lineage, allowlist, current data, reconciliation/risk stav a právě jeden ACTIVE monitoring run.
PAUSED a SUSPENDED vrací auditovatelný `BLOCKED_BY_LIFECYCLE` bez ekonomického účinku; RETIRED nemá
open monitoring context a failne uzavřeně. Neplatná lineage je permanentní failure. Chybějící
execution data nebo dosud nezačatá executable session jsou bounded retry přes existující retry a
dead-letter model.

## Evidence a idempotence

Job occurrence zůstává deterministická a unikátní. JobRun snapshotuje deployment identity a po
výsledku ukládá `deployment_id`, `monitoring_id`, `trading_cycle_id`, outcome a no-action reason.
Phase 7 cycle lineage propojí monitoring/deployment s trading cycle; orders a fills již odkazují
cycle. Retry stejné occurrence znovu používá deterministickou Phase 4 identitu
`account + phase6:deployment + execution session`, takže nevytvoří druhý cycle/order/fill.

## Zachování B3

Worker předává skutečný aktuální UTC čas do `Phase6PaperExecutionService`. Nemění `as_of`,
neposkytuje synthetic open a nečte budoucí data. Authority zůstává Phase 6:
`adjusted close T → decision_time close T → next XNYS session → raw open T+1`, přičemž před
executable open nevzniká economic side effect.

## Legacy disposition

`RUN_PAPER_CYCLE` zůstává v enumu pouze kvůli čitelnosti historických snapshotů a test fixtures.
Produkční API jej nevytvoří a production executor jej vždy odmítne jako permanentní legacy demo
contract. Neexistuje fallback na jeho payload.

## Důkaz a zbývající findings

B2 regression pokrývá minimal payload, odmítnutí legacy CSV/target contractu, lifecycle gating,
lineage a PAPER-only boundary. PostgreSQL acceptance je explicitní samostatný CI krok.

Tato remediation neřeší H1 (automatic market-data refresh), M3 (XNYS/session-aware scheduler), H2
(provider corporate actions) ani H3 (risk/cost deployment identity). Celkový systém proto nelze
jen na základě B2 označit jako READY.
