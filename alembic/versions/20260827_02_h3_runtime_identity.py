"""Přidá neměnnou identitu schválené paper runtime konfigurace.

Revision ID: 20260827_02
Revises: 20260827_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_02"
down_revision = "20260827_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_deployments", sa.Column("runtime_manifest_json", sa.Text()))
    op.add_column(
        "strategy_deployments", sa.Column("runtime_manifest_hash", sa.String(64))
    )
    op.add_column(
        "strategy_deployments", sa.Column("runtime_manifest_version", sa.Integer())
    )
    op.create_index(
        "ix_strategy_deployments_runtime_manifest_hash",
        "strategy_deployments",
        ["runtime_manifest_hash"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_approved_runtime_manifest_mutation() RETURNS trigger AS $$
            BEGIN
              IF OLD.status = 'APPROVED' AND
                 (OLD.runtime_manifest_json IS DISTINCT FROM NEW.runtime_manifest_json OR
                  OLD.runtime_manifest_hash IS DISTINCT FROM NEW.runtime_manifest_hash OR
                  OLD.runtime_manifest_version IS DISTINCT FROM NEW.runtime_manifest_version)
              THEN RAISE EXCEPTION 'approved runtime manifest is immutable'; END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_approved_runtime_manifest_immutable
            BEFORE UPDATE ON strategy_deployments FOR EACH ROW
            EXECUTE FUNCTION reject_approved_runtime_manifest_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER trg_approved_runtime_manifest_immutable ON strategy_deployments"
        )
        op.execute("DROP FUNCTION reject_approved_runtime_manifest_mutation()")
    op.drop_index(
        "ix_strategy_deployments_runtime_manifest_hash", "strategy_deployments"
    )
    op.drop_column("strategy_deployments", "runtime_manifest_version")
    op.drop_column("strategy_deployments", "runtime_manifest_hash")
    op.drop_column("strategy_deployments", "runtime_manifest_json")
