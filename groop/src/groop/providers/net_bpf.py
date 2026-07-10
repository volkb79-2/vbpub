from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from groop.model import Entity, EntityKey
from groop.providers.base import NetSample, unavailable_sample

SNAPSHOT_FILENAME = "snapshot.json"


class BpfProvider:
    """BPF-based per-cgroup socket traffic provider.

    Reads daemon-produced BPF map snapshots (JSON) from
    *state_dir/snapshot.json*. For compatibility with the P18 fixture seam,
    *state_dir* falls back to *bpf_root* when omitted.
    Each snapshot contains:

    - ``maps["groop_cgroup_skb"]`` - list of entries with
      ``cgroup_id``, ``direction`` ("ingress"|"egress"), ``family``, ``proto``,
      ``bytes``, ``packets``.
    - ``cgroup_map`` - ``{str(cgroup_id): entity_key}`` built by the
      daemon/helper that walks the cgroup tree.

    The provider maps numeric cgroup ids back to entity keys entirely in
    userspace, emitting ``NetSample`` with ``source_label="net:BPF"`` and
    ``confidence="exact"``.

    When no snapshot is available (root missing, file absent, parse error), the
    provider returns no samples so lower-ranked providers can fill the frame and
    populates ``status()`` with the reason.
    """

    name = "net_bpf"

    def __init__(self, bpf_root: Path | None = None, state_dir: Path | None = None) -> None:
        self.bpf_root = bpf_root
        self.state_dir = state_dir or bpf_root
        self._status: dict[str, Any] = {
            "loaded": False,
            "attached": False,
            "last_read": None,
            "errors": [],
        }

    def collect(self, entities: dict[EntityKey, Entity]) -> dict[EntityKey, NetSample]:
        self._status = {
            "loaded": True,
            "attached": False,
            "last_read": time.time(),
            "errors": [],
        }

        if self.bpf_root is None:
            return self._all_unavailable("no BPF root configured")

        state_dir = self.state_dir or self.bpf_root
        snapshot_path = state_dir / SNAPSHOT_FILENAME
        if not snapshot_path.exists():
            return self._all_unavailable(f"no BPF snapshot at {snapshot_path}")

        snapshot = self._load_snapshot(snapshot_path)
        if snapshot is None:
            return self._all_unavailable(f"failed to parse BPF snapshot at {snapshot_path}")

        maps = snapshot.get("maps", {})
        cgroup_map = snapshot.get("cgroup_map", {})
        if not isinstance(maps, dict) or not isinstance(cgroup_map, dict):
            return self._all_unavailable(f"invalid BPF snapshot shape at {snapshot_path}")

        # Build entity_key -> list[cgroup_id] from cgroup_map
        entity_to_cgroup: dict[str, list[int]] = {}
        for cid_str, ekey in cgroup_map.items():
            if not isinstance(ekey, str):
                continue
            try:
                cid = int(cid_str)
            except (TypeError, ValueError):
                continue
            entity_to_cgroup.setdefault(ekey, []).append(cid)

        # Parse all map entries
        entries = self._parse_entries(maps)

        # Aggregate per entity
        result: dict[EntityKey, NetSample] = {}
        for key in entities:
            cgroup_ids = entity_to_cgroup.get(key, [])
            if not cgroup_ids:
                result[key] = unavailable_sample(
                    "no BPF counter mapping for this entity",
                    source_label="net:N/A",
                    confidence="n/a",
                )
                continue
            sample = self._aggregate_for_entity(key, cgroup_ids, entries)
            if sample is None:
                result[key] = unavailable_sample(
                    "no BPF counters found for mapped cgroup ids",
                    source_label="net:N/A",
                    confidence="n/a",
                )
                continue
            result[key] = sample

        self._status["entities_seen"] = len(entities)
        self._status["entities_with_bpf"] = sum(
            1 for s in result.values() if s.source_label == "net:BPF"
        )
        self._status["snapshot_path"] = str(snapshot_path)
        self._status["state_dir"] = str(state_dir)
        return result

    def status(self) -> dict:
        return dict(self._status)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _all_unavailable(self, reason: str) -> dict[EntityKey, NetSample]:
        self._status["loaded"] = False
        self._status["attached"] = False
        self._status["errors"].append(reason)
        return {}

    def _load_snapshot(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._status["errors"].append(f"read error: {exc}")
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            self._status["errors"].append(f"JSON parse error: {exc}")
            return None

    @staticmethod
    def _parse_entries(maps: dict[object, object]) -> list[dict[object, object]]:
        """Flatten all named maps into a single list of entries."""
        entries: list[dict[object, object]] = []
        for map_name, map_entries in maps.items():
            if not isinstance(map_entries, list):
                continue
            for entry in map_entries:
                if not isinstance(entry, dict):
                    continue
                entries.append(entry)
        return entries

    def _aggregate_for_entity(
        self,
        key: EntityKey,
        cgroup_ids: list[int],
        entries: list[dict],
    ) -> NetSample | None:
        cid_set = frozenset(cgroup_ids)
        rx_bytes = 0
        tx_bytes = 0
        rx_pkts = 0
        tx_pkts = 0
        proto_map: dict[str, dict[str, int]] = {}
        matched = False

        for entry in entries:
            cid = entry.get("cgroup_id")
            if cid is None or not isinstance(cid, int):
                continue
            if cid not in cid_set:
                continue
            matched = True
            direction = str(entry.get("direction", ""))
            family = str(entry.get("family", "other"))
            proto = str(entry.get("proto", "other"))
            bytes_val = _int_or_none(entry.get("bytes", 0)) or 0
            pkts_val = _int_or_none(entry.get("packets", 0)) or 0

            if direction == "ingress":
                rx_bytes += bytes_val
                rx_pkts += pkts_val
            elif direction == "egress":
                tx_bytes += bytes_val
                tx_pkts += pkts_val
            else:
                continue

            proto_map.setdefault(family, {})
            proto_map[family].setdefault(proto, 0)
            proto_map[family][proto] += pkts_val

        if not matched:
            return None

        return NetSample(
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            rx_pkts=rx_pkts,
            tx_pkts=tx_pkts,
            proto={"family": proto_map} if proto_map else None,
            source_label="net:BPF",
            confidence="exact",
            aggregation="exact",
            unavailable_reason=None,
        )


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
