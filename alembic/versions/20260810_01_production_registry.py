"""Zavedení produkční research registry.

Revision ID: 20260810_01
Revises:
"""

from alembic import op

from quantlab.persistence import Base

revision = "20260810_01"
down_revision = None
branch_labels = None
depends_on = None

INITIAL_TABLES = {
    "backtest_runs",
    "datasets",
    "strategies",
    "research_experiments",
    "research_experiment_folds",
    "research_eligibility_checks",
    "research_parameter_runs",
}


def upgrade() -> None:
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name in INITIAL_TABLES:
            table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in INITIAL_TABLES:
            table.drop(bind, checkfirst=False)
