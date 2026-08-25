# CODEX PROMPT — PHASE 8

## Phase 8 — Operator Control Plane, Stable Read API & Web Dashboard

Jsi principal software architect, senior quantitative developer, senior Python/TypeScript engineer, frontend architect, DevOps engineer, QA engineer a security reviewer v jednom.

Tvým úkolem je **dokončit celou Phase 8 v jednom uceleném PR** nad aktuálním stavem repository `Bbambaaamm/Autonomous-Quant-Lab`.

Nechci skeleton, mockup, statický prototyp, fake dashboard ani sérii dílčích PR. Chci skutečný, spustitelný, testovaný single-operator dashboard nad reálnými daty systému, který zachová všechny bezpečnostní a deterministické invarianty Phase 3–7.

---

# 1. AUTORITATIVNÍ STARTING STATE

Pracuj z aktuálního `main`.

V okamžiku zadání je authoritative Phase 7 merge:

```text
main: 50aac0bb9cf78e9cf8d91ce27da832b5a93cd38c
PR #33: merged
Phase 7: COMPLETE
Phase 7 audit gate: PASSED
```

Finální Phase 7 CI evidence:

```text
CI run #185:                      PASS
uv 0.12.3:                        PASS
uv lock --check:                  PASS
uv sync --locked --all-groups:    PASS
ruff format --check:              PASS
ruff check:                       PASS
mypy src/quantlab:                PASS
unit/research:                    120 passed
API:                              10 passed
PostgreSQL Phase 3–7:             204 passed
Alembic fresh upgrade:            PASS
Alembic head:                     20260824_02
PostgreSQL:                       17.11
```

Pokud je `main` při startu novější, nejprve ověř historii a použij **novější `main` jako jediný zdroj pravdy**. Nevracej repository na uvedený SHA.

Preferovaný branch:

```text
codex/phase8-operator-dashboard
```

Preferovaný PR title:

```text
Phase 8 — Operator Dashboard and Control Plane
```

---

# 2. NEJDŘÍVE OPRAV DOCUMENTATION TRUTH

Phase 7 je funkčně i auditně uzavřena, ale některé dokumenty na `main` ještě obsahují historický text typu `audit gate pending`.

Před nebo současně s Phase 8 oprav dokumentační pravdu:

- `docs/codex/phase7-complete.md`
- `docs/implementation-plan.md`

Musí po úpravě jednoznačně uvádět:

```text
PHASE 7: COMPLETE
PHASE 7 AUDIT GATE: PASSED
```

A finální evidence minimálně:

```text
unit/research 120 passed
API 10 passed
PostgreSQL Phase 3–7 204 passed
Alembic head 20260824_02
CI #185 success
```

Nezasahuj kvůli tomu do Phase 7 business logiky. Je to correction of documentation truth, ne znovuotevření Phase 7.

---

# 3. VYTVOŘ AUTORITATIVNÍ PHASE 8 SPEC

Vytvoř:

```text
docs/codex/phase8-complete.md
```

Tento soubor bude authoritative Phase 8 specification a completion record.

Musí popisovat skutečně implementovaný stav, ne plánované funkce.

---

# 4. PROČ JE PHASE 8 PRÁVĚ TOTO

Aktuální systém již umí:

- immutable research evidence,
- PIT/survivorship-safe datasets,
- multi-asset strategy research,
- explicitní research → paper promotion/deployment boundary,
- persistentní paper broker,
- risk engine,
- reconciliation,
- automation scheduler/worker,
- paper performance snapshots,
- expected-vs-realized Phase 7 evaluation,
- monitoring lifecycle,
- immutable audit evidence.

Master goal po těchto vrstvách vyžaduje skutečný webový dashboard.

Současný `backend/src/quantlab/api.py` stále obsahuje pouze historickou inline HTML demo stránku s tlačítkem na demo MA backtest. To **není produkční operator dashboard**.

Phase 8 proto není:

- nový quant research milestone,
- live trading,
- nový broker,
- automatické retuning,
- nový strategy deployment algoritmus,
- Prometheus/Grafana rollout,
- plný auth/RBAC/security hardening,
- cloud deployment pipeline.

Phase 8 je:

```text
Stable operator read model/API
        ↓
real Next.js/React dashboard
        ↓
clear paper/risk/data/automation/audit visibility
        ↓
limited explicit operator safety actions
```

---

# 5. PHASE 8 MISSION

Vytvoř skutečný **single-operator control plane** v češtině, který umožní operátorovi na jednom místě bezpečně a pravdivě vidět:

1. stav systému,
2. stav paper tradingu,
3. equity/performance a drawdown,
4. Phase 7 monitoring a expected-vs-realized verdict,
5. strategie a research evidence,
6. current positions/orders/fills,
7. risk stav a recent rejects,
8. data health a ingestion stav,
9. automation/jobs/workers/dead letters,
10. reconciliation,
11. audit history.

Dashboard musí používat **pouze reálná data API/persistence**.

Zakázáno:

```text
fake data
placeholder business metrics vydávané za reálné
random demo values
synthetic benchmark without persisted evidence
front-end recomputation that changes authoritative financial meaning
browser direct DB access
browser live-broker path
```

