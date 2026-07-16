from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from topos.collect.cgroup import read_text
from topos.model import Entity, EntityKey
from topos.providers.base import NetSample, unavailable_sample
from topos.providers.net_host import parse_net_dev


@dataclass
class _Observation:
    sample: NetSample
    ns_ids: frozenset[int]
    contributes: bool
    own_pids: bool


@dataclass
class _Candidate:
    ns_id: int
    rx_bytes: int
    tx_bytes: int
    rx_pkts: int
    tx_pkts: int


class NetnsProvider:
    name = "net_netns"

    def __init__(self, cgroup_root: Path, *, proc_root: Path = Path("/proc"), host_netns_id: int | None = None) -> None:
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        self.host_netns_id = host_netns_id if host_netns_id is not None else self._detect_host_netns()
        self._status: dict[str, Any] = {
            "loaded": True,
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
            "host_netns_id": self.host_netns_id,
        }
        candidates: dict[EntityKey, _Candidate] = {}
        base: dict[EntityKey, _Observation] = {}
        ns_usage: dict[int, list[EntityKey]] = {}

        for key in entities:
            pids = self._read_pids(key)
            if not pids:
                base[key] = _Observation(unavailable_sample("no processes"), frozenset(), False, False)
                continue
            ns_ids = {ns_id for ns_id in (self._ns_id_for_pid(pid) for pid in pids) if ns_id is not None}
            if not ns_ids:
                base[key] = _Observation(unavailable_sample("no visible netns"), frozenset(), False, True)
                continue
            if len(ns_ids) > 1:
                base[key] = _Observation(unavailable_sample("multiple netns in cgroup"), frozenset(), False, True)
                continue
            ns_id = next(iter(ns_ids))
            if self.host_netns_id is not None and ns_id == self.host_netns_id:
                base[key] = _Observation(unavailable_sample("host netns", confidence="n/a"), frozenset(), False, True)
                continue
            candidate = self._candidate_from_pid(next(iter(pids)), ns_id)
            if candidate is None:
                base[key] = _Observation(unavailable_sample("missing /proc/<pid>/net/dev"), frozenset(), False, True)
                continue
            candidates[key] = candidate
            ns_usage.setdefault(ns_id, []).append(key)

        for key, candidate in candidates.items():
            if len(ns_usage[candidate.ns_id]) > 1:
                base[key] = _Observation(unavailable_sample("shared private netns"), frozenset(), False, True)
                continue
            sample = NetSample(
                rx_bytes=candidate.rx_bytes,
                tx_bytes=candidate.tx_bytes,
                rx_pkts=candidate.rx_pkts,
                tx_pkts=candidate.tx_pkts,
                proto=None,
                source_label="net:NS",
                confidence="estimated",
                aggregation="exact",
                unavailable_reason=None,
            )
            base[key] = _Observation(sample, frozenset({candidate.ns_id}), True, True)

        children: dict[EntityKey, list[EntityKey]] = {}
        for key, entity in entities.items():
            if entity.parent is not None:
                children.setdefault(entity.parent, []).append(key)
            children.setdefault(key, [])

        observations = dict(base)
        ordered_keys = sorted(entities, key=lambda value: value.count("/"), reverse=True)
        for key in ordered_keys:
            child_keys = children.get(key, [])
            if not child_keys:
                continue
            current = observations.get(key)
            if current is not None and current.own_pids:
                continue
            child_states = [observations[child_key] for child_key in child_keys if child_key in observations]
            if len(child_states) != len(child_keys) or not child_states:
                observations[key] = _Observation(unavailable_sample("aggregation proof failed"), frozenset(), False, False)
                continue
            ns_sets = [state.ns_ids for state in child_states]
            if any(not state.contributes or not ns_ids for state, ns_ids in zip(child_states, ns_sets, strict=False)):
                observations[key] = _Observation(unavailable_sample("aggregation proof failed"), frozenset(), False, False)
                continue
            combined = set().union(*ns_sets)
            if sum(len(ns_ids) for ns_ids in ns_sets) != len(combined):
                observations[key] = _Observation(unavailable_sample("aggregation proof failed"), frozenset(), False, False)
                continue
            sample = NetSample(
                rx_bytes=sum(state.sample.rx_bytes or 0 for state in child_states),
                tx_bytes=sum(state.sample.tx_bytes or 0 for state in child_states),
                rx_pkts=sum(state.sample.rx_pkts or 0 for state in child_states),
                tx_pkts=sum(state.sample.tx_pkts or 0 for state in child_states),
                proto=None,
                source_label="net:NS",
                confidence="estimated",
                aggregation="private_ns_only",
                unavailable_reason=None,
            )
            observations[key] = _Observation(sample, frozenset(combined), True, False)

        self._status.update(
            {
                "entities_seen": len(entities),
                "private_namespaces": len(ns_usage),
                "shared_namespaces": sum(1 for keys in ns_usage.values() if len(keys) > 1),
            }
        )
        return {key: observation.sample for key, observation in observations.items()}

    def status(self) -> dict:
        return dict(self._status)

    def _detect_host_netns(self) -> int | None:
        try:
            return (self.proc_root / "1" / "ns" / "net").stat().st_ino
        except OSError:
            return None

    def _read_pids(self, key: EntityKey) -> tuple[int, ...]:
        cgroup_path = self.cgroup_root if key == "" else self.cgroup_root / key
        result = read_text(cgroup_path / "cgroup.procs")
        if result.value is None:
            return ()
        out: list[int] = []
        for line in str(result.value).splitlines():
            try:
                out.append(int(line.strip()))
            except ValueError:
                continue
        return tuple(out)

    def _ns_id_for_pid(self, pid: int) -> int | None:
        try:
            return (self.proc_root / str(pid) / "ns" / "net").stat().st_ino
        except OSError:
            return None

    def _candidate_from_pid(self, pid: int, ns_id: int) -> _Candidate | None:
        result = read_text(self.proc_root / str(pid) / "net" / "dev")
        if result.value is None:
            return None
        interfaces = parse_net_dev(str(result.value))
        return _Candidate(
            ns_id=ns_id,
            rx_bytes=sum(values["rx_bytes"] for values in interfaces.values()),
            tx_bytes=sum(values["tx_bytes"] for values in interfaces.values()),
            rx_pkts=sum(values["rx_pkts"] for values in interfaces.values()),
            tx_pkts=sum(values["tx_pkts"] for values in interfaces.values()),
        )
