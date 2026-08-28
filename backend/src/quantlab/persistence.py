import builtins
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class InstrumentRecord(Base):
    __tablename__ = "instruments"
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    calendar: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstrumentSymbolRecord(Base):
    __tablename__ = "instrument_symbol_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("instrument_id", "symbol", "valid_from"),)


class MarketDataIngestionRecord(Base):
    __tablename__ = "market_data_ingestions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class MarketObservationRecord(Base):
    __tablename__ = "market_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), index=True
    )
    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("market_data_ingestions.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    session_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[str] = mapped_column(String(50), nullable=False)
    high: Mapped[str] = mapped_column(String(50), nullable=False)
    low: Mapped[str] = mapped_column(String(50), nullable=False)
    close: Mapped[str] = mapped_column(String(50), nullable=False)
    volume: Mapped[str] = mapped_column(String(50), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("instrument_id", "provider", "session_date", "revision"),
        Index("ix_observation_asof", "instrument_id", "session_date", "observed_at"),
    )


class CorporateActionRecord(Base):
    __tablename__ = "corporate_actions"
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[str | None] = mapped_column(String(50))
    new_symbol: Mapped[str | None] = mapped_column(String(32))


class CorporateActionReadinessRecord(Base):
    """Neměnný důkaz, že provider prověřil konkrétní interval akcí."""

    __tablename__ = "corporate_action_readiness"
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider_version: Mapped[str] = mapped_column(String(40), nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), index=True
    )
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supports_actions: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    blocking_reason: Mapped[str | None] = mapped_column(String(80))
    action_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        Index(
            "ix_action_readiness_scope",
            "instrument_id",
            "provider",
            "requested_start",
            "requested_end",
            "knowledge_cutoff",
        ),
    )


class UniverseDefinitionRecord(Base):
    __tablename__ = "universe_definitions"
    universe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UniverseMembershipRecord(Base):
    __tablename__ = "universe_memberships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), index=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("universe_id", "instrument_id", "valid_from"),
        Index("ix_universe_pit", "universe_id", "valid_from", "valid_to", "known_at"),
    )


class DatasetSnapshotRecord(Base):
    __tablename__ = "dataset_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    calendar_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"), index=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    coverage: Mapped[str] = mapped_column(String(50), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "calendar_identity",
            "universe_id",
            "start_at",
            "end_at",
            "as_of",
            "content_hash",
        ),
    )


class RunRecord(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)


class DatasetRecord(Base):
    __tablename__ = "datasets"
    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    universe: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(30), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    schema_version: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class StrategyRecord(Base):
    __tablename__ = "strategies"
    strategy_identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("strategy_name", "strategy_version"),)


class ExperimentRecord(Base):
    __tablename__ = "research_experiments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="COMPLETED")
    failure_kind: Mapped[str | None] = mapped_column(String(40))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.dataset_id", ondelete="RESTRICT"), index=True
    )
    # FK vzniká až forward migrací 20260811_06; initial migrace importuje aktuální metadata.
    snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy_identity: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.strategy_identity", ondelete="RESTRICT"), index=True
    )
    strategy_name: Mapped[str | None] = mapped_column(String(100), index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(50), index=True)
    parameter_space_id: Mapped[str | None] = mapped_column(String(64), index=True)
    decision: Mapped[str | None] = mapped_column(String(30), index=True)
    total_return: Mapped[float | None] = mapped_column(Float)
    cagr: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    sortino: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    calmar: Mapped[float | None] = mapped_column(Float)
    closed_trade_count: Mapped[int | None] = mapped_column(Integer)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    expectancy: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    total_commissions: Mapped[float | None] = mapped_column(Float)
    total_slippage: Mapped[float | None] = mapped_column(Float)
    annualized_return: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float | None] = mapped_column(Float)
    time_weighted_exposure: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    total_costs: Mapped[float | None] = mapped_column(Float)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    code_sha: Mapped[str | None] = mapped_column(String(64))
    seed: Mapped[int | None] = mapped_column(Integer)
    cost_model_json: Mapped[str | None] = mapped_column(Text)
    selected_parameters_json: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)