Pokud evidence není dostupná, zobraz:

```text
N/A
Not available
Insufficient data
```

s vhodným českým vysvětlením.

---

# 6. PAPER-ONLY INVARIANT JE ABSOLUTNÍ

Phase 8 nesmí vytvořit žádnou live execution path.

Musí platit:

```text
TRADING MODE = PAPER
LIVE BROKER = ABSENT
```

Frontend ani nový operator API nesmí:

- obsahovat live broker adapter,
- posílat live order,
- vytvářet live credential flow,
- přidat `enable live` tlačítko,
- přidat live promotion,
- obcházet RiskEngine,
- obcházet Phase 7 `ACTIVE` monitoring gate,
- obcházet HALTED,
- obcházet reconciliation safety.

Ve frontend UI musí být trading mode viditelný a jednoznačný.

---

# 7. NEIMPLEMENTUJ GENERICKÝ ADMIN PANEL

Nechci univerzální CRUD nad databází.

Nechci:

- arbitrary SQL,
- arbitrary endpoint proxy,
- arbitrary module import,
- raw table editor,
- editaci immutable evidence,
- ruční změnu risk decisions,
- ruční změnu fills/orders/positions,
- ruční změnu experiment metrics,
- ruční změnu monitoring/evaluation evidence.

UI musí odrážet domain model a existující služby.

---

# 8. FRONTEND STACK

Vytvoř nový skutečný frontend:

```text
frontend/
```

Použij:

```text
TypeScript
React
Next.js
Tailwind CSS
```

Použij aktuální stabilní a vzájemně kompatibilní verze podle oficiální dokumentace v době implementace.

Preferuj současný Next.js App Router.

Grafy přes lehkou stabilní React knihovnu vhodnou pro line/area/bar charts. Nepřidávej obrovský framework jen kvůli několika grafům.

Frontend musí mít commitnutý lockfile.

Preferuj npm, pokud repository nemá jiný frontend package manager:

```text
frontend/package-lock.json
```

Pinuj podporovaný Node LTS pro CI a dokumentuj jej, například přes `.node-version` nebo ekvivalent.

---

# 9. BEZPEČNÁ LOKÁLNÍ ARCHITEKTURA FRONTENDU

Toto je single-operator systém.

Phase 8 ještě neimplementuje plný auth/RBAC.

Proto default frontend/API provoz **nesmí být bezmyšlenkovitě vystaven do internetu**.

Default local run musí používat loopback:

```text
127.0.0.1
```

Frontend backend URL nesmí být tajemství a zároveň ji nevystavuj zbytečně jako browser-configured arbitrary host.

Preferuj:

```text
Next.js server components / server actions
→ explicitní backend API calls
```

Browser nesmí dostat generický arbitrary proxy endpoint typu:

```text
/proxy?url=<anything>
```

Pokud použiješ proxy/BFF route handlers, musí být endpoint allowlistovaný a typed.

Žádné secrets v `NEXT_PUBLIC_*`.

---

# 10. STABLE OPERATOR API — NEPOUŽÍVEJ RAW ORM JAKO DASHBOARD CONTRACT

Existující API obsahuje řadu historických endpointů vracejících raw `_row()`/`vars()` struktury. Zachovej je kvůli backward compatibility, pokud nejsou chybné, ale **nepoužívej raw ORM shape jako nový dlouhodobý dashboard contract**.

Vytvoř explicitní Phase 8 read-model/service vrstvu, například:

```text
backend/src/quantlab/operator_read_model.py
```

nebo lépe pojmenovanou ekvivalentní vrstvu.

Business agregace nesmí být nacpaná přímo do FastAPI route funkcí.

Použij explicitní Pydantic response schemas.

Financial exact values drž jako `Decimal` v backendu. Pro JSON contract zvol konzistentní bezpečnou reprezentaci; pokud použiješ string pro money/precise decimals, frontend smí převádět na JS `number` pouze na presentation/chart boundary.

Zakázáno je změnit účetní logiku kvůli pohodlí UI.

---

# 11. PREFEROVANÉ OPERATOR READ ENDPOINTS

Můžeš názvy mírně upravit, pokud budou konzistentní, ale preferovaný kontrakt je:

```text
GET /operator/overview
GET /operator/paper
GET /operator/paper/performance
GET /operator/monitoring/{monitoring_id}/comparison
GET /operator/strategies
GET /operator/strategies/{strategy_identity}
GET /operator/research/experiments
GET /operator/research/experiments/{experiment_id}
GET /operator/risk
GET /operator/data-health
GET /operator/automation
GET /operator/audit
```

Nepřidávej endpoint jen proto, aby existoval. Každý musí mít konkrétní dashboard consumer a tests.

Staré read endpointy mohou být interně využity pouze pokud jejich semantics jsou správné; preferuj service/read model nad přímým HTTP chainingem uvnitř backendu.

---

# 12. `/operator/overview`

Musí vracet minimálně:

