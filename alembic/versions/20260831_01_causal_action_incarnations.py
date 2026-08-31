"""Rozšíří identitu corporate-action revize o kauzální incarnation.

Revision ID: 20260831_01
Revises: 20260830_02
"""

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
    # Downgrade je bezpečný jen bez více incarnations stejného payloadu; databáze při
    # porušení původního invariantu transakčně odmítne vytvoření constraintu.
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
