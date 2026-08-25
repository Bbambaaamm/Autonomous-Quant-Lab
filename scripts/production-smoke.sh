#!/bin/sh
set -eu
compose="docker compose -f docker-compose.production.yml"
trap '$compose down' EXIT
$compose up -d --build
test "$($compose exec -T backend id -u)" != 0
test "$($compose exec -T frontend id -u)" != 0
curl --fail --silent http://127.0.0.1:3000/login >/dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:3000/)" = 307
! $compose config | grep -q '5432:5432'
