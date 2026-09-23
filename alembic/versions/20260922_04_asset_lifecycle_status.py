"""Track provider active/inactive lifecycle in immutable asset-directory snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20260922_04"
down_revision = "20260922_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset_directory_entries",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_index(
        "ix_asset_directory_entries_status",
        "asset_directory_entries",
        ["snapshot_id", "status"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_asset_directory_entries_status",
            "asset_directory_entries",
            "status IN ('active','inactive')",
        )
    op.alter_column(
        "asset_directory_entries",
        "status",
        server_default=None,
        existing_type=sa.String(16),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT 1 FROM asset_directory_entries WHERE status <> 'active' LIMIT 1")
    ).first():
        raise RuntimeError("Nelze odstranit zachycenou inactive lifecycle evidenci")
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "ck_asset_directory_entries_status",
            "asset_directory_entries",
            type_="check",
        )
    op.drop_index("ix_asset_directory_entries_status", table_name="asset_directory_entries")
    op.drop_column("asset_directory_entries", "status")