class StrategyDeploymentRecord(Base):
    __tablename__ = "strategy_deployments"
    deployment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"), nullable=False
    )
    # Phase 4 model importuje tento modul, proto zde nelze vytvořit bezpečný ORM FK bez cyklu.
    # Produkční FK na paper_accounts.id vynucuje forward migrace 20260811_06.
    paper_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    runtime_manifest_json: Mapped[str | None] = mapped_column(Text)
    runtime_manifest_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_manifest_version: Mapped[int | None] = mapped_column(Integer)


class ExperimentFoldRecord(Base):
    __tablename__ = "research_experiment_folds"
    __table_args__ = (UniqueConstraint("experiment_id", "fold_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), index=True
    )
    fold_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    oos_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    oos_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    oos_evaluations: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_config_json: Mapped[str | None] = mapped_column(Text)


class EligibilityCheckRecord(Base):
    __tablename__ = "research_eligibility_checks"
    __table_args__ = (UniqueConstraint("experiment_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    observed_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(200))


class Phase6EligibilityDecisionRecord(Base):
    """Append-only autorita pro Phase 6 promotion; legacy checks nejsou autorizační."""

    __tablename__ = "phase6_eligibility_decisions"
    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    code_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_id: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        UniqueConstraint("experiment_id", "policy_id", "policy_version"),
        CheckConstraint("status IN ('ELIGIBLE', 'INELIGIBLE')"),
    )


class ParameterRunRecord(Base):
    __tablename__ = "research_parameter_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("experiment_id", "fold_id"),
            ("research_experiment_folds.experiment_id", "research_experiment_folds.fold_id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("experiment_id", "fold_id", "stage", "run_id"),
        Index("ix_parameter_run_fold", "experiment_id", "fold_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), index=True
    )
    fold_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(20), index=True)
    parameter_config_id: Mapped[str] = mapped_column(String(64), index=True)
    parameter_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True)
    objective_score: Mapped[float | None] = mapped_column(Float)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    closed_trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime, StrEnum)):
        return str(value)
    raise TypeError(f"Nelze serializovat {type(value)}")


