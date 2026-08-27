# Operational Readiness Remediation — Stage C

## Stav a původní mezera

H1 vzniklo tím, že podporovaná B1 ingest mutation byla pouze ruční a production allowlist neměl provider-backed refresh job. M3 vzniklo tím, že Phase 5 uměla interval/daily wall-clock occurrence, ale neodvozovala signal close, next execution session ani readiness z `XNYSCalendar`.

Tato změna přidává explicitně opt-in `PREPARE_PAPER_SESSION` do stejné Phase 5 infrastruktury. Job pro APPROVED deployment vyžaduje ACTIVE monitoring, odvodí poslední completed signal session, její skutečný close a next valid open. Z experiment/deployment universe lineage vybere pouze membership s `known_at <= signal_close` a platným intervalem, načte canonical instruments a přes allowlisted Stooq adapter volá `PersistentMarketDataService`. Stooq nadále deklaruje `supports_actions=False`; H2 není skryt ani řešen.

## Bezpečnost, readiness a recovery

- Approval sama nic nespouští. Nutné jsou globální automation flag a auditované enable pro deployment.
- Refresh a execution jsou oddělené joby. Execution occurrence má identitu deployment + XNYS execution session a databázovou uniqueness; scheduler/worker restart proto nevytvoří druhý cyklus.
- Pending run nelze claimnout před `scheduled_for`, které je přesný `session_open` z kalendáře.
- Provider failure, chybějící completed bar a stale data jsou retryable; Phase 5 uplatní omezený exponential backoff a po vyčerpání dead-letter. Lineage/config chyby jsou permanentní.
- `Phase6PaperExecutionService` zůstává finální autoritou pro ACTIVE lifecycle, current observation timing, adjusted-close signal, raw-open execution a no-lookahead. Missed open se nepřepisuje; služba vždy znovu odvodí current completed signal a next session.
- Weekend, holiday, early close a DST nejsou v orchestru počítány ručně. Jedinou autoritou je auditovatelná identita `XNYSCalendar`.

## Evidence a CI

Audit lze spojit přes deployment, monitoring, scheduled job, session occurrence, JobRun, ingestion scope/observation revisions a následný trading cycle/orders/fills. CI má samostatný Stage C PostgreSQL krok pro opt-in, holiday, early-close, DST, pre-open a occurrence idempotenci; B1 a B2 kroky zůstaly beze změny.

## Neuzavřené body

Dokud neproběhne autoritativní PostgreSQL provider-to-fill acceptance a plný CI běh v cílovém prostředí, H1 ani M3 se v readiness auditu neoznačují za RESOLVED. Nadále zůstávají také H2, H3, M1, M2, M4 a LOW findings. Stage C nemění verdikt systému na READY.
