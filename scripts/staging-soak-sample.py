#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SERVICES = {
    "backend": ("quantlab-staging-backend-1", "uvicorn quantlab.api"),
    "worker": ("quantlab-staging-worker-1", "quantlab-worker"),
    "listener": ("quantlab-staging-alpaca-events-1", "quantlab-alpaca-events"),
    "frontend": ("quantlab-staging-frontend-1", "next-server"),
}
POSTGRES = "quantlab-staging-postgres-1"


def run(args: list[str], timeout: float = 8.0) -> str:
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def process_metrics(pattern: str) -> dict[str, object] | None:
    try:
        pid = int(run(["pgrep", "-f", pattern]).splitlines()[0])
        status = Path(f"/proc/{pid}/status").read_text()
        rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
        hwm = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmHWM:"))
        cpu = run(["ps", "-o", "time=", "-p", str(pid)]).strip()
        return {"pid": pid, "rss_kib": rss, "hwm_kib": hwm, "cpu_time": cpu}
    except (OSError, ValueError, subprocess.SubprocessError, StopIteration):
        return None


def container_state(name: str) -> dict[str, object]:
    try:
        raw = run([
            "docker", "inspect", "--format",
            "{{json .State}}", name,
        ])
        state = json.loads(raw)
        return {
            "status": state.get("Status"),
            "running": bool(state.get("Running")),
            "oom_killed": bool(state.get("OOMKilled")),
            "restart_count": int(run(["docker", "inspect", "--format", "{{.RestartCount}}", name])),
        }
    except (ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return {"status": "unknown", "running": False, "oom_killed": False, "restart_count": -1}


def readyz() -> dict[str, object]:
    code = (
        "import time,urllib.request;"
        "t=time.perf_counter();"
        "r=urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=2);"
        "print(r.status,round((time.perf_counter()-t)*1000,3))"
    )
    try:
        status, latency = run([
            "docker", "exec", SERVICES["backend"][0],
            "/app/backend/.venv/bin/python", "-c", code,
        ], timeout=5).split()
        return {"status": int(status), "latency_ms": float(latency)}
    except (ValueError, subprocess.SubprocessError):
        return {"status": 0, "latency_ms": None}


def db_json(sql: str) -> object:
    try:
        raw = run([
            "docker", "exec", POSTGRES, "psql", "-U", "quantlab_migration", "-d", "quantlab",
            "-At", "-c", sql,
        ], timeout=10)
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, subprocess.SubprocessError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--deployed-file", default="/home/quantadmin/quantlab-config/deployed-sha")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    deployed = Path(args.deployed_file).read_text().strip()
    mem_available = int(
        next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
             if line.startswith("MemAvailable:"))
    )
    load = [float(item) for item in Path("/proc/loadavg").read_text().split()[:3]]

    processes: dict[str, object] = {}
    containers: dict[str, object] = {}
    for key, (container, pattern) in SERVICES.items():
        processes[key] = process_metrics(pattern)
        containers[key] = container_state(container)
    containers["postgres"] = container_state(POSTGRES)

    sample = {
        "timestamp": datetime.now(UTC).isoformat(),
        "deployed_sha": deployed,
        "host_available_kib": mem_available,
        "load": load,
        "processes": processes,
        "containers": containers,
        "readyz": readyz(),
        "db_size_bytes": db_json("select to_json(pg_database_size('quantlab'))"),
        "market_tasks": db_json(
            "select coalesce(json_object_agg(state,n),'{}'::json) "
            "from (select state,count(*) n from market_tasks group by state) s"
        ),
        "job_runs": db_json(
            "select coalesce(json_object_agg(status,n),'{}'::json) "
            "from (select status,count(*) n from job_runs group by status) s"
        ),
        "corporate_action_events": db_json(
            "select to_json(count(*)) from corporate_action_events"
        ),
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
