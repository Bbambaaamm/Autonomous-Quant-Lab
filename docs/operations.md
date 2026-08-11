# Provoz paper tradingu

1. Spusťte PostgreSQL a `uv run alembic -c ../alembic.ini upgrade head`.
2. Před prvním cyklem a po incidentu volejte `POST /reconciliation/run`.
3. Cycle spouští `POST /trading/cycles/run-paper`; incident zastaví `POST /risk/halt`.
4. `/risk/resume` funguje jen po safe reconciliation.

Diagnostika používá risk events/decisions, orders, audit a reconciliation status. Po crashi spusťte stejný logical cycle: DB identity obnoví existující stav namísto nového obchodu. Testy: `uv run pytest -q`.

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