def _canonical(value: object) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _sqlite_fk(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_test_schema(engine: Engine) -> None:
    """Izolovaný helper; runtime PostgreSQL bootstrap vždy používá Alembic."""
    import quantlab.phase7  # noqa: F401

    Base.metadata.create_all(engine)


@dataclass(frozen=True)
class LeaderboardPolicy:
    name: str = "eligibility-consistency-v1"
    eligibility_order: tuple[str, ...] = ("PAPER_CANDIDATE", "RESEARCH_ONLY", "REJECTED")


class DatasetRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def register(self, record: dict[str, object]) -> str:
        dataset_id = str(record["dataset_id"])
        candidate = dict(record)
        candidate.pop("created_at", None)
        candidate["start_at"] = _utc(cast(datetime, candidate["start_at"]))
        candidate["end_at"] = _utc(cast(datetime, candidate["end_at"]))
        candidate.setdefault("timezone", "UTC")
        candidate.setdefault("storage_uri", None)
        candidate.setdefault("metadata", {})
        with Session(self.engine) as session:
            existing = session.get(DatasetRecord, dataset_id)
            if existing:
                persisted = _canonical(self._as_dict(existing, include_created=False))
                if persisted != _canonical(candidate):
                    raise ValueError("Dataset identity koliduje s odlišným neměnným obsahem")
                return dataset_id
            metadata = record.get("metadata", {})
            row = DatasetRecord(
                dataset_id=dataset_id,
                content_hash=str(record["content_hash"]),
                universe=str(record["universe"]),
                source=str(record["source"]),
                timeframe=str(record["timeframe"]),
                start_at=_utc(cast(datetime, record["start_at"])),
                end_at=_utc(cast(datetime, record["end_at"])),
                row_count=int(str(record["row_count"])),
                timezone=str(record.get("timezone", "UTC")),
                schema_version=str(record["schema_version"]),
                storage_uri=str(record["storage_uri"]) if record.get("storage_uri") else None,
                created_at=_utc(cast(datetime, record.get("created_at", datetime.now(UTC)))),
                metadata_json=_canonical(metadata),
            )
            session.add(row)
            session.commit()
            logger.info("dataset_registered", extra={"dataset_id": dataset_id})
            return dataset_id

    @staticmethod
    def _as_dict(row: DatasetRecord, include_created: bool = True) -> dict[str, object]:
        result = {
            "dataset_id": row.dataset_id,
            "content_hash": row.content_hash,
            "universe": row.universe,
            "source": row.source,
            "timeframe": row.timeframe,
            "start_at": _utc(row.start_at),
            "end_at": _utc(row.end_at),
            "row_count": row.row_count,
            "timezone": row.timezone,
            "schema_version": row.schema_version,
            "storage_uri": row.storage_uri,
            "metadata": json.loads(row.metadata_json),
        }
        if include_created:
            result["created_at"] = _utc(row.created_at)
        return result

    def get(self, dataset_id: str) -> dict[str, object] | None:
        with Session(self.engine) as session:
            row = session.get(DatasetRecord, dataset_id)
            return self._as_dict(row) if row else None


class StrategyRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def register(self, name: str, version: str, metadata: dict[str, object] | None = None) -> str:
        identity = hashlib.sha256(
            _canonical({"name": name, "version": version}).encode()
        ).hexdigest()
        payload = _canonical(metadata or {})
        with Session(self.engine) as session:
            existing = session.get(StrategyRecord, identity)
            if existing and existing.metadata_json != payload:
                raise ValueError("Strategy identity koliduje")
            if not existing:
                session.add(
                    StrategyRecord(
                        strategy_identity=identity,
                        strategy_name=name,
                        strategy_version=version,
                        created_at=datetime.now(UTC),
                        metadata_json=payload,
                    )
                )
                session.commit()
        return identity


class RunRepository:
    def __init__(
        self, url: str = "sqlite:///:memory:", *, bootstrap_test_schema: bool | None = None
    ) -> None:
        self.engine = create_engine(url)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_fk)
        if bootstrap_test_schema is None:
            bootstrap_test_schema = url.startswith("sqlite")
        if bootstrap_test_schema:
            create_test_schema(self.engine)
        self.datasets, self.strategies = (
            DatasetRepository(self.engine),
            StrategyRepository(self.engine),
        )

    def save(self, strategy: str, result: dict[str, object], created_at: datetime) -> int:
        with Session(self.engine) as session:
            row = RunRecord(
                strategy=strategy,
                result_json=json.dumps(result, default=_json_default),
                created_at=_utc(created_at),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def list(self) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            return [
                {
                    "id": row.id,
                    "strategy": row.strategy,
                    "created_at": _utc(row.created_at),
                    "result": json.loads(row.result_json),
                }
                for row in session.scalars(select(RunRecord))
            ]

    def save_experiment(
        self,
        experiment_id: str,
        config: dict[str, object],
        result: dict[str, object],
        created_at: datetime,
    ) -> str:
        config_json, result_json = _canonical(config), _canonical(result)
        dataset_id = str(result.get("dataset_id", "")) or None
        name, version = (
            str(result.get("strategy_name", "")) or None,
            str(result.get("strategy_version", "")) or None,
        )
        if dataset_id and not self.datasets.get(dataset_id):
            folds = result.get("folds", ())
            if not isinstance(folds, (list, tuple)):
                raise TypeError("Fold snapshot musí být sekvence")
            starts = [
                datetime.fromisoformat(str(f["train_start"])) for f in folds if isinstance(f, dict)
            ]
            ends = [datetime.fromisoformat(str(f["oos_end"])) for f in folds if isinstance(f, dict)]
            self.datasets.register(
                {
                    "dataset_id": dataset_id,
                    "content_hash": dataset_id,
                    "universe": "unknown",
                    "source": "research-input",
                    "timeframe": "unknown",
                    "start_at": min(starts),
                    "end_at": max(ends),
                    "row_count": 0,
                    "timezone": "UTC",
                    "schema_version": "phase2-bars-v1",
                    "storage_uri": None,
                    "metadata": {"registration": "legacy-experiment-materialization"},
                }
            )
        strategy_identity = self.strategies.register(name, version) if name and version else None
        eligibility = result.get("eligibility", {})
        if not isinstance(eligibility, dict):
            raise TypeError("Eligibility snapshot musí být mapování")
        metrics = result.get("aggregate_oos_metrics", {})
        if not isinstance(metrics, dict):
            raise TypeError("Aggregate metrics musí být mapování")
        with Session(self.engine) as session:
            existing = session.get(ExperimentRecord, experiment_id)
            if existing:
                if existing.config_json != config_json or existing.result_json != result_json:
                    raise ValueError("Experiment identity koliduje s odlišným neměnným snapshotem")
                return experiment_id
            row = ExperimentRecord(
                id=experiment_id,
                created_at=_utc(created_at),
                completed_at=_utc(created_at),
                status="COMPLETED",
                dataset_id=dataset_id,
                strategy_identity=strategy_identity,
                strategy_name=name,
                strategy_version=version,
                parameter_space_id=str(result.get("parameter_space_id", "")) or None,
                decision=str(eligibility.get("decision", "")) or None,
                total_return=metrics.get("total_return"),
                cagr=metrics.get("cagr"),
                sharpe=metrics.get("sharpe_ratio"),
                sortino=metrics.get("sortino_ratio"),
                max_drawdown=metrics.get("maximum_drawdown"),
                calmar=metrics.get("calmar_ratio"),
                closed_trade_count=metrics.get("number_of_trades"),
                profit_factor=metrics.get("profit_factor"),
                expectancy=metrics.get("expectancy"),
                turnover=metrics.get("turnover"),
                total_commissions=metrics.get("total_commissions"),
                total_slippage=metrics.get("total_slippage_cost"),
                config_json=config_json,
                result_json=result_json,
            )
            session.add(row)
            session.flush()
            for fold in self._folds(experiment_id, result):
                session.add(fold)
            session.flush()
            for run in self._runs(experiment_id, result):
                session.add(run)
            for check in self._checks(experiment_id, eligibility):
                session.add(check)
            session.commit()
            logger.info("experiment_persisted", extra={"experiment_id": experiment_id})
        return experiment_id

    def _folds(
        self, experiment_id: str, result: dict[str, object]
    ) -> builtins.list[ExperimentFoldRecord]:
        folds = result.get("folds", ())
        if not isinstance(folds, (list, tuple)):
            raise TypeError("Fold snapshot musí být sekvence")
        return [
            ExperimentFoldRecord(
                experiment_id=experiment_id,
                fold_id=str(f["fold_id"]),
                status=str(f["status"]),
                oos_start=_utc(datetime.fromisoformat(str(f["oos_start"]))),
                oos_end=_utc(datetime.fromisoformat(str(f["oos_end"]))),
                oos_evaluations=int(f["oos_evaluations"]),
                selected_config_json=_canonical(f.get("selected_config")),
            )
            for f in folds
            if isinstance(f, dict)
        ]

    def _runs(
        self, experiment_id: str, result: dict[str, object]
    ) -> builtins.list[ParameterRunRecord]:
        rows: builtins.list[ParameterRunRecord] = []
        folds = result.get("folds", ())
        if not isinstance(folds, (list, tuple)):
            raise TypeError("Fold snapshot musí být sekvence")
        for fold in folds:
            if not isinstance(fold, dict):
                raise TypeError("Fold snapshot musí být mapování")
            for stage, key in (("TRAIN", "train_runs"), ("VALIDATION", "validation_runs")):
                for run in fold.get(key, ()):
                    pc = run["parameter_config"]
                    score = run.get("objective_score")
                    rows.append(
                        ParameterRunRecord(
                            run_id=str(run["run_id"]),
                            experiment_id=experiment_id,
                            fold_id=str(fold["fold_id"]),
                            stage=stage,
                            parameter_config_id=hashlib.sha256(_canonical(pc).encode()).hexdigest(),
                            parameter_config_json=_canonical(pc),
                            status=str(run["status"]),
                            objective_score=float(score) if score is not None else None,
                            metrics_json=_canonical(run["metrics"])
                            if run.get("metrics") is not None
                            else None,
                            closed_trade_count=int(run["closed_trades"]),
                            failure_reason=str(run["failure_reason"])
                            if run.get("failure_reason") is not None
                            else None,
                        )
                    )
        return rows

    def _checks(
        self, experiment_id: str, eligibility: dict[str, object]
    ) -> builtins.list[EligibilityCheckRecord]:
        checks = eligibility.get("checks", ())
        if not isinstance(checks, (list, tuple)):
            raise TypeError("Eligibility checks musí být sekvence")
        if any(not isinstance(check, dict) for check in checks):
            raise TypeError("Eligibility check musí být mapování")
        return [
            EligibilityCheckRecord(
                experiment_id=experiment_id,
                name=str(c["name"]),
                status=str(c["status"]),
                observed_value=float(c["observed_value"])
                if c.get("observed_value") is not None
                else None,
                threshold=float(c["threshold"]) if c.get("threshold") is not None else None,
                reason=str(c["reason"]) if c.get("reason") is not None else None,
            )
            for c in checks
        ]

    def get_experiment(self, experiment_id: str) -> dict[str, object] | None:
        with Session(self.engine) as session:
            row = session.get(ExperimentRecord, experiment_id)
            return (
                {
                    "id": row.id,
                    "config": json.loads(row.config_json),
                    "result": json.loads(row.result_json),
                }
                if row
                else None
            )

    def list_experiments(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        strategy: str | None = None,
        strategy_version: str | None = None,
        dataset_id: str | None = None,
        eligibility_status: str | None = None,
    ) -> builtins.list[dict[str, object]]:
        if limit < 1 or limit > 200 or offset < 0:
            raise ValueError("Neplatná pagination")
        statement = select(ExperimentRecord)
        for column, value in (
            (ExperimentRecord.strategy_name, strategy),
            (ExperimentRecord.strategy_version, strategy_version),
            (ExperimentRecord.dataset_id, dataset_id),
            (ExperimentRecord.decision, eligibility_status),
        ):
            if value is not None:
                statement = statement.where(column == value)
        statement = (
            statement.order_by(ExperimentRecord.created_at.desc(), ExperimentRecord.id)
            .limit(limit)
            .offset(offset)
        )
        with Session(self.engine) as session:
            return [
                {
                    "id": r.id,
                    "config": json.loads(r.config_json),
                    "result": json.loads(r.result_json),
                }
                for r in session.scalars(statement)
            ]

    def leaderboard(
        self, *, limit: int = 50, offset: int = 0, policy: LeaderboardPolicy | None = None
    ) -> builtins.list[dict[str, object]]:
        if limit < 1 or limit > 200 or offset < 0:
            raise ValueError("Neplatná pagination")
        policy = policy or LeaderboardPolicy()
        with Session(self.engine) as session:
            rows = [
                {
                    "id": row.id,
                    "config": json.loads(row.config_json),
                    "result": json.loads(row.result_json),
                }
                for row in session.scalars(select(ExperimentRecord).order_by(ExperimentRecord.id))
            ]
        rank = {value: index for index, value in enumerate(policy.eligibility_order)}

        def key(item: dict[str, object]) -> tuple[object, ...]:
            result = item["result"]
            if not isinstance(result, dict):
                raise TypeError("Persistovaný research výsledek musí být objekt")
            metrics = result.get("aggregate_oos_metrics", {})
            eligibility = result.get("eligibility", {})
            if not isinstance(metrics, dict) or not isinstance(eligibility, dict):
                raise TypeError("Persistované metriky a eligibility musí být objekty")
            stress = result.get("cost_stress", {})
            stability = result.get("parameter_stability", {})
            survived = bool(
                isinstance(stress, dict) and stress and all(float(v) > 0 for v in stress.values())
            )
            profitable_fraction = (
                stability.get("profitable_fraction") if isinstance(stability, dict) else None
            )
            consistency = float(profitable_fraction) if profitable_fraction is not None else -1.0
            total_return = metrics.get("total_return")
            maximum_drawdown = metrics.get("maximum_drawdown")
            sharpe_ratio = metrics.get("sharpe_ratio")
            return (
                rank.get(str(eligibility.get("decision")), len(rank)),
                -(float(total_return) > 0 if total_return is not None else False),
                -survived,
                -consistency,
                abs(float(maximum_drawdown)) if maximum_drawdown is not None else float("inf"),
                -float(sharpe_ratio) if sharpe_ratio is not None else float("inf"),
                item["id"],
            )

        selected = sorted(rows, key=key)[offset : offset + limit]
        return [
            {"rank": offset + i + 1, "policy": policy.name, **item}
            for i, item in enumerate(selected)
        ]

    def compare(self, experiment_ids: builtins.list[str]) -> builtins.list[dict[str, object]]:
        if len(experiment_ids) < 2:
            raise ValueError("Porovnání vyžaduje alespoň dva experimenty")
        output = []
        for experiment_id in experiment_ids:
            item = self.get_experiment(experiment_id)
            if item is None:
                raise KeyError(experiment_id)
            result = item["result"]
            if not isinstance(result, dict):
                raise TypeError("Persistovaný research výsledek musí být objekt")
            output.append(
                {
                    "id": experiment_id,
                    "strategy_name": result.get("strategy_name"),
                    "strategy_version": result.get("strategy_version"),
                    "dataset_id": result.get("dataset_id"),
                    "fold_count": len(result.get("folds", [])),
                    "metrics": result.get("aggregate_oos_metrics"),
                    "cost_stress": result.get("cost_stress"),
                    "monte_carlo": result.get("monte_carlo"),
                    "parameter_stability": result.get("parameter_stability"),
                    "eligibility": result.get("eligibility"),
                }
            )
        return output

    def lineage(self, experiment_id: str) -> dict[str, object] | None:
        item = self.get_experiment(experiment_id)
        if not item:
            return None
        result, config = item["result"], item["config"]
        if not isinstance(result, dict) or not isinstance(config, dict):
            raise TypeError("Persistovaný research výsledek a konfigurace musí být objekty")
        research_config = config.get("research_config", {})
        dataset_id = str(result.get("dataset_id"))
        return {
            "experiment_id": experiment_id,
            "dataset": self.datasets.get(dataset_id),
            "strategy_name": result.get("strategy_name"),
            "strategy_version": result.get("strategy_version"),
            "parameter_space_id": result.get("parameter_space_id"),
            "research_config": research_config,
            "engine_version": research_config.get("engine_version")
            if isinstance(research_config, dict)
            else None,
            "transaction_costs": research_config.get("costs")
            if isinstance(research_config, dict)
            else None,
            "random_seed": research_config.get("random_seed")
            if isinstance(research_config, dict)
            else None,
        }

    def get_experiment_structure(self, experiment_id: str) -> dict[str, object] | None:
        with Session(self.engine) as session:
            row = session.get(ExperimentRecord, experiment_id)
            if not row:
                return None
            folds = session.scalars(
                select(ExperimentFoldRecord).where(
                    ExperimentFoldRecord.experiment_id == experiment_id
                )
            ).all()
            checks = session.scalars(
                select(EligibilityCheckRecord)
                .where(EligibilityCheckRecord.experiment_id == experiment_id)
                .order_by(EligibilityCheckRecord.id)
            ).all()
            runs = session.scalars(
                select(ParameterRunRecord)
                .where(ParameterRunRecord.experiment_id == experiment_id)
                .order_by(ParameterRunRecord.id)
            ).all()
            return {
                "experiment": {
                    "id": row.id,
                    "dataset_id": row.dataset_id,
                    "strategy_name": row.strategy_name,
                    "strategy_version": row.strategy_version,
                    "parameter_space_id": row.parameter_space_id,
                    "decision": row.decision,
                },
                "folds": [
                    {
                        "fold_id": f.fold_id,
                        "status": f.status,
                        "oos_start": _utc(f.oos_start),
                        "oos_end": _utc(f.oos_end),
                        "oos_evaluations": f.oos_evaluations,
                        "selected_config": json.loads(f.selected_config_json)
                        if f.selected_config_json
                        else None,
                    }
                    for f in folds
                ],
                "eligibility_checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "observed_value": c.observed_value,
                        "threshold": c.threshold,
                        "reason": c.reason,
                    }
                    for c in checks
                ],
                "parameter_runs": [
                    {
                        "run_id": r.run_id,
                        "experiment_id": r.experiment_id,
                        "fold_id": r.fold_id,
                        "stage": r.stage,
                        "parameter_config_id": r.parameter_config_id,
                        "parameter_config": json.loads(r.parameter_config_json),
                        "status": r.status,
                        "objective_score": r.objective_score,
                        "metrics": json.loads(r.metrics_json) if r.metrics_json else None,
                        "closed_trade_count": r.closed_trade_count,
                        "failure_reason": r.failure_reason,
                    }
                    for r in runs
                ],
            }

    def persistence_counts(self) -> dict[str, int]:
        with Session(self.engine) as session:
            return {
                "experiments": session.query(ExperimentRecord).count(),
                "folds": session.query(ExperimentFoldRecord).count(),
                "parameter_runs": session.query(ParameterRunRecord).count(),
                "eligibility_checks": session.query(EligibilityCheckRecord).count(),
            }
