# Provoz paper tradingu

1. Spusťte PostgreSQL a `uv run alembic -c ../alembic.ini upgrade head`.
2. Před prvním cyklem a po incidentu volejte `POST /reconciliation/run`.
3. Cycle spouští `POST /trading/cycles/run-paper`; incident zastaví `POST /risk/halt`.
4. `/risk/resume` funguje jen po safe reconciliation.

Diagnostika používá risk events/decisions, orders, audit a reconciliation status. Po crashi spusťte stejný logical cycle: DB identity obnoví existující stav namísto nového obchodu. Testy: `uv run pytest -q`.

RUNNING cycle má databázový lease. Aktivní lease chrání před paralelním vlastníkem; po jeho
expiraci může retry cycle atomicky převzít a pokračovat idempotentně z persisted orders/fills.
