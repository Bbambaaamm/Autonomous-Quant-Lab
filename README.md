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
