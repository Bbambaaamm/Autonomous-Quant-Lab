from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.domain import SystemTradingState, require_utc
from quantlab.market_data import CorporateActionKind, DatasetInvalid, XNYSCalendar
from quantlab.market_data_service import _database_utc
from quantlab.persistence import (
    Base,
    CorporateActionRecord,
    ExperimentRecord,
    StrategyDeploymentRecord,
)
from quantlab.phase4 import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    PositionRecord,
    ReconciliationRecord,
    RiskDecisionRecord,
    TradingCycleRecord,
)
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6ExperimentReplayService,
    Phase6ExperimentRequest,
    ValidatedCurrentDataAccessor,
)

ALGORITHM_VERSION = "paper-monitoring-v1"
OPEN_STATES = ("ACTIVE", "PAUSED", "SUSPENDED")


class MonitoringState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class EvaluationVerdict(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SUSPENDED = "SUSPENDED"


class PaperMonitoringPolicyRecord(Base):
    __tablename__ = "paper_monitoring_policies"
    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)


class PaperExpectationBaselineRecord(Base):
    __tablename__ = "paper_expectation_baselines"
    baseline_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_deployments.deployment_id", ondelete="RESTRICT"), unique=True
    )
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    strategy_identity: Mapped[str] = mapped_column(
        ForeignKey("strategies.strategy_identity", ondelete="RESTRICT"), nullable=False
    )
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    code_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_json: Mapped[str] = mapped_column(Text, nullable=False)
    oos_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    oos_returns_json: Mapped[str] = mapped_column(Text, nullable=False)
    oos_equity_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    oos_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperMonitoringRunRecord(Base):
    __tablename__ = "paper_monitoring_runs"
    monitoring_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_deployments.deployment_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    paper_account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("paper_monitoring_policies.policy_id", ondelete="RESTRICT"), nullable=False
    )
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("paper_expectation_baselines.baseline_id", ondelete="RESTRICT"), nullable=False
    )
    starting_equity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state_reason: Mapped[str] = mapped_column(Text, nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("deployment_id", "started_at"),)


class PaperPerformanceSnapshotRecord(Base):
    __tablename__ = "paper_performance_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monitoring_id: Mapped[str] = mapped_column(
        ForeignKey("paper_monitoring_runs.monitoring_id", ondelete="RESTRICT"), nullable=False
    )
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_deployments.deployment_id", ondelete="RESTRICT"), nullable=False
    )
    paper_account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calendar_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    marked_equity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    gross_exposure: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    net_exposure: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_return: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    cumulative_return: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    cumulative_turnover: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cumulative_commissions: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cumulative_slippage_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_count: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_rejection_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    trading_state: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        UniqueConstraint("monitoring_id", "session_date"),
        Index("ix_performance_monitoring_session", "monitoring_id", "session_date"),
        Index("ix_performance_deployment_session", "deployment_id", "session_date"),
    )


class PaperPerformanceEvaluationRecord(Base):
    __tablename__ = "paper_performance_evaluations"
    evaluation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monitoring_id: Mapped[str] = mapped_column(
        ForeignKey("paper_monitoring_runs.monitoring_id", ondelete="RESTRICT"), nullable=False
    )
    performance_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("paper_performance_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("paper_monitoring_policies.policy_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paper_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    paper_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_comparison_json: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        UniqueConstraint(
            "monitoring_id", "performance_snapshot_id", "policy_id", "algorithm_version"
        ),
        Index("ix_evaluation_monitoring_created", "monitoring_id", "created_at"),
    )


