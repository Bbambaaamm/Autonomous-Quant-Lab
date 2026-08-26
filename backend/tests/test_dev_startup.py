import os
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_SCRIPT = ROOT / "scripts" / "generate-dev-secrets.sh"
RESET_SCRIPT = ROOT / "scripts" / "reset-dev-secrets.sh"


def generate_environment(tmp_path: Path, environment: dict[str, str]) -> dict[str, str]:
    result = subprocess.run(
        [str(SECRET_SCRIPT)],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], **environment},
        check=True,
        capture_output=True,
        text=True,
    )
    assert "jednorázové heslo" in result.stdout
    return dict(
        line.split("=", maxsplit=1)
        for line in (tmp_path / ".secrets" / "dev.env").read_text().splitlines()
    )


def test_codespaces_urls_are_derived_from_verified_environment(tmp_path: Path) -> None:
    values = generate_environment(
        tmp_path,
        {
            "CODESPACES": "true",
            "CODESPACE_NAME": "probable-space",
            "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
        },
    )
    host = "probable-space-3000.app.github.dev"
    assert values["PUBLIC_BASE_URL"] == f"https://{host}"
    assert values["FRONTEND_ALLOWED_HOSTS"] == f"{host},localhost,127.0.0.1"
    assert values["OPERATOR_PASSWORD_SCRYPT"] != ""


def test_incomplete_codespaces_environment_uses_localhost(tmp_path: Path) -> None:
    values = generate_environment(
        tmp_path,
        {"CODESPACES": "true", "CODESPACE_NAME": "probable-space"},
    )
    assert values["PUBLIC_BASE_URL"] == "http://localhost:3000"
    assert values["FRONTEND_ALLOWED_HOSTS"] == "localhost,127.0.0.1"


def test_credentials_reset_fails_while_dashboard_is_running(tmp_path: Path) -> None:
    secrets_directory = tmp_path / ".secrets"
    secrets_directory.mkdir()
    secret_file = secrets_directory / "dev.env"
    secret_file.write_text("SENTINEL=unchanged\n")
    scripts_directory = tmp_path / "scripts"
    scripts_directory.symlink_to(ROOT / "scripts", target_is_directory=True)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 3000))
        listener.listen()
        result = subprocess.run(
            [str(RESET_SCRIPT)],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
        )

    assert result.returncode != 0
    assert "zastavte dashboard i API" in result.stderr
    assert secret_file.read_text() == "SENTINEL=unchanged\n"
