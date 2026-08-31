"""Přidá immutable canonicalizaci legacy corporate-action revizí.

Revision ID: 20260831_02
Revises: 20260831_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260831_02"
down_revision = "20260831_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migrace pouze připraví auditní sidecar. Rozhodnutí vyžaduje runtime SSE evidence.
    op.create_table(
        "corporate_action_revision_canonicalizations",
        sa.Column(
            "superseded_revision_id",
            sa.String(64),
            sa.ForeignKey("corporate_action_revisions.revision_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "canonical_revision_id",
            sa.String(64),
            sa.ForeignKey("corporate_action_revisions.revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_action_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column(
            "source_event_id",
            sa.String(128),
            sa.ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "superseded_revision_id <> canonical_revision_id",
            name="ck_action_canonicalization_distinct_revisions",
        ),
    )
    op.create_index(
        "ix_action_canonicalizations_canonical",
        "corporate_action_revision_canonicalizations",
        ["canonical_revision_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER corporate_action_revision_canonicalizations_immutable "
            "BEFORE UPDATE OR DELETE ON corporate_action_revision_canonicalizations "
            "FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
        )


def downgrade() -> None:
    canonicalization_exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM corporate_action_revision_canonicalizations LIMIT 1"))
        .first()
    )
    if canonicalization_exists is not None:
        raise RuntimeError(
            "Downgrade 20260831_02 není možný bez ztráty immutable corporate-action "
            "canonicalization evidence"
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS corporate_action_revision_canonicalizations_immutable "
            "ON corporate_action_revision_canonicalizations"
        )
    op.drop_index(
        "ix_action_canonicalizations_canonical",
        table_name="corporate_action_revision_canonicalizations",
    )
    op.drop_table("corporate_action_revision_canonicalizations")
