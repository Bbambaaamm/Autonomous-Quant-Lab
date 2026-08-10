import builtins
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy: Mapped[str] = mapped_column(String(100))
    result_json: Mapped[str] = mapped_column(Text)


class ExperimentRecord(Base):
    __tablename__ = "research_experiments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dataset_id: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy_name: Mapped[str | None] = mapped_column(String(100), index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(50))
    parameter_space_id: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(30), index=True)
    config_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)


class ExperimentFoldRecord(Base):
    __tablename__ = "research_experiment_folds"
    __table_args__ = (UniqueConstraint("experiment_id", "fold_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("research_experiments.id"), index=True)
    fold_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    oos_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    oos_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    oos_evaluations: Mapped[int] = mapped_column(Integer)
    selected_config_json: Mapped[str | None] = mapped_column(Text)


class EligibilityCheckRecord(Base):
    __tablename__ = "research_eligibility_checks"
    __table_args__ = (UniqueConstraint("experiment_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("research_experiments.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), index=True)
    observed_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(200))


class ParameterRunRecord(Base):
    __tablename__ = "research_parameter_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("experiment_id", "fold_id"),
            ("research_experiment_folds.experiment_id", "research_experiment_folds.fold_id"),
        ),
        UniqueConstraint("experiment_id", "fold_id", "stage", "run_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("research_experiments.id"), index=True)
    fold_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(20), index=True)
    parameter_config_id: Mapped[str] = mapped_column(String(64), index=True)
    parameter_config_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    objective_score: Mapped[float | None] = mapped_column(Float)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    closed_trade_count: Mapped[int] = mapped_column(Integer)
    failure_reason: Mapped[str | None] = mapped_column(Text)


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"Nelze serializovat {type(value)}")


