#!/bin/bash
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$REPO/.env.production}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO/docker-compose.production.yml")
if [ -n "${COMPOSE_OVERRIDE:-}" ]; then
  COMPOSE+=(-f "$COMPOSE_OVERRIDE")
fi

for service in backend worker; do
  echo "Checking market-data egress from $service..."
  "${COMPOSE[@]}" exec -T "$service" /app/backend/.venv/bin/python - <<'PY'
from datetime import date, timedelta
from quantlab.market_data import StooqProvider

end = date.today()
start = end - timedelta(days=14)
bars = StooqProvider(timeout=10, max_attempts=2).historical_daily("SPY", start, end)
if not bars:
    raise SystemExit("Stooq egress returned no daily bars")
print(f"Stooq egress OK: {len(bars)} bars, last={bars[-1].session_date}")
PY
done
