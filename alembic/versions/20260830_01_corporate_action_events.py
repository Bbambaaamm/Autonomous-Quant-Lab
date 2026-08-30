"""Přidá immutable evidence provider eventů corporate actions.

Revision ID: 20260830_01
Revises: 20260828_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_01"
down_revision = "20260828_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corporate_action_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("provider_action_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "action IN ('insert', 'update', 'delete')", name="ck_action_event_action"
        ),
    )
    op.create_index(
        "ix_corporate_action_events_provider", "corporate_action_events", ["provider"]
    )
    op.create_index(
        "ix_corporate_action_events_provider_action_id",
        "corporate_action_events",
        ["provider_action_id"],
    )


def downgrade() -> None:
    op.drop_table("corporate_action_events")
