# Databáze a migrace

SQLite slouží pro rychlé unit testy. PostgreSQL 17 je produkční cíl. Business vrstva používá
portable SQLAlchemy typy a timezone-aware timestamps. Produkční schema vzniká příkazem:

Lokální Compose používá důvěryhodné připojení bez hesla pouze na loopback rozhraní. Jde výhradně
o development konfiguraci; produkční PostgreSQL musí vyžadovat credentials mimo repository.

```bash
docker compose up -d postgres
cd backend
uv sync --locked --all-groups
uv run alembic -c ../alembic.ini upgrade head
```

Initial revision `20260810_01` vytvoří Phase 2 schema i Phase 3 registry. Migrace jsou
forward-first; downgrade initial revision nesmí být použit na databázi s auditní historií.
Aplikační runtime nevolá `create_all`; dostupný je pouze pojmenovaný test helper. CI čeká na
`pg_isready`, provede upgrade a spustí PostgreSQL testy. SQLite není důkaz PostgreSQL kompatibility.

Závislosti jsou uzamčené v `backend/uv.lock`. Lokální instalace i všechny CI joby používají
`uv sync --locked --all-groups`; CI navíc spouští `uv lock --check`, takže nesoulad mezi
`pyproject.toml` a lockfilem selže bez automatické změny lockfilu.

Phase 4 audit přidal migraci `20260811_02`, která vynucuje kladné order/fill hodnoty, nezáporné filled/remaining/commission, zákaz overfillu a přesnou quantity bilanci. Migrace před vytvořením nebo odstraněním každého constraintu kontroluje skutečné databázové schema; podporuje tak jak upgrade starší Phase 4 databáze bez constraintů, tak fresh upgrade, kde je může vytvořit aktuální SQLAlchemy metadata už v předchozí revizi. Aplikační fill transakce zamyká na PostgreSQL řádek příkazu i účtu; SQLite zůstává vývojový backend, nikoli důkaz produkční souběžnosti.
