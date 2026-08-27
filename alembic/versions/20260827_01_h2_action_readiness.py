"""Přidá neměnnou evidence úplnosti corporate actions.

Revision ID: 20260827_01
Revises: 20260826_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_01"
down_revision = "20260826_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "corporate_action_readiness" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "corporate_action_readiness",
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_version", sa.String(40), nullable=False),
        sa.Column("instrument_id", sa.String(64), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supports_actions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("blocking_reason", sa.String(80), nullable=True),
        sa.Column("action_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_corporate_action_readiness_provider",
        "corporate_action_readiness",
        ["provider"],
    )
    op.create_index(
        "ix_corporate_action_readiness_instrument_id",
        "corporate_action_readiness",
        ["instrument_id"],
    )
    op.create_index(
        "ix_action_readiness_scope",
        "corporate_action_readiness",
        [
            "instrument_id",
            "provider",
            "requested_start",
            "requested_end",
            "knowledge_cutoff",
        ],
    )


def downgrade() -> None:
    op.drop_table("corporate_action_readiness")
