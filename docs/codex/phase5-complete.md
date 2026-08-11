# Autonomous Quant Lab — Phase 5 Complete Implementation

Pracuj přímo v aktuálním repository projektu **Autonomous Quant Lab**.

Tvým úkolem je implementovat celou:

# Phase 5 — Automation & Operations

Toto je produkční foundation pro bezpečné automatické spouštění již existujícího paper-trading runtime z Phase 4.

Neimplementuj Phase 6, nové strategie, live trading ani frontend.

Phase 5 musí být dotažena end-to-end, ne jako scaffold nebo pseudokód.

---

# 0. Výchozí stav

Phase 4 byla dokončena a prošla samostatným Audit Gate.

Poslední známý stav:

```text
Phase 4 COMPLETE
Phase 4 Audit Gate PASS WITH FIXES
PostgreSQL integration PASS
```

Nepředpokládej ale konkrétní HEAD.

Aktuální `main` je jediný zdroj pravdy.

---

# 1. Authoritative sources

Nejdříve přečti:

* `/AGENTS.md`
* `/CODEX_MASTER_PROMPT.md`
* `/README.md`
* `/docs/architecture.md`
* `/docs/implementation-plan.md`
* `/docs/database.md`
* `/docs/risk-management.md`
* `/docs/paper-trading.md`
* `/docs/operations.md`
* `/docs/live-trading-safety.md`
* `/docs/phase4-audit.md`
* `/backend/src/quantlab/domain.py`
* `/backend/src/quantlab/config.py`
* `/backend/src/quantlab/phase4.py`
* `/backend/src/quantlab/api.py`
* `/backend/src/quantlab/persistence.py`
* celý Alembic strom
* všechny relevantní testy
* `.github/workflows/ci.yml`
* `Makefile`

Před změnami zjisti:

```bash
git status
git rev-parse HEAD
git branch --show-current
```

Working tree musí být čistý.

---

# 2. Baseline verification

Před implementací spusť maximum dostupného:

```bash
cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
uv run pytest -q
```

Pokud je něco blokováno prostředím, označ:

```text
BLOCKED BY ENVIRONMENT
```

Nezaměňuj environmentální chybu za PASS.

---

# 3. Hlavní cíl Phase 5

Po dokončení musí systém umět:

```text
persistent scheduled jobs
→ worker claims due work
→ executes safe paper-trading operation
→ records attempt/result
→ retries transient failures
→ survives process restart
→ prevents duplicate execution
→ reconciles after failure
→ exposes operational health/status
```

Celé řešení musí být:

```text
persistent
idempotent
restart-safe
concurrency-safe
observable
fail-closed
PostgreSQL-first
paper-only
```

---

# 4. Scope Phase 5

Implementuj minimálně:

## Phase 5.1

Persistent Job & Schedule Domain

## Phase 5.2

PostgreSQL-safe Worker & Leasing

## Phase 5.3

Automated Paper Trading Scheduler

## Phase 5.4

Retry, Recovery & Failure Handling

## Phase 5.5

Operational Health & Observability Foundation

## Phase 5.6

API & Operator Controls

## Phase 5.7

CI, PostgreSQL E2E & Documentation

Nezastavuj se mezi podfázemi.

Dokonči celý Phase 5 scope.

---

# 5. Paper-only invariant

Absolutní invariant:

```text
paper only
no live broker
no live order execution
```

Automation nesmí umožnit změnit execution mode.

Scheduler ani worker nesmí přijímat:

```text
broker=live
live=true
mode=live
```

nebo ekvivalent.

Phase 5 pouze automatizuje bezpečný Phase 4 paper runtime.

---

# 6. Domain model

Přidej jasné typované modely minimálně pro:

```text
ScheduledJob
JobSchedule
JobRun
JobAttempt
WorkerLease
WorkerHeartbeat
OperationalHealth
```

Případně vhodnější názvy podle existující architektury.

---

# 7. Job type

Podporuj explicitní job typy alespoň:

```text
RUN_PAPER_CYCLE
RUN_RECONCILIATION
```

Volitelně:

```text
HEALTH_CHECK
```

pokud to architektura smysluplně potřebuje.

Nepoužívej libovolný string, který mapuje na dynamické importy nebo arbitrary execution.

Použij enum / allowlist.

---

# 8. Scheduled job model

Persistentní job musí obsahovat minimálně:

```text
id
job_type
account_id
strategy_id / strategy reference podle Phase 4
enabled
schedule definition
timezone / canonical time basis
next_run_at
last_run_at
created_at
updated_at
correlation metadata
configuration payload pokud nutné
```

Config payload musí být validovaný a bezpečný.

---

# 9. Job schedule

Implementuj bezpečný scheduling model.

Preferuj jednoduchý, auditovatelný rozsah před plnou cron platformou.

Podpor minimálně:

```text
interval schedule
daily fixed-time schedule
```

Pokud použiješ cron:

* musí být bezpečně validovaný;
* nepřidávej těžkou dependency bez důvodu;
* časové zóny musí být explicitní;
* DST musí mít definované chování.

---

# 10. Time semantics

Interní persistence musí používat UTC.

Schedule může mít explicitní timezone, ale:

```text
stored timestamps = UTC
```

Musí být jednoznačně definováno:

* co se děje při DST spring-forward;
* co se děje při repeated hour;
* missed run;
* restart workeru po naplánovaném času.

---

# 11. Misfire policy

Definuj explicitní policy pro opožděný job.

Například:

```text
RUN_ONCE_IF_MISSED
SKIP_IF_TOO_OLD
```

Musí existovat maximální:

```text
misfire_grace_period
```

Worker nesmí po dlouhém výpadku bez omezení spustit stovky historických paper cycles.

---

# 12. Persistent job runs

Každé plánované execution musí vytvořit persistentní:

```text
JobRun
```

Minimální stavy:

```text
PENDING
CLAIMED
RUNNING
SUCCEEDED
FAILED
RETRY_SCHEDULED
CANCELLED
DEAD_LETTER
```

Pokud zvolíš menší množinu, musí pokrýt stejné semantics.

---

# 13. JobRun identity

Každý logical scheduled execution musí mít deterministickou identitu.

Například z:

```text
scheduled_job_id
scheduled_for
```

DB musí zabránit dvěma JobRun záznamům pro tentýž logical occurrence.

Použij unique constraint.

---

# 14. Job attempts

Odděl:

```text
logical JobRun
```

od:

