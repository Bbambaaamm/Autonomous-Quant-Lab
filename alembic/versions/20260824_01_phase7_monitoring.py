"""Phase 7 paper performance monitoring and lifecycle.

Revision ID: 20260824_01
Revises: 20260812_01
"""

from alembic import op

import quantlab.phase7  # noqa: F401
from quantlab.persistence import Base

revision = "20260824_01"
down_revision = "20260812_01"
branch_labels = None
depends_on = None

TABLES = (
    "paper_monitoring_policies",
    "paper_expectation_baselines",
    "paper_monitoring_runs",
    "paper_performance_snapshots",
    "paper_performance_evaluations",
    "paper_deployment_cycles",
    "paper_corporate_action_applications",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=False)
    op.create_index(
        "uq_open_monitoring_per_account",
        "paper_monitoring_runs",
        ["paper_account_id"],
        unique=True,
        postgresql_where=__import__("sqlalchemy").text(
            "state IN ('ACTIVE', 'PAUSED', 'SUSPENDED')"
        ),
        sqlite_where=__import__("sqlalchemy").text(
            "state IN ('ACTIVE', 'PAUSED', 'SUSPENDED')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_open_monitoring_per_account", table_name="paper_monitoring_runs")
    for name in reversed(TABLES):
        op.drop_table(name)
