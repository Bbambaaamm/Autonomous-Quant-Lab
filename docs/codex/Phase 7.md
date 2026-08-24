Pracuj v repository:

Bbambaaamm/Autonomous-Quant-Lab

TOTO JE CELKOVÁ IMPLEMENTACE NOVÉ PROJEKTOVÉ PHASE 7.

==================================================
PHASE 7
PAPER PERFORMANCE MONITORING,
DRIFT DETECTION & STRATEGY LIFECYCLE
==================================================

Cílem této fáze je vytvořit produkční vrstvu, která umožní:

- dlouhodobě provozovat Phase 6 strategie v paper tradingu;
- přesně měřit skutečný paper výkon;
- zachovat každý den reprodukovatelný performance snapshot;
- porovnávat research/OOS očekávání se skutečným paper výkonem;
- detekovat performance drift;
- detekovat execution/cost/risk/data problémy;
- řídit lifecycle paper deploymentu;
- automaticky failnout/suspendovat nebezpečný runtime;
- nikdy automaticky neměnit strategii nebo její parametry;
- nikdy automaticky nepovýšit systém do live tradingu;
- vytvořit základ, podle kterého bude možné po delším paper provozu objektivně rozhodovat o dalších krocích.

Toto je stále:

PAPER ONLY.

LIVE TRADING JE MIMO SCOPE PHASE 7.

==================================================
0. AUTORITATIVNÍ START
==================================================

Poslední auditovaný main po Phase 6:

b2350038a6d3e9f2d1f303b0f3de9932492813b3

Merge:

PR #26
Phase6: add research→paper audit boundary, validated current-data and paper execution path

Phase 6 Audit Gate:

PASS

Phase 6:

COMPLETE

Aktuálně ověřeno v GitHub CI:

- uv 0.12.3
- locked dependencies
- Ruff
- mypy
- unit/research
- PostgreSQL 17
- fresh Alembic upgrade
- Phase 3–6 PostgreSQL integration
- concurrency
- immutable replay
- PIT universe
- corporate actions
- Phase6ExperimentRunner exactly-once
- OOS isolation
- manifest integrity
- research → paper E2E
- HALTED safety
- paper-only architecture

Pokud aktuální main obsahuje novější commit:

aktuální main je jediný source of truth.

Necheckoutuj ani neobnovuj zavřený PR #27.

==================================================
1. DŮLEŽITÉ — HISTORICKÉ ČÍSLOVÁNÍ PHASE
==================================================

CODEX_MASTER_PROMPT.md historicky obsahuje:

Phase 7 – Portfolio & Risk

TENTO STARÝ MILESTONE JE JIŽ IMPLEMENTOVANÝ.

Aktuální docs/implementation-plan.md potvrzuje Portfolio & Risk jako COMPLETE.

Proto v tomto tasku:

NEIMPLEMENTUJ znovu Portfolio & Risk.

Nová projektová Phase 7 je:

Paper Performance Monitoring,
Drift Detection
&
Strategy Lifecycle.

Aktuální milestone numbering Phase 4–7 má přednost před původním historickým pořadím master promptu.

==================================================
2. PŘED ZAČÁTKEM
==================================================

Spusť:

git status
git branch --show-current
git rev-parse HEAD
git log --oneline -20
git diff --check

Working tree musí být čistý.

Preferovaná branch:

codex/phase7-paper-performance-monitoring

Pokud branch/push/PR není v sandboxu možné:

NEBLOKUJ IMPLEMENTACI.

==================================================
3. PŘEČTI PŘED EDITACÍ
==================================================

Povinně přečti celé:

AGENTS.md
CODEX_MASTER_PROMPT.md
docs/codex/phase5-complete.md
docs/codex/phase6-complete.md
docs/implementation-plan.md

Dále minimálně:

README.md
docs/architecture.md
docs/database.md
docs/market-data.md
docs/strategy-research.md
docs/risk-management.md
docs/paper-trading.md
docs/operations.md
docs/live-trading-safety.md

Source minimálně:

backend/src/quantlab/domain.py
backend/src/quantlab/config.py
backend/src/quantlab/persistence.py
backend/src/quantlab/market_data.py
backend/src/quantlab/market_data_service.py
backend/src/quantlab/multi_asset.py
backend/src/quantlab/phase4.py
backend/src/quantlab/phase6_runtime.py
backend/src/quantlab/automation.py
backend/src/quantlab/api.py
backend/src/quantlab/research.py
backend/src/quantlab/research_engine.py

Tests minimálně:

backend/tests/test_phase4.py
backend/tests/test_phase5.py
backend/tests/test_phase5_postgres.py
backend/tests/test_phase6.py
backend/tests/test_phase6_runtime.py
backend/tests/test_phase6_postgres.py
backend/tests/test_phase6_audit_fixes.py
backend/tests/test_phase6_experiment_audit.py
backend/tests/test_phase6_current_data_postgres.py
backend/tests/test_phase6_e2e_postgres.py
backend/tests/test_paper_only_architecture.py
backend/tests/phase6_audit_helpers.py

CI:

.github/workflows/ci.yml

Migrations:

všechny existující Alembic revisions.

==================================================
4. VYTVOŘ AUTORITATIVNÍ PHASE 7 SPEC
==================================================

Vytvoř:

docs/codex/phase7-complete.md

Tento dokument musí zachytit skutečný scope této Phase 7 a stát se source of truth pro budoucí:

Phase 7 completion
Phase 7 Audit Gate

Musí obsahovat:

- goals
- non-goals
- architecture
- invariants
- persistence
- monitoring lifecycle
- baseline semantics
- performance snapshot semantics
- drift evaluation
- fail-closed behavior
- concurrency
- API
- automation
- paper-only boundary
- tests
- CI
- Definition of Done
- verdict rules

Nevytvářej pouze obecný dokument.

Musí odpovídat skutečné implementaci.

==================================================
5. HLAVNÍ ARCHITEKTURA PHASE 7
==================================================

Authoritative flow po Phase 7 má být:

Phase 6 immutable research
        ↓
COMPLETED / RESEARCH_ONLY
        ↓
explicit eligibility promotion
        ↓
PAPER_CANDIDATE
        ↓
explicit deployment
        ↓
PENDING_REVIEW
        ↓
explicit approval
        ↓
APPROVED
        ↓
explicit Phase 7 monitoring enrollment
        ↓
ACTIVE PAPER MONITORING
        ↓
ValidatedCurrentDataAccessor
        ↓
Phase6PaperExecutionService
        ↓
TradingCycleService
        ↓
ProductionRiskEngine
        ↓
PersistentPaperBroker
        ↓
ReconciliationService
        ↓
Paper Performance Snapshot
        ↓
Expected-vs-Realized Evaluation
        ↓
HEALTHY / WATCH / REVIEW_REQUIRED / SUSPENDED
        ↓
operator review

NIKDY:

evaluation
→ change parameters automatically

NIKDY:

evaluation
→ create experiment automatically

NIKDY:

evaluation
→ promote to live

NIKDY:

Phase7 monitoring
→ broker

==================================================
6. ZÁSADNÍ INVARIANT
==================================================

Phase 7 je OBSERVATION + CONTROL LAYER.

Nesmí vytvořit druhou ekonomickou execution cestu.

Jediný economic path zůstává:

Phase6PaperExecutionService
→ TradingCycleService
→ ProductionRiskEngine
→ PersistentPaperBroker
→ ReconciliationService

Monitoring může:

- číst data;
- vypočítat performance;
- persistovat snapshots;
- persistovat evaluations;
- generovat audit evidence;
- PAUSE/SUSPEND deployment runtime.

Monitoring nesmí:

- vytvořit order;
- vytvořit fill;
- volat broker;
- modifikovat selected parameters;
- měnit research snapshot;
- přepisovat experiment evidence.

==================================================
7. PAPER MONITORING ENROLLMENT
==================================================

Zaveď explicitní enrollment service.

Preferovaný koncept:

PaperMonitoringService.enroll(...)

nebo architektonicky ekvivalentní služba.

Enrollment musí být explicitní operator action.

Povolen pouze pokud:

- deployment existuje;
- deployment.status == APPROVED;
- experiment existuje;
- experiment.status == COMPLETED;
- experiment.decision == PAPER_CANDIDATE;
- snapshot existuje;
- snapshot.status == VALID;
- exact strategy/version existuje;
- parameters deploymentu odpovídají experimentu;
- paper account existuje;
- account currency odpovídá;
- deployment je USD/XNYS/1d;
- Phase 6 lineage je konzistentní;
- account není v nekonzistentním reconciliation stavu.

Enrollment vytvoří persistentní monitoring run.

==================================================
8. PAPER MONITORING RUN
==================================================

Preferovaný model:

PaperMonitoringRunRecord

Minimálně:

monitoring_id
deployment_id
paper_account_id
policy_id
baseline_id
started_at
ended_at
state
state_reason
state_changed_at
created_at

Monitoring state enum:

ACTIVE
PAUSED
SUSPENDED
RETIRED

Semantika:

ACTIVE
→ paper execution povolen, pokud projdou všechny další Phase4/6 gates

PAUSED
→ explicitně pozastaveno operátorem

SUSPENDED
→ automatický nebo explicitní fail-closed safety stop

RETIRED
→ monitoring ukončen; nelze resume

ACTIVE není approval.

APPROVED deployment + ACTIVE monitoring jsou dvě nezávislé podmínky.

==================================================
9. ONE ACTIVE DEPLOYMENT PER PAPER ACCOUNT
==================================================

Pro Phase 7 vyřeš attribution jednoznačně.

Jeden paper account nesmí současně patřit více aktivním monitoring runům.

Pro jeden account může existovat maximálně jeden open:

ACTIVE
PAUSED
SUSPENDED

monitoring run.

RETIRED run účet uvolňuje.

Preferuj PostgreSQL partial unique index nebo jiný robustní DB invariant.

SQLite test adapter může invariant doplnit service validation.

Důvod:

performance jednoho paper accountu musí být jednoznačně připsatelná jednomu deploymentu.

Nepředstírej přesnou attribution při více strategiích na jednom accountu.

Multi-strategy account je future scope.

==================================================
10. MONITORING POLICY
==================================================

Zaveď immutable, versioned monitoring policy.

Preferovaný model:

PaperMonitoringPolicyRecord

Minimálně:

policy_id
name
schema_version
created_at
content_hash
config_json

Policy musí explicitně obsahovat například:

minimum_sessions
bootstrap_samples
bootstrap_block_size
lower_return_percentile
upper_volatility_percentile

watch thresholds

review thresholds

hard suspension thresholds

maximum paper drawdown
maximum cost deviation
maximum turnover deviation
maximum target tracking deviation
maximum risk rejection rate

pokud tyto metriky implementace podporuje.

Nehardcoduj investiční kritéria přímo do business logiky.

Policy je explicitní configuration/evidence.

Validuj bezpečné meze hodnot.

==================================================
11. POLICY IMMUTABILITY
==================================================

Po použití policy v monitoring runu:

policy se nesmí in-place změnit.

Změna policy:

→ nový policy_id/content hash.

Pokud chce operator použít jinou policy:

musí vzniknout nový monitoring epoch/run nebo explicitní policy transition, která zachová historii.

Nikdy nepřepisuj staré evaluations novou policy.

==================================================
12. IMMUTABLE EXPECTATION BASELINE
==================================================

Při enrollment vytvoř immutable:

PaperExpectationBaselineRecord

nebo ekvivalent.

Musí pinovat:

deployment_id
experiment_id
research snapshot_id
strategy identity
strategy name
strategy version
selected parameters
code_sha
cost model
OOS interval
OOS session count
OOS metrics
baseline return/equity evidence
content_hash
created_at

Baseline musí být reprodukovatelný pouze z immutable Phase 6 evidence.

==================================================
13. RECONSTRUCT OOS BASELINE
==================================================

Phase6 ExperimentRecord dnes obsahuje agregované OOS metrics.

Pro robustní Phase7 monitoring potřebujeme také baseline return/equity series.

NESERIALIZUJ falešná data z agregovaných statistik.

Místo toho:

refactoruj Phase6 research execution tak, aby bylo možné přesně replayovat existující OOS experiment ze:

snapshot
strategy/version
selected parameters
experiment config
cost model
seed
code SHA evidence

Preferuj reusable deterministic service/helper například:

Phase6ExperimentReplayService

nebo čistou společnou interní funkci.

NEDUPLIKUJ Phase6 experiment engine.

Phase6ExperimentRunner i Phase7 baseline replay musí používat stejnou authoritative research logic.

==================================================
14. BASELINE REPLAY VALIDATION
==================================================

Při baseline creation:

replay musí reprodukovat persistentní ExperimentRecord minimálně pro:

total_return
annualized_return
volatility
sharpe
max_drawdown
turnover
trade_count
total_costs

v přesné deterministic/tolerované Decimal reprezentaci podle stávající architektury.

Pokud replay neodpovídá persistentní experiment evidence:

FAIL CLOSED.

Monitoring se nesmí enrollnout.

==================================================
15. CORRECTION SAFETY BASELINE
==================================================

Baseline replay používá přesně:

historical immutable research snapshot observations/revisions.

Nikdy:

latest corrected provider history.

Pozdější provider correction nesmí změnit:

baseline_id
baseline content_hash
baseline returns
baseline metrics

Přidej regression.

==================================================
16. PAPER PERFORMANCE SNAPSHOT
==================================================

Implementuj production service například:

PaperPerformanceService.capture(
    monitoring_id,
    as_of
)

Výsledkem je immutable:

PaperPerformanceSnapshotRecord

Snapshot se vztahuje k:

jednomu monitoring runu
jednomu deploymentu
jednomu paper accountu
jedné completed XNYS session.

==================================================
17. PERFORMANCE SNAPSHOT DATA
==================================================

Persistuj minimálně:

snapshot_id
monitoring_id
deployment_id
paper_account_id
session_date
captured_at
as_of
calendar_identity

cash
marked_equity
realized_pnl
unrealized_pnl pokud lze autoritativně spočítat
gross_exposure
net_exposure
position_count

daily_return
cumulative_return
drawdown

cumulative_turnover
cumulative_commissions
cumulative_slippage_cost pokud je autoritativní evidence
order_count
fill_count
rejected_order/risk rejection count

reconciliation status
trading_state

target_vs_actual deviation pokud ji lze přesně rekonstruovat

market observation IDs/revisions/source hashes použité pro valuation

content_hash

Neskladuj pouze obrovský opaque JSON, pokud jsou klíčové metriky potřebné pro queries.

Detail může být JSON, ale hlavní query fields mají být typed columns.

==================================================
18. PERFORMANCE VALUATION
==================================================

Performance snapshot nesmí slepě používat stale:

PaperAccountRecord.equity

pokud není garantováno, že je oceněný k dané session.

Marked equity vypočítej autoritativně:

cash
+
aktuální positions × current validated close

pro latest completed XNYS session.

Použij:

ValidatedCurrentDataAccessor

nebo vhodně rozšířený read-only valuation accessor.

Žádný unsafe forward fill.

==================================================
19. PERFORMANCE DATA CAUSALITY
==================================================

Performance capture k as_of smí použít pouze data:

known_at / observed_at <= as_of

a pouze:

SUCCEEDED ingestion.

Pokud latest expected session data chybí:

FAIL CLOSED.

Pokud latest revision je:

STARTED
FAILED

FAIL CLOSED.

Pokud data nebyla v as_of ještě známá:

FAIL CLOSED.

==================================================
20. SNAPSHOT CALENDAR SEMANTICS
==================================================

Použij skutečný:

XNYSCalendar / exchange-calendars.

Performance session je:

latest_completed_session(as_of)

Testuj:

normal session
before close
after close
weekend
holiday
early close
DST

Performance snapshot před close nesmí omylem obsahovat aktuální nedokončenou session.

==================================================
21. PERFORMANCE SNAPSHOT IDEMPOTENCE
==================================================

Stejný:

monitoring_id
+
session_date

musí být exactly-once logical snapshot.

Opakované capture se stejnou dostupnou evidencí:

→ stejný snapshot.

Dva concurrent workers:

→ jeden persistent snapshot.

Použij DB invariant + transaction lock.

==================================================
22. HISTORICAL PERFORMANCE IMMUTABILITY
==================================================

Jakmile snapshot vznikne:

pozdější correction market dat nesmí přepsat historical performance snapshot ani staré evaluation decision.

Snapshot pinuje observation IDs/revisions/source hashes, které byly použity v době capture.

Pokud později dorazí correction:

může být zaznamenána jako nová data-quality/audit evidence,

ale nesmí rewrite historical monitoring decision.

==================================================
23. PERFORMANCE SERIES
==================================================

Implementuj query/service pro:

deployment/monitoring performance series.

Chronologicky:

session_date
equity
daily return
cumulative return
drawdown
exposure
turnover
costs

Musí být deterministic a ordered.

==================================================
24. FIRST SNAPSHOT SEMANTICS
==================================================

První monitoring snapshot nemá předchozí paper observation.

Nefabrikuj daily return.

Použij explicitně:

daily_return = NULL

nebo jasně definovanou baseline 0 pouze pokud je to zdokumentované.

Preferuj NULL / NOT_EVALUATED.

Cumulative return může být měřena od:

monitoring starting equity.

Starting equity musí být immutable evidence v monitoring runu.

==================================================
25. CASH FLOWS
==================================================

Pokud systém nemá podporu external deposit/withdrawal:

explicitně to definuj.

Pokud by neautorizovaná změna cash narušila performance measurement:

detect/fail closed nebo vytvoř audit anomaly.

Nevydávej cash injection za trading return.

Nepřidávej komplikovaný cash-flow subsystem, pokud není potřebný.

==================================================
26. PAPER CORPORATE ACTION AUDIT
==================================================

Proveď explicitní audit skutečné paper-runtime semantiky corporate actions.

Ověř:

SPLIT
CASH_DIVIDEND
SYMBOL_CHANGE
DELISTING

v kontextu otevřené paper pozice.

Performance monitoring nesmí reportovat falešný:

-50 % return při 2:1 splitu.

Ani nesmí dividendy:

ignorovat
nebo double-count.

