# Autonomous Quant Lab

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
