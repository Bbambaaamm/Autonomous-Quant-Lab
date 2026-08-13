"""Phase 6 market data, PIT universe and immutable snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260811_05"
down_revision = "20260811_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("calendar", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_to", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"])
    op.create_table(
        "instrument_symbol_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(64),
            sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("instrument_id", "symbol", "valid_from"),
    )
    op.create_table(
        "market_data_ingestions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument_count", sa.Integer, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text),
    )
    op.create_table(
        "market_observations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("observation_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "instrument_id",
            sa.String(64),
            sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_id",
            sa.String(64),
            sa.ForeignKey("market_data_ingestions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("session_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.String(50), nullable=False),
        sa.Column("high", sa.String(50), nullable=False),
        sa.Column("low", sa.String(50), nullable=False),
        sa.Column("close", sa.String(50), nullable=False),
        sa.Column("volume", sa.String(50), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.UniqueConstraint("instrument_id", "provider", "session_date", "revision"),
    )
    op.create_index(
        "ix_observation_asof",
        "market_observations",
        ["instrument_id", "session_date", "observed_at"],
    )
    op.create_table(
        "corporate_actions",
        sa.Column("action_id", sa.String(64), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(64),
            sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.String(50)),
        sa.Column("new_symbol", sa.String(32)),
    )
    op.create_table(
        "universe_definitions",
        sa.Column("universe_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "universe_memberships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "universe_id",
            sa.String(64),
            sa.ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "instrument_id",
            sa.String(64),
            sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("universe_id", "instrument_id", "valid_from"),
    )
    op.create_index(
        "ix_universe_pit",
        "universe_memberships",
        ["universe_id", "valid_from", "valid_to", "known_at"],
    )
    op.create_table(
        "dataset_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("calendar_identity", sa.String(100), nullable=False),
        sa.Column(
            "universe_id",
            sa.String(64),
            sa.ForeignKey("universe_definitions.universe_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("coverage", sa.String(50), nullable=False),
        sa.Column("manifest_json", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "provider",
            "calendar_identity",
            "universe_id",
            "start_at",
            "end_at",
            "as_of",
            "content_hash",
        ),
    )


def downgrade() -> None:
    for table in (
        "dataset_snapshots",
        "universe_memberships",
        "universe_definitions",
        "corporate_actions",
        "market_observations",
        "market_data_ingestions",
        "instrument_symbol_history",
        "instruments",
    ):
        op.drop_table(table)
