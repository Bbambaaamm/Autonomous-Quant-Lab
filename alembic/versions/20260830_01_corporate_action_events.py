"""Přidá immutable evidence a revize provider corporate actions.

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
        # Causal local receipt time. Provider timestamp je v audit sidecar tabulce níže.
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
    op.create_table(
        "corporate_action_event_audit",
        sa.Column(
            "event_id",
            sa.String(128),
            sa.ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("provider_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols_json", sa.Text, nullable=False),
        sa.Column("scope_date", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "corporate_action_revisions",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        # Interní stabilní 64znaková logická identita používaná existujícími FK.
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        # Provider identity smí být delší; nikdy se nepoužije jako corporate_actions PK.
        sa.Column("provider_action_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "instrument_id",
            sa.String(64),
            sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.String(50)),
        sa.Column("new_symbol", sa.String(32)),
        sa.UniqueConstraint(
            "provider",
            "provider_action_id",
            "payload_hash",
            name="uq_corporate_action_revision_provider_payload",
        ),
    )
    op.create_index(
        "ix_corporate_action_revisions_pit",
        "corporate_action_revisions",
        ["instrument_id", "provider", "provider_action_id", "known_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE FUNCTION reject_corporate_action_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'corporate action evidence is immutable'; END; $$"""
        )
        for table, trigger in (
            ("corporate_action_events", "corporate_action_events_immutable"),
            ("corporate_action_event_audit", "corporate_action_event_audit_immutable"),
            ("corporate_action_revisions", "corporate_action_revisions_immutable"),
        ):
            op.execute(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, trigger in (
            ("corporate_action_revisions", "corporate_action_revisions_immutable"),
            ("corporate_action_event_audit", "corporate_action_event_audit_immutable"),
            ("corporate_action_events", "corporate_action_events_immutable"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_corporate_action_evidence_mutation()")
    op.drop_table("corporate_action_revisions")
    op.drop_table("corporate_action_event_audit")
    op.drop_table("corporate_action_events")
