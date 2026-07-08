from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GroopConfig:
    interval: float = 5.0
    cgroup_root: Path = Path("/sys/fs/cgroup")
    tiers: dict[str, list[str]] = field(default_factory=dict)
    protected_services: tuple[str, ...] = ()
    thresholds: dict[str, Any] = field(default_factory=dict)


def _default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "groop" / "config.toml"


def load(path: Path | None = None) -> GroopConfig:
    data: dict[str, Any] = {}
    try:
        with (path or _default_path()).open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        pass
    general = data.get("general", {})
    tiers_data = data.get("tiers", {})
    tiers = {
        str(name): [str(prefix) for prefix in prefixes]
        for name, prefixes in tiers_data.items()
        if isinstance(prefixes, list) and name != "protected_services"
    }
    return GroopConfig(
        interval=float(general.get("interval", 5.0)),
        cgroup_root=Path(general.get("cgroup_root", "/sys/fs/cgroup")),
        tiers=tiers,
        protected_services=tuple(str(v) for v in tiers_data.get("protected_services", ())),
        thresholds=data.get("thresholds", {}),
    )
