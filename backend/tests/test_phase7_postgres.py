import os

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from quantlab.phase7 import PaperMonitoringPolicyRecord

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje PostgreSQL CI service"
)


def test_phase7_migration_tables_and_policy_constraint_exist() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PaperMonitoringPolicyRecord)) == 0


def test_open_monitoring_partial_unique_index_is_installed() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        names = set(connection.execute(select(func.unnest(func.current_schemas(False)))).scalars())
        assert "public" in names
        index = connection.exec_driver_sql(
            "SELECT indexdef FROM pg_indexes WHERE indexname='uq_open_monitoring_per_account'"
        ).scalar_one()
        assert "UNIQUE" in index and "SUSPENDED" in index