def _parameter_config_id(config: object) -> str:
    canonical = json.dumps(config, default=_json_default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class RunRepository:
    def __init__(self, url: str = "sqlite:///:memory:") -> None:
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)

    def save(self, strategy: str, result: dict[str, object], created_at: datetime) -> int:
        with Session(self.engine) as session:
            row = RunRecord(
                strategy=strategy,
                result_json=json.dumps(result, default=_json_default),
                created_at=created_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def list(self) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            records: builtins.list[dict[str, object]] = []
            for row in session.query(RunRecord).all():
                created_at = row.created_at
                # SQLite časovou zónu neuchová; repository obnoví explicitní UTC.
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                records.append(
                    {
                        "id": row.id,
                        "strategy": row.strategy,
                        "created_at": created_at,
                        "result": json.loads(row.result_json),
                    }
                )
            return records

    def save_experiment(
        self,
        experiment_id: str,
        config: dict[str, object],
        result: dict[str, object],
        created_at: datetime,
    ) -> str:
        config_json = json.dumps(config, default=_json_default, sort_keys=True)
        result_json = json.dumps(result, default=_json_default, sort_keys=True)
        with Session(self.engine) as session:
            existing = session.get(ExperimentRecord, experiment_id)
            if existing is None:
                eligibility = result.get("eligibility", {})
                if not isinstance(eligibility, dict):
                    raise TypeError("Eligibility snapshot musí být mapování")
                session.add(
                    ExperimentRecord(
                        id=experiment_id,
                        dataset_id=str(result.get("dataset_id", "")) or None,
                        strategy_name=str(result.get("strategy_name", "")) or None,
                        strategy_version=str(result.get("strategy_version", "")) or None,
                        parameter_space_id=str(result.get("parameter_space_id", "")) or None,
                        decision=str(eligibility.get("decision", "")) or None,
                        config_json=config_json,
                        result_json=result_json,
                        created_at=created_at,
                    )
                )
                folds = result.get("folds", ())
                if not isinstance(folds, (list, tuple)):
                    raise TypeError("Fold snapshot musí být sekvence")
                for fold in folds:
                    if not isinstance(fold, dict):
                        raise TypeError("Fold snapshot musí být mapování")
                    session.add(
                        ExperimentFoldRecord(
                            experiment_id=experiment_id,
                            fold_id=str(fold["fold_id"]),
                            status=str(fold["status"]),
                            oos_start=datetime.fromisoformat(str(fold["oos_start"])),
                            oos_end=datetime.fromisoformat(str(fold["oos_end"])),
                            oos_evaluations=int(fold["oos_evaluations"]),
                            selected_config_json=json.dumps(
                                fold.get("selected_config"), default=_json_default, sort_keys=True
                            ),
                        )
                    )
                    for stage, key in (("TRAIN", "train_runs"), ("VALIDATION", "validation_runs")):
                        runs = fold.get(key, ())
                        if not isinstance(runs, (list, tuple)):
                            raise TypeError("Parameter runs musí být sekvence")
                        for run in runs:
                            if not isinstance(run, dict):
                                raise TypeError("Parameter run snapshot musí být mapování")
                            parameter_config = run["parameter_config"]
                            score = run.get("objective_score")
                            session.add(
                                ParameterRunRecord(
                                    run_id=str(run["run_id"]),
                                    experiment_id=experiment_id,
                                    fold_id=str(fold["fold_id"]),
                                    stage=stage,
                                    parameter_config_id=_parameter_config_id(parameter_config),
                                    parameter_config_json=json.dumps(
                                        parameter_config, default=_json_default, sort_keys=True
                                    ),
                                    status=str(run["status"]),
                                    objective_score=float(score) if score is not None else None,
                                    metrics_json=json.dumps(
                                        run["metrics"], default=_json_default, sort_keys=True
                                    )
                                    if run.get("metrics") is not None
                                    else None,
                                    closed_trade_count=int(run["closed_trades"]),
                                    failure_reason=str(run["failure_reason"])
                                    if run.get("failure_reason") is not None
                                    else None,
                                )
                            )
                checks = eligibility.get("checks", ())
                if not isinstance(checks, (list, tuple)):
                    raise TypeError("Eligibility checks musí být sekvence")
                for check in checks:
                    if not isinstance(check, dict):
                        raise TypeError("Eligibility check musí být mapování")
                    observed = check.get("observed_value")
                    threshold = check.get("threshold")
                    session.add(
                        EligibilityCheckRecord(
                            experiment_id=experiment_id,
                            name=str(check["name"]),
                            status=str(check["status"]),
                            observed_value=float(observed) if observed is not None else None,
                            threshold=float(threshold) if threshold is not None else None,
                            reason=str(check["reason"])
                            if check.get("reason") is not None
                            else None,
                        )
                    )
                session.commit()
            elif existing.config_json != config_json or existing.result_json != result_json:
                raise ValueError("Experiment identity koliduje s odlišným neměnným snapshotem")
            return experiment_id

    def get_experiment(self, experiment_id: str) -> dict[str, object] | None:
        with Session(self.engine) as session:
            row = session.get(ExperimentRecord, experiment_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "config": json.loads(row.config_json),
                "result": json.loads(row.result_json),
            }

    def get_experiment_structure(self, experiment_id: str) -> dict[str, object] | None:
        """Vrátí queryable strukturu vedle úplného neměnného reprodukčního snapshotu."""
        with Session(self.engine) as session:
            row = session.get(ExperimentRecord, experiment_id)
            if row is None:
                return None
            folds = session.query(ExperimentFoldRecord).filter_by(experiment_id=experiment_id).all()
            checks = (
                session.query(EligibilityCheckRecord)
                .filter_by(experiment_id=experiment_id)
                .order_by(EligibilityCheckRecord.id)
                .all()
            )
            parameter_runs = (
                session.query(ParameterRunRecord)
                .filter_by(experiment_id=experiment_id)
                .order_by(ParameterRunRecord.id)
                .all()
            )
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
                        "fold_id": fold.fold_id,
                        "status": fold.status,
                        "oos_start": fold.oos_start.replace(tzinfo=fold.oos_start.tzinfo or UTC),
                        "oos_end": fold.oos_end.replace(tzinfo=fold.oos_end.tzinfo or UTC),
                        "oos_evaluations": fold.oos_evaluations,
                        "selected_config": json.loads(fold.selected_config_json)
                        if fold.selected_config_json is not None
                        else None,
                    }
                    for fold in folds
                ],
                "eligibility_checks": [
                    {
                        "name": check.name,
                        "status": check.status,
                        "observed_value": check.observed_value,
                        "threshold": check.threshold,
                        "reason": check.reason,
                    }
                    for check in checks
                ],
                "parameter_runs": [
                    {
                        "run_id": run.run_id,
                        "experiment_id": run.experiment_id,
                        "fold_id": run.fold_id,
                        "stage": run.stage,
                        "parameter_config_id": run.parameter_config_id,
                        "parameter_config": json.loads(run.parameter_config_json),
                        "status": run.status,
                        "objective_score": run.objective_score,
                        "metrics": json.loads(run.metrics_json)
                        if run.metrics_json is not None
                        else None,
                        "closed_trade_count": run.closed_trade_count,
                        "failure_reason": run.failure_reason,
                    }
                    for run in parameter_runs
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

    def list_experiments(self) -> builtins.list[dict[str, object]]:
        with Session(self.engine) as session:
            return [
                {
                    "id": row.id,
                    "config": json.loads(row.config_json),
                    "result": json.loads(row.result_json),
                }
                for row in session.query(ExperimentRecord).order_by(ExperimentRecord.id)
            ]