```text
individual attempts
```

Retry nesmí vytvořit nový logical job.

Měl by vzniknout nový attempt stejného JobRun.

---

# 15. Attempt model

Attempt musí obsahovat:

```text
attempt_number
started_at
finished_at
worker_id
status
error_type
error_message
retryable
correlation_id
```

Neloguj secrets.

---

# 16. Worker architecture

Implementuj skutečný worker runtime.

Nemusí to být Celery/Redis.

Preferuj jednoduchý PostgreSQL-backed worker, pokud je dostatečný.

Nevkládej Redis pouze proto, že „scheduler ho často používá“.

---

# 17. Worker loop

Worker musí dělat přibližně:

```text
poll due jobs
→ create/find logical JobRun
→ atomically claim
→ create attempt
→ execute
→ persist result
→ compute next schedule
→ release
```

Polling interval musí být konfigurovatelný.

---

# 18. PostgreSQL-safe claiming

Dva workers nesmí vykonat stejný JobRun.

Použij skutečný DB concurrency mechanismus.

Preferované možnosti:

```text
SELECT ... FOR UPDATE SKIP LOCKED
```

nebo ekvivalentní PostgreSQL-safe leasing.

Pouhý Python lock je nepřijatelný.

---

# 19. Leasing

Claim nesmí být navždy vlastněn mrtvým workerem.

Implementuj lease:

```text
lease_owner
lease_acquired_at
lease_expires_at
```

nebo ekvivalent.

---

# 20. Lease expiry

Pokud worker zemře:

```text
RUNNING + expired lease
```

musí být bezpečně recoverable.

Nový worker může execution převzít podle definované recovery policy.

---

# 21. Lease renewal / heartbeat

Pro delší operace implementuj heartbeat nebo lease renewal.

Heartbeat interval musí být kratší než lease timeout.

Nesmí vzniknout situace:

```text
worker still executing
+
lease expires
+
second worker executes same job
```

---

# 22. Worker identity

Každý worker musí mít stabilní runtime identifier.

Například:

```text
hostname + process id + generated instance id
```

Worker ID se ukládá do attempt/lease metadata.

---

# 23. Graceful shutdown

Worker musí reagovat na:

```text
SIGTERM
SIGINT
```

a:

* přestat claimovat novou práci;
* umožnit aktuálnímu jobu bezpečně skončit, pokud je to možné;
* nebo zanechat stav recoverable;
* neoznačit neprovedenou práci za SUCCEEDED.

---

# 24. Worker crash model

Explicitně řeš:

```text
crash before execution
crash during execution
crash after economic commit
crash before job-run success commit
```

Phase 4 idempotency musí být použita, nikoli obcházena.

---

# 25. Phase 4 integration

`RUN_PAPER_CYCLE` musí volat existující:

```text
TradingCycleService
```

nebo autoritativní Phase 4 orchestration entrypoint.

Nesmí duplikovat:

* risk logic;
* broker logic;
* reconciliation;
* accounting.

---

# 26. Automated cycle identity

Scheduler musí používat stabilní logical cycle inputs.

Opakovaný execution stejného JobRun po crashi musí vytvořit stejný logical Phase 4 cycle.

Nikdy:

```text
retry
→ nový decision time
→ nový cycle
→ duplicate economic action
```

---

# 27. Decision time semantics

Automatický cycle musí mít stabilně odvozený:

```text
decision_time
```

z logical scheduled execution / market data snapshot.

Retry nesmí decision time automaticky měnit.

---

# 28. Dataset/snapshot semantics

Stejný JobRun retry musí používat stejnou dataset/snapshot identitu.

Pokud původní snapshot již není dostupný:

```text
fail closed
```

ne tiše použít nový dataset.

---

# 29. Reconciliation job

`RUN_RECONCILIATION` musí volat existující autoritativní:

```text
ReconciliationService
```

a respektovat Phase 4 HALT policy.

---

# 30. Reconciliation schedule

Podpor explicitní pravidelnou reconciliation.

Například:

```text
after trading cycle
+
periodic scheduled reconciliation
```

Nepřidávej redundantní economic action.

---

# 31. Post-cycle reconciliation

Pokud Phase 4 již dělá reconciliation na konci každého cycle:

automatizace ji nesmí nebezpečně obejít ani dvojitě modifikovat stav.

Periodická reconciliation je nezávislý safety check.

---

# 32. Retry classification

Chyby musí být rozděleny minimálně na:

```text
retryable
non-retryable
```

---

# 33. Retryable examples

Například:

```text
temporary DB connection issue
transient provider error
worker interruption
temporary lock conflict
```

Pouze pokud to odpovídá skutečnému kódu.

---

# 34. Non-retryable examples

Například:

```text
invalid configuration
invalid market data
risk rejection
HALTED state
forged/mismatched identity
permanent invariant violation
```

Risk rejection není technická chyba k nekonečnému retry.

---

# 35. Retry policy

Implementuj:

```text
max_attempts
base_backoff
max_backoff
```

Preferuj bounded exponential backoff.

Například:

```text
delay = min(base * 2^(attempt-1), max)
```

Můžeš přidat deterministický jitter, ale není povinný.

---

# 36. Retry safety

Retry musí vždy nejprve lookupnout stav předchozí ekonomické operace.

Nikdy:

```text
exception
→ slepě submit again
```

---

# 37. Dead-letter state

Po vyčerpání retry:

```text
DEAD_LETTER
```

nebo ekvivalentní persistentní terminal stav.

Musí být viditelný přes API/operations.

---

# 38. Manual retry

Operator musí mít možnost bezpečně retrynout failed/dead-letter job.

Manual retry:

* nevytvoří nový logical occurrence;
* vytvoří další attempt;
* zachová original scheduled identity;
* je auditovaný.

---

# 39. Manual trigger

Podpor manuální spuštění jobu.

Musí vytvořit explicitní logical occurrence s vlastní identitou.

Nesmí kolidovat se scheduled occurrence.

---

# 40. Idempotent manual trigger

Pokud API používá idempotency key:

preferuj explicitní:

```text
idempotency_key
```

a DB unique constraint.

Opakovaný stejný request nesmí spustit dva joby.

---

# 41. Scheduler concurrency

Dva scheduler procesy mohou běžet zároveň.

Musí platit:

```text
one schedule occurrence
→ one JobRun
```

Důkaz musí být PostgreSQL testem.

---

# 42. Worker concurrency

Více workerů může běžet zároveň.

