"""Databázové invarianty příkazů a fillů z Phase 4 auditu.

Revision ID: 20260811_02
Revises: 20260811_01
"""

from alembic import op

revision = "20260811_02"
down_revision = "20260811_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("paper_orders") as batch:
        batch.create_check_constraint("ck_paper_orders_quantity_positive", "quantity > 0")
        batch.create_check_constraint(
            "ck_paper_orders_filled_nonnegative", "filled_quantity >= 0"
        )
        batch.create_check_constraint(
            "ck_paper_orders_remaining_nonnegative", "remaining_quantity >= 0"
        )
        batch.create_check_constraint(
            "ck_paper_orders_not_overfilled", "filled_quantity <= quantity"
        )
        batch.create_check_constraint(
            "ck_paper_orders_quantity_balance",
            "remaining_quantity = quantity - filled_quantity",
        )
    with op.batch_alter_table("paper_fills") as batch:
        batch.create_check_constraint("ck_paper_fills_sequence_positive", "sequence > 0")
        batch.create_check_constraint("ck_paper_fills_quantity_positive", "quantity > 0")
        batch.create_check_constraint("ck_paper_fills_price_positive", "price > 0")
        batch.create_check_constraint(
            "ck_paper_fills_reference_price_positive", "reference_price > 0"
        )
        batch.create_check_constraint(
            "ck_paper_fills_commission_nonnegative", "commission >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("paper_fills") as batch:
        batch.drop_constraint("ck_paper_fills_commission_nonnegative", type_="check")
        batch.drop_constraint("ck_paper_fills_reference_price_positive", type_="check")
        batch.drop_constraint("ck_paper_fills_price_positive", type_="check")
        batch.drop_constraint("ck_paper_fills_quantity_positive", type_="check")
        batch.drop_constraint("ck_paper_fills_sequence_positive", type_="check")
    with op.batch_alter_table("paper_orders") as batch:
        batch.drop_constraint("ck_paper_orders_quantity_balance", type_="check")
        batch.drop_constraint("ck_paper_orders_not_overfilled", type_="check")
        batch.drop_constraint("ck_paper_orders_remaining_nonnegative", type_="check")
        batch.drop_constraint("ck_paper_orders_filled_nonnegative", type_="check")
        batch.drop_constraint("ck_paper_orders_quantity_positive", type_="check")