```text
server_time_utc
trading_mode
live_trading_enabled
api_health
readiness
paper_account_id
trading_state
reconciliation_safe
latest_reconciliation_status
monitoring_id
monitoring_state
monitoring_verdict
paper_equity
paper_cash
cumulative_return
current_drawdown
position_count
open_order_count
last_trading_cycle
next_scheduled_paper_cycle
latest_completed_market_session
latest_market_data_status
automation_enabled
enabled_job_count
dead_letter_count
healthy_worker_count
stale_worker_count
```

Pokud některý údaj objektivně neexistuje, vrať `null`/typed unavailable state; nevymýšlej jej.

`latest_completed_market_session` musí používat authoritative XNYS calendar, ne `today()`.

---

# 13. PAPER READ MODEL

`/operator/paper` musí umět zobrazit:

```text
account
cash
marked equity
realized P/L
positions
orders
fills
latest reconciliation
monitoring lifecycle state
latest evaluation verdict
```

Unrealized P/L smí být uveden pouze pokud jej lze korektně odvodit z authoritative current mark + basis. Pokud ne, zobrazení musí být N/A.

Žádný stale `PaperAccount.equity` nesmí být vydáván za session-marked current equity, pokud Phase 7 snapshot poskytuje přesnější authoritative evidence.

---

# 14. PAPER PERFORMANCE SERIES

`/operator/paper/performance` musí číst authoritative Phase 7 persisted snapshots.

Podporované období:

```text
1M
3M
6M
YTD
1Y
ALL
```

Period filtering musí být deterministic podle session date.

Vrací minimálně:

```text
session_date
marked_equity
cash
daily_return
cumulative_return
drawdown
gross_exposure
net_exposure
turnover
commissions
slippage
order_count
fill_count
risk_rejection_count
```

Historické Phase 7 snapshots jsou immutable. Provider correction po capture nesmí změnit historickou dashboard řadu.

---

# 15. EXPECTED VS REALIZED UI SEMANTICS

Nesmíš vizuálně lhát.

OOS baseline a paper period jsou různá období.

Zakázáno je vykreslit OOS equity a paper equity na společné kalendářní ose tak, aby to vypadalo jako současně probíhající série.

V detailu monitoring/strategie zobraz:

- latest Phase 7 verdict,
- reasons,
- paper session count,
- baseline OOS metrics,
- paper metrics,
- policy thresholds,
- OOS baseline equity/returns jako **oddělenou historickou baseline**,
- paper series jako **realized paper**.

Pokud chceš srovnávací chart, používej například normalized index od `1.0` s jasným označením:

```text
OOS baseline path
Paper realized path
```

na ordinal/session-index ose, nikoli falešné společné calendar date ose.

Pokud percentile/rank nebyl persistován v immutable evaluation record, může Phase 8 read projection deterministicky dopočítat pouze display-only statistiku z pinned baseline/policy/snapshot evidence za použití stejné Phase 7 algoritmické funkce. Nesmí přepisovat historickou evaluation ani měnit její verdict.

Derived display statistic musí být explicitně označena jako derived projection.

---

# 16. DASHBOARD HOME

Vytvoř `/` s přehledným operator dashboardem.

Musí mít minimálně:

## System status

```text
API status
readiness
trading mode = PAPER
account trading state NORMAL/HALTED
reconciliation status
monitoring state
latest Phase 7 verdict
```

## Paper portfolio

```text
equity
cash
cumulative P/L / return
daily return (pokud existuje)
current drawdown
gross exposure
net exposure
positions count
```

## Operations

```text
last trading cycle
next expected paper cycle
automation enabled
dead letters
worker health
```

## Data health

```text
latest completed XNYS session
latest successful ingestion
failed/started ingestion warning
coverage / missing data indicator where evidence supports it
```

Každý kritický card musí obsahovat data freshness/as-of informaci.

---

# 17. PAPER PAGE

Vytvoř například:

```text
/paper
```

Musí obsahovat:

- equity curve,
- drawdown chart,
- period selector 1M/3M/6M/YTD/1Y/ALL,
- account summary,
- positions table,
- orders table,
- fills table,
- commissions/slippage,
- latest monitoring evaluation,
- link na monitoring detail.

Zero-trade session musí být v time series viditelná.

První daily return musí zůstat N/A/null, ne `0 %`.

---

# 18. MONITORING DETAIL

Vytvoř například:

```text
/paper/monitoring/[id]
```

Zobraz:

```text
state
state reason
started / ended
policy
baseline identity
strategy/version/parameters
OOS metrics
paper metrics
latest verdict
verdict reason codes
performance series
expected-vs-realized comparison
historical evaluations
```

Monitoring state musí používat přesné Phase 7 semantics:

```text
ACTIVE
PAUSED
SUSPENDED
RETIRED
```

RETIRED je terminal.

---

# 19. STRATEGIES PAGE

Vytvoř:

```text
/strategies
```

Řádek strategie má ukázat jen evidence, které skutečně existují, například:

```text
strategy name
version
required lookback
rebalance frequency
asset scope
latest experiment status
latest OOS Sharpe
latest OOS return
latest deployment status
monitoring state
latest paper cumulative return
latest Phase 7 verdict
```

Pokud `stability`, CAGR nebo jiná master-prompt metrika není v authoritative evidence dostupná, ukaž `N/A`. Nesmíš ji dopočítat z nesouvisejících dat jen proto, aby tabulka nebyla prázdná.

