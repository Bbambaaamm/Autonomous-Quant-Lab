"""Persist bounded Alpaca SSE replay cursor.

Revision ID: 20260924_01
Revises: 20260923_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260924_01"
down_revision = "20260923_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_corporate_action_events_provider_occurred_event",
        "corporate_action_events",
        ["provider", "occurred_at", "event_id"],
    )
    op.create_table(
        "corporate_action_event_cursors",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column(
            "last_event_id",
            sa.String(128),
            sa.ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Preserve the previous startup ordering exactly for the one-time migration.
    # Future cursor movement is persisted transactionally in stream arrival order.
    op.execute(
        sa.text(
            """
            INSERT INTO corporate_action_event_cursors (provider, last_event_id, updated_at)
            SELECT provider, event_id, occurred_at
            FROM (
                SELECT
                    provider,
                    event_id,
                    occurred_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY provider
                        ORDER BY occurred_at DESC, event_id DESC
                    ) AS row_number
                FROM corporate_action_events
            ) ranked
            WHERE row_number = 1
            """
        )
    )


def downgrade() -> None:
    op.drop_table("corporate_action_event_cursors")
    op.drop_index(
        "ix_corporate_action_events_provider_occurred_event",
        table_name="corporate_action_events",
    )
