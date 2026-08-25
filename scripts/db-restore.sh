#!/bin/sh
set -eu
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL je povinný}"
: "${RESTORE_CONFIRMATION:?Nastav RESTORE_CONFIRMATION=RESTORE_EPHEMERAL_DATABASE}"
test "$RESTORE_CONFIRMATION" = RESTORE_EPHEMERAL_DATABASE || { echo "Destruktivní restore odmítnut" >&2; exit 2; }
source=${1:?Použití: db-restore.sh <backup.dump>}
test -f "$source" && test -f "$source.sha256" || { echo "Backup nebo checksum chybí" >&2; exit 2; }
sha256sum --check "$source.sha256"
pg_restore --exit-on-error --no-owner --no-acl --clean --if-exists --dbname="$RESTORE_DATABASE_URL" "$source"