---

# 20. STRATEGY DETAIL

Vytvoř například:

```text
/strategies/[identity]
```

Zobraz dostupné:

```text
description/metadata
version
parameters
research experiments
immutable dataset/snapshot lineage
OOS metrics
cost model
paper deployment status
monitoring status
paper performance
expected vs realized
recent audit history
```

Pokud některé historické master-prompt UI položky (např. full trade distribution nebo general Monte Carlo UI) nejsou u současného authoritative Phase 6 experimentu persistovány, nefalšuj je. Ukaž jasně, že evidence není dostupná.

---

# 21. RESEARCH PAGE

Vytvoř například:

```text
/research
```

Phase 8 je primárně **read/observe**, ne nový generalized research write engine.

Zobraz:

- leaderboard,
- experiment list,
- filters,
- experiment detail,
- dataset/snapshot identity,
- strategy/version,
- parameters,
- code SHA,
- cost model,
- OOS metrics,
- decision (`RESEARCH_ONLY` / `PAPER_CANDIDATE` apod.).

Nevytvářej fake „Create Experiment“ UI nad demo endpointem.

Generalized experiment creation UI je mimo Phase 8, pokud backend nemá skutečný typed generalized experiment contract. Neimplementuj kvůli dashboardu novou research engine logiku.

---

# 22. RISK PAGE

Vytvoř:

```text
/risk
```

Zobraz minimálně:

```text
trading state
reconciliation_safe
current marked equity
current drawdown
gross/net exposure
position count
recent risk decisions
recent risk rejects
recent risk events
```

Pokud můžeš získat authoritative configured risk limits ze současné `ProductionRiskConfig`/runtime configuration bez hardcoded duplicace, zobraz je.

Pokud ne, přidej typed read projection z authoritative config.

Nedrž druhou kopii risk limitů pouze ve frontendu.

---

# 23. HALT / RESUME UI

Master dashboard požaduje operator HALT/RESUME.

UI smí používat pouze existující bezpečnou backend service path.

## HALT

Musí vyžadovat explicitní potvrzení, například:

```text
zadej HALT
+ auditní důvod
```

Nesmí být jedno-click accidental action.

## RESUME

Musí rovněž vyžadovat explicitní potvrzení + důvod.

Backend i frontend musí respektovat:

```text
HALTED
reconciliation_safe
Phase 7 monitoring state
```

Risk account resume nesmí automaticky změnit `SUSPENDED` monitoring na `ACTIVE`.

Monitoring resume zůstává samostatná explicitní operator action.

Pokud backend bezpečnostní podmínku odmítne, UI musí zobrazit 409/error a nesmí lokálně předstírat úspěch.

---

# 24. MONITORING OPERATOR ACTIONS

Na monitoring detailu může Phase 8 zpřístupnit:

```text
PAUSE
RESUME
RETIRE
```

Používej existující `PaperMonitoringService` API semantics.

Každá akce vyžaduje:

- explicitní confirmation,
- non-empty reason,
- server response before optimistic success.

Zakázáno:

- auto resume,
- auto retire,
- auto retune,
- auto experiment,
- auto deployment.

---

# 25. RECONCILIATION ACTION

Risk/operations page může nabídnout explicitní:

```text
RUN RECONCILIATION
```

Musí být jasně označeno jako paper account operation.

Reconciliation nesmí měnit evidence tak, aby maskovala mismatch; používej stávající canonical `ReconciliationService`.

Po dokončení refreshni status z backendu.

---

# 26. DATA HEALTH PAGE

Vytvoř:

```text
/data
```

Musí zobrazit:

```text
provider metadata
calendar identity
latest completed session
instruments
latest ingestions
SUCCEEDED/STARTED/FAILED
latest successful observation/session
snapshot status
coverage
PIT universe status
```

Data freshness nesmí být založena na fixed `24h` TTL.

Používej XNYS session semantics.

Pokud latest completed session nemá validní current observation pro relevantní monitored instrument, dashboard musí ukázat warning/error, ne „healthy“.

---

# 27. AUTOMATION / OPERATIONS PAGE

Vytvoř:

```text
/operations
```

Zobraz:

```text
automation global state
enabled jobs
job type
next run
latest run status
attempts
dead letters
workers
last heartbeat
healthy/stale state
```

Phase 8 nemusí vytvářet generic job editor.

Preferuj read-only operations page.

Pokud implementuješ disable/enable existujícího jobu, musí to být explicitní a typed. `run-now` ekonomického paper cycle není pro Phase 8 povinný a raději jej do UI nepřidávej, pokud pro něj není jasná bezpečnostní potřeba.

---

# 28. AUDIT PAGE

Vytvoř:

```text
/audit
```

Přidej server-side filters minimálně podle dostupných audit fields:

```text
event type
entity type
entity id
correlation id
account/order/cycle identity, pokud existuje
start UTC
end UTC
```

Pagination musí být server-side.

Neparsuj tisíce audit rows jen v browseru.

Audit payload zobraz bezpečně jako formatted JSON; escape HTML, nikdy `dangerouslySetInnerHTML` pro raw payload.

---

# 29. NO FAKE BROKER HEALTH

