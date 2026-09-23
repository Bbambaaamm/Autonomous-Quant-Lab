"""Persist exact broad-market transport and resource telemetry."""

import sqlalchemy as sa
from alembic import op

revision = "20260923_01"
down_revision = "20260922_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_batches", sa.Column("telemetry_version", sa.Integer(), nullable=True))
    op.add_column(
        "market_batches", sa.Column("database_bytes_at_start", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "market_batches", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("market_batches", sa.Column("metrics_json", sa.Text(), nullable=True))
    op.add_column(
        "market_tasks",
        sa.Column("http_requests", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "market_tasks",
        sa.Column("response_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "market_tasks",
        sa.Column("peak_rss_kib", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("market_tasks", "peak_rss_kib")
    op.drop_column("market_tasks", "response_bytes")
    op.drop_column("market_tasks", "http_requests")
    op.drop_column("market_batches", "metrics_json")
    op.drop_column("market_batches", "completed_at")
    op.drop_column("market_batches", "database_bytes_at_start")
    op.drop_column("market_batches", "telemetry_version")
