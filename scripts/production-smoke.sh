#!/bin/sh
set -eu

network="quantlab-phase9-smoke-$$"
ingress_network="quantlab-phase9-smoke-ingress-$$"
postgres="quantlab-phase9-postgres-$$"
backend="quantlab-phase9-backend-$$"
frontend="quantlab-phase9-frontend-$$"

cleanup() {
  docker rm -f "$frontend" "$backend" "$postgres" >/dev/null 2>&1 || true
  docker network rm "$ingress_network" "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

eval "$(python3 - <<'PY'
import base64
import hashlib
import os
import secrets

password = secrets.token_urlsafe(24)
salt = os.urandom(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
print(f'DB_PASSWORD={secrets.token_urlsafe(24)}')
print(f'VIEWER_TOKEN={secrets.token_urlsafe(32)}')
print(f'OPERATOR_TOKEN={secrets.token_urlsafe(32)}')
print(f'ADMIN_TOKEN={secrets.token_urlsafe(32)}')
print(f'SESSION_SECRET={secrets.token_urlsafe(32)}')
print(f'PASSWORD_HASH={base64.urlsafe_b64encode(salt).decode().rstrip("=")}:{base64.urlsafe_b64encode(digest).decode().rstrip("=")}')
PY
)"
export DB_PASSWORD VIEWER_TOKEN OPERATOR_TOKEN ADMIN_TOKEN SESSION_SECRET PASSWORD_HASH

docker network create --internal "$network" >/dev/null
docker network create "$ingress_network" >/dev/null
docker run -d --name "$postgres" --network "$network" \
  -e POSTGRES_DB=quantlab -e POSTGRES_USER=quantlab -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  postgres:17-alpine >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$postgres" pg_isready -U quantlab -d quantlab >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$postgres" pg_isready -U quantlab -d quantlab >/dev/null

migration_url="postgresql+psycopg://quantlab:${DB_PASSWORD}@${postgres}:5432/quantlab"
docker run --rm --network "$network" -e DATABASE_URL="$migration_url" \
  --entrypoint alembic quantlab-backend -c /app/alembic.ini upgrade head

docker exec -i -e PGPASSWORD="$DB_PASSWORD" "$postgres" psql -U quantlab -d quantlab -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE quantlab_runtime LOGIN PASSWORD 'phase9-runtime-password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO quantlab_runtime;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO quantlab_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO quantlab_runtime;
SQL

runtime_url="postgresql+psycopg://quantlab_runtime:phase9-runtime-password@${postgres}:5432/quantlab"
docker run -d --name "$backend" --network "$network" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e APP_ENV=production -e DATABASE_URL="$runtime_url" \
  -e TRUSTED_HOSTS="$backend,127.0.0.1,localhost" \
  -e API_VIEWER_TOKEN="$VIEWER_TOKEN" -e API_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
  -e API_ADMIN_TOKEN="$ADMIN_TOKEN" quantlab-backend >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$backend" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')" \
    >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$backend" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')" >/dev/null

docker run -d --name "$frontend" --network "$network" -p 127.0.0.1:3000:3000 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  -e QUANTLAB_API_URL="http://${backend}:8000" \
  -e QUANTLAB_API_VIEWER_TOKEN="$VIEWER_TOKEN" \
  -e QUANTLAB_API_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
  -e QUANTLAB_API_ADMIN_TOKEN="$ADMIN_TOKEN" \
  -e SESSION_SECRET="$SESSION_SECRET" -e OPERATOR_USERNAME=operator \
  -e OPERATOR_PASSWORD_SCRYPT="$PASSWORD_HASH" -e OPERATOR_ROLE=ADMIN \
  -e SESSION_MAX_AGE_SECONDS=3600 -e PUBLIC_BASE_URL=https://localhost:3000 \
  -e FRONTEND_ALLOWED_HOSTS=localhost,127.0.0.1 quantlab-frontend >/dev/null
docker network connect "$ingress_network" "$frontend"

for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:3000/login >/dev/null 2>&1; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:3000/login >/dev/null

docker exec -i "$backend" python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000"

def request(path, token=None, body=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers)) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code

viewer = os.environ["API_VIEWER_TOKEN"]
operator = os.environ["API_OPERATOR_TOKEN"]
admin = os.environ["API_ADMIN_TOKEN"]
assert request("/operator/overview") == 401
assert request("/operator/overview", viewer) == 200
assert request("/operator/risk/halt", viewer, {"confirmation": "HALT", "reason": "smoke"}) == 403
assert request("/operator/risk/halt", operator, {"confirmation": "HALT", "reason": "smoke"}) == 200
assert request("/operator/risk/resume", operator, {"confirmation": "RESUME", "reason": "smoke"}) == 403
assert request("/reconciliation/run", admin, {}) == 200
assert request("/operator/risk/resume", admin, {"confirmation": "RESUME", "reason": "smoke"}) == 200
PY

test "$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$postgres")" = "{}"
test "$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$backend")" = "{}"
test "$(docker exec "$backend" id -u)" != 0
test "$(docker exec "$frontend" id -u)" != 0
! git grep -n -E 'TRADING_MODE=live|LIVE_TRADING_ENABLED=true' -- ':!docs/codex/*' ':!CODEX_MASTER_PROMPT.md' ':!scripts/production-smoke.sh'
