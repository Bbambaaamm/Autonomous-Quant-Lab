import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_SCRIPT = ROOT / "scripts" / "generate-dev-secrets.sh"


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