Musí platit:

```text
one JobRun
→ at most one active execution owner
```

---

# 43. Cross-job account safety

Dva různé scheduled jobs nad stejným paper accountem mohou být due zároveň.

Zvaž:

```text
RUN_PAPER_CYCLE A
RUN_PAPER_CYCLE B
```

Pokud by mohly vytvořit souběžné ekonomické akce nad stejným accountem, serializuj je vhodným DB lockem nebo account-level execution lockem.

Nespoléhej jen na scheduler job ID.

---

# 44. Trading cycle mutual exclusion

Pro stejný account musí být definováno, zda mohou běžet dva paper cycles současně.

Preferuj:

```text
one active economic paper cycle per account
```

pokud není silný důvod jinak.

---

# 45. Reconciliation mutual exclusion

Reconciliation nesmí číst/přepisovat nekonzistentní mezistav během kritické fill transakce.

Použij vhodné transaction semantics.

---

# 46. Schedule enable / disable

Operator musí umět:

```text
enable
disable
```

schedule.

Disable:

* zabrání novým runům;
* nesmí automaticky rušit už běžící ekonomickou transakci.

---

# 47. Pause / halt distinction

Rozliš:

```text
scheduler disabled
```

od:

```text
paper account HALTED
```

Scheduler disable je operational control.

HALTED je risk safety state.

Nikdy je nemíchej.

---

# 48. HALTED account scheduling

Pokud account je HALTED:

scheduler nesmí opakovaně generovat risk-increasing paper cycles.

Může:

* vytvořit skipped/block result;
* spustit reconciliation;
* případně risk-reducing flow podle Phase 4 policy.

Chování musí být explicitní.

---

# 49. Market-session awareness

Automatický paper cycle nesmí slepě běžet mimo relevantní market session, pokud strategie/job vyžaduje market session.

Použij existující calendar abstractions, pokud jsou dostupné.

Nevytvářej v Phase 5 kompletní nový exchange-calendar subsystem.

---

# 50. Data readiness

Před automatickým paper cycle ověř:

```text
data available
latest bar valid
not stale
snapshot stable
```

Phase 4 risk/data validation musí zůstat autoritativní.

Phase 5 může přidat operational readiness precheck.

---

# 51. Duplicate stale scheduling

Pokud 5 polling iterací vidí tentýž due job:

nesmí vzniknout 5 JobRuns.

DB identity musí zaručit jeden.

---

# 52. Next-run computation

Výpočet:

```text
next_run_at
```

musí být deterministický.

Nesmí být založen pouze na:

```text
now + interval
```

pokud by tím po zpoždění docházelo k driftu.

Preferuj výpočet z předchozí scheduled occurrence.

---

# 53. Clock drift

Audituj rozdíl mezi:

```text
database time
application time
```

Pro concurrency-sensitive expiry preferuj PostgreSQL server time, pokud je praktické.

---

# 54. Persistence model

Přidej SQLAlchemy modely podle návrhu.

Minimálně tabulky typu:

```text
scheduled_jobs
job_runs
job_attempts
worker_instances / worker_leases
```

Můžeš model konsolidovat, pokud zachová stejné guarantees.

---

# 55. DB constraints

Přidej:

* unique logical occurrence;
* positive attempt number;
* terminal-state consistency;
* valid lease expiry;
* non-negative retry count;
* valid schedule fields.

Použij DB constraint tam, kde chrání kritický invariant.

---

# 56. Foreign keys

JobRun musí odkazovat na ScheduledJob.

Attempt musí odkazovat na JobRun.

Account refs musí být skutečné FK, pokud to aktuální schema umožňuje.

Cascade policy audituj opatrně.

Historical execution records se nemají náhodně mazat.

---

# 57. Indexes

Přidej indexy minimálně pro:

```text
enabled + next_run_at
job_run status
lease expiry
scheduled_job_id + scheduled_for
worker_id
created_at
correlation_id
```

---

# 58. Alembic

Přidej samostatnou Phase 5 revision.

Neměň staré migrace bez absolutní nutnosti.

Migration chain:

```text
Phase 3
→ Phase 4
→ Phase 4 audit
→ Phase 5
```

---

# 59. Migration testing

Ověř:

```text
fresh DB → head
Phase4-audit head → Phase5 head
downgrade Phase5 → previous head
re-upgrade
```

Bez ztráty Phase 4 dat.

---

# 60. Job execution transaction model

Dokumentuj, co je v jedné transakci a co ne.

Nemusí celý TradingCycle běžet v jedné obří transakci.

Ale:

```text
claim
attempt creation
status transitions
```

musí být crash-safe.

---

# 61. Status transitions

Implementuj explicitní povolené přechody.

Například:

```text
PENDING → CLAIMED
CLAIMED → RUNNING
RUNNING → SUCCEEDED
RUNNING → RETRY_SCHEDULED
RUNNING → FAILED/DEAD_LETTER
```

Nesmí být libovolný update enumu.

---

# 62. Terminal state protection

`SUCCEEDED` JobRun nesmí být znovu vykonán.

Retry endpoint musí failnout nebo idempotentně vrátit existující stav.

---

# 63. Stuck job recovery

Přidej recovery service:

```text
find expired leases
→ classify
→ safely requeue or mark failed
```

Musí být idempotentní.

---

# 64. Recovery after economic commit

Nejdůležitější scénář:

```text
Phase 4 paper cycle commits economic action
→ worker dies
→ JobRun remains RUNNING
```

Recovery:

```text
rerun same logical Phase4 cycle identity
→ Phase4 returns existing completed cycle
→ JobRun becomes SUCCEEDED
→ no duplicate order/fill
```

Přidej explicitní regresní test.

---

# 65. Recovery before economic action

Pokud worker zemře před Phase 4 execution:

nový worker může bezpečně pokračovat.

---

# 66. Reconciliation after uncertain failure

Pokud worker nedokáže určit, zda economic execution commitnul:

preferuj:

```text
lookup Phase4 cycle
+
reconciliation
```

před retry ekonomické operace.

---

# 67. Heartbeat persistence

Worker heartbeat musí být persistentní, aby API mohlo ukázat:

```text
alive
stale
dead/unknown
```

Není nutné implementovat distributed consensus.

---

# 68. Worker health

Definuj:

```text
healthy
degraded
unhealthy
```

podle:

* DB connectivity;
* recent heartbeat;
* scheduler loop;
* stale leases;
* dead-letter backlog;
* reconciliation state;
* HALTED accounty.

