#!/bin/sh
set -eu

python3 - <<'PY'
import socket

active_ports = []
for port in (3000, 8000):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            active_ports.append(str(port))

if active_ports:
    raise SystemExit(
        "Před resetem zastavte dashboard i API; aktivní porty: "
        + ", ".join(active_ports)
    )
PY

rm -f .secrets/dev.env
exec ./scripts/generate-dev-secrets.sh
