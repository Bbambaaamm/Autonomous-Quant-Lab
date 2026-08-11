"""Verzované immutable snapshoty identity JobRun."""

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260811_04"
down_revision = "20260811_03"
branch_labels = None
depends_on = None


def version_snapshot(
    config_snapshot_json: str, account_id: str, job_type: str, strategy_id: str | None
) -> str:
    """Převede legacy config na oddělený config a autoritativní execution identitu."""
    config: Any = json.loads(config_snapshot_json)
    if not isinstance(config, dict):
        raise ValueError("Legacy JobRun snapshot musí být JSON objekt")
    return json.dumps(
        {
            "snapshot_version": 1,
            "identity": {
                "account_id": account_id,
                "job_type": job_type,
                "strategy_id": strategy_id,
            },
            "config": config,
        },
        sort_keys=True,
    )


def legacy_snapshot(config_snapshot_json: str) -> str:
    """Při downgrade vrátí pouze původní konfigurační část snapshotu."""
    snapshot: Any = json.loads(config_snapshot_json)
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("snapshot_version") != 1
        or not isinstance(snapshot.get("config"), dict)
    ):
        raise ValueError("Versioned JobRun snapshot má neplatný formát")
    return json.dumps(snapshot["config"], sort_keys=True)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT r.id, r.config_snapshot_json, j.account_id, j.job_type, j.strategy_id
            FROM job_runs AS r
            JOIN scheduled_jobs AS j ON j.id = r.scheduled_job_id
            """
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE job_runs SET config_snapshot_json = :snapshot WHERE id = :run_id"
            ),
            {
                "run_id": row["id"],
                "snapshot": version_snapshot(
                    row["config_snapshot_json"],
                    row["account_id"],
                    row["job_type"],
                    row["strategy_id"],
                ),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, config_snapshot_json FROM job_runs")
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE job_runs SET config_snapshot_json = :snapshot WHERE id = :run_id"
            ),
            {
                "run_id": row["id"],
                "snapshot": legacy_snapshot(row["config_snapshot_json"]),
            },
        )
