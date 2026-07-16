from __future__ import annotations

from pathlib import Path
from typing import Any

from topos.collect.cgroup import read_text


def _status_values(path: Path) -> dict[str, int]:
    result = read_text(path)
    if result.value is None:
        return {}
    out: dict[str, int] = {}
    for line in str(result.value).splitlines():
        key, _, rest = line.partition(":")
        if key in {"VmRSS", "VmSwap"}:
            parts = rest.split()
            if parts:
                try:
                    out[key] = int(parts[0]) * 1024
                except ValueError:
                    pass
    return out


def list_processes(cgroup_root: Path, entity_key: str, proc_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    cgroup_path = cgroup_root if entity_key == "" else cgroup_root / entity_key
    procs = read_text(cgroup_path / "cgroup.procs")
    if procs.value is None:
        return []
    out: list[dict[str, Any]] = []
    for line in str(procs.value).splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        proc = proc_root / str(pid)
        comm = read_text(proc / "comm")
        cmdline = read_text(proc / "cmdline")
        status = _status_values(proc / "status")
        cmd = None
        if cmdline.value is not None:
            cmd = " ".join(part for part in str(cmdline.value).split("\0") if part)
        out.append({"pid": pid, "comm": str(comm.value) if comm.value is not None else None, "cmdline": cmd, "rss": status.get("VmRSS"), "swap": status.get("VmSwap")})
    return out
