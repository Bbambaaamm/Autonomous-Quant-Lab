"""Doplnění typované identity a lineage Phase 6 experimentu."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260812_01"
down_revision = "20260811_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("research_experiments")
    }
    for name, kind in (
        ("idempotency_key", sa.String(64)),
        ("code_sha", sa.String(64)),
        ("seed", sa.Integer()),
        ("cost_model_json", sa.Text()),
        ("selected_parameters_json", sa.Text()),
    ):
        # Initial migrace používá aktuální metadata, proto na prázdné DB sloupec už existuje.
        if name not in columns:
            op.add_column("research_experiments", sa.Column(name, kind))
    indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("research_experiments")
    }
    if "ix_research_experiments_idempotency_key" not in indexes:
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
