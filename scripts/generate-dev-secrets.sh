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
    "PUBLIC_BASE_URL": "http://localhost:3000",
    "FRONTEND_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "OPERATOR_PASSWORD_SCRYPT": f"{base64.urlsafe_b64encode(salt).decode().rstrip('=')}:{base64.urlsafe_b64encode(digest).decode().rstrip('=')}",
}
with open(".secrets/dev.env", "x", encoding="utf-8") as output:
    output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print(f"Dev credentials vytvořeny v .secrets/dev.env; jednorázové heslo: {password}")
PY
