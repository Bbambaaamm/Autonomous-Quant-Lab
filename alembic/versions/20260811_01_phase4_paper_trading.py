"""Persistentní paper trading a risk základ Phase 4.

Revision ID: 20260811_01
Revises: 20260810_01
"""

from alembic import op

from quantlab.persistence import Base
import quantlab.phase4  # noqa: F401, E402

revision = "20260811_01"
down_revision = "20260810_01"
branch_labels = None
depends_on = None

TABLES = {
    "paper_accounts",
    "paper_positions",
    "trading_cycles",
    "risk_decisions",
    "paper_orders",
    "paper_fills",
    "audit_events",
    "risk_events",
    "reconciliation_results",
}


def upgrade() -> None:
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name in TABLES:
            table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in TABLES:
            table.drop(bind, checkfirst=False)
