#!/bin/sh
set -eu
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL je povinný}"
: "${RESTORE_CONFIRMATION:?Nastav RESTORE_CONFIRMATION=RESTORE_EPHEMERAL_DATABASE}"
test "$RESTORE_CONFIRMATION" = RESTORE_EPHEMERAL_DATABASE || { echo "Destruktivní restore odmítnut" >&2; exit 2; }
source=${1:?Použití: db-restore.sh <backup.dump>}
test -f "$source" && test -f "$source.sha256" || { echo "Backup nebo checksum chybí" >&2; exit 2; }
expected_checksum=$(awk 'NR == 1 { print $1 } NR > 1 { exit 2 }' "$source.sha256") || {
  echo "Checksum manifest má neplatný formát" >&2
  exit 2
}
case "$expected_checksum" in
  *[!0-9a-fA-F]*|'') echo "Checksum manifest má neplatný formát" >&2; exit 2 ;;
esac
test "${#expected_checksum}" -eq 64 || { echo "Checksum manifest má neplatný formát" >&2; exit 2; }
actual_checksum=$(sha256sum "$source" | awk '{print $1}')
test "$actual_checksum" = "$expected_checksum" || { echo "Checksum backupu nesouhlasí" >&2; exit 1; }
case "$RESTORE_DATABASE_URL" in
  postgresql+psycopg://*) restore_database_url="postgresql://${RESTORE_DATABASE_URL#postgresql+psycopg://}" ;;
  *) restore_database_url=$RESTORE_DATABASE_URL ;;
esac
pg_restore --exit-on-error --no-owner --no-acl --clean --if-exists --dbname="$restore_database_url" "$source"
