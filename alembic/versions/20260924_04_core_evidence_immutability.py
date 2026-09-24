"""Protect core paper-trading evidence from mutation.

Revision ID: 20260924_04
Revises: 20260924_03
"""

from alembic import op

revision = "20260924_04"
down_revision = "20260924_03"
branch_labels = None
depends_on = None

CORE_EVIDENCE_TABLES = (
    "risk_decisions",
    "paper_fills",
    "audit_events",
    "risk_events",
    "reconciliation_results",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION reject_core_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'core evidence is immutable';
        END;
        $$
        """
    )
    for table in CORE_EVIDENCE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_core_evidence_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(CORE_EVIDENCE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_core_evidence_mutation()")
