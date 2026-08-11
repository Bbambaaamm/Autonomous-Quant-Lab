"""Databázové invarianty příkazů a fillů z Phase 4 auditu.

Revision ID: 20260811_02
Revises: 20260811_01
"""

from alembic import op
from sqlalchemy import inspect

revision = "20260811_02"
down_revision = "20260811_01"
branch_labels = None
depends_on = None

ORDER_CONSTRAINTS = {
    "ck_paper_orders_quantity_positive": "quantity > 0",
    "ck_paper_orders_filled_nonnegative": "filled_quantity >= 0",
    "ck_paper_orders_remaining_nonnegative": "remaining_quantity >= 0",
    "ck_paper_orders_not_overfilled": "filled_quantity <= quantity",
    "ck_paper_orders_quantity_balance": "remaining_quantity = quantity - filled_quantity",
}

FILL_CONSTRAINTS = {
    "ck_paper_fills_sequence_positive": "sequence > 0",
    "ck_paper_fills_quantity_positive": "quantity > 0",
    "ck_paper_fills_price_positive": "price > 0",
    "ck_paper_fills_reference_price_positive": "reference_price > 0",
    "ck_paper_fills_commission_nonnegative": "commission >= 0",
}


def _constraint_names(table_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspect(op.get_bind()).get_check_constraints(table_name)
        if constraint["name"] is not None
    }


def _create_missing_constraints(table_name: str, definitions: dict[str, str]) -> None:
    existing = _constraint_names(table_name)
    missing = {name: condition for name, condition in definitions.items() if name not in existing}
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch:
        for name, condition in missing.items():
            batch.create_check_constraint(name, condition)


def _drop_existing_constraints(table_name: str, definitions: dict[str, str]) -> None:
    existing = _constraint_names(table_name)
    present = [name for name in definitions if name in existing]
    if not present:
        return
    with op.batch_alter_table(table_name) as batch:
        for name in reversed(present):
            batch.drop_constraint(name, type_="check")


def upgrade() -> None:
    _create_missing_constraints("paper_orders", ORDER_CONSTRAINTS)
    _create_missing_constraints("paper_fills", FILL_CONSTRAINTS)


def downgrade() -> None:
    _drop_existing_constraints("paper_fills", FILL_CONSTRAINTS)
    _drop_existing_constraints("paper_orders", ORDER_CONSTRAINTS)
