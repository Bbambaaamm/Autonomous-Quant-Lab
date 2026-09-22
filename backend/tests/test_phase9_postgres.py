import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from quantlab.phase4 import Phase4Repository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje autoritativní PostgreSQL 17 CI"
)


def _dsn(database: str = "quantlab") -> str:
    configured = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(configured)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", "", ""))


def _create_runtime_role() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as connection:
        connection.execute("DROP ROLE IF EXISTS quantlab_runtime_phase9")
        connection.execute(
            "CREATE ROLE quantlab_runtime_phase9 LOGIN PASSWORD 'phase9-runtime-password'"
        )
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO quantlab_runtime_phase9")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO quantlab_runtime_phase9"
        )
        connection.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO quantlab_runtime_phase9"
        )


def test_runtime_role_allows_dml_but_denies_ddl() -> None:
    _create_runtime_role()
    parsed = urlsplit(_dsn())
    runtime_dsn = urlunsplit(
        (
            parsed.scheme,
            f"quantlab_runtime_phase9:phase9-runtime-password@{parsed.hostname}:{parsed.port}",
            parsed.path,
            "",
            "",
        )
    )
    with psycopg.connect(runtime_dsn, autocommit=True) as connection:
        assert connection.execute("SELECT count(*) FROM paper_accounts").fetchone() is not None
        connection.execute("UPDATE paper_accounts SET updated_at = updated_at WHERE false")
        for statement in (
            "CREATE TABLE phase9_forbidden(id integer)",
            "ALTER TABLE paper_accounts ADD COLUMN phase9_forbidden integer",
            "DROP TABLE paper_accounts",
            "CREATE SCHEMA phase9_forbidden",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


def test_production_repository_does_not_create_schema_implicitly() -> None:
    database = f"phase9_empty_{uuid4().hex[:12]}"
    with psycopg.connect(_dsn(), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{database}"')
    try:
        Phase4Repository(
            _dsn(database).replace("postgresql://", "postgresql+psycopg://", 1),
            bootstrap_test_schema=False,
        )
        with psycopg.connect(_dsn(database)) as connection:
            tables = connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchone()
            assert tables == (0,)
    finally:
        with psycopg.connect(_dsn(), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE "{database}" WITH (FORCE)')


def _run_postgres_tool(
    script: str,
    arguments: list[str],
    environment: dict[str, str],
    backup_path: str = "/backup/phase9.dump",
):
    repository = Path(__file__).parents[2]
    docker_environment = [item for key in environment for item in ("-e", key)]
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "host",
            "-v",
            f"{repository / 'scripts'}:/scripts:ro",
            *arguments,
            *docker_environment,
            "postgres:17-alpine",
            f"/scripts/{script}",
            backup_path,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
    )


def test_backup_checksum_restore_and_fail_closed_inputs(tmp_path: Path) -> None:
    repository = Path(__file__).parents[2]
    volume = ["-v", f"{tmp_path}:/backup"]
    backup = _run_postgres_tool("db-backup.sh", volume, {"DATABASE_URL": _dsn()})
    assert backup.returncode == 0, backup.stderr
    assert (tmp_path / "phase9.dump").is_file()
    assert (tmp_path / "phase9.dump.sha256").is_file()

    relocated = tmp_path / "off-site" / "recovery"
    relocated.mkdir(parents=True)
    backup_path = relocated / "phase9.dump"
    checksum_path = relocated / "phase9.dump.sha256"
    (tmp_path / "phase9.dump").rename(backup_path)
    (tmp_path / "phase9.dump.sha256").rename(checksum_path)
    assert checksum_path.read_text().endswith("  phase9.dump\n")

    missing = subprocess.run(
        [str(repository / "scripts/db-restore.sh"), str(tmp_path / "missing.dump")],
        env={
            **os.environ,
            "RESTORE_DATABASE_URL": _dsn("missing"),
            "RESTORE_CONFIRMATION": "RESTORE_EPHEMERAL_DATABASE",
        },
        check=False,
    )
    assert missing.returncode != 0
    unconfirmed = _run_postgres_tool(
        "db-restore.sh", volume, {"RESTORE_DATABASE_URL": _dsn("missing")}
    )
    assert unconfirmed.returncode != 0

    checksum = checksum_path
    valid_checksum = checksum.read_text()
    checksum.write_text("0" * 64 + "  phase9.dump\n")
    corrupt = _run_postgres_tool(
        "db-restore.sh",
        volume,
        {
            "RESTORE_DATABASE_URL": _dsn("missing"),
            "RESTORE_CONFIRMATION": "RESTORE_EPHEMERAL_DATABASE",
        },
        "/backup/off-site/recovery/phase9.dump",
    )
    assert corrupt.returncode != 0
    checksum.write_text(valid_checksum)

    restored_database = f"phase9_restore_{uuid4().hex[:12]}"
    with psycopg.connect(_dsn(), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{restored_database}"')
    try:
        restored = _run_postgres_tool(
            "db-restore.sh",
            volume,
            {
                "RESTORE_DATABASE_URL": _dsn(restored_database),
                "RESTORE_CONFIRMATION": "RESTORE_EPHEMERAL_DATABASE",
            },
            "/backup/off-site/recovery/phase9.dump",
        )
        assert restored.returncode == 0, restored.stderr
        with psycopg.connect(_dsn(restored_database)) as connection:
            assert connection.execute("SELECT count(*) FROM alembic_version").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM paper_accounts").fetchone() is not None
    finally:
        with psycopg.connect(_dsn(), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE "{restored_database}" WITH (FORCE)')


def test_postgres_asset_directory_and_batch_are_immutable():
    import os
    from datetime import UTC, date, datetime

    from sqlalchemy import create_engine, select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import sessionmaker
    from test_market_pipeline import assets

    from quantlab.asset_directory import AssetDirectoryService
    from quantlab.market_pipeline import (
        MarketActionReceipt,
        MarketActionReview,
        MarketPipeline,
        MarketTask,
    )

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            now = datetime.now(UTC)
            snapshot = AssetDirectoryService(sessions).sync(
                assets(), actor="postgres-test", reason="Immutable identities", received_at=now
            )
            pipeline = MarketPipeline(sessions)
            batch = pipeline.create(
                snapshot,
                date(2026, 1, 2),
                date(2026, 1, 5),
                "alpaca:iex",
                "postgres-test",
                "Immutable batch",
                now,
            )
            from quantlab.market_screening import MarketScreening

            with sessions() as session, session.begin():
                task = session.scalar(select(MarketTask).where(MarketTask.batch_id == batch))
                receipt_id = uuid4().hex
                session.add(
                    MarketActionReceipt(
                        receipt_id=receipt_id,
                        task_id=task.task_id,
                        received_at=now,
                        content_hash="a" * 64,
                        payload_json='{"rows":[]}',
                    )
                )
                session.flush()
                review_id = uuid4().hex
                session.add(
                    MarketActionReview(
                        review_id=review_id,
                        task_id=task.task_id,
                        receipt_id=receipt_id,
                        reviewed_at=now,
                        content_hash="b" * 64,
                        payload_json='{"review":"test"}',
                    )
                )
                session.execute(
                    text("UPDATE market_tasks SET state='FAILED' WHERE batch_id=:id"), {"id": batch}
                )
            screen_id = MarketScreening(sessions).finalize(batch, now)
            for statement, identity in (
                (
                    "UPDATE market_action_reviews SET payload_json='{}' WHERE review_id=:id",
                    review_id,
                ),
                ("DELETE FROM market_action_reviews WHERE review_id=:id", review_id),
                (
                    "UPDATE market_action_receipts SET payload_json='{}' WHERE receipt_id=:id",
                    receipt_id,
                ),
                ("DELETE FROM market_action_receipts WHERE receipt_id=:id", receipt_id),
                ("UPDATE market_screen_runs SET eligible=999 WHERE run_id=:id", screen_id),
                ("DELETE FROM market_screen_items WHERE run_id=:id", screen_id),
                (
                    "UPDATE asset_directory_snapshots SET actor='changed' WHERE snapshot_id=:id",
                    snapshot,
                ),
                ("DELETE FROM asset_directory_entries WHERE snapshot_id=:id", snapshot),
                ("DELETE FROM market_batches WHERE batch_id=:id", batch),
            ):
                with pytest.raises(DBAPIError), sessions() as session, session.begin():
                    session.execute(text(statement), {"id": identity})
            assert pipeline.read()["total"] == 2
        finally:
            transaction.rollback()
    engine.dispose()
