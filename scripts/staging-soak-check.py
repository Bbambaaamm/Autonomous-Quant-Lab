#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

MIB = 1024
RSS_LIMIT_KIB = {
    "backend": 500 * MIB,
    "worker": 500 * MIB,
    "listener": 300 * MIB,
    "frontend": 300 * MIB,
}


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def slope_kib_per_hour(rows: list[dict[str, object]], name: str) -> float:
    points: list[tuple[float, float]] = []
    origin = ts(str(rows[0]["timestamp"]))
    for row in rows:
        proc = (row.get("processes") or {}).get(name)  # type: ignore[union-attr]
        if isinstance(proc, dict) and isinstance(proc.get("rss_kib"), int):
            hours = (ts(str(row["timestamp"])) - origin).total_seconds() / 3600
            points.append((hours, float(proc["rss_kib"])))
    if len(points) < 2:
        return 0.0
    xbar = statistics.fmean(x for x, _ in points)
    ybar = statistics.fmean(y for _, y in points)
    denom = sum((x - xbar) ** 2 for x, _ in points)
    return 0.0 if denom == 0 else sum((x - xbar) * (y - ybar) for x, y in points) / denom


def state_count(row: dict[str, object], section: str, names: tuple[str, ...]) -> int:
    values = row.get(section)
    if not isinstance(values, dict):
        return 0
    return sum(int(values.get(name, 0) or 0) for name in names)


def evaluate(rows: list[dict[str, object]], minimum_hours: float) -> dict[str, object]:
    if len(rows) < 2:
        return {"ok": False, "reasons": ["INSUFFICIENT_SAMPLES"]}
    start, end = ts(str(rows[0]["timestamp"])), ts(str(rows[-1]["timestamp"]))
    span = (end - start).total_seconds() / 3600
    reasons: list[str] = []
    if span < minimum_hours:
        reasons.append(f"SOAK_TOO_SHORT:{span:.2f}h<{minimum_hours:.2f}h")

    expected = max(2, int(minimum_hours * 12 * 0.8))
    if len(rows) < expected:
        reasons.append(f"TOO_FEW_SAMPLES:{len(rows)}<{expected}")

    deployed = {str(row.get("deployed_sha")) for row in rows}
    if len(deployed) != 1:
        reasons.append("DEPLOY_SHA_CHANGED")

    ready_ok = sum(
        1 for row in rows
        if isinstance(row.get("readyz"), dict) and row["readyz"].get("status") == 200  # type: ignore[index]
    )
    if ready_ok / len(rows) < 0.99:
        reasons.append(f"READYZ_AVAILABILITY:{ready_ok}/{len(rows)}")

    if min(int(row.get("host_available_kib") or 0) for row in rows) < 800 * MIB:
        reasons.append("HOST_MEMORY_FLOOR")

    for name, limit in RSS_LIMIT_KIB.items():
        values = [
            int(proc["rss_kib"])
            for row in rows
            if isinstance((proc := (row.get("processes") or {}).get(name)), dict)  # type: ignore[union-attr]
            and isinstance(proc.get("rss_kib"), int)
        ]
        if len(values) < len(rows) * 0.95:
            reasons.append(f"PROCESS_MISSING:{name}")
            continue
        if max(values) >= limit:
            reasons.append(f"RSS_LIMIT:{name}:{max(values)}")
        slope = slope_kib_per_hour(rows, name)
        if slope > 5 * MIB and values[-1] > values[0] + 50 * MIB:
            reasons.append(f"RSS_GROWTH:{name}:{slope:.1f}KiB/h")

    for row in rows:
        containers = row.get("containers")
        if not isinstance(containers, dict):
            reasons.append("CONTAINER_STATE_MISSING")
            break
        if any(
            isinstance(value, dict) and (value.get("oom_killed") or not value.get("running"))
            for value in containers.values()
        ):
            reasons.append("CONTAINER_OOM_OR_DOWN")
            break

    for section, states in (
        ("market_tasks", ("FAILED", "DEAD_LETTER")),
        ("job_runs", ("FAILED", "DEAD_LETTER")),
    ):
        if state_count(rows[-1], section, states) > state_count(rows[0], section, states):
            reasons.append(f"FAILURE_COUNT_INCREASED:{section}")

    latencies = [
        float(row["readyz"]["latency_ms"])  # type: ignore[index]
        for row in rows
        if isinstance(row.get("readyz"), dict)
        and isinstance(row["readyz"].get("latency_ms"), (int, float))  # type: ignore[index]
    ]
    result = {
        "ok": not reasons,
        "minimum_hours": minimum_hours,
        "span_hours": round(span, 3),
        "samples": len(rows),
        "deployed_sha": next(iter(deployed)) if len(deployed) == 1 else None,
        "min_host_available_kib": min(int(row.get("host_available_kib") or 0) for row in rows),
        "readyz_p95_ms": (
            round(sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3)
            if latencies else None
        ),
        "rss_slope_kib_per_hour": {
            name: round(slope_kib_per_hour(rows, name), 3) for name in RSS_LIMIT_KIB
        },
        "db_growth_bytes": int(rows[-1].get("db_size_bytes") or 0) - int(rows[0].get("db_size_bytes") or 0),
        "reasons": reasons,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--minimum-hours", type=float, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.file).read_text().splitlines() if line.strip()]
    result = evaluate(rows, args.minimum_hours)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
