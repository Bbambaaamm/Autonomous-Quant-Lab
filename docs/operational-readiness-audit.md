# Phase 1–9 End-to-End Operational Readiness Audit

**Datum auditu:** 2026-08-26
**Auditovaný commit:** `8b064a68a922f323440379a31b95220811f97753`
**Rozsah:** aktuální větev odvozená z `main`; žádná Phase 10 ani změna produkčního kódu
**Verdikt:** **NOT READY**

## 1. Executive summary

Odpověď na hlavní otázku je **ne**. Repository obsahuje kvalitní a převážně dobře testované
stavební bloky pro point-in-time research, schválený paper runtime, persistentní ledger, risk,
automation a monitoring. Tyto bloky však v běžícím produktu netvoří jeden autonomní tok.

Forenzní trace našel dvě odlišné execution cesty:

1. zamýšlenou Phase 6 cestu `Phase6PaperExecutionService`, která rekonstruuje schválený deployment,
   vypočítá signál z adjusted close a předá targety Phase 4 risk/execution/broker vrstvě; tato cesta
   v auditovaném commitu nebyla zapojena do API ani workeru a retroaktivně používala open
   dokončené session; konkrétní timing blocker je nyní vyřešen v P0-A níže;
2. skutečnou cestu workeru `RUN_PAPER_CYCLE`, která načte lokální CSV cestu a uživatelem dodané
   `target_weights`. Nepoužije deployment, experiment, strategy implementation, PIT universe ani
   persistentní current-data feed.

Stejně tak není vystaven write workflow pro ingest, universe, snapshot, Phase 6 experiment,
promotion, deployment a approval. Dostupné research POST endpointy vytvářejí pouze demo experiment
nad repository fixture a ruční paper endpoint obchoduje fixture. Dashboard je v těchto oblastech
záměrně read-only. Z čisté databáze proto operátor bez vlastního Python skriptu nebo přímé práce se
service třídami nedokáže připravit systém k provozu; ani po této ruční přípravě scheduler nespustí
schválený Phase 6 deployment.

**Souhrn nálezů:** 3 BLOCKER, 3 HIGH, 4 MEDIUM a 3 LOW. PAPER-only, chronologická research
validace, persistentní risk/ledger a worker concurrency mechanismy jsou PASS. Phase 6 paper runtime
v auditovaném commitu porušoval execution-time kauzalitu popsanou v B3; P0-A tento konkrétní nález
nyní řeší bez změny celkového verdiktu.

## 2. Auditní metoda a ověřené zdroje

Audit nevychází pouze z dokumentace. Byly přečteny root instrukce, README, architektura, projektový
a Phase 4–9 audit/completion materiál, Makefile, CI, databázová, market-data, research, paper,
automation, monitoring, operations, dashboard a security dokumentace. Kódová kontrola zahrnula
všechny moduly `backend/src/quantlab`, Alembic modely/migrace, všechny backend testy, operator API,
všechny požadované frontend stránky, server actions a frontend testy.

Rozlišení v tabulkách:

- **runtime** znamená cestu dosažitelnou přes dodané API/CLI/worker;
- **library-only** znamená produkčně napsanou službu, kterou používají testy, ale produkt ji
  neorchestruje;
- **demo** znamená fixture nebo uživatelem předpočítané vstupy, nikoli Phase 6 provozní tok.

## 3. End-to-end architecture trace

