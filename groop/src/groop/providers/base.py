from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from groop.model import Entity, EntityKey


@dataclass
class NetSample:
    rx_bytes: int | None
    tx_bytes: int | None
    rx_pkts: int | None
    tx_pkts: int | None
    proto: dict | None
    source_label: str
    confidence: str
    aggregation: str
    unavailable_reason: str | None


class Provider(Protocol):
    name: str

    def collect(self, entities: dict[EntityKey, Entity]) -> dict[EntityKey, NetSample]: ...

    def status(self) -> dict: ...


def unavailable_sample(reason: str, *, source_label: str = "net:N/A", confidence: str = "n/a", aggregation: str = "none") -> NetSample:
    return NetSample(
        rx_bytes=None,
        tx_bytes=None,
        rx_pkts=None,
        tx_pkts=None,
        proto=None,
        source_label=source_label,
        confidence=confidence,
        aggregation=aggregation,
        unavailable_reason=reason,
    )


def sample_rank(sample: NetSample) -> int:
    return {
        "net:N/A": 0,
        "net:HOST": 1,
        "net:NS": 2,
        "net:BPF": 3,
    }.get(sample.source_label, -1)
