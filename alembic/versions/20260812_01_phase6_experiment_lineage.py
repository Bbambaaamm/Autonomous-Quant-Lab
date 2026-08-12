"""Doplnění typované identity a lineage Phase 6 experimentu."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260812_01"
down_revision = "20260811_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("research_experiments", sa.Column("idempotency_key", sa.String(64)))
    op.add_column("research_experiments", sa.Column("code_sha", sa.String(64)))
    op.add_column("research_experiments", sa.Column("seed", sa.Integer()))
    op.add_column("research_experiments", sa.Column("cost_model_json", sa.Text()))
    op.add_column(
        "research_experiments", sa.Column("selected_parameters_json", sa.Text())
    )
    op.create_index(
        "ix_research_experiments_idempotency_key",
        "research_experiments",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_experiments_idempotency_key", table_name="research_experiments"
    )
    for name in (
        "selected_parameters_json",
        "cost_model_json",
        "seed",
        "code_sha",
        "idempotency_key",
    ):
        op.drop_column("research_experiments", name)