Pokud současný Phase4/6 paper ledger tyto události pro skutečné otevřené paper pozice neumí správně aplikovat:

TOTO JE P0 BLOCKER PRO PHASE 7.

Implementuj canonical idempotent:

PaperCorporateActionService

nebo vhodný ekvivalent.

==================================================
27. PAPER CORPORATE ACTION INVARIANTS
==================================================

Pokud musíš PaperCorporateActionService přidat:

SPLIT:
- adjust quantity;
- adjust unit cost/basis;
- cash unchanged;
- no artificial realized P&L;
- exactly once.

CASH_DIVIDEND:
- credit eligible cash exactly once;
- preserve audit evidence;
- no double-count.

SYMBOL_CHANGE:
- canonical instrument_id zůstává authoritative;
- symbol history se nesmí vydávat za nový ekonomický asset.

DELISTING:
- nesmí vytvořit synthetic fake execution;
- pokud není bezpečný executable price/defined policy:
  → fail closed / REVIEW_REQUIRED / SUSPENDED.

Každá action musí mít immutable application record:

action_id
account_id
position/instrument
applied_at
effect
correlation/audit identity

Unique:

account_id + action_id.

==================================================
28. PAPER CORPORATE ACTION CAUSALITY
==================================================

Action nesmí být aplikována před:

known_at.

Současně respektuj:

effective_at.

Žádné future-known corporate actions.

Přidej regression:

action effective T
known T+2

nesmí ovlivnit stav při T+1.

==================================================
29. EXPECTED VS REALIZED
==================================================

Implementuj:

PaperPerformanceEvaluationService

nebo vhodný ekvivalent.

Úkol:

porovnat immutable OOS expectation baseline

vs.

realized paper performance series.

Nejde o garanci profitability.

Je to monitoring divergence.

==================================================
30. HORIZON-AWARE COMPARISON
==================================================

Nesrovnávej naivně:

3 měsíce OOS total return

vs.

5 dní paper total return.

Comparison musí být horizon-aware.

Použij baseline daily return series a paper horizon.

Preferovaný postup:

deterministic block bootstrap baseline OOS returns

na délku aktuální paper historie.

Policy určuje:

bootstrap_samples
block_size
percentiles

Seed musí být deterministic z:

monitoring_id
policy_id
paper horizon
algorithm version.

Stejný input:

stejný output.

==================================================
31. BOOTSTRAP INTERPRETACE
==================================================

Bootstrap není důkaz budoucí profitability.

Používá se pouze jako monitoring heuristic:

"Je současný paper průběh extrémně mimo distribuci, kterou jsme viděli v immutable OOS baseline?"

Dokumentace to musí jasně říct.

Nepoužívej marketingová tvrzení.

==================================================
32. PERFORMANCE COMPARISON METRICS
==================================================

Vyhodnocuj podle dostupné evidence minimálně:

paper cumulative return
baseline horizon return distribution

paper volatility
baseline volatility distribution

paper max drawdown
baseline drawdown distribution

paper turnover
baseline turnover / normalized turnover

paper trading costs
baseline cost assumptions

paper exposure
baseline exposure

risk rejection rate

reconciliation failures

data validation failures

execution target-vs-actual drift pokud je autoritativně měřitelný

fill/slippage quality pokud je autoritativní evidence.

==================================================
33. ŽÁDNÉ FALEŠNÉ METRIKY
==================================================

Pokud některou metric nelze z persistentní evidence spolehlivě spočítat:

neodhaduj ji.

Buď:

- doplň minimální potřebnou immutable evidence do execution lineage;

nebo:

- metric označ NOT_AVAILABLE.

Nikdy nevymýšlej:

slippage
benchmark
tracking error
expected return

z informací, které repository nemá.

==================================================
34. EVALUATION VERDICT
==================================================

Každá evaluation má explicitní verdict:

INSUFFICIENT_DATA
HEALTHY
WATCH
REVIEW_REQUIRED
SUSPENDED

Volitelně může existovat:

PAPER_REVIEW_READY

pokud je jasně definováno, že jde pouze o:

candidate for human review

a NE:

live trading authorization.

==================================================
35. INSUFFICIENT SAMPLE
==================================================

Pokud paper history < policy.minimum_sessions:

INSUFFICIENT_DATA.

Neoznačuj:

HEALTHY
nebo
BAD

na základě několika dnů.

==================================================
36. WATCH
==================================================

WATCH je soft warning.

Například:

- return na slabém percentilu baseline;
- vyšší volatility;
- vyšší turnover;
- vyšší costs;
- zvýšený reject rate.

WATCH nesmí automaticky měnit strategii.

WATCH nemusí zastavit trading.

==================================================
37. REVIEW_REQUIRED
==================================================

Použij pro významnější drift, který ještě není hard safety breach.

Například:

- opakované WATCH;
- paper výsledky hluboko mimo baseline;
- výrazný cost drift;
- target-vs-actual divergence;
- významná operational degradation.

Trading může podle policy zůstat ACTIVE nebo být explicitně paused operátorem.

Definuj jednoznačně.

==================================================
38. AUTOMATIC SUSPENSION
==================================================

Pouze hard fail-closed triggers mohou automaticky:

ACTIVE
→ SUSPENDED

Minimálně audituj:

account HALTED
unsafe reconciliation
hard drawdown breach podle pinned policy
critical data/evidence inconsistency
critical corporate-action inconsistency

Soft underperformance sama o sobě nesmí bez explicitní policy vytvořit náhodný stop/start systém.

==================================================
39. NO AUTO RESUME
==================================================

SUSPENDED:

nikdy automaticky zpět ACTIVE.

Resume je pouze explicitní operator action.

Resume musí znovu zkontrolovat:

deployment APPROVED
account not HALTED
reconciliation safe
latest data valid
monitoring evidence consistent
policy valid

Jinak fail closed.

==================================================
40. OPERATOR PAUSE
==================================================

Implementuj explicit:

pause(monitoring_id, reason)

ACTIVE/WATCH runtime:

→ PAUSED

PAUSED execution nesmí projít.

Resume pouze explicitní.

==================================================
41. RETIRE
==================================================

Explicit:

retire(monitoring_id, reason)

→ RETIRED

RETIRED je terminal.

Nelze resume.

Pokud chce operator znovu provozovat stejný deployment:

musí vzniknout nový monitoring run/epoch podle explicitních pravidel.

==================================================
42. PHASE6 PAPER EXECUTION GATE
==================================================

Po Phase 7 musí Phase6PaperExecutionService před jakoukoli ekonomickou akcí ověřit:

deployment.status == APPROVED

a:

existuje právě jeden open monitoring run

a:

monitoring.state == ACTIVE.

Pokud:

missing monitoring
PAUSED
SUSPENDED
RETIRED

→ žádný TradingCycleService economic run.

FAIL CLOSED.

Toto je záměrné zpřísnění bezpečnostní hranice po Phase 6.

==================================================
43. UPDATE PHASE6 E2E
==================================================

Existující Phase 6 tests, které očekávají:

APPROVED → execution

aktualizuj pro Phase 7 boundary:

APPROVED
→ explicit monitoring enrollment
→ ACTIVE
→ execution.

Neoslabuj původní assertions.

Phase 6 invariants musí zůstat zachované.

==================================================
44. MONITORING → EXECUTION SEPARATION
==================================================

PaperPerformanceService ani EvaluationService nesmí importovat/volat:

PersistentPaperBroker.process
PersistentExecutionEngine.submit
TradingCycleService.run

pro svou monitoring činnost.

Výjimka:

žádná.

Monitoring pouze čte trading evidence.

Execution gate v Phase6PaperExecutionService smí číst monitoring state.

==================================================
45. DEPLOYMENT/CYCLE LINEAGE
==================================================

Performance musí jednoznačně vědět, které cycles patří monitoring runu.

Preferuj robustní persistentní lineage.

Například:

PaperDeploymentCycleRecord

nebo jinou explicitní vazbu.

Minimálně:

monitoring_id
deployment_id
trading_cycle_id
session_date
linked_at

Unique:

trading_cycle_id

a/nebo vhodný composite invariant.

Nevytvářej attribution pouze heuristikou názvu strategie.

==================================================
46. EXISTING ACCOUNT HISTORY
==================================================

Monitoring starts_at je tvrdá hranice.

Historické trades před monitoring start:

nesmí vstupovat do nové performance série jako nové paper výsledky.

Pokud account při enrollment není "clean":

definuj explicitně policy.

Preferovaně:

enrollment vyžaduje:

- zero positions;
- no open orders;
- reconciliation safe;

NEBO explicitně zaznamenej opening account state jako immutable starting state.

Vyber robustnější variantu podle stávající Phase4 architecture a zdokumentuj ji.

Nevynulovávej účet skrytě.

==================================================
47. PERFORMANCE RECONCILIATION
==================================================

Před capture musí být možné ověřit:

cash
positions
orders
fills

jsou reconciliation-safe.

Pokud poslední reconciliation selhala:

snapshot může být uložen pouze jako:

INVALID / UNSAFE

pokud tento model implementuješ,

ale nesmí být použit jako valid performance evidence.

Preferuj:

capture fails closed + audit event.

==================================================
48. AUDIT EVENTS
==================================================

Každá zásadní Phase 7 událost musí vytvořit audit evidence.

Minimálně:

MONITORING_ENROLLED
MONITORING_PAUSED
MONITORING_RESUMED
MONITORING_SUSPENDED
MONITORING_RETIRED
PERFORMANCE_CAPTURED
PERFORMANCE_EVALUATED
PERFORMANCE_WATCH
PERFORMANCE_REVIEW_REQUIRED
PERFORMANCE_HARD_BREACH

Pokud AuditEventType používá enum:

rozšiř ho bezpečně.

Audit musí obsahovat:

monitoring
deployment
experiment
snapshot
account
session
correlation

podle dostupnosti.

==================================================
49. EVALUATION IMMUTABILITY
==================================================

Persistuj:

PaperPerformanceEvaluationRecord.

Minimálně:

evaluation_id
monitoring_id
performance_snapshot_id
policy_id
created_at
paper_session_count
verdict
reasons_json
paper_metrics_json
baseline_comparison_json
algorithm_version
content_hash

Evaluation je immutable.

Stejný:

monitoring
snapshot
policy
algorithm_version

→ stejná logical evaluation.

==================================================
50. CONCURRENCY — ENROLLMENT
==================================================

Přidej PostgreSQL race:

2 workers současně enroll stejný deployment.

Použij:

2 independent sessions
2 connections
threading.Barrier

Výsledek:

jeden monitoring run.

Oba callers:

same logical result
nebo jeden explicitně idempotentní success.

Žádný duplicate.

==================================================
51. CONCURRENCY — PERFORMANCE CAPTURE
==================================================

PostgreSQL race:

2 workers současně capture stejnou:

monitoring_id + session.

Výsledek:

1 performance snapshot.

No duplicate.

No lost update.

==================================================
52. CONCURRENCY — EVALUATION
==================================================

PostgreSQL race:

2 workers současně evaluate stejný:

monitoring + performance snapshot + policy.

Výsledek:

1 evaluation.

State transition nesmí být aplikována dvakrát.

==================================================
53. STATE TRANSITION CONCURRENCY
==================================================

Otestuj minimálně:

evaluation hard breach
vs.
operator pause

a:

operator resume
vs.
concurrent suspension

Použij row lock/versioning/transaction semantics.

Bezpečnější stav musí vyhrát.

Například:

SUSPENDED nesmí být přepsán stale ACTIVE write.

==================================================
54. CRASH/RETRY SAFETY
==================================================

Monitoring jobs musí být retry safe.

Crash:

po snapshot insert
před job finish

nesmí při retry vytvořit duplicate snapshot.

Stejně evaluation.

Phase5 fencing/lease semantics zůstávají authoritative.

==================================================
55. PHASE 5 AUTOMATION
==================================================

Rozšiř automation pouze o NON-ECONOMIC monitoring workflow.

Preferovaný allowlisted JobType:

MONITOR_PAPER_DEPLOYMENT

nebo rozděleně:

CAPTURE_PAPER_PERFORMANCE
EVALUATE_PAPER_DEPLOYMENT

Vyber architektonicky jednodušší variantu.

Monitoring job smí:

capture
evaluate
audit

Nesmí:

run trading cycle
create order
change strategy parameters
create new experiment
promote deployment
enable live mode.

==================================================
56. AUTOMATION CONFIG
==================================================

Job config smí obsahovat pouze allowlisted:

monitoring_id
deployment_id pokud nutné

Ne:

broker
mode
live
python path
class
arbitrary SQL
arbitrary module
arbitrary URL.

Použij existující validate_payload safety.

==================================================
57. MONITORING SCHEDULE
==================================================

Monitoring musí být exchange-calendar aware.

Denní evaluation může být provedena až po:

latest completed XNYS session.

Nespoléhej na:

"každý den v 16:00 UTC"

protože:

DST
early close
holiday
weekend.

Scheduler může ticknout pravidelně, ale service musí rozhodnout, zda existuje nová completed session.

Pokud ne:

NO_ACTION.

==================================================
58. NO DUPLICATE WEEKEND SNAPSHOTS
==================================================

Saturday/Sunday monitoring job nesmí vytvořit:

Saturday
Sunday

performance session.

Latest completed Friday může být již captured:

→ NO_ACTION.

Holiday stejně.

==================================================
59. AUTOMATION DATA DEPENDENCY
==================================================

Pokud latest completed session market data ještě není SUCCEEDED:

monitoring fail closed / retry podle existing semantics.

Nesmí tiše ocenit portfolio starými daty.

==================================================
60. API — READ
==================================================

Přidej minimální operator/read API podle existujícího FastAPI stylu.

Minimálně:

GET /paper/monitoring
GET /paper/monitoring/{monitoring_id}

GET /paper/monitoring/{monitoring_id}/performance
GET /paper/monitoring/{monitoring_id}/evaluations

GET /paper/deployments/{deployment_id}/performance

GET /paper/performance/summary

Response musí být deterministic a stránkovaný, kde je potřeba.

==================================================
61. API — MUTATIONS
==================================================

Minimální explicit operator actions:

POST /paper/monitoring/policies
POST /paper/deployments/{deployment_id}/monitoring/enroll

POST /paper/monitoring/{id}/pause
POST /paper/monitoring/{id}/resume
POST /paper/monitoring/{id}/retire

Pokud explicit manual evaluation/run-now endpoint dává smysl:

POST /paper/monitoring/{id}/evaluate

Mutation musí používat service layer.

Žádný raw ORM business logic v endpointu.

==================================================
62. API FAIL-CLOSED
==================================================

HTTP API nesmí umožnit:

arbitrary state string
arbitrary SQL
arbitrary strategy import
arbitrary broker
LIVE
credentials
code execution

Použij Pydantic request schemas.

Invalid transition:

409 / 422 podle současné API convention.

==================================================
63. PERFORMANCE SUMMARY
==================================================

Summary pro monitoring/deployment má vracet minimálně:

strategy
strategy version
deployment
monitoring state
paper account
monitoring start
paper sessions

starting equity
current equity
cumulative return
max drawdown
volatility pokud dost dat
Sharpe pokud dost dat
turnover
costs
risk rejects

latest evaluation
latest verdict
latest evaluation reasons

baseline OOS key metrics

current-vs-baseline comparison.

==================================================
64. NO OVERFITTING FEEDBACK LOOP
==================================================

CRITICAL.

Phase 7 nesmí vytvořit:

paper underperforms
→ automatically tune parameters
→ continue same experiment

Pokud strategy potřebuje změnu:

musí se vrátit zpět přes:

nový Phase 6 research experiment
→ validation
→ OOS
→ explicit promotion
→ nový deployment
→ nový monitoring run.

Paper results lze použít jako diagnostickou informaci.

Nikdy jako hidden parameter optimizer.

==================================================
65. NO AUTOMATIC STRATEGY REPLACEMENT
==================================================

Pokud deployment selže:

Phase 7 nesmí automaticky najít "lepší" strategy z leaderboardu.

Žádný:

best backtest
→ replace paper deployment.

Operator rozhoduje.

==================================================
66. PAPER REVIEW READY
==================================================

Pokud implementuješ verdict:

PAPER_REVIEW_READY

musí znamenat pouze:

"deployment splnil předem definovaná paper monitoring kritéria a může být předložen člověku k dalšímu review."

Nesmí:

activate live
create live broker
write live credentials
change TRADING_MODE.

==================================================
67. LIVE TRADING ABSOLUTELY OUT OF SCOPE
==================================================

Phase 7 nesmí přidat:

LiveBroker
Alpaca live
IBKR live
Binance live
broker API credentials
live order adapter
live webhook
TRADING_MODE=live path
LIVE_TRADING_ENABLED runtime

Pokud něco podobného najdeš již v repo jako dormant scaffolding:

neměň ho tak, aby bylo executable.

==================================================
68. RISK ENGINE ZŮSTÁVÁ AUTHORITY
==================================================

Performance HEALTHY:

neznamená RiskEngine approval.

Při každém obchodě stále musí:

ProductionRiskEngine

znovu rozhodnout.

Monitoring nesmí cacheovat risk approval mezi cycles.

==================================================
69. HALTED
==================================================

Pokud account HALTED:

Phase6PaperExecutionService stále nevytvoří order/fill.

Phase7 monitoring:

detekuje HALTED
→ SUSPENDED nebo hard-breach evidence podle policy.

Resume monitoring nesmí obejít account HALTED.

==================================================
70. RECONCILIATION
==================================================

Pokud reconciliation je unsafe:

monitoring hard breach.

Žádný performance HEALTHY.

Žádný resume bez safe reconciliation.

==================================================
71. TARGET VS ACTUAL DRIFT
==================================================

Pokud persistence dovoluje přesně rekonstruovat:

desired target
actual resulting position

persistuj target-vs-actual drift.

Použij například:

sum(abs(actual_weight - target_weight))

nebo jasně definovanou metriku.

Pokud authoritative target evidence chybí:

přidej minimální immutable target evidence k deployment-cycle lineage.

Nevymýšlej ji zpětně z fills.

==================================================
72. EXECUTION COST DRIFT
==================================================

Pokud lze autoritativně spočítat:

expected raw reference price
vs.
actual fill price

