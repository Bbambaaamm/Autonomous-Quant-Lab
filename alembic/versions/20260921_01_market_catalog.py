"""Immutable reference market directory snapshots, separate from trading identity."""

import sqlalchemy as sa

from alembic import op

revision = "20260921_01"
down_revision = "20260831_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_catalog_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("listing_count", sa.Integer(), nullable=False),
        sa.Column("test_count", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
    )
    op.create_index(
        "ix_market_catalog_snapshots_received_at",
        "market_catalog_snapshots",
        ["received_at"],
    )
    op.create_table(
        "market_catalog_listings",
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("market_catalog_snapshots.snapshot_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("listing_key", sa.String(80), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("exchange", sa.String(64), nullable=False),
        sa.Column("security_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    for name in ("symbol", "exchange"):
        op.create_index(
            f"ix_market_catalog_listings_{name}", "market_catalog_listings", [name]
        )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("market_catalog_snapshots", "market_catalog_listings"):
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()"
            )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM market_catalog_snapshots LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Downgrade nesmí odstranit existující katalogovou evidenci")
    op.drop_table("market_catalog_listings")
    op.drop_table("market_catalog_snapshots")
