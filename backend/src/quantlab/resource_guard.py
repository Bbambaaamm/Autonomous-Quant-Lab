from __future__ import annotations

from pathlib import Path

MIB = 1024 * 1024


class ResourcePressure(RuntimeError):
    pass


def _read_status_kib(path: Path, key: str) -> int | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key):
                fields = line.split()
                return int(fields[1]) if len(fields) >= 2 else None
    except (OSError, ValueError):
        return None
    return None


def host_available_mib(path: Path = Path("/proc/meminfo")) -> int | None:
    value = _read_status_kib(path, "MemAvailable:")
    return value // 1024 if value is not None else None


def current_rss_mib(path: Path = Path("/proc/self/status")) -> int | None:
    value = _read_status_kib(path, "VmRSS:")
    return value // 1024 if value is not None else None


def cgroup_headroom_mib(root: Path = Path("/sys/fs/cgroup")) -> int | None:
    try:
        raw_max = (root / "memory.max").read_text(encoding="utf-8").strip()
        if raw_max == "max":
            return None
        maximum = int(raw_max)
        current = int((root / "memory.current").read_text(encoding="utf-8").strip())
        return max(0, maximum - current) // MIB
    except (OSError, ValueError):
        return None


def require_capacity(
    *,
    host_min_mib: int,
    cgroup_min_mib: int,
    purpose: str,
) -> dict[str, int | None]:
    host = host_available_mib()
    cgroup = cgroup_headroom_mib()
    if host is not None and host < host_min_mib:
        raise ResourcePressure(
            f"RESOURCE_PRESSURE_{purpose}: host_available_mib={host} < {host_min_mib}"
        )
    if cgroup is not None and cgroup < cgroup_min_mib:
        raise ResourcePressure(
            f"RESOURCE_PRESSURE_{purpose}: cgroup_headroom_mib={cgroup} < {cgroup_min_mib}"
        )
    return {"host_available_mib": host, "cgroup_headroom_mib": cgroup}