| Krok | Vstup | Skutečná implementace | Výstup a persistence | Napojení na další krok | Stav |
|---|---|---|---|---|---|
| Market data | symbol, období, Stooq HTTPS | `StooqProvider`; `PersistentMarketDataService.ingest` | `market_data_ingestions`, immutable `market_observations`, `corporate_actions` | snapshot service | **library-only**; není ingest endpoint/job/CLI |
| PIT universe | instrument a membership intervaly | `PointInTimeUniverse`; ORM `UniverseDefinitionRecord`, `UniverseMembershipRecord` | `universe_definitions`, `universe_memberships` | snapshot coverage | **library-only**; pouze GET API |
| Dataset | provider, universe, as-of, období, coverage | `DatasetSnapshotService.build` | `dataset_snapshots`, manifest v3, content hash | `Phase6ExperimentRunner` | **library-only**; pouze GET API |
| Strategy identity | allowlisted strategy name/version | `STRATEGY_REGISTRY`, `StrategyRegistry` | `strategies` | experiment FK | registry existuje; chybí provozní registrace/workflow |
| Parameter experiment | VALID snapshot, parameter configs, split, SHA, seed | `Phase6ExperimentRunner` | `research_experiments` s selected params a OOS result | eligibility | **library-only**; POST vytváří jiný demo experiment |
| Backtest/validation | chronologické observations | `run_multi_asset`, metrics; legacy `BacktestEngine` | OOS metrics/result; legacy folds/parameter runs | selection/eligibility | Phase 6 selection PASS; API není napojeno |
| Eligibility | COMPLETED experiment | `Phase6EligibilityService.promote` + `DeploymentService.validate_experiment` | mutace `decision=PAPER_CANDIDATE` | deployment | **library-only**, bez endpointu/UI/audit eventu |
| Promotion/deployment | candidate + paper account | `DeploymentService.create/approve` | `strategy_deployments` (`PENDING_REVIEW` → `APPROVED`) | monitoring/runtime | **library-only**, bez endpointu/UI/audit eventu |
| Monitoring enrollment | APPROVED deployment + policy | `PaperMonitoringService.enroll` | policy, baseline, `paper_monitoring_runs` | Phase 6 execution gate | API existuje; UI neumí enrollment/policy create |
| Scheduler | ručně vytvořený schedule | `SchedulerService.tick` | `scheduled_jobs`, `job_runs` | worker claim | runtime PASS, ale schedule není market-session trigger |
| Worker | materializovaný run | `WorkerService` | `job_attempts`, lease, fencing, retry/dead-letter, heartbeat | executor | runtime PASS pro legacy job payload |
| Strategy signal | Phase 6 deployment + current DB feed | `Phase6PaperExecutionService.run` | target weights v paměti, cycle lineage | Phase 4 cycle | **není volán workerem/API** |
| Skutečný worker signal | CSV `dataset_path` + hotové `target_weights` | `WorkerService._default_executor` | žádná research/deployment lineage | Phase 4 cycle | **demo/legacy provozní cesta** |
| Portfolio/risk/execution | target weights, raw next bar | `TradingCycleService` → `ProductionRiskEngine` → `ExecutionEngine` | cycles, decisions, orders | broker | PASS |
| PaperBroker | schválený intent + raw OHLC | `PersistentPaperBroker` | cash, FIFO positions, orders, partial fills, costs | reconciliation | PASS |
| Monitoring | deployment-cycle link + ledger/current data | Phase 7 performance/evaluation services | immutable snapshots/evaluations | lifecycle/UI | PASS pouze pro Phase 6 service cycles |
| Evidence | DB records a audit events | operator read model + `/operator/audit` | audit/filter/pagination | operator | dílčí; promotion/deployment nemají úplný actor/event trail |

### Implementační mapa služeb, API, tabulek, UI a testů

- **Data:** `market_data.py`, `market_data_service.py`, `universe.py`; GET endpointy
  `/market-data/*`, `/universes`, `/datasets`; UI `/data`; testy `test_phase6*`,
  `test_xnys_calendar.py`, PostgreSQL E2E.
- **Research:** `multi_asset.py`, `research_engine.py`, `phase6_runtime.py`, `persistence.py`;
  read endpointy `/operator/research/*`; demo POST `/research/experiments`; UI `/research` a
  `/strategies`; Phase 2/3/6 research a leakage testy.
- **Promotion/deployment:** `Phase6EligibilityService`, `DeploymentService`; tabulka
  `strategy_deployments`; žádné endpointy ani UI actions; přímé service testy a Phase 6 PostgreSQL
  E2E.
- **Paper:** `phase4.py`, `phase6_runtime.py`, `phase7.py`; `/paper`, `/risk`, `/trading`,
  `/reconciliation` read/mutation API; UI `/paper`, `/risk`, monitoring detail; Phase 4/6/7 testy.
- **Automation:** `automation.py`, `worker.py`; `/automation/*`, `/operations/*`; UI
  `/operations`; Phase 5 a Phase 7 automation PostgreSQL testy.
- **Operations/audit:** `operator_read_model.py`, `api.py`, `security.py`; UI `/`, `/operations`,
  `/audit`; Phase 8/9 API, PostgreSQL a security testy.

## 4. PASS části

### Market-data a research invarianty

- XNYS kalendář je timezone-aware, zahrnuje DST/holiday/early-close a identita knihovny je v
  snapshot lineage.
