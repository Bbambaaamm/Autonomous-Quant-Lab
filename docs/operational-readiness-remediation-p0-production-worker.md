# P0-B remediation: production worker runtime

## Blocker a změna topology

Původní production Compose spouštěl pouze `postgres → backend → frontend`. API proto mohlo být
ready a autonomní schedule enabled, přestože žádný proces nevolal `SchedulerService.tick()` ani
`WorkerService.execute_one()`. Konkrétní P0-B blocker je touto změnou **RESOLVED**: podporovaná
topology je `postgres → {backend, worker}` a `backend → frontend`; worker používá stejný backend
image a skutečný `/app/backend/.venv/bin/quantlab-worker` entrypoint.

## Safety a provozní důkaz

Worker fail-closed vyžaduje `APP_ENV=production`, PostgreSQL `DATABASE_URL`, platné existující
production secrets a `AUTOMATION_ENABLED=true`. Compose nastavuje globální automation shodně pro
API i worker, takže control plane nezobrazuje stav odporující běžícímu enginu. Neposkytuje mu live
credential, URL, mode ani flag. Globální engine pouze tickuje; obchodní deployment nadále potřebuje explicitní
autonomous opt-in, APPROVED stav, ACTIVE monitoring a všechny B1/B2/P0-A gates. Exekuce zůstává
Strategy → Portfolio → RiskEngine → ExecutionEngine → PersistentPaperBroker.

Každý loop zapisuje persistentní worker heartbeat a po skutečném scheduler ticku samostatný
`scheduler_heartbeat_at`. Read model odvozuje `HEALTHY/UNAVAILABLE/STALE/DISABLED` ze sdíleného
lease timeoutu; frontend tuto logiku pouze zobrazuje. Graceful stop se persistuje. Restart policy
je `unless-stopped`; unikátní occurrence, `SKIP LOCKED`, lease a fencing zůstávají databázové a
nový proces dostane čitelný prefix plus unikátní hostname/PID/UUID identitu.

Production smoke vytváří v izolované smoke DB bezpečný due reconciliation schedule a čeká na
scheduler materializaci, worker claim a terminální `SUCCEEDED` s jediným attemptem. Současně
ověřuje čerstvý worker/scheduler heartbeat, živý non-root worker proces a fail-closed disabled
automation. PostgreSQL P0-B acceptance navíc ověřuje restart bez duplicate completion. P0-A test
nadále vyžaduje pozdní start jako `NO_ACTION/MISSED_EXECUTION_OPEN` bez orderu či fillu.

Worker nemá triviální Docker healthcheck typu `ps`; Docker restartuje mrtvý proces a autoritativní
funkční readiness je DB-backed. Backend image proto zůstává pod stávající Trivy a SBOM gate a CI
navíc ověřuje přítomnost spustitelného `quantlab-worker`.

## Zbývající findings

Tato úzká remediation neřeší H2 corporate-actions provider, H3 runtime config identity, M1
eligibility ani M4 širší operator bootstrap. Neprohlašuje celý systém za READY.