---

# 69. Application health endpoints

Přidej:

```text
GET /health/live
GET /health/ready
```

Semantics:

### live

Proces běží.

### ready

Proces je schopen bezpečně obsluhovat požadavky / runtime.

Ready musí minimálně ověřit DB connectivity.

---

# 70. Worker status endpoint

Přidej například:

```text
GET /operations/workers
```

nebo ekvivalent.

Vrací:

* worker ID;
* last heartbeat;
* state;
* active run.

---

# 71. Job API

Přidej minimálně:

```text
GET /automation/jobs
GET /automation/jobs/{id}
POST /automation/jobs
PATCH /automation/jobs/{id}
POST /automation/jobs/{id}/enable
POST /automation/jobs/{id}/disable
```

Pokud PATCH komplikuje invarianty, použij explicitní endpoints.

---

# 72. Run API

Přidej:

```text
GET /automation/runs
GET /automation/runs/{id}
POST /automation/jobs/{id}/run-now
POST /automation/runs/{id}/retry
```

Retry jen pro povolené stavy.

---

# 73. Pagination

Všechny kolekce:

```text
limit
offset
```

nebo cursor pagination.

Musí mít max limit.

---

# 74. Filtering

Užitečné filtry:

```text
status
job_type
account_id
scheduled_job_id
from/to timestamp
```

Neimplementuj komplexní query DSL.

---

# 75. API validation

Odmítni:

* invalid interval;
* schedule v minulosti, pokud není explicitně povolena;
* neznámý job_type;
* chybějící account;
* invalid timezone;
* negative retry config;
* absurdně krátký poll/schedule interval.

---

# 76. Minimum schedule interval

Zaveď bezpečnou minimální frekvenci.

Phase 5 nemá být high-frequency trading engine.

Například nepovoluj scheduler každou milisekundu.

Konkrétní minimum zdokumentuj.

---

# 77. Rate of economic cycles

Zaveď ochranu proti konfigurační chybě, která vytvoří příliš časté paper cycles.

Může být:

```text
minimum paper-cycle interval
```

na job/account úrovni.

---

# 78. Manual controls audit

Operator endpoints nesmí obejít:

```text
risk
HALT
reconciliation
Phase4 idempotency
```

Manual `run-now` spouští stejnou safe pipeline.

---

# 79. Authentication scope

Pokud projekt ještě nemá auth/RBAC:

neimplementuj celý auth systém.

Ale dokumentuj, že operator mutation endpoints nejsou production-ready bez Phase 9 security hardeningu.

Neoznačuj je za bezpečné pro veřejný internet.

---

# 80. Audit trail

Každá operational mutation musí být auditovaná:

```text
job created
job enabled
job disabled
manual run
manual retry
worker recovery
dead-letter
```

Použij Phase 4 audit infrastructure, pokud je vhodná, nebo nový operational audit model.

Nevytvářej chaoticky dva nekonzistentní audit systémy.

---

# 81. Correlation IDs

Propaguj correlation přes:

```text
ScheduledJob
JobRun
Attempt
TradingCycle
RiskDecision
Order
Fill
Reconciliation
```

Tak, aby bylo možné dohledat celý automatický execution chain.

---

# 82. Logging

Zaveď strukturované logování pro:

```text
worker started
worker stopped
job claimed
attempt started
attempt succeeded
attempt failed
retry scheduled
lease recovered
dead-letter
```

Neloguj credentials ani celé citlivé payloady.

---

# 83. Error model

Použij typované operational exceptions.

Například:

```text
RetryableJobError
PermanentJobError
LeaseLostError
```

ne string parsing výjimek.

---

# 84. Lease lost during execution

Pokud worker zjistí, že lease již nevlastní:

nesmí označit JobRun za SUCCEEDED bez ověření authoritative state.

Fail closed.

---

# 85. Split-brain simulation

Přidej test:

* worker A claimne job;
* lease expiruje;
* worker B job převezme;
* worker A se později pokusí commitnout status.

Worker A nesmí přepsat autoritativní stav B.

Použij lease generation/version token.

---

# 86. Fencing token

Preferuj:

```text
lease_version
```

nebo fencing token.

Každý claim inkrementuje token.

Status update musí obsahovat očekávaný token.

Starý worker nesmí commitnout po novém ownerovi.

---

# 87. Optimistic concurrency

Tam, kde není row lock vhodný, můžeš použít:

```text
version
```

optimistic locking.

Kritické status transitions musí být atomické.

---

# 88. Database outages

Testuj:

```text
DB exception during claim
DB exception after execution
DB exception while marking success
```

Worker loop nesmí spadnout navždy bez recovery.

---

# 89. Poll-loop resilience

Jedna vadná job konfigurace nesmí zabít celý worker.

Chybu izoluj na konkrétní JobRun.

---

# 90. Poison job protection

Non-retryable job nesmí být claimován donekonečna.

Po terminal state se polling query již nesmí vracet jako due.

---

# 91. Retry storm protection

Při mnoha chybách:

* bounded retries;
* backoff;
* no tight loop.

---

# 92. Dead-letter visibility

Health/operations API musí ukázat počet dead-letter runs.

---

# 93. Operational metrics foundation

Bez zavádění Prometheus stacku vytvoř alespoň interní agregace:

```text
runs succeeded
runs failed
runs retrying
dead letters
active workers
stale workers
average/recent duration
```

Pokud je vhodné, API endpoint:

```text
GET /operations/summary
```

---

# 94. Do not overbuild observability

Phase 5 není kompletní Phase 7.

Nedělej:

* Grafana stack;
* full metrics platform;
* distributed tracing backend.

Pouze foundation potřebnou pro bezpečný provoz workeru.

---

# 95. Health state persistence

Neukládej každý health poll jako nekonečný event.

Heartbeat může být mutable operational state.

Historical JobRun/Attempt musí zůstat auditovatelný.

---

# 96. Startup procedure

Při startu worker:

```text
verify DB
verify migration state if practical
register worker
recover expired leases
perform startup reconciliation where required
begin polling
```

---

# 97. Startup reconciliation

Phase 4 požaduje safe reconciliation.

Před spuštěním economic automation pro account:

pokud není účet reconciliation-safe:

```text
do not execute risk-increasing cycle
```

---

# 98. Shutdown procedure

Dokumentuj:

```text
stop claiming
finish or abandon safely
release/expire lease
persist heartbeat/state
```