Paper broker je lokální persistentní adapter, ne remote broker API.

Dashboard nesmí zobrazovat zelené „Broker online“ jen proto, že karta existuje.

Místo toho zobraz například:

```text
Paper broker: persistent/local
Reconciliation: SUCCEEDED / FAILED / missing
```

Remote broker health je budoucí live scope.

---

# 30. ERROR / EMPTY / LOADING STATES

Každá stránka musí korektně zvládnout:

```text
loading
empty database/result
404
409
422
503 readiness failure
network error
stale data
no performance snapshots
INSUFFICIENT_DATA
SUSPENDED
HALTED
```

Zakázáno je nahradit error nulami, prázdnou křivkou nebo „healthy“ badge.

---

# 31. DATA FRESHNESS

Dashboard musí u důležitých provozních údajů ukazovat:

```text
as of
session date
last updated
```

kde je to relevantní.

Nezaměňuj:

```text
browser refresh time
```

za:

```text
financial evidence as_of
```

---

# 32. CACHING

Financial/operator state nesmí být omylem staticky cacheovaný během Next.js build nebo dlouho po změně stavu.

Použij explicitní dynamic/no-store semantics tam, kde je to potřeba.

Pro critical status (HALTED, monitoring state, readiness) můžeš implementovat rozumný krátký polling/manual refresh.

Nevytvářej vysokofrekvenční polling; systém je převážně EOD.

---

# 33. UI/UX

Default language:

```text
Czech
```

Technické termíny mohou zůstat anglicky, pokud jsou přesnější.

Dashboard musí být:

- desktop-first, ale responsive,
- čitelný na notebooku,
- konzistentní,
- bez vizuálního chaosu,
- s jasným rozlišením HEALTHY/WATCH/REVIEW_REQUIRED/SUSPENDED/HALTED,
- s accessible labels,
- s keyboard-friendly controls,
- bez reliance pouze na barvu.

Preferuj profesionální quant/operator vzhled, ne marketing landing page.

---

# 34. NAVIGACE

Minimální navigace:

```text
Přehled
Paper
Strategie
Research
Risk
Data
Operations
Audit
```

Monitoring detail může být v Paper/Strategie.

---

# 35. GRAFY

Minimálně implementuj:

1. Paper equity curve
2. Drawdown
3. Cumulative return / performance
4. Expected-vs-realized comparison, pokud evidence existuje

Grafy musí:

- používat reálná API data,
- správně řadit XNYS sessions,
- mít UTC/session date semantics,
- nezobrazovat `null` first return jako nulu,
- zvládat prázdnou řadu,
- mít tooltip,
- mít period selector,
- nepřepočítávat authoritative drawdown jinou metodikou, pokud backend již persistuje Phase 7 drawdown.

---

# 36. BACKEND READ MODEL PERFORMANCE

Dashboard agregace nesmí vytvořit zjevný N+1 dotaz na každou strategii/run/snapshot.

Použij rozumné SQL query/join/subquery patterns.

Přidej index pouze pokud existující schema reálně nestačí a změnu podlož query patternem.

Pokud není potřeba schema change, **nevytvářej migraci jen proto, že je nová fáze**.

---

# 37. ALEMBIC

Aktuální head je:

```text
20260824_02
```

Phase 8 by měla být primárně read-model + frontend a může skončit bez DB migrace.

Pokud ale implementace skutečně vyžaduje novou persistence strukturu:

- vytvoř forward-only migration po `20260824_02`,
- neměň historické migrace,
- fresh PostgreSQL `upgrade head` musí projít,
- upgrade z předchozího head musí projít.

Neměň immutable Phase 7 evidence schema jen kvůli pohodlí UI.

---

# 38. OPENAPI / FRONTEND CONTRACT

Frontend a backend contract nesmí driftovat bez povšimnutí.

Preferuj jednu z variant:

1. generovat TypeScript types/client z FastAPI OpenAPI,
2. nebo mít explicitní sdílenou contract-generation pipeline.

Pokud generuješ OpenAPI types:

- generovaný soubor může být commitnutý,
- CI musí ověřit, že je aktuální,
- generation musí být deterministic,
- frontend nesmí ručně duplikovat desítky backend response interfaces.

Nepřidávej codegen, pokud je výrazně křehčí než explicitní malý typed client, ale contract drift musí mít test.

---

# 39. FRONTEND TESTS

Použij moderní frontend testing stack vhodný k aktuálním verzím React/Next.

Minimálně testuj:

- formatting/lint,
- TypeScript typecheck,
- component/unit tests,
- production build.

Test cases minimálně:

```text
Overview renders PAPER mode
HALTED is visibly unsafe
SUSPENDED monitoring is visible
INSUFFICIENT_DATA does not show HEALTHY
first daily return null is not rendered as 0%
empty performance series renders truthful empty state
503 readiness renders unavailable state
positions/orders/fills tables render exact API data
provider correction cannot alter historical returned Phase 7 snapshot series
HALT confirmation required
RESUME confirmation required
backend 409 on unsafe resume is shown as failure
monitoring transition requires reason
RETIRED cannot show active/resume state
no live action exists in navigation/actions
```

---

# 40. BACKEND PHASE 8 TESTS