- Normalizace odmítá neplatné OHLCV a non-session bary. Observation revisions jsou immutable;
  snapshot pinuje revision/hash i kauzálně známé corporate actions a kontroluje coverage.
- PIT membership používá `valid_from/valid_to` a `known_at`; static universe je explicitně
  označen bias-prone.
- Phase 6 experiment používá chronologické train/validation/OOS, selection pouze z validation a
  OOS právě jednou. Snapshot replay kontroluje obsahový hash, přesnou strategii, code SHA, seed a
  cost model.
- Multi-asset research engine odděluje adjusted close pro signál od raw open pro fill a jeho
  backtestová historie končí před executable session. Toto PASS se nevztahuje na
  `Phase6PaperExecutionService`; jeho tehdejší retroaktivní timing je nyní RESOLVED jako P0-A.

### Paper safety a účetnictví

- Správná ekonomická cesta v Phase 4 je Strategy/targets → Portfolio → RiskEngine →
  ExecutionEngine → PersistentPaperBroker. Risk approval je svázaný s intent fingerprintem.
- Risk rejection nevytvoří broker order; HALTED a unsafe reconciliation blokují risk-increasing
  execution. Resume vyžaduje úspěšnou reconciliation.
- MARKET/LIMIT lifecycle, partial fills, deterministic slippage, commission, cash, FIFO lot/basis,
  realized PnL, position a quantity constraints jsou persistentní a transakční.
- Není nalezen live broker, live credential flow, live endpoint ani runtime flag. Architecture
  test explicitně kontroluje PAPER-only hranici.

### Automation primitives

- Scheduler používá deterministickou occurrence identity a DB uniqueness.
- PostgreSQL claim používá `SKIP LOCKED`; worker má lease, heartbeat a monotónní fencing token.
  Dokončení/failure vyžaduje aktuální neexpirovaný lease.
- Retry je bounded exponential backoff, permanentní chyby končí FAILED a vyčerpané retry v
  DEAD_LETTER. Restart po ekonomickém commitu spoléhá na idempotentní Phase 4 cycle identity.
- Worker heartbeat, run/attempt/failure a dead letters jsou persistované a viditelné v read API.

### Monitoring, UI a security

- Monitoring ukládá immutable baseline, paper performance a evaluation; PAUSED/SUSPENDED/RETIRED
  stavy blokují execution fail-closed.
- `/`, `/paper`, `/risk`, `/data`, `/operations`, `/research`, `/strategies` a `/audit` čtou
  backend-authoritative data. Risk HALT/RESUME, reconciliation a monitoring lifecycle actions jsou
  skutečné server actions, nikoli dead buttons.
- Auth/RBAC, server-only bearer credentials, CSRF/origin/host/CORS ochrany a production fail-closed
  konfigurace jsou implementované. UI neobsahuje finanční business logiku.

## 5. BLOCKER findings

### B1 — RESOLVED — podporovaný Phase 6 control plane

Původní příčinou byla absence mutation orchestrace nad existujícími Phase 6 službami. Nový
autentizovaný ADMIN control plane pokrývá canonical instrument, PIT universe a membership, skutečný
provider ingest, validovaný snapshot, allowlisted multi-parameter experiment, explicitní promotion,
deployment create/approve a monitoring policy/enrollment. Actor a reason se ukládají do audit
evidence a domain identity zajišťují bezpečné retry. Podrobnosti a PostgreSQL důkaz jsou v
[`operational-readiness-remediation-b1.md`](operational-readiness-remediation-b1.md).

Tato změna **nemění celkový verdikt NOT READY**. M1 je implementováno a čeká na autoritativní CI acceptance; celkový verdikt se tím nemění.

### B2 — RESOLVED — worker provádí schválený deployment a strategii

Původní `RUN_PAPER_CYCLE` cesta přijímala lokální CSV a caller-supplied target weights. Nový
produkční contract `RUN_PAPER_DEPLOYMENT` snapshotuje pouze `deployment_id`; worker rekonstruuje
approved deployment a ACTIVE monitoring lineage a volá výhradně `Phase6PaperExecutionService`.
Legacy contract produkční API nevytvoří a executor jej fail-closed odmítá. JobRun persistuje
`deployment_id`, `monitoring_id` a `trading_cycle_id`; occurrence a Phase 4 cycle identity zachovávají
idempotenci. B3 next-open authority se nemění. Podrobnosti jsou v
[`operational-readiness-remediation-b2.md`](operational-readiness-remediation-b2.md).