---

# 99. CLI entrypoint

Přidej jednoduchý entrypoint pro worker.

Například:

```bash
uv run python -m quantlab.worker
```

nebo:

```bash
uv run quantlab-worker
```

Preferuj čistý package entrypoint.

---

# 100. One-shot mode

Pro CI/test/operations je užitečný:

```text
worker --once
```

který:

* claimne maximálně dostupnou jednu várku;
* zpracuje;
* skončí.

Pomáhá deterministickým testům.

---

# 101. Scheduler tick

Odděl schedule materialization od execution, pokud to pomůže testovatelnosti.

Například:

```text
SchedulerService.tick()
WorkerService.run_once()
```

Ale nepřekomplikuj architekturu.

---

# 102. Pure next-run calculation

Výpočet schedule udělej pokud možno jako čistou funkci s unit testy.

Testuj:

* interval;
* daily;
* timezone;
* DST;
* misfire.

---

# 103. Job config versioning

Pokud job config změníš:

historický JobRun musí zachovat snapshot relevantní konfigurace.

Retry starého JobRun nesmí automaticky použít novou config.

---

# 104. Config snapshot

Persistuj u JobRun snapshot nebo hash:

```text
job configuration
schedule occurrence
strategy/account refs
```

---

# 105. Schedule edit semantics

Když operator změní schedule:

* už vytvořené JobRuns se nemění;
* budoucí `next_run_at` se přepočítá;
* změna je auditovaná.

---

# 106. Disable while due

Pokud job je due a současně disabled:

nový run se nesmí vytvořit po effective disable.

Ověř race.

---

# 107. Manual run while disabled

Definuj policy.

Preferuj:

```text
disabled job cannot be manually run
```

pokud není explicitní operator override.

Jednodušší a fail-closed.

---

# 108. Account delete / unavailable

Pokud referenced account chybí nebo je invalid:

JobRun:

```text
non-retryable failure
```

ne worker crash.

---

# 109. Strategy unavailable

Stejně fail-closed.

Nepoužívej fallback strategii.

---

# 110. Paper account HALT escalation

Pokud automatický cycle způsobí failed reconciliation:

* účet zůstane HALTED;
* job run zaznamená bezpečnostní failure;
* další scheduled economic runs jsou blokovány;
* reconciliation job může pokračovat.

---

# 111. Operational global kill switch

Zvaž jednoduchý globální automation switch:

```text
AUTOMATION_ENABLED=false
```

Default musí být bezpečný.

Ale nesmí obcházet Phase 4 account HALT.

---

# 112. Safe defaults

Doporučené:

```text
AUTOMATION_ENABLED=false
WORKER_POLL_SECONDS=...
LEASE_TIMEOUT_SECONDS=...
MAX_JOB_ATTEMPTS=...
```

Automation se nemá samovolně rozběhnout jen importem aplikace.

---

# 113. No auto-start in API process

FastAPI import nesmí automaticky spouštět background worker thread.

Worker musí být samostatný explicitní proces.

To je důležité kvůli:

* více web workerům;
* duplicate execution;
* testům.

---

# 114. Deployment boundary

Architecture musí jasně rozlišovat:

```text
API process
Worker process
PostgreSQL
```

Scheduler/worker může být jeden proces pro Phase 5.

---

# 115. Docker

Pokud repository již má Compose:

doplň worker service pouze pokud je to přiměřené a nepřidá nepřiměřené komplikace.

PostgreSQL musí zůstat bezpečný.

---

# 116. Do not add Redis unless justified

Redis není povinný.

Pokud PostgreSQL `SKIP LOCKED` + leases dostatečně řeší worker queue, preferuj PostgreSQL.

---

# 117. Tests — minimum categories

Přidej:

```text
unit
repository
scheduler
worker
retry
lease
recovery
API
PostgreSQL concurrency
E2E
```

---

# 118. Unit tests

Minimálně:

* schedule calculation;
* retry backoff;
* misfire;
* terminal state transitions;
* config validation;
* timezone behavior.

---

# 119. Repository tests

Minimálně:

* unique logical JobRun;
* attempts;
* lease ownership;
* fencing token;
* expired lease recovery;
* DB constraints.

---

# 120. Scheduler idempotency test

Dvakrát:

```text
tick same due job
```

výsledek:

```text
one JobRun
```

---

# 121. Concurrent scheduler PostgreSQL test

Dva scheduler workers současně:

```text
same due schedule
```

výsledek:

```text
one logical JobRun
```

---

# 122. Concurrent worker PostgreSQL test

Dva workers:

```text
same pending JobRun
```

výsledek:

```text
one execution owner
```

---

# 123. Fencing-token test

Worker A:

```text
lease token 1
```

Lease expiruje.

Worker B:

```text
lease token 2
```

Worker A se pokusí dokončit.

Musí selhat.

---

# 124. Heartbeat test

Aktivní worker pravidelně prodlužuje lease.

Druhý worker job nepřevezme.

---

# 125. Crash-after-economic-commit test

Povinný.

Scénář:

```text
scheduled JobRun
→ Phase4 cycle commits fill
→ simulate worker crash before JobRun success
→ expire/recover lease
→ worker retry
```

Na konci:

```text
one trading cycle
one economic order
one set of fills
JobRun SUCCEEDED
```

---

# 126. Crash-before-economic-commit test

Recovery může bezpečně provést execution.

---

# 127. Retry classification test

Transient error:

```text
RETRY_SCHEDULED
```

Permanent config/data/risk error:

```text
FAILED/DEAD_LETTER
```

podle policy.

---

# 128. Max retry test

Po max attempts:

```text
DEAD_LETTER
```

a žádný další auto claim.

---

# 129. Backoff test

Ověř přesné:

```text
next_attempt_at
```

bez sleeping testů.

Použij controllable clock/time injection.

---

# 130. Clock abstraction

Pro testovatelnost zaveď malou clock abstraction nebo explicitní `now`.

Nevytvářej globální monkeypatch chaos.

---

# 131. Graceful shutdown test

Worker po stop request:

* neclaimne další job;
* současný stav zůstane validní.

---

# 132. Account-level concurrency test

Dva různé due paper jobs nad stejným accountem.

Ověř, že nevznikne unsafe parallel economic execution.

---

# 133. Reconciliation coexistence test

Economic cycle + reconciliation concurrency nesmí poškodit state.

---

# 134. Disabled job test

Disabled schedule:

```text
no JobRun
```