persistuj:

slippage amount
slippage bps
commission bps
total execution cost bps.

Pokud Phase4 fill neukládá reference:

přidej minimální immutable execution reference při cycle.

Nedělej heuristic inference z budoucích prices.

==================================================
73. ZERO TRADE SESSIONS
==================================================

Session bez obchodu je stále valid performance session.

Performance time series nesmí obsahovat jen dny s fills.

Jinak by Sharpe/volatility/exposure byly zkreslené.

==================================================
74. EXPOSURE
==================================================

Exposure počítej z marked portfolio state na každé performance session.

Ne:

z počtu trades.

To je důležitý dříve auditovaný invariant projektu.

==================================================
75. DRAWDOWN
==================================================

Drawdown:

current equity / running peak - 1.

Running peak pouze z monitoring performance series.

Žádná future peak knowledge.

==================================================
76. RETURN
==================================================

Daily return:

equity_t / equity_t-1 - 1

pokud nejsou external flows.

Cumulative:

equity_t / starting_equity - 1

Podle explicitní definice.

Používej Decimal tam, kde je vhodné.

==================================================
77. VOLATILITY / SHARPE
==================================================

Pokud sample není dostatečný:

NOT_EVALUATED / NULL.

Nedělej numericky zavádějící annualized Sharpe z jednoho nebo dvou sessions.

Minimum lze definovat policy.

==================================================
78. COSTS
==================================================

Rozliš:

commission
slippage
total execution costs

pokud evidence existuje.

Nevydávej spread/slippage assumption z research za realized paper cost.

==================================================
79. RISK REJECTION RATE
==================================================

Definuj přesně denominator.

Například:

rejected intents / evaluated intents

ne:

rejected orders / fills

Pokud data model neumí denominator přesně rekonstruovat:

přidej evidenci nebo metriku nepoužívej.

==================================================
80. DATA QUALITY EVENTS
==================================================

Performance monitoring musí umět rozlišit:

strategy underperformance

vs.

system/data failure.

Evaluation reason codes například:

RETURN_DRIFT
VOLATILITY_DRIFT
DRAWDOWN_BREACH
TURNOVER_DRIFT
COST_DRIFT
TARGET_TRACKING_DRIFT
RISK_REJECTION_DRIFT
DATA_UNAVAILABLE
INGESTION_FAILED
RECONCILIATION_UNSAFE
ACCOUNT_HALTED
CORPORATE_ACTION_ERROR

Persistuj machine-readable reasons.

==================================================
81. MONITORING ALGORITHM VERSION
==================================================

Baseline/evaluation musí obsahovat algorithm version.

Například:

paper-monitoring-v1
baseline-bootstrap-v1

Změna algoritmu nesmí tiše přepsat staré evaluation.

==================================================
82. REPRODUCIBILITY
==================================================

Pro libovolnou evaluation musí být možné dohledat:

deployment
experiment
research snapshot
strategy/version
parameters
code SHA
monitoring policy
baseline hash
paper performance snapshot IDs
market observation IDs/revisions
orders/fills
reconciliation
algorithm version
verdict reasons.

==================================================
83. POSTGRESQL PERSISTENCE
==================================================

Nové persistentní modely musí mít:

FK
unique constraints
indexes
check constraints kde dávají smysl
timezone-aware timestamps
deterministic IDs kde vhodné.

Žádné pouze in-memory Phase 7 registry.

SQLite zůstává test adapter.

PostgreSQL je authoritative production DB.

==================================================
84. ALEMBIC
==================================================

Historické migrations:

NEMĚŇ.

Přidej novou forward migration.

Preferovaný revision po současném head.

Migration musí vytvořit pouze nové Phase7 schema změny.

Fresh:

alembic upgrade head

musí PASS.

Ověř také upgrade:

previous head → Phase7 head.

Pokud downgrade není součástí project convention, nevynucuj.

==================================================
85. INDEXING
==================================================

Přidej indexy minimálně pro časté queries:

monitoring deployment
monitoring account
monitoring state

performance monitoring_id + session_date
performance deployment_id + session_date

evaluation monitoring_id + created_at
evaluation verdict

policy identity

cycle lineage deployment/monitoring/cycle.

==================================================
86. API QUERY EFFICIENCY
==================================================

Performance time-series endpoint nesmí dělat N+1 query per day.

Použij normální SQL queries.

Pagination/range filters například:

start_date
end_date
limit
offset

kde dává smysl.

==================================================
87. UNIT TESTS
==================================================

Přidej minimálně:

backend/tests/test_phase7.py

Scope:

policy validation
policy immutability
baseline replay
baseline hash
enrollment validations
state machine
pause/resume/retire
daily returns
cumulative returns
drawdown
exposure
turnover
costs
bootstrap determinism
evaluation verdicts
insufficient data
hard breach
no auto-resume
no auto-retune
no live path

==================================================
88. CALENDAR REGRESSIONS
==================================================

Phase7 test musí explicitně ověřit performance capture:

normal day before close
normal day after close
weekend
holiday
early close before
early close after

No duplicate weekend capture.

==================================================
89. POSTGRESQL TESTS
==================================================

Přidej například:

backend/tests/test_phase7_postgres.py

Musí běžet pouze pokud:

RUN_POSTGRES_TESTS=1

ale musí být explicitně zahrnut v CI PostgreSQL job.

Testy:

- monitoring enrollment race;
- one-open-monitoring-per-account;
- capture exactly once race;
- evaluation exactly once race;
- state transition concurrency;
- performance persistence;
- baseline immutability;
- snapshot immutability;
- FK/unique/check constraints.

==================================================
90. PHASE7 FULL E2E
==================================================

Přidej:

backend/tests/test_phase7_e2e_postgres.py

Authoritative full flow:

fixture/provider data
→ PersistentMarketDataService
→ PIT universe
→ DatasetSnapshotService
→ Phase6ExperimentRunner
→ explicit promotion
→ PAPER_CANDIDATE
→ DeploymentService.create
→ PENDING_REVIEW
→ approve
→ APPROVED
→ Phase7 monitoring policy
→ monitoring enrollment
→ ACTIVE
→ current data
→ Phase6PaperExecutionService
→ TradingCycleService
→ RiskEngine
→ PaperBroker
→ fills
→ reconciliation
→ next completed sessions
→ performance snapshots
→ performance series
→ evaluation
→ persisted verdict

Assert:

research baseline pinned
paper account pinned
performance correct
no direct broker path
no future data
monitoring immutable.

==================================================
91. MULTI-SESSION E2E
==================================================

Full Phase7 E2E nesmí mít pouze jednu session.

Simuluj dostatek sessions pro:

returns
drawdown
volatility
turnover
comparison

alespoň několik desítek XNYS sessions tam, kde to test runtime dovolí.

Použij menší policy.minimum_sessions v test fixture, pokud chceš držet CI rychlé.

Production default není diktován test fixture.

==================================================
92. HEALTHY SCENARIO
==================================================

E2E:

paper výkon přibližně v baseline očekávání.

Výsledek:

HEALTHY

po minimum sessions.

Monitoring zůstává:

ACTIVE.

==================================================
93. WATCH SCENARIO
==================================================

Vytvoř controlled drift.

Assert:

WATCH

a:

monitoring stále ACTIVE

pokud policy neurčuje hard stop.

==================================================
94. REVIEW_REQUIRED SCENARIO
==================================================

Významný non-hard drift.

Assert:

REVIEW_REQUIRED

bez:

parameter mutation
deployment replacement
live promotion.

==================================================
95. HARD SUSPENSION E2E
==================================================

Vytvoř například hard drawdown nebo reconciliation unsafe.

Assert:

monitoring:

ACTIVE
→ SUSPENDED

A další Phase6PaperExecutionService call:

→ zero orders
→ zero fills.

==================================================
96. RESUME E2E
==================================================

Po SUSPENDED:

pokus resume při stále unsafe:

FAIL.

Po explicitní opravě/safe evidence:

operator resume:

ACTIVE.

Žádný automatic resume.

==================================================
97. HALTED E2E
==================================================

Approved deployment
+
ACTIVE monitoring
+
HALTED account.

Execution:

zero order/fill.

Monitoring evaluation:

hard safety reason.

Resume:

fail.

==================================================
98. CORPORATE ACTION PAPER E2E
==================================================

Pokud audit zjistí nutnost PaperCorporateActionService:

přidej full PostgreSQL E2E:

open position
→ split
→ quantity/basis adjustment
→ no fake P&L
→ performance curve continuous

a:

open position
→ dividend
→ cash credit once
→ performance includes economic dividend
→ retry does not double count.

==================================================
99. PROVIDER CORRECTION REGRESSION
==================================================

Po performance snapshotu T:

provider opraví observation T.

Assert:

historical performance snapshot T unchanged.

Historical evaluation unchanged.

Novější capture/evaluation může zaznamenat correction evidence, ale nesmí rewrite historii.

==================================================
100. BASELINE CORRECTION REGRESSION
==================================================

Po monitoring enrollment:

provider correction změní latest historical market data.

Baseline je stále založená na immutable research snapshotu.

Assert:

baseline hash unchanged
baseline daily returns unchanged
baseline metrics unchanged.

==================================================
101. CRASH RECOVERY E2E
==================================================

Simuluj:

monitoring job vytvoří snapshot

worker ztratí lease před finish.

Retry jiným workerem:

žádný duplicate snapshot.

Použij existující Phase5 fencing semantics.

==================================================
102. AUTOMATION POSTGRESQL E2E
==================================================

Pokud přidáš nový JobType:

Scheduler
→ JobRun
→ Worker
→ monitor capture/evaluate
→ JobRun SUCCEEDED

Assert:

immutable config snapshot
correlation
exactly once
retry safe.

No economic trading action.

==================================================
103. STATIC SAFETY TEST
==================================================

Rozšiř:

test_paper_only_architecture.py

nebo přidej:

test_phase7_architecture.py

Assert:

PaperMonitoringService
PaperPerformanceService
PaperPerformanceEvaluationService
baseline service

nesmí importovat/volat:

PersistentPaperBroker
PersistentExecutionEngine
LiveBroker

Monitoring modules nesmí volat:

TradingCycleService.run

Monitoring automation JobType nesmí provádět trading.

==================================================
104. NO LIVE PATH REGRESSION
==================================================

Repository scan musí stále potvrdit:

no LiveBroker implementation
no live broker credentials
no executable live mode
no Phase7 → live promotion

Health endpoint stále:

paper
live_trading_enabled=false

podle aktuální architecture.

==================================================
105. PERFORMANCE MANIPULATION REGRESSION
==================================================

Přidej test, že evaluation nemůže změnit:

ExperimentRecord.selected_parameters_json
StrategyDeploymentRecord.parameters_json
ExperimentRecord.decision
research snapshot
strategy version.

==================================================
106. NO AUTOMATIC NEW EXPERIMENT
==================================================

Evaluation WATCH/REVIEW/SUSPENDED:

nesmí vytvořit nový ExperimentRecord.

Assert count before/after.

==================================================
107. NO AUTO DEPLOYMENT
==================================================

Evaluation nesmí vytvořit:

StrategyDeploymentRecord.

Assert.

==================================================
108. NO AUTO LIVE
==================================================

Evaluation nemá žádný field/action:

LIVE_APPROVED
LIVE
broker credentials

nebo ekvivalent.

==================================================
109. API TESTS
==================================================

Rozšiř API testy minimálně o:

create policy
enroll
read monitoring
performance query
evaluation query
pause
resume validation
retire
invalid transition
missing deployment
missing monitoring

No arbitrary execution payload.

==================================================
110. CI
==================================================

Aktualizuj:

.github/workflows/ci.yml

Quality stále:

uv --version
uv lock --check
uv sync --locked --all-groups
ruff format --check
ruff check
mypy

Unit/research přidej Phase7 non-PG tests.

API job přidej relevant Phase7 endpoint tests.

PostgreSQL job:

RUN_POSTGRES_TESTS=1

a explicitně:

Phase3
Phase4
Phase5
Phase6
Phase7

tests.

==================================================
111. CI FALSE-GREEN ZÁKAZ
==================================================

Zakázáno:

SQLite jako PG concurrency proof
shared Session mezi workers
sleep-only race
raw inserts místo production service v business E2E
unconditional skip
test file mimo CI
monkeypatch bypass production flow
test bez meaningful assertions

==================================================
112. FULL POSTGRES TEST COMMAND
==================================================

PostgreSQL CI musí zahrnout minimálně:

tests/test_phase3_platform.py
tests/test_phase4.py
tests/test_phase5.py
tests/test_phase5_postgres.py

všechny Phase6 testy

a:

tests/test_phase7.py
tests/test_phase7_postgres.py
tests/test_phase7_e2e_postgres.py

plus případné další nové Phase7 soubory.

==================================================
113. LOCAL LOCKED GATE
==================================================

Pokud environment dovolí:

cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
uv run pytest -q

Report exact:

passed
skipped
failed.

==================================================
114. CODEX CLOUD ENVIRONMENT LIMITATION
==================================================

DŮLEŽITÉ.

Pokud Codex sandbox znovu obsahuje:

uv 0.7.22

místo:

uv 0.12.3

nebo nemá:

PostgreSQL
Docker
origin

NEZASTAVUJ IMPLEMENTACI.

NEVRACEJ TASK INCOMPLETE pouze kvůli environmentu.

Implementuj:

CODE
TESTS
CI
DOCS

celý Phase7 scope.

Potom odděl:

IMPLEMENTATION COMPLETE

od:

VERIFICATION PENDING IN GITHUB CI/CODESPACE.

PostgreSQL tests musí být NAPSANÉ, i pokud je sandbox neumí spustit.

==================================================
115. DEPENDENCIES
==================================================

Nepřidávej novou dependency bez skutečné potřeby.

Bootstrap implementuj pokud možno pomocí:

stdlib
Decimal
existující numpy/pandas

podle architektury.

Pokud přidáš dependency:

uv.lock nesmí být ručně upraven.

Pokud sandbox nemůže resolve lock:

nepřidávej dependency, pokud lze totéž bezpečně implementovat existujícím stackem.

==================================================
116. DETERMINISTIC BOOTSTRAP
==================================================

Pokud implementuješ bootstrap:

žádný global random.

Explicit deterministic seed.

Preferuj:

random.Random(seed)

nebo explicitní numpy Generator.

Block bootstrap musí mít unit tests:

same seed/input → same output.

Different horizon → deterministic corresponding output.

==================================================
117. BOOTSTRAP EDGE CASES
==================================================

Ošetři:

empty baseline
one return
zero volatility
paper horizon > baseline horizon
paper horizon < block size
all zero returns

Bez NaN/inf leakage do DB/API.

==================================================
118. DECIMAL / FLOAT SAFETY
==================================================

Financial ledger calculations:

Decimal.

Statistical calculations mohou použít float tam, kde je to rozumné, ale:

boundary conversion explicitně.

Nesmí vzniknout:

NaN
Infinity

v persisted JSON/API.

==================================================
119. TIMEZONE
==================================================

Všechny DB timestamps:

UTC timezone-aware.

Exchange timezone pouze calendar boundary.

No naive datetime.

Regression test mandatory.

==================================================
120. MIGRATION FRESH DB
==================================================

Na fresh PostgreSQL:

uv run alembic -c ../alembic.ini upgrade head

PASS.

Migration chain musí stále projít:

Phase3
Phase4
Phase5
Phase6
Phase7.

==================================================
121. MIGRATION EXISTING DB
==================================================

Ověř upgrade:

současný Phase6 Alembic head

→ nový Phase7 head.

Bez data loss.

Historical tables:

nemazat.

==================================================
122. DOCUMENTATION
==================================================

Aktualizuj pravdivě:

README.md
docs/architecture.md
docs/database.md
docs/implementation-plan.md
docs/paper-trading.md
docs/strategy-research.md
docs/risk-management.md
docs/operations.md
docs/live-trading-safety.md

Přidej podle potřeby:

docs/performance-monitoring.md

Preferuji nový:

docs/performance-monitoring.md

==================================================
123. PERFORMANCE MONITORING DOC
==================================================

Musí vysvětlit:

baseline OOS
paper performance
why horizon matching matters
bootstrap monitoring
drawdown
cost drift
target drift
evaluation states
minimum sample
pause/suspend/resume
no auto tuning
no live promotion

A explicitně:

good paper performance ≠ guaranteed future profit.

==================================================
124. IMPLEMENTATION PLAN
==================================================

Aktualizuj docs/implementation-plan.md.

Přidej novou projektovou:

Phase 7 — Paper Performance Monitoring, Drift Detection & Strategy Lifecycle

s:

status
scope
acceptance criteria
tests
remaining work.

Nevraceuj starou historickou Phase7 Portfolio & Risk na IN PROGRESS.

Jasně vysvětli numbering transition.

==================================================
125. OBSERVABILITY
==================================================

Phase7 nemusí implementovat kompletní:

Prometheus
Grafana
central log stack.

Ale všechny monitoring outcomes musí být:

persistent
queryable
auditable.

Full observability stack zůstává future phase.

==================================================
126. FRONTEND
==================================================

NEIMPLEMENTUJ Next.js dashboard v této Phase7.

API + persistence + services jsou dostatečné.

Web dashboard přijde později.

==================================================
127. SECURITY
==================================================

Neimplementuj celý RBAC/Auth v této fázi.

Ale nové mutation endpointy musí:

fail closed
validate input
neumožnit arbitrary execution.

Never commit secrets.

==================================================
128. PERFORMANCE RETENTION
==================================================

Performance snapshots a evaluations jsou audit evidence.

Nemaž je automaticky.

No retention cleanup v Phase7.

==================================================
129. PAPER ACCOUNT DELETE
==================================================

FK musí zabránit smazání account/deployment evidence, pokud by tím došlo k rozbití monitoring lineage.

Použij RESTRICT tam, kde je vhodné.

==================================================
130. DEPLOYMENT DELETE
==================================================

Stejně:

monitoring evidence musí zachovat deployment lineage.

Žádný cascade delete důležitého auditu.

==================================================
131. CONTENT HASH
==================================================

Baseline
performance snapshot
evaluation
policy

mají mít canonical deterministic content hash tam, kde dává smysl.

