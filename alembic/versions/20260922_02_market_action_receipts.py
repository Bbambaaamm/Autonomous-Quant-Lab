"""Current REST action receipts, distinct from historical SSE readiness."""

import sqlalchemy as sa
from alembic import op

revision = "20260922_02"
down_revision = "20260922_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_action_receipts",
        sa.Column("receipt_id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(64),
            sa.ForeignKey("market_tasks.task_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_market_action_receipts_task_id", "market_action_receipts", ["task_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER market_action_receipts_immutable BEFORE UPDATE OR DELETE "
            "ON market_action_receipts FOR EACH ROW "
            "EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM market_action_receipts LIMIT 1")).first():
        raise RuntimeError("Nelze odstranit přijatou evidenci tržních událostí")
    op.drop_table("market_action_receipts")
