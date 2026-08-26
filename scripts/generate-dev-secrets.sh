#!/bin/sh
set -eu
umask 077
mkdir -p .secrets
python3 - <<'PY'
import base64, hashlib, os, secrets

codespaces = (
    os.environ.get("CODESPACES") == "true"
    and bool(os.environ.get("CODESPACE_NAME"))
    and bool(os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"))
)
if codespaces:
    codespace_name = os.environ["CODESPACE_NAME"]
    forwarding_domain = os.environ["GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"]
    allowed_dns = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
    if (
        any(character not in allowed_dns for character in codespace_name + forwarding_domain)
        or codespace_name.startswith("-")
        or forwarding_domain.startswith(('-', '.'))
        or ".." in forwarding_domain
    ):
        raise SystemExit("Neplatná GitHub Codespaces hostname konfigurace")
    frontend_host = f"{codespace_name}-3000.{forwarding_domain}"
    public_base_url = f"https://{frontend_host}"
    frontend_allowed_hosts = f"{frontend_host},localhost,127.0.0.1"
else:
    public_base_url = "http://localhost:3000"
    frontend_allowed_hosts = "localhost,127.0.0.1"

password = secrets.token_urlsafe(24)
salt = os.urandom(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
viewer_token = secrets.token_urlsafe(32)
operator_token = secrets.token_urlsafe(32)
admin_token = secrets.token_urlsafe(32)
values = {
    "DATABASE_URL": "postgresql+psycopg://quantlab@127.0.0.1:5432/quantlab",
    "SESSION_SECRET": secrets.token_urlsafe(32),
    "API_VIEWER_TOKEN": viewer_token,
    "API_OPERATOR_TOKEN": operator_token,
    "API_ADMIN_TOKEN": admin_token,
    "QUANTLAB_API_VIEWER_TOKEN": viewer_token,
    "QUANTLAB_API_OPERATOR_TOKEN": operator_token,
    "QUANTLAB_API_ADMIN_TOKEN": admin_token,
    "OPERATOR_USERNAME": "operator",
    "OPERATOR_ROLE": "ADMIN",
    "SESSION_MAX_AGE_SECONDS": "3600",
    "PUBLIC_BASE_URL": public_base_url,
    "FRONTEND_ALLOWED_HOSTS": frontend_allowed_hosts,
    "OPERATOR_PASSWORD_SCRYPT": f"{base64.urlsafe_b64encode(salt).decode().rstrip('=')}:{base64.urlsafe_b64encode(digest).decode().rstrip('=')}",
}
with open(".secrets/dev.env", "x", encoding="utf-8") as output:
    output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print(f"Dev credentials vytvořeny v .secrets/dev.env; jednorázové heslo: {password}")
PY
