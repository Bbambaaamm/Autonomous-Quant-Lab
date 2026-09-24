"""Persist bounded asset-directory summary read model.

Revision ID: 20260924_03
Revises: 20260924_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260924_03"
down_revision = "20260924_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_directory_snapshot_metrics",
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "previous_snapshot_id",
            sa.String(64),
            sa.ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"),
        ),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("active", sa.Integer(), nullable=False),
        sa.Column("inactive", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.Integer(), nullable=False),
        sa.Column("no_longer_present", sa.Integer(), nullable=False),
        sa.Column("symbol_or_venue_changed", sa.Integer(), nullable=False),
        sa.Column("status_changed", sa.Integer(), nullable=False),
        sa.Column("became_inactive", sa.Integer(), nullable=False),
        sa.Column("became_active", sa.Integer(), nullable=False),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                INSERT INTO asset_directory_snapshot_metrics (
                    snapshot_id,
                    previous_snapshot_id,
                    total,
                    active,
                    inactive,
                    first_seen,
                    no_longer_present,
                    symbol_or_venue_changed,
                    status_changed,
                    became_inactive,
                    became_active
                )
                SELECT
                    s.snapshot_id,
                    prev.snapshot_id,
                    counts.total,
                    counts.active,
                    counts.inactive,
                    COALESCE(diff.first_seen, counts.total),
                    COALESCE(removed.no_longer_present, 0),
                    COALESCE(diff.symbol_or_venue_changed, 0),
                    COALESCE(diff.status_changed, 0),
                    COALESCE(diff.became_inactive, 0),
                    COALESCE(diff.became_active, 0)
                FROM asset_directory_snapshots AS s
                LEFT JOIN LATERAL (
                    SELECT p.snapshot_id
                    FROM asset_directory_snapshots AS p
                    WHERE p.received_at < s.received_at
                    ORDER BY p.received_at DESC
                    LIMIT 1
                ) AS prev ON TRUE
                CROSS JOIN LATERAL (
                    SELECT
                        count(*)::integer AS total,
                        count(*) FILTER (WHERE status = 'active')::integer AS active,
                        count(*) FILTER (WHERE status = 'inactive')::integer AS inactive
                    FROM asset_directory_entries AS e
                    WHERE e.snapshot_id = s.snapshot_id
                ) AS counts
                LEFT JOIN LATERAL (
                    SELECT
                        count(*) FILTER (WHERE p.asset_id IS NULL)::integer AS first_seen,
                        count(*) FILTER (
                            WHERE p.asset_id IS NOT NULL
                              AND (c.symbol, c.exchange) IS DISTINCT FROM (p.symbol, p.exchange)
                        )::integer AS symbol_or_venue_changed,
                        count(*) FILTER (
                            WHERE p.asset_id IS NOT NULL
                              AND c.status IS DISTINCT FROM p.status
                        )::integer AS status_changed,
                        count(*) FILTER (
                            WHERE p.status = 'active' AND c.status = 'inactive'
                        )::integer AS became_inactive,
                        count(*) FILTER (
                            WHERE p.status = 'inactive' AND c.status = 'active'
                        )::integer AS became_active
                    FROM asset_directory_entries AS c
                    LEFT JOIN asset_directory_entries AS p
                      ON p.snapshot_id = prev.snapshot_id
                     AND p.asset_id = c.asset_id
                    WHERE c.snapshot_id = s.snapshot_id
                ) AS diff ON TRUE
                LEFT JOIN LATERAL (
                    SELECT count(*)::integer AS no_longer_present
                    FROM asset_directory_entries AS p
                    LEFT JOIN asset_directory_entries AS c
                      ON c.snapshot_id = s.snapshot_id
                     AND c.asset_id = p.asset_id
                    WHERE p.snapshot_id = prev.snapshot_id
                      AND c.asset_id IS NULL
                ) AS removed ON TRUE
                """
            )
        )
        op.execute(
            """
            CREATE TRIGGER asset_directory_snapshot_metrics_immutable
            BEFORE UPDATE OR DELETE ON asset_directory_snapshot_metrics
            FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS asset_directory_snapshot_metrics_immutable "
            "ON asset_directory_snapshot_metrics"
        )
    op.drop_table("asset_directory_snapshot_metrics")
