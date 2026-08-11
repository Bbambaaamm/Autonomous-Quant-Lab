"""Persistentní Automation & Operations základ Phase 5."""

from alembic import op

import quantlab.automation  # noqa: F401, E402
import quantlab.phase4  # noqa: F401, E402
from quantlab.persistence import Base

revision = "20260811_03"
down_revision = "20260811_02"
branch_labels = None
depends_on = None

TABLES = {"scheduled_jobs", "job_runs", "job_attempts", "worker_heartbeats"}


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
