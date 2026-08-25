from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobAttempt,
    JobRun,
    JobType,
    MisfirePolicy,
    ScheduledJob,
    SchedulerService,
    ScheduleType,
    WorkerHeartbeat,
    WorkerService,
)
from quantlab.backtest import serialize_result
from quantlab.config import get_settings
from quantlab.demo import load_fixture, run_demo
from quantlab.domain import AuditEventType
from quantlab.market_data import StooqProvider, XNYSCalendar
from quantlab.multi_asset import STRATEGY_REGISTRY
from quantlab.operator_read_model import OperatorReadModel
from quantlab.persistence import (
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    RunRepository,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import (
    AuditEventRecord,
    PaperOrderRecord,
    Phase4Repository,
    ReconciliationRecord,
    ReconciliationService,
    RiskDecisionRecord,
    RiskEventRecord,
    TradingCycleRecord,
    TradingCycleService,
)
from quantlab.phase7 import (
    DEFAULT_POLICY,
    MonitoringState,
    PaperMonitoringRunRecord,
    PaperMonitoringService,
    PaperPerformanceEvaluationRecord,
    PaperPerformanceSnapshotRecord,
)
from quantlab.research_service import ResearchService

app = FastAPI(title="Autonomous Quant Lab", version="0.1.0")
settings = get_settings()
repository = RunRepository(
    settings.database_url, bootstrap_test_schema=settings.database_url.startswith("sqlite")
)
fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "sample_market_data.csv"
research_service = ResearchService(repository)
paper_repository = Phase4Repository(settings.database_url, bootstrap_test_schema=False)
paper_repository.seed_account()
trading_service = TradingCycleService(paper_repository)
reconciliation_service = ReconciliationService(paper_repository)
automation_repository = AutomationRepository(settings.database_url)
automation_scheduler = SchedulerService(automation_repository)
automation_worker = WorkerService(automation_repository, settings)
monitoring_service = PaperMonitoringService(lambda: Session(paper_repository.engine))
operator_read_model = OperatorReadModel(lambda: Session(paper_repository.engine), settings)


class JobCreate(BaseModel):
    job_type: JobType
    account_id: str = "paper-main"
    strategy_id: str | None = None
    schedule_type: ScheduleType
    next_run_at: datetime
    interval_seconds: int | None = Field(None, gt=0)
    daily_time: str | None = None
    timezone: str = "UTC"
    misfire_policy: MisfirePolicy = MisfirePolicy.RUN_ONCE_IF_MISSED
    misfire_grace_seconds: int = Field(3600, ge=0)
    max_attempts: int = Field(5, ge=1, le=100)
    config: dict[str, object] = {}


class JobPatch(BaseModel):
    enabled: bool | None = None
    next_run_at: datetime | None = None


class MonitoringPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, object] = DEFAULT_POLICY.copy()


