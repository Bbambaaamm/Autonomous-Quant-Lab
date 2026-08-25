# Autonomous Quant Lab

## Phase 9 production-like PAPER security

Dashboard a API jsou autentizované. Lokální secrets vytvořte `make generate-dev-secrets`, načtěte
je ze `.secrets/dev.env` a nikdy je necommitujte. API role jsou VIEWER, OPERATOR (navíc HALT) a
ADMIN (RESUME a ostatní mutations). Browser backend token nikdy nedostane; Next.js volí
role-specific server credential. Produkční postup, health/readiness a recovery jsou popsány v
[`docs/production-deployment.md`](docs/production-deployment.md), threat model v
[`docs/security.md`](docs/security.md).

Kontroly: `make check`, `make test`, `make frontend-check`, `make security-check`,
`make frontend-security`, `make production-build` a `make production-smoke`. Backup používejte
pouze s explicitním `BACKUP=... make db-backup`. Platforma je PAPER-only a neobsahuje live path.

Phase 5 doplňuje PostgreSQL-backed plánování a worker pro bezpečnou automatizaci výhradně
paper runtime. Worker se spouští `cd backend && uv run quantlab-worker`; výchozí
`AUTOMATION_ENABLED=false` zabraňuje ekonomickému execution bez explicitního povolení.
Operator API nabízí `/automation/jobs`, `/automation/runs`, `/operations/workers` a oddělené
`/health/live` a `/health/ready`. Phase 9 chrání read i mutation API bearer autentizací a
backend-authoritative RBAC; veřejné zůstávají pouze minimální `/healthz` a `/readyz`.

Auditovatelná research a paper-trading platforma. Aktuální vertical slice načte fixture,
validuje data, vytvoří moving-average cíle, provede next-open backtest s náklady a slippage,
aplikuje risk limity, uloží běh a zobrazí jej ve FastAPI dashboardu.

## Lokální spuštění

```bash
cd backend
uv sync --locked --all-groups
uv run uvicorn quantlab.api:app --reload
```

Otevřete <http://127.0.0.1:8000>. Testy a kontroly: `make test`.

## Závislosti a lockfile

Projekt vyžaduje `uv 0.12.3`; verze je vynucena konfigurací backendu a stejně připnuta v CI.
Commitnutý `backend/uv.lock` je autoritativní. Pro běžnou instalaci bez změny závislostí použijte
pouze uzamčenou synchronizaci:

```bash
cd backend
uv sync --locked --all-groups
```

Při záměrné změně závislostí v `backend/pyproject.toml` regenerujte lockfile výhradně pomocí `uv`
a před commitem ověřte jeho konzistenci i úplnou instalaci:

```bash
cd backend
uv lock
uv lock --check
uv sync --locked --all-groups
```

Frontend používá Node 24 a přesně `npm 11.17.0`. Autoritativním lockfilem je
`frontend/package-lock.json`; smí jej generovat pouze skutečné npm. Pokud se dependency graph v
`package.json` nezměnil, lockfile se nesmí měnit. Po záměrné změně dependency graphu jej
regenerujte připnutým npm a proveďte úplnou kontrolu:

