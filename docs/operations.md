# Provoz paper tradingu

Production-like PAPER provoz vyžaduje explicitní migration step, strong secrets, HTTPS ingress, oddělenou runtime DB roli a pravidelně ověřovaný backup/restore. Viz `security.md` a `production-deployment.md`. HALT dovoluje OPERATOR/ADMIN ve vlastním limiter bucketu; RESUME je ADMIN-only a nadále fail-closed vyžaduje reconciliation.


1. Spusťte PostgreSQL a `uv run alembic -c ../alembic.ini upgrade head`.
2. Před prvním cyklem a po incidentu volejte `POST /reconciliation/run`.
3. Cycle spouští `POST /trading/cycles/run-paper`; incident zastaví `POST /risk/halt`.
4. `/risk/resume` funguje jen po safe reconciliation.

Diagnostika používá risk events/decisions, orders, audit a reconciliation status. Po crashi spusťte stejný logical cycle: DB identity obnoví existující stav namísto nového obchodu. Testy: `uv run pytest -q`.

Backup skript ukládá vedle dumpu SHA-256 manifest s relativním názvem souboru. Dump a jeho
`.sha256` proto lze společně přesunout do off-site úložiště nebo recovery adresáře; restore vždy
přepočítá a porovná obsah skutečně předaného dumpu před prvním destruktivním databázovým krokem.

PostgreSQL Phase 5 acceptance suite je v `tests/test_phase5_postgres.py` a používá oddělené
enginy, sessions a souběžná vlákna. Ověřuje race schedulerů, race worker claimů včetně lease a
fencing tokenu, restart po ekonomickém commitu Phase 4 a account lock mezi paper cycle a
reconciliation. CI ji spouští v `integration-postgres` s `RUN_POSTGRES_TESTS=1` společně s Phase
3, Phase 4 a ostatními Phase 5 testy. Lokální běh vyžaduje migrované PostgreSQL v `DATABASE_URL`.

RUNNING cycle má databázový lease. Aktivní lease chrání před paralelním vlastníkem; po jeho
expiraci může retry cycle atomicky převzít a pokračovat idempotentně z persisted orders/fills.

## Automation worker a runbook

Po Alembic upgradu vytvořte schedule přes `POST /automation/jobs`, nastavte
`AUTOMATION_ENABLED=true` a spusťte `cd backend && uv run quantlab-worker`. SIGTERM/SIGINT zastaví
nové claimy. Job deaktivujte přes `POST /automation/jobs/{id}/disable`; běžící ekonomická
transakce se tím neruší. Runy a attempts kontrolujte přes `/automation/runs/{id}`, worker přes
`/operations/workers`, backlog přes `/operations/summary`; dead-letter ručně obnovte
`POST /automation/runs/{id}/retry`. Manual occurrence vyžaduje hlavičku `Idempotency-Key` na
`POST /automation/jobs/{id}/run-now`.
Deaktivovaný job nelze spustit ani přes `run-now`; globálně vypnutá automation neclaimuje a
nepovolí manual retry. Již běžící pokus se pouze bezpečně dokončí nebo nechá expirovat.

* **Worker neheartbeatuje / DB outage:** nevynucujte paralelní běh; obnovte DB, ověřte readiness
  a nechte nový worker převzít pouze expirovaný lease.
* **Stuck RUNNING:** ověřte Phase 4 cycle a reconciliation; fencing zabrání starému vlastníkovi
  dokončit převzatý run.
* **Repeated retry / dead-letter:** opravte klasifikovanou příčinu, zkontrolujte snapshot a pak
  použijte auditovaný manual retry. Neměňte occurrence identity.
* **HALTED účet / reconciliation failure:** nespouštějte risk-increasing cycle; proveďte safe
  reconciliation a explicitní Phase 4 resume.

