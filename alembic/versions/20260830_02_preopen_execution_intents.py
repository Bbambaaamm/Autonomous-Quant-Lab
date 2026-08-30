"""Přidá immutable pre-open execution intent evidence.

Revision ID: 20260830_02
Revises: 20260830_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_02"
down_revision = "20260830_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preopen_execution_intents",
        sa.Column("intent_id", sa.String(64), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("strategy_deployments.deployment_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("instrument_id", sa.String(40), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False),
        sa.Column("execution_session", sa.Date(), nullable=False),
        sa.Column(
            "intended_execution_open", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sizing_reference_price", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "sizing_reference_known_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("signal_observation_ids_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "deployment_id",
            "execution_session",
            "instrument_id",
            name="uq_preopen_intent_logical_identity",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_preopen_intent_quantity_positive"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_preopen_intent_side"),
        sa.CheckConstraint(
            "order_type = 'MARKET'", name="ck_preopen_intent_order_type"
        ),
        sa.CheckConstraint(
            "sizing_reference_price > 0", name="ck_preopen_intent_reference_positive"
        ),
        sa.CheckConstraint(
            "created_at < intended_execution_open",
            name="ck_preopen_intent_created_preopen",
        ),
        sa.CheckConstraint(
            "decision_time < intended_execution_open",
            name="ck_preopen_intent_decision_preopen",
        ),
        sa.CheckConstraint(
            "sizing_reference_known_at <= decision_time",
            name="ck_preopen_intent_reference_causal",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE FUNCTION reject_preopen_intent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pre-open execution intents are immutable'; END; $$"""
        )
        op.execute(
            "CREATE TRIGGER preopen_execution_intents_immutable BEFORE UPDATE OR DELETE ON preopen_execution_intents FOR EACH ROW EXECUTE FUNCTION reject_preopen_intent_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS preopen_execution_intents_immutable ON preopen_execution_intents"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_preopen_intent_mutation()")
    op.drop_table("preopen_execution_intents")
