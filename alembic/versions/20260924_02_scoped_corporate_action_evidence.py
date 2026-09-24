"""Add bounded symbol-scoped corporate-action evidence lookup.

Revision ID: 20260924_02
Revises: 20260924_01
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "20260924_02"
down_revision = "20260924_01"
branch_labels = None
depends_on = None


def _backfill_non_postgres() -> None:
    bind = op.get_bind()
    target = sa.table(
        "corporate_action_event_symbols",
        sa.column("event_id", sa.String(128)),
        sa.column("symbol", sa.String(32)),
    )
    result = bind.execute(
        sa.text("SELECT event_id, symbols_json FROM corporate_action_event_audit ORDER BY event_id")
    )
    while True:
        batch = result.fetchmany(500)
        if not batch:
            break
        values: list[dict[str, str]] = []
        for event_id, symbols_json in batch:
            decoded = json.loads(symbols_json)
            if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
                raise RuntimeError("Corporate-action symbol backfill narazil na neplatný audit")
            for symbol in sorted({item.strip().upper() for item in decoded if item.strip()}):
                values.append({"event_id": event_id, "symbol": symbol})
        if values:
            bind.execute(target.insert(), values)


def upgrade() -> None:
    op.create_table(
        "corporate_action_event_symbols",
        sa.Column(
            "event_id",
            sa.String(128),
            sa.ForeignKey("corporate_action_events.event_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(32), primary_key=True),
    )
    op.create_index(
        "ix_corporate_action_event_symbols_symbol_event",
        "corporate_action_event_symbols",
        ["symbol", "event_id"],
    )
    op.create_index(
        "ix_corporate_action_events_provider_action_occurred_event",
        "corporate_action_events",
        ["provider", "provider_action_id", "occurred_at", "event_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                INSERT INTO corporate_action_event_symbols (event_id, symbol)
                SELECT DISTINCT
                    audit.event_id,
                    upper(btrim(symbol.value))
                FROM corporate_action_event_audit AS audit
                CROSS JOIN LATERAL
                    jsonb_array_elements_text(audit.symbols_json::jsonb) AS symbol(value)
                WHERE btrim(symbol.value) <> ''
                ON CONFLICT DO NOTHING
                """
            )
        )
        op.execute(
            """
            CREATE TRIGGER corporate_action_event_symbols_immutable
            BEFORE UPDATE OR DELETE ON corporate_action_event_symbols
            FOR EACH ROW EXECUTE FUNCTION reject_corporate_action_evidence_mutation()
            """
        )
    else:
        _backfill_non_postgres()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS corporate_action_event_symbols_immutable "
            "ON corporate_action_event_symbols"
        )
    op.drop_index(
        "ix_corporate_action_events_provider_action_occurred_event",
        table_name="corporate_action_events",
    )
    op.drop_index(
        "ix_corporate_action_event_symbols_symbol_event",
        table_name="corporate_action_event_symbols",
    )
    op.drop_table("corporate_action_event_symbols")
