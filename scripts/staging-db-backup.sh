#!/bin/sh
set -eu
umask 077

backup_dir=${BACKUP_DIR:-/var/backups/quantlab}
retention_days=${BACKUP_RETENTION_DAYS:-14}
prefix=${BACKUP_PREFIX:-quantlab-daily}
container=${POSTGRES_CONTAINER:-quantlab-staging-postgres-1}
database=${POSTGRES_DB:-quantlab}
user=${POSTGRES_USER:-quantlab_migration}
docker_bin=${DOCKER_BIN:-docker}

case "$retention_days" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS musí být celé číslo" >&2; exit 2 ;;
esac
[ "$retention_days" -ge 1 ] || { echo "Retence musí být alespoň 1 den" >&2; exit 2; }

mkdir -p "$backup_dir"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$backup_dir/$prefix-$timestamp.dump"
tmp="$target.tmp"

cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT HUP INT TERM

"$docker_bin" exec "$container" pg_dump \
  -U "$user" -d "$database" \
  --format=custom --no-owner --no-acl > "$tmp"

test -s "$tmp"
mv "$tmp" "$target"
sha256sum "$target" > "$target.sha256"

find "$backup_dir" -type f -name "$prefix-*.dump" -mtime +"$retention_days" \
  -exec sh -c 'for old do rm -f "$old" "$old.sha256"; done' sh {} +

printf '%s\n' "$target"