Použij canonical JSON:

sort_keys
stable separators
explicit Decimal serialization.

==================================================
132. IDEMPOTENT IDS
==================================================

Preferuj hash identity pro logical:

baseline
snapshot
evaluation

spíše než random UUID tam, kde exact logical idempotence dává smysl.

Monitoring run může mít explicit ID, ale repeated identical enrollment musí mít definovanou semantics.

==================================================
133. NO HIDDEN MUTABILITY
==================================================

Neměň historical snapshot JSON po insertu.

Neměň old evaluation.

Neměň baseline.

Stateful monitoring run samozřejmě mění current state, ale každý transition musí mít audit trail.

==================================================
134. TRANSITION HISTORY
==================================================

Pokud AuditEvent nestačí pro snadné query:

zaveď:

PaperMonitoringTransitionRecord

nebo ekvivalent.

Preferuj reuse AuditEvent, pokud bezpečně zachová:

from_state
to_state
reason
actor/source
timestamp.

Nevytvářej redundantní tabulku bez potřeby.

==================================================
135. EXPLICIT OPERATOR VS AUTOMATION SOURCE
==================================================

State transitions musí rozlišit:

OPERATOR
AUTOMATION
SAFETY

source.

Hard automatic suspension:

SAFETY.

Manual pause:

OPERATOR.

==================================================
136. PERFORMANCE POLICY CHANGE
==================================================

Policy change nesmí zpětně změnit verdict staré evaluation.

Nová policy:

nová evaluation identity.

Pokud se provede re-evaluation historického snapshotu novou policy:

obě evaluations musí zůstat uložené.

==================================================
137. MONITORING VERSION CHANGE
==================================================

Stejně algorithm_version.

Nová algorithm version:

nová evaluation.

Old evaluation preserved.

==================================================
138. PERFORMANCE READ MODE
==================================================

Latest endpoint musí jasně definovat:

latest by performance session
ne jen created_at.

Corrections/re-evaluation nesmí zamíchat chronologii.

==================================================
139. DEPLOYMENT PARAMETER IMMUTABILITY
==================================================

Po monitoring enrollment:

pokud někdo pokusí změnit deployment parameters/strategy lineage:

execution + monitoring musí fail closed.

Deployment immutable semantics z Phase6 zachovej.

==================================================
140. STRATEGY VERSION IMMUTABILITY
==================================================

Pokud runtime registry strategy version už neodpovídá pinned deployment:

FAIL CLOSED.

Monitoring nesmí přeskočit na latest.

==================================================
141. CODE SHA
==================================================

Baseline musí pinovat experiment code_sha.

Není nutné, aby současný running Git HEAD byl stejný pro všechny read-only monitoring calculations.

Ale pokud business logic replay vyžaduje exact code semantics:

algorithm/replay version musí být explicitně zachycena.

Nefabrikuj code SHA.

==================================================
142. PERFORMANCE AFTER CODE UPDATE
==================================================

Deployment je pinned na strategy version/parameters, ne obecně "latest code".

Pokud změna kódu změní semantics stejné strategy version:

to je registry invariant violation.

Fail closed / audit.

Nevydávej změněnou implementaci za stejnou version.

==================================================
143. REBALANCE FREQUENCY
==================================================

Monitoring nevyhodnocuje session bez rebalance jako "missing trade".

Zero-trade může být správný outcome.

Respect strategy rebalance_frequency.

==================================================
144. PAPER SESSION WITH NO POSITION
==================================================

Cash-only session je valid.

Exposure 0.

Return může být 0.

Není to automaticky system failure.

==================================================
145. MISSING DATA VS CASH
==================================================

Pokud není žádná position a strategy neměla rebalance:

nemusíš vyžadovat price pro neexistující holdings.

Ale pokud target/evaluation potřebuje universe data:

použij odpovídající validated data path.

Nezvyšuj uměle data dependency.

==================================================
146. TARGET ATTRIBUTION
==================================================

Pokud paper strategy vytvořila target 0:

to je validní signal.

Nesmí být zaměněno za missing target.

==================================================
147. PAPER FILL PARTIAL
==================================================

Performance cost/tracking metrics musí respektovat partial fills.

Actual position ≠ desired target může být legitimně kvůli:

cash
risk
partial fill.

Reason evidence má pomoci odlišit:

risk constrained
execution constrained
data error.

==================================================
148. RISK DECISIONS
==================================================

Performance evaluation může číst:

RiskDecisionRecord

ale nesmí jej modifikovat.

Risk decision history immutable.

==================================================
149. PAPER RECONCILIATION FAILURE
==================================================

Pokud Phase4 reconciliation HALTne account:

Phase7 state nesmí následně account resume.

To je jiná authority.

Operator musí řešit Phase4 reconciliation/risk recovery samostatně.

==================================================
150. DEFAULT MONITORING BEHAVIOR
==================================================

Po Phase7 má nový paper deployment typicky projít:

research
→ promotion
→ deployment
→ approval
→ monitoring enrollment
→ active paper trading.

Monitoring policy musí být explicitně pinned.

No silent default policy, pokud by obsahovala ekonomické hard thresholds.

Může existovat default template, ale enrollment musí uložit přesný policy identity.

==================================================
151. DEFAULT POLICY TEMPLATE
==================================================

Pokud vytvoříš default template:

nesmí být prezentována jako univerzálně optimální investment policy.

Jde o operational example/default.

Všechny hodnoty musí být dokumentované.

==================================================
152. PERFORMANCE EXPORT
==================================================

Pokud je snadné, service/API může exportovat JSON/CSV performance series.

Není povinné pro COMPLETE.

Nepřidávej complexity na úkor core scope.

==================================================
153. REPORT ARTIFACT
==================================================

Přidej service:

PaperPerformanceReportService

pouze pokud přirozeně zapadá.

Může vracet structured JSON summary.

PDF/HTML report není Phase7 blocker.

==================================================
154. ADVERSARIAL PASS
==================================================

Po implementaci proveď druhý adversarial audit.

Zkus:

1. enroll RESEARCH_ONLY experiment
2. enroll unapproved deployment
3. two active deployments same account
4. malformed policy
5. mutate policy
6. mutate baseline
7. provider correction after baseline
8. provider correction after performance snapshot
9. future observed_at
10. STARTED ingestion
11. FAILED ingestion
12. weekend capture
13. holiday capture
14. early-close capture
15. duplicate capture
16. concurrent capture
17. concurrent evaluation
18. stale state transition
19. PAUSED execution
20. SUSPENDED execution
21. RETIRED execution
22. resume while HALTED
23. resume while reconciliation unsafe
24. WATCH auto-tunes strategy
25. REVIEW_REQUIRED creates deployment
26. SUSPENDED creates new experiment
27. monitoring calls broker
28. one-session Sharpe
29. zero-vol baseline
30. split creates fake -50%
31. dividend double count
32. late-known corporate action
33. partial fill tracking
34. no-trade session
35. cash-only session
36. runtime strategy version mismatch
37. policy version change rewrites history
38. evaluation version change rewrites history
39. worker retry duplicate
40. crash between snapshot and finish.

Každý nalezený bug:

reproduce
→ regression
→ fix
→ rerun.

==================================================
155. DEFINITION OF DONE — DATA/PERSISTENCE
==================================================

[ ] docs/codex/phase7-complete.md
[ ] Phase7 forward Alembic migration
[ ] immutable monitoring policy
[ ] immutable expectation baseline
[ ] monitoring run
[ ] performance snapshots
[ ] evaluations
[ ] cycle/deployment monitoring lineage
[ ] indexes/FKs/constraints
[ ] one open monitoring deployment per account

==================================================
156. DEFINITION OF DONE — BASELINE
==================================================

[ ] deterministic Phase6 OOS replay
[ ] no duplicated research engine
[ ] baseline daily returns/equity evidence
[ ] baseline aggregate metrics
[ ] baseline lineage
[ ] baseline content hash
[ ] replay matches ExperimentRecord
[ ] provider correction cannot mutate baseline

==================================================
157. DEFINITION OF DONE — PERFORMANCE
==================================================

[ ] calendar-aware performance capture
[ ] validated current data
[ ] marked equity
[ ] daily returns
[ ] cumulative return
[ ] drawdown
[ ] exposure
[ ] turnover
[ ] costs where authoritative
[ ] risk rejection evidence
[ ] reconciliation state
[ ] source observation lineage
[ ] zero-trade sessions supported
[ ] cash-only sessions supported

==================================================
158. DEFINITION OF DONE — EVALUATION
==================================================

[ ] minimum sample
[ ] horizon-aware comparison
[ ] deterministic bootstrap or equivalent robust method
[ ] HEALTHY
[ ] WATCH
[ ] REVIEW_REQUIRED
[ ] SUSPENDED
[ ] machine-readable reasons
[ ] immutable evaluation
[ ] policy pinned
[ ] algorithm version pinned

==================================================
159. DEFINITION OF DONE — LIFECYCLE
==================================================

[ ] explicit enrollment
[ ] ACTIVE
[ ] PAUSED
[ ] SUSPENDED
[ ] RETIRED
[ ] explicit pause
[ ] explicit resume
[ ] no auto-resume
[ ] explicit retire
[ ] execution blocked unless ACTIVE
[ ] HALTED cannot be bypassed
[ ] reconciliation unsafe cannot be bypassed

==================================================
160. DEFINITION OF DONE — SAFETY
==================================================

[ ] monitoring never calls broker
[ ] monitoring never calls economic execution
[ ] no auto retune
[ ] no auto experiment
[ ] no auto deployment
[ ] no live path
[ ] RiskEngine still authoritative
[ ] paper only
[ ] deployment exact strategy/version/params preserved

==================================================
161. DEFINITION OF DONE — CORPORATE ACTIONS
==================================================

[ ] paper corporate-action path audited
[ ] split continuity proven
[ ] dividend handling proven
[ ] no double-count
[ ] late-known action causal
[ ] delisting fail-closed if unsupported

Pokud stávající Phase4/6 runtime již všechny tyto invarianty správně splňuje:

neimplementuj redundantní service.

Dokaž to tests.

==================================================
162. DEFINITION OF DONE — CONCURRENCY
==================================================

[ ] enrollment PostgreSQL race
[ ] performance capture PostgreSQL race
[ ] evaluation PostgreSQL race
[ ] state transition race
[ ] crash/retry idempotence
[ ] independent sessions/connections
[ ] deterministic synchronization

==================================================
163. DEFINITION OF DONE — AUTOMATION
==================================================

[ ] allowlisted non-economic monitoring job
[ ] immutable job config
[ ] exchange-calendar aware
[ ] no duplicate weekend/holiday sessions
[ ] retry safe
[ ] Phase5 lease/fencing preserved
[ ] no broker/trading action from monitoring job

==================================================
164. DEFINITION OF DONE — API
==================================================

[ ] policy API
[ ] enrollment API
[ ] monitoring read
[ ] performance series
[ ] evaluations
[ ] summary
[ ] pause
[ ] resume
[ ] retire
[ ] invalid transition fail closed
[ ] no arbitrary execution

==================================================
165. DEFINITION OF DONE — TESTS/CI
==================================================

[ ] Phase7 unit tests
[ ] Phase7 API tests
[ ] Phase7 PostgreSQL tests
[ ] Phase7 PostgreSQL E2E
[ ] healthy scenario
[ ] watch scenario
[ ] review scenario
[ ] suspension
[ ] resume
[ ] HALTED
[ ] corrections
[ ] paper-only architecture
[ ] corporate actions
[ ] CI wiring
[ ] locked quality gate
[ ] fresh Alembic upgrade

==================================================
166. NO FAKE COMPLETENESS
==================================================

Phase7 není COMPLETE pouze proto, že:

performance table exists

nebo:

API endpoint exists

nebo:

equity chart data exists

nebo:

bootstrap function exists.

COMPLETE vyžaduje celý lifecycle:

research baseline
→ paper monitoring
→ persistent performance
→ expected-vs-realized comparison
→ drift evaluation
→ safe state transitions
→ automation
→ E2E
→ CI.

==================================================
167. VERDICT
==================================================

Použij pouze:

COMPLETE

COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING

INCOMPLETE

COMPLETE pouze pokud:

- všechny functional requirements jsou hotové;
- locked suite PASS;
- PostgreSQL Phase7 integration/concurrency PASS;
- fresh Alembic PASS.

COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING pouze pokud:

- celý implementation + required tests + CI scope je hotový;
- pouze konkrétní sandbox nemůže testy spustit.

INCOMPLETE:

pokud chybí code nebo mandatory test scope.

==================================================
168. AUDIT READINESS
==================================================

Použij:

READY FOR PHASE 7 AUDIT GATE

pouze pokud jsou relevantní gates skutečně zelené.

Pokud sandbox neumí PostgreSQL spustit, ale scope je kompletně implementován:

IMPLEMENTATION COMPLETE
VERIFICATION PENDING IN GITHUB CI/CODESPACE

a:

NOT YET VERIFIED FOR PHASE 7 AUDIT GATE

je přijatelné.

==================================================
169. TASK NESMÍ SKONČIT PŘEDČASNĚ
==================================================

Neukončuj práci po:

- schema
- baseline
- performance service
- evaluation
- API
- automation
- tests
- docs

samostatně.

Dokonči celý Phase7 scope v jednom tasku.

Pokud narazíš na skutečný blocker v existující Phase4/5/6 implementaci, který brání korektní Phase7:

oprav jej v tomto tasku

+
přidej regression.

==================================================
170. NEPŘIDÁVEJ PHASE 8
==================================================

Neimplementuj:

web dashboard
full observability stack
auth/RBAC
live broker
live trading
ML/AI strategies
options
short selling
leverage
FX
crypto
intraday
distributed research cluster

pokud nejsou nezbytné k opravě regresního invariant.

==================================================
171. FINAL GATES
==================================================

Pokud environment dovolí:

cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab

uv run pytest -q

A PostgreSQL:

RUN_POSTGRES_TESTS=1 uv run pytest -q \
  tests/test_phase3_platform.py \
  tests/test_phase4.py \
  tests/test_phase5.py \
  tests/test_phase5_postgres.py \
  tests/test_phase6.py \
  tests/test_phase6_runtime.py \
  tests/test_xnys_calendar.py \
  tests/test_paper_only_architecture.py \
  tests/test_phase6_postgres.py \
  tests/test_phase6_audit_fixes.py \
  tests/test_phase6_experiment_audit.py \
  tests/test_phase6_current_data_postgres.py \
  tests/test_phase6_e2e_postgres.py \
  tests/test_phase7.py \
  tests/test_phase7_postgres.py \
  tests/test_phase7_e2e_postgres.py

Přizpůsob seznam, pokud Phase7 rozdělíš do dalších relevantních test files.

Všechny musí být explicitně v CI.

==================================================
172. ALEMBIC GATE
==================================================

Fresh PostgreSQL:

uv run alembic -c ../alembic.ini upgrade head

PASS.

Reportuj final head.

==================================================
173. TEST COUNTS
==================================================

Ve final reportu uveď přesně:

quality
unit/research
API
PostgreSQL

passed
skipped
failed.

Neříkej pouze:

tests pass.

==================================================
174. FINAL REPORT
==================================================

Na konci vrať:

# Starting state
SHA
branch
working tree

# Phase 7 verdict
COMPLETE / COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING / INCOMPLETE

# Architecture
nový flow

# Persistence
nové tables/models/migration

# Monitoring policy
immutability/versioning

# Baseline
OOS replay
lineage
correction safety

# Paper performance
metrics
valuation
calendar
data causality

# Expected vs realized
bootstrap/comparison
minimum sample

# Lifecycle
ACTIVE
PAUSED
SUSPENDED
RETIRED

# Safety
HALTED
reconciliation
no auto resume
no auto tuning

# Corporate actions
paper behavior

# Automation
new JobType
idempotence
fencing

# Concurrency
enrollment
capture
evaluation
transitions

# API
endpoints

# E2E
research → paper → multi-session monitoring → evaluation

# Paper only
confirm no live path

# Tests
exact results

# PostgreSQL
exact results

# Alembic
head
fresh upgrade result

# CI
jobs/test files

# Adversarial findings
bugs found and fixed

# Documentation
files updated

# Remaining gaps
NONE
or exact blockers

# Phase 7 Audit readiness
READY FOR PHASE 7 AUDIT GATE
or exact status

# Git
starting SHA
ending SHA
branch
commits
changed files
PR

==================================================
175. GIT
==================================================

Preferovaná branch:

codex/phase7-paper-performance-monitoring

Preferovaný commit:

Implementuj Phase 7 paper performance monitoring

Preferovaný PR title:

Phase 7 — Paper Performance Monitoring and Strategy Lifecycle

Pokud je změn mnoho:

můžeš vytvořit více logických commitů.

Nevytvářej více neúplných completion PRs.

Cílem je jeden kompletní Phase7 PR připravený pro CI a audit.

==================================================
176. DŮLEŽITÁ POSLEDNÍ KONTROLA
==================================================

Před dokončením polož explicitně otázku nad kódem:

"Dokáže po této Phase 7 systém dlouhodobě provozovat schválenou strategii v paper tradingu a objektivně, reprodukovatelně a bez look-ahead porovnávat její skutečný paper výkon s immutable OOS očekáváním?"

Pokud ne:

task není hotový.

Druhá otázka:

"Může Phase 7 sama změnit strategii, obejít RiskEngine nebo spustit live trading?"

Správná odpověď musí být:

NE.

==================================================
177. START
==================================================

Začni nyní.

Nevracej pouze návrh architektury.

Nevracej pouze TODO list.

Prozkoumej aktuální main.

Vytvoř docs/codex/phase7-complete.md.

Implementuj celý Phase7 scope.

Napiš všechny unit/PostgreSQL/E2E tests i v případě, že je konkrétní Codex sandbox nemůže spustit.

Oprav všechny Phase7 blockers, které během implementace odhalíš.

Cíl po dokončení:

Phase 7 implementation complete
→ PR
→ GitHub CI
→ Phase 7 Audit Gate

A TEPRVE PO PASS:

READY FOR PHASE 8.Pracuj v repository:



Bbambaaamm/Autonomous-Quant-Lab



