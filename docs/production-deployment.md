# Production-like PAPER deployment

1. `make generate-dev-secrets` použijte jen pro lokální credentials a bezpečně provisionujte
   ekvivalentní production secrets. Veřejný ingress musí ukončovat HTTPS.
2. Vytvořte oddělené PostgreSQL migration/runtime role; migration credential použijte pouze pro
   `alembic upgrade head`, runtime roli udělte jen SELECT/INSERT/UPDATE a potřebné sequences.
3. Nastavte `APP_ENV=production`, PostgreSQL `DATABASE_URL`, unikátní API tokeny, silný
   `SESSION_SECRET`, scrypt hash hesla, HTTPS `PUBLIC_BASE_URL` a explicitní hosts.
4. Spusťte explicitní migration job, pak `make production-up`. Frontend na loopbacku je jediný
   publikovaný port; backend a PostgreSQL nemají host port.
5. `GET /healthz` je liveness a `GET /readyz` ověřuje DB bez citlivých detailů.

Backup vytvoří `BACKUP=backups/name.dump make db-backup`. Restore vyžaduje jinou explicitní
`RESTORE_DATABASE_URL`, správný checksum a `RESTORE_CONFIRMATION=RESTORE_EPHEMERAL_DATABASE`.
Off-site přenos ani plánování backupu není implementováno; provozovatel je musí zajistit.

Nasazení je pouze PAPER. Neposkytuje live broker ani cestu k reálnému orderu.