B2 resolution **nemění celkový verdikt NOT READY**. H1, M3 a další P1/P2 findings zůstávají otevřené.

### B3 / P0-A — RESOLVED — retroaktivní missed-open fill

Phase 6, worker i Stage C orchestrace nyní fail-closed povolují raw daily open pouze v kauzálním
jednosekundovém XNYS open window a pouze s `observed_at` uvnitř tohoto knowledge cutoff. Pozdní provider retry ani
restart workeru nemohou historický open použít; JobRun končí auditovatelným `NO_ACTION` a
`MISSED_EXECUTION_OPEN` bez ekonomické změny. PostgreSQL CI vede regresi přes skutečný worker claim.
Podrobnosti jsou v
[`operational-readiness-remediation-p0-missed-open.md`](operational-readiness-remediation-p0-missed-open.md).

Tato konkrétní resolution **nemění celkový verdikt NOT READY**. Zejména chybějící production worker
service zůstává samostatným P0 blockerem.

## 6. HIGH findings

### H1 — Market data se automaticky neobnovují

Automation allowlist nemá ingest/data-readiness job a dokumentace to výslovně přiznává.
`AUTOMATION_ENABLED` je defaultně false a samotný worker žádný schedule nevytváří. Při další market
session tedy bez člověka nedorazí nový bar; Phase 6 accessor by správně odmítl missing/stale session,
zatímco legacy CSV job buď použije předem existující budoucí řádek, nebo permanentně selže.

### H2 — Dodaný externí provider neposkytuje corporate actions

Stooq adapter deklaruje `supports_actions=False` a `corporate_actions()` vždy vrací prázdný seznam.
Infrastruktura actions a causal adjustment je správná, ale reálný externí ingest ji nenaplní.
Split/dividend/delisting evidence by proto pro běžná equities nebyla úplná a adjusted signal série
nemůže být provozně důvěryhodná bez jiného ručního provideru/importu.

### H3 — RESOLVED: deployment pinuje úplnou runtime konfiguraci

Remediation H3 přidala verzovaný canonical manifest risku, sizingu, commission, slippage,
execution a ověřitelné artifact identity. Hash je součástí deployment identity, approval jej
auditně pinuje a PostgreSQL odmítá jeho následnou změnu. Deployment worker před ekonomickým
efektem manifest ověří a komponenty rekonstruuje přímo z něj; legacy deployment bez identity je
fail-closed. Podrobnosti a test evidence jsou v
`docs/operational-readiness-remediation-h3-runtime-identity.md`.

## 7. MEDIUM findings

### M1 — Eligibility je pouze strukturální a bez samostatného rozhodnutí

**M1 — IMPLEMENTOVÁNO, ČEKÁ NA AUTORITATIVNÍ CI ACCEPTANCE.** Phase 6 má verzovanou policy,
append-only `phase6_eligibility_decisions`, server-side actor/reason audit a samostatnou evaluaci.
Promotion nyní fail-closed vyžaduje integritně platný `ELIGIBLE` record se shodnou lineage a OOS
metrikami. Legacy `research_eligibility_checks` je explicitně neautoritativní. Stav nebude označen
`RESOLVED`, dokud nový PostgreSQL acceptance krok a standardní CI skutečně neprojdou.

### M2 — Promotion a deployment approval nemají úplný audit event/actor trail

Časy experimentu a deployment approval jsou persistované, ale service metody nepřijímají actor ani
reason a nevytvářejí obecný audit event. Z DB lze rekonstruovat co a kdy, nikoli kdo a proč.

### M3 — Scheduler není market-session aware

Schedule podporuje interval nebo daily wall-clock s timezone a misfire policy. Neplánuje occurrence
podle XNYS session close/open, nečeká na ingest readiness a neváže cycle na očekávanou session.
Kalendářová kontrola je až ve správném Phase 6 accessor, který worker nepoužívá.

### M4 — RESOLVED: kompletní PAPER-only operator workflow

