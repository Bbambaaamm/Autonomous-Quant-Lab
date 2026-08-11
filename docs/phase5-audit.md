# Phase 5 Audit Gate — 2026-08-11

Verdikt: **PASS WITH FIXES**. Phase 6 readiness: **READY FOR PHASE 6**.

Audit nalezl tři bezpečnostně relevantní mezery. Expirovaný vlastník mohl bez takeoveru obnovit
lease nebo zapsat terminal state (CRITICAL). Materializovaný run nesnapshotoval account, job type a
strategy, takže edit schedule mohl změnit význam retry (HIGH). Souběžné stejné `run-now` se
spoléhalo na constraint bez aplikačního rollback/recovery (MEDIUM). Všechny byly opraveny a dostaly
regresní testy; manual run navíc nyní fail-closed odmítá deaktivovaný job.

## Ověřené invarianty

- Scheduler používá deterministickou occurrence identitu, savepoint při konfliktu, logické
  intervaly bez driftu, explicitní grace boundary a dokumentovanou DST policy.
- PostgreSQL claim i scheduler používají row lock se `SKIP LOCKED`; claim, lease, fencing token a
  attempt vznikají v jedné transakci.
- Heartbeat, success i failure jsou fenced vlastníkem, tokenem, RUNNING stavem a neexpirovaným
  lease. Selhání heartbeat vlákna zabrání autoritativnímu completion write.
- Phase 4 ekonomická identita je deterministická. Recovery po ekonomickém commitu znovu načte
  jeden cycle/order/fill a account advisory lock serializuje trading s reconciliation.
- Automation vede pouze k Phase 4 službám, které znovu aplikují RiskEngine, persistentní HALT,
  paper broker accounting a reconciliation. Live broker ani live order cesta neexistuje.

## Transakční hranice a omezení důkazů

Materializace occurrence a posun schedule jsou jeden commit. Claim s attemptem je jeden commit.
Heartbeat je samostatný podmíněný update. Attempt+run completion nebo failure/retry/dead-letter
jsou vždy jeden commit. Phase 4 economic commit je záměrně oddělený; idempotentní Phase 4
identity uzavírá crash window.

SQLite testy dokazují determinismus a stavové invarianty, nikoli produkční souběh. PostgreSQL
acceptance testy a CI konfigurace poskytují souběžný důkaz. V auditním prostředí nebyl dostupný
Docker ani požadovaný `uv 0.12.3`, proto lokální opakování PostgreSQL a locked suite bylo
**BLOCKED BY ENVIRONMENT**, nikoli označeno jako PASS.

## Stav oblastí plánu

- Automation: COMPLETE
- Worker: COMPLETE
- Operations: COMPLETE
- Observability: PARTIAL
- Security: PARTIAL (auth/RBAC je mimo Phase 5)
- Deployment: NOT STARTED
