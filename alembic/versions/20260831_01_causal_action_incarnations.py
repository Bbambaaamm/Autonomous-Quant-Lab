"""Rozšíří identitu corporate-action revize o kauzální incarnation.

Revision ID: 20260831_01
Revises: 20260830_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260831_01"
down_revision = "20260830_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existující immutable řádky ani jejich původní PK se nemění. Nový invariant pouze
    # dovolí návrat stejného payloadu po intervenující revizi nebo DELETE.
    op.drop_constraint(
        "uq_corporate_action_revision_provider_payload",
        "corporate_action_revisions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_corporate_action_revision_provider_payload",
        "corporate_action_revisions",
        ["provider", "provider_action_id", "payload_hash", "known_at"],
    )


def downgrade() -> None:
    duplicate_incarnations = (
        op.get_bind()
        .execute(
            sa.text(
                """SELECT 1
            FROM corporate_action_revisions
            GROUP BY provider, provider_action_id, payload_hash
            HAVING COUNT(*) > 1
            LIMIT 1"""
            )
        )
        .first()
    )
    if duplicate_incarnations is not None:
        # Starý invariant neumí novou legitimní historii vyjádřit. Při downgrade směrem
        # k base ji proto nepřepisujeme ani nemažeme; následující starší migrace tabulku
        # bezpečně odstraní. Mezistupeň si ponechá novější bezpečnější UNIQUE constraint.
        return
    op.drop_constraint(
        "uq_corporate_action_revision_provider_payload",
        "corporate_action_revisions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_corporate_action_revision_provider_payload",
        "corporate_action_revisions",
        ["provider", "provider_action_id", "payload_hash"],
    )
