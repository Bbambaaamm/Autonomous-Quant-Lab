# Phase 7 — Paper Performance Monitoring and Strategy Lifecycle

## Finální auditní opravy

Validní enrollment deterministicky přehrává immutable Phase 6 snapshot stejnou authoritative
evaluací jako `Phase6ExperimentRunner`. Před vytvořením monitoringu ověří přesnou shodu
vybraných parametrů a uložených OOS metrik. Baseline ukládá denní OOS returns, session
timestamps a equity curve; prázdná série je pro nový enrollment fail-closed. Provider correction
proto nemůže přepsat již hashovanou baseline evidenci.

Paper ledger má produkční `PaperCorporateActionService`. Kauzálně (`effective_at` i `known_at`)
a exactly-once aplikuje split a cash dividend bez orderu, fillu nebo syntetického P&L. Symbol
change zachovává canonical `instrument_id`; delisting otevřené pozice bez authoritative ceny
suspenduje monitoring s důvodem `DELISTING_UNSUPPORTED` a nevytváří syntetickou likvidaci.
Authoritative orchestration probíhá před performance valuation dokončené session.

Expected-vs-realized evaluace používá uložené skutečné OOS returns v horizon-aware
deterministickém block bootstrapu. WATCH, REVIEW_REQUIRED ani SUSPENDED nikdy nemění parametry,
nevytvářejí experiment/deployment a neprovádějí ekonomickou nebo live operaci.

## Verdict

Implementace je dokončena; auditní verdict závisí na locked quality a PostgreSQL CI gate.

## Goals a non-goals

Phase 7 dlouhodobě pozoruje jediný schválený paper deployment, oceňuje jej po dokončených XNYS
sessions, porovnává realizovanou řadu s immutable Phase 6 OOS evidencí a bezpečně řídí lifecycle.
Neprovádí retuning, nevytváří experiment/deployment, nepovoluje live trading a nemá ekonomickou
cestu k brokeru.

## Architecture a invariants

Tok je Phase 6 immutable research → explicitní `PAPER_CANDIDATE` → deployment → approval →
explicitní enrollment → `ACTIVE` monitoring → existující Phase 6/4 economic path →
reconciliation → performance snapshot → evaluation. Execution gate vyžaduje `APPROVED` i právě
jeden `ACTIVE` run. RiskEngine, HALTED a reconciliation gates zůstávají autoritativní.

## Persistence

Forward revision `20260824_01` přidává policy, expectation baseline, monitoring runs, performance
snapshots, evaluations, deployment-cycle lineage a corporate-action application evidence. FK,
unique a query indexy vynucují lineage; partial unique index dovoluje nejvýše jeden otevřený run
na účet. Identita a content hash vznikají z canonical JSON. Historické evidence se neaktualizují.

## Monitoring policy a baseline

Policy schema v1 přijímá pouze allowlist, bezpečné percentily, bootstrap parametry a povinné
fail-closed HALTED/reconciliation gates. Změna obsahu vytváří jiné ID. Enrollment pinuje exact
experiment, snapshot, strategy identity/version, parameters, code SHA, cost model a OOS metrics.
Phase 6 v1 neobsahuje denní OOS řadu; implementace ji proto explicitně označuje nedostupnou a
nikdy nevyrábí falešná data z agregátů. Provider corrections nemohou změnit uložený baseline.

## Performance snapshot semantics

Capture určí `latest_completed_session` skutečným XNYS kalendářem. Pozice oceňuje pouze přes
`ValidatedCurrentDataAccessor`; chybějící, future-known nebo neúspěšná ingestion selže uzavřeně.
Ukládá cash, marked equity, realized P&L, exposure, position count, returns, drawdown, turnover,
commission, skutečný reference-price slippage, orders/fills/rejects, reconciliation/trading state
a observation ID/revision/hash. První daily return je NULL. Cash-only a zero-trade session jsou
platné. Unique `(monitoring_id, session_date)` dává retry/concurrency idempotenci.

## Drift evaluation

Evaluation je verzovaná `paper-monitoring-v1`, immutable a přesně jednou pro snapshot/policy.
Před minimum sample vrací `INSUFFICIENT_DATA`. Pokud je denní baseline řada dostupná, používá
deterministický horizon-aware block bootstrap; jinak jasně uvede `BASELINE_SERIES_NOT_AVAILABLE`.
Verdicty jsou `HEALTHY`, `WATCH`, `REVIEW_REQUIRED`, `SUSPENDED`. HALTED, unsafe reconciliation
a hard drawdown vždy suspendují; soft drift nic ekonomického nemění.

## Lifecycle a fail-closed behavior

Stavy jsou `ACTIVE`, `PAUSED`, `SUSPENDED`, `RETIRED`. Pause/resume/retire jsou explicitní
operator actions. RETIRED je terminal. Resume znovu kontroluje approval, HALTED a reconciliation;
automatický resume neexistuje. Row lock brání stale transition a bezpečnější suspension vítězí.

## Corporate actions

Research ceny zachovávají kauzální adjusted-price semantics. Phase 7 schema rezervuje idempotentní
application evidence `(account_id, action_id)` pro paper ledger. Unsupported delisting nesmí být
syntetickým fill a musí skončit fail-closed. Monitoring nikdy neupravuje pozici ani nevolá broker.

## API, automation a concurrency

API nabízí policy create, enrollment, list/detail, performance/evaluations, deployment series,
summary a pause/resume/retire přes service layer a typed schemas. Arbitrary state, broker, live,
SQL nebo import nejsou vstupy. DB identities, unique constraints, partial index a row locks chrání
enrollment, capture, evaluation a transitions; retry po insertu vrací stejný logical record.
Monitoring scheduling je non-economic a service-level calendar gate zabrání weekend/holiday
duplicitě; stávající Phase 5 fencing zůstává beze změny.

## Paper-only boundary

Monitoring modul neimportuje broker, execution engine ani TradingCycleService. Pouze Phase 6
execution čte monitoring state a poté pokračuje jedinou existující risk-controlled paper cestou.
Neexistuje auto-tune, auto-experiment, auto-deployment ani live promotion.

## Tests a CI

`test_phase7.py` pokrývá policy safety a deterministic bootstrap. PostgreSQL soubory ověřují
migration/schema a partial index; Phase 6 PostgreSQL E2E zůstává autoritativní pro předcházející
research→paper tok. CI explicitně spouští Phase 7 unit i PostgreSQL soubory, locked Ruff/mypy a
fresh Alembic upgrade.

## Definition of Done a verdict rules

DoD vyžaduje forward schema, immutable evidence, calendar/PIT valuation, deterministic evaluation,
celý lifecycle, ACTIVE execution gate, paper-only safety, API, CI a dokumentaci. `COMPLETE` lze
vydat pouze po locked unit a PostgreSQL/fresh-Alembic PASS. Bez dostupného PostgreSQL/uv 0.12.3 je
verdikt `COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING`; funkční mezera znamená `INCOMPLETE`.
