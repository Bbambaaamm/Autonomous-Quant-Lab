"""Immutable current-universe screening selections."""

import sqlalchemy as sa
from alembic import op

revision = "20260922_01"
down_revision = "20260921_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_screen_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(64),
            sa.ForeignKey("market_batches.batch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Integer(), nullable=False),
    )
    for column in ("batch_id", "created_at"):
        op.create_index(
            f"ix_market_screen_runs_{column}", "market_screen_runs", [column]
        )
    op.create_table(
        "market_screen_items",
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("market_screen_runs.run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("asset_id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("eligible", sa.Integer(), nullable=False),
        sa.Column("momentum", sa.Numeric(30, 12)),
        sa.Column("trend", sa.Numeric(30, 12)),
        sa.Column("mean_reversion", sa.Numeric(30, 12)),
        sa.Column("evidence_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_market_screen_items_symbol", "market_screen_items", ["symbol"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("market_screen_runs", "market_screen_items"):
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
            )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM market_screen_runs LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Nelze odstranit uloženou evidenci screeningu")
    op.drop_table("market_screen_items")
    op.drop_table("market_screen_runs")