Přidej například:

```text
backend/tests/test_phase8_api.py
backend/tests/test_phase8_operator_read_model.py
```

Pokud read-model používá PostgreSQL-specific query semantics, přidej:

```text
backend/tests/test_phase8_postgres.py
```

Testuj minimálně:

- overview aggregation,
- XNYS session-aware data health,
- latest monitoring/evaluation selection,
- HALTED/reconciliation state,
- no-data/empty state,
- performance period filtering,
- audit filters + pagination,
- stable financial serialization,
- N/A fields when evidence absent,
- no future/current correction rewriting history,
- API validation and 404/422 behavior.

---

# 41. CONTRACT INTEGRATION TEST

Přidej alespoň jeden test, který ověří skutečnou shodu frontend očekávání a backend OpenAPI/JSON contractu.

Mock-only frontend test není dostatečný důkaz contract integrity.

Přijatelný příklad:

```text
FastAPI exports OpenAPI
→ frontend type generation/check
→ representative operator payload validates
```

nebo robustnější ekvivalent.

---

# 42. BROWSER E2E

Preferuj jeden malý, ale skutečný browser E2E smoke test, pokud jej lze stabilně provozovat v CI bez neúměrné infrastruktury.

Scénář například:

```text
start seeded test backend
start Next.js
open /
verify PAPER
open /paper
verify real seeded equity row
open /risk
verify state
```

Pokud browser dependency výrazně destabilizuje CI, nenechávej falešný Playwright skeleton. V takovém případě poskytni silnou component + contract integration coverage a transparentně zdokumentuj, že browser E2E je mimo Phase 8.

Ale `next build` je povinný.

---

# 43. CI

Existující backend CI musí zůstat green:

```text
quality
unit-research
api
integration-postgres
```

Přidej samostatný frontend job, například:

```text
frontend
```

který minimálně provede:

```text
setup supported Node LTS
npm ci
npm run lint
npm run typecheck
npm test -- --run   # nebo ekvivalent
npm run build
```

Pokud framework používá jiný přesný script contract, uprav příkazy, ale musí být ekvivalentní.

Nepoužívej `npm install` bez lockfile v CI.

Backend locked gate zůstává:

```bash
cd backend
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
```

A všechny relevantní backend tests.

---

# 44. ROOT DEMO HTML

Současná inline `HTMLResponse` stránka v `backend/src/quantlab/api.py` s tlačítkem `Spustit MA backtest` není Phase 8 dashboard.

Po vytvoření Next.js frontend ji nenechávej prezentovat jako hlavní produktové UI.

Můžeš:

- backend `/` změnit na minimal API metadata/redirect na docs,
- nebo historický demo endpoint zachovat na explicitním legacy path, pokud má testovací hodnotu.

Neodstraňuj `/api/backtests/demo`, pokud jej existující testy legitimně používají, bez náhrady.

Dokumentuj rozhodnutí.

---

# 45. NO BUSINESS LOGIC IN REACT

Frontend může:

- formátovat,
- filtrovat presentation data,
- převést Decimal string → number pro chart,
- zvolit period,
- ukázat badge.

Frontend nesmí být autoritou pro:

- drawdown,
- risk limit evaluation,
- trading state,
- monitoring verdict,
- reconciliation safety,
- XNYS latest completed session,
- OOS eligibility,
- portfolio valuation,
- corporate action accounting.

Tyto hodnoty musí pocházet z backend authoritative services/evidence.

---

# 46. NO AUTO-TRADING EXPANSION

Phase 8 nesmí měnit ekonomickou pipeline na:

```text
Dashboard → Broker
```

Musí zůstat:

```text
Strategy
→ Target Portfolio
→ RiskEngine
→ ExecutionEngine
→ PersistentPaperBroker
→ Reconciliation
```

Operator actions mohou pouze volat existující safe service boundaries.

---

# 47. SAFETY ACTION ADVERSARIAL TESTS

Ověř minimálně:

1. UI nemá live trading action.
2. HALT bez confirmation neodešle request.
3. RESUME bez confirmation neodešle request.
4. RESUME při reconciliation unsafe vrátí failure a UI zůstane HALTED.
5. Account resume automaticky neresumuje SUSPENDED monitoring.
6. Monitoring resume při posledním SUSPENDED evaluation failne podle Phase 7 semantics.
7. RETIRED monitoring nemá active execution affordance.
8. Error response není přepsán optimistic UI success state.
9. Double-click/retry operátorské akce nevytvoří nový economic execution path.
10. Dashboard nikdy přímo nevolá broker.

---

# 48. DATA ADVERSARIAL TESTS

Ověř minimálně:

1. no performance snapshots,
2. first snapshot only,
3. zero-trade sessions,
4. provider correction after historical snapshot,
5. STARTED latest ingestion,
6. FAILED latest ingestion,
7. missing latest completed session data,
8. weekend,
9. holiday,
10. early close,
11. HALTED account,
12. stale worker,
13. dead-letter job,
14. missing reconciliation,
15. SUSPENDED monitoring,
16. RETIRED monitoring,
17. `INSUFFICIENT_DATA`,
18. WATCH,
19. REVIEW_REQUIRED,
20. SUSPENDED evaluation.