class PaperDeploymentCycleRecord(Base):
    __tablename__ = "paper_deployment_cycles"
    lineage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monitoring_id: Mapped[str] = mapped_column(
        ForeignKey("paper_monitoring_runs.monitoring_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_deployments.deployment_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trading_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("trading_cycles.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperCorporateActionApplicationRecord(Base):
    __tablename__ = "paper_corporate_action_applications"
    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("corporate_actions.action_id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effect_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "action_id"),)


class PaperCorporateActionService:
    """Aplikuje kauzální corporate actions přímo na paper ledger, bez order/fill cesty."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def apply(
        self, account_id: str, as_of: datetime
    ) -> tuple[PaperCorporateActionApplicationRecord, ...]:
        cutoff = require_utc(as_of)
        applied: list[PaperCorporateActionApplicationRecord] = []
        with self.sessions() as session, session.begin():
            account = session.scalar(
                select(PaperAccountRecord)
                .where(PaperAccountRecord.id == account_id)
                .with_for_update()
            )
            if account is None:
                raise DatasetInvalid("Paper účet neexistuje")
            actions = tuple(
                session.scalars(
                    select(CorporateActionRecord)
                    .where(
                        CorporateActionRecord.known_at <= cutoff,
                        CorporateActionRecord.effective_at <= cutoff,
                        CorporateActionRecord.effective_at >= account.created_at,
                    )
                    .order_by(CorporateActionRecord.effective_at, CorporateActionRecord.action_id)
                )
            )
            for action in actions:
                existing = session.scalar(
                    select(PaperCorporateActionApplicationRecord).where(
                        PaperCorporateActionApplicationRecord.account_id == account_id,
                        PaperCorporateActionApplicationRecord.action_id == action.action_id,
                    )
                )
                if existing is not None:
                    continue
                position = session.scalar(
                    select(PositionRecord)
                    .where(
                        PositionRecord.account_id == account_id,
                        PositionRecord.instrument_id == action.instrument_id,
                    )
                    .with_for_update()
                )
                fill_rows = tuple(
                    session.execute(
                        select(PaperFillRecord.quantity, PaperOrderRecord.side)
                        .join(PaperOrderRecord, PaperOrderRecord.id == PaperFillRecord.order_id)
                        .where(
                            PaperOrderRecord.account_id == account_id,
                            PaperOrderRecord.instrument_id == action.instrument_id,
                            PaperFillRecord.timestamp <= action.effective_at,
                        )
                    )
                )
                historical_quantity = sum(
                    (quantity if side == "BUY" else -quantity for quantity, side in fill_rows),
                    Decimal(0),
                )
                if (
                    not fill_rows
                    and position is not None
                    and position.updated_at <= action.effective_at
                ):
                    historical_quantity = position.quantity
                quantity = max(historical_quantity, Decimal(0))
                kind = CorporateActionKind(action.kind)
                effect: dict[str, object] = {"kind": kind, "eligible_quantity": quantity}
                if kind is CorporateActionKind.SPLIT and position is not None:
                    ratio = Decimal(action.value or "0")
                    if ratio <= 0:
                        raise DatasetInvalid("Split ratio musí být kladné")
                    lots: object = json.loads(position.lots_json)
                    if not isinstance(lots, list) or any(not isinstance(lot, dict) for lot in lots):
                        raise DatasetInvalid("Paper position lots nejsou validní")
                    adjusted_quantity = Decimal(0)
                    for lot in cast(list[dict[str, object]], lots):
                        lot_quantity = Decimal(str(lot["quantity"]))
                        unit_basis = Decimal(str(lot["unit_basis"]))
                        acquired_at = lot.get("acquired_at")
                        eligible = acquired_at is None or require_utc(
                            datetime.fromisoformat(str(acquired_at))
                        ) <= _database_utc(action.effective_at)
                        if eligible:
                            lot_quantity *= ratio
                            unit_basis /= ratio
                            lot["quantity"] = str(lot_quantity)
                            lot["unit_basis"] = str(unit_basis)
                        adjusted_quantity += lot_quantity
                    position.quantity = adjusted_quantity
                    position.lots_json = canonical(lots)
                    position.average_cost = (
                        sum(
                            Decimal(str(lot["quantity"])) * Decimal(str(lot["unit_basis"]))
                            for lot in cast(list[dict[str, object]], lots)
                        )
                        / adjusted_quantity
                        if adjusted_quantity
                        else Decimal(0)
                    )
                    position.updated_at = cutoff
                    effect.update({"ratio": ratio, "quantity_after": position.quantity})
                elif kind is CorporateActionKind.CASH_DIVIDEND:
                    dividend = Decimal(action.value or "0")
                    if dividend <= 0:
                        raise DatasetInvalid("Dividend amount musí být kladný")
                    credit = quantity * dividend
                    account.cash += credit
                    account.equity += credit
                    account.updated_at = cutoff
                    effect.update({"per_share": dividend, "cash_credit": credit})
                elif (
                    kind is CorporateActionKind.DELISTING
                    and position is not None
                    and position.quantity
                ):
                    run = session.scalar(
                        select(PaperMonitoringRunRecord)
                        .where(
                            PaperMonitoringRunRecord.paper_account_id == account_id,
                            PaperMonitoringRunRecord.state.in_(OPEN_STATES),
                        )
                        .with_for_update()
                    )
                    if run is not None:
                        run.state = MonitoringState.SUSPENDED
                        run.state_reason = "DELISTING_UNSUPPORTED"
                        run.state_changed_at = cutoff
                    effect["resolution"] = "SUSPENDED_NO_SYNTHETIC_FILL"
                elif kind is CorporateActionKind.SYMBOL_CHANGE:
                    effect.update(
                        {"canonical_instrument_unchanged": True, "new_symbol": action.new_symbol}
                    )
                row = PaperCorporateActionApplicationRecord(
                    application_id=identity(
                        {"account_id": account_id, "action_id": action.action_id}
                    ),
                    action_id=action.action_id,
                    account_id=account_id,
                    instrument_id=action.instrument_id,
                    applied_at=cutoff,
                    effect_json=canonical(effect),
                )
                session.add(row)
                session.flush()
                applied.append(row)
            for row in applied:
                session.expunge(row)
        return tuple(applied)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def identity(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


DEFAULT_POLICY: dict[str, object] = {
    "minimum_sessions": 20,
    "bootstrap_samples": 1000,
    "bootstrap_block_size": 5,
    "watch_return_percentile": 10,
    "review_return_percentile": 2,
    "maximum_paper_drawdown": "0.20",
    "hard_suspend_on_halted": True,
    "hard_suspend_on_reconciliation_failure": True,
}


def validate_policy(config: dict[str, object]) -> None:
    required = set(DEFAULT_POLICY)
    if set(config) != required:
        raise ValueError("Monitoring policy musí obsahovat přesně allowlisted položky")
    integers = (
        "minimum_sessions",
        "bootstrap_samples",
        "bootstrap_block_size",
        "watch_return_percentile",
        "review_return_percentile",
    )
    if any(isinstance(config[key], bool) or not isinstance(config[key], int) for key in integers):
        raise ValueError("Monitoring policy má neplatný integer")
    minimum_sessions = cast(int, config["minimum_sessions"])
    bootstrap_samples = cast(int, config["bootstrap_samples"])
    bootstrap_block_size = cast(int, config["bootstrap_block_size"])
    review_percentile = cast(int, config["review_return_percentile"])
    watch_percentile = cast(int, config["watch_return_percentile"])
    if (
        not 3 <= minimum_sessions <= 5000
        or not 100 <= bootstrap_samples <= 100000
        or not 1 <= bootstrap_block_size <= 252
    ):
        raise ValueError("Monitoring policy je mimo bezpečné meze")
    if not 0 <= review_percentile < watch_percentile <= 50:
        raise ValueError("Monitoring percentily nejsou konzistentní")
    drawdown = Decimal(str(config["maximum_paper_drawdown"]))
    if not Decimal("0.01") <= drawdown <= Decimal("0.80"):
        raise ValueError("Hard drawdown je mimo bezpečné meze")
    if (
        config["hard_suspend_on_halted"] is not True
        or config["hard_suspend_on_reconciliation_failure"] is not True
    ):
        raise ValueError("Fail-closed safety gates nelze vypnout")


class PaperMonitoringService:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def create_policy(
        self, name: str, config: dict[str, object], now: datetime
    ) -> PaperMonitoringPolicyRecord:
        validate_policy(config)
        created = require_utc(now)
        content_hash = identity({"schema_version": "1", "config": config})
        with self.sessions() as session, session.begin():
            existing = session.scalar(
                select(PaperMonitoringPolicyRecord).where(
                    PaperMonitoringPolicyRecord.content_hash == content_hash
                )
            )
            if existing is None:
                existing = PaperMonitoringPolicyRecord(
                    policy_id=content_hash,
                    name=name,
                    schema_version="1",
                    created_at=created,
                    content_hash=content_hash,
                    config_json=canonical(config),
                )
                session.add(existing)
                session.flush()
            session.expunge(existing)
            return existing

    def enroll(self, deployment_id: str, policy_id: str, now: datetime) -> PaperMonitoringRunRecord:
        started = require_utc(now)
        with self.sessions() as session, session.begin():
            deployment = session.get(StrategyDeploymentRecord, deployment_id)
            policy = session.get(PaperMonitoringPolicyRecord, policy_id)
            if deployment is None or deployment.status != "APPROVED" or policy is None:
                raise DatasetInvalid("Enrollment vyžaduje APPROVED deployment a validní policy")
            experiment = session.get(ExperimentRecord, deployment.experiment_id)
            if (
                experiment is None
                or experiment.status != "COMPLETED"
                or experiment.decision != "PAPER_CANDIDATE"
            ):
                raise DatasetInvalid("Experiment není paper candidate")
            snapshot, strategy, selected = DeploymentService.validate_experiment(
                session, experiment
            )
            account = session.get(PaperAccountRecord, deployment.paper_account_id)
            if (
                account is None
                or not account.reconciliation_safe
                or account.base_currency != "USD"
                or deployment.currency != "USD"
                or deployment.timeframe != "1d"
                or snapshot.timeframe != "1d"
                or deployment.parameters_json != canonical(selected)
                or deployment.strategy_name != strategy.strategy_name
                or deployment.strategy_version != strategy.strategy_version
            ):
                raise DatasetInvalid("Deployment lineage nebo paper account není bezpečný")
            open_run = session.scalar(
                select(PaperMonitoringRunRecord).where(
                    PaperMonitoringRunRecord.paper_account_id == account.id,
                    PaperMonitoringRunRecord.state.in_(OPEN_STATES),
                )
            )
            if open_run is not None:
                if open_run.deployment_id == deployment_id:
                    session.expunge(open_run)
                    return open_run
                raise DatasetInvalid("Paper account již má otevřený monitoring run")
            metrics = {
                key: getattr(experiment, key)
                for key in (
                    "total_return",
                    "annualized_return",
                    "volatility",
                    "sharpe",
                    "max_drawdown",
                    "turnover",
                    "time_weighted_exposure",
                    "trade_count",
                    "total_costs",
                )
            }
            if (
                experiment.snapshot_id is None
                or experiment.strategy_name is None
                or experiment.strategy_version is None
            ):
                raise DatasetInvalid("Experiment nemá úplnou Phase 6 replay lineage")
            config = json.loads(experiment.config_json)
            replay = Phase6ExperimentReplayService(self.sessions).replay(
                Phase6ExperimentRequest(
                    snapshot_id=experiment.snapshot_id,
                    strategy_name=experiment.strategy_name,
                    strategy_version=experiment.strategy_version,
                    parameter_configs=tuple(config["parameters"]),
                    train_fraction=Decimal(str(config["train_fraction"])),
                    validation_fraction=Decimal(str(config["validation_fraction"])),
                    initial_cash=Decimal(str(config["initial_cash"])),
                    commission_bps=Decimal(str(config["commission_bps"])),
                    seed=int(config["seed"]),
                    code_sha=experiment.code_sha,
                )
            )
            if canonical(replay.selected_parameters) != canonical(selected):
                raise DatasetInvalid("Phase 6 replay vybral jiné parametry")
            for key, persisted in metrics.items():
                replayed = getattr(replay.oos, key)
                if abs(Decimal(str(persisted)) - Decimal(str(replayed))) > Decimal("1e-10"):
                    raise DatasetInvalid(f"Phase 6 replay metric mismatch: {key}")
            if not replay.oos_returns:
                raise DatasetInvalid("Validní Phase 6 replay nemá denní OOS returns")
            returns = list(replay.oos_returns)
            equity = [
                {"timestamp": when.isoformat(), "equity": str(value)}
                for when, value in replay.oos_equity
            ]
            baseline_payload = {
                "deployment_id": deployment_id,
                "experiment_id": experiment.id,
                "snapshot_id": snapshot.snapshot_id,
                "strategy_identity": strategy.strategy_identity,
                "strategy": [strategy.strategy_name, strategy.strategy_version],
                "parameters": selected,
                "code_sha": experiment.code_sha,
                "cost_model": json.loads(experiment.cost_model_json or "{}"),
                "metrics": metrics,
                "returns": returns,
                "equity": equity,
                "algorithm_version": "phase6-oos-replay-v2",
            }
            baseline_hash = identity(baseline_payload)
            baseline = session.scalar(
                select(PaperExpectationBaselineRecord).where(
                    PaperExpectationBaselineRecord.deployment_id == deployment_id
                )
            )
            if baseline is None:
                baseline = PaperExpectationBaselineRecord(
                    baseline_id=baseline_hash,
                    deployment_id=deployment_id,
                    experiment_id=experiment.id,
                    snapshot_id=snapshot.snapshot_id,
                    strategy_identity=strategy.strategy_identity,
                    strategy_name=strategy.strategy_name,
                    strategy_version=strategy.strategy_version,
                    parameters_json=canonical(selected),
                    code_sha=experiment.code_sha or "",
                    cost_model_json=experiment.cost_model_json or "{}",
                    oos_metrics_json=canonical(metrics),
                    oos_returns_json=canonical(returns),
                    oos_equity_json=canonical(equity),
                    oos_session_count=len(replay.oos_sessions),
                    algorithm_version="phase6-oos-replay-v2",
                    content_hash=baseline_hash,
                    created_at=started,
                )
                session.add(baseline)
                session.flush()
            monitoring_id = identity(
                {"deployment_id": deployment_id, "policy_id": policy_id, "started_at": started}
            )
            run = PaperMonitoringRunRecord(
                monitoring_id=monitoring_id,
                deployment_id=deployment_id,
                paper_account_id=account.id,
                policy_id=policy_id,
                baseline_id=baseline.baseline_id,
                starting_equity=account.equity,
                started_at=started,
                ended_at=None,
                state=MonitoringState.ACTIVE,
                state_reason="OPERATOR_ENROLLMENT",
                state_changed_at=started,
                created_at=started,
            )
            session.add(run)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                with self.sessions() as retry:
                    found = retry.scalar(
                        select(PaperMonitoringRunRecord).where(
                            PaperMonitoringRunRecord.deployment_id == deployment_id,
                            PaperMonitoringRunRecord.state.in_(OPEN_STATES),
                        )
                    )
                    if found is None:
                        raise
                    retry.expunge(found)
                    return found
            session.expunge(run)
            return run

    def transition(
        self, monitoring_id: str, target: MonitoringState, reason: str, now: datetime
    ) -> PaperMonitoringRunRecord:
        changed = require_utc(now)
        if not reason.strip():
            raise ValueError("Transition vyžaduje auditní důvod")
        with self.sessions() as session, session.begin():
            run = session.scalar(
                select(PaperMonitoringRunRecord)
                .where(PaperMonitoringRunRecord.monitoring_id == monitoring_id)
                .with_for_update()
            )
            if run is None:
                raise DatasetInvalid("Monitoring neexistuje")
            current = MonitoringState(run.state)
            allowed = {
                MonitoringState.ACTIVE: {
                    MonitoringState.PAUSED,
                    MonitoringState.SUSPENDED,
                    MonitoringState.RETIRED,
                },
                MonitoringState.PAUSED: {
                    MonitoringState.ACTIVE,
                    MonitoringState.SUSPENDED,
                    MonitoringState.RETIRED,
                },
                MonitoringState.SUSPENDED: {MonitoringState.ACTIVE, MonitoringState.RETIRED},
                MonitoringState.RETIRED: set(),
            }
            if target not in allowed[current]:
                raise DatasetInvalid("Neplatný monitoring transition")
            if target is MonitoringState.ACTIVE:
                deployment = session.get(StrategyDeploymentRecord, run.deployment_id)
                account = session.get(PaperAccountRecord, run.paper_account_id)
                if (
                    deployment is None
                    or deployment.status != "APPROVED"
                    or account is None
                    or account.trading_state == SystemTradingState.HALTED
                    or not account.reconciliation_safe
                ):
                    raise DatasetInvalid("Monitoring nelze bezpečně resume")
            run.state, run.state_reason, run.state_changed_at = target, reason, changed
            if target is MonitoringState.RETIRED:
                run.ended_at = changed
            session.flush()
            session.expunge(run)
            return run


def deterministic_block_bootstrap(
    returns: Sequence[Decimal], horizon: int, samples: int, block_size: int, seed_material: str
) -> list[Decimal]:
    if not returns or horizon <= 0:
        return []
    rng = random.Random(int(identity(seed_material), 16))
    outcomes: list[Decimal] = []
    for _ in range(samples):
        path: list[Decimal] = []
        while len(path) < horizon:
            start = rng.randrange(len(returns))
            path.extend(returns[(start + offset) % len(returns)] for offset in range(block_size))
        equity = Decimal(1)
        for value in path[:horizon]:
            equity *= Decimal(1) + value
        outcomes.append(equity - Decimal(1))
    return sorted(outcomes)


class PaperPerformanceService:
    """Read-only valuation; tato služba záměrně nemá broker ani execution dependency."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        current_data: ValidatedCurrentDataAccessor,
        calendar: XNYSCalendar | None = None,
    ) -> None:
        self.sessions, self.current_data = sessions, current_data
        self.calendar = calendar or XNYSCalendar()

    def _suspend(self, monitoring_id: str, reason: str, changed_at: datetime) -> None:
        """Persistuje safety stop v oddělené transakci před vyhozením capture chyby."""
        with self.sessions() as session, session.begin():
            run = session.scalar(
                select(PaperMonitoringRunRecord)
                .where(PaperMonitoringRunRecord.monitoring_id == monitoring_id)
                .with_for_update()
            )
            if run is not None and run.state != MonitoringState.RETIRED:
                run.state = MonitoringState.SUSPENDED
                run.state_reason = reason
                run.state_changed_at = changed_at

    def capture(self, monitoring_id: str, as_of: datetime) -> PaperPerformanceSnapshotRecord:
        captured = require_utc(as_of)
        session_date = self.calendar.latest_completed_session(captured)
        with self.sessions() as lookup:
            account_id = lookup.scalar(
                select(PaperMonitoringRunRecord.paper_account_id).where(
                    PaperMonitoringRunRecord.monitoring_id == monitoring_id
                )
            )
        if account_id is not None:
            PaperCorporateActionService(self.sessions).apply(account_id, captured)
        with self.sessions() as session, session.begin():
            run = session.get(PaperMonitoringRunRecord, monitoring_id)
            if run is None or run.state == MonitoringState.RETIRED:
                raise DatasetInvalid("Performance vyžaduje otevřený monitoring")
            existing = session.scalar(
                select(PaperPerformanceSnapshotRecord).where(
                    PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id,
                    PaperPerformanceSnapshotRecord.session_date == session_date,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            account = session.get(PaperAccountRecord, run.paper_account_id)
            if account is None or not account.reconciliation_safe:
                self._suspend(monitoring_id, "RECONCILIATION_UNSAFE", captured)
                raise DatasetInvalid("Paper účet není reconciliation-safe")
            latest_reconciliation = session.scalar(
                select(ReconciliationRecord)
                .where(ReconciliationRecord.account_id == account.id)
                .order_by(ReconciliationRecord.timestamp.desc())
                .limit(1)
            )
            if latest_reconciliation is not None and latest_reconciliation.status != "SUCCEEDED":
                self._suspend(monitoring_id, "RECONCILIATION_UNSAFE", captured)
                raise DatasetInvalid("Poslední reconciliation selhala")
            positions = tuple(
                session.scalars(
                    select(PositionRecord).where(
                        PositionRecord.account_id == account.id, PositionRecord.quantity != 0
                    )
                )
            )
            observation_lineage: list[dict[str, object]] = []
            values: dict[str, Decimal] = {}
            if positions:
                observations = self.current_data.latest(
                    [item.instrument_id for item in positions], captured
                )
                if any(item.session_date != session_date for item in observations):
                    raise DatasetInvalid("Valuation data neodpovídají poslední dokončené session")
                values = {item.instrument_id: item.close for item in observations}
                observation_lineage = [
                    {
                        "observation_id": item.observation_id,
                        "revision": item.revision,
                        "source_hash": item.source_hash,
                    }
                    for item in observations
                ]
            gross = sum(
                (abs(item.quantity * values[item.instrument_id]) for item in positions), Decimal(0)
            )
            net = sum(
                (item.quantity * values[item.instrument_id] for item in positions), Decimal(0)
            )
            equity = account.cash + net
            previous = session.scalar(
                select(PaperPerformanceSnapshotRecord)
                .where(PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id)
                .order_by(PaperPerformanceSnapshotRecord.session_date.desc())
                .limit(1)
            )
            daily = None if previous is None else equity / previous.marked_equity - Decimal(1)
            peak = max(
                [run.starting_equity, equity] + ([previous.marked_equity] if previous else [])
            )
            if previous is not None:
                historical_peak = session.scalar(
                    select(func.max(PaperPerformanceSnapshotRecord.marked_equity)).where(
                        PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id
                    )
                )
                if historical_peak is not None:
                    peak = max(peak, Decimal(historical_peak))
            cycle_ids = select(PaperDeploymentCycleRecord.trading_cycle_id).where(
                PaperDeploymentCycleRecord.monitoring_id == monitoring_id,
                PaperDeploymentCycleRecord.session_date <= session_date,
            )
            unrelated_cycle = session.scalar(
                select(TradingCycleRecord.id)
                .where(
                    TradingCycleRecord.account_id == account.id,
                    TradingCycleRecord.started_at >= run.started_at,
                    TradingCycleRecord.started_at <= captured,
                    TradingCycleRecord.id.not_in(cycle_ids),
                )
                .limit(1)
            )
            if unrelated_cycle is not None:
                self._suspend(monitoring_id, "UNATTRIBUTED_ACCOUNT_ACTIVITY", captured)
                raise DatasetInvalid("Paper účet obsahuje aktivitu mimo monitoring lineage")
            orders = tuple(
                session.scalars(
                    select(PaperOrderRecord).where(PaperOrderRecord.trading_cycle_id.in_(cycle_ids))
                )
            )
            order_ids = [item.id for item in orders]
            fills = (
                tuple(
                    session.scalars(
                        select(PaperFillRecord).where(PaperFillRecord.order_id.in_(order_ids))
                    )
                )
                if order_ids
                else ()
            )
            turnover = (
                sum((item.quantity * item.reference_price for item in fills), Decimal(0))
                / run.starting_equity
            )
            commissions = sum((item.commission for item in fills), Decimal(0))
            slippage = sum(
                (abs(item.price - item.reference_price) * item.quantity for item in fills),
                Decimal(0),
            )
            rejects = (
                session.scalar(
                    select(func.count())
                    .select_from(RiskDecisionRecord)
                    .where(
                        RiskDecisionRecord.trading_cycle_id.in_(cycle_ids),
                        RiskDecisionRecord.status == "REJECTED",
                    )
                )
                or 0
            )
            payload = {
                "monitoring_id": monitoring_id,
                "session_date": session_date,
                "as_of": captured,
                "cash": account.cash,
                "equity": equity,
                "gross": gross,
                "net": net,
                "daily_return": daily,
                "cumulative_return": equity / run.starting_equity - Decimal(1),
                "drawdown": equity / peak - Decimal(1),
                "turnover": turnover,
                "commissions": commissions,
                "slippage": slippage,
                "observations": observation_lineage,
            }
            snapshot_id = identity({"monitoring_id": monitoring_id, "session_date": session_date})
            row = PaperPerformanceSnapshotRecord(
                snapshot_id=snapshot_id,
                monitoring_id=monitoring_id,
                deployment_id=run.deployment_id,
                paper_account_id=run.paper_account_id,
                session_date=session_date,
                captured_at=captured,
                as_of=captured,
                calendar_identity="XNYS/exchange-calendars",
                cash=account.cash,
                marked_equity=equity,
                realized_pnl=account.realized_pnl,
                gross_exposure=gross,
                net_exposure=net,
                position_count=len(positions),
                daily_return=daily,
                cumulative_return=equity / run.starting_equity - Decimal(1),
                drawdown=equity / peak - Decimal(1),
                cumulative_turnover=turnover,
                cumulative_commissions=commissions,
                cumulative_slippage_cost=slippage,
                order_count=len(orders),
                fill_count=len(fills),
                risk_rejection_count=int(rejects),
                reconciliation_status="SUCCEEDED",
                trading_state=account.trading_state,
                observation_lineage_json=canonical(observation_lineage),
                content_hash=identity(payload),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                with self.sessions() as retry:
                    found = retry.get(PaperPerformanceSnapshotRecord, snapshot_id)
                    if found is None:
                        raise
                    retry.expunge(found)
                    return found
            session.expunge(row)
            return row

    def series(
        self,
        monitoring_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[PaperPerformanceSnapshotRecord]:
        query = select(PaperPerformanceSnapshotRecord).where(
            PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id
        )
        if start_date is not None:
            query = query.where(PaperPerformanceSnapshotRecord.session_date >= start_date)
        if end_date is not None:
            query = query.where(PaperPerformanceSnapshotRecord.session_date <= end_date)
        with self.sessions() as session:
            return list(
                session.scalars(
                    query.order_by(PaperPerformanceSnapshotRecord.session_date)
                    .limit(min(limit, 1000))
                    .offset(offset)
                )
            )


class PaperPerformanceEvaluationService:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def evaluate(
        self, monitoring_id: str, performance_snapshot_id: str, now: datetime
    ) -> PaperPerformanceEvaluationRecord:
        created = require_utc(now)
        with self.sessions() as session, session.begin():
            run = session.scalar(
                select(PaperMonitoringRunRecord)
                .where(PaperMonitoringRunRecord.monitoring_id == monitoring_id)
                .with_for_update()
            )
            snapshot = session.get(PaperPerformanceSnapshotRecord, performance_snapshot_id)
            if run is None or snapshot is None or snapshot.monitoring_id != monitoring_id:
                raise DatasetInvalid("Evaluation lineage není konzistentní")
            existing = session.scalar(
                select(PaperPerformanceEvaluationRecord).where(
                    PaperPerformanceEvaluationRecord.monitoring_id == monitoring_id,
                    PaperPerformanceEvaluationRecord.performance_snapshot_id
                    == performance_snapshot_id,
                    PaperPerformanceEvaluationRecord.policy_id == run.policy_id,
                    PaperPerformanceEvaluationRecord.algorithm_version == ALGORITHM_VERSION,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            policy_row = session.get(PaperMonitoringPolicyRecord, run.policy_id)
            baseline = session.get(PaperExpectationBaselineRecord, run.baseline_id)
            account = session.get(PaperAccountRecord, run.paper_account_id)
            if policy_row is None or baseline is None or account is None:
                raise DatasetInvalid("Evaluation evidence chybí")
            policy = json.loads(policy_row.config_json)
            validate_policy(policy)
            series = list(
                session.scalars(
                    select(PaperPerformanceSnapshotRecord)
                    .where(
                        PaperPerformanceSnapshotRecord.monitoring_id == monitoring_id,
                        PaperPerformanceSnapshotRecord.session_date <= snapshot.session_date,
                    )
                    .order_by(PaperPerformanceSnapshotRecord.session_date)
                )
            )
            reasons: list[str] = []
            verdict = EvaluationVerdict.INSUFFICIENT_DATA
            hard_drawdown = snapshot.drawdown <= -Decimal(str(policy["maximum_paper_drawdown"]))
            if account.trading_state == SystemTradingState.HALTED:
                reasons.append("ACCOUNT_HALTED")
            if not account.reconciliation_safe or snapshot.reconciliation_status != "SUCCEEDED":
                reasons.append("RECONCILIATION_UNSAFE")
            if hard_drawdown:
                reasons.append("DRAWDOWN_BREACH")
            if reasons:
                verdict = EvaluationVerdict.SUSPENDED
                run.state, run.state_reason, run.state_changed_at = (
                    MonitoringState.SUSPENDED,
                    ",".join(reasons),
                    created,
                )
            elif len(series) >= int(policy["minimum_sessions"]):
                baseline_returns = [
                    Decimal(value) for value in json.loads(baseline.oos_returns_json)
                ]
                if baseline_returns:
                    distribution = deterministic_block_bootstrap(
                        baseline_returns,
                        len(series),
                        int(policy["bootstrap_samples"]),
                        int(policy["bootstrap_block_size"]),
                        f"{monitoring_id}:{run.policy_id}:{len(series)}:{ALGORITHM_VERSION}",
                    )
                    rank = (
                        sum(value <= snapshot.cumulative_return for value in distribution)
                        * 100
                        / len(distribution)
                    )
                    if rank <= int(policy["review_return_percentile"]):
                        verdict, reasons = EvaluationVerdict.REVIEW_REQUIRED, ["RETURN_DRIFT"]
                    elif rank <= int(policy["watch_return_percentile"]):
                        verdict, reasons = EvaluationVerdict.WATCH, ["RETURN_DRIFT"]
                    else:
                        verdict = EvaluationVerdict.HEALTHY
                else:
                    verdict, reasons = (
                        EvaluationVerdict.INSUFFICIENT_DATA,
                        ["BASELINE_SERIES_NOT_AVAILABLE"],
                    )
            comparison = {
                "baseline_metrics": json.loads(baseline.oos_metrics_json),
                "baseline_series_available": baseline.oos_session_count > 0,
            }
            metrics = {
                "cumulative_return": snapshot.cumulative_return,
                "drawdown": snapshot.drawdown,
                "turnover": snapshot.cumulative_turnover,
                "sessions": len(series),
            }
            row_hash = identity(
                {
                    "monitoring_id": monitoring_id,
                    "snapshot_id": performance_snapshot_id,
                    "policy_id": run.policy_id,
                    "algorithm": ALGORITHM_VERSION,
                    "verdict": verdict,
                    "reasons": reasons,
                    "metrics": metrics,
                    "comparison": comparison,
                }
            )
            row = PaperPerformanceEvaluationRecord(
                evaluation_id=row_hash,
                monitoring_id=monitoring_id,
                performance_snapshot_id=performance_snapshot_id,
                policy_id=run.policy_id,
                created_at=created,
                paper_session_count=len(series),
                verdict=verdict,
                reasons_json=canonical(reasons),
                paper_metrics_json=canonical(metrics),
                baseline_comparison_json=canonical(comparison),
                algorithm_version=ALGORITHM_VERSION,
                content_hash=row_hash,
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row