Interval se počítá z předchozí occurrence bez driftu. Daily schedule používá explicitní IANA
timezone: při opakované hodině první výskyt, při neexistující hodině první normalizovaný čas po
skoku. `SKIP_IF_TOO_OLD` přeskočí occurrence za grace; `RUN_ONCE_IF_MISSED` materializuje nejvýše
jeden run a next time posune do budoucnosti. Historie se automaticky nemaže.

## Phase 6
Phase 6 je implementována jako provider → validace/immutable revisions → XNYS calendar/corporate actions → PIT universe → immutable snapshot → multi-asset target portfolio. Detailní invariants jsou v `docs/market-data.md` a `docs/strategy-research.md`. Žádná část nevytváří live execution path; automatický data refresh zatím není allowlistovaný job a refresh se provádí odděleně od trading cycle.

### Phase 6 operator workflow
Refresh se nepřidává do Phase 5 workeru, dokud není dokončen produkční calendar a PostgreSQL master E2E. Operátor používá pouze allowlistovaný provider, zkontroluje ingestion stav přes read API a až potom sestaví snapshot. Refresh nikdy nespouští trading. Paper accessor používá oddělený pohled poslední dokončené session a odmítne missing nebo neúspěšně ingestovaná data.

## Phase 6 paper deployment operations

Operátor vytváří `PENDING_REVIEW` deployment a schválení fail-closed ověří experiment, VALID snapshot, exact strategy version, universe, USD paper account, daily timeframe, code SHA, cost model a shodné parameters. Před cyklem se current data ověří proti poslední dokončené XNYS session; STARTED, FAILED nebo missing ingestion nejsou použitelné. Ekonomické provedení patří výhradně Phase 4 a `HALTED` account zastaví objednávku.

## Phase 6 provozní hranice
Session freshness vyhodnocuje `XNYSCalendar` nad `exchange-calendars` 4.13.2 / XNYS (identita `XNYS:exchange-calendars:4.13.2`), nikoli fixed-hour TTL nebo ruční holiday tabulka. Concurrent ingestion, correction, snapshot a experiment se opírají o PostgreSQL idempotenci. Deployment/approval je vždy ruční a current feed je oddělen od immutable research replay. Paper ekonomika smí projít jen Phase 4 službami; `HALTED` účet failne uzavřeně a live broker není podporován.

### Phase 6 research → paper audit boundary

Autoritativní workflow je `COMPLETED/RESEARCH_ONLY` experiment → explicitní
`Phase6EligibilityService.promote()` → `PAPER_CANDIDATE` → explicitní
`DeploymentService.create()` → `PENDING_REVIEW` → explicitní `approve()` → `APPROVED` →
`ValidatedCurrentDataAccessor` → `Phase6PaperExecutionService` → existující Phase 4
`TradingCycleService` / `ProductionRiskEngine` / `PersistentPaperBroker` → reconciliation.
Promotion ani deployment nevznikají automaticky a opakovaná promotion je idempotentní.

`PAPER_CANDIDATE` není automatický deployment a `APPROVED` neobchází risk engine ani stav
`HALTED`. Research snapshot slouží pouze jako immutable lineage; current execution feed pochází z
nejnovější dokončené XNYS session a přijímá jen nejnovější revizi z úspěšné ingestion. Runtime
rekonstruuje pouze přesnou allowlisted strategii, verzi, parametry, PIT universe a USD/XNYS/1d
scope. Live trading path nadále neexistuje.

## Phase 8 dashboard operations

Spouštějte API i dashboard na `127.0.0.1` podle `docs/dashboard.md`. Přehled ukazuje server UTC a
financial `as_of` odděleně. HALT/RESUME vyžaduje potvrzení a auditní důvod; unsafe reconciliation
vrátí 409 a účet zůstane HALTED. Account resume neresumuje Phase 7 monitoring. Data incident je
healthy až tehdy, když poslední úspěšná ingestion pokrývá authoritative dokončenou XNYS session.
