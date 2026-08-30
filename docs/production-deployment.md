# Production-like PAPER deployment

Produkční backend a frontend používají distroless `static` Debian 13 runtime, do kterého build
kopíruje jen aplikační interpreter a jeho dynamické knihovny. Runtime neobsahuje shell ani
systémový package manager. Diagnostiku a non-root kontroly proto provádějte přes aplikační Python
nebo Node runtime, nikoli pomocí `sh`, `id`, `apt` či `npm` uvnitř běžícího kontejneru.

1. `make generate-dev-secrets` použijte jen pro lokální credentials a bezpečně provisionujte
   ekvivalentní production secrets. Veřejný ingress musí ukončovat HTTPS.
2. Vytvořte oddělené PostgreSQL migration/runtime role; migration credential použijte pouze pro
   `alembic upgrade head`, runtime roli udělte jen SELECT/INSERT/UPDATE a potřebné sequences.
3. Nastavte `APP_ENV=production`, PostgreSQL `DATABASE_URL`, unikátní API tokeny, silný
   `SESSION_SECRET`, scrypt hash hesla, HTTPS `PUBLIC_BASE_URL` a explicitní hosts.
4. Spusťte explicitní migration job, pak `make production-up`. Compose automaticky spustí také
   samostatný `worker` příkazem `/app/backend/.venv/bin/quantlab-worker` a corporate-action
   ingest `alpaca-events` příkazem `/app/backend/.venv/bin/quantlab-alpaca-events`; oba používají hardened
   backend image a čekají na PostgreSQL healthcheck, nikoli na API. Frontend na loopbacku je jediný
   publikovaný port; backend, worker a PostgreSQL nemají host port.
   Compose nastavuje `AUTOMATION_ENABLED=true` shodně pro API i worker; jde pouze o globální engine
   a jednotlivé deploymenty nadále vyžadují samostatný explicitní autonomous opt-in.
   Pro Alpaca nastavte canonical credentials `ALPACA_KEY_ID`/`ALPACA_SECRET_KEY` a ponechte
   `ALPACA_FEED=iex` pro Basic/Paper účet. Ve Stooq režimu `alpaca-events` skončí s kódem 0 a
   politika `on-failure` jej znovu nespouští.
5. `GET /healthz` je liveness a `GET /readyz` ověřuje DB bez citlivých detailů.

Worker má `restart: unless-stopped`, read-only filesystem, non-root UID a pouze interní datovou
síť. `WORKER_ID_PREFIX` označuje deployment a hostname/PID/UUID zachovávají unikátní identitu po
restartu. Deklarovaná topology má jednu worker repliku; databázové occurrence, lease a fencing
zůstávají autoritou pro restart safety. Worker vypíná zděděný API HTTP healthcheck, protože
neposlouchá na HTTP portu, a záměrně jej nenahrazuje process-only kontrolou: funkční stav poskytuje
DB-backed operator read model z čerstvého heartbeat i scheduler heartbeat.

Backup vytvoří `BACKUP=backups/name.dump make db-backup`. Restore vyžaduje jinou explicitní
`RESTORE_DATABASE_URL`, správný checksum a `RESTORE_CONFIRMATION=RESTORE_EPHEMERAL_DATABASE`.
Off-site přenos ani plánování backupu není implementováno; provozovatel je musí zajistit.

Nasazení je pouze PAPER. Neposkytuje live broker ani cestu k reálnému orderu.
