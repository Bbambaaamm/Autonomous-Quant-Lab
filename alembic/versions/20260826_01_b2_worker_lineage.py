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
    bind = op.get_bind()
    # Phase 5 migrace vytváří tabulku z aktuálních ORM metadat, takže při čistém
    # upgrade už mohou být nové sloupce a indexy přítomné. Explicitní FK však
    # doplníme až zde, kdy už existují obě cílové tabulky.
    columns = {column["name"] for column in sa.inspect(bind).get_columns("job_runs")}
    if "deployment_id" not in columns:
        op.add_column(
            "job_runs", sa.Column("deployment_id", sa.String(64), nullable=True)
        )
    if "monitoring_id" not in columns:
        op.add_column(
            "job_runs", sa.Column("monitoring_id", sa.String(64), nullable=True)
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("job_runs")}
    if "ix_job_runs_deployment_id" not in indexes:
        op.create_index("ix_job_runs_deployment_id", "job_runs", ["deployment_id"])
    if "ix_job_runs_monitoring_id" not in indexes:
        op.create_index("ix_job_runs_monitoring_id", "job_runs", ["monitoring_id"])

    foreign_keys = {
        foreign_key["name"]
        for foreign_key in sa.inspect(bind).get_foreign_keys("job_runs")
    }
    if "fk_job_runs_deployment_id" not in foreign_keys:
        op.create_foreign_key(
            "fk_job_runs_deployment_id",
            "job_runs",
            "strategy_deployments",
            ["deployment_id"],
            ["deployment_id"],
            ondelete="RESTRICT",
        )
    if "fk_job_runs_monitoring_id" not in foreign_keys:
        op.create_foreign_key(
            "fk_job_runs_monitoring_id",
            "job_runs",
            "paper_monitoring_runs",
            ["monitoring_id"],
            ["monitoring_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    foreign_keys = {
        foreign_key["name"]
        for foreign_key in sa.inspect(bind).get_foreign_keys("job_runs")
    }
    if "fk_job_runs_monitoring_id" in foreign_keys:
        op.drop_constraint("fk_job_runs_monitoring_id", "job_runs", type_="foreignkey")
    if "fk_job_runs_deployment_id" in foreign_keys:
        op.drop_constraint("fk_job_runs_deployment_id", "job_runs", type_="foreignkey")

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("job_runs")}
    if "ix_job_runs_monitoring_id" in indexes:
        op.drop_index("ix_job_runs_monitoring_id", table_name="job_runs")
    if "ix_job_runs_deployment_id" in indexes:
        op.drop_index("ix_job_runs_deployment_id", table_name="job_runs")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("job_runs")}
    if "monitoring_id" in columns:
        op.drop_column("job_runs", "monitoring_id")
    if "deployment_id" in columns:
        op.drop_column("job_runs", "deployment_id")