---

# 49. FRONTEND SECURITY BASELINE

I bez full auth/RBAC musí Phase 8 dodržet:

- no secrets in frontend,
- no raw SQL,
- no dynamic eval,
- no arbitrary URL fetch from user input,
- no `dangerouslySetInnerHTML` for audit/DB content,
- same-origin/server-side mutation pattern where practical,
- loopback default host,
- explicit confirmation for safety mutations,
- input length/shape validation for reasons,
- no credentials committed.

Full authentication, RBAC, CSP/security headers hardening, rate limits a secret-management production rollout jsou budoucí Phase 9/10 scope, pokud nejsou nutné k bezpečnému lokálnímu Phase 8.

---

# 50. OBSERVABILITY SCOPE BOUNDARY

Phase 8 smí vytvořit **operator-visible operational health read model**.

Neimplementuj v této fázi celý Prometheus/Grafana/centralized logging stack jen kvůli dashboardu.

Plný observability/security hardening bude samostatný další milestone.

Můžeš využít existující:

```text
/health
/health/ready
operations summary
worker heartbeat
audit
reconciliation
risk events
```

A agregovat je do dashboard-safe read modelu.

---

# 51. DOCUMENTATION

Aktualizuj pravdivě minimálně:

```text
README.md
docs/architecture.md
docs/implementation-plan.md
docs/operations.md
docs/codex/phase8-complete.md
```

Přidej:

```text
docs/dashboard.md
```

Dokumentuj:

- frontend architecture,
- local run commands,
- ports/loopback binding,
- API contract,
- pages,
- data freshness semantics,
- operator safety actions,
- no-live boundary,
- frontend tests,
- troubleshooting.

---

# 52. README QUICKSTART

Po Phase 8 musí být možné z README zjistit přesně, jak spustit:

```text
PostgreSQL
Alembic
backend API
automation worker (volitelné pro dashboard read)
frontend dashboard
```

Přidej přesné commands podle skutečného repository.

Nepiš command, který jsi neověřil alespoň syntakticky/CI.

---

# 53. MAKEFILE / DEV COMMANDS

Pokud to zjednoduší použití, doplň bezpečné targety například:

```text
make api
make dashboard
make frontend-test
```

Default host musí zůstat loopback.

Nevytvářej křehký `make dev` orchestrator, pokud neumí spolehlivě ukončit child processes.

---

# 54. DOCKER / DEPLOYMENT SCOPE

Phase 8 nemusí dokončit production Docker image, Redis ani deploy pipeline.

Současný `docker-compose.yml` může zůstat pouze pro PostgreSQL, pokud dashboard spustíš explicitními lokálními příkazy.

Pokud přidáš frontend/backend containers, udělej to pouze pokud je to čisté, plně otestované a nepřidává to zbytečně velký scope.

Nesmí být důvodem k nedokončení dashboardu.

---

# 55. NO REDIS REQUIREMENT

Master plan historicky zmiňuje Redis, ale aktuální Phase 5 automation používá PostgreSQL leases/fencing a funguje.

Nepřidávej Redis jen proto, aby odpovídal starému původnímu plánu.

Phase 8 na Redis nesmí záviset, pokud neexistuje konkrétní nový technický důvod.

---

# 56. ACCESSIBILITY

Minimálně:

- semantic headings,
- labels pro formuláře,
- keyboard operability,
- focus state,
- textový stav vedle barvy,
- readable contrast,
- table headers,
- confirmation dialog accessible.

Nepotřebuji formální WCAG audit, ale základní accessibility nesmí být ignorována.

---

# 57. PERFORMANCE

Dashboard je single-operator a EOD-oriented.

Neoptimalizuj předčasně, ale:

- nevytvářej N+1 API flood,
- nepřenášej celé audit tables bez pagination,
- nepřenášej všechny historical snapshots, když je zvoleno 1M,
- nepolluj každou sekundu,
- Next build nesmí fetchovat live backend data.

---

# 58. PHASE 8 DEFINITION OF DONE

Phase 8 je `COMPLETE` pouze pokud současně platí:

## Backend/operator API

- explicitní operator read-model service existuje,
- response schemas jsou typed/stable,
- overview/paper/performance/risk/data/operations/audit data jsou dostupná,
- session/data health je XNYS-aware,
- no fake data,
- historical Phase 7 evidence je čtena immutable.

## Frontend

- skutečný `frontend/` Next.js/React/TS/Tailwind app existuje,
- `/` overview funguje,
- paper page funguje,
- monitoring detail funguje,
- strategies/research fungují v read scope,
- risk page funguje,
- data page funguje,
- operations page funguje,
- audit page funguje,
- equity/drawdown charts fungují,
- empty/error/loading states jsou pravdivé,
- Czech default language.

## Safety

- PAPER mode je zřetelný,
- žádná live path,
- no direct broker call,
- HALT/RESUME mají confirmation,
- monitoring actions mají reason/confirmation,
- backend safety rejection se nezakrývá.

## Quality

- backend locked gates PASS,
- existing Phase 3–7 tests PASS,
- new Phase 8 backend tests PASS,
- frontend lint PASS,
- frontend typecheck PASS,
- frontend tests PASS,
- frontend production build PASS,
- contract drift check PASS.