---

# 135. Schedule update test

Po změně:

```text
new future next_run_at
historical JobRun unchanged
```

---

# 136. Misfire test

Worker byl vypnutý déle než grace.

Ověř definovanou policy.

---

# 137. Restart persistence test

Nové service/worker objekty nad stejnou DB musí vidět:

```text
jobs
runs
attempts
leases
dead letters
next_run_at
```

---

# 138. API tests

Přidej testy pro:

* create job;
* invalid job;
* list;
* detail;
* enable/disable;
* run-now;
* retry;
* worker status;
* health;
* pagination.

---

# 139. API idempotency

Manual run s idempotency key:

dvakrát stejný request:

```text
one logical JobRun
```

Pokud idempotency key neimplementuješ, musí existovat jiný silný mechanismus.

---

# 140. API unsafe inputs

Odmítni:

```text
live broker
unknown job type
invalid timezone
negative interval
zero interval
negative retry
huge retry
invalid account
```

---

# 141. PostgreSQL E2E

Přidej skutečný test:

```text
create schedule
→ scheduler tick
→ worker claim
→ paper TradingCycle
→ risk
→ order
→ fill
→ reconciliation
→ JobRun success
→ retry tick
→ no duplicate
```

---

# 142. PostgreSQL worker recovery E2E

Povinný pokud prakticky možné:

```text
worker A claim
→ economic commit
→ simulated death
→ lease expiry
→ worker B recover
→ no duplicate trade
→ JobRun success
```

---

# 143. CI

Rozšiř `.github/workflows/ci.yml`.

Phase 5 testy musí skutečně běžet.

Quality:

```text
uv 0.12.3
uv lock --check
uv sync --locked --all-groups
ruff
mypy
```

PostgreSQL integration:

```text
alembic upgrade head
Phase3
Phase4
Phase5 tests
```

---

# 144. CI time

Nedělej testy založené na skutečném čekání několik sekund/minut.

Použij injected/fake clock.

CI musí zůstat rychlé a deterministické.

---

# 145. No dependency changes unless necessary

Preferuj standard library + SQLAlchemy/FastAPI již v projektu.

Pokud přidáš dependency:

* vysvětli proč;
* update `pyproject.toml`;
* vygeneruj `uv.lock`;
* `uv lock --check`;
* `uv sync --locked`;
* dodrž AGENTS policy.

Pokud registry není dostupná:

```text
BLOCKED BY ENVIRONMENT
```

a necommituj neověřený lockfile.

---

# 146. Documentation

Aktualizuj:

* `README.md`
* `docs/architecture.md`
* `docs/implementation-plan.md`
* `docs/operations.md`

Vytvoř případně:

```text
docs/automation.md
docs/worker-runtime.md
```

---

# 147. Operations documentation

Musí obsahovat:

```text
how to create a schedule
how to start worker
how to stop worker
how to inspect runs
how to retry
how to disable automation
how to recover expired work
how to interpret dead-letter
```

---

# 148. Worker command documentation

Například:

```bash
cd backend
uv run quantlab-worker
```

nebo skutečný implementovaný příkaz.

---

# 149. Runbook

Doplň jednoduchý runbook:

## Worker not heartbeating

## Job stuck RUNNING

## Repeated retry

## Dead-letter

## Account HALTED

## Reconciliation failed

## Database outage

---

# 150. Architecture documentation

Popiš:

```text
API
Scheduler
Worker
PostgreSQL
Phase4 TradingCycleService
```

a jejich hranice.

---

# 151. Implementation plan

Označ pouze skutečně dokončený scope.

Automation & Operations může být COMPLETE v Phase 5 scope.

Nevydávej:

```text
full observability
production deployment
security
```

za dokončené, pokud nejsou.

---

# 152. Security notes

Operator mutation API bez auth musí být jasně označeno jako:

```text
not internet-exposed production-ready
```

dokud nepřijde security phase.

---

# 153. No live-trading regression

Aktualizuj:

```text
docs/live-trading-safety.md
```

Potvrď, že worker/scheduler nemají live execution path.

---

# 154. Structured configuration

Doplň do central config:

```text
automation_enabled
worker_poll_interval
worker_lease_timeout
worker_heartbeat_interval
job_max_attempts
retry_base_delay
retry_max_delay
```

s bezpečnými defaulty.

---

# 155. Validation of config

Musí platit:

```text
heartbeat < lease_timeout
poll_interval > 0
max_attempts >= 1
retry delays valid
```

Invalid config failne startup.

---

# 156. Environment example

Aktualizuj `.env.example` pokud je relevantní.

Neobsahuj skutečné secrets.

---

# 157. Operational global disable

Pokud:

```text
AUTOMATION_ENABLED=false
```

worker nesmí vykonávat economic jobs.

Health může stále fungovat.

---

# 158. Scheduler side effects

Scheduler materialization sama nesmí provádět trading.

Pouze vytvoří logical work.

Execution dělá worker.

---

# 159. Separation of responsibilities

Preferuj rozdělení:

```text
AutomationRepository
SchedulerService
WorkerService
JobExecutor
```

ne jeden obří soubor/třídu.

Pokud Phase 4 již má velký `phase4.py`, Phase 5 implementaci dej do nového modulu, například:

```text
automation.py
worker.py
```

podle nejlepšího návrhu.

---

# 160. Avoid giant module

Nevkládej dalších 1500 řádků automatizace do `phase4.py`.

Phase 4 zůstává paper/risk runtime.

Phase 5 má vlastní vrstvu.

---

# 161. Dependency direction

Musí být:

```text
Automation
→ Phase4 application services
```

nikoli:

```text
Phase4
→ Worker/Scheduler
```

Core trading runtime nesmí záviset na automation runtime.

---

# 162. Determinism

Stejný logical scheduled occurrence musí mít stabilní:

```text
JobRun identity
Phase4 cycle inputs
correlation
```

---

# 163. Auditability

Z jednoho JobRun musí být možné dohledat:

```text
scheduled job
attempts
worker
trading cycle
risk decisions
orders
fills
reconciliation
```

---

# 164. Failure state semantics

Každá chyba musí skončit v definovaném persistentním stavu.

Zakázáno:

```text
exception logged
row remains indefinitely RUNNING
```

bez recovery mechanismu.

---

# 165. Repository startup safety

Application import nesmí:

* vytvářet schema;
* automaticky startovat worker;
* automaticky spouštět trading.

Schema pouze Alembic.

