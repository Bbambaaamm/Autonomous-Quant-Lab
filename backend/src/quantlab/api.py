from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from quantlab.backtest import serialize_result
from quantlab.demo import run_demo
from quantlab.persistence import RunRepository

app = FastAPI(title="Autonomous Quant Lab", version="0.1.0")
repository = RunRepository()
fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "sample_market_data.csv"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "trading_mode": "paper", "live_trading_enabled": "false"}


@app.post("/api/backtests/demo")
def demo_backtest() -> dict[str, object]:
    result = serialize_result(run_demo(fixture))
    run_id = repository.save("moving_average:1.0.0", result, datetime.now(timezone.utc))
    return {"id": run_id, **result}


@app.get("/api/backtests")
def backtests() -> list[dict[str, object]]:
    return repository.list()


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html><html lang='cs'><head><meta charset='utf-8'><title>Autonomous Quant Lab</title>
<style>body{font-family:system-ui;max-width:900px;margin:3rem auto;background:#07111f;color:#e6f1ff}button{padding:.7rem;background:#40c9a2;border:0}pre{background:#101f33;padding:1rem}</style></head>
<body><h1>Autonomous Quant Lab</h1><p>Bezpečný paper-trading vertical slice.</p>
<button onclick='run()'>Spustit MA backtest</button><pre id='out'>Připraveno</pre>
<script>async function run(){let r=await fetch('/api/backtests/demo',{method:'POST'});document.querySelector('#out').textContent=JSON.stringify(await r.json(),null,2)}</script></body></html>"""