class MonitoringTransition(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class OperatorAction(BaseModel):
    confirmation: str = Field(min_length=4, max_length=10)
    reason: str = Field(min_length=3, max_length=1000)


class OperatorDocument(BaseModel):
    model_config = {"extra": "allow"}


class OperatorOverview(BaseModel):
    server_time_utc: datetime
    trading_mode: str
    live_trading_enabled: bool
    api_health: str
    readiness: str
    paper_account_id: str | None
    trading_state: str | None
    reconciliation_safe: bool | None
    latest_reconciliation_status: str | None
    monitoring_id: str | None
    monitoring_state: str | None
    monitoring_verdict: str | None
    paper_equity: Decimal | None
    paper_cash: Decimal | None
    cumulative_return: Decimal | None
    current_drawdown: Decimal | None
    position_count: int
    open_order_count: int
    last_trading_cycle: datetime | None
    next_scheduled_paper_cycle: datetime | None
    latest_completed_market_session: date
    latest_market_data_status: str | None
    latest_market_data_at: datetime | None
    automation_enabled: bool
    enabled_job_count: int
    dead_letter_count: int
    healthy_worker_count: int
    stale_worker_count: int
    as_of: datetime | None


class OperatorList(BaseModel):
    model_config = {"extra": "allow"}
    items: list[dict[str, object]]
    total: int
    limit: int
    offset: int


@app.get("/operator/overview", response_model=OperatorOverview)
def operator_overview() -> dict[str, object]:
    return operator_read_model.overview(datetime.now(UTC))


@app.get("/operator/paper", response_model=OperatorDocument)
def operator_paper() -> dict[str, object]:
    return operator_read_model.paper()


@app.get("/operator/paper/performance", response_model=OperatorDocument)
def operator_performance(
    period: str = Query("ALL", pattern="^(1M|3M|6M|YTD|1Y|ALL)$"),
) -> dict[str, object]:
    return operator_read_model.performance(period, datetime.now(UTC))


@app.get("/operator/monitoring/{monitoring_id}/comparison", response_model=OperatorDocument)
def operator_monitoring_comparison(monitoring_id: str) -> dict[str, object]:
    result = operator_read_model.comparison(monitoring_id)
    if result is None:
        raise HTTPException(404, "Monitoring neexistuje")
    return result


@app.get("/operator/strategies", response_model=list[OperatorDocument])
def operator_strategies() -> list[dict[str, object]]:
    return operator_read_model.strategies()


@app.get("/operator/strategies/{strategy_identity}", response_model=OperatorDocument)
def operator_strategy(strategy_identity: str) -> dict[str, object]:
    result = operator_read_model.strategy(strategy_identity)
    if result is None:
        raise HTTPException(404, "Strategie neexistuje")
    return result


@app.get("/operator/research/experiments", response_model=OperatorList)
def operator_experiments(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> dict[str, object]:
    return operator_read_model.experiments(limit, offset)


@app.get("/operator/research/experiments/{experiment_id}", response_model=OperatorDocument)
def operator_experiment(experiment_id: str) -> dict[str, object]:
    result = operator_read_model.experiment(experiment_id)
    if result is None:
        raise HTTPException(404, "Experiment neexistuje")
    return result


@app.get("/operator/risk", response_model=OperatorDocument)
def operator_risk() -> dict[str, object]:
    return operator_read_model.risk()


@app.get("/operator/data-health", response_model=OperatorDocument)
def operator_data_health() -> dict[str, object]:
    return operator_read_model.data_health(datetime.now(UTC))


@app.get("/operator/automation", response_model=OperatorDocument)
def operator_automation() -> dict[str, object]:
    return operator_read_model.automation(datetime.now(UTC))


@app.get("/operator/audit", response_model=OperatorList)
def operator_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    correlation_id: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[str, object]:
    if start_utc and end_utc and start_utc > end_utc:
        raise HTTPException(422, "start_utc musí být před end_utc")
    return operator_read_model.audit(
        limit=limit,
        offset=offset,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        start=start_utc,
        end=end_utc,
    )


@app.post("/operator/risk/halt", response_model=OperatorDocument)
def operator_halt(request: OperatorAction) -> dict[str, str]:
    if request.confirmation != "HALT":
        raise HTTPException(422, "Potvrzení musí být HALT")
    paper_repository.halt(
        "paper-main",
        request.reason,
        str(uuid4()),
        AuditEventType.KILL_SWITCH_MANUAL_HALT,
    )
    return {"trading_state": "HALTED"}


@app.post("/operator/risk/resume", response_model=OperatorDocument)
def operator_resume(request: OperatorAction) -> dict[str, str]:
    if request.confirmation != "RESUME":
        raise HTTPException(422, "Potvrzení musí být RESUME")
    try:
        paper_repository.resume("paper-main", str(uuid4()), request.reason)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"trading_state": "NORMAL"}


def _row(row: object) -> dict[str, object]:
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


@app.post("/paper/monitoring/policies")
def create_monitoring_policy(request: MonitoringPolicyCreate) -> dict[str, object]:
    try:
        return _row(
            monitoring_service.create_policy(request.name, request.config, datetime.now(UTC))
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/paper/deployments/{deployment_id}/monitoring/enroll")
def enroll_monitoring(deployment_id: str, policy_id: str) -> dict[str, object]:
    try:
        return _row(monitoring_service.enroll(deployment_id, policy_id, datetime.now(UTC)))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/paper/monitoring")
def monitoring_runs(
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    with Session(paper_repository.engine) as session:
        return [
            _row(item)
            for item in session.scalars(
                select(PaperMonitoringRunRecord)
                .order_by(PaperMonitoringRunRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ]


@app.get("/paper/monitoring/{monitoring_id}")
def monitoring_run(monitoring_id: str) -> dict[str, object]:
    with Session(paper_repository.engine) as session:
        row = session.get(PaperMonitoringRunRecord, monitoring_id)
        if row is None:
            raise HTTPException(404, "Monitoring neexistuje")
        return _row(row)


@app.get("/paper/monitoring/{monitoring_id}/performance")
def monitoring_performance(
    monitoring_id: str, limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    with Session(paper_repository.engine) as session:
        if session.get(PaperMonitoringRunRecord, monitoring_id) is None:
            raise HTTPException(404, "Monitoring neexistuje")
        return [
            _row(item)
            for item in session.scalars(
                select(PaperPerformanceSnapshotRecord)
                .where(PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id)
                .order_by(PaperPerformanceSnapshotRecord.session_date)
                .limit(limit)
                .offset(offset)
            )
        ]


@app.get("/paper/monitoring/{monitoring_id}/evaluations")
def monitoring_evaluations(
    monitoring_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    with Session(paper_repository.engine) as session:
        if session.get(PaperMonitoringRunRecord, monitoring_id) is None:
            raise HTTPException(404, "Monitoring neexistuje")
        return [
            _row(item)
            for item in session.scalars(
                select(PaperPerformanceEvaluationRecord)
                .where(PaperPerformanceEvaluationRecord.monitoring_id == monitoring_id)
                .order_by(PaperPerformanceEvaluationRecord.created_at)
                .limit(limit)
                .offset(offset)
            )
        ]


def _transition_monitoring(
    monitoring_id: str, target: MonitoringState, request: MonitoringTransition
) -> dict[str, object]:
    try:
        return _row(
            monitoring_service.transition(monitoring_id, target, request.reason, datetime.now(UTC))
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/paper/monitoring/{monitoring_id}/pause")
def pause_monitoring(monitoring_id: str, request: MonitoringTransition) -> dict[str, object]:
    return _transition_monitoring(monitoring_id, MonitoringState.PAUSED, request)


@app.post("/paper/monitoring/{monitoring_id}/resume")
def resume_monitoring(monitoring_id: str, request: MonitoringTransition) -> dict[str, object]:
    return _transition_monitoring(monitoring_id, MonitoringState.ACTIVE, request)


@app.post("/paper/monitoring/{monitoring_id}/retire")
def retire_monitoring(monitoring_id: str, request: MonitoringTransition) -> dict[str, object]:
    return _transition_monitoring(monitoring_id, MonitoringState.RETIRED, request)


@app.get("/paper/deployments/{deployment_id}/performance")
def deployment_performance(
    deployment_id: str, limit: int = Query(500, ge=1, le=1000)
) -> list[dict[str, object]]:
    with Session(paper_repository.engine) as session:
        return [
            _row(item)
            for item in session.scalars(
                select(PaperPerformanceSnapshotRecord)
                .where(PaperPerformanceSnapshotRecord.deployment_id == deployment_id)
                .order_by(PaperPerformanceSnapshotRecord.session_date)
                .limit(limit)
            )
        ]


@app.get("/paper/performance/summary")
def performance_summary() -> list[dict[str, object]]:
    with Session(paper_repository.engine) as session:
        runs = tuple(
            session.scalars(
                select(PaperMonitoringRunRecord).order_by(PaperMonitoringRunRecord.created_at)
            )
        )
        result = []
        for run in runs:
            latest = session.scalar(
                select(PaperPerformanceSnapshotRecord)
                .where(PaperPerformanceSnapshotRecord.monitoring_id == run.monitoring_id)
                .order_by(PaperPerformanceSnapshotRecord.session_date.desc())
                .limit(1)
            )
            evaluation = session.scalar(
                select(PaperPerformanceEvaluationRecord)
                .where(PaperPerformanceEvaluationRecord.monitoring_id == run.monitoring_id)
                .order_by(PaperPerformanceEvaluationRecord.created_at.desc())
                .limit(1)
            )
            result.append(
                {
                    "monitoring_id": run.monitoring_id,
                    "deployment_id": run.deployment_id,
                    "state": run.state,
                    "starting_equity": run.starting_equity,
                    "current_equity": latest.marked_equity if latest else None,
                    "cumulative_return": latest.cumulative_return if latest else None,
                    "latest_verdict": evaluation.verdict if evaluation else None,
                }
            )
        return result


@app.get("/health")
@app.get("/health/live")
def health() -> dict[str, str]:
    return {"status": "ok", "trading_mode": "paper", "live_trading_enabled": "false"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    try:
        with automation_repository.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Databáze není dostupná") from exc
    return {"status": "ready", "database": "ok"}


@app.get("/market-data/providers")
def market_data_providers() -> list[dict[str, object]]:
    """Vrací pouze allowlistované adaptery, nikdy dynamický import nebo arbitrary URL."""
    metadata = StooqProvider.metadata
    return [
        {
            "name": metadata.name,
            "version": metadata.version,
            "supports_actions": metadata.supports_actions,
            "requires_credentials": metadata.requires_credentials,
        }
    ]


@app.get("/market-data/calendar")
def market_data_calendar() -> dict[str, str]:
    calendar = XNYSCalendar()
    return {"identity": calendar.identity, "timezone": str(calendar.timezone)}


@app.get("/strategies")
def phase6_strategies() -> list[dict[str, object]]:
    result = []
    for name, strategy_type in sorted(STRATEGY_REGISTRY.items()):
        strategy = strategy_type()
        result.append(
            {
                "name": name,
                "version": strategy.version,
                "required_lookback": strategy.required_lookback,
                "rebalance_frequency": strategy.rebalance_frequency,
                "asset_scope": "long-only USD equities on XNYS calendar",
            }
        )
    return result


@app.get("/datasets/{snapshot_id}")
def dataset_snapshot(snapshot_id: str) -> dict[str, object]:
    with Session(repository.engine) as session:
        row = session.get(DatasetSnapshotRecord, snapshot_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Dataset snapshot nebyl nalezen")
        return {
            "snapshot_id": row.snapshot_id,
            "content_hash": row.content_hash,
            "provider": row.provider,
            "universe_id": row.universe_id,
            "start_at": row.start_at,
            "end_at": row.end_at,
            "coverage": row.coverage,
            "status": row.status,
        }


@app.get("/datasets")
def dataset_snapshots(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    with Session(repository.engine) as session:
        rows = session.scalars(
            select(DatasetSnapshotRecord)
            .order_by(DatasetSnapshotRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_row(row) for row in rows]


@app.get("/market-data/instruments")
def market_data_instruments() -> list[dict[str, object]]:
    with Session(repository.engine) as session:
        return [
            _row(row)
            for row in session.scalars(
                select(InstrumentRecord).order_by(InstrumentRecord.instrument_id)
            )
        ]


@app.get("/market-data/instruments/{instrument_id}")
def market_data_instrument(instrument_id: str) -> dict[str, object]:
    with Session(repository.engine) as session:
        row = session.get(InstrumentRecord, instrument_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Instrument nebyl nalezen")
        return _row(row)


@app.get("/market-data/ingestions")
def market_data_ingestions() -> list[dict[str, object]]:
    with Session(repository.engine) as session:
        return [
            _row(row)
            for row in session.scalars(
                select(MarketDataIngestionRecord).order_by(
                    MarketDataIngestionRecord.started_at.desc()
                )
            )
        ]


@app.get("/market-data/ingestions/{ingestion_id}")
def market_data_ingestion(ingestion_id: str) -> dict[str, object]:
    with Session(repository.engine) as session:
        row = session.get(MarketDataIngestionRecord, ingestion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ingestion nebyl nalezen")
        return _row(row)


@app.get("/universes")
def universes() -> list[dict[str, object]]:
    with Session(repository.engine) as session:
        rows = session.scalars(
            select(UniverseDefinitionRecord).order_by(UniverseDefinitionRecord.universe_id)
        )
        return [
            {
                "universe_id": row.universe_id,
                "name": row.name,
                "kind": row.kind,
                "survivorship_bias_status": (
                    "BIAS_PRONE_STATIC" if row.kind == "STATIC" else "POINT_IN_TIME_SAFE"
                ),
            }
            for row in rows
        ]


@app.get("/universes/{universe_id}")
def universe(universe_id: str) -> dict[str, object]:
    with Session(repository.engine) as session:
        row = session.get(UniverseDefinitionRecord, universe_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Universe nebyl nalezen")
        memberships = session.scalars(
            select(UniverseMembershipRecord)
            .where(UniverseMembershipRecord.universe_id == universe_id)
            .order_by(UniverseMembershipRecord.valid_from)
        )
        return {**_row(row), "memberships": [_row(item) for item in memberships]}


@app.post("/automation/jobs")
def create_automation_job(request: JobCreate) -> dict[str, object]:
    try:
        return _row(automation_repository.create_job(**request.model_dump()))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/automation/jobs")
def automation_jobs(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in automation_repository.page(ScheduledJob, limit, offset)]


@app.get("/automation/jobs/{job_id}")
def automation_job(job_id: str) -> dict[str, object]:
    with Session(automation_repository.engine) as session:
        row = session.get(ScheduledJob, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        return _row(row)


@app.patch("/automation/jobs/{job_id}")
def patch_automation_job(job_id: str, request: JobPatch) -> dict[str, object]:
    with Session(automation_repository.engine) as session:
        row = session.get(ScheduledJob, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        if request.enabled is not None:
            row.enabled = request.enabled
        if request.next_run_at is not None:
            row.next_run_at = request.next_run_at
        row.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        return _row(row)


@app.post("/automation/jobs/{job_id}/enable")
def enable_automation_job(job_id: str) -> dict[str, object]:
    return patch_automation_job(job_id, JobPatch(enabled=True))


@app.post("/automation/jobs/{job_id}/disable")
def disable_automation_job(job_id: str) -> dict[str, object]:
    return patch_automation_job(job_id, JobPatch(enabled=False))


@app.post("/automation/jobs/{job_id}/run-now")
def run_automation_job(
    job_id: str, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]
) -> dict[str, str]:
    if not settings.automation_enabled:
        raise HTTPException(status_code=503, detail="Automation je globálně vypnutá")
    try:
        return {"id": automation_scheduler.run_now(job_id, idempotency_key)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job nebyl nalezen") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/automation/runs")
def automation_runs(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in automation_repository.page(JobRun, limit, offset)]


@app.get("/automation/runs/{run_id}")
def automation_run(run_id: str) -> dict[str, object]:
    with Session(automation_repository.engine) as session:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run nebyl nalezen")
        result = _row(run)
        result["attempts"] = [
            _row(row)
            for row in session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_run_id == run_id)
                .order_by(JobAttempt.attempt_number)
            )
        ]
        return result


@app.post("/automation/runs/{run_id}/retry")
def retry_automation_run(run_id: str) -> dict[str, str]:
    try:
        automation_worker.retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run nebyl nalezen") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": run_id, "status": "RETRY_SCHEDULED"}


@app.get("/operations/workers")
def operations_workers(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    now = datetime.now(UTC)
    rows = automation_repository.page(WorkerHeartbeat, limit, offset)
    result = []
    for row in rows:
        item = _row(row)
        last = (
            row.last_heartbeat_at
            if row.last_heartbeat_at.tzinfo
            else row.last_heartbeat_at.replace(tzinfo=UTC)
        )
        item["state"] = (
            "healthy" if now - last < timedelta(seconds=settings.worker_lease_timeout) else "stale"
        )
        result.append(item)
    return result


@app.get("/operations/summary")
def operations_summary() -> dict[str, object]:
    with Session(automation_repository.engine) as session:
        return {
            "automation_enabled": settings.automation_enabled,
            "enabled_jobs": session.scalar(
                select(func.count()).select_from(ScheduledJob).where(ScheduledJob.enabled.is_(True))
            )
            or 0,
            "dead_letters": session.scalar(
                select(func.count()).select_from(JobRun).where(JobRun.status == "DEAD_LETTER")
            )
            or 0,
        }


@app.get("/paper/account")
@app.get("/portfolio")
def paper_account() -> dict[str, object]:
    return vars(paper_repository.account("paper-main"))


@app.get("/positions")
def paper_positions() -> list[dict[str, object]]:
    return [vars(position) for position in paper_repository.positions("paper-main")]


@app.get("/orders")
def paper_orders(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(PaperOrderRecord, limit, offset)]


@app.get("/orders/{order_id}")
def paper_order(order_id: str) -> dict[str, object]:
    order = trading_service.broker.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Příkaz nebyl nalezen")
    return _row(order)


@app.get("/risk/status")
def risk_status() -> dict[str, object]:
    account = paper_repository.account("paper-main")
    return {"account_id": account.id, "trading_state": account.trading_state}


@app.get("/risk/events")
def risk_events(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(RiskEventRecord, limit, offset)]


@app.get("/risk/decisions")
def risk_decisions(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(RiskDecisionRecord, limit, offset)]


@app.post("/risk/halt")
def risk_halt() -> dict[str, str]:
    paper_repository.halt("paper-main", "manual API halt", str(datetime.now(UTC).timestamp()))
    return {"trading_state": "HALTED"}


@app.post("/risk/resume")
def risk_resume() -> dict[str, str]:
    try:
        paper_repository.resume("paper-main", str(datetime.now(UTC).timestamp()))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"trading_state": "NORMAL"}


@app.get("/trading/cycles")
def trading_cycles(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(TradingCycleRecord, limit, offset)]


@app.get("/trading/cycles/{cycle_id}")
def trading_cycle(cycle_id: str) -> dict[str, object]:
    rows = paper_repository.page(TradingCycleRecord, 200, 0)
    row = next((item for item in rows if _row(item).get("id") == cycle_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Cycle nebyl nalezen")
    return _row(row)


@app.post("/trading/cycles/run-paper")
def run_paper_cycle() -> dict[str, str]:
    bars = load_fixture(fixture)
    cycle_id = trading_service.run(
        "paper-main",
        "moving_average:1.0.0",
        bars[-2:],
        {"SPY": Decimal("0.10")},
        bars[-1].timestamp.date(),
        bars[-2].timestamp,
    )
    return {"id": cycle_id, "mode": "paper"}


@app.get("/audit")
def audit(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(AuditEventRecord, limit, offset)]


@app.get("/reconciliation/status")
def reconciliation_status(
    limit: int = Query(1, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    return [_row(row) for row in paper_repository.page(ReconciliationRecord, limit, offset)]


@app.post("/reconciliation/run")
def reconciliation_run() -> dict[str, object]:
    return vars(reconciliation_service.reconcile("paper-main"))


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


@app.get("/", response_class=RedirectResponse)
def api_root() -> str:
    """Backend je API; produktové uživatelské rozhraní obsluhuje Next.js."""
    return "/docs"
