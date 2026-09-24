#!/bin/bash
set -Eeuo pipefail
umask 077

REPO="/home/quantadmin/apps/quantlab-staging"
CONFIG="/home/quantadmin/quantlab-config"
BACKUPS="/home/quantadmin/quantlab-backups"
DEPLOYED_FILE="$CONFIG/deployed-sha"
OVERRIDE="$CONFIG/docker-compose.staging.yml"
CHECK_CI="$CONFIG/check-ci.py"
GHCR_OWNER="${GHCR_OWNER:-bbambaaamm}"

if [ -f "$CONFIG/deploy.hold" ]; then
    echo "DEPLOY: hold aktivni, preskakuji"
    exit 0
fi

exec 9>"$CONFIG/deploy.lock"
if ! flock -n 9; then
    echo "DEPLOY: jiny deployment uz probiha"
    exit 10
fi

if [ "$EUID" -eq 0 ]; then
    DOCKER=(docker)
    COMPOSE=(docker compose)
    gitq() { runuser -u quantadmin -- git -C "$REPO" "$@"; }
else
    DOCKER=(sudo -n docker)
    COMPOSE=(sudo -n docker compose)
    gitq() { git -C "$REPO" "$@"; }
fi

compose() {
    BACKEND_IMAGE="$BACKEND_IMAGE" FRONTEND_IMAGE="$FRONTEND_IMAGE" QUANTLAB_CODE_SHA="$TARGET_SHA"     "${COMPOSE[@]}"         --env-file "$REPO/.env.production"         -f "$REPO/docker-compose.production.yml"         -f "$OVERRIDE"         "$@"
}

cd "$REPO"
test -f "$DEPLOYED_FILE" || { echo "DEPLOY: chybi deployed-sha"; exit 11; }
DEPLOYED="$(cat "$DEPLOYED_FILE")"

install_backup_timer() {
    [ "$EUID" -eq 0 ] || return 0
    install -m 0644 "$REPO/ops/systemd/quantlab-db-backup.service"         /etc/systemd/system/quantlab-db-backup.service
    install -m 0644 "$REPO/ops/systemd/quantlab-db-backup.timer"         /etc/systemd/system/quantlab-db-backup.timer
    systemctl daemon-reload
    systemctl enable --now quantlab-db-backup.timer >/dev/null
}

gitq fetch --quiet origin main
REMOTE="$(gitq rev-parse origin/main)"
LOCAL="$(gitq rev-parse HEAD)"

echo "Nasazeno: ${DEPLOYED:0:8}"
echo "GitHub:   ${REMOTE:0:8}"

if [ "$REMOTE" = "$DEPLOYED" ]; then
    echo "DEPLOY: zadna nova verze"
    exit 0
fi
[ "$LOCAL" = "$DEPLOYED" ] || { echo "DEPLOY: lokalni HEAD neodpovida deployed-sha"; exit 12; }
[ -z "$(gitq status --porcelain --untracked-files=no)" ] || {
    echo "DEPLOY: repository obsahuje lokalni zmeny"; exit 13;
}
gitq merge-base --is-ancestor "$DEPLOYED" "$REMOTE" || {
    echo "DEPLOY: GitHub main neni potomkem nasazene verze"; exit 14;
}

set +e
"$CHECK_CI" "$REMOTE"
RC=$?
set -e
case "$RC" in
    0) ;;
    2) echo "DEPLOY: CI zatim neni hotove"; exit 0 ;;
    1) echo "DEPLOY: CI selhalo"; exit 1 ;;
    *) echo "DEPLOY: kontrola CI selhala ($RC)"; exit "$RC" ;;
esac

SCHEMA_CHANGE=no
if ! gitq diff --quiet "$DEPLOYED" "$REMOTE" --     alembic/versions alembic.ini scripts/configure-runtime-role.sql
then
    SCHEMA_CHANGE=yes
fi

TARGET_SHA="$REMOTE"
BACKEND_IMAGE="ghcr.io/$GHCR_OWNER/autonomous-quant-lab-backend:$TARGET_SHA"
FRONTEND_IMAGE="ghcr.io/$GHCR_OWNER/autonomous-quant-lab-frontend:$TARGET_SHA"

echo "DEPLOY: cekam na immutable GHCR image..."
FOUND=0
for attempt in $(seq 1 30); do
    if "${DOCKER[@]}" manifest inspect "$BACKEND_IMAGE" >/dev/null 2>&1         && "${DOCKER[@]}" manifest inspect "$FRONTEND_IMAGE" >/dev/null 2>&1
    then
        FOUND=1
        break
    fi
    sleep 10
done
[ "$FOUND" -eq 1 ] || { echo "DEPLOY: GHCR image pro commit neni dostupny"; exit 15; }

gitq reset --hard "$REMOTE"

rollback_code() {
    RC=$?
    trap - ERR
    echo "DEPLOY: SELHANI, deployed-sha zustava ${DEPLOYED:0:8}"
    gitq reset --hard "$DEPLOYED" >/dev/null 2>&1 || true
    exit "$RC"
}
trap rollback_code ERR

echo "DEPLOY: stahuji overene image..."
compose pull backend worker alpaca-events frontend

if [ "$SCHEMA_CHANGE" = yes ]; then
    mkdir -p "$BACKUPS"
    TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    BACKUP="$BACKUPS/quantlab-pre-migration-$TIMESTAMP.dump"
    TMP="$BACKUP.tmp"
    echo "DEPLOY: schema change -> pre-migration backup..."
    "${DOCKER[@]}" exec quantlab-staging-postgres-1 pg_dump         -U quantlab_migration -d quantlab         --format=custom --no-owner --no-acl > "$TMP"
    test -s "$TMP"
    mv "$TMP" "$BACKUP"
    sha256sum "$BACKUP" > "$BACKUP.sha256"
    if [ "$EUID" -eq 0 ]; then
        chown quantadmin:quantadmin "$BACKUP" "$BACKUP.sha256"
    fi
else
    echo "DEPLOY: bez schema change -> full DB dump se nevytvari"
fi

DB_PASSWORD="$(cat "$REPO/.secrets/db_password")"
MIGRATION_DATABASE_URL="postgresql+psycopg://quantlab_migration:${DB_PASSWORD}@postgres:5432/quantlab"
compose run --rm --no-deps     -e DATABASE_URL="$MIGRATION_DATABASE_URL"     backend     /app/backend/.venv/bin/alembic     -c /app/alembic.ini upgrade head
unset DB_PASSWORD MIGRATION_DATABASE_URL

"${DOCKER[@]}" exec -i quantlab-staging-postgres-1     psql     -U quantlab_migration     -d quantlab     -v ON_ERROR_STOP=1     -v runtime_role=quantlab_runtime     < "$REPO/scripts/configure-runtime-role.sql"

compose up -d --wait --wait-timeout 120

OK=0
for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error         --output /dev/null         https://2.28.67.165/login
    then
        OK=1
        break
    fi
    sleep 2
done
[ "$OK" -eq 1 ] || { echo "DEPLOY: HTTPS health check selhal"; false; }

printf '%s\n' "$REMOTE" > "$DEPLOYED_FILE"
chmod 600 "$DEPLOYED_FILE"
install_backup_timer
trap - ERR
echo "DEPLOY: HOTOVO"
echo "Nasazena verze: ${REMOTE:0:8}"
