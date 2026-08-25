#!/bin/sh
set -eu
umask 077
mkdir -p .secrets
python3 - <<'PY'
import base64, hashlib, os, secrets
password = secrets.token_urlsafe(24)
salt = os.urandom(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
values = {
    "SESSION_SECRET": secrets.token_urlsafe(32),
    "API_VIEWER_TOKEN": secrets.token_urlsafe(32),
    "API_OPERATOR_TOKEN": secrets.token_urlsafe(32),
    "API_ADMIN_TOKEN": secrets.token_urlsafe(32),
    "OPERATOR_PASSWORD_SCRYPT": f"{base64.urlsafe_b64encode(salt).decode().rstrip('=')}:{base64.urlsafe_b64encode(digest).decode().rstrip('=')}",
}
with open(".secrets/dev.env", "x", encoding="utf-8") as output:
    output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print(f"Dev credentials vytvořeny v .secrets/dev.env; jednorázové heslo: {password}")
PY