```bash
cd frontend
npm install --package-lock-only
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Pro běžnou práci slouží `make frontend-install`, `make frontend-check` a fail-closed
`make frontend-lock-check`. Poslední target ověří strukturu a přímé závislosti lockfilu,
regresní scénář root-only placeholderu a čistou reprodukovatelnost přes `npm ci`.

Pokud registry není dostupná, lockfile neměňte ani nevytvářejte minimální náhradu a stav
označte `BLOCKED BY ENVIRONMENT`. Bezpečná obnova je dostupná přes **Actions → Repair frontend
lockfile → Run workflow → vyberte `source_ref`**. `source_ref` musí být existující vzdálená větev
s verzí `package.json`, kterou je třeba opravit; workflow nikdy implicitně nepřejde na `main`.
Manuální workflow použije skutečné připnuté npm, provede všechny kontroly a vytvoří review branch
a pull request proti zadanému `source_ref`; pokud oprávnění PR nedovolí, poskytne ověřený lockfile
jako workflow artifact. npm a repository skripty běží pouze v read-only verification jobu. Oddělený
publish job získá write token až po úspěšném ověření, zkontroluje checksum artifactu i nezměněné SHA
`source_ref` a smí commitnout pouze `frontend/package-lock.json`. Nikdy nepushuje přímo do
`source_ref` ani do `main`.

## Bezpečnost

Projekt implementuje výhradně `PaperBroker`. Výchozí a demo režim je paper, bez možnosti
odeslat live obchod. Každý příkaz prochází RiskEngine přes ExecutionEngine.

## Phase 2: research foundation

Research vrstva obsahuje CSV a lokální Parquet provider, content-hash identity datasetu,
strukturované kontroly kvality, chronologický IS/validation/OOS split, walk-forward hranice,
deterministický parameter grid a identitu experimentu. Baseline strategie jsou moving average,
buy-and-hold a Donchian breakout. Nástroje zahrnují cost stress, seedované bootstrap Monte Carlo,
lokální stabilitu parametrů a rozhodnutí `REJECTED`, `RESEARCH_ONLY` nebo `PAPER_CANDIDATE`.

Výkonnostní vrstva počítá total return, CAGR, anualizovanou volatilitu, Sharpe, Sortino,
max drawdown a jeho délku, Calmar, win rate, profit factor, průměrný zisk/ztrátu, expectancy,
exposure, turnover, počet uzavřených obchodů, holding period, komise a slippage. Nedefinované
poměry vracejí `None`. Buy-and-hold benchmark je zarovnán na stejné bary. Parquet používá
`pyarrow`. Žádný internetový data provider ani live broker nebyl přidán.

Phase 2.6 doplňuje FIFO lot ledger pro scale-in/scale-out, splitovou úpravu množství i jednotkové
báze a idempotentní dividendy. Equity snapshot vždy ukládá cash, market value a jejich součet.
Cost stress se ověřuje plným opakovaným backtestem, protože vyšší náklady mohou změnit pozdější
whole-share sizing; slippage je ekonomicky obsažena ve fill ceně a samostatná hodnota slouží jen
pro audit.

Vývojová skupina obsahuje `httpx2`, který Starlette 1.6 preferenčně importuje pro `TestClient`.
Původní `httpx` byl odstraněn: aplikační ani testovací kód jej přímo nepoužívá a fallback
Starlette je zastaralá kompatibilní cesta. Synchronizace používá commitnutý lockfile.

## Phase 2.7: kompletní research use-case

`ResearchExperimentRunner` je jediný aplikační tok od validace a dataset hash přes obecný
`StrategyFactory`, neměnný `ParameterSpace`, train sweep, validation výběr a právě jednu OOS
evaluaci až po agregované OOS metriky, OOS benchmark, robustness, eligibility, SQLite snapshot
a report. Raw invalidní kombinace se neztrácejí. OOS foldy se nesmějí překrývat.

Research trade metriky a Monte Carlo používají autoritativní FIFO closed-trade ledger. Cost
stress znovu přehrává každý vybraný OOS fold s jeho zamčenou konfigurací; nejde o aritmetický
odhad nákladů.

## Phase 2.8: closure

**Phase 2 je COMPLETE v definovaném research-foundation scope.** Experiment má vedle úplného
neměnného reprodukčního snapshotu strukturované, dotazovatelné záznamy identity, OOS foldů a
jejich train/validation ParameterRunů i typovaných eligibility kontrol. Každá kontrola ukládá
status, pozorovanou hodnotu, práh a případný důvod; chybějící stabilitní sousedství se netváří jako
běžné selhání, ale jako `not_evaluated`. Konzistenční test porovnává celý persisted snapshot s JSON
reprezentací in-memory experimentu a ověřuje idempotenci i transakční rollback celé projekce.

## Phase 3: production research data platform

`DATABASE_URL` volí SQLite development adapter nebo PostgreSQL production adapter. Produkční
bootstrap probíhá výhradně přes Alembic; `create_all` je izolován v testovacím helperu.

```bash
docker compose up -d postgres
cd backend
uv sync --locked --all-groups
uv run alembic -c ../alembic.ini upgrade head
uv run pytest
```

Registry uchovává neměnnou identitu datasetu, verzi strategie, experiment, foldy, parameter runy,
eligibility a leaderboard metriky. API nabízí stránkované/filtrované experimenty, leaderboard a
comparison. Ranking je lexikografický: eligibility, kladné OOS, cost stress, stabilita, drawdown,
Sharpe a deterministické ID; není predikcí budoucí ziskovosti.


## Phase 4: production paper foundation

Persistentní účet, MARKET/LIMIT orders, partial fills, FIFO pozice, portfolio risk, idempotentní cycle, kill switch, reconciliation a audit jsou dostupné přes `/paper/account`, `/portfolio`, `/positions`, `/orders`, `/risk/*`, `/trading/cycles/*`, `/audit` a `/reconciliation/*`. Vše je pouze paper; live broker ani live order path neexistují.

## Phase 6 — market data a multi-asset research
Phase 6 přidává canonical instruments, XNYS sessions, immutable observation revisions, corporate actions, PIT universes a content-addressed dataset snapshots. Multi-asset strategie vracejí target weights do společného long-only USD portfolia; close T se plní nejdříve na raw open další session. Podrobnosti: [market data](docs/market-data.md) a [strategy research](docs/strategy-research.md). Runtime zůstává výhradně paper-only.

## Phase 6 production invariants

Phase 6 používá verzovaný auditovaný XNYS kalendář, včetně podporovaných historických mimořádných uzavření, early-close sessions a DST. Snapshoty jsou immutable manifesty konkrétních observation revisions a corporate actions; pozdější korekce vytváří nový snapshot a nemění replay starého. Ingestion, snapshot build a experiment run mají databázové exactly-once identity a PostgreSQL advisory transaction lock. Výběr parametrů končí validací a OOS se vyhodnotí právě jednou až poté.

Nasazení je pouze explicitně a ručně schvalované pro paper účet. Current-data accessor je session-aware a není research snapshot. Jediná ekonomická cesta zůstává Phase 4 `TradingCycleService → ProductionRiskEngine → PersistentPaperBroker`; stav `HALTED` selže uzavřeně. Live broker ani live execution mode neexistuje.

### Produkční burzovní kalendář
Produkční `XNYSCalendar` deleguje schedule na `exchange-calendars` 4.13.2 / XNYS; vlastní holiday ani exceptional-closure tabulka není autoritativní. Lineage ukládá identitu `XNYS:exchange-calendars:4.13.2`, aby změna verze kalendáře změnila identitu snapshotu. Immutable revisions a snapshoty zachovávají correction replay, PIT membership a kauzalitu corporate actions. Experimenty mají exactly-once identitu a OOS je izolované od selection. Deployment je pouze ruční; current feed není research snapshot. Ekonomická cesta zůstá Phase 4 paper-only a `HALTED` selže uzavřeně; live broker neexistuje.

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

## Phase 8 operator dashboard

Dashboard je skutečná Next.js aplikace v `frontend/`, nikoli původní inline demo. Kompletní
architektura a bezpečnostní semantics jsou v [docs/dashboard.md](docs/dashboard.md).

```bash
docker compose up -d postgres
cd backend && uv run alembic -c ../alembic.ini upgrade head
cd backend && uv run uvicorn quantlab.api:app --host 127.0.0.1 --port 8000
# volitelně AUTOMATION_ENABLED=true uv run quantlab-worker
cd frontend && npm ci && npm run dev
```

Frontend i API jsou defaultně dostupné pouze přes loopback. Trading mode je vždy PAPER; Phase 8
neobsahuje live broker, credential flow ani live order action.
