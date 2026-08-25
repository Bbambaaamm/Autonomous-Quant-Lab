#!/bin/sh
set -eu
umask 077
: "${DATABASE_URL:?DATABASE_URL je povinný}"
target=${1:?Použití: db-backup.sh <nový-backup.dump>}
test ! -e "$target" || { echo "Cíl již existuje" >&2; exit 2; }
mkdir -p "$(dirname "$target")"
case "$DATABASE_URL" in
  postgresql+psycopg://*) database_url="postgresql://${DATABASE_URL#postgresql+psycopg://}" ;;
  *) database_url=$DATABASE_URL ;;
esac
pg_dump --format=custom --no-owner --no-acl --file="$target" "$database_url"
sha256sum "$target" > "$target.sha256"
