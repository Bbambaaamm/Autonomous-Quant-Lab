from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from quantlab.backtest import serialize_result
from quantlab.config import get_settings
from quantlab.demo import run_demo
from quantlab.persistence import RunRepository
from quantlab.research_service import ResearchService

app = FastAPI(title="Autonomous Quant Lab", version="0.1.0")
settings = get_settings()
repository = RunRepository(
    settings.database_url, bootstrap_test_schema=settings.database_url.startswith("sqlite")
)
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
@app.get("/research/experiments")
def research_experiments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    strategy: str | None = None,
    strategy_version: str | None = None,
    dataset_id: str | None = None,
    eligibility_status: str | None = None,
) -> list[dict[str, object]]:
    return repository.list_experiments(
        limit=limit,
        offset=offset,
        strategy=strategy,
        strategy_version=strategy_version,
        dataset_id=dataset_id,
        eligibility_status=eligibility_status,
    )


@app.get("/research/leaderboard")
@app.get("/api/research/leaderboard")
def research_leaderboard(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return repository.leaderboard(limit=limit, offset=offset)


@app.get("/research/compare")
@app.get("/api/research/compare")
def research_compare(ids: Annotated[list[str], Query()]) -> list[dict[str, object]]:
    try:
        return repository.compare(ids)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
