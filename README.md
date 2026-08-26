# Autonomous Quant Lab

Auditovatelná platforma pro kvantitativní research, bezpečné automatizované **paper trading** a provozní dohled. Projekt je po dokončení Phase 1–9 v deklarovaném scope. Neobsahuje live broker, live credentials ani cestu k odeslání reálného obchodu.

## Co projekt umí

- **Research a backtesting** — chronologický train/validation/OOS proces, next-open execution, FIFO accounting, náklady, slippage, benchmarky, robustness a eligibility.
- **Market data a point-in-time research** — canonical instruments, XNYS sessions, immutable observation revisions, corporate actions, PIT universe a content-addressed dataset snapshots.
- **Strategie a portfolio** — single- i multi-asset strategie vracejí target weights; research je oddělený od paper execution.
- **Paper trading a risk** — persistentní účet, MARKET/LIMIT orders, partial fills, FIFO pozice, portfolio risk, HALT/RESUME, reconciliation a audit trail.
- **Automatizace** — PostgreSQL-backed scheduler/worker, leases, fencing, retry, dead-letter a restart-safe execution.
- **Monitoring strategií** — immutable paper performance snapshots, OOS baseline, drift evaluation a lifecycle `ACTIVE/PAUSED/SUSPENDED/RETIRED`.
- **Operator dashboard** — Next.js control plane pro portfolio, strategie, research, risk, data health, automatizaci a audit.
- **Security a production hardening** — bearer auth, VIEWER/OPERATOR/ADMIN RBAC, secure dashboard session, rate limits, trusted hosts/CORS, least-privilege PostgreSQL, distroless non-root images, strict Trivy gates, backup/restore a production smoke test.

## Základní architektura

```text
market data
→ validation / immutable revisions
→ PIT universe / dataset snapshot
→ research / strategy evaluation
→ explicit PAPER_CANDIDATE promotion
→ explicit deployment approval
→ current-data validation
→ Strategy
→ Portfolio
→ RiskEngine
→ ExecutionEngine
→ PersistentPaperBroker
→ reconciliation
→ monitoring / operator dashboard
```

Každý ekonomický příkaz prochází přes `RiskEngine` a `ExecutionEngine`. Strategie nikdy nevolá broker přímo. Signál z close T se může realizovat nejdříve na raw open následující session. Research snapshoty a current paper feed jsou oddělené.

Podporovaný Phase 6 bootstrap je dostupný v autentizovaném ADMIN namespace `/operator`: instrument,
PIT universe/membership, provider ingest, validovaný snapshot, allowlisted experiment, promotion,
deployment create/approve a monitoring enrollment. Připraví pouze schválený PAPER deployment;
worker integration a automatický market-data refresh zatím nejsou součástí této cesty.

## Požadavky

- Python **3.12+**
- `uv` **0.12.3**
- Node.js **24+**
- npm **11.17.0**
- Docker / Docker Compose pro PostgreSQL a production-like provoz

Autoritativní lockfiles jsou `backend/uv.lock` a `frontend/package-lock.json`. Neupravují se ručně.

## Doporučené spuštění v GitHub Codespaces

Codespaces je doporučené vývojové prostředí. Startup používá PostgreSQL z `docker-compose.yml`, paper-only backend a produkční standalone dashboard se striktním CSP. Forwardovaný port `3000` nastavte v Codespaces jako **Private**; generátor odvodí konkrétní HTTPS URL bez wildcard origins.

### První spuštění

```bash
make codespaces-setup
```

Target nainstaluje uzamčené backend/frontend dependency, spustí PostgreSQL, vytvoří `.secrets/dev.env` pouze pokud ještě neexistuje, provede Alembic migrace a sestaví standalone dashboard. Generátor vypíše jednorázové heslo pro uživatele `operator`; bezpečně si je uložte. Soubor `.secrets/dev.env` ani heslo necommitujte.

Potom spusťte dva terminály (Makefile načte uloženou konfiguraci automaticky):

```bash
# terminál 1
make api

# terminál 2
make dashboard
```

API naslouchá na `127.0.0.1:8000`. Dashboard naslouchá na portu `3000` a otevře se přes privátní Codespaces HTTPS URL.

### Běžné spuštění po restartu Codespace

Není nutné znovu generovat credentials ani ručně exportovat proměnné:

```bash
docker compose up -d --wait postgres
make api        # terminál 1
make dashboard  # terminál 2
```

