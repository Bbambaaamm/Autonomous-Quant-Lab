#!/bin/sh
set -eu

timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host_available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
load=$(cut -d' ' -f1-3 /proc/loadavg)

printf '{"timestamp":"%s","host_available_kib":%s,"load":"%s","processes":['   "$timestamp" "$host_available_kib" "$load"

first=1
for spec in   "backend:uvicorn quantlab.api"   "worker:quantlab-worker"   "listener:quantlab-alpaca-events"   "frontend:next-server"
do
  name=${spec%%:*}
  pattern=${spec#*:}
  pid=$(pgrep -f "$pattern" | head -1 || true)
  [ -n "$pid" ] || continue
  rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status")
  cpu=$(ps -o time= -p "$pid" | tr -d ' ')
  [ "$first" -eq 1 ] || printf ','
  first=0
  printf '{"name":"%s","pid":%s,"rss_kib":%s,"cpu_time":"%s"}' "$name" "$pid" "$rss" "$cpu"
done
printf ']}\n'