---

# 166. No silent fallback

Pokud PostgreSQL feature není dostupná na SQLite:

test adapter může mít omezenou semantiku, ale production runtime nesmí tiše degradovat concurrency guarantees.

Dokumentuj rozdíl.

---

# 167. SQLite scope

SQLite může sloužit pro rychlé unit/integration testy.

Produkční concurrency evidence:

```text
PostgreSQL only
```

---

# 168. Database server time

Pro lease expiry a due work preferuj atomické DB podmínky.

Například query:

```text
lease_expires_at < now()
```

Podle SQLAlchemy/PostgreSQL best practices.

---

# 169. Worker fairness

`SKIP LOCKED` query musí mít deterministické řazení:

```text
scheduled_for
created_at
id
```

aby nevznikala zbytečná starvation.

---

# 170. Batch size

Worker může claimovat omezený batch.

Config musí mít bezpečný max.

Default může být 1.

---

# 171. No unbounded queries

API ani worker nesmí načítat všechny historical runs bez limitu.

---

# 172. Data retention

Phase 5 nemusí implementovat cleanup.

Historical runs/attempts se nemažou automaticky.

Poznamenej future retention scope.

---

# 173. Manual cancellation

Pokud implementuješ cancel:

pouze PENDING/RETRY_SCHEDULED před execution.

Neimplementuj agresivní kill thread/process u právě běžícího trading cycle.

Pokud cancel není nutný pro DoD, může zůstat mimo scope.

---

# 174. Schedule deletion

Preferuj disable/soft lifecycle před hard delete, protože history FK musí zůstat auditovatelná.

---

# 175. Job immutability history

Job může být editovatelný pro budoucí occurrences, ale JobRun musí držet snapshot.

---

# 176. Corrupted job config

Pokud DB obsahuje nevalidní config:

worker ji musí označit jako permanent failure, ne spadnout celý loop.

---

# 177. Unknown job type in DB

Fail closed + audit.

---

# 178. Retry after HALT

Pokud job selže kvůli HALTED:

neprováděj exponential technical retry donekonečna.

Výsledek má být blocked/non-retryable podle jasné policy.

---

# 179. Retry after risk rejection

Risk rejection není infrastrukturová chyba.

Job může být SUCCEEDED_WITH_NO_ACTION / BLOCKED / non-retryable terminal stav podle zvoleného modelu.

Dokumentuj.

---

# 180. No-action result

Automatický cycle může legitimně skončit bez orderu:

* target already satisfied;
* risk reject;
* no data;
* HALTED.

Model musí odlišit:

```text
successful processing with no economic action
```

od:

```text
worker failure
```

---

# 181. Result summary

JobRun ulož stručný result:

```text
trading_cycle_id
reconciliation_id
outcome
no_action_reason
```

Ne duplikuj celý Phase 4 payload.

---

# 182. Failure reason taxonomy

Typované reasons, například:

```text
TRANSIENT_DATABASE
TRANSIENT_DATA_PROVIDER
INVALID_CONFIGURATION
ACCOUNT_HALTED
RISK_REJECTED
STALE_DATA
RECONCILIATION_FAILED
LEASE_LOST
```

podle skutečného návrhu.

---

# 183. Performance

Phase 5 není HFT.

Preferuj correctness a auditability před throughput.

---

# 184. Testing failure injection

Vytvoř kontrolované test hooks / injectable executor.

Nezaváděj produkční debug endpoints.

---

# 185. No monkeypatch-only proof

Klíčové E2E testy musí používat skutečné repository a Phase 4 services.

---

# 186. Postgres test isolation

Každý PostgreSQL test musí být izolovaný.

Nesmí záviset na pořadí testů.

---

# 187. Parallel CI safety

Testy nesmí používat globálně fixní IDs, které kolidují při paralelním běhu.

---

# 188. Migration indexes verification

PostgreSQL tests ověří existenci klíčových constraints/indexů, pokud je to praktické.

---

# 189. DB rollback

Simuluj IntegrityError během claim/attempt creation.

Session musí být po rollbacku použitelná.

---

# 190. Scheduler DB race

Concurrency test musí skutečně používat dvě independent sessions/connections.

---

# 191. Worker DB race

Totéž.

---

# 192. Cross-process conceptual safety

Nepoužívej proces-global singleton jako jediný lock.

---

# 193. E2E master scenario

Přidej jeden kompletní Phase 5 master test:

```text
create paper account
create automation job
schedule becomes due
scheduler materializes JobRun
worker claims
attempt starts
paper cycle runs
risk approves
order submits
fill commits
reconciliation succeeds
attempt succeeds
JobRun succeeds
next_run_at advances
second scheduler tick does not duplicate previous run
```

---

# 194. E2E recovery scenario

Druhý master test:

```text
create due job
worker claims
paper cycle commits
simulate crash
lease expires
new worker recovers
same cycle lookup
no duplicate order/fill
JobRun succeeds
```

---

# 195. E2E failure scenario

Například:

```text
reconciliation failure
→ paper account HALTED
→ JobRun records safety failure
→ later paper job does not execute risk-increasing action
```

---

# 196. Definition of Done — Domain

```text
[ ] ScheduledJob model
[ ] JobRun model
[ ] JobAttempt model
[ ] worker/lease state
[ ] typed states/reasons
[ ] config snapshot
```

---

# 197. Definition of Done — Scheduler

```text
[ ] persistent schedules
[ ] deterministic next_run_at
[ ] UTC semantics
[ ] interval scheduling
[ ] daily scheduling
[ ] misfire policy
[ ] idempotent materialization
[ ] PostgreSQL concurrent scheduler safe
```

---

# 198. Definition of Done — Worker

```text
[ ] explicit worker process
[ ] no FastAPI auto-start
[ ] PostgreSQL-safe claim
[ ] SKIP LOCKED or equivalent
[ ] lease timeout
[ ] heartbeat/renewal
[ ] fencing token
[ ] graceful shutdown
[ ] stuck lease recovery
```

---

# 199. Definition of Done — Execution

```text
[ ] RUN_PAPER_CYCLE
[ ] RUN_RECONCILIATION
[ ] reuses Phase4 services
[ ] stable Phase4 cycle identity on retry
[ ] no duplicate economic action
[ ] account HALT respected
```

---

# 200. Definition of Done — Retry

```text
[ ] retryable vs permanent errors
[ ] bounded attempts
[ ] exponential backoff
[ ] no retry storm
[ ] dead-letter
[ ] manual retry
[ ] crash-after-commit recovery
```

