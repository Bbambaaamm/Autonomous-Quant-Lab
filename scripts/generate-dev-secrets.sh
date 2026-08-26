#!/bin/sh
set -eu
umask 077
mkdir -p .secrets
python3 - <<'PY'
import base64, hashlib, os, secrets

password = secrets.token_urlsafe(24)
salt = os.urandom(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
viewer_token = secrets.token_urlsafe(32)
operator_token = secrets.token_urlsafe(32)
admin_token = secrets.token_urlsafe(32)

codespace_name = os.environ.get("CODESPACE_NAME", "").strip()
is_codespaces = os.environ.get("CODESPACES", "").lower() == "true"
forwarding_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").strip()
if is_codespaces and codespace_name and not forwarding_domain:
    forwarding_domain = "app.github.dev"

if codespace_name and forwarding_domain:
    frontend_host = f"{codespace_name}-3000.{forwarding_domain}"
    public_base_url = f"https://{frontend_host}"
    frontend_allowed_hosts = f"{frontend_host},localhost,127.0.0.1"
else:
    public_base_url = "http://localhost:3000"
    frontend_allowed_hosts = "localhost,127.0.0.1"

values = {
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
print(f"Dashboard URL: {public_base_url}")
PY
