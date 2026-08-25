import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from quantlab.phase4 import Phase4Repository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Vyžaduje autoritativní PostgreSQL 17 CI"
)


def _dsn(database: str = "quantlab") -> str:
    return f"postgresql://quantlab:phase9-ci-password@localhost:5432/{database}"


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
    runtime_dsn = (
        "postgresql://quantlab_runtime_phase9:phase9-runtime-password@localhost:5432/quantlab"
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
            f"postgresql+psycopg://quantlab:phase9-ci-password@localhost:5432/{database}",
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


def _run_postgres_tool(script: str, arguments: list[str], environment: dict[str, str]):
    repository = Path(__file__).parents[2]
    docker_environment = [item for key in environment for item in ("-e", key)]
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{repository / 'scripts'}:/scripts:ro",
            *arguments,
            *docker_environment,
            "postgres:17-alpine",
            f"/scripts/{script}",
            "/backup/phase9.dump",
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

    checksum = tmp_path / "phase9.dump.sha256"
    valid_checksum = checksum.read_text()
    checksum.write_text("0" * 64 + "  /backup/phase9.dump\n")
    corrupt = _run_postgres_tool(
        "db-restore.sh",
        volume,
        {
            "RESTORE_DATABASE_URL": _dsn("missing"),
            "RESTORE_CONFIRMATION": "RESTORE_EPHEMERAL_DATABASE",
        },
    )
    assert corrupt.returncode != 0
    checksum.write_text(valid_checksum.replace(str(tmp_path), "/backup"))

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
        )
        assert restored.returncode == 0, restored.stderr
        with psycopg.connect(_dsn(restored_database)) as connection:
            assert connection.execute("SELECT count(*) FROM alembic_version").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM paper_accounts").fetchone() is not None
    finally:
        with psycopg.connect(_dsn(), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE "{restored_database}" WITH (FORCE)')