## Documentation

- Phase 7 docs truthful COMPLETE/PASSED,
- Phase 8 authoritative doc exists,
- implementation plan updated,
- dashboard docs exist,
- local quickstart works.

---

# 59. VERDICT RULES

Použij pouze:

```text
COMPLETE
COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING
INCOMPLETE
```

`COMPLETE` smíš vydat jen pokud:

- implementace je celá,
- backend CI gates jsou green,
- frontend CI gates jsou green,
- production `next build` je green,
- relevantní integration/contract tests jsou green.

`COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING` pouze pokud je celý kód/test/CI wiring dokončen, ale Codex sandbox objektivně nedovoluje jeden environment-specific gate. Pokud GitHub CI následně proběhne, finální verdict se řídí GitHub CI.

Funkční mezera = `INCOMPLETE`.

---

# 60. CI FALSE-GREEN AUDIT

Než dokončíš PR, proveď vlastní adversarial audit testů.

U každého významného testu zkontroluj, že název odpovídá skutečné akci.

Příklady false-green, které jsou zakázané:

```text
"provider correction immutable"
→ ale test nic nereingestuje

"HALT blocks"
→ ale test nikdy nevolá relevantní action

"frontend uses API"
→ ale data jsou hardcoded fixture v componentě

"contract test"
→ ale frontend type není odvozen/ověřen proti backendu

"no live path"
→ ale test pouze hledá string "live" v jednom souboru
```

Test musí prokazovat skutečnou invariantní vlastnost.

---

# 61. GIT / PR

Preferuj jeden kompletní PR.

Nezastav se po:

- vytvoření frontend skeletonu,
- vytvoření několika cards,
- vytvoření pouze operator API,
- napsání tests bez spuštění,
- napsání docs bez UI,
- `next build` bez backend integration.

Dokonči celou fázi.

Preferovaný commit message:

```text
Implementuj Phase 8 operator dashboard
```

Preferovaný PR:

```text
Phase 8 — Operator Dashboard and Control Plane
```

---

# 62. REQUIRED FINAL CODEX REPORT

Na konci vrať přesně a konkrétně:

1. Starting main SHA.
2. Final branch/head SHA.
3. Phase 7 documentation truth correction.
4. Phase 8 verdict.
5. Backend operator read-model architecture.
6. Nové/stabilizované API endpoints.
7. Frontend stack + exact pinned versions.
8. Pages implementované ve frontend.
9. Charts a period semantics.
10. Expected-vs-realized visualization semantics.
11. HALT/RESUME/monitoring safety controls.
12. Data health semantics.
13. Automation/worker visibility.
14. Audit filtering/pagination.
15. OpenAPI/frontend contract strategy.
16. Frontend test counts.
17. Backend new test counts.
18. Existing backend regression counts.
19. PostgreSQL test result.
20. Alembic result/head.
21. `npm ci` result.
22. frontend lint result.
23. frontend typecheck result.
24. frontend unit/component result.
25. `next build` result.
26. CI jobs + run ID + conclusions, pokud dostupné.
27. False-green audit findings/fixes.
28. Paper-only/no-live evidence.
29. Remaining gaps/out-of-scope.
30. Changed files summary.
31. Commit/PR URL/number, pokud vytvořeno.
32. `READY FOR PHASE 8 AUDIT GATE` pouze pokud všechny required gates prošly.

---

# 63. TWO FINAL CORRECTNESS QUESTIONS

Před uzavřením si odpověz:

## Q1

Může operátor z nového dashboardu pravdivě a reprodukovatelně vidět research → deployment → paper monitoring → performance → risk/data/automation/audit stav bez fake dat a bez přímého DB přístupu?

Správná odpověď pro COMPLETE:

```text
YES
```

## Q2

Vytvořila Phase 8 jakoukoli novou cestu, která může obejít RiskEngine/Phase 7 monitoring nebo spustit live trading?

Správná odpověď pro COMPLETE:

```text
NO
```

---

# 64. PHASE 8 SCOPE BOUNDARY — CO ZŮSTÁVÁ NA DALŠÍ FÁZE

Nedělej v Phase 8:

- live broker,
- live credentials,
- live trading enablement,
- full auth/RBAC,
- internet-exposed multi-user deployment,
- Prometheus/Grafana full stack,
- centralized logging platform,
- secret manager rollout,
- SAST/dependency policy overhaul,
- cloud/Kubernetes deploy,
- generalized experiment creation platform,
- nové strategy families,
- auto-retune,
- auto-deployment,
- auto-resume.

Po úspěšném Phase 8 audit gate bude logický další milestone zaměřený na **production observability + security hardening + deployability**, nikoli na live trading samotný.

---

# 65. FINÁLNÍ POŽADAVEK

Neoptimalizuj na to, aby PR vypadal hotově.

Optimalizuj na to, aby po merge šlo otevřít skutečný lokální operator dashboard a důvěřovat tomu, co ukazuje.

Dashboard je pouze prezentace a control plane nad authoritative backend evidence.

Nikdy nesmí být novou ekonomickou autoritou.

