from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from quantlab.backtest import serialize_result
from quantlab.demo import run_demo
from quantlab.persistence import RunRepository
from quantlab.research_service import ResearchService

app = FastAPI(title="Autonomous Quant Lab", version="0.1.0")
repository = RunRepository()
fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "sample_market_data.csv"
research_service = ResearchService(repository)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "trading_mode": "paper", "live_trading_enabled": "false"}


@app.post("/api/backtests/demo")
def demo_backtest() -> dict[str, object]:
    result = serialize_result(run_demo(fixture))
    run_id = repository.save("moving_average:1.0.0", result, datetime.now(UTC))
    return {"id": run_id, **result}


@app.get("/api/backtests")
def backtests() -> list[dict[str, object]]:
    return repository.list()


@app.post("/research/experiments")
@app.post("/api/research/experiments")
def create_research_experiment() -> dict[str, object]:
    return research_service.create_demo_experiment(fixture)


@app.get("/api/research/experiments")
def research_experiments() -> list[dict[str, object]]:
    return research_service.list()


@app.get("/research/experiments/{experiment_id}")
@app.get("/api/research/experiments/{experiment_id}")
def research_experiment(experiment_id: str) -> dict[str, object]:
    result = research_service.get(experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment nebyl nalezen")
    return result


@app.get("/research/experiments/{experiment_id}/report")
@app.get("/api/research/experiments/{experiment_id}/report")
def research_report(experiment_id: str) -> dict[str, str]:
    experiment = research_service.get(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment nebyl nalezen")
    result = experiment["result"]
    if not isinstance(result, dict) or not isinstance(result.get("report"), str):
        raise HTTPException(status_code=404, detail="Report nebyl nalezen")
    return {"id": experiment_id, "report": result["report"]}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    # ruff: noqa: E501
    return """<!doctype html><html lang='cs'><head><meta charset='utf-8'><title>Autonomous Quant Lab</title>
<style>body{font-family:system-ui;max-width:900px;margin:3rem auto;background:#07111f;color:#e6f1ff}button{padding:.7rem;background:#40c9a2;border:0}pre{background:#101f33;padding:1rem}</style></head>
<body><h1>Autonomous Quant Lab</h1><p>Bezpečný paper-trading vertical slice.</p>
<button onclick='run()'>Spustit MA backtest</button><pre id='out'>Připraveno</pre>
<script>async function run(){let r=await fetch('/api/backtests/demo',{method:'POST'});document.querySelector('#out').textContent=JSON.stringify(await r.json(),null,2)}</script></body></html>"""