---

# 201. Definition of Done — Operations

```text
[ ] liveness
[ ] readiness
[ ] worker heartbeat/status
[ ] automation summary
[ ] enable/disable
[ ] manual run
[ ] retry controls
[ ] pagination/filtering
```

---

# 202. Definition of Done — Persistence

```text
[ ] Alembic Phase5 revision
[ ] unique logical occurrence
[ ] job/run/attempt FKs
[ ] lease constraints
[ ] indexes
[ ] fresh upgrade
[ ] Phase4→Phase5 upgrade
[ ] downgrade/re-upgrade
```

---

# 203. Definition of Done — Testing

```text
[ ] scheduler unit tests
[ ] worker unit tests
[ ] retry tests
[ ] lease tests
[ ] fencing test
[ ] restart persistence
[ ] PostgreSQL scheduler concurrency
[ ] PostgreSQL worker concurrency
[ ] crash-after-economic-commit E2E
[ ] master automation E2E
[ ] API tests
[ ] full regression suite
```

---

# 204. Definition of Done — Safety

```text
[ ] paper only
[ ] no live broker
[ ] no live flags
[ ] worker cannot bypass risk
[ ] worker cannot bypass HALT
[ ] reconciliation failure stops unsafe automation
[ ] disabled automation cannot execute economic job
```

---

# 205. Definition of Done — Documentation

```text
[ ] architecture updated
[ ] implementation plan updated
[ ] operations updated
[ ] automation docs
[ ] worker runbook
[ ] live safety updated
```

---

# 206. Full verification

Na konci:

```bash
cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
uv run pytest -q
```

Potom:

```bash
cd ..
git diff --check
git status
```

---

# 207. PostgreSQL verification

Pokud Docker dostupný:

```bash
docker compose up -d postgres
```

Spusť všechny relevantní PostgreSQL testy s:

```text
RUN_POSTGRES_TESTS=1
```

Minimálně:

```text
Phase 3
Phase 4
Phase 5
```

---

# 208. CI acceptance

Phase 5 není COMPLETE pouze proto, že SQLite testy projdou.

Musí být připravená tak, aby GitHub CI ověřilo:

```text
locked sync
PostgreSQL migration
PostgreSQL scheduler concurrency
PostgreSQL worker concurrency
Phase5 E2E
```

---

# 209. Internal review

Po implementaci proveď vlastní adversarial review:

```text
duplicate schedule
duplicate claim
lease expiry
split brain
retry after commit
account concurrent jobs
HALT bypass
worker crash
DB outage
misfire storm
```

Pokud najdeš problém, oprav jej před final reportem.

---

# 210. No placeholders

Zakázáno:

```text
TODO
stub
pass
NotImplemented
fake queue
in-memory production scheduler
```

pokud nejde o explicitní test double.

---

# 211. No Phase 6

Neimplementuj:

* nové strategies;
* market-data provider expansion;
* multi-asset research expansion;
* point-in-time universe;
* frontend;
* full observability stack;
* auth/RBAC;
* live trading.

---

# 212. Git

Po implementaci:

```bash
git status
git diff --check
git log --oneline -5
```

Working tree po commitu musí být clean.

---

# 213. Commit

Preferovaný commit:

```text
Dokončení Automation & Operations Foundation Phase 5
```

Pokud během implementace vzniknou oddělené významné opravy, můžeš použít několik logických commitů.

---

# 214. Final report

Finální odpověď musí mít sekce:

## Starting state

* starting HEAD
* branch
* initial working tree

## Phase 5 verdict

Použij pouze:

```text
COMPLETE
COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING
INCOMPLETE
```

## Implemented

Rozdělené podle:

```text
5.1
5.2
5.3
5.4
5.5
5.6
5.7
```

## Architecture

Popiš:

```text
Scheduler
Worker
PostgreSQL
Phase4 runtime
```

## Scheduling guarantees

* logical occurrence
* next run
* misfire
* timezone

## Worker guarantees

* claim
* lease
* heartbeat
* fencing
* graceful shutdown

## Idempotency

Popiš ochranu:

```text
job
run
attempt
Phase4 cycle
order/fill
```

## Concurrency

Zvlášť:

```text
scheduler concurrency
worker concurrency
same-account concurrency
PostgreSQL evidence
```

## Retry & recovery

* transient
* permanent
* backoff
* dead-letter
* crash before execution
* crash after economic commit

## Operations

* worker command
* health
* readiness
* status
* enable/disable
* manual run
* retry

## API

Seznam endpointů.

## Database

* nové tabulky
* constraints
* indexes
* migration

## Trading safety

Potvrď:

```text
paper only
no live broker
no live order path
```

## Tests

Rozděl:

```text
PASS
SKIPPED
BLOCKED BY ENVIRONMENT
FAILED
```

Přesné příkazy a výsledky.

## PostgreSQL tests

Přesný výsledek.

## Documentation

Seznam změn.

## Remaining risks

Pouze skutečný remaining scope.

## Phase 5 Audit readiness

Použij:

```text
READY FOR PHASE 5 AUDIT GATE
```

nebo:

```text
NOT READY FOR PHASE 5 AUDIT GATE
```

## Git

* commit SHA
* changed files
* status

---

# 215. Verdict rules

## COMPLETE

Pouze pokud:

* všechny Phase 5 requirements jsou implementovány;
* full locked suite prošla;
* PostgreSQL Phase 5 E2E/concurrency testy prošly.

## COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING

Pouze pokud:

* implementace je kompletní;
* dostupné testy prošly;
* zbývá pouze externí CI/Docker/environment verification.

## INCOMPLETE

Pokud zůstává funkční/correctness/safety scope neimplementovaný.

---

# 216. Start

Začni nyní.

Postup:

```text
1. inspect current main
2. baseline verification
3. map Phase4 integration boundaries
4. design persistent automation domain
5. implement migrations/models/repository
6. implement scheduler
7. implement worker + leasing/fencing
8. integrate Phase4 paper execution
9. implement retry/recovery
10. implement operational health
11. implement API/operator controls
12. add PostgreSQL concurrency tests
13. add crash/recovery E2E
14. update CI
15. update documentation
16. run full verification
17. adversarial second-pass review
18. fix findings
19. commit
20. final report
```

Nevracej pouze návrh nebo plán.

**Implementuj celou Phase 5 end-to-end.**