TOTO JE CELKOVÁ IMPLEMENTACE NOVÉ PROJEKTOVÉ PHASE 7.



==================================================

PHASE 7

PAPER PERFORMANCE MONITORING,

DRIFT DETECTION & STRATEGY LIFECYCLE

==================================================



Cílem této fáze je vytvořit produkční vrstvu, která umožní:



- dlouhodobě provozovat Phase 6 strategie v paper tradingu;

- přesně měřit skutečný paper výkon;

- zachovat každý den reprodukovatelný performance snapshot;

- porovnávat research/OOS očekávání se skutečným paper výkonem;

- detekovat performance drift;

- detekovat execution/cost/risk/data problémy;

- řídit lifecycle paper deploymentu;

- automaticky failnout/suspendovat nebezpečný runtime;

- nikdy automaticky neměnit strategii nebo její parametry;

- nikdy automaticky nepovýšit systém do live tradingu;

- vytvořit základ, podle kterého bude možné po delším paper provozu objektivně rozhodovat o dalších krocích.



Toto je stále:



PAPER ONLY.



LIVE TRADING JE MIMO SCOPE PHASE 7.



==================================================

0. AUTORITATIVNÍ START

==================================================



Poslední auditovaný main po Phase 6:



b2350038a6d3e9f2d1f303b0f3de9932492813b3



Merge:



PR #26

Phase6: add research→paper audit boundary, validated current-data and paper execution path



Phase 6 Audit Gate:



PASS



Phase 6:



COMPLETE



Aktuálně ověřeno v GitHub CI:



- uv 0.12.3

- locked dependencies

- Ruff

- mypy

- unit/research

- PostgreSQL 17

- fresh Alembic upgrade

- Phase 3–6 PostgreSQL integration

- concurrency

- immutable replay

- PIT universe

- corporate actions

- Phase6ExperimentRunner exactly-once

- OOS isolation

- manifest integrity

- research → paper E2E

- HALTED safety

- paper-only architecture



Pokud aktuální main obsahuje novější commit:



aktuální main je jediný source of truth.



Necheckoutuj ani neobnovuj zavřený PR #27.



==================================================

1. DŮLEŽITÉ — HISTORICKÉ ČÍSLOVÁNÍ PHASE

==================================================



CODEX_MASTER_PROMPT.md historicky obsahuje:



Phase 7 – Portfolio & Risk



TENTO STARÝ MILESTONE JE JIŽ IMPLEMENTOVANÝ.



Aktuální docs/implementation-plan.md potvrzuje Portfolio & Risk jako COMPLETE.



Proto v tomto tasku:



NEIMPLEMENTUJ znovu Portfolio & Risk.



Nová projektová Phase 7 je:



Paper Performance Monitoring,

Drift Detection

&

Strategy Lifecycle.



Aktuální milestone numbering Phase 4–7 má přednost před původním historickým pořadím master promptu.



==================================================

2. PŘED ZAČÁTKEM

==================================================



Spusť:



git status

git branch --show-current

git rev-parse HEAD

git log --oneline -20

git diff --check



Working tree musí být čistý.



Preferovaná branch:



codex/phase7-paper-performance-monitoring



Pokud branch/push/PR není v sandboxu možné:



NEBLOKUJ IMPLEMENTACI.



==================================================

3. PŘEČTI PŘED EDITACÍ

==================================================



Povinně přečti celé:



AGENTS.md

CODEX_MASTER_PROMPT.md

docs/codex/phase5-complete.md

docs/codex/phase6-complete.md

docs/implementation-plan.md



Dále minimálně:



README.md

docs/architecture.md

docs/database.md

docs/market-data.md

docs/strategy-research.md

docs/risk-management.md

docs/paper-trading.md

docs/operations.md

docs/live-trading-safety.md



Source minimálně:



backend/src/quantlab/domain.py

backend/src/quantlab/config.py

backend/src/quantlab/persistence.py

backend/src/quantlab/market_data.py

backend/src/quantlab/market_data_service.py

backend/src/quantlab/multi_asset.py

backend/src/quantlab/phase4.py

backend/src/quantlab/phase6_runtime.py

backend/src/quantlab/automation.py

backend/src/quantlab/api.py

backend/src/quantlab/research.py

backend/src/quantlab/research_engine.py



Tests minimálně:



backend/tests/test_phase4.py

backend/tests/test_phase5.py

backend/tests/test_phase5_postgres.py

backend/tests/test_phase6.py

backend/tests/test_phase6_runtime.py

backend/tests/test_phase6_postgres.py

backend/tests/test_phase6_audit_fixes.py

backend/tests/test_phase6_experiment_audit.py

backend/tests/test_phase6_current_data_postgres.py

backend/tests/test_phase6_e2e_postgres.py

backend/tests/test_paper_only_architecture.py

backend/tests/phase6_audit_helpers.py



CI:



.github/workflows/ci.yml



Migrations:



všechny existující Alembic revisions.



==================================================

4. VYTVOŘ AUTORITATIVNÍ PHASE 7 SPEC

==================================================



Vytvoř:



docs/codex/phase7-complete.md



Tento dokument musí zachytit skutečný scope této Phase 7 a stát se source of truth pro budoucí:



Phase 7 completion

Phase 7 Audit Gate



Musí obsahovat:



- goals

- non-goals

- architecture

- invariants

- persistence

- monitoring lifecycle

- baseline semantics

- performance snapshot semantics

- drift evaluation

- fail-closed behavior

- concurrency

- API

- automation

- paper-only boundary

- tests

- CI

- Definition of Done

- verdict rules



Nevytvářej pouze obecný dokument.



Musí odpovídat skutečné implementaci.



==================================================

5. HLAVNÍ ARCHITEKTURA PHASE 7

==================================================



Authoritative flow po Phase 7 má být:



Phase 6 immutable research

        ↓

COMPLETED / RESEARCH_ONLY

        ↓

explicit eligibility promotion

        ↓

PAPER_CANDIDATE

        ↓

explicit deployment

        ↓

PENDING_REVIEW

        ↓

explicit approval

        ↓

APPROVED

        ↓

explicit Phase 7 monitoring enrollment

        ↓

ACTIVE PAPER MONITORING

        ↓

ValidatedCurrentDataAccessor

        ↓

Phase6PaperExecutionService

        ↓

TradingCycleService

        ↓

ProductionRiskEngine

        ↓

PersistentPaperBroker

        ↓

ReconciliationService

        ↓

Paper Performance Snapshot

        ↓

Expected-vs-Realized Evaluation

        ↓

HEALTHY / WATCH / REVIEW_REQUIRED / SUSPENDED

        ↓

operator review



NIKDY:



evaluation

→ change parameters automatically



NIKDY:



evaluation

→ create experiment automatically



NIKDY:



evaluation

→ promote to live



NIKDY:



Phase7 monitoring

→ broker



==================================================

6. ZÁSADNÍ INVARIANT

==================================================



Phase 7 je OBSERVATION + CONTROL LAYER.



Nesmí vytvořit druhou ekonomickou execution cestu.



Jediný economic path zůstává:



Phase6PaperExecutionService

→ TradingCycleService

→ ProductionRiskEngine

→ PersistentPaperBroker

→ ReconciliationService



Monitoring může:



- číst data;

- vypočítat performance;

- persistovat snapshots;

- persistovat evaluations;

- generovat audit evidence;

- PAUSE/SUSPEND deployment runtime.



Monitoring nesmí:



- vytvořit order;

- vytvořit fill;

- volat broker;

- modifikovat selected parameters;

- měnit research snapshot;

- přepisovat experiment evidence.



==================================================

7. PAPER MONITORING ENROLLMENT

==================================================



Zaveď explicitní enrollment service.



Preferovaný koncept:



PaperMonitoringService.enroll(...)



nebo architektonicky ekvivalentní služba.



Enrollment musí být explicitní operator action.



Povolen pouze pokud:



- deployment existuje;

- deployment.status == APPROVED;

- experiment existuje;

- experiment.status == COMPLETED;

- experiment.decision == PAPER_CANDIDATE;

- snapshot existuje;

- snapshot.status == VALID;

- exact strategy/version existuje;

- parameters deploymentu odpovídají experimentu;

- paper account existuje;

- account currency odpovídá;

- deployment je USD/XNYS/1d;

- Phase 6 lineage je konzistentní;

- account není v nekonzistentním reconciliation stavu.



Enrollment vytvoří persistentní monitoring run.



==================================================

8. PAPER MONITORING RUN

==================================================



Preferovaný model:



PaperMonitoringRunRecord



Minimálně:



monitoring_id

deployment_id

paper_account_id

policy_id

baseline_id

started_at

ended_at

state

state_reason

state_changed_at

created_at



Monitoring state enum:



ACTIVE

PAUSED

SUSPENDED

RETIRED



Semantika:



ACTIVE

→ paper execution povolen, pokud projdou všechny další Phase4/6 gates



PAUSED

→ explicitně pozastaveno operátorem



SUSPENDED

→ automatický nebo explicitní fail-closed safety stop



RETIRED

→ monitoring ukončen; nelze resume



ACTIVE není approval.



APPROVED deployment + ACTIVE monitoring jsou dvě nezávislé podmínky.



==================================================

9. ONE ACTIVE DEPLOYMENT PER PAPER ACCOUNT

==================================================



Pro Phase 7 vyřeš attribution jednoznačně.



Jeden paper account nesmí současně patřit více aktivním monitoring runům.



Pro jeden account může existovat maximálně jeden open:



ACTIVE

PAUSED

SUSPENDED



monitoring run.



RETIRED run účet uvolňuje.



Preferuj PostgreSQL partial unique index nebo jiný robustní DB invariant.



SQLite test adapter může invariant doplnit service validation.



Důvod:



performance jednoho paper accountu musí být jednoznačně připsatelná jednomu deploymentu.



Nepředstírej přesnou attribution při více strategiích na jednom accountu.



Multi-strategy account je future scope.



==================================================

10. MONITORING POLICY

==================================================



Zaveď immutable, versioned monitoring policy.



Preferovaný model:



PaperMonitoringPolicyRecord



Minimálně:



policy_id

name

schema_version

created_at

content_hash

config_json



Policy musí explicitně obsahovat například:



minimum_sessions

bootstrap_samples

bootstrap_block_size

lower_return_percentile

upper_volatility_percentile



watch thresholds



review thresholds



hard suspension thresholds



maximum paper drawdown

maximum cost deviation

maximum turnover deviation

maximum target tracking deviation

maximum risk rejection rate



pokud tyto metriky implementace podporuje.



Nehardcoduj investiční kritéria přímo do business logiky.



Policy je explicitní configuration/evidence.



Validuj bezpečné meze hodnot.



==================================================

11. POLICY IMMUTABILITY

==================================================



Po použití policy v monitoring runu:



policy se nesmí in-place změnit.



Změna policy:



→ nový policy_id/content hash.



Pokud chce operator použít jinou policy:



musí vzniknout nový monitoring epoch/run nebo explicitní policy transition, která zachová historii.



Nikdy nepřepisuj staré evaluations novou policy.



==================================================

12. IMMUTABLE EXPECTATION BASELINE

==================================================



Při enrollment vytvoř immutable:



PaperExpectationBaselineRecord



nebo ekvivalent.



Musí pinovat:



deployment_id

experiment_id

research snapshot_id

strategy identity

strategy name

strategy version

selected parameters

code_sha

cost model

OOS interval

OOS session count

OOS metrics

baseline return/equity evidence

content_hash

created_at



Baseline musí být reprodukovatelný pouze z immutable Phase 6 evidence.



==================================================

13. RECONSTRUCT OOS BASELINE

==================================================



Phase6 ExperimentRecord dnes obsahuje agregované OOS metrics.



Pro robustní Phase7 monitoring potřebujeme také baseline return/equity series.



NESERIALIZUJ falešná data z agregovaných statistik.



Místo toho:



refactoruj Phase6 research execution tak, aby bylo možné přesně replayovat existující OOS experiment ze:



snapshot

strategy/version

selected parameters

experiment config

cost model

seed

code SHA evidence



Preferuj reusable deterministic service/helper například:



Phase6ExperimentReplayService



nebo čistou společnou interní funkci.



NEDUPLIKUJ Phase6 experiment engine.



Phase6ExperimentRunner i Phase7 baseline replay musí používat stejnou authoritative research logic.



==================================================

14. BASELINE REPLAY VALIDATION

==================================================



Při baseline creation:



replay musí reprodukovat persistentní ExperimentRecord minimálně pro:



total_return

annualized_return

volatility

sharpe

max_drawdown

turnover

trade_count

total_costs



v přesné deterministic/tolerované Decimal reprezentaci podle stávající architektury.



Pokud replay neodpovídá persistentní experiment evidence:



FAIL CLOSED.



Monitoring se nesmí enrollnout.



==================================================

15. CORRECTION SAFETY BASELINE

==================================================



Baseline replay používá přesně:



historical immutable research snapshot observations/revisions.



Nikdy:



latest corrected provider history.



Pozdější provider correction nesmí změnit:



baseline_id

baseline content_hash

baseline returns

baseline metrics



Přidej regression.



==================================================

16. PAPER PERFORMANCE SNAPSHOT

==================================================



Implementuj production service například:



PaperPerformanceService.capture(

    monitoring_id,

    as_of

)



Výsledkem je immutable:



PaperPerformanceSnapshotRecord



Snapshot se vztahuje k:



jednomu monitoring runu

jednomu deploymentu

jednomu paper accountu

jedné completed XNYS session.



==================================================

17. PERFORMANCE SNAPSHOT DATA

==================================================



Persistuj minimálně:



snapshot_id

monitoring_id

deployment_id

paper_account_id

session_date

captured_at

as_of

calendar_identity



cash

marked_equity

realized_pnl

unrealized_pnl pokud lze autoritativně spočítat

gross_exposure

net_exposure

position_count



daily_return

cumulative_return

drawdown



cumulative_turnover

cumulative_commissions

cumulative_slippage_cost pokud je autoritativní evidence

order_count

fill_count

rejected_order/risk rejection count



reconciliation status

trading_state



target_vs_actual deviation pokud ji lze přesně rekonstruovat



market observation IDs/revisions/source hashes použité pro valuation



content_hash



Neskladuj pouze obrovský opaque JSON, pokud jsou klíčové metriky potřebné pro queries.



Detail může být JSON, ale hlavní query fields mají být typed columns.



==================================================

18. PERFORMANCE VALUATION

==================================================



Performance snapshot nesmí slepě používat stale:



PaperAccountRecord.equity



pokud není garantováno, že je oceněný k dané session.



Marked equity vypočítej autoritativně:



cash

+

aktuální positions × current validated close



pro latest completed XNYS session.



Použij:



ValidatedCurrentDataAccessor



nebo vhodně rozšířený read-only valuation accessor.



Žádný unsafe forward fill.



==================================================

19. PERFORMANCE DATA CAUSALITY

==================================================



Performance capture k as_of smí použít pouze data:



known_at / observed_at <= as_of



a pouze:



SUCCEEDED ingestion.



Pokud latest expected session data chybí:



FAIL CLOSED.



Pokud latest revision je:



STARTED

FAILED



FAIL CLOSED.



Pokud data nebyla v as_of ještě známá:



FAIL CLOSED.



==================================================

20. SNAPSHOT CALENDAR SEMANTICS

==================================================



Použij skutečný:



XNYSCalendar / exchange-calendars.



Performance session je:



latest_completed_session(as_of)



Testuj:



normal session

before close

after close

weekend

holiday

early close

DST



Performance snapshot před close nesmí omylem obsahovat aktuální nedokončenou session.



==================================================

21. PERFORMANCE SNAPSHOT IDEMPOTENCE

==================================================



Stejný:



monitoring_id

+

session_date



musí být exactly-once logical snapshot.



Opakované capture se stejnou dostupnou evidencí:



→ stejný snapshot.



Dva concurrent workers:



→ jeden persistent snapshot.



Použij DB invariant + transaction lock.



==================================================

22. HISTORICAL PERFORMANCE IMMUTABILITY

==================================================



Jakmile snapshot vznikne:



pozdější correction market dat nesmí přepsat historical performance snapshot ani staré evaluation decision.



Snapshot pinuje observation IDs/revisions/source hashes, které byly použity v době capture.



Pokud později dorazí correction:



může být zaznamenána jako nová data-quality/audit evidence,



ale nesmí rewrite historical monitoring decision.



==================================================

23. PERFORMANCE SERIES

==================================================



Implementuj query/service pro:



deployment/monitoring performance series.



Chronologicky:



session_date

equity

daily return

cumulative return

drawdown

exposure

turnover

costs



Musí být deterministic a ordered.



==================================================

24. FIRST SNAPSHOT SEMANTICS

==================================================



První monitoring snapshot nemá předchozí paper observation.



Nefabrikuj daily return.



Použij explicitně:



daily_return = NULL



nebo jasně definovanou baseline 0 pouze pokud je to zdokumentované.



Preferuj NULL / NOT_EVALUATED.



Cumulative return může být měřena od:



monitoring starting equity.



Starting equity musí být immutable evidence v monitoring runu.



==================================================

25. CASH FLOWS

==================================================



Pokud systém nemá podporu external deposit/withdrawal:



explicitně to definuj.



Pokud by neautorizovaná změna cash narušila performance measurement:



detect/fail closed nebo vytvoř audit anomaly.



Nevydávej cash injection za trading return.



Nepřidávej komplikovaný cash-flow subsystem, pokud není potřebný.



==================================================

26. PAPER CORPORATE ACTION AUDIT

==================================================



Proveď explicitní audit skutečné paper-runtime semantiky corporate actions.



Ověř:



SPLIT

CASH_DIVIDEND

SYMBOL_CHANGE

DELISTING



v kontextu otevřené paper pozice.



Performance monitoring nesmí reportovat falešný:



-50 % return při 2:1 splitu.



Ani nesmí dividendy:



ignorovat

nebo double-count.



Pokud současný Phase4/6 paper ledger tyto události pro skutečné otevřené paper pozice neumí správně aplikovat:



TOTO JE P0 BLOCKER PRO PHASE 7.



Implementuj canonical idempotent:



PaperCorporateActionService



