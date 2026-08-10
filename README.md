# Autonomous Quant Lab

Auditovatelná research a paper-trading platforma. Aktuální vertical slice načte fixture,
validuje data, vytvoří moving-average cíle, provede next-open backtest s náklady a slippage,
aplikuje risk limity, uloží běh a zobrazí jej ve FastAPI dashboardu.

## Lokální spuštění

```bash
cd backend
uv sync --all-groups
uv run uvicorn quantlab.api:app --reload
```

Otevřete <http://127.0.0.1:8000>. Testy a kontroly: `make test`.

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
