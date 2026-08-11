# Databáze a migrace

SQLite slouží pro rychlé unit testy. PostgreSQL 17 je produkční cíl. Business vrstva používá
portable SQLAlchemy typy a timezone-aware timestamps. Produkční schema vzniká příkazem:

Lokální Compose používá důvěryhodné připojení bez hesla pouze na loopback rozhraní. Jde výhradně
o development konfiguraci; produkční PostgreSQL musí vyžadovat credentials mimo repository.

```bash
docker compose up -d postgres
cd backend
uv sync --all-groups
uv run alembic -c ../alembic.ini upgrade head
```

Initial revision `20260810_01` vytvoří Phase 2 schema i Phase 3 registry. Migrace jsou
forward-first; downgrade initial revision nesmí být použit na databázi s auditní historií.
Aplikační runtime nevolá `create_all`; dostupný je pouze pojmenovaný test helper. CI čeká na
`pg_isready`, provede upgrade a spustí PostgreSQL testy. SQLite není důkaz PostgreSQL kompatibility.

Repository zatím neobsahuje `uv.lock`. CI proto nesmí používat `uv sync --locked`, který bez
lockfile končí ještě před instalací a nespustí žádný test. Po vygenerování a commitnutí lockfile
v prostředí s dostupným package indexem se CI vrátí k `uv sync --all-groups --locked`.
