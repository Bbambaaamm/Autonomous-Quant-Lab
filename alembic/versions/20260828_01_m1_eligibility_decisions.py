"""Přidá autoritativní immutable Phase 6 eligibility rozhodnutí.

Revision ID: 20260828_01
Revises: 20260827_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260828_01"
down_revision = "20260827_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phase6_eligibility_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey("research_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("strategy_identity", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(50), nullable=False),
        sa.Column("code_sha", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("policy_json", sa.Text, nullable=False),
        sa.Column("metrics_json", sa.Text, nullable=False),
        sa.Column("rules_json", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_json", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("integrity_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "status IN ('ELIGIBLE', 'INELIGIBLE')", name="ck_phase6_eligibility_status"
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "policy_id",
            "policy_version",
            name="uq_phase6_eligibility_authority",
        ),
        sa.UniqueConstraint("integrity_hash", name="uq_phase6_eligibility_integrity"),
    )
    op.create_index(
        "ix_phase6_eligibility_experiment_id",
        "phase6_eligibility_decisions",
        ["experiment_id"],
    )
    op.create_index(
        "ix_phase6_eligibility_status", "phase6_eligibility_decisions", ["status"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE FUNCTION reject_phase6_eligibility_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'phase6 eligibility decisions are immutable'; END $$"""
        )
        op.execute(
            """CREATE TRIGGER phase6_eligibility_immutable BEFORE UPDATE OR DELETE ON phase6_eligibility_decisions FOR EACH ROW EXECUTE FUNCTION reject_phase6_eligibility_mutation()"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS phase6_eligibility_immutable ON phase6_eligibility_decisions"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_phase6_eligibility_mutation()")
    op.drop_table("phase6_eligibility_decisions")