Po změně frontend kódu nejprve spusťte `make dashboard-build`. Po změně migrací spusťte `make codespaces-setup`; existující `.secrets/dev.env` zůstane zachován.

### Explicitní reset credentials

Nejprve pomocí `Ctrl+C` zastavte **API i dashboard**. Target odmítne reset, pokud port `8000` nebo `3000` stále naslouchá; tím zabrání souběhu procesů se starými a novými credentials. Potom vygenerujte credentials, znovu sestavte dashboard a oba procesy spusťte:

```bash
make codespaces-reset-credentials
make dashboard-build
make api        # terminál 1
make dashboard  # terminál 2
```

### Lokální fallback mimo Codespaces

Stejný postup funguje lokálně; generátor použije `http://localhost:3000` a allowlist `localhost,127.0.0.1`. `make dashboard` mimo Codespaces záměrně spustí vývojový Next.js server, protože produkční standalone validace správně vyžaduje HTTPS. Lokální `docker-compose.yml` vystavuje PostgreSQL pouze na loopback a používá trust auth výhradně pro development. `make codespaces-setup` existující `.secrets/dev.env` nikdy nepřepíše.

## Automatizovaný paper worker

Automatizace je defaultně vypnutá. Pro explicitní lokální spuštění workeru:

```bash
export AUTOMATION_ENABLED=true
cd backend
uv run quantlab-worker
```

Worker používá stejné `DATABASE_URL` a pouze existující paper-only service boundaries. Scheduler sám neobchoduje; materializuje persistentní runs, které worker bezpečně claimuje.

## Hlavní části dashboardu

- **Přehled** — stav účtu a systému
- **Paper** — portfolio, pozice, orders a performance
- **Strategie** — deploymenty a monitoring lifecycle
- **Research** — experimenty, metriky a lineage
- **Risk** — stav risk engine, HALT/RESUME
- **Data** — XNYS freshness a market-data health
- **Operations** — scheduled jobs, runs, dead letters a workers
- **Audit** — stránkovaná auditní evidence

## Kontroly

```bash
make check
make test
make frontend-check
make security-check
make frontend-security
make production-build
make production-smoke
```

CI na `main` navíc ověřuje PostgreSQL integration, locked dependencies, strict HIGH/CRITICAL Trivy scan obou production images, SBOM a production-like PAPER smoke test.

## Production-like PAPER deployment

```bash
make production-build
make production-up
```

Produkční postup vyžaduje explicitní PostgreSQL migration/runtime credentials, silné secrets, HTTPS ingress a `APP_ENV=production`. Detailní postup je v [`docs/production-deployment.md`](docs/production-deployment.md).

Backup a restore:

```bash
BACKUP=backups/quantlab.dump make db-backup
BACKUP=backups/quantlab.dump make db-restore
```

Restore má další fail-closed požadavky popsané v [`docs/production-deployment.md`](docs/production-deployment.md) a [`docs/operations.md`](docs/operations.md).

## Bezpečnostní hranice

Projekt je **PAPER-only**. Neexistuje live broker, live order endpoint, live credential flow ani feature flag umožňující live trading. Produkční runtime používá autentizaci a RBAC; browser nikdy nedostává backend bearer token. Runtime PostgreSQL role nemá DDL oprávnění.

Známá omezení po Phase 9:

- process-local rate limiter předpokládá jednu backend repliku;
- HTTPS ingress a automatizovaný off-site backup jsou odpovědností deploymentu;
- live trading není implementován a není součástí současného scope;
- další fáze po Phase 9 zatím nemá autoritativní specifikaci.

## Dokumentace

- [Aktuální projektový audit](docs/project-audit.md)
- [Architektura](docs/architecture.md)
- [Databáze a migrace](docs/database.md)
- [Market data](docs/market-data.md)
- [Strategy research](docs/strategy-research.md)
- [Paper trading](docs/paper-trading.md)
- [Risk management](docs/risk-management.md)
- [Automation](docs/automation.md)
- [Operator dashboard](docs/dashboard.md)
- [Security / threat model](docs/security.md)
- [Production-like deployment](docs/production-deployment.md)
- [Operations a recovery](docs/operations.md)
- [Aktuální implementation status](docs/implementation-plan.md)

Detailní Phase specifikace a completion evidence zůstávají v `docs/codex/` jako auditní historie.
