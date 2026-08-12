"""Persistentní Phase 6 lineage a explicitní research-to-paper boundary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260811_06"
down_revision = "20260811_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("research_experiments", sa.Column("snapshot_id", sa.String(64)))
    op.create_foreign_key(
        "fk_research_experiments_snapshot",
        "research_experiments",
        "dataset_snapshots",
        ["snapshot_id"],
        ["snapshot_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_research_experiments_snapshot_id", "research_experiments", ["snapshot_id"]
    )
    for name, kind in (
        ("annualized_return", sa.Float()),
        ("volatility", sa.Float()),
        ("time_weighted_exposure", sa.Float()),
        ("trade_count", sa.Integer()),
        ("total_costs", sa.Float()),
    ):
        op.add_column("research_experiments", sa.Column(name, kind))
    op.create_table(
        "strategy_deployments",
        sa.Column("deployment_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("strategy_version", sa.String(50), nullable=False),
        sa.Column("parameters_json", sa.Text, nullable=False),
        sa.Column(
            "universe_id",
            sa.String(64),
            sa.ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "paper_account_id",
            sa.String(64),
            sa.ForeignKey("paper_accounts.account_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey("research_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("strategy_deployments")
    for name in (
        "total_costs",
        "trade_count",
        "time_weighted_exposure",
        "volatility",
        "annualized_return",
    ):
        op.drop_column("research_experiments", name)
    op.drop_index(
        "ix_research_experiments_snapshot_id", table_name="research_experiments"
    )
    op.drop_constraint(
        "fk_research_experiments_snapshot", "research_experiments", type_="foreignkey"
    )
    op.drop_column("research_experiments", "snapshot_id")
