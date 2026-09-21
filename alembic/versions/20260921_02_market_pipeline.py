"""Provider identities and resumable market data acquisition."""

import sqlalchemy as sa

from alembic import op

revision = "20260921_02"
down_revision = "20260921_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_directory_snapshots",
        sa.Column(
            "snapshot_id", sa.String(length=64), primary_key=True, nullable=False
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
    )
    op.create_index(
        "ix_asset_directory_snapshots_received_at",
        "asset_directory_snapshots",
        ["received_at"],
    )
    op.create_table(
        "asset_directory_entries",
        sa.Column(
            "snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_asset_directory_entries_symbol", "asset_directory_entries", ["symbol"]
    )
    op.create_table(
        "market_batches",
        sa.Column("batch_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
    )
    op.create_index("ix_market_batches_created_at", "market_batches", ["created_at"])
    op.create_table(
        "market_tasks",
        sa.Column("task_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "batch_id",
            sa.String(length=64),
            sa.ForeignKey("market_batches.batch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.Column("bars", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("momentum", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("trend", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("mean_reversion", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_market_tasks_retry_at", "market_tasks", ["retry_at"])
    op.create_index("ix_market_tasks_state", "market_tasks", ["state"])
    op.create_index("ix_market_tasks_batch_id", "market_tasks", ["batch_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in (
            "asset_directory_snapshots",
            "asset_directory_entries",
            "market_batches",
        ):
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
            )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM asset_directory_snapshots LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Nelze odstranit existující identitní a datovou evidenci")
    op.drop_table("market_tasks")
    op.drop_table("market_batches")
    op.drop_table("asset_directory_entries")
    op.drop_table("asset_directory_snapshots")
