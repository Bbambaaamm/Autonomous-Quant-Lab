import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab import api
from quantlab.config import Settings
from quantlab.security import limiter


def test_backup_fails_when_checksum_cannot_be_computed(tmp_path: Path) -> None:
    repository = Path(__file__).parents[2]
    tools = tmp_path / "tools"
    tools.mkdir()
    pg_dump = tools / "pg_dump"
    pg_dump.write_text('#!/bin/sh\nprintf "valid dump" > "${4#--file=}"\n')
    pg_dump.chmod(0o700)
    sha256sum = tools / "sha256sum"
    sha256sum.write_text("#!/bin/sh\nexit 74\n")
    sha256sum.chmod(0o700)
    backup = tmp_path / "backup.dump"

    result = subprocess.run(
        [str(repository / "scripts/db-backup.sh"), str(backup)],
        env={
            **os.environ,
            "DATABASE_URL": "postgresql://synthetic.invalid/quantlab",
            "PATH": f"{tools}:{os.environ['PATH']}",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Výpočet checksumu backupu selhal" in result.stderr
    assert backup.is_file()
    assert not backup.with_suffix(".dump.sha256").exists()


def client(role: str = "admin") -> TestClient:
    value = TestClient(api.app)
    value.headers["Authorization"] = f"Bearer {getattr(api.settings, f'api_{role}_token')}"
    return value


def test_health_public_but_other_routes_require_auth() -> None:
    value = TestClient(api.app)
    del value.headers["Authorization"]
    assert value.get("/healthz").json() == {"status": "ok"}
    denied = value.get("/operator/overview")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"


def test_rbac_matrix_and_actor_evidence() -> None:
    viewer = client("viewer")
    operator = client("operator")
    assert viewer.get("/operator/risk").status_code == 200
    halt = {"confirmation": "HALT", "reason": "bezpečnostní test"}
    resume = {"confirmation": "RESUME", "reason": "bezpečnostní test"}
    assert viewer.post("/operator/risk/halt", json=halt).status_code == 403
    assert operator.post("/operator/risk/halt", json=halt).status_code == 200
    assert operator.post("/operator/risk/resume", json=resume).status_code == 403
    events = client().get("/operator/audit?event_type=KILL_SWITCH_MANUAL_HALT").json()["items"]
    actor = json.loads(events[0]["payload_json"])["security_actor"]
    assert actor == {
        "actor_id": "api-operator",
        "actor_role": "OPERATOR",
        "authentication": "bearer",
    }


def test_host_cors_and_spoofed_forwarded_for() -> None:
    value = client()
    assert value.get("/operator/overview", headers={"Host": "evil.example"}).status_code == 400
    response = value.options(
        "/operator/overview",
        headers={"Origin": "https://evil.example", "X-Forwarded-For": "1.2.3.4"},
    )
    assert response.headers.get("access-control-allow-origin") is None


def test_production_configuration_fails_closed() -> None:
    base = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://runtime@db/quantlab",
        "trusted_hosts": "quant.example",
    }
    overrides = (
        {},
        {
            "api_viewer_token": "short",
            "api_operator_token": "short",
            "api_admin_token": "short",
        },
        {"trusted_hosts": "*"},
        {"database_url": "sqlite:///bad.db"},
    )
    for override in overrides:
        try:
            Settings(**(base | override))
        except ValueError:
            pass
        else:
            raise AssertionError("Nebezpečná production konfigurace byla přijata")


def test_rate_limit_http_boundary_has_retry_after() -> None:
    original = api.settings.api_read_limit
    limiter.events.clear()
    api.settings.api_read_limit = 2
    try:
        admin = client()
        assert admin.get("/operator/risk").status_code == 200
        assert admin.get("/operator/risk").status_code == 200
        rejected = admin.get("/operator/risk")
        assert rejected.status_code == 429
        assert int(rejected.headers["retry-after"]) >= 1
        assert client("viewer").get("/operator/risk").status_code == 200
    finally:
        api.settings.api_read_limit = original
        limiter.events.clear()


def test_production_worker_configuration_requires_enabled_postgres_runtime() -> None:
    secret = "x" * 48
    base = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://runtime@db/quantlab",
        "trusted_hosts": "worker",
        "api_viewer_token": "v" + secret,
        "api_operator_token": "o" + secret,
        "api_admin_token": "a" + secret,
        "worker_require_production": True,
    }
    with pytest.raises(ValueError, match="AUTOMATION_ENABLED"):
        Settings(**base).validate_worker_runtime()
    with pytest.raises(ValueError, match="APP_ENV=production"):
        Settings(
            **(base | {"app_env": "development"}), automation_enabled=True
        ).validate_worker_runtime()
    configured = Settings(**base, automation_enabled=True)
    configured.validate_worker_runtime()


def test_staging_backup_creates_checksum_and_prunes_expired_dump(tmp_path: Path) -> None:
    repository = Path(__file__).parents[2]
    tools = tmp_path / "tools"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text('#!/bin/sh\nprintf "portable staging dump"\n')
    docker.chmod(0o700)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "quantlab-daily-20260101T000000Z.dump"
    old.write_text("old")
    old.with_suffix(".dump.sha256").write_text("old checksum\n")
    stale = 20 * 24 * 60 * 60
    old_time = int(__import__("time").time()) - stale
    os.utime(old, (old_time, old_time))
    os.utime(old.with_suffix(".dump.sha256"), (old_time, old_time))

    result = subprocess.run(
        ["sh", str(repository / "scripts/staging-db-backup.sh")],
        env={
            **os.environ,
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_RETENTION_DAYS": "14",
            "DOCKER_BIN": str(docker),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    dumps = list(backup_dir.glob("quantlab-daily-*.dump"))
    assert len(dumps) == 1
    assert dumps[0].stat().st_size > 0
    assert dumps[0].with_suffix(".dump.sha256").is_file()
    assert not old.exists()
    assert not old.with_suffix(".dump.sha256").exists()


def test_schema_migration_detector_only_flags_schema_boundary(tmp_path: Path) -> None:
    repository = Path(__file__).parents[2]
    work = tmp_path / "repo"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=work, check=True)
    (work / "alembic" / "versions").mkdir(parents=True)
    (work / "scripts").mkdir()
    (work / "alembic.ini").write_text("[alembic]\n")
    (work / "scripts" / "configure-runtime-role.sql").write_text("-- grants\n")
    (work / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=work, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip()

    (work / "README.md").write_text("application-only\n")
    subprocess.run(["git", "add", "README.md"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "app"], cwd=work, check=True)
    app = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip()
    detector = repository / "scripts/schema-migration-needed.sh"
    ordinary = subprocess.check_output(["sh", str(detector), base, app], cwd=work, text=True)
    assert ordinary.strip() == "no"

    (work / "alembic" / "versions" / "next.py").write_text('revision = "next"\n')
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "schema"], cwd=work, check=True)
    schema = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip()
    migration = subprocess.check_output(["sh", str(detector), app, schema], cwd=work, text=True)
    assert migration.strip() == "yes"
