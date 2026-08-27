"""Přidá explicitní B2 deployment a monitoring lineage do JobRun.

Revision ID: 20260826_01
Revises: 20260824_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_01"
down_revision = "20260824_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("deployment_id", sa.String(64), nullable=True))
    op.add_column("job_runs", sa.Column("monitoring_id", sa.String(64), nullable=True))
    op.create_index("ix_job_runs_deployment_id", "job_runs", ["deployment_id"])
    op.create_index("ix_job_runs_monitoring_id", "job_runs", ["monitoring_id"])
    op.create_foreign_key(
        "fk_job_runs_deployment_id",
        "job_runs",
        "strategy_deployments",
        ["deployment_id"],
        ["deployment_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_runs_monitoring_id",
        "job_runs",
        "paper_monitoring_runs",
        ["monitoring_id"],
        ["monitoring_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_job_runs_monitoring_id", "job_runs", type_="foreignkey")
    op.drop_constraint("fk_job_runs_deployment_id", "job_runs", type_="foreignkey")
    op.drop_index("ix_job_runs_monitoring_id", table_name="job_runs")
    op.drop_index("ix_job_runs_deployment_id", table_name="job_runs")
    op.drop_column("job_runs", "monitoring_id")
    op.drop_column("job_runs", "deployment_id")
