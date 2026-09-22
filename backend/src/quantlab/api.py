import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from quantlab.asset_directory import AssetDirectoryService, fetch_assets
from quantlab.automation import (
    MANAGED_JOB_TYPES,
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
from quantlab.control_plane import ControlPlaneRegistryService
from quantlab.demo import run_demo
from quantlab.domain import AuditEventType
from quantlab.free_sources import zero_cost_source_matrix
from quantlab.market_catalog import CatalogError, MarketCatalogService
from quantlab.market_data import AssetType, DatasetInvalid, Instrument, XNYSCalendar
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.market_pipeline import MarketPipeline
from quantlab.market_screening import MarketScreening
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
    normalize_strategy_config,
)
from quantlab.phase7 import (
    DEFAULT_POLICY,
    MonitoringState,
    PaperMonitoringRunRecord,
    PaperMonitoringService,
    PaperPerformanceEvaluationRecord,
    PaperPerformanceSnapshotRecord,
)
from quantlab.provider_factory import build_market_data_provider
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
    provider: str = settings.market_data_provider
    instrument_id: str = Field(min_length=1, max_length=64)
    start: date
    end: date
    reason: str = Field(min_length=3, max_length=1000)


class SnapshotCreate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=40)
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
    code_sha: str | None = Field(default=None, min_length=40, max_length=40)
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
    stopped_worker_count: int
    latest_paper_run: dict[str, object] | None
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


@app.get("/operator/research/options", response_model=OperatorDocument)
def operator_research_options() -> dict[str, object]:
    try:
        revision = Phase6ExperimentRunner._code_sha(None)
    except DatasetInvalid:
        revision = None
    return {
        "code_sha": revision,
        "strategies": [
            {
                "name": name,
                "version": strategy().version,
                "defaults": normalize_strategy_config(name, strategy().version, {}),
            }
            for name, strategy in STRATEGY_REGISTRY.items()
        ],
    }


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
    if body.provider != settings.market_data_provider:
        raise HTTPException(422, "Provider neodpovídá production konfiguraci")
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
    provider = build_market_data_provider(settings, paper_repository.engine)
    result = market_data_service.ingest(
        provider, instrument, body.start, body.end, datetime.now(UTC)
    )
    _audit_control_mutation(
        "CONTROL_MARKET_DATA_INGESTED",
        "market_data_ingestion",
        result.ingestion_id,
        _actor(request),
        body.reason,
        _correlation(request),
    )
    # HTTPException detail obchází Pydantic response serializaci; proto zde datumy
    # převádíme explicitně a deterministicky, stejně jako úspěšná JSON response.
    payload: dict[str, object] = jsonable_encoder(result)
    if result.status != "SUCCEEDED":
        raise HTTPException(502, payload)
    return payload


@app.post("/operator/datasets")
def build_dataset(body: SnapshotCreate, request: Request) -> dict[str, object]:
    try:
        snapshot = dataset_snapshot_service.build(
            as_of=body.as_of,
            provider=body.provider
            or build_market_data_provider(
                settings, paper_repository.engine
            ).metadata.persistent_name,
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
        if enabled:
            provider = build_market_data_provider(settings, paper_repository.engine)
            if not provider.metadata.supports_actions:
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