from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.automation import JobRun, ScheduledJob, WorkerHeartbeat
from quantlab.config import Settings
from quantlab.market_data import XNYSCalendar
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
)
from quantlab.phase4 import (
    AuditEventRecord,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    PositionRecord,
    ProductionRiskConfig,
    ReconciliationRecord,
    RiskDecisionRecord,
    RiskEventRecord,
    TradingCycleRecord,
)
from quantlab.phase7 import (
    PaperExpectationBaselineRecord,
    PaperMonitoringPolicyRecord,
    PaperMonitoringRunRecord,
    PaperPerformanceEvaluationRecord,
    PaperPerformanceSnapshotRecord,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _json(value: str) -> Any:
    return json.loads(value)


def _row(row: object) -> dict[str, Any]:
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


class OperatorReadModel:
    """Stabilní, read-only projekce perzistovaných provozních důkazů."""

    def __init__(self, session_factory: Any, settings: Settings):
        self._session_factory = session_factory
        self._settings = settings

    def _latest_monitoring(self, session: Session) -> PaperMonitoringRunRecord | None:
        return session.scalar(
            select(PaperMonitoringRunRecord)
            .order_by(PaperMonitoringRunRecord.created_at.desc())
            .limit(1)
        )

    def _latest_snapshot(
        self, session: Session, monitoring_id: str | None = None
    ) -> PaperPerformanceSnapshotRecord | None:
        query = select(PaperPerformanceSnapshotRecord)
        if monitoring_id:
            query = query.where(PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id)
        return session.scalar(
            query.order_by(PaperPerformanceSnapshotRecord.session_date.desc()).limit(1)
        )

    def _latest_evaluation(
        self, session: Session, monitoring_id: str | None = None
    ) -> PaperPerformanceEvaluationRecord | None:
        query = select(PaperPerformanceEvaluationRecord)
        if monitoring_id:
            query = query.where(PaperPerformanceEvaluationRecord.monitoring_id == monitoring_id)
        return session.scalar(
            query.order_by(PaperPerformanceEvaluationRecord.created_at.desc()).limit(1)
        )

    def overview(self, now: datetime) -> dict[str, Any]:
        calendar = XNYSCalendar()
        completed = calendar.latest_completed_session(now)
        with self._session_factory() as session:
            account = session.get(PaperAccountRecord, "paper-main")
            monitoring = self._latest_monitoring(session)
            snapshot = self._latest_snapshot(
                session, monitoring.monitoring_id if monitoring else None
            )
            evaluation = self._latest_evaluation(
                session, monitoring.monitoring_id if monitoring else None
            )
            reconciliation = session.scalar(
                select(ReconciliationRecord)
                .order_by(ReconciliationRecord.timestamp.desc())
                .limit(1)
            )
            cycle = session.scalar(
                select(TradingCycleRecord).order_by(TradingCycleRecord.started_at.desc()).limit(1)
            )
            ingestion = session.scalar(
                select(MarketDataIngestionRecord)
                .order_by(MarketDataIngestionRecord.started_at.desc())
                .limit(1)
            )
            next_job = session.scalar(
                select(ScheduledJob.next_run_at)
                .where(ScheduledJob.enabled.is_(True), ScheduledJob.job_type == "RUN_PAPER_CYCLE")
                .order_by(ScheduledJob.next_run_at)
                .limit(1)
            )
            worker_cutoff = now - timedelta(seconds=self._settings.worker_lease_timeout)
            healthy = (
                session.scalar(
                    select(func.count())
                    .select_from(WorkerHeartbeat)
                    .where(WorkerHeartbeat.last_heartbeat_at >= worker_cutoff)
                )
                or 0
            )
            workers = session.scalar(select(func.count()).select_from(WorkerHeartbeat)) or 0
            return {
                "server_time_utc": now,
                "trading_mode": "PAPER",
                "live_trading_enabled": False,
                "api_health": "ok",
                "readiness": "ready",
                "paper_account_id": account.id if account else None,
                "trading_state": account.trading_state if account else None,
                "reconciliation_safe": account.reconciliation_safe if account else None,
                "latest_reconciliation_status": reconciliation.status if reconciliation else None,
                "monitoring_id": monitoring.monitoring_id if monitoring else None,
                "monitoring_state": monitoring.state if monitoring else None,
                "monitoring_verdict": evaluation.verdict if evaluation else None,
                "paper_equity": snapshot.marked_equity if snapshot else None,
                "paper_cash": snapshot.cash if snapshot else (account.cash if account else None),
                "cumulative_return": snapshot.cumulative_return if snapshot else None,
                "current_drawdown": snapshot.drawdown if snapshot else None,
                "position_count": snapshot.position_count if snapshot else 0,
                "open_order_count": session.scalar(
                    select(func.count())
                    .select_from(PaperOrderRecord)
                    .where(PaperOrderRecord.status.in_(("NEW", "SUBMITTED", "PARTIALLY_FILLED")))
                )
                or 0,
                "last_trading_cycle": _utc(cycle.completed_at or cycle.started_at)
                if cycle
                else None,
                "next_scheduled_paper_cycle": _utc(next_job),
                "latest_completed_market_session": completed,
                "latest_market_data_status": ingestion.status if ingestion else None,
                "latest_market_data_at": _utc(ingestion.finished_at or ingestion.started_at)
                if ingestion
                else None,
                "automation_enabled": self._settings.automation_enabled,
                "enabled_job_count": session.scalar(
                    select(func.count())
                    .select_from(ScheduledJob)
                    .where(ScheduledJob.enabled.is_(True))
                )
                or 0,
                "dead_letter_count": session.scalar(
                    select(func.count()).select_from(JobRun).where(JobRun.status == "DEAD_LETTER")
                )
                or 0,
                "healthy_worker_count": healthy,
                "stale_worker_count": workers - healthy,
                "as_of": snapshot.as_of if snapshot else (account.updated_at if account else None),
            }

    def performance(
        self, period: str, now: datetime, monitoring_id: str | None = None
    ) -> dict[str, Any]:
        calendar = XNYSCalendar()
        end = calendar.latest_completed_session(now)
        starts = {
            "1M": end - timedelta(days=31),
            "3M": end - timedelta(days=93),
            "6M": end - timedelta(days=186),
            "1Y": end - timedelta(days=366),
            "YTD": date(end.year, 1, 1),
        }
        with self._session_factory() as session:
            monitoring = (
                session.get(PaperMonitoringRunRecord, monitoring_id)
                if monitoring_id
                else self._latest_monitoring(session)
            )
            query = select(PaperPerformanceSnapshotRecord)
            if monitoring:
                query = query.where(
                    PaperPerformanceSnapshotRecord.monitoring_id == monitoring.monitoring_id
                )
            else:
                return {"period": period, "monitoring_id": None, "points": []}
            if period != "ALL":
                query = query.where(PaperPerformanceSnapshotRecord.session_date >= starts[period])
            rows = session.scalars(
                query.order_by(PaperPerformanceSnapshotRecord.session_date)
            ).all()
            return {
                "period": period,
                "monitoring_id": monitoring.monitoring_id,
                "points": [
                    {
                        "session_date": r.session_date,
                        "as_of": r.as_of,
                        "marked_equity": r.marked_equity,
                        "cash": r.cash,
                        "daily_return": r.daily_return,
                        "cumulative_return": r.cumulative_return,
                        "drawdown": r.drawdown,
                        "gross_exposure": r.gross_exposure,
                        "net_exposure": r.net_exposure,
                        "turnover": r.cumulative_turnover,
                        "commissions": r.cumulative_commissions,
                        "slippage": r.cumulative_slippage_cost,
                        "order_count": r.order_count,
                        "fill_count": r.fill_count,
                        "risk_rejection_count": r.risk_rejection_count,
                    }
                    for r in rows
                ],
            }

    def paper(self) -> dict[str, Any]:
        with self._session_factory() as session:
            account = session.get(PaperAccountRecord, "paper-main")
            monitoring = self._latest_monitoring(session)
            snapshot = self._latest_snapshot(
                session, monitoring.monitoring_id if monitoring else None
            )
            evaluation = self._latest_evaluation(
                session, monitoring.monitoring_id if monitoring else None
            )
            reconciliation = session.scalar(
                select(ReconciliationRecord)
                .order_by(ReconciliationRecord.timestamp.desc())
                .limit(1)
            )
            return {
                "account": _row(account) if account else None,
                "marked_equity": snapshot.marked_equity if snapshot else None,
                "positions": [
                    _row(x)
                    for x in session.scalars(
                        select(PositionRecord).order_by(PositionRecord.instrument_id)
                    )
                ],
                "orders": [
                    _row(x)
                    for x in session.scalars(
                        select(PaperOrderRecord)
                        .order_by(PaperOrderRecord.created_at.desc())
                        .limit(100)
                    )
                ],
                "fills": [
                    _row(x)
                    for x in session.scalars(
                        select(PaperFillRecord)
                        .order_by(PaperFillRecord.timestamp.desc())
                        .limit(100)
                    )
                ],
                "latest_reconciliation": _row(reconciliation) if reconciliation else None,
                "monitoring": _row(monitoring) if monitoring else None,
                "latest_evaluation": _row(evaluation) if evaluation else None,
                "as_of": snapshot.as_of if snapshot else (account.updated_at if account else None),
            }

    def risk(self) -> dict[str, Any]:
        config = ProductionRiskConfig()
        with self._session_factory() as session:
            account = session.get(PaperAccountRecord, "paper-main")
            snapshot = self._latest_snapshot(session)
            return {
                "trading_state": account.trading_state if account else None,
                "reconciliation_safe": account.reconciliation_safe if account else None,
                "marked_equity": snapshot.marked_equity if snapshot else None,
                "current_drawdown": snapshot.drawdown if snapshot else None,
                "gross_exposure": snapshot.gross_exposure if snapshot else None,
                "net_exposure": snapshot.net_exposure if snapshot else None,
                "position_count": snapshot.position_count if snapshot else 0,
                "limits": {k: v for k, v in vars(config).items()},
                "decisions": [
                    _row(x)
                    for x in session.scalars(
                        select(RiskDecisionRecord)
                        .order_by(RiskDecisionRecord.timestamp.desc())
                        .limit(50)
                    )
                ],
                "events": [
                    _row(x)
                    for x in session.scalars(
                        select(RiskEventRecord).order_by(RiskEventRecord.timestamp.desc()).limit(50)
                    )
                ],
            }

    def data_health(self, now: datetime) -> dict[str, Any]:
        completed = XNYSCalendar().latest_completed_session(now)
        with self._session_factory() as session:
            ingestions = list(
                session.scalars(
                    select(MarketDataIngestionRecord)
                    .order_by(MarketDataIngestionRecord.started_at.desc())
                    .limit(50)
                )
            )
            snapshots = list(
                session.scalars(
                    select(DatasetSnapshotRecord)
                    .order_by(DatasetSnapshotRecord.created_at.desc())
                    .limit(50)
                )
            )
            latest_success = next((x for x in ingestions if x.status == "SUCCEEDED"), None)
            return {
                "provider": {"name": "stooq", "type": "persistent"},
                "calendar_identity": XNYSCalendar().identity,
                "latest_completed_session": completed,
                "latest_successful_session": latest_success.requested_end.date()
                if latest_success
                else None,
                "fresh": bool(latest_success and latest_success.requested_end.date() >= completed),
                "instruments": [
                    _row(x)
                    for x in session.scalars(
                        select(InstrumentRecord).order_by(InstrumentRecord.instrument_id)
                    )
                ],
                "ingestions": [_row(x) for x in ingestions],
                "snapshots": [_row(x) for x in snapshots],
            }

    def automation(self, now: datetime) -> dict[str, Any]:
        cutoff = now - timedelta(seconds=self._settings.worker_lease_timeout)
        with self._session_factory() as session:
            workers = list(
                session.scalars(
                    select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_heartbeat_at.desc())
                )
            )
            return {
                "enabled": self._settings.automation_enabled,
                "jobs": [
                    _row(x)
                    for x in session.scalars(
                        select(ScheduledJob).order_by(ScheduledJob.next_run_at)
                    )
                ],
                "runs": [
                    _row(x)
                    for x in session.scalars(
                        select(JobRun).order_by(JobRun.created_at.desc()).limit(100)
                    )
                ],
                "workers": [
                    {
                        **_row(x),
                        "state": (
                            "HEALTHY"
                            if (heartbeat := _utc(x.last_heartbeat_at)) is not None
                            and heartbeat >= cutoff
                            else "STALE"
                        ),
                    }
                    for x in workers
                ],
            }

    def audit(
        self,
        *,
        limit: int,
        offset: int,
        event_type: str | None,
        entity_type: str | None,
        entity_id: str | None,
        correlation_id: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            query = select(AuditEventRecord)
            count = select(func.count()).select_from(AuditEventRecord)
            conditions = []
            for column, value in (
                (AuditEventRecord.event_type, event_type),
                (AuditEventRecord.entity_type, entity_type),
                (AuditEventRecord.entity_id, entity_id),
                (AuditEventRecord.correlation_id, correlation_id),
            ):
                if value:
                    conditions.append(column == value)
            if start:
                conditions.append(AuditEventRecord.timestamp >= start)
            if end:
                conditions.append(AuditEventRecord.timestamp <= end)
            query = query.where(*conditions)
            count = count.where(*conditions)
            rows = session.scalars(
                query.order_by(AuditEventRecord.timestamp.desc()).limit(limit).offset(offset)
            )
            return {
                "items": [{**_row(x), "payload": _json(x.payload_json)} for x in rows],
                "total": session.scalar(count) or 0,
                "limit": limit,
                "offset": offset,
            }

    def strategies(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return [
                _row(x)
                for x in session.scalars(
                    select(StrategyRecord).order_by(StrategyRecord.name, StrategyRecord.version)
                )
            ]

    def strategy(self, identity: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(StrategyRecord, identity)
            if not row:
                return None
            experiments = list(
                session.scalars(
                    select(ExperimentRecord)
                    .where(ExperimentRecord.strategy_identity == identity)
                    .order_by(ExperimentRecord.created_at.desc())
                )
            )
            deployments = list(
                session.scalars(
                    select(StrategyDeploymentRecord)
                    .where(StrategyDeploymentRecord.strategy_identity == identity)
                    .order_by(StrategyDeploymentRecord.created_at.desc())
                )
            )
            return {
                **_row(row),
                "experiments": [_row(x) for x in experiments],
                "deployments": [_row(x) for x in deployments],
            }

    def experiments(self, limit: int, offset: int) -> dict[str, Any]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperimentRecord)
                .order_by(ExperimentRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return {
                "items": [_row(x) for x in rows],
                "total": session.scalar(select(func.count()).select_from(ExperimentRecord)) or 0,
                "limit": limit,
                "offset": offset,
            }

    def experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExperimentRecord, experiment_id)
            return _row(row) if row else None

    def comparison(self, monitoring_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            run = session.get(PaperMonitoringRunRecord, monitoring_id)
            if not run:
                return None
            baseline = session.get(PaperExpectationBaselineRecord, run.baseline_id)
            policy = session.get(PaperMonitoringPolicyRecord, run.policy_id)
            evaluations = list(
                session.scalars(
                    select(PaperPerformanceEvaluationRecord)
                    .where(PaperPerformanceEvaluationRecord.monitoring_id == monitoring_id)
                    .order_by(PaperPerformanceEvaluationRecord.created_at)
                )
            )
            snapshots = list(
                session.scalars(
                    select(PaperPerformanceSnapshotRecord)
                    .where(PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id)
                    .order_by(PaperPerformanceSnapshotRecord.session_date)
                )
            )
            return {
                "monitoring": _row(run),
                "policy": {**_row(policy), "config": _json(policy.config_json)} if policy else None,
                "baseline": {
                    **_row(baseline),
                    "oos_metrics": _json(baseline.oos_metrics_json),
                    "oos_returns": _json(baseline.oos_returns_json),
                    "oos_equity": _json(baseline.oos_equity_json),
                }
                if baseline
                else None,
                "paper_points": [
                    {
                        "index": i + 1,
                        "session_date": x.session_date,
                        "cumulative_return": x.cumulative_return,
                        "marked_equity": x.marked_equity,
                    }
                    for i, x in enumerate(snapshots)
                ],
                "evaluations": [
                    {
                        **_row(x),
                        "reasons": _json(x.reasons_json),
                        "paper_metrics": _json(x.paper_metrics_json),
                        "baseline_comparison": _json(x.baseline_comparison_json),
                    }
                    for x in evaluations
                ],
            }