Control Center a navazující Data, Research, Strategy, Monitoring a Operations stránky nyní zpřístupňují podporovanou cestu data → universe → snapshot → experiment → eligibility → explicitní promotion → deployment → approval → monitoring → autonomous PAPER. UI používá výhradně autoritativní operator endpointy, server-side RBAC/actor identity a auditní reason; runtime manifest, readiness a XNYS termíny pouze zobrazuje z backend read modelu. Implementace a mapování jsou v `docs/operational-readiness-remediation-m4-operator-ui.md`.

## 8. LOW findings

### L1 — Dvě veřejné demo mutace matou provozní kontrakt

Demo backtest/research a fixture paper cycle jsou ve stejném API procesu jako operator runtime.
Jsou PAPER-only, ale názvy bez výrazného development namespace mohou vést k mylnému dojmu, že
ověřují skutečný Phase 6 tok.

### L2 — Některé N/A jsou legitimní, jiné signalizují neprovedený bootstrap

První daily return a ukončení aktivního monitoringu jsou legitimně N/A. Na home/data/strategies
však N/A pro equity, cash, market-data success, cycle a lookback zůstane trvale, dokud se ručně
nevytvoří chybějící workflow. UI nerozlišuje „not initialized“ od provozního incidentu dostatečně
akčně.

### L3 — Dokumentace nadhodnocuje systémové propojení

README a architecture diagram prezentují jediný uzavřený tok, zatímco operations/automation
dokumentace na jiném místě přiznává chybějící refresh. Není zdokumentováno, že worker je legacy CSV
target executor a Phase 6 runtime je library-only.

## 9. Missing links mezi subsystémy

1. provider → žádný ingest command/API/job;
2. ingest → žádný universe/snapshot orchestration workflow;
3. snapshot → produkční experiment endpoint nepoužívá `Phase6ExperimentRunner`;
4. experiment → žádná promotion action;
5. candidate → žádná deployment create/approve action;
6. deployment → monitoring enrollment je jen raw API, ne kompletní UI workflow;
7. deployment → scheduled job nemá `deployment_id` contract;
8. worker → nevolá `Phase6PaperExecutionService`;
9. scheduler → nevytváří data refresh ani XNYS-aware paper occurrences;
10. promotion/approval → chybí actor/reason audit event;
11. deployment → chybí verzovaná risk/cost policy identity.

## 10. Manual steps, které stále vyžadují člověka

Pro skutečný Phase 6 pokus by člověk musel napsat/spustit vlastní Python orchestration, která:

1. vytvoří canonical instruments a PIT universe membership;
2. zavolá `PersistentMarketDataService.ingest(StooqProvider(), ...)` pro každý instrument;
3. případné corporate actions dodá jiným vlastním providerem/importem;
4. zavolá `DatasetSnapshotService.build` a ověří VALID coverage;
5. registruje přesnou strategy identity a spustí `Phase6ExperimentRunner` s parameter configs;
6. ručně zavolá `Phase6EligibilityService.promote`;
7. ručně zavolá `DeploymentService.create` a `approve`;
8. přes API/službu vytvoří policy a enrollment;
9. před každou session zopakuje ingest;
10. místo standardního workeru přímo zavolá `Phase6PaperExecutionService.run`, poté reconciliation,
    capture a evaluation.

Alternativní dokumentovaný worker workflow vyžaduje ručně vytvořit job s lokální CSV cestou a
hotovými target weights. To je funkční Phase 4 automation demo, nikoli autonomní Phase 1–9 tok.

## 11. Co se stane při jednom skutečném market cycle

### Pokud systém pouze „necháme běžet“

- `AUTOMATION_ENABLED=false` ve výchozím nastavení: worker zapisuje heartbeat, ale scheduler ani
  executor nic neprovedou.
- Po explicitním zapnutí, ale bez ručně vytvořených jobs: scheduler nemá co materializovat.
- S ručně vytvořeným `RUN_PAPER_CYCLE`: v due wall-clock čase vznikne `JobRun`, worker jej claimne,
  načte lokální CSV a předané target weights. Najde první bar po `scheduled_for`; chybí-li, run
  skončí permanentním FAILED. Existuje-li, Phase 4 vypočte delta quantities, risk rozhodnutí,
  accepted/rejected orders, raw-bar fills, slippage/commission, cash/positions/PnL a cycle uloží.
  Deployment/experiment/monitoring lineage nevznikne. Market-data DB se neobnoví.

### Co provede nezapojená Phase 6 služba

