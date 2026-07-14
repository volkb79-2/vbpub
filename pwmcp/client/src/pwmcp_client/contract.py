from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PwmcpContract:
    schema_version: int
    release: str
    playwright_version: str
    protocol_version: str
    ws_url: str
    health_url: str
    default_lease_seconds: int
    max_lease_seconds: int
    max_clients: int
    idle_recycle_seconds: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PwmcpContract":
        endpoints = value["endpoints"]
        limits = value["run_server"]["limits"]
        return cls(
            schema_version=int(value["schema_version"]),
            release=str(value["release"]),
            playwright_version=str(value["playwright"]["python"]),
            protocol_version=str(value["playwright"]["protocol"]),
            ws_url=str(endpoints["playwright_ws"]),
            health_url=str(endpoints["health"]),
            default_lease_seconds=int(limits["default_lease_seconds"]),
            max_lease_seconds=int(limits["max_lease_seconds"]),
            max_clients=int(limits["max_clients"]),
            idle_recycle_seconds=int(limits["idle_recycle_seconds"]),
        )


def load_contract(source: str | Path, *, timeout: float = 10.0) -> PwmcpContract:
    raw_source = str(source)
    if raw_source.startswith(("http://", "https://")):
        request = urllib.request.Request(raw_source, headers={"User-Agent": "pwmcp-client/0.1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    else:
        with Path(source).open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("pwmcp contract must be a JSON object")
    return PwmcpContract.from_dict(payload)
