#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$ROOT/agent-stack/bin"
UNITS="$ROOT/agent-stack/systemd"

required_bin=(
  agent-codex-usage-export
  agent-github-intake
  agent-stack-ensure
  agent-stack-watchdog
  agent-task-dispatcher
  agent-task-export
  agent-task-worker
  hermes-maintenance
  hermes-offsite-prepare
  hermes-offsite-sync
  majak
  quantlab
)
required_units=(
  agent-stack-watchdog.service
  hermes-maintenance.service
  hermes-maintenance.timer
)

for name in "${required_bin[@]}"; do
  [[ -f "$BIN/$name" ]] || { echo "missing runtime source: $name" >&2; exit 1; }
done
for name in "${required_units[@]}"; do
  [[ -f "$UNITS/$name" ]] || { echo "missing runtime unit: $name" >&2; exit 1; }
done

grep -Fq '/home/agentops/.local/bin/hermes-maintenance' "$BIN/agent-stack-watchdog"
grep -Fq '.local/bin/agent-codex-usage-export' "$BIN/hermes-maintenance"
grep -Fq '.local/bin/hermes-offsite-prepare' "$BIN/hermes-maintenance"
grep -Fq '.local/bin/hermes-offsite-sync' "$BIN/hermes-maintenance"
grep -Fq 'ExecStart=/home/agentops/.local/bin/hermes-maintenance' "$UNITS/hermes-maintenance.service"
grep -Fq 'Unit=hermes-maintenance.service' "$UNITS/hermes-maintenance.timer"

# hermes, herdr, codex and rclone are external installed executables, not custom source.
# hermes-offsite-connect-drive is an operator-only bootstrap helper and is not invoked
# by the active watchdog/timer runtime path.
echo "Agent Platform custom runtime source closure OK."