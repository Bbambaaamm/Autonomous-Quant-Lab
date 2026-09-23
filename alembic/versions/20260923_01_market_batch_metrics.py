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
    op.add_column(
        "market_tasks",
        sa.Column("telemetry_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "market_batch_metrics",
        sa.Column(
            "batch_id",
            sa.String(64),
            sa.ForeignKey("market_batches.batch_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("metrics_json", sa.Text(), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER market_batch_metrics_immutable "
            "BEFORE UPDATE OR DELETE ON market_batch_metrics "
            "FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    evidence = bind.execute(
        sa.text(
            """
            SELECT 1 FROM market_batch_metrics
            UNION ALL
            SELECT 1 FROM market_batches WHERE telemetry_version IS NOT NULL
            UNION ALL
            SELECT 1 FROM market_tasks
            WHERE http_requests <> 0
               OR response_bytes <> 0
               OR peak_rss_kib <> 0
               OR telemetry_complete IS FALSE
            LIMIT 1
            """
        )
    ).first()
    if evidence:
        raise RuntimeError("Nelze odstranit existující provozní telemetry evidence")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER market_batch_metrics_immutable ON market_batch_metrics")
    op.drop_table("market_batch_metrics")
    op.drop_column("market_tasks", "telemetry_complete")
    op.drop_column("market_tasks", "peak_rss_kib")
    op.drop_column("market_tasks", "response_bytes")
    op.drop_column("market_tasks", "http_requests")
    op.drop_column("market_batches", "database_bytes_at_start")
    op.drop_column("market_batches", "telemetry_version")
