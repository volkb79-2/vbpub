from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from topos.model import Entity, EntityKey
from topos.providers.base import NetSample, unavailable_sample

SNAPSHOT_FILENAME = "snapshot.json"
# Three default sweep intervals: a producer that has not refreshed the snapshot for this
# long is considered dead, and its frozen counters must not read as an idle container.
DEFAULT_MAX_SNAPSHOT_AGE_S = 15.0


class BpfProvider:
    """BPF-based per-cgroup socket traffic provider.

    Reads daemon-produced BPF map snapshots (JSON) from
    *state_dir/snapshot.json*. For compatibility with the P18 fixture seam,
    *state_dir* falls back to *bpf_root* when omitted.
    Each snapshot contains:

    - ``maps["topos_cgroup_skb"]`` - list of entries with
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

    def __init__(
        self,
        bpf_root: Path | None = None,
        state_dir: Path | None = None,
        max_age_s: float = DEFAULT_MAX_SNAPSHOT_AGE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_age_s = max_age_s
        self.clock = clock
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

        generated_at = snapshot.get("generated_at")
        if isinstance(generated_at, (int, float)) and not isinstance(generated_at, bool):
            age = self.clock() - float(generated_at)
            if age > self.max_age_s:
                # Lower-ranked providers fill the frame; frozen counters would read as 0 B/s.
                self._status["stale"] = True
                return self._all_unavailable(f"stale BPF snapshot: {age:.0f}s old (limit {self.max_age_s:g}s)")

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

        # Parse all map entries once and pre-aggregate per cgroup id (single pass).
        entries = self._index_entries(self._parse_entries(maps))

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

    @staticmethod
    def _index_entries(entries: list[dict[object, object]]) -> dict[int, dict[str, Any]]:
        """Fold the flat entry list into ``{cgroup_id: totals}`` in one pass."""
        index: dict[int, dict[str, Any]] = {}
        for entry in entries:
            cid = entry.get("cgroup_id")
            if cid is None or not isinstance(cid, int) or isinstance(cid, bool):
                continue
            row = index.setdefault(cid, {"rx_bytes": 0, "tx_bytes": 0, "rx_pkts": 0, "tx_pkts": 0, "proto": {}})
            direction = str(entry.get("direction", ""))
            bytes_val = _int_or_none(entry.get("bytes", 0)) or 0
            pkts_val = _int_or_none(entry.get("packets", 0)) or 0
            if direction == "ingress":
                row["rx_bytes"] += bytes_val
                row["rx_pkts"] += pkts_val
            elif direction == "egress":
                row["tx_bytes"] += bytes_val
                row["tx_pkts"] += pkts_val
            else:
                continue
            family = str(entry.get("family", "other"))
            proto = str(entry.get("proto", "other"))
            row["proto"].setdefault(family, {}).setdefault(proto, 0)
            row["proto"][family][proto] += pkts_val
        return index

    def _aggregate_for_entity(
        self,
        key: EntityKey,
        cgroup_ids: list[int],
        index: dict[int, dict[str, Any]] | list[dict[object, object]],
    ) -> NetSample | None:
        if isinstance(index, list):  # raw entry list (direct callers)
            index = self._index_entries(index)
        rx_bytes = tx_bytes = rx_pkts = tx_pkts = 0
        proto_map: dict[str, dict[str, int]] = {}
        matched = False
        for cid in set(cgroup_ids):
            row = index.get(cid)
            if row is None:
                continue
            matched = True
            rx_bytes += row["rx_bytes"]
            tx_bytes += row["tx_bytes"]
            rx_pkts += row["rx_pkts"]
            tx_pkts += row["tx_pkts"]
            for family, protos in row["proto"].items():
                for proto, pkts in protos.items():
                    proto_map.setdefault(family, {}).setdefault(proto, 0)
                    proto_map[family][proto] += pkts

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