nebo vhodný ekvivalent.



==================================================

27. PAPER CORPORATE ACTION INVARIANTS

==================================================



Pokud musíš PaperCorporateActionService přidat:



SPLIT:

- adjust quantity;

- adjust unit cost/basis;

- cash unchanged;

- no artificial realized P&L;

- exactly once.



CASH_DIVIDEND:

- credit eligible cash exactly once;

- preserve audit evidence;

- no double-count.



SYMBOL_CHANGE:

- canonical instrument_id zůstává authoritative;

- symbol history se nesmí vydávat za nový ekonomický asset.



DELISTING:

- nesmí vytvořit synthetic fake execution;

- pokud není bezpečný executable price/defined policy:

  → fail closed / REVIEW_REQUIRED / SUSPENDED.



Každá action musí mít immutable application record:



action_id

account_id

position/instrument

applied_at

effect

correlation/audit identity



Unique:



account_id + action_id.



==================================================

28. PAPER CORPORATE ACTION CAUSALITY

==================================================



Action nesmí být aplikována před:



known_at.



Současně respektuj:



effective_at.



Žádné future-known corporate actions.



Přidej regression:



action effective T

known T+2



nesmí ovlivnit stav při T+1.



==================================================

29. EXPECTED VS REALIZED

==================================================



Implementuj:



PaperPerformanceEvaluationService



nebo vhodný ekvivalent.



Úkol:



porovnat immutable OOS expectation baseline



vs.



realized paper performance series.



Nejde o garanci profitability.



Je to monitoring divergence.



==================================================

30. HORIZON-AWARE COMPARISON

==================================================



Nesrovnávej naivně:



3 měsíce OOS total return



vs.



5 dní paper total return.



Comparison musí být horizon-aware.



Použij baseline daily return series a paper horizon.



Preferovaný postup:



deterministic block bootstrap baseline OOS returns



na délku aktuální paper historie.



Policy určuje:



bootstrap_samples

block_size

percentiles



Seed musí být deterministic z:



monitoring_id

policy_id

paper horizon

algorithm version.



Stejný input:



stejný output.



==================================================

31. BOOTSTRAP INTERPRETACE

==================================================



Bootstrap není důkaz budoucí profitability.



Používá se pouze jako monitoring heuristic:



"Je současný paper průběh extrémně mimo distribuci, kterou jsme viděli v immutable OOS baseline?"



Dokumentace to musí jasně říct.



Nepoužívej marketingová tvrzení.



==================================================

32. PERFORMANCE COMPARISON METRICS

==================================================



Vyhodnocuj podle dostupné evidence minimálně:



paper cumulative return

baseline horizon return distribution



paper volatility

baseline volatility distribution



paper max drawdown

baseline drawdown distribution



paper turnover

baseline turnover / normalized turnover



paper trading costs

baseline cost assumptions



paper exposure

baseline exposure



risk rejection rate



reconciliation failures



data validation failures



execution target-vs-actual drift pokud je autoritativně měřitelný



fill/slippage quality pokud je autoritativní evidence.



==================================================

33. ŽÁDNÉ FALEŠNÉ METRIKY

==================================================



Pokud některou metric nelze z persistentní evidence spolehlivě spočítat:



neodhaduj ji.



Buď:



- doplň minimální potřebnou immutable evidence do execution lineage;



nebo:



- metric označ NOT_AVAILABLE.



Nikdy nevymýšlej:



slippage

benchmark

tracking error

expected return



z informací, které repository nemá.



==================================================

34. EVALUATION VERDICT

==================================================



Každá evaluation má explicitní verdict:



INSUFFICIENT_DATA

HEALTHY

WATCH

REVIEW_REQUIRED

SUSPENDED



Volitelně může existovat:



PAPER_REVIEW_READY



pokud je jasně definováno, že jde pouze o:



candidate for human review



a NE:



live trading authorization.



==================================================

35. INSUFFICIENT SAMPLE

==================================================



Pokud paper history < policy.minimum_sessions:



INSUFFICIENT_DATA.



Neoznačuj:



HEALTHY

nebo

BAD



na základě několika dnů.



==================================================

36. WATCH

==================================================



WATCH je soft warning.



Například:



- return na slabém percentilu baseline;

- vyšší volatility;

- vyšší turnover;

- vyšší costs;

- zvýšený reject rate.



WATCH nesmí automaticky měnit strategii.



WATCH nemusí zastavit trading.



==================================================

37. REVIEW_REQUIRED

==================================================



Použij pro významnější drift, který ještě není hard safety breach.



Například:



- opakované WATCH;

- paper výsledky hluboko mimo baseline;

- výrazný cost drift;

- target-vs-actual divergence;

- významná operational degradation.



Trading může podle policy zůstat ACTIVE nebo být explicitně paused operátorem.



Definuj jednoznačně.



==================================================

38. AUTOMATIC SUSPENSION

==================================================



Pouze hard fail-closed triggers mohou automaticky:



ACTIVE

→ SUSPENDED



Minimálně audituj:



account HALTED

unsafe reconciliation

hard drawdown breach podle pinned policy

critical data/evidence inconsistency

critical corporate-action inconsistency



Soft underperformance sama o sobě nesmí bez explicitní policy vytvořit náhodný stop/start systém.



==================================================

39. NO AUTO RESUME

==================================================



SUSPENDED:



nikdy automaticky zpět ACTIVE.



Resume je pouze explicitní operator action.



Resume musí znovu zkontrolovat:



deployment APPROVED

account not HALTED

reconciliation safe

latest data valid

monitoring evidence consistent

policy valid



Jinak fail closed.



==================================================

40. OPERATOR PAUSE

==================================================



Implementuj explicit:



pause(monitoring_id, reason)



ACTIVE/WATCH runtime:



→ PAUSED



PAUSED execution nesmí projít.



Resume pouze explicitní.



==================================================

41. RETIRE

==================================================



Explicit:



retire(monitoring_id, reason)



→ RETIRED



RETIRED je terminal.



Nelze resume.



Pokud chce operator znovu provozovat stejný deployment:



musí vzniknout nový monitoring run/epoch podle explicitních pravidel.



==================================================

42. PHASE6 PAPER EXECUTION GATE

==================================================



Po Phase 7 musí Phase6PaperExecutionService před jakoukoli ekonomickou akcí ověřit:



deployment.status == APPROVED



a:



existuje právě jeden open monitoring run



a:



monitoring.state == ACTIVE.



Pokud:



missing monitoring

PAUSED

SUSPENDED

RETIRED



→ žádný TradingCycleService economic run.



FAIL CLOSED.



Toto je záměrné zpřísnění bezpečnostní hranice po Phase 6.



==================================================

43. UPDATE PHASE6 E2E

==================================================



Existující Phase 6 tests, které očekávají:



APPROVED → execution



aktualizuj pro Phase 7 boundary:



APPROVED

→ explicit monitoring enrollment

→ ACTIVE

→ execution.



Neoslabuj původní assertions.



Phase 6 invariants musí zůstat zachované.



==================================================

44. MONITORING → EXECUTION SEPARATION

==================================================



PaperPerformanceService ani EvaluationService nesmí importovat/volat:



PersistentPaperBroker.process

PersistentExecutionEngine.submit

TradingCycleService.run



pro svou monitoring činnost.



Výjimka:



žádná.



Monitoring pouze čte trading evidence.



Execution gate v Phase6PaperExecutionService smí číst monitoring state.



==================================================

45. DEPLOYMENT/CYCLE LINEAGE

==================================================



Performance musí jednoznačně vědět, které cycles patří monitoring runu.



Preferuj robustní persistentní lineage.



Například:



PaperDeploymentCycleRecord



nebo jinou explicitní vazbu.



Minimálně:



monitoring_id

deployment_id

trading_cycle_id

session_date

linked_at



Unique:



trading_cycle_id



a/nebo vhodný composite invariant.



Nevytvářej attribution pouze heuristikou názvu strategie.



==================================================

46. EXISTING ACCOUNT HISTORY

==================================================



Monitoring starts_at je tvrdá hranice.



Historické trades před monitoring start:



nesmí vstupovat do nové performance série jako nové paper výsledky.



Pokud account při enrollment není "clean":



definuj explicitně policy.



Preferovaně:



enrollment vyžaduje:



- zero positions;

- no open orders;

- reconciliation safe;



NEBO explicitně zaznamenej opening account state jako immutable starting state.



Vyber robustnější variantu podle stávající Phase4 architecture a zdokumentuj ji.



Nevynulovávej účet skrytě.



==================================================

47. PERFORMANCE RECONCILIATION

==================================================



Před capture musí být možné ověřit:



cash

positions

orders

fills



jsou reconciliation-safe.



Pokud poslední reconciliation selhala:



snapshot může být uložen pouze jako:



INVALID / UNSAFE



pokud tento model implementuješ,



ale nesmí být použit jako valid performance evidence.



Preferuj:



capture fails closed + audit event.



==================================================

48. AUDIT EVENTS

==================================================



Každá zásadní Phase 7 událost musí vytvořit audit evidence.



Minimálně:



MONITORING_ENROLLED

MONITORING_PAUSED

MONITORING_RESUMED

MONITORING_SUSPENDED

MONITORING_RETIRED

PERFORMANCE_CAPTURED

PERFORMANCE_EVALUATED

PERFORMANCE_WATCH

PERFORMANCE_REVIEW_REQUIRED

PERFORMANCE_HARD_BREACH



Pokud AuditEventType používá enum:



rozšiř ho bezpečně.



Audit musí obsahovat:



monitoring

deployment

experiment

snapshot

account

session

correlation



podle dostupnosti.



==================================================

49. EVALUATION IMMUTABILITY

==================================================



Persistuj:



PaperPerformanceEvaluationRecord.



Minimálně:



evaluation_id

monitoring_id

performance_snapshot_id

policy_id

created_at

paper_session_count

verdict

reasons_json

paper_metrics_json

baseline_comparison_json

algorithm_version

content_hash



Evaluation je immutable.



Stejný:



monitoring

snapshot

policy

algorithm_version



→ stejná logical evaluation.



==================================================

50. CONCURRENCY — ENROLLMENT

==================================================



Přidej PostgreSQL race:



2 workers současně enroll stejný deployment.



Použij:



2 independent sessions

2 connections

threading.Barrier



Výsledek:



jeden monitoring run.



Oba callers:



same logical result

nebo jeden explicitně idempotentní success.



Žádný duplicate.



==================================================

51. CONCURRENCY — PERFORMANCE CAPTURE

==================================================



PostgreSQL race:



2 workers současně capture stejnou:



monitoring_id + session.



Výsledek:



1 performance snapshot.



No duplicate.



No lost update.



==================================================

52. CONCURRENCY — EVALUATION

==================================================



PostgreSQL race:



2 workers současně evaluate stejný:



monitoring + performance snapshot + policy.



Výsledek:



1 evaluation.



State transition nesmí být aplikována dvakrát.



==================================================

53. STATE TRANSITION CONCURRENCY

==================================================



Otestuj minimálně:



evaluation hard breach

vs.

operator pause



a:



operator resume

vs.

concurrent suspension



Použij row lock/versioning/transaction semantics.



Bezpečnější stav musí vyhrát.



Například:



SUSPENDED nesmí být přepsán stale ACTIVE write.



==================================================

54. CRASH/RETRY SAFETY

==================================================



Monitoring jobs musí být retry safe.



Crash:



po snapshot insert

před job finish



nesmí při retry vytvořit duplicate snapshot.



Stejně evaluation.



Phase5 fencing/lease semantics zůstávají authoritative.



==================================================

55. PHASE 5 AUTOMATION

==================================================



Rozšiř automation pouze o NON-ECONOMIC monitoring workflow.



Preferovaný allowlisted JobType:



MONITOR_PAPER_DEPLOYMENT



nebo rozděleně:



CAPTURE_PAPER_PERFORMANCE

EVALUATE_PAPER_DEPLOYMENT



Vyber architektonicky jednodušší variantu.



Monitoring job smí:



capture

evaluate

audit



Nesmí:



run trading cycle

create order

change strategy parameters

create new experiment

promote deployment

enable live mode.



==================================================

56. AUTOMATION CONFIG

==================================================



Job config smí obsahovat pouze allowlisted:



monitoring_id

deployment_id pokud nutné



Ne:



broker

mode

live

python path

class

arbitrary SQL

arbitrary module

arbitrary URL.



Použij existující validate_payload safety.



==================================================

57. MONITORING SCHEDULE

==================================================



Monitoring musí být exchange-calendar aware.



Denní evaluation může být provedena až po:



latest completed XNYS session.



Nespoléhej na:



"každý den v 16:00 UTC"



protože:



DST

early close

holiday

weekend.



Scheduler může ticknout pravidelně, ale service musí rozhodnout, zda existuje nová completed session.



Pokud ne:



NO_ACTION.



==================================================

58. NO DUPLICATE WEEKEND SNAPSHOTS

==================================================



Saturday/Sunday monitoring job nesmí vytvořit:



Saturday

Sunday



performance session.



Latest completed Friday může být již captured:



→ NO_ACTION.



Holiday stejně.



==================================================

59. AUTOMATION DATA DEPENDENCY

==================================================



Pokud latest completed session market data ještě není SUCCEEDED:



monitoring fail closed / retry podle existing semantics.



Nesmí tiše ocenit portfolio starými daty.



==================================================

60. API — READ

==================================================



Přidej minimální operator/read API podle existujícího FastAPI stylu.



Minimálně:



GET /paper/monitoring

GET /paper/monitoring/{monitoring_id}



GET /paper/monitoring/{monitoring_id}/performance

GET /paper/monitoring/{monitoring_id}/evaluations



GET /paper/deployments/{deployment_id}/performance



GET /paper/performance/summary



Response musí být deterministic a stránkovaný, kde je potřeba.



==================================================

61. API — MUTATIONS

==================================================



Minimální explicit operator actions:



POST /paper/monitoring/policies

POST /paper/deployments/{deployment_id}/monitoring/enroll



POST /paper/monitoring/{id}/pause

POST /paper/monitoring/{id}/resume

POST /paper/monitoring/{id}/retire



Pokud explicit manual evaluation/run-now endpoint dává smysl:



POST /paper/monitoring/{id}/evaluate



Mutation musí používat service layer.



Žádný raw ORM business logic v endpointu.



==================================================

62. API FAIL-CLOSED

==================================================



HTTP API nesmí umožnit:



arbitrary state string

arbitrary SQL

arbitrary strategy import

arbitrary broker

LIVE

credentials

code execution



Použij Pydantic request schemas.



Invalid transition:



409 / 422 podle současné API convention.



==================================================

63. PERFORMANCE SUMMARY

==================================================



Summary pro monitoring/deployment má vracet minimálně:



strategy

strategy version

deployment

monitoring state

paper account

monitoring start

paper sessions



starting equity

current equity

cumulative return

max drawdown

volatility pokud dost dat

Sharpe pokud dost dat

turnover

costs

risk rejects



latest evaluation

latest verdict

latest evaluation reasons



baseline OOS key metrics



current-vs-baseline comparison.



==================================================

64. NO OVERFITTING FEEDBACK LOOP

==================================================



CRITICAL.



Phase 7 nesmí vytvořit:



paper underperforms

→ automatically tune parameters

→ continue same experiment



Pokud strategy potřebuje změnu:



musí se vrátit zpět přes:



nový Phase 6 research experiment

→ validation

→ OOS

→ explicit promotion

→ nový deployment

→ nový monitoring run.



Paper results lze použít jako diagnostickou informaci.



Nikdy jako hidden parameter optimizer.



==================================================

65. NO AUTOMATIC STRATEGY REPLACEMENT

==================================================



Pokud deployment selže:



Phase 7 nesmí automaticky najít "lepší" strategy z leaderboardu.



Žádný:



best backtest

→ replace paper deployment.



Operator rozhoduje.



==================================================

66. PAPER REVIEW READY

==================================================



Pokud implementuješ verdict:



PAPER_REVIEW_READY



musí znamenat pouze:



"deployment splnil předem definovaná paper monitoring kritéria a může být předložen člověku k dalšímu review."



Nesmí:



activate live

create live broker

write live credentials

change TRADING_MODE.



==================================================

67. LIVE TRADING ABSOLUTELY OUT OF SCOPE

==================================================



Phase 7 nesmí přidat:



LiveBroker

Alpaca live

IBKR live

Binance live

broker API credentials

live order adapter

live webhook

TRADING_MODE=live path

LIVE_TRADING_ENABLED runtime



Pokud něco podobného najdeš již v repo jako dormant scaffolding:



neměň ho tak, aby bylo executable.



==================================================

68. RISK ENGINE ZŮSTÁVÁ AUTHORITY

==================================================



Performance HEALTHY:



neznamená RiskEngine approval.



Při každém obchodě stále musí:



ProductionRiskEngine



znovu rozhodnout.



Monitoring nesmí cacheovat risk approval mezi cycles.



==================================================

69. HALTED

==================================================



Pokud account HALTED:



Phase6PaperExecutionService stále nevytvoří order/fill.



Phase7 monitoring:



detekuje HALTED

→ SUSPENDED nebo hard-breach evidence podle policy.



Resume monitoring nesmí obejít account HALTED.



==================================================

70. RECONCILIATION

==================================================



Pokud reconciliation je unsafe:



monitoring hard breach.



Žádný performance HEALTHY.



Žádný resume bez safe reconciliation.



==================================================

71. TARGET VS ACTUAL DRIFT

==================================================



Pokud persistence dovoluje přesně rekonstruovat:



desired target

actual resulting position



persistuj target-vs-actual drift.



Použij například:



sum(abs(actual_weight - target_weight))



nebo jasně definovanou metriku.



Pokud authoritative target evidence chybí:



přidej minimální immutable target evidence k deployment-cycle lineage.



Nevymýšlej ji zpětně z fills.



==================================================

72. EXECUTION COST DRIFT

==================================================



Pokud lze autoritativně spočítat:



expected raw reference price

vs.

actual fill price



persistuj:



slippage amount

slippage bps

commission bps

total execution cost bps.



Pokud Phase4 fill neukládá reference:



přidej minimální immutable execution reference při cycle.



Nedělej heuristic inference z budoucích prices.



