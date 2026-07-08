from __future__ import annotations

import hashlib
import json
import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HistoryConfig:
    full_resolution_seconds: int = 14_400
    downsample_interval_seconds: int = 60
    downsample_retention_hours: int = 4
    entity_grace_seconds: float = 30.0

    def capacity_for_interval(self, interval: float) -> int:
        if interval <= 0:
            return 1
        return max(1, math.ceil(self.full_resolution_seconds / interval))

    def entity_grace_frames(self, interval: float) -> int:
        if interval <= 0:
            return 0
        return max(0, math.ceil(self.entity_grace_seconds / interval))


@dataclass(frozen=True)
class RecordConfig:
    flush_every_frames: int = 1
    fsync: bool = False


@dataclass(frozen=True)
class NetConfig:
    classes: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def classify_port(self, port: int) -> str | None:
        for name, ports in self.classes.items():
            if port in ports:
                return name
        return None


@dataclass(frozen=True)
class GroopConfig:
    interval: float = 5.0
    cgroup_root: Path = Path("/sys/fs/cgroup")
    tiers: dict[str, list[str]] = field(default_factory=dict)
    protected_services: tuple[str, ...] = ()
    thresholds: dict[str, Any] = field(default_factory=dict)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    net: NetConfig = field(default_factory=NetConfig)

    def to_primitive(self) -> dict[str, Any]:
        return {
            "general": {
                "interval": self.interval,
                "cgroup_root": str(self.cgroup_root),
            },
            "tiers": {**dict(self.tiers), "protected_services": list(self.protected_services)},
            "thresholds": self.thresholds,
            "history": {
                "full_resolution_seconds": self.history.full_resolution_seconds,
                "downsample_interval_seconds": self.history.downsample_interval_seconds,
                "downsample_retention_hours": self.history.downsample_retention_hours,
                "entity_grace_seconds": self.history.entity_grace_seconds,
            },
            "record": {
                "flush_every_frames": self.record.flush_every_frames,
                "fsync": self.record.fsync,
            },
            "net": {
                "classes": {name: list(ports) for name, ports in self.net.classes.items()},
            },
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_primitive(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "groop" / "config.toml"


def _parse_port_list(values: object) -> tuple[int, ...]:
    if not isinstance(values, list):
        return ()
    ports: set[int] = set()
    for value in values:
        if isinstance(value, int):
            if 0 < value < 65536:
                ports.add(value)
            continue
        if isinstance(value, str):
            text = value.strip()
            if "-" in text:
                start_text, _, end_text = text.partition("-")
                try:
                    start = int(start_text)
                    end = int(end_text)
                except ValueError:
                    continue
                if start > end:
                    start, end = end, start
                for port in range(max(1, start), min(65535, end) + 1):
                    ports.add(port)
                continue
            try:
                port = int(text)
            except ValueError:
                continue
            if 0 < port < 65536:
                ports.add(port)
    return tuple(sorted(ports))


def load(path: Path | None = None) -> GroopConfig:
    data: dict[str, Any] = {}
    try:
        with (path or _default_path()).open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        pass
    general = data.get("general", {})
    tiers_data = data.get("tiers", {})
    history_data = data.get("history", {})
    record_data = data.get("record", {})
    net_data = data.get("net", {})
    tiers = {
        str(name): [str(prefix) for prefix in prefixes]
        for name, prefixes in tiers_data.items()
        if isinstance(prefixes, list) and name != "protected_services"
    }
    net_classes = {
        str(name): _parse_port_list(values)
        for name, values in (net_data.get("classes", {}) or {}).items()
    }
    return GroopConfig(
        interval=float(general.get("interval", 5.0)),
        cgroup_root=Path(general.get("cgroup_root", "/sys/fs/cgroup")),
        tiers=tiers,
        protected_services=tuple(str(v) for v in tiers_data.get("protected_services", ())),
        thresholds=data.get("thresholds", {}),
        history=HistoryConfig(
            full_resolution_seconds=int(history_data.get("full_resolution_seconds", 14_400)),
            downsample_interval_seconds=int(history_data.get("downsample_interval_seconds", 60)),
            downsample_retention_hours=int(history_data.get("downsample_retention_hours", 4)),
            entity_grace_seconds=float(history_data.get("entity_grace_seconds", 30.0)),
        ),
        record=RecordConfig(
            flush_every_frames=max(1, int(record_data.get("flush_every_frames", 1))),
            fsync=bool(record_data.get("fsync", False)),
        ),
        net=NetConfig(classes=net_classes),
    )
