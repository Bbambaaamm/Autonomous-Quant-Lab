import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from quantlab.persistence import (
    Base,
    ExperimentRecord,
    RunRepository,
    StrategyDeploymentRecord,
    create_test_schema,
)


def dataset(dataset_id: str = "a" * 64) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "content_hash": dataset_id,
        "universe": "SPY",
        "source": "fixture",
        "timeframe": "1d",
        "start_at": datetime(2024, 1, 1, tzinfo=UTC),
        "end_at": datetime(2024, 1, 2, tzinfo=UTC),
        "row_count": 2,
        "timezone": "UTC",
        "schema_version": "bars-v1",
        "storage_uri": "fixtures/sample_market_data.csv",
        "metadata": {"adjustment": "fixture"},
    }


def test_empty_database_bootstrap_contains_expected_schema(tmp_path: object) -> None:
    repository = RunRepository("sqlite:///:memory:", bootstrap_test_schema=False)
    assert inspect(repository.engine).get_table_names() == []
    create_test_schema(repository.engine)
    assert set(inspect(repository.engine).get_table_names()) == set(Base.metadata.tables)


def test_research_metadata_does_not_require_phase4_models() -> None:
    """Research test bootstrap nesmí záviset na importu Phase 4 tabulek."""
    assert not StrategyDeploymentRecord.__table__.c.paper_account_id.foreign_keys
    assert StrategyDeploymentRecord.__table__ in Base.metadata.sorted_tables


def test_dataset_registry_is_idempotent_and_fails_closed_on_conflict() -> None:
    repository = RunRepository()
    value = dataset()
    assert repository.datasets.register(value) == value["dataset_id"]
    assert repository.datasets.register(value) == value["dataset_id"]
    conflict = {**value, "row_count": 3}
    with pytest.raises(ValueError, match="koliduje"):
        repository.datasets.register(conflict)


def test_strategy_registry_is_versioned_and_immutable() -> None:
    repository = RunRepository()
    identity = repository.strategies.register("moving_average", "1.0.0", {"owner": "core"})
    assert repository.strategies.register("moving_average", "1.0.0", {"owner": "core"}) == identity
    with pytest.raises(ValueError, match="koliduje"):
        repository.strategies.register("moving_average", "1.0.0", {"owner": "other"})


def test_pagination_limits_fail_closed() -> None:
    repository = RunRepository()
    with pytest.raises(ValueError, match="pagination"):
        repository.list_experiments(limit=201)


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL integrační službu"
)
def test_postgres_migrated_schema_and_registry() -> None:
    database_url = os.environ["DATABASE_URL"]
    repository = RunRepository(database_url, bootstrap_test_schema=False)
    tables = set(inspect(repository.engine).get_table_names())
    assert set(Base.metadata.tables).issubset(tables)
    assert "alembic_version" in tables
    value = dataset("b" * 64)
    assert repository.datasets.register(value) == value["dataset_id"]
    assert repository.datasets.get(str(value["dataset_id"])) is not None


def test_dataset_timestamps_are_normalized_to_utc() -> None:
    repository = RunRepository()
    value = dataset("c" * 64)
    non_utc = timezone(timedelta(hours=2))
    value["start_at"] = datetime(2024, 1, 1, 2, tzinfo=non_utc)
    repository.datasets.register(value)
    persisted = repository.datasets.get(str(value["dataset_id"]))
    assert persisted is not None
    assert persisted["start_at"] == datetime(2024, 1, 1, tzinfo=UTC)


def test_malformed_eligibility_check_fails_closed() -> None:
    repository = RunRepository()
    with pytest.raises(TypeError, match="mapování"):
        repository._checks("experiment", {"checks": [{"name": "valid"}, "invalid"]})


def test_leaderboard_ranks_all_experiments_and_preserves_zero_metrics() -> None:
    repository = RunRepository()
    created_at = datetime(2024, 1, 1, tzinfo=UTC)
    with Session(repository.engine) as session:
        for index in range(205):
            experiment_id = f"{index:064d}"
            is_old_winner = index == 0
            is_competitor = index == 1
            result = {
                "aggregate_oos_metrics": {
                    "total_return": 0.1 if is_old_winner or is_competitor else -0.1,
                    "maximum_drawdown": 0.0 if is_old_winner else -0.2,
                    "sharpe_ratio": 0.0 if is_old_winner else -0.5,
                },
                "eligibility": {
                    "decision": "PAPER_CANDIDATE" if is_old_winner or is_competitor else "REJECTED"
                },
                "cost_stress": {"base": 0.01},
                "parameter_stability": {"profitable_fraction": 1.0 if is_old_winner else 0.0},
            }
            session.add(
                ExperimentRecord(
                    id=experiment_id,
                    created_at=created_at + timedelta(days=index),
                    status="COMPLETED",
                    config_json="{}",
                    result_json=json.dumps(result),
                )
            )
        session.commit()
    leaderboard = repository.leaderboard(limit=1)
    assert leaderboard[0]["id"] == f"{0:064d}"


def test_phase4_audit_migration_skips_constraints_created_by_current_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "20260811_02_phase4_audit_constraints.py"
    )
    spec = importlib.util.spec_from_file_location("phase4_audit_constraints", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    existing = {
        "paper_orders": set(migration.ORDER_CONSTRAINTS),
        "paper_fills": set(migration.FILL_CONSTRAINTS),
    }

    class FakeInspector:
        def get_check_constraints(self, table_name: str) -> list[dict[str, str]]:
            return [{"name": name} for name in existing[table_name]]

    monkeypatch.setattr(migration, "inspect", lambda _: FakeInspector())
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())

    def unexpected_batch(*_: object, **__: object) -> None:
        raise AssertionError("Existující constraint se nesmí vytvářet znovu")

    monkeypatch.setattr(migration.op, "batch_alter_table", unexpected_batch)
    migration.upgrade()
