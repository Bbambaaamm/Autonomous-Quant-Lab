from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from threading import Event, Thread
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.config import Settings
from quantlab.persistence import Base, _sqlite_fk
from quantlab.phase4 import ReconciliationService, TradingCycleRecord, TradingCycleService


class JobType(StrEnum):
    RUN_PAPER_CYCLE = "RUN_PAPER_CYCLE"
    RUN_PAPER_DEPLOYMENT = "RUN_PAPER_DEPLOYMENT"
    RUN_RECONCILIATION = "RUN_RECONCILIATION"
    MONITOR_PAPER_DEPLOYMENT = "MONITOR_PAPER_DEPLOYMENT"


class ScheduleType(StrEnum):
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"


class MisfirePolicy(StrEnum):
    RUN_ONCE_IF_MISSED = "RUN_ONCE_IF_MISSED"
    SKIP_IF_TOO_OLD = "SKIP_IF_TOO_OLD"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    LEASE_LOST = "LEASE_LOST"


TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.DEAD_LETTER, RunStatus.CANCELLED}


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        CheckConstraint(
            "interval_seconds IS NULL OR interval_seconds > 0", name="ck_job_interval_positive"
        ),
        CheckConstraint("misfire_grace_seconds >= 0", name="ck_job_misfire_nonnegative"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_job_attempts_range"),
        Index("ix_scheduled_jobs_due", "enabled", "next_run_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_id: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    daily_time: Mapped[str | None] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    misfire_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("scheduled_job_id", "occurrence_key", name="uq_job_run_occurrence"),
        Index("ix_job_runs_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index("ix_job_runs_schedule", "scheduled_job_id", "scheduled_for"),
        Index("ix_job_runs_created", "created_at"),
        Index("ix_job_runs_correlation", "correlation_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scheduled_job_id: Mapped[str] = mapped_column(
        ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    occurrence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trading_cycle_id: Mapped[str | None] = mapped_column(String(64))
    deployment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    monitoring_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reconciliation_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(40))
    no_action_reason: Mapped[str | None] = mapped_column(String(100))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_run_id", "attempt_number"),
        CheckConstraint("attempt_number > 0", name="ck_attempt_number_positive"),
        Index("ix_attempt_worker", "worker_id"),
        Index("ix_attempt_correlation", "correlation_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_run_id: Mapped[str] = mapped_column(
        ForeignKey("job_runs.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column()
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    scheduler_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL")
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def validate_payload(payload: dict[str, Any]) -> None:
    forbidden = {"broker", "live", "mode", "trading_mode", "live_trading_enabled"}
    stack: list[object] = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            normalized_keys = {str(key).lower() for key in value}
            if forbidden.intersection(normalized_keys):
                raise ValueError("Automation konfigurace nesmí obsahovat execution mode ani broker")
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def next_occurrence(job: ScheduledJob, previous: datetime) -> datetime:
    previous = utc(previous)
    if job.schedule_type == ScheduleType.INTERVAL:
        if not job.interval_seconds or job.interval_seconds <= 0:
            raise ValueError("Interval musí být kladný")
        return previous + timedelta(seconds=job.interval_seconds)
    try:
        zone = ZoneInfo(job.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Neplatná časová zóna") from exc
    if not job.daily_time:
        raise ValueError("Daily schedule vyžaduje čas")
    hour, minute = parse_daily_time(job.daily_time)
    local_day = previous.astimezone(zone).date() + timedelta(days=1)
    # fold=0 volí první výskyt opakované hodiny; neexistující hodina se normalizuje dopředu.
    candidate = datetime.combine(local_day, time(hour, minute), zone).replace(fold=0)
    roundtrip = candidate.astimezone(UTC).astimezone(zone)
    if (roundtrip.hour, roundtrip.minute) != (hour, minute):
        candidate = roundtrip
    return candidate.astimezone(UTC)


def parse_daily_time(value: str) -> tuple[int, int]:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Daily čas musí mít platný formát HH:MM") from exc
    if parsed.second or parsed.microsecond or len(value) != 5:
        raise ValueError("Daily čas musí mít platný formát HH:MM")
    return parsed.hour, parsed.minute


class AutomationRepository:
    def __init__(self, database_url: str, bootstrap_test_schema: bool = False) -> None:
        self.engine = create_engine(database_url)
        if database_url.startswith("sqlite"):
            from sqlalchemy import event

            event.listen(self.engine, "connect", _sqlite_fk)
        if bootstrap_test_schema:
            Base.metadata.create_all(self.engine)

    def create_job(
        self,
        *,
        job_type: JobType,
        account_id: str,
        schedule_type: ScheduleType,
        next_run_at: datetime,
        strategy_id: str | None = None,
        interval_seconds: int | None = None,
        daily_time: str | None = None,
        timezone: str = "UTC",
        misfire_policy: MisfirePolicy = MisfirePolicy.RUN_ONCE_IF_MISSED,
        misfire_grace_seconds: int = 3600,
        max_attempts: int = 5,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
        job_id: str | None = None,
    ) -> ScheduledJob:
        config = config or {}
        validate_payload(config)
        ZoneInfo(timezone)
        if job_type == JobType.RUN_PAPER_CYCLE and not strategy_id:
            raise ValueError("Paper cycle vyžaduje strategy_id")
        if job_type == JobType.RUN_PAPER_DEPLOYMENT:
            if strategy_id is not None or set(config) != {"deployment_id"}:
                raise ValueError("Deployment job přijímá pouze deployment_id")
            if not isinstance(config["deployment_id"], str) or not config["deployment_id"]:
                raise ValueError("Deployment job vyžaduje platné deployment_id")
        if job_type == JobType.MONITOR_PAPER_DEPLOYMENT and set(config) != {"monitoring_id"}:
            raise ValueError("Monitoring job přijímá pouze monitoring_id")
        if schedule_type == ScheduleType.INTERVAL and (
            interval_seconds is None or interval_seconds <= 0
        ):
            raise ValueError("Interval musí být kladný")
        if schedule_type == ScheduleType.DAILY:
            if not daily_time:
                raise ValueError("Daily schedule vyžaduje čas")
            parse_daily_time(daily_time)
        now = datetime.now(UTC)
        row = ScheduledJob(
            id=job_id or str(uuid4()),
            job_type=job_type,
            account_id=account_id,
            strategy_id=strategy_id,
            enabled=enabled,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            daily_time=daily_time,
            timezone=timezone,
            misfire_policy=misfire_policy,
            misfire_grace_seconds=misfire_grace_seconds,
            next_run_at=utc(next_run_at),
            max_attempts=max_attempts,
            config_json=json.dumps(config, sort_keys=True),
            correlation_metadata_json="{}",
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if job_id is None:
                    raise
                concurrent = session.get(ScheduledJob, job_id)
                if concurrent is None:
                    raise
                session.expunge(concurrent)
                return concurrent
            session.refresh(row)
            session.expunge(row)
        return row

    def create_deployment_job(self, *, deployment_id: str, **schedule: Any) -> ScheduledJob:
        """Vytvoří idempotentní job pouze z ověřené persistentní deployment lineage."""
        from quantlab.persistence import StrategyDeploymentRecord

        with Session(self.engine) as session:
            deployment = session.get(StrategyDeploymentRecord, deployment_id)
            if deployment is None:
                raise KeyError(deployment_id)
            if deployment.status != "APPROVED":
                raise ValueError("Deployment job vyžaduje APPROVED deployment")
            existing = session.scalar(
                select(ScheduledJob).where(
                    ScheduledJob.job_type == JobType.RUN_PAPER_DEPLOYMENT,
                    ScheduledJob.config_json
                    == json.dumps({"deployment_id": deployment_id}, sort_keys=True),
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            account_id = deployment.paper_account_id
        return self.create_job(
            job_type=JobType.RUN_PAPER_DEPLOYMENT,
            account_id=account_id,
            strategy_id=None,
            config={"deployment_id": deployment_id},
            job_id=hashlib.sha256(f"paper-deployment:{deployment_id}".encode()).hexdigest(),
            **schedule,
        )

    def page(self, model: type[Any], limit: int = 50, offset: int = 0) -> list[Any]:
        order_column = model.created_at if hasattr(model, "created_at") else model.last_heartbeat_at
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(model).order_by(order_column.desc()).limit(limit).offset(offset)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows


class SchedulerService:
    def __init__(self, repository: AutomationRepository) -> None:
        self.repository = repository

    def tick(self, now: datetime | None = None, limit: int = 100) -> list[str]:
        now = utc(now or datetime.now(UTC))
        made: list[str] = []
        with Session(self.repository.engine) as session:
            query = (
                select(ScheduledJob)
                .where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
                .order_by(ScheduledJob.next_run_at, ScheduledJob.id)
                .limit(limit)
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            for job in session.scalars(query):
                scheduled_for = utc(job.next_run_at)
                too_old = now - scheduled_for > timedelta(seconds=job.misfire_grace_seconds)
                if not (too_old and job.misfire_policy == MisfirePolicy.SKIP_IF_TOO_OLD):
                    occurrence = f"scheduled:{scheduled_for.isoformat()}"
                    run_id = hashlib.sha256(f"{job.id}|{occurrence}".encode()).hexdigest()
                    run = JobRun(
                        id=run_id,
                        scheduled_job_id=job.id,
                        occurrence_key=occurrence,
                        scheduled_for=scheduled_for,
                        status=RunStatus.PENDING,
                        attempt_count=0,
                        fencing_token=0,
                        config_snapshot_json=self._snapshot(job),
                        correlation_id=run_id,
                        created_at=now,
                    )
                    try:
                        # Savepoint zachová zámek i změny schedule, pokud již occurrence
                        # vložila jiná transakce a databáze ohlásí konflikt.
                        with session.begin_nested():
                            session.add(run)
                            session.flush()
                        made.append(run_id)
                    except IntegrityError:
                        # Vnější transakce zůstává použitelná a schedule se posune níže.
                        pass
                job.last_run_at = scheduled_for
                following = next_occurrence(job, scheduled_for)
                while following <= now:
                    following = next_occurrence(job, following)
                job.next_run_at = following
                job.updated_at = now
            session.commit()
        return made

    def run_now(self, job_id: str, idempotency_key: str, now: datetime | None = None) -> str:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Neplatný idempotency key")
        now = utc(now or datetime.now(UTC))
        occurrence = f"manual:{idempotency_key}"
        run_id = hashlib.sha256(f"{job_id}|{occurrence}".encode()).hexdigest()
        with Session(self.repository.engine) as session:
            job = session.get(ScheduledJob, job_id)
            if job is None:
                raise KeyError(job_id)
            if not job.enabled:
                raise ValueError("Zakázaný job nelze spustit ručně")
            existing = session.get(JobRun, run_id)
            if existing:
                return existing.id
            session.add(
                JobRun(
                    id=run_id,
                    scheduled_job_id=job.id,
                    occurrence_key=occurrence,
                    scheduled_for=now,
                    status=RunStatus.PENDING,
                    attempt_count=0,
                    fencing_token=0,
                    config_snapshot_json=self._snapshot(job),
                    correlation_id=run_id,
                    created_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # Souběžný request mohl vložit stejnou deterministickou identitu.
                session.rollback()
                if session.get(JobRun, run_id) is None:
                    raise
        return run_id

    @staticmethod
    def _snapshot(job: ScheduledJob) -> str:
        return json.dumps(
            {
                "snapshot_version": 1,
                "identity": {
                    "account_id": job.account_id,
                    "job_type": job.job_type,
                    "strategy_id": job.strategy_id,
                },
                "config": json.loads(job.config_json),
            },
            sort_keys=True,
        )


class LeaseLost(RuntimeError):
    pass


class PermanentJobError(RuntimeError):
    pass


class TransientJobError(RuntimeError):
    pass


class JobExecutor:
    def __init__(self, repository: AutomationRepository) -> None:
        from quantlab.phase4 import Phase4Repository

        phase4 = Phase4Repository(
            repository.engine.url.render_as_string(hide_password=False),
            bootstrap_test_schema=False,
        )
        self.trading = TradingCycleService(phase4)
        self.reconciliation = ReconciliationService(phase4)

    def __call__(self, job: ScheduledJob, run: JobRun) -> dict[str, str | None]:
        snapshot = json.loads(run.config_snapshot_json)
        if not isinstance(snapshot, dict) or snapshot.get("snapshot_version") != 1:
            raise PermanentJobError("JobRun používá nepodporovaný legacy snapshot")
        identity = snapshot.get("identity")
        payload = snapshot.get("config")
        if not isinstance(identity, dict) or not isinstance(payload, dict):
            raise PermanentJobError("JobRun obsahuje neplatný immutable snapshot")
        account_id = identity.get("account_id")
        job_type = identity.get("job_type")
        strategy_id = identity.get("strategy_id")
        if (
            not isinstance(account_id, str)
            or not isinstance(job_type, str)
            or (strategy_id is not None and not isinstance(strategy_id, str))
        ):
            raise PermanentJobError("JobRun obsahuje neplatnou execution identitu")
        validate_payload(payload)
        if job_type == JobType.MONITOR_PAPER_DEPLOYMENT:
            monitoring_id = payload.get("monitoring_id")
            if not isinstance(monitoring_id, str) or not monitoring_id:
                raise PermanentJobError("Monitoring job nemá platné monitoring_id")
            from quantlab.phase6_runtime import ValidatedCurrentDataAccessor
            from quantlab.phase7 import (
                PaperPerformanceEvaluationService,
                PaperPerformanceService,
            )

            def sessions() -> Session:
                return Session(self.trading.repository.engine)

            performance = PaperPerformanceService(sessions, ValidatedCurrentDataAccessor(sessions))
            # Ledger účtu není historicky rekonstruovatelný. Opožděný job proto musí
            # ocenit skutečný stav v čase execution, nikdy jej backdatovat na occurrence.
            execution_time = datetime.now(UTC)
            snapshot = performance.capture(monitoring_id, execution_time)
            evaluation = PaperPerformanceEvaluationService(sessions).evaluate(
                monitoring_id, snapshot.snapshot_id, execution_time
            )
            return {
                "trading_cycle_id": None,
                "reconciliation_id": None,
                "outcome": evaluation.verdict,
            }
        if job_type == JobType.RUN_PAPER_DEPLOYMENT:
            return self._run_paper_deployment(account_id, payload)
        connection = self.trading.repository.engine.connect()
        advisory_lock_acquired = False
        try:
            if connection.dialect.name == "postgresql":
                connection.execute(select(func.pg_advisory_lock(func.hashtext(account_id))))
                advisory_lock_acquired = True
            if job_type == JobType.RUN_RECONCILIATION:
                result = self.reconciliation.reconcile(account_id)
                return {
                    "reconciliation_id": result.id,
                    "outcome": result.status,
                    "trading_cycle_id": None,
                }
            if job_type == JobType.RUN_PAPER_CYCLE:
                raise PermanentJobError(
                    "RUN_PAPER_CYCLE je legacy demo contract a production worker jej nespouští"
                )
            raise PermanentJobError("Neznámý job type")
        finally:
            if advisory_lock_acquired:
                connection.execute(select(func.pg_advisory_unlock(func.hashtext(account_id))))
            connection.close()

    def _run_paper_deployment(
        self, account_id: str, payload: dict[str, Any]
    ) -> dict[str, str | None]:
        from quantlab.market_data import DatasetInvalid
        from quantlab.persistence import StrategyDeploymentRecord
        from quantlab.phase6_runtime import (
            Phase6PaperExecutionService,
            ValidatedCurrentDataAccessor,
        )
        from quantlab.phase7 import PaperMonitoringRunRecord

        if set(payload) != {"deployment_id"} or not isinstance(payload["deployment_id"], str):
            raise PermanentJobError("RUN_PAPER_DEPLOYMENT přijímá pouze deployment_id")
        deployment_id = payload["deployment_id"]

        def sessions() -> Session:
            return Session(self.trading.repository.engine)

        with sessions() as session:
            deployment = session.get(StrategyDeploymentRecord, deployment_id)
            if deployment is None:
                raise PermanentJobError("Deployment neexistuje")
            if deployment.status != "APPROVED":
                raise PermanentJobError("Paper execution vyžaduje APPROVED deployment")
            if deployment.paper_account_id != account_id:
                raise PermanentJobError("Job account neodpovídá deployment lineage")
            monitoring_runs = list(
                session.scalars(
                    select(PaperMonitoringRunRecord)
                    .where(PaperMonitoringRunRecord.deployment_id == deployment_id)
                    .order_by(PaperMonitoringRunRecord.started_at.desc())
                )
            )
            open_monitoring = [
                item for item in monitoring_runs if item.state in {"ACTIVE", "PAUSED", "SUSPENDED"}
            ]
            if len(open_monitoring) != 1:
                if monitoring_runs and monitoring_runs[0].state == "RETIRED":
                    return {
                        "deployment_id": deployment_id,
                        "monitoring_id": monitoring_runs[0].monitoring_id,
                        "trading_cycle_id": None,
                        "reconciliation_id": None,
                        "outcome": "BLOCKED_BY_LIFECYCLE",
                        "no_action_reason": "MONITORING_RETIRED",
                    }
                raise PermanentJobError("Paper execution vyžaduje právě jeden monitoring context")
            monitoring = open_monitoring[0]
            if monitoring.state != "ACTIVE":
                return {
                    "deployment_id": deployment_id,
                    "monitoring_id": monitoring.monitoring_id,
                    "trading_cycle_id": None,
                    "reconciliation_id": None,
                    "outcome": "BLOCKED_BY_LIFECYCLE",
                    "no_action_reason": f"MONITORING_{monitoring.state}",
                }
        service = Phase6PaperExecutionService(
            sessions, ValidatedCurrentDataAccessor(sessions), self.trading
        )
        try:
            cycle_id = service.run(deployment_id, datetime.now(UTC))
        except DatasetInvalid as exc:
            message = str(exc)
            if (
                "executable session" in message
                or "Current data" in message
                or "observation" in message
            ):
                raise TransientJobError(message) from exc
            raise PermanentJobError(message) from exc
        with sessions() as session:
            persisted_monitoring = session.scalar(
                select(PaperMonitoringRunRecord).where(
                    PaperMonitoringRunRecord.deployment_id == deployment_id,
                    PaperMonitoringRunRecord.state == "ACTIVE",
                )
            )
            cycle = session.get(TradingCycleRecord, cycle_id)
            if persisted_monitoring is None or cycle is None:
                raise TransientJobError("Execution lineage po Phase 6 execution chybí")
            if cycle.account_id != account_id:
                raise PermanentJobError("Job account neodpovídá deployment lineage")
        return {
            "deployment_id": deployment_id,
            "monitoring_id": persisted_monitoring.monitoring_id,
            "trading_cycle_id": cycle_id,
            "reconciliation_id": None,
            "outcome": "EXECUTED",
            "no_action_reason": None,
        }


class WorkerService:
    def __init__(
        self,
        repository: AutomationRepository,
        settings: Settings,
        executor: Callable[[ScheduledJob, JobRun], dict[str, str | None]] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.executor = executor or JobExecutor(repository)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        self.stop_event = Event()

    def heartbeat(
        self, run_id: str | None = None, token: int | None = None, now: datetime | None = None
    ) -> bool:
        now = utc(now or datetime.now(UTC))
        expires = now + timedelta(seconds=self.settings.worker_lease_timeout)
        with Session(self.repository.engine) as session:
            heartbeat = session.get(WorkerHeartbeat, self.worker_id)
            if heartbeat is None:
                heartbeat = WorkerHeartbeat(
                    worker_id=self.worker_id, started_at=now, last_heartbeat_at=now
                )
                session.add(heartbeat)
            heartbeat.last_heartbeat_at = now
            heartbeat.active_run_id = run_id
            renewed = True
            if run_id and token is not None:
                result = session.execute(
                    update(JobRun)
                    .where(
                        JobRun.id == run_id,
                        JobRun.lease_owner == self.worker_id,
                        JobRun.fencing_token == token,
                        JobRun.status == RunStatus.RUNNING,
                        JobRun.lease_expires_at > now,
                    )
                    .values(lease_expires_at=expires)
                )
                renewed = isinstance(result, CursorResult) and result.rowcount == 1
            session.commit()
            return renewed

    def claim(self, now: datetime | None = None) -> tuple[str, int] | None:
        if not self.settings.automation_enabled or self.stop_event.is_set():
            return None
        now = utc(now or datetime.now(UTC))
        expires = now + timedelta(seconds=self.settings.worker_lease_timeout)
        with Session(self.repository.engine) as session:
            query = (
                select(JobRun)
                .where(
                    or_(
                        and_(
                            JobRun.status.in_([RunStatus.PENDING, RunStatus.RETRY_SCHEDULED]),
                            or_(JobRun.next_attempt_at.is_(None), JobRun.next_attempt_at <= now),
                        ),
                        and_(
                            JobRun.status.in_([RunStatus.CLAIMED, RunStatus.RUNNING]),
                            JobRun.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(JobRun.scheduled_for, JobRun.created_at, JobRun.id)
                .limit(1)
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            run = session.scalar(query)
            if run is None:
                return None
            if run.status in {RunStatus.CLAIMED, RunStatus.RUNNING} and run.attempt_count > 0:
                superseded = session.scalar(
                    select(JobAttempt).where(
                        JobAttempt.job_run_id == run.id,
                        JobAttempt.attempt_number == run.attempt_count,
                        JobAttempt.status == AttemptStatus.RUNNING,
                    )
                )
                if superseded is not None:
                    superseded.status = AttemptStatus.LEASE_LOST
                    superseded.finished_at = now
                    superseded.error_type = "LEASE_LOST"
                    superseded.error_message = "Lease expiroval a run převzal jiný worker"
                    superseded.retryable = True
            run.fencing_token += 1
            run.lease_owner = self.worker_id
            run.lease_acquired_at = now
            run.lease_expires_at = expires
            run.status = RunStatus.RUNNING
            run.attempt_count += 1
            session.add(
                JobAttempt(
                    id=str(uuid4()),
                    job_run_id=run.id,
                    attempt_number=run.attempt_count,
                    started_at=now,
                    worker_id=self.worker_id,
                    fencing_token=run.fencing_token,
                    status=AttemptStatus.RUNNING,
                    correlation_id=run.correlation_id,
                )
            )
            session.commit()
            return run.id, run.fencing_token

    def finish(
        self, run_id: str, token: int, result: dict[str, str | None], now: datetime | None = None
    ) -> None:
        now = utc(now or datetime.now(UTC))
        with Session(self.repository.engine) as session:
            run = session.scalar(
                select(JobRun)
                .where(
                    JobRun.id == run_id,
                    JobRun.lease_owner == self.worker_id,
                    JobRun.fencing_token == token,
                    JobRun.status == RunStatus.RUNNING,
                    JobRun.lease_expires_at > now,
                )
                .with_for_update()
            )
            if run is None:
                raise LeaseLost("Stale worker nesmí dokončit převzatý run")
            attempt = session.scalar(
                select(JobAttempt).where(
                    JobAttempt.job_run_id == run_id, JobAttempt.attempt_number == run.attempt_count
                )
            )
            if attempt is None:
                raise RuntimeError("Aktivní automation attempt nebyl nalezen")
            run.status = RunStatus.SUCCEEDED
            run.finished_at = now
            run.outcome = result.get("outcome")
            run.trading_cycle_id = result.get("trading_cycle_id")
            run.deployment_id = result.get("deployment_id")
            run.monitoring_id = result.get("monitoring_id")
            run.reconciliation_id = result.get("reconciliation_id")
            run.no_action_reason = result.get("no_action_reason")
            run.lease_owner = None
            run.lease_expires_at = None
            attempt.status = AttemptStatus.SUCCEEDED
            attempt.finished_at = now
            session.commit()

    def fail(self, run_id: str, token: int, exc: Exception, now: datetime | None = None) -> None:
        now = utc(now or datetime.now(UTC))
        retryable = isinstance(
            exc, (OperationalError, DBAPIError, TimeoutError, ConnectionError, TransientJobError)
        )
        with Session(self.repository.engine) as session:
            run = session.scalar(
                select(JobRun)
                .where(
                    JobRun.id == run_id,
                    JobRun.lease_owner == self.worker_id,
                    JobRun.fencing_token == token,
                    JobRun.status == RunStatus.RUNNING,
                    JobRun.lease_expires_at > now,
                )
                .with_for_update()
            )
            if run is None:
                raise LeaseLost("Lease byl ztracen")
            job = session.get(ScheduledJob, run.scheduled_job_id)
            if job is None:
                raise RuntimeError("Automation job nebyl nalezen")
            attempt = session.scalar(
                select(JobAttempt).where(
                    JobAttempt.job_run_id == run_id, JobAttempt.attempt_number == run.attempt_count
                )
            )
            if attempt is None:
                raise RuntimeError("Aktivní automation attempt nebyl nalezen")
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = now
            attempt.error_type = type(exc).__name__
            attempt.error_message = str(exc)[:2000]
            attempt.retryable = retryable
            if retryable and run.attempt_count < job.max_attempts:
                delay = min(
                    self.settings.retry_base_delay * 2 ** (run.attempt_count - 1),
                    self.settings.retry_max_delay,
                )
                run.status = RunStatus.RETRY_SCHEDULED
                run.next_attempt_at = now + timedelta(seconds=delay)
            else:
                run.status = (
                    RunStatus.DEAD_LETTER
                    if run.attempt_count >= job.max_attempts
                    else RunStatus.FAILED
                )
                run.finished_at = now
            run.failure_reason = f"{type(exc).__name__}: {str(exc)[:1900]}"
            run.lease_owner = None
            run.lease_expires_at = None
            session.commit()

    def execute_one(self, now: datetime | None = None) -> str | None:
        claimed = self.claim(now)
        if claimed is None:
            return None
        run_id, token = claimed
        try:
            with Session(self.repository.engine) as session:
                run = session.get(JobRun, run_id)
                if run is None:
                    raise RuntimeError("Automation run nebyl nalezen")
                job = session.get(ScheduledJob, run.scheduled_job_id)
                if job is None:
                    raise RuntimeError("Automation job nebyl nalezen")
                session.expunge(run)
                session.expunge(job)
            heartbeat_stop = Event()
            heartbeat_failed = Event()

            def renew() -> None:
                while not heartbeat_stop.wait(self.settings.worker_heartbeat_interval):
                    try:
                        renewed = self.heartbeat(run_id, token)
                    except Exception:
                        heartbeat_failed.set()
                        return
                    if not renewed:
                        heartbeat_failed.set()
                        return

            heartbeat_thread = Thread(target=renew, daemon=True)
            heartbeat_thread.start()
            try:
                result = self.executor(job, run)
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join()
            if heartbeat_failed.is_set():
                raise LeaseLost("Heartbeat selhal nebo lease mezitím ztratil vlastníka")
            self.finish(run_id, token, result, now)
        except Exception as exc:
            if isinstance(exc, LeaseLost):
                raise
            self.fail(run_id, token, exc, now)
        return run_id

    def retry(self, run_id: str, now: datetime | None = None) -> None:
        if not self.settings.automation_enabled:
            raise ValueError("Automation je globálně vypnutá")
        now = utc(now or datetime.now(UTC))
        with Session(self.repository.engine) as session:
            run = session.scalar(select(JobRun).where(JobRun.id == run_id).with_for_update())
            if run is None:
                raise KeyError(run_id)
            if run.status == RunStatus.SUCCEEDED:
                raise ValueError("Úspěšný run nelze opakovat")
            if run.status not in {RunStatus.FAILED, RunStatus.DEAD_LETTER}:
                raise ValueError("Run není v retry stavu")
            run.status = RunStatus.RETRY_SCHEDULED
            run.next_attempt_at = now
            run.finished_at = None
            session.commit()

    def request_stop(self) -> None:
        self.stop_event.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.request_stop())
        signal.signal(signal.SIGINT, lambda *_: self.request_stop())
