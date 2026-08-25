#!/bin/sh
set -eu
umask 077
: "${DATABASE_URL:?DATABASE_URL je povinný}"
target=${1:?Použití: db-backup.sh <nový-backup.dump>}
test ! -e "$target" || { echo "Cíl již existuje" >&2; exit 2; }
mkdir -p "$(dirname "$target")"
pg_dump --format=custom --no-owner --no-acl --file="$target" "$DATABASE_URL"
sha256sum "$target" > "$target.sha256"
