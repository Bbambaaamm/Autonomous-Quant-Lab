"""Přidá immutable Phase 6 OOS equity evidenci.

Revision ID: 20260824_02
Revises: 20260824_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260824_02"
down_revision = "20260824_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("paper_expectation_baselines")
    }
    if "oos_equity_json" not in columns:
        op.add_column(
            "paper_expectation_baselines",
            sa.Column("oos_equity_json", sa.Text(), nullable=False, server_default="[]"),
        )
        op.alter_column("paper_expectation_baselines", "oos_equity_json", server_default=None)


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("paper_expectation_baselines")
    }
    if "oos_equity_json" in columns:
        op.drop_column("paper_expectation_baselines", "oos_equity_json")