==================================================

73. ZERO TRADE SESSIONS

==================================================



Session bez obchodu je stále valid performance session.



Performance time series nesmí obsahovat jen dny s fills.



Jinak by Sharpe/volatility/exposure byly zkreslené.



==================================================

74. EXPOSURE

==================================================



Exposure počítej z marked portfolio state na každé performance session.



Ne:



z počtu trades.



To je důležitý dříve auditovaný invariant projektu.



==================================================

75. DRAWDOWN

==================================================



Drawdown:



current equity / running peak - 1.



Running peak pouze z monitoring performance series.



Žádná future peak knowledge.



==================================================

76. RETURN

==================================================



Daily return:



equity_t / equity_t-1 - 1



pokud nejsou external flows.



Cumulative:



equity_t / starting_equity - 1



Podle explicitní definice.



Používej Decimal tam, kde je vhodné.



==================================================

77. VOLATILITY / SHARPE

==================================================



Pokud sample není dostatečný:



NOT_EVALUATED / NULL.



Nedělej numericky zavádějící annualized Sharpe z jednoho nebo dvou sessions.



Minimum lze definovat policy.



==================================================

78. COSTS

==================================================



Rozliš:



commission

slippage

total execution costs



pokud evidence existuje.



Nevydávej spread/slippage assumption z research za realized paper cost.



==================================================

79. RISK REJECTION RATE

==================================================



Definuj přesně denominator.



Například:



rejected intents / evaluated intents



ne:



rejected orders / fills



Pokud data model neumí denominator přesně rekonstruovat:



přidej evidenci nebo metriku nepoužívej.



==================================================

80. DATA QUALITY EVENTS

==================================================



Performance monitoring musí umět rozlišit:



strategy underperformance



vs.



system/data failure.



Evaluation reason codes například:



RETURN_DRIFT

VOLATILITY_DRIFT

DRAWDOWN_BREACH

TURNOVER_DRIFT

COST_DRIFT

TARGET_TRACKING_DRIFT

RISK_REJECTION_DRIFT

DATA_UNAVAILABLE

INGESTION_FAILED

RECONCILIATION_UNSAFE

ACCOUNT_HALTED

CORPORATE_ACTION_ERROR



Persistuj machine-readable reasons.



==================================================

81. MONITORING ALGORITHM VERSION

==================================================



Baseline/evaluation musí obsahovat algorithm version.



Například:



paper-monitoring-v1

baseline-bootstrap-v1



Změna algoritmu nesmí tiše přepsat staré evaluation.



==================================================

82. REPRODUCIBILITY

==================================================



Pro libovolnou evaluation musí být možné dohledat:



deployment

experiment

research snapshot

strategy/version

parameters

code SHA

monitoring policy

baseline hash

paper performance snapshot IDs

market observation IDs/revisions

orders/fills

reconciliation

algorithm version

verdict reasons.



==================================================

83. POSTGRESQL PERSISTENCE

==================================================



Nové persistentní modely musí mít:



FK

unique constraints

indexes

check constraints kde dávají smysl

timezone-aware timestamps

deterministic IDs kde vhodné.



Žádné pouze in-memory Phase 7 registry.



SQLite zůstává test adapter.



PostgreSQL je authoritative production DB.



==================================================

84. ALEMBIC

==================================================



Historické migrations:



NEMĚŇ.



Přidej novou forward migration.



Preferovaný revision po současném head.



Migration musí vytvořit pouze nové Phase7 schema změny.



Fresh:



alembic upgrade head



musí PASS.



Ověř také upgrade:



previous head → Phase7 head.



Pokud downgrade není součástí project convention, nevynucuj.



==================================================

85. INDEXING

==================================================



Přidej indexy minimálně pro časté queries:



monitoring deployment

monitoring account

monitoring state



performance monitoring_id + session_date

performance deployment_id + session_date



evaluation monitoring_id + created_at

evaluation verdict



policy identity



cycle lineage deployment/monitoring/cycle.



==================================================

86. API QUERY EFFICIENCY

==================================================



Performance time-series endpoint nesmí dělat N+1 query per day.



Použij normální SQL queries.



Pagination/range filters například:



start_date

end_date

limit

offset



kde dává smysl.



==================================================

87. UNIT TESTS

==================================================



Přidej minimálně:



backend/tests/test_phase7.py



Scope:



policy validation

policy immutability

baseline replay

baseline hash

enrollment validations

state machine

pause/resume/retire

daily returns

cumulative returns

drawdown

exposure

turnover

costs

bootstrap determinism

evaluation verdicts

insufficient data

hard breach

no auto-resume

no auto-retune

no live path



==================================================

88. CALENDAR REGRESSIONS

==================================================



Phase7 test musí explicitně ověřit performance capture:



normal day before close

normal day after close

weekend

holiday

early close before

early close after



No duplicate weekend capture.



==================================================

89. POSTGRESQL TESTS

==================================================



Přidej například:



backend/tests/test_phase7_postgres.py



Musí běžet pouze pokud:



RUN_POSTGRES_TESTS=1



ale musí být explicitně zahrnut v CI PostgreSQL job.



Testy:



- monitoring enrollment race;

- one-open-monitoring-per-account;

- capture exactly once race;

- evaluation exactly once race;

- state transition concurrency;

- performance persistence;

- baseline immutability;

- snapshot immutability;

- FK/unique/check constraints.



==================================================

90. PHASE7 FULL E2E

==================================================



Přidej:



backend/tests/test_phase7_e2e_postgres.py



Authoritative full flow:



fixture/provider data

→ PersistentMarketDataService

→ PIT universe

→ DatasetSnapshotService

→ Phase6ExperimentRunner

→ explicit promotion

→ PAPER_CANDIDATE

→ DeploymentService.create

→ PENDING_REVIEW

→ approve

→ APPROVED

→ Phase7 monitoring policy

→ monitoring enrollment

→ ACTIVE

→ current data

→ Phase6PaperExecutionService

→ TradingCycleService

→ RiskEngine

→ PaperBroker

→ fills

→ reconciliation

→ next completed sessions

→ performance snapshots

→ performance series

→ evaluation

→ persisted verdict



Assert:



research baseline pinned

paper account pinned

performance correct

no direct broker path

no future data

monitoring immutable.



==================================================

91. MULTI-SESSION E2E

==================================================



Full Phase7 E2E nesmí mít pouze jednu session.



Simuluj dostatek sessions pro:



returns

drawdown

volatility

turnover

comparison



alespoň několik desítek XNYS sessions tam, kde to test runtime dovolí.



Použij menší policy.minimum_sessions v test fixture, pokud chceš držet CI rychlé.



Production default není diktován test fixture.



==================================================

92. HEALTHY SCENARIO

==================================================



E2E:



paper výkon přibližně v baseline očekávání.



Výsledek:



HEALTHY



po minimum sessions.



Monitoring zůstává:



ACTIVE.



==================================================

93. WATCH SCENARIO

==================================================



Vytvoř controlled drift.



Assert:



WATCH



a:



monitoring stále ACTIVE



pokud policy neurčuje hard stop.



==================================================

94. REVIEW_REQUIRED SCENARIO

==================================================



Významný non-hard drift.



Assert:



REVIEW_REQUIRED



bez:



parameter mutation

deployment replacement

live promotion.



==================================================

95. HARD SUSPENSION E2E

==================================================



Vytvoř například hard drawdown nebo reconciliation unsafe.



Assert:



monitoring:



ACTIVE

→ SUSPENDED



A další Phase6PaperExecutionService call:



→ zero orders

→ zero fills.



==================================================

96. RESUME E2E

==================================================



Po SUSPENDED:



pokus resume při stále unsafe:



FAIL.



Po explicitní opravě/safe evidence:



operator resume:



ACTIVE.



Žádný automatic resume.



==================================================

97. HALTED E2E

==================================================



Approved deployment

+

ACTIVE monitoring

+

HALTED account.



Execution:



zero order/fill.



Monitoring evaluation:



hard safety reason.



Resume:



fail.



==================================================

98. CORPORATE ACTION PAPER E2E

==================================================



Pokud audit zjistí nutnost PaperCorporateActionService:



přidej full PostgreSQL E2E:



open position

→ split

→ quantity/basis adjustment

→ no fake P&L

→ performance curve continuous



a:



open position

→ dividend

→ cash credit once

→ performance includes economic dividend

→ retry does not double count.



==================================================

99. PROVIDER CORRECTION REGRESSION

==================================================



Po performance snapshotu T:



provider opraví observation T.



Assert:



historical performance snapshot T unchanged.



Historical evaluation unchanged.



Novější capture/evaluation může zaznamenat correction evidence, ale nesmí rewrite historii.



==================================================

100. BASELINE CORRECTION REGRESSION

==================================================



Po monitoring enrollment:



provider correction změní latest historical market data.



Baseline je stále založená na immutable research snapshotu.



Assert:



baseline hash unchanged

baseline daily returns unchanged

baseline metrics unchanged.



==================================================

101. CRASH RECOVERY E2E

==================================================



Simuluj:



monitoring job vytvoří snapshot



worker ztratí lease před finish.



Retry jiným workerem:



žádný duplicate snapshot.



Použij existující Phase5 fencing semantics.



==================================================

102. AUTOMATION POSTGRESQL E2E

==================================================



Pokud přidáš nový JobType:



Scheduler

→ JobRun

→ Worker

→ monitor capture/evaluate

→ JobRun SUCCEEDED



Assert:



immutable config snapshot

correlation

exactly once

retry safe.



No economic trading action.



==================================================

103. STATIC SAFETY TEST

==================================================



Rozšiř:



test_paper_only_architecture.py



nebo přidej:



test_phase7_architecture.py



Assert:



PaperMonitoringService

PaperPerformanceService

PaperPerformanceEvaluationService

baseline service



nesmí importovat/volat:



PersistentPaperBroker

PersistentExecutionEngine

LiveBroker



Monitoring modules nesmí volat:



TradingCycleService.run



Monitoring automation JobType nesmí provádět trading.



==================================================

104. NO LIVE PATH REGRESSION

==================================================



Repository scan musí stále potvrdit:



no LiveBroker implementation

no live broker credentials

no executable live mode

no Phase7 → live promotion



Health endpoint stále:



paper

live_trading_enabled=false



podle aktuální architecture.



==================================================

105. PERFORMANCE MANIPULATION REGRESSION

==================================================



Přidej test, že evaluation nemůže změnit:



ExperimentRecord.selected_parameters_json

StrategyDeploymentRecord.parameters_json

ExperimentRecord.decision

research snapshot

strategy version.



==================================================

106. NO AUTOMATIC NEW EXPERIMENT

==================================================



Evaluation WATCH/REVIEW/SUSPENDED:



nesmí vytvořit nový ExperimentRecord.



Assert count before/after.



==================================================

107. NO AUTO DEPLOYMENT

==================================================



Evaluation nesmí vytvořit:



StrategyDeploymentRecord.



Assert.



==================================================

108. NO AUTO LIVE

==================================================



Evaluation nemá žádný field/action:



LIVE_APPROVED

LIVE

broker credentials



nebo ekvivalent.



==================================================

109. API TESTS

==================================================



Rozšiř API testy minimálně o:



create policy

enroll

read monitoring

performance query

evaluation query

pause

resume validation

retire

invalid transition

missing deployment

missing monitoring



No arbitrary execution payload.



==================================================

110. CI

==================================================



Aktualizuj:



.github/workflows/ci.yml



Quality stále:



uv --version

uv lock --check

uv sync --locked --all-groups

ruff format --check

ruff check

mypy



Unit/research přidej Phase7 non-PG tests.



API job přidej relevant Phase7 endpoint tests.



PostgreSQL job:



RUN_POSTGRES_TESTS=1



a explicitně:



Phase3

Phase4

Phase5

Phase6

Phase7



tests.



==================================================

111. CI FALSE-GREEN ZÁKAZ

==================================================



Zakázáno:



SQLite jako PG concurrency proof

shared Session mezi workers

sleep-only race

raw inserts místo production service v business E2E

unconditional skip

test file mimo CI

monkeypatch bypass production flow

test bez meaningful assertions



==================================================

112. FULL POSTGRES TEST COMMAND

==================================================



PostgreSQL CI musí zahrnout minimálně:



tests/test_phase3_platform.py

tests/test_phase4.py

tests/test_phase5.py

tests/test_phase5_postgres.py



všechny Phase6 testy



a:



tests/test_phase7.py

tests/test_phase7_postgres.py

tests/test_phase7_e2e_postgres.py



plus případné další nové Phase7 soubory.



==================================================

113. LOCAL LOCKED GATE

==================================================



Pokud environment dovolí:



cd backend



uv --version

uv lock --check

uv sync --locked --all-groups



uv run ruff format --check .

uv run ruff check .

uv run mypy src/quantlab

uv run pytest -q



Report exact:



passed

skipped

failed.



==================================================

114. CODEX CLOUD ENVIRONMENT LIMITATION

==================================================



DŮLEŽITÉ.



Pokud Codex sandbox znovu obsahuje:



uv 0.7.22



místo:



uv 0.12.3



nebo nemá:



PostgreSQL

Docker

origin



NEZASTAVUJ IMPLEMENTACI.



NEVRACEJ TASK INCOMPLETE pouze kvůli environmentu.



Implementuj:



CODE

TESTS

CI

DOCS



celý Phase7 scope.



Potom odděl:



IMPLEMENTATION COMPLETE



od:



VERIFICATION PENDING IN GITHUB CI/CODESPACE.



PostgreSQL tests musí být NAPSANÉ, i pokud je sandbox neumí spustit.



==================================================

115. DEPENDENCIES

==================================================



Nepřidávej novou dependency bez skutečné potřeby.



Bootstrap implementuj pokud možno pomocí:



stdlib

Decimal

existující numpy/pandas



podle architektury.



Pokud přidáš dependency:



uv.lock nesmí být ručně upraven.



Pokud sandbox nemůže resolve lock:



nepřidávej dependency, pokud lze totéž bezpečně implementovat existujícím stackem.



==================================================

116. DETERMINISTIC BOOTSTRAP

==================================================



Pokud implementuješ bootstrap:



žádný global random.



Explicit deterministic seed.



Preferuj:



random.Random(seed)



nebo explicitní numpy Generator.



Block bootstrap musí mít unit tests:



same seed/input → same output.



Different horizon → deterministic corresponding output.



==================================================

117. BOOTSTRAP EDGE CASES

==================================================



Ošetři:



empty baseline

one return

zero volatility

paper horizon > baseline horizon

paper horizon < block size

all zero returns



Bez NaN/inf leakage do DB/API.



==================================================

118. DECIMAL / FLOAT SAFETY

==================================================



Financial ledger calculations:



Decimal.



Statistical calculations mohou použít float tam, kde je to rozumné, ale:



boundary conversion explicitně.



Nesmí vzniknout:



NaN

Infinity



v persisted JSON/API.



==================================================

119. TIMEZONE

==================================================



Všechny DB timestamps:



UTC timezone-aware.



Exchange timezone pouze calendar boundary.



No naive datetime.



Regression test mandatory.



==================================================

120. MIGRATION FRESH DB

==================================================



Na fresh PostgreSQL:



uv run alembic -c ../alembic.ini upgrade head



PASS.



Migration chain musí stále projít:



Phase3

Phase4

Phase5

Phase6

Phase7.



==================================================

121. MIGRATION EXISTING DB

==================================================



Ověř upgrade:



současný Phase6 Alembic head



→ nový Phase7 head.



Bez data loss.



Historical tables:



nemazat.



==================================================

122. DOCUMENTATION

==================================================



Aktualizuj pravdivě:



README.md

docs/architecture.md

docs/database.md

docs/implementation-plan.md

docs/paper-trading.md

docs/strategy-research.md

docs/risk-management.md

docs/operations.md

docs/live-trading-safety.md



Přidej podle potřeby:



docs/performance-monitoring.md



Preferuji nový:



docs/performance-monitoring.md



==================================================

123. PERFORMANCE MONITORING DOC

==================================================



Musí vysvětlit:



baseline OOS

paper performance

why horizon matching matters

bootstrap monitoring

drawdown

cost drift

target drift

evaluation states

minimum sample

pause/suspend/resume

no auto tuning

no live promotion



A explicitně:



good paper performance ≠ guaranteed future profit.



==================================================

124. IMPLEMENTATION PLAN

==================================================



Aktualizuj docs/implementation-plan.md.



Přidej novou projektovou:



Phase 7 — Paper Performance Monitoring, Drift Detection & Strategy Lifecycle



s:



status

scope

acceptance criteria

tests

remaining work.



Nevraceuj starou historickou Phase7 Portfolio & Risk na IN PROGRESS.



Jasně vysvětli numbering transition.



==================================================

125. OBSERVABILITY

==================================================



Phase7 nemusí implementovat kompletní:



Prometheus

Grafana

central log stack.



Ale všechny monitoring outcomes musí být:



persistent

queryable

auditable.



Full observability stack zůstává future phase.



==================================================

126. FRONTEND

==================================================



NEIMPLEMENTUJ Next.js dashboard v této Phase7.



API + persistence + services jsou dostatečné.



Web dashboard přijde později.



==================================================

127. SECURITY

==================================================



Neimplementuj celý RBAC/Auth v této fázi.



Ale nové mutation endpointy musí:



fail closed

validate input

neumožnit arbitrary execution.



Never commit secrets.



==================================================

128. PERFORMANCE RETENTION

==================================================



Performance snapshots a evaluations jsou audit evidence.



Nemaž je automaticky.



No retention cleanup v Phase7.



==================================================

129. PAPER ACCOUNT DELETE

==================================================



FK musí zabránit smazání account/deployment evidence, pokud by tím došlo k rozbití monitoring lineage.



Použij RESTRICT tam, kde je vhodné.



==================================================

130. DEPLOYMENT DELETE

==================================================



Stejně:



monitoring evidence musí zachovat deployment lineage.



Žádný cascade delete důležitého auditu.



==================================================

131. CONTENT HASH

==================================================



Baseline

performance snapshot

evaluation

policy



mají mít canonical deterministic content hash tam, kde dává smysl.



Použij canonical JSON:



sort_keys

stable separators