Ověří APPROVED deployment a právě jeden ACTIVE monitoring, immutable experiment lineage, PIT
USD/XNYS universe a account; aplikuje corporate actions; načte completed-session data a předchozí
lookback close data; adjusted close předá allowlisted strategy a target weights převede na Phase 4
cyklus. Risk každý intent schválí nebo odmítne a služba cyklus sváže s deployment/monitoringem.
V auditovaném commitu současně nastavovala fill time na již zmeškaný open. P0-A nyní tento stav
odmítá podle skutečného run a knowledge time; následná performance capture/evaluation zůstává
samostatný job.

## 12. Failure-mode tabletop

| Scénář | Skutečné chování | Fail closed / evidence / recovery |
|---|---|---|
| Data nedorazí | Phase 6 accessor vyhodí `DatasetInvalid`; legacy worker postrádá další CSV bar | closed; worker chyba je permanentní, ale accessor není zapojen |
| Dataset invalid | snapshot/runner odmítne experiment a promotion | closed; ingestion/snapshot musí člověk opravit |
| Strategy exception | Phase 6 direct call selže před Phase 4; worker strategii nevolá | ekonomicky closed; chybí standardní job evidence pro Phase 6 |
| Worker spadne | lease expiruje, nový worker převezme s fencing tokenem | recoverable, idempotentní Phase 4 identity |
| DB spojení vypadne | readiness fail; DB chyby jsou retryable | bounded retry/dead-letter; po obnově lease/retry |
| Job se spustí dvakrát | occurrence unique, claim SKIP LOCKED, cycle key deterministic | closed/idempotentní pro stejnou identitu |
| Restart během cycle | economic commit je oddělen; stejný cycle se načte | recoverable, následně reconciliation |
| Risk odmítne order | decision persistuje, broker order nevznikne | closed a auditovatelné; cycle může dokončit s rejectem |
| Chybí executable price | Phase 4/6 odmítne chybějící next raw bar/open | closed; důvod v cycle/job failure podle cesty |
| Market zavřený / open zmeškaný | worker a Phase 6 vrátí `MISSED_EXECUTION_OPEN` | closed; bez orderu, fillu a ledger změny; P0-A |
| Stale data | Phase 6 vyžaduje poslední dokončenou session | closed; worker CSV cesta kontroluje pouze bar po decision time |
| Neplatný deployment | Phase 6 service odmítne status/lineage/monitoring | closed, avšak standardní worker deployment ignoruje |

## 13. Evidence a audit trail

### Hypotetický Phase 6 experiment

Z `research_experiments` lze přes FK/manifest dohledat dataset snapshot, universe, observation
revisions/actions, strategy identity/version, parameter space, selected parameters, code SHA, seed,
cost model a OOS metrics/equity/returns. To je silná immutable research evidence. Slabý skok je
promotion: pouze mutable decision field, bez samostatného Phase 6 eligibility recordu, actor/reason.

### Hypotetický Phase 6 paper cycle

Při přímém použití správné služby `paper_deployment_cycles` sváže monitoring, deployment a trading
cycle; přes deployment lze dojít k experimentu/snapshotu/strategii/parametrům. Cycle vede k risk
decisions, orders, fills a účtu/positions; performance snapshot zachytí výsledný stav. Chybí pin
schválené risk/cost konfigurace a actor/reason promotion/approval. Standardní worker cycle navíc
nemá deployment link vůbec, takže mezi experimentem a ekonomickým rozhodnutím vzniká
neauditovatelný skok přes ručně zadané target weights.

## 14. Test results

### Původní Codex task runner

Lokální omezení původního auditu zůstávají relevantní pouze jako popis daného runneru, nikoli jako
aktuální autoritativní stav ověření PR:

