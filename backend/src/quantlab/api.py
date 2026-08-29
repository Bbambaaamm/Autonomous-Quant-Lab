import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from quantlab.automation import (
    AutomationRepository,
    JobAttempt,
    JobRun,
    JobType,
    MANAGED_JOB_TYPES,
    MisfirePolicy,
    ScheduledJob,
    SchedulerService,
    ScheduleType,
    WorkerHeartbeat,
    WorkerService,
)
from quantlab.backtest import serialize_result
from quantlab.config import get_settings
from quantlab.control_plane import ControlPlaneRegistryService
from quantlab.demo import run_demo
from quantlab.domain import AuditEventType
from quantlab.market_data import AssetType, DatasetInvalid, Instrument, StooqProvider, XNYSCalendar
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.multi_asset import STRATEGY_REGISTRY
from quantlab.operator_read_model import OperatorReadModel
from quantlab.persistence import (
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    RunRepository,
    StrategyDeploymentRecord,
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
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6EligibilityService,
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
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
from quantlab.security import current_principal, security_boundary
from quantlab.universe import UniverseDefinition, UniverseKind, UniverseMembership

settings = get_settings()
app = FastAPI(
    title="Autonomous Quant Lab",
    version="0.1.0",
    docs_url=None if settings.app_env == "production" else "/docs",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)


@app.middleware("http")
async def enforce_security(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.headers.get("host", "").split(":", 1)[0] not in settings.allowed_hosts:
        return JSONResponse({"detail": "Neplatný Host"}, 400)
    return await security_boundary(request, call_next, settings)


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


def session_factory() -> Session:
    return Session(paper_repository.engine)


control_plane_registry = ControlPlaneRegistryService(session_factory)
market_data_service = PersistentMarketDataService(session_factory)
dataset_snapshot_service = DatasetSnapshotService(session_factory)
phase6_runner = Phase6ExperimentRunner(session_factory)
eligibility_service = Phase6EligibilityService(session_factory)
deployment_service = DeploymentService(session_factory)


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


class DeploymentJobCreate(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    schedule_type: ScheduleType
    next_run_at: datetime
    interval_seconds: int | None = Field(None, gt=0)
    daily_time: str | None = None
    timezone: str = "UTC"
    misfire_policy: MisfirePolicy = MisfirePolicy.RUN_ONCE_IF_MISSED
    misfire_grace_seconds: int = Field(3600, ge=0)
    max_attempts: int = Field(5, ge=1, le=100)


class AutonomousScheduleMutation(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class MonitoringPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, object] = DEFAULT_POLICY.copy()


class OperatorMonitoringPolicyCreate(MonitoringPolicyCreate):
    reason: str = Field(min_length=3, max_length=1000)


class MonitoringTransition(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class EligibilityMutation(BaseModel):
    model_config = {"extra": "forbid"}
    reason: str = Field(min_length=3, max_length=1000)
    policy_id: str = Field("phase6-paper-candidate", pattern="^phase6-paper-candidate$")
    policy_version: int = Field(1, ge=1, le=1)


class ReasonedMutation(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class InstrumentCreate(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    exchange: str = "XNYS"
    calendar: str = "XNYS"
    currency: str = "USD"
    asset_type: AssetType = AssetType.EQUITY
    active_from: date
    active_to: date | None = None
    reason: str = Field(min_length=3, max_length=1000)


class UniverseCreate(BaseModel):
    universe_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._~-]+$")
    name: str = Field(min_length=1, max_length=100)
    kind: UniverseKind = UniverseKind.POINT_IN_TIME_MEMBERSHIP
    reason: str = Field(min_length=3, max_length=1000)


class MembershipCreate(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    valid_from: datetime
    valid_to: datetime | None = None
    known_at: datetime
    reason: str = Field(min_length=3, max_length=1000)


class IngestionCreate(BaseModel):
    provider: str = "stooq"
    instrument_id: str = Field(min_length=1, max_length=64)
    start: date
    end: date
    reason: str = Field(min_length=3, max_length=1000)


class SnapshotCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    universe_id: str = Field(min_length=1, max_length=64)
    start: date
    end: date
    as_of: datetime
    minimum_coverage: Decimal = Field(Decimal("0.8"), ge=0, le=1)
    reason: str = Field(min_length=3, max_length=1000)


class ExperimentCreate(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=64)
    strategy_name: str = Field(min_length=1, max_length=100)
    strategy_version: str = Field(min_length=1, max_length=50)
    parameter_configs: list[dict[str, object]] = Field(min_length=1, max_length=50)
    train_fraction: Decimal = Decimal("0.6")
    validation_fraction: Decimal = Decimal("0.2")
    initial_cash: Decimal = Field(Decimal("100000"), gt=0)
    commission_bps: Decimal = Field(Decimal("1"), ge=0)
    seed: int = 42
    code_sha: str = Field(min_length=40, max_length=40)
    reason: str = Field(min_length=3, max_length=1000)


class DeploymentCreate(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=64)
    paper_account_id: str = "paper-main"
    reason: str = Field(min_length=3, max_length=1000)


class MonitoringEnrollment(BaseModel):
    deployment_id: str = Field(min_length=1, max_length=64)
    policy_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=1000)


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
    autonomous_readiness: str
    autonomous_workload_enabled: bool
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


@app.get("/operator/deployments/summary", response_model=list[OperatorDocument])
def operator_deployments_summary(
    limit: int = Query(50, ge=1, le=100),
) -> list[dict[str, object]]:
    return operator_read_model.deployments(limit)


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
def operator_data_health(
    membership_limit: int = Query(100, ge=1, le=500),
    membership_offset: int = Query(0, ge=0),
) -> dict[str, object]:
    return operator_read_model.data_health(
        datetime.now(UTC), membership_limit=membership_limit, membership_offset=membership_offset
    )


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
    start_utc = _normalize_utc_filter(start_utc)
    end_utc = _normalize_utc_filter(end_utc)
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


def _normalize_utc_filter(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@app.post("/operator/risk/halt", response_model=OperatorDocument)
def operator_halt(request: OperatorAction, http_request: Request) -> dict[str, str]:
    if request.confirmation != "HALT":
        raise HTTPException(422, "Potvrzení musí být HALT")
    principal = current_principal(http_request)
    paper_repository.halt(
        "paper-main",
        request.reason,
        str(uuid4()),
        AuditEventType.KILL_SWITCH_MANUAL_HALT,
        actor={
            "actor_id": principal.actor_id,
            "actor_role": principal.role.name,
            "authentication": "bearer",
        },
    )
    return {"trading_state": "HALTED"}


@app.post("/operator/risk/resume", response_model=OperatorDocument)
def operator_resume(request: OperatorAction, http_request: Request) -> dict[str, str]:
    if request.confirmation != "RESUME":
        raise HTTPException(422, "Potvrzení musí být RESUME")
    try:
        principal = current_principal(http_request)
        paper_repository.resume(
            "paper-main",
            str(uuid4()),
            request.reason,
            actor={
                "actor_id": principal.actor_id,
                "actor_role": principal.role.name,
                "authentication": "bearer",
            },
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"trading_state": "NORMAL"}


def _row(row: object) -> dict[str, object]:
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


def _actor(request: Request) -> dict[str, str]:
    principal = current_principal(request)
    return {
        "actor_id": principal.actor_id,
        "actor_role": principal.role.name,
        "authentication": "bearer",
    }


def _correlation(request: Request) -> str:
    return request.headers.get("x-correlation-id", str(uuid4()))[:64]


def _audit_control_mutation(
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: dict[str, str],
    reason: str,
    correlation_id: str,
) -> None:
    identity = hashlib.sha256(
        json.dumps(
            [
                event_type,
                entity_type,
                entity_id,
                actor["actor_id"],
                reason,
                correlation_id,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with session_factory() as session, session.begin():
        if session.get(AuditEventRecord, identity) is None:
            session.add(
                AuditEventRecord(
                    id=identity,
                    timestamp=datetime.now(UTC),
                    event_type=event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    trading_cycle_id=None,
                    correlation_id=correlation_id,
                    payload_json=json.dumps({"actor": actor, "reason": reason}, sort_keys=True),
                )
            )


@app.post("/operator/reconciliation/run", response_model=OperatorDocument)
def operator_reconciliation(body: ReasonedMutation, request: Request) -> dict[str, object]:
    correlation_id = _correlation(request)
    result = reconciliation_service.reconcile("paper-main", correlation_id=correlation_id)
    _audit_control_mutation(
        "CONTROL_RECONCILIATION_RUN",
        "reconciliation",
        result.id,
        _actor(request),
        body.reason,
        correlation_id,
    )
    return vars(result)


@app.post("/operator/instruments")
def create_instrument(body: InstrumentCreate, request: Request) -> dict[str, object]:
    try:
        row = control_plane_registry.register_instrument(
            Instrument(
                body.instrument_id,
                body.symbol.strip().upper(),
                body.exchange,
                body.calendar,
                body.currency,
                body.asset_type,
                body.active_from,
                body.active_to,
                datetime.now(UTC),
            )
        )
        _audit_control_mutation(
            "CONTROL_INSTRUMENT_REGISTERED",
            "instrument",
            row.instrument_id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(row)
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/universes")
def create_universe(body: UniverseCreate, request: Request) -> dict[str, object]:
    try:
        row = control_plane_registry.create_universe(
            UniverseDefinition(body.universe_id, body.name, body.kind, datetime.now(UTC))
        )
        _audit_control_mutation(
            "CONTROL_UNIVERSE_CREATED",
            "universe",
            row.universe_id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(row)
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/universes/{universe_id}/memberships")
def add_universe_membership(
    universe_id: str, body: MembershipCreate, request: Request
) -> dict[str, object]:
    try:
        row = control_plane_registry.add_membership(
            UniverseMembership(
                universe_id, body.instrument_id, body.valid_from, body.valid_to, body.known_at
            )
        )
        evidence_id = f"{universe_id}:{body.instrument_id}:{body.valid_from.isoformat()}"
        _audit_control_mutation(
            "CONTROL_MEMBERSHIP_ADDED",
            "universe_membership",
            evidence_id[:64],
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(row)
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/market-data/ingestions")
def ingest_market_data(body: IngestionCreate, request: Request) -> dict[str, object]:
    if body.provider != "stooq":
        raise HTTPException(422, "Provider není v production allowlistu")
    with session_factory() as session:
        persisted = session.get(InstrumentRecord, body.instrument_id)
        if persisted is None:
            raise HTTPException(404, "Instrument neexistuje")
        instrument = Instrument(
            persisted.instrument_id,
            persisted.symbol,
            persisted.exchange,
            persisted.calendar,
            persisted.currency,
            AssetType(persisted.asset_type),
            persisted.active_from.date(),
            persisted.active_to.date() if persisted.active_to else None,
            persisted.created_at,
        )
    result = market_data_service.ingest(
        StooqProvider(), instrument, body.start, body.end, datetime.now(UTC)
    )
    _audit_control_mutation(
        "CONTROL_MARKET_DATA_INGESTED",
        "market_data_ingestion",
        result.ingestion_id,
        _actor(request),
        body.reason,
        _correlation(request),
    )
    payload = vars(result)
    if result.status != "SUCCEEDED":
        raise HTTPException(502, payload)
    return payload


@app.post("/operator/datasets")
def build_dataset(body: SnapshotCreate, request: Request) -> dict[str, object]:
    try:
        snapshot = dataset_snapshot_service.build(
            as_of=body.as_of,
            provider=body.provider,
            universe_id=body.universe_id,
            start=body.start,
            end=body.end,
            minimum_coverage=body.minimum_coverage,
        )
        _audit_control_mutation(
            "CONTROL_DATASET_BUILT",
            "dataset_snapshot",
            snapshot.snapshot_id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        if snapshot.status != "VALID":
            raise HTTPException(
                409,
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "status": snapshot.status,
                    "coverage": str(snapshot.coverage),
                },
            )
        return vars(snapshot)
    except DatasetInvalid as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/research/experiments")
def run_phase6_experiment(body: ExperimentCreate, request: Request) -> dict[str, object]:
    try:
        control_plane_registry.ensure_strategy(
            body.strategy_name, body.strategy_version, datetime.now(UTC)
        )
        row = phase6_runner.run(
            Phase6ExperimentRequest(
                body.snapshot_id,
                body.strategy_name,
                body.strategy_version,
                tuple(body.parameter_configs),
                body.train_fraction,
                body.validation_fraction,
                body.initial_cash,
                body.commission_bps,
                body.seed,
                body.code_sha,
            )
        )
        _audit_control_mutation(
            "CONTROL_PHASE6_EXPERIMENT_COMPLETED",
            "experiment",
            row.id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(row)
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/research/experiments/{experiment_id}/eligibility")
def evaluate_phase6_eligibility(
    experiment_id: str, body: EligibilityMutation, request: Request
) -> dict[str, object]:
    try:
        return _row(
            eligibility_service.evaluate_eligibility(
                experiment_id,
                actor=_actor(request),
                reason=body.reason,
                correlation_id=_correlation(request),
            )
        )
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/operator/research/experiments/{experiment_id}/eligibility")
def phase6_eligibility(experiment_id: str) -> dict[str, object]:
    row = eligibility_service.get(experiment_id)
    if row is None:
        raise HTTPException(404, "Eligibility rozhodnutí neexistuje")
    result = _row(row)
    for name in ("policy_json", "metrics_json", "rules_json", "actor_json"):
        result[name.removesuffix("_json")] = json.loads(str(result.pop(name)))
    return result


@app.post("/operator/research/experiments/{experiment_id}/promote")
def promote_phase6_experiment(
    experiment_id: str, body: ReasonedMutation, request: Request
) -> dict[str, object]:
    try:
        return _row(
            eligibility_service.promote(
                experiment_id,
                actor=_actor(request),
                reason=body.reason,
                correlation_id=_correlation(request),
            )
        )
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/deployments")
def create_deployment(body: DeploymentCreate, request: Request) -> dict[str, object]:
    try:
        return _row(
            deployment_service.create(
                body.experiment_id,
                body.paper_account_id,
                actor=_actor(request),
                reason=body.reason,
                correlation_id=_correlation(request),
            )
        )
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/deployments/{deployment_id}/approve")
def approve_deployment(
    deployment_id: str, body: ReasonedMutation, request: Request
) -> dict[str, str]:
    try:
        deployment_service.approve(
            deployment_id,
            datetime.now(UTC),
            actor=_actor(request),
            reason=body.reason,
            correlation_id=_correlation(request),
            allow_already_approved=True,
        )
        return {"deployment_id": deployment_id, "status": "APPROVED"}
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/deployments/{deployment_id}/jobs")
def schedule_deployment_job(
    deployment_id: str, body: DeploymentJobCreate, request: Request
) -> dict[str, object]:
    try:
        job = automation_repository.create_deployment_job(
            deployment_id=deployment_id,
            **body.model_dump(exclude={"reason"}),
        )
        _audit_control_mutation(
            "CONTROL_PAPER_DEPLOYMENT_JOB_SCHEDULED",
            "scheduled_job",
            job.id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(job)
    except KeyError as exc:
        raise HTTPException(404, "Deployment neexistuje") from exc
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/deployments/{deployment_id}/autonomous/enable")
def enable_autonomous_deployment(
    deployment_id: str, body: AutonomousScheduleMutation, request: Request
) -> dict[str, object]:
    return _set_autonomous_deployment(deployment_id, body, request, True)


@app.post("/operator/deployments/{deployment_id}/autonomous/disable")
def disable_autonomous_deployment(
    deployment_id: str, body: AutonomousScheduleMutation, request: Request
) -> dict[str, object]:
    return _set_autonomous_deployment(deployment_id, body, request, False)


def _set_autonomous_deployment(
    deployment_id: str,
    body: AutonomousScheduleMutation,
    request: Request,
    enabled: bool,
) -> dict[str, object]:
    try:
        if enabled and not StooqProvider().metadata.supports_actions:
            raise DatasetInvalid(
                "CORPORATE_ACTIONS_UNSUPPORTED: production provider není způsobilý pro equity autonomous pilot"
            )
        job = automation_repository.set_autonomous_deployment(
            deployment_id=deployment_id, enabled=enabled
        )
        _audit_control_mutation(
            "CONTROL_AUTONOMOUS_SCHEDULE_ENABLED"
            if enabled
            else "CONTROL_AUTONOMOUS_SCHEDULE_DISABLED",
            "scheduled_job",
            job.id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(job)
    except KeyError as exc:
        raise HTTPException(404, "Deployment neexistuje") from exc
    except (ValueError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/operator/monitoring/enrollments")
def operator_monitoring_enrollment(
    body: MonitoringEnrollment, request: Request
) -> dict[str, object]:
    try:
        now = datetime.now(UTC)
        row = monitoring_service.enroll(body.deployment_id, body.policy_id, now)
        with session_factory() as session:
            deployment = session.get(StrategyDeploymentRecord, row.deployment_id)
            if deployment is None:
                raise DatasetInvalid("Monitoring deployment lineage neexistuje")
            account_id = deployment.paper_account_id
        monitoring_job = automation_repository.ensure_monitoring_job(
            monitoring_id=row.monitoring_id,
            account_id=account_id,
            now=now,
        )
        correlation_id = _correlation(request)
        _audit_control_mutation(
            "CONTROL_MONITORING_ENROLLED",
            "monitoring",
            row.monitoring_id,
            _actor(request),
            body.reason,
            correlation_id,
        )
        _audit_control_mutation(
            "CONTROL_MONITORING_JOB_ENSURED",
            "scheduled_job",
            monitoring_job.id,
            _actor(request),
            body.reason,
            correlation_id,
        )
        result = _row(row)
        result["monitoring_job"] = _row(monitoring_job)
        return result
    except (ValueError, RuntimeError, DatasetInvalid) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/paper/monitoring/policies")
def create_monitoring_policy(request: MonitoringPolicyCreate) -> dict[str, object]:
    try:
        return _row(
            monitoring_service.create_policy(request.name, request.config, datetime.now(UTC))
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/operator/monitoring/policies")
def create_operator_monitoring_policy(
    body: OperatorMonitoringPolicyCreate, request: Request
) -> dict[str, object]:
    try:
        row = monitoring_service.create_policy(body.name, body.config, datetime.now(UTC))
        _audit_control_mutation(
            "CONTROL_MONITORING_POLICY_CREATED",
            "monitoring_policy",
            row.policy_id,
            _actor(request),
            body.reason,
            _correlation(request),
        )
        return _row(row)
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


@app.post("/operator/monitoring/{monitoring_id}/{action}")
def operator_monitoring_transition(
    monitoring_id: str, action: str, body: MonitoringTransition, request: Request
) -> dict[str, object]:
    """Tenký auditovaný operator adaptér nad autoritativním Phase 7 state machine."""
    targets = {
        "pause": MonitoringState.PAUSED,
        "resume": MonitoringState.ACTIVE,
        "retire": MonitoringState.RETIRED,
    }
    target = targets.get(action)
    if target is None:
        raise HTTPException(404, "Monitoring transition není podporována")
    result = _transition_monitoring(monitoring_id, target, body)
    _audit_control_mutation(
        f"CONTROL_MONITORING_{action.upper()}",
        "monitoring",
        monitoring_id,
        _actor(request),
        body.reason,
        _correlation(request),
    )
    return result


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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    try:
        with automation_repository.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Databáze není dostupná") from exc
    return {"status": "ready", "database": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    health_ready()
    return {"status": "ready"}


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
    if request.job_type == JobType.RUN_PAPER_CYCLE or request.job_type in MANAGED_JOB_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Managed PAPER job lze plánovat pouze podporovanou operator control-plane mutation",
        )
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
        if JobType(row.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER job lze měnit pouze operator control-plane cestou",
            )
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
    with Session(automation_repository.engine) as session:
        job = session.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        if JobType(job.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER job nepodporuje generic run-now",
            )
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


@app.post("/operator/automation/runs/{run_id}/retry")
def operator_retry_automation_run(
    run_id: str, body: ReasonedMutation, request: Request
) -> dict[str, object]:
    if not settings.automation_enabled:
        raise HTTPException(status_code=503, detail="Automation je globálně vypnutá")
    try:
        row = automation_repository.retry_managed_run(
            run_id,
            actor=_actor(request),
            reason=body.reason,
            correlation_id=_correlation(request),
        )
        return _row(row)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run nebyl nalezen") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/automation/runs/{run_id}/retry")
def retry_automation_run(run_id: str) -> dict[str, str]:
    with Session(automation_repository.engine) as session:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run nebyl nalezen")
        job = session.get(ScheduledJob, run.scheduled_job_id)
        if job is not None and JobType(job.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER run vyžaduje auditovaný operator retry",
            )
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
def risk_halt(request: OperatorAction, http_request: Request) -> dict[str, str]:
    return operator_halt(request, http_request)


@app.post("/risk/resume")
def risk_resume(request: OperatorAction, http_request: Request) -> dict[str, str]:
    return operator_resume(request, http_request)


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


@app.post("/demo/research/experiments")
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
