#!/bin/sh
set -eu

from=${1:?Použití: schema-migration-needed.sh <from-sha> <to-sha>}
to=${2:?Použití: schema-migration-needed.sh <from-sha> <to-sha>}

git rev-parse --verify "$from^{commit}" >/dev/null
git rev-parse --verify "$to^{commit}" >/dev/null

if git diff --quiet "$from" "$to" -- \
  alembic/versions \
  alembic.ini \
  scripts/configure-runtime-role.sql
then
  printf 'no\n'
else
  printf 'yes\n'
fi