- `uv lock --check` a `uv sync --locked --all-groups` — **BLOCKED BY ENVIRONMENT**: runner měl
  `uv 0.7.22`, projekt vyžadoval přesně `0.12.3` a instalace přes PyPI skončila HTTP 403;
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src/quantlab` a
  `uv run pytest` — **BLOCKED BY ENVIRONMENT** před spuštěním kvůli uv version gate;
- PostgreSQL integration, PAPER-only, Phase 6–9, security, container a production-smoke suites —
  **BLOCKED BY ENVIRONMENT**, protože dependencies nebylo možné synchronizovat a runner neměl
  Docker binary;
- `npm ci` — **BLOCKED BY ENVIRONMENT** kvůli HTTP 403 při stažení locked Next.js tarballu;
- `npm run lockfile:check` — **PASS** (2/2 Node testy a strukturální kontrola lockfilu);
- `npm run lint`, `npm run typecheck`, `npm test` a `npm run build` — **BLOCKED BY ENVIRONMENT** po
  neúspěšném `npm ci`, protože chyběly eslint/next/vitest dependencies.

### Autoritativní GitHub Actions CI pro PR #52

Standardní repository CI následně ověřilo head PR #52 ve vybaveném GitHub Actions prostředí.
Dokončené joby měly stav `success`:

- **quality — PASS:** locked backend dependencies, Ruff format/check a mypy;
- **unit-research — PASS:** research, Phase 6/7, XNYS a PAPER-only architecture test selection;
- **api — PASS:** vertical slice a Phase 7–9 API/security test selection;
- **integration-postgres — PASS:** Alembic upgrade a PostgreSQL Phase 3–9, concurrency, recovery a
  end-to-end test selection;
- **frontend — PASS:** pinned toolchain, lockfile check, `npm ci`, lint, typecheck, test a build;
- **security — PASS:** Python static/dependency audit, npm audit a repository secret/misconfiguration
  scan;
- **container-build — PASS:** backend/frontend build, non-root/minimal-runtime kontroly, blocking
  HIGH/CRITICAL Trivy scans a CycloneDX SBOM;
- **production-smoke — PASS:** production-like PAPER smoke test.

Původní zelené CI potvrzovalo pouze tehdejší implementovaný stav. P0-A proto přidává samostatný
PostgreSQL worker-path causality krok; celkový verdikt nadále závisí i na ostatních blockerech.

## 15. Operational readiness verdict

# NOT READY

Autonomous Quant Lab dnes nelze začít používat jako autonomní PAPER trading laboratoř od čistých
market dat. Jednotlivé bezpečnostní a kvantitativní komponenty jsou nadprůměrně robustní, ale
produkční orchestrace končí před Phase 6 a worker vykonává jiný, legacy CSV/target workflow.

Verdikt se netýká live tradingu (ten správně neexistuje) ani nepožaduje Phase 10. Jde o chybějící
integraci již implementovaných Phase 1–9 schopností.

## 16. Minimální remediation plan

1. **P0-A — RESOLVED:** Phase 6 paper timing dovolí fill pouze v prokazatelném XNYS open knowledge
   instantu; worker-path regrese odmítá pozdní provider response, retry i restart bez ekonomické
   změny.
2. **P0:** definovat jediný versioned `RUN_PAPER_DEPLOYMENT` job contract s `deployment_id` a teprve
   po opravě B3 zapojit worker na `Phase6PaperExecutionService`; odstranit možnost ekonomického
   rozhodnutí z dodaných `target_weights` z produkčního job contractu.
3. **P0:** dodat autentizované/RBAC service endpoints nebo podporované CLI pro instrument/universe,
   ingest, snapshot, Phase 6 experiment, promotion, deployment create/approve a enrollment. Každá
   mutace musí mít actor, reason/idempotency a audit event.
4. **P0:** přidat data-refresh/data-readiness orchestration před cycle a XNYS session-aware
   materializaci; provider failure/staleness musí vytvořit viditelný no-action/failure stav.
5. **P1:** použít provider s auditovatelnými corporate actions (nebo explicitně omezit universe a
   failnout při chybějící action coverage); přidat split/dividend/delisting provozní alarmy.
6. **P1:** verzovat a pinovat risk policy, commission a slippage identity v deployment/cycle
   evidence; approval musí dokazovat použitou konfiguraci.
7. **P1:** vytvořit immutable Phase 6 eligibility/promotion/approval decisions včetně kritérií,
   metrik, actor identity, důvodu a timestampu.
8. **P2:** doplnit UI actions pro podporovaný workflow, nebo UI jasně odkazovat na autoritativní CLI;
   odlišit uninitialized, stale, incident a legitimní N/A.
9. **Acceptance gate:** PostgreSQL E2E test musí z prázdné DB provést provider fixture ingest → PIT
   snapshot → multi-parameter experiment → promotion → approval/enrollment → scheduled worker cycle
   → risk/order/fill → monitoring/evidence a musí zahrnout future-data mutation, duplicate/restart,
   stale/missing data a market-closed scénáře. Teprve poté zopakovat všechny quality, security,
   frontend, container a production-smoke gates.