explicit Decimal serialization.



==================================================

132. IDEMPOTENT IDS

==================================================



Preferuj hash identity pro logical:



baseline

snapshot

evaluation



spíše než random UUID tam, kde exact logical idempotence dává smysl.



Monitoring run může mít explicit ID, ale repeated identical enrollment musí mít definovanou semantics.



==================================================

133. NO HIDDEN MUTABILITY

==================================================



Neměň historical snapshot JSON po insertu.



Neměň old evaluation.



Neměň baseline.



Stateful monitoring run samozřejmě mění current state, ale každý transition musí mít audit trail.



==================================================

134. TRANSITION HISTORY

==================================================



Pokud AuditEvent nestačí pro snadné query:



zaveď:



PaperMonitoringTransitionRecord



nebo ekvivalent.



Preferuj reuse AuditEvent, pokud bezpečně zachová:



from_state

to_state

reason

actor/source

timestamp.



Nevytvářej redundantní tabulku bez potřeby.



==================================================

135. EXPLICIT OPERATOR VS AUTOMATION SOURCE

==================================================



State transitions musí rozlišit:



OPERATOR

AUTOMATION

SAFETY



source.



Hard automatic suspension:



SAFETY.



Manual pause:



OPERATOR.



==================================================

136. PERFORMANCE POLICY CHANGE

==================================================



Policy change nesmí zpětně změnit verdict staré evaluation.



Nová policy:



nová evaluation identity.



Pokud se provede re-evaluation historického snapshotu novou policy:



obě evaluations musí zůstat uložené.



==================================================

137. MONITORING VERSION CHANGE

==================================================



Stejně algorithm_version.



Nová algorithm version:



nová evaluation.



Old evaluation preserved.



==================================================

138. PERFORMANCE READ MODE

==================================================



Latest endpoint musí jasně definovat:



latest by performance session

ne jen created_at.



Corrections/re-evaluation nesmí zamíchat chronologii.



==================================================

139. DEPLOYMENT PARAMETER IMMUTABILITY

==================================================



Po monitoring enrollment:



pokud někdo pokusí změnit deployment parameters/strategy lineage:



execution + monitoring musí fail closed.



Deployment immutable semantics z Phase6 zachovej.



==================================================

140. STRATEGY VERSION IMMUTABILITY

==================================================



Pokud runtime registry strategy version už neodpovídá pinned deployment:



FAIL CLOSED.



Monitoring nesmí přeskočit na latest.



==================================================

141. CODE SHA

==================================================



Baseline musí pinovat experiment code_sha.



Není nutné, aby současný running Git HEAD byl stejný pro všechny read-only monitoring calculations.



Ale pokud business logic replay vyžaduje exact code semantics:



algorithm/replay version musí být explicitně zachycena.



Nefabrikuj code SHA.



==================================================

142. PERFORMANCE AFTER CODE UPDATE

==================================================



Deployment je pinned na strategy version/parameters, ne obecně "latest code".



Pokud změna kódu změní semantics stejné strategy version:



to je registry invariant violation.



Fail closed / audit.



Nevydávej změněnou implementaci za stejnou version.



==================================================

143. REBALANCE FREQUENCY

==================================================



Monitoring nevyhodnocuje session bez rebalance jako "missing trade".



Zero-trade může být správný outcome.



Respect strategy rebalance_frequency.



==================================================

144. PAPER SESSION WITH NO POSITION

==================================================



Cash-only session je valid.



Exposure 0.



Return může být 0.



Není to automaticky system failure.



==================================================

145. MISSING DATA VS CASH

==================================================



Pokud není žádná position a strategy neměla rebalance:



nemusíš vyžadovat price pro neexistující holdings.



Ale pokud target/evaluation potřebuje universe data:



použij odpovídající validated data path.



Nezvyšuj uměle data dependency.



==================================================

146. TARGET ATTRIBUTION

==================================================



Pokud paper strategy vytvořila target 0:



to je validní signal.



Nesmí být zaměněno za missing target.



==================================================

147. PAPER FILL PARTIAL

==================================================



Performance cost/tracking metrics musí respektovat partial fills.



Actual position ≠ desired target může být legitimně kvůli:



cash

risk

partial fill.



Reason evidence má pomoci odlišit:



risk constrained

execution constrained

data error.



==================================================

148. RISK DECISIONS

==================================================



Performance evaluation může číst:



RiskDecisionRecord



ale nesmí jej modifikovat.



Risk decision history immutable.



==================================================

149. PAPER RECONCILIATION FAILURE

==================================================



Pokud Phase4 reconciliation HALTne account:



Phase7 state nesmí následně account resume.



To je jiná authority.



Operator musí řešit Phase4 reconciliation/risk recovery samostatně.



==================================================

150. DEFAULT MONITORING BEHAVIOR

==================================================



Po Phase7 má nový paper deployment typicky projít:



research

→ promotion

→ deployment

→ approval

→ monitoring enrollment

→ active paper trading.



Monitoring policy musí být explicitně pinned.



No silent default policy, pokud by obsahovala ekonomické hard thresholds.



Může existovat default template, ale enrollment musí uložit přesný policy identity.



==================================================

151. DEFAULT POLICY TEMPLATE

==================================================



Pokud vytvoříš default template:



nesmí být prezentována jako univerzálně optimální investment policy.



Jde o operational example/default.



Všechny hodnoty musí být dokumentované.



==================================================

152. PERFORMANCE EXPORT

==================================================



Pokud je snadné, service/API může exportovat JSON/CSV performance series.



Není povinné pro COMPLETE.



Nepřidávej complexity na úkor core scope.



==================================================

153. REPORT ARTIFACT

==================================================



Přidej service:



PaperPerformanceReportService



pouze pokud přirozeně zapadá.



Může vracet structured JSON summary.



PDF/HTML report není Phase7 blocker.



==================================================

154. ADVERSARIAL PASS

==================================================



Po implementaci proveď druhý adversarial audit.



Zkus:



1. enroll RESEARCH_ONLY experiment

2. enroll unapproved deployment

3. two active deployments same account

4. malformed policy

5. mutate policy

6. mutate baseline

7. provider correction after baseline

8. provider correction after performance snapshot

9. future observed_at

10. STARTED ingestion

11. FAILED ingestion

12. weekend capture

13. holiday capture

14. early-close capture

15. duplicate capture

16. concurrent capture

17. concurrent evaluation

18. stale state transition

19. PAUSED execution

20. SUSPENDED execution

21. RETIRED execution

22. resume while HALTED

23. resume while reconciliation unsafe

24. WATCH auto-tunes strategy

25. REVIEW_REQUIRED creates deployment

26. SUSPENDED creates new experiment

27. monitoring calls broker

28. one-session Sharpe

29. zero-vol baseline

30. split creates fake -50%

31. dividend double count

32. late-known corporate action

33. partial fill tracking

34. no-trade session

35. cash-only session

36. runtime strategy version mismatch

37. policy version change rewrites history

38. evaluation version change rewrites history

39. worker retry duplicate

40. crash between snapshot and finish.



Každý nalezený bug:



reproduce

→ regression

→ fix

→ rerun.



==================================================

155. DEFINITION OF DONE — DATA/PERSISTENCE

==================================================



[ ] docs/codex/phase7-complete.md

[ ] Phase7 forward Alembic migration

[ ] immutable monitoring policy

[ ] immutable expectation baseline

[ ] monitoring run

[ ] performance snapshots

[ ] evaluations

[ ] cycle/deployment monitoring lineage

[ ] indexes/FKs/constraints

[ ] one open monitoring deployment per account



==================================================

156. DEFINITION OF DONE — BASELINE

==================================================



[ ] deterministic Phase6 OOS replay

[ ] no duplicated research engine

[ ] baseline daily returns/equity evidence

[ ] baseline aggregate metrics

[ ] baseline lineage

[ ] baseline content hash

[ ] replay matches ExperimentRecord

[ ] provider correction cannot mutate baseline



==================================================

157. DEFINITION OF DONE — PERFORMANCE

==================================================



[ ] calendar-aware performance capture

[ ] validated current data

[ ] marked equity

[ ] daily returns

[ ] cumulative return

[ ] drawdown

[ ] exposure

[ ] turnover

[ ] costs where authoritative

[ ] risk rejection evidence

[ ] reconciliation state

[ ] source observation lineage

[ ] zero-trade sessions supported

[ ] cash-only sessions supported



==================================================

158. DEFINITION OF DONE — EVALUATION

==================================================



[ ] minimum sample

[ ] horizon-aware comparison

[ ] deterministic bootstrap or equivalent robust method

[ ] HEALTHY

[ ] WATCH

[ ] REVIEW_REQUIRED

[ ] SUSPENDED

[ ] machine-readable reasons

[ ] immutable evaluation

[ ] policy pinned

[ ] algorithm version pinned



==================================================

159. DEFINITION OF DONE — LIFECYCLE

==================================================



[ ] explicit enrollment

[ ] ACTIVE

[ ] PAUSED

[ ] SUSPENDED

[ ] RETIRED

[ ] explicit pause

[ ] explicit resume

[ ] no auto-resume

[ ] explicit retire

[ ] execution blocked unless ACTIVE

[ ] HALTED cannot be bypassed

[ ] reconciliation unsafe cannot be bypassed



==================================================

160. DEFINITION OF DONE — SAFETY

==================================================



[ ] monitoring never calls broker

[ ] monitoring never calls economic execution

[ ] no auto retune

[ ] no auto experiment

[ ] no auto deployment

[ ] no live path

[ ] RiskEngine still authoritative

[ ] paper only

[ ] deployment exact strategy/version/params preserved



==================================================

161. DEFINITION OF DONE — CORPORATE ACTIONS

==================================================



[ ] paper corporate-action path audited

[ ] split continuity proven

[ ] dividend handling proven

[ ] no double-count

[ ] late-known action causal

[ ] delisting fail-closed if unsupported



Pokud stávající Phase4/6 runtime již všechny tyto invarianty správně splňuje:



neimplementuj redundantní service.



Dokaž to tests.



==================================================

162. DEFINITION OF DONE — CONCURRENCY

==================================================



[ ] enrollment PostgreSQL race

[ ] performance capture PostgreSQL race

[ ] evaluation PostgreSQL race

[ ] state transition race

[ ] crash/retry idempotence

[ ] independent sessions/connections

[ ] deterministic synchronization



==================================================

163. DEFINITION OF DONE — AUTOMATION

==================================================



[ ] allowlisted non-economic monitoring job

[ ] immutable job config

[ ] exchange-calendar aware

[ ] no duplicate weekend/holiday sessions

[ ] retry safe

[ ] Phase5 lease/fencing preserved

[ ] no broker/trading action from monitoring job



==================================================

164. DEFINITION OF DONE — API

==================================================



[ ] policy API

[ ] enrollment API

[ ] monitoring read

[ ] performance series

[ ] evaluations

[ ] summary

[ ] pause

[ ] resume

[ ] retire

[ ] invalid transition fail closed

[ ] no arbitrary execution



==================================================

165. DEFINITION OF DONE — TESTS/CI

==================================================



[ ] Phase7 unit tests

[ ] Phase7 API tests

[ ] Phase7 PostgreSQL tests

[ ] Phase7 PostgreSQL E2E

[ ] healthy scenario

[ ] watch scenario

[ ] review scenario

[ ] suspension

[ ] resume

[ ] HALTED

[ ] corrections

[ ] paper-only architecture

[ ] corporate actions

[ ] CI wiring

[ ] locked quality gate

[ ] fresh Alembic upgrade



==================================================

166. NO FAKE COMPLETENESS

==================================================



Phase7 není COMPLETE pouze proto, že:



performance table exists



nebo:



API endpoint exists



nebo:



equity chart data exists



nebo:



bootstrap function exists.



COMPLETE vyžaduje celý lifecycle:



research baseline

→ paper monitoring

→ persistent performance

→ expected-vs-realized comparison

→ drift evaluation

→ safe state transitions

→ automation

→ E2E

→ CI.



==================================================

167. VERDICT

==================================================



Použij pouze:



COMPLETE



COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING



INCOMPLETE



COMPLETE pouze pokud:



- všechny functional requirements jsou hotové;

- locked suite PASS;

- PostgreSQL Phase7 integration/concurrency PASS;

- fresh Alembic PASS.



COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING pouze pokud:



- celý implementation + required tests + CI scope je hotový;

- pouze konkrétní sandbox nemůže testy spustit.



INCOMPLETE:



pokud chybí code nebo mandatory test scope.



==================================================

168. AUDIT READINESS

==================================================



Použij:



READY FOR PHASE 7 AUDIT GATE



pouze pokud jsou relevantní gates skutečně zelené.



Pokud sandbox neumí PostgreSQL spustit, ale scope je kompletně implementován:



IMPLEMENTATION COMPLETE

VERIFICATION PENDING IN GITHUB CI/CODESPACE



a:



NOT YET VERIFIED FOR PHASE 7 AUDIT GATE



je přijatelné.



==================================================

169. TASK NESMÍ SKONČIT PŘEDČASNĚ

==================================================



Neukončuj práci po:



- schema

- baseline

- performance service

- evaluation

- API

- automation

- tests

- docs



samostatně.



Dokonči celý Phase7 scope v jednom tasku.



Pokud narazíš na skutečný blocker v existující Phase4/5/6 implementaci, který brání korektní Phase7:



oprav jej v tomto tasku



+

přidej regression.



==================================================

170. NEPŘIDÁVEJ PHASE 8

==================================================



Neimplementuj:



web dashboard

full observability stack

auth/RBAC

live broker

live trading

ML/AI strategies

options

short selling

leverage

FX

crypto

intraday

distributed research cluster



pokud nejsou nezbytné k opravě regresního invariant.



==================================================

171. FINAL GATES

==================================================



Pokud environment dovolí:



cd backend



uv --version

uv lock --check

uv sync --locked --all-groups



uv run ruff format --check .

uv run ruff check .

uv run mypy src/quantlab



uv run pytest -q



A PostgreSQL:



RUN_POSTGRES_TESTS=1 uv run pytest -q \

  tests/test_phase3_platform.py \

  tests/test_phase4.py \

  tests/test_phase5.py \

  tests/test_phase5_postgres.py \

  tests/test_phase6.py \

  tests/test_phase6_runtime.py \

  tests/test_xnys_calendar.py \

  tests/test_paper_only_architecture.py \

  tests/test_phase6_postgres.py \

  tests/test_phase6_audit_fixes.py \

  tests/test_phase6_experiment_audit.py \

  tests/test_phase6_current_data_postgres.py \

  tests/test_phase6_e2e_postgres.py \

  tests/test_phase7.py \

  tests/test_phase7_postgres.py \

  tests/test_phase7_e2e_postgres.py



Přizpůsob seznam, pokud Phase7 rozdělíš do dalších relevantních test files.



Všechny musí být explicitně v CI.



==================================================

172. ALEMBIC GATE

==================================================



Fresh PostgreSQL:



uv run alembic -c ../alembic.ini upgrade head



PASS.



Reportuj final head.



==================================================

173. TEST COUNTS

==================================================



Ve final reportu uveď přesně:



quality

unit/research

API

PostgreSQL



passed

skipped

failed.



Neříkej pouze:



tests pass.



==================================================

174. FINAL REPORT

==================================================



Na konci vrať:



# Starting state

SHA

branch

working tree



# Phase 7 verdict

COMPLETE / COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING / INCOMPLETE



# Architecture

nový flow



# Persistence

nové tables/models/migration



# Monitoring policy

immutability/versioning



# Baseline

OOS replay

lineage

correction safety



# Paper performance

metrics

valuation

calendar

data causality



# Expected vs realized

bootstrap/comparison

minimum sample



# Lifecycle

ACTIVE

PAUSED

SUSPENDED

RETIRED



# Safety

HALTED

reconciliation

no auto resume

no auto tuning



# Corporate actions

paper behavior



# Automation

new JobType

idempotence

fencing



# Concurrency

enrollment

capture

evaluation

transitions



# API

endpoints



# E2E

research → paper → multi-session monitoring → evaluation



# Paper only

confirm no live path



# Tests

exact results



# PostgreSQL

exact results



# Alembic

head

fresh upgrade result



# CI

jobs/test files



# Adversarial findings

bugs found and fixed



# Documentation

files updated



# Remaining gaps

NONE

or exact blockers



# Phase 7 Audit readiness

READY FOR PHASE 7 AUDIT GATE

or exact status



# Git

starting SHA

ending SHA

branch

commits

changed files

PR



==================================================

175. GIT

==================================================



Preferovaná branch:



codex/phase7-paper-performance-monitoring



Preferovaný commit:



Implementuj Phase 7 paper performance monitoring



Preferovaný PR title:



Phase 7 — Paper Performance Monitoring and Strategy Lifecycle



Pokud je změn mnoho:



můžeš vytvořit více logických commitů.



Nevytvářej více neúplných completion PRs.



Cílem je jeden kompletní Phase7 PR připravený pro CI a audit.



==================================================

176. DŮLEŽITÁ POSLEDNÍ KONTROLA

==================================================



Před dokončením polož explicitně otázku nad kódem:



"Dokáže po této Phase 7 systém dlouhodobě provozovat schválenou strategii v paper tradingu a objektivně, reprodukovatelně a bez look-ahead porovnávat její skutečný paper výkon s immutable OOS očekáváním?"



Pokud ne:



task není hotový.



Druhá otázka:



"Může Phase 7 sama změnit strategii, obejít RiskEngine nebo spustit live trading?"



Správná odpověď musí být:



NE.



==================================================

177. START

==================================================



Začni nyní.



Nevracej pouze návrh architektury.



Nevracej pouze TODO list.



Prozkoumej aktuální main.



Vytvoř docs/codex/phase7-complete.md.



Implementuj celý Phase7 scope.



Napiš všechny unit/PostgreSQL/E2E tests i v případě, že je konkrétní Codex sandbox nemůže spustit.



Oprav všechny Phase7 blockers, které během implementace odhalíš.



Cíl po dokončení:



Phase 7 implementation complete

→ PR

→ GitHub CI

→ Phase 7 Audit Gate



A TEPRVE PO PASS:



READY FOR PHASE 8.
