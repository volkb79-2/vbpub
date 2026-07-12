from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from groop.collect.cgroup import (
    CgroupSample,
    add_entity_ancestors,
    build_entity_predicate,
    collect_cgroup,
    walk_entities,
)
from groop.collect.dockerjoin import DockerInspect, enrich_entities
from groop.collect.host import collect_host, collect_host_meta
from groop.config import GroopConfig, load
from groop.damon import DEFAULT_DAMON_ROOT, annotate_frame_damon
from groop.diag import annotate as annotate_frame_diagnostics
from groop.drift.origin import SystemctlShowRunner, annotate_frame_governance
from groop.model import Entity, EntityFrame, EntityKey, Frame, MetricSource, MetricValue
from groop.providers.base import NetSample, Provider, sample_rank
from groop.providers.net_host import NetHostProvider
from groop.providers.net_netns import NetnsProvider
from groop.registry import COMPACT_GROUPS, METRIC_GROUPS


class Collector:
    def __init__(
        self,
        cgroup_root: Path | None = None,
        config: GroopConfig | None = None,
        docker_inspect: DockerInspect | None = None,
        host_collector: Callable[[], dict[str, MetricValue]] | None = None,
        now: Callable[[], float] | None = None,
        network_providers: tuple[Provider, ...] | None = None,
        proc_root: Path = Path("/proc"),
        sys_root: Path = Path("/sys"),
        damon_root: Path = DEFAULT_DAMON_ROOT,
        damon_state_dir: Path | None = None,
        systemctl_show_runner: SystemctlShowRunner | None = None,
        entities_globs: tuple[str, ...] | None = None,
        slice_names: tuple[str, ...] | None = None,
        metrics_mode: str = "full",
    ) -> None:
        self.config = config or load()
        self.cgroup_root = cgroup_root or self.config.cgroup_root
        self.docker_inspect = docker_inspect
        self.now = now or time.time
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.host_collector = host_collector or (
            lambda: collect_host(proc_root=self.proc_root, sys_root=self.sys_root)
        )
        self.damon_root = damon_root
        self.damon_state_dir = damon_state_dir
        self.systemctl_show_runner = systemctl_show_runner
        self.network_providers = network_providers if network_providers is not None else (
            NetnsProvider(self.cgroup_root, proc_root=self.proc_root),
            NetHostProvider(proc_root=self.proc_root),
        )
        self._prev_ts: float | None = None
        self._prev_counters: dict[tuple[EntityKey, str], int] = {}
        self._prev_device_counters: dict[str, list[dict[str, object]]] | None = None
        # Entity and metric filtering state
        self._entity_predicate = build_entity_predicate(entities_globs, slice_names)
        # Pre-compute the set of metric names to keep under compact mode
        if metrics_mode == "compact":
            self._compact_metric_names: frozenset[str] = frozenset().union(
                *(METRIC_GROUPS[g] for g in COMPACT_GROUPS)
            )
        else:
            self._compact_metric_names = frozenset()

    def collect_once(self) -> Frame:
        ts = self.now()
        interval_s = self.config.interval if self._prev_ts is None else max(0.0, ts - self._prev_ts)
        entities = walk_entities(self.cgroup_root)
        self._apply_config(entities)
        entities = enrich_entities(entities, self.docker_inspect)
        # Apply entity filtering: determine which entity keys to collect.
        # Non-matching entities are skipped (no sysfs reads for their cgroup).
        if self._entity_predicate is not None:
            matched = {k for k in entities if self._entity_predicate(k)}
            collect_keys = add_entity_ancestors(matched)
        else:
            collect_keys = set(entities.keys())
        frames: dict[EntityKey, EntityFrame] = {}
        for key, entity in entities.items():
            if key not in collect_keys:
                continue
            sample = collect_cgroup(self.cgroup_root, key, entity)
            metrics = dict(sample.metrics)
            metrics.update(self._derived_rates(key, sample, interval_s))
            frames[key] = EntityFrame(entity=sample.entity, metrics=metrics)
        self._apply_network_metrics(frames, entities, interval_s)
        self._prev_ts = ts
        host_meta = collect_host_meta(proc_root=self.proc_root, sys_root=self.sys_root)
        self._apply_host_device_rates(host_meta, interval_s)
        frame = Frame(
            schema_version=1,
            ts=ts,
            interval_s=interval_s,
            host=self.host_collector(),
            entities=frames,
            host_meta=host_meta,
        )
        annotate_frame_damon(
            frame,
            damon_root=self.damon_root,
            proc_root=self.proc_root,
            cgroup_root=self.cgroup_root,
            config=self.config.damon,
            now=ts,
            state_dir=self.damon_state_dir,
        )
        annotate_frame_governance(frame, self.systemctl_show_runner)
        frame = annotate_frame_diagnostics(frame, self.config)
        # Apply metric filtering last, after all annotations have populated
        # entity metrics. This ensures ALL non-compact metrics (including
        # DAMON, governance, and diagnostics) are pruned.
        if self._compact_metric_names:
            for eframe in frame.entities.values():
                eframe.metrics = {
                    k: v for k, v in eframe.metrics.items()
                    if k in self._compact_metric_names
                }
        return frame

    def _apply_config(self, entities: dict[EntityKey, Entity]) -> None:
        for entity in entities.values():
            for tier, prefixes in self.config.tiers.items():
                if any(entity.key == prefix or entity.key.startswith(prefix.rstrip("/") + "/") for prefix in prefixes):
                    entity.tier = tier
                    break
            name = Path(entity.key).name
            entity.is_protected = entity.key in self.config.protected_services or name in self.config.protected_services

    def _delta(self, key: EntityKey, raw_name: str, raw_now: int | None) -> tuple[int | None, int | None]:
        if raw_now is None:
            return None, None
        prev_key = (key, raw_name)
        raw_prev = self._prev_counters.get(prev_key)
        self._prev_counters[prev_key] = raw_now
        if raw_prev is None or raw_now < raw_prev:
            return None, raw_now
        return raw_now - raw_prev, raw_now

    def _rate_metric(self, key: EntityKey, raw_name: str, output_raw: int | None, interval_s: float, src: str = "derived") -> MetricValue:
        delta, raw_now = self._delta(key, raw_name, output_raw)
        if delta is None or interval_s <= 0:
            return MetricValue(None, src, raw_now)
        return MetricValue(delta / interval_s, src, raw_now)

    def _apply_network_metrics(self, frames: dict[EntityKey, EntityFrame], entities: dict[EntityKey, Entity], interval_s: float) -> None:
        samples: dict[EntityKey, NetSample] = {}
        for provider in self.network_providers:
            for key, sample in provider.collect(entities).items():
                current = samples.get(key)
                if current is None or sample_rank(sample) > sample_rank(current):
                    samples[key] = sample
        for key, sample in samples.items():
            frame = frames.get(key)
            if frame is None:
                continue
            frame.metrics.update(self._network_rates(key, sample, interval_s))
            frame.network = self._network_metadata(sample)

    def _network_rates(self, key: EntityKey, sample: NetSample, interval_s: float) -> dict[str, MetricValue]:
        src = self._network_metric_source(sample)
        counter_prefix = f"network:{sample.source_label}:{sample.aggregation}"
        return {
            "net_rx_bps": self._rate_metric(key, f"{counter_prefix}:rx_bytes", sample.rx_bytes, interval_s, src=src),
            "net_tx_bps": self._rate_metric(key, f"{counter_prefix}:tx_bytes", sample.tx_bytes, interval_s, src=src),
            "net_rx_pps": self._rate_metric(key, f"{counter_prefix}:rx_pkts", sample.rx_pkts, interval_s, src=src),
            "net_tx_pps": self._rate_metric(key, f"{counter_prefix}:tx_pkts", sample.tx_pkts, interval_s, src=src),
        }

    def _network_metric_source(self, sample: NetSample) -> MetricSource:
        if sample.source_label == "net:BPF":
            return "bpf"
        if sample.source_label == "net:HOST" or sample.unavailable_reason == "host netns":
            return "host"
        if sample.source_label == "net:NS" or sample.unavailable_reason is not None:
            return "netns"
        return "derived"

    def _network_metadata(self, sample: NetSample) -> dict[str, object]:
        return {
            "source_label": sample.source_label,
            "confidence": sample.confidence,
            "aggregation": sample.aggregation,
            "unavailable_reason": sample.unavailable_reason,
            "proto": sample.proto,
        }

    def _derived_rates(self, key: EntityKey, sample: CgroupSample, interval_s: float) -> dict[str, MetricValue]:
        raw = sample.raw_counters
        out: dict[str, MetricValue] = {}
        wra_delta, wra_raw = self._delta(key, "memory.stat:workingset_refault_anon", raw.get("memory.stat:workingset_refault_anon"))
        zswpin_delta, zswpin_raw = self._delta(key, "memory.stat:zswpin", raw.get("memory.stat:zswpin"))
        if wra_delta is None or zswpin_delta is None or interval_s <= 0:
            out["rf_z_per_s"] = MetricValue(None, "derived", zswpin_raw)
            out["rf_d_per_s"] = MetricValue(None, "derived", wra_raw)
        else:
            out["rf_z_per_s"] = MetricValue(zswpin_delta / interval_s, "derived", zswpin_raw)
            out["rf_d_per_s"] = MetricValue(max(0, wra_delta - zswpin_delta) / interval_s, "derived", wra_raw)
        out["rf_f_per_s"] = self._rate_metric(key, "memory.stat:workingset_refault_file", raw.get("memory.stat:workingset_refault_file"), interval_s)
        usage = self._rate_metric(key, "cpu.stat:usage_usec", raw.get("cpu.stat:usage_usec"), interval_s)
        out["cpu_pct"] = usage if usage.v is None else MetricValue((usage.v / 1_000_000.0) * 100.0, "derived", usage.raw)
        throttled = self._rate_metric(key, "cpu.stat:throttled_usec", raw.get("cpu.stat:throttled_usec"), interval_s)
        out["cpu_throttled_pct"] = throttled if throttled.v is None else MetricValue((throttled.v / 1_000_000.0) * 100.0, "derived", throttled.raw)
        for metric, raw_name in (
            ("io_r_bps", "io.stat:rbytes"),
            ("io_w_bps", "io.stat:wbytes"),
            ("io_r_iops", "io.stat:rios"),
            ("io_w_iops", "io.stat:wios"),
            ("io_discard_bps", "io.stat:dbytes"),
            ("pgscan_per_s", "memory.stat:pgscan"),
            ("pgsteal_per_s", "memory.stat:pgsteal"),
            ("restore_anon_per_s", "memory.stat:workingset_restore_anon"),
            ("mem_events_low_per_s", "memory.events:low"),
            ("mem_events_high_per_s", "memory.events:high"),
            ("mem_events_max_per_s", "memory.events:max"),
            ("mem_events_oom_per_s", "memory.events:oom"),
            ("mem_events_oom_kill_per_s", "memory.events:oom_kill"),
            ("pids_events_max_per_s", "pids.events:max"),
        ):
            out[metric] = self._rate_metric(key, raw_name, raw.get(raw_name), interval_s)

        # io_cap_saturation_pct: compare each I/O rate to its finite io.max cap
        # and take the highest ratio; clamp at 0, allow overshoot above 100.
        io_sat: float | None = None
        for rate_name, cap_field in (
            ("io_r_bps", "io.max:rbps"),
            ("io_w_bps", "io.max:wbps"),
            ("io_r_iops", "io.max:riops"),
            ("io_w_iops", "io.max:wiops"),
        ):
            rate = out.get(rate_name)
            cap = raw.get(cap_field)
            if rate is not None and rate.v is not None and isinstance(cap, int) and cap > 0:
                ratio = float(rate.v) / float(cap)
                if io_sat is None or ratio > io_sat:
                    io_sat = ratio
        if io_sat is not None:
            out["io_cap_saturation_pct"] = MetricValue(max(0.0, io_sat * 100.0), "derived")
        else:
            io_max_readable = raw.get("io.max:_available") == 1
            any_cap = any(raw.get(f) is not None for f in ("io.max:rbps", "io.max:wbps", "io.max:riops", "io.max:wiops"))
            if any_cap:
                out["io_cap_saturation_pct"] = MetricValue(None, "derived")
            elif io_max_readable:
                out["io_cap_saturation_pct"] = MetricValue(None, "unlimited")
            else:
                out["io_cap_saturation_pct"] = MetricValue(None, "unavail_kernel")
        return out

    def _apply_host_device_rates(self, host_meta: dict[str, object], interval_s: float) -> None:
        """Compute host device rates from raw counters and store in host_meta.

        Replaces raw "net_device_counters" with rate "net_devices" and raw
        "block_device_counters" with rate "block_devices". On first sample
        (no previous counters) rates are None (collecting state).
        """
        prev = self._prev_device_counters
        net_raw = _device_counter_list(host_meta, "net_device_counters")
        block_raw = _device_counter_list(host_meta, "block_device_counters")

        if net_raw is not None and (prev is None or "net_device_counters" not in prev):
            # Compute first-sample rates (all None, collecting state)
            net_rates = [
                {
                    "name": d["name"],
                    "rx_bps": None,
                    "tx_bps": None,
                    "rx_pps": None,
                    "tx_pps": None,
                    "rx_errors_s": None,
                    "rx_drops_s": None,
                    "tx_errors_s": None,
                    "tx_drops_s": None,
                    "src": d["src"],
                }
                for d in net_raw
            ]
        elif net_raw is not None and prev is not None:
            prev_net = prev.get("net_device_counters", [])
            prev_map = {d["name"]: d for d in prev_net}
            net_rates = []
            for d in net_raw:
                name = str(d["name"])
                pd = prev_map.get(name)
                if pd is None or interval_s <= 0:
                    net_rates.append({"name": name, "rx_bps": None, "tx_bps": None, "rx_pps": None, "tx_pps": None, "rx_errors_s": None, "rx_drops_s": None, "tx_errors_s": None, "tx_drops_s": None, "src": "host"})
                else:
                    rx_b_delta = int(d["rx_bytes"]) - int(pd["rx_bytes"])
                    tx_b_delta = int(d["tx_bytes"]) - int(pd["tx_bytes"])
                    rx_p_delta = int(d["rx_packets"]) - int(pd["rx_packets"])
                    tx_p_delta = int(d["tx_packets"]) - int(pd["tx_packets"])
                    rx_e_delta = int(d["rx_errors"]) - int(pd["rx_errors"])
                    rx_d_delta = int(d["rx_drop"]) - int(pd["rx_drop"])
                    tx_e_delta = int(d["tx_errors"]) - int(pd["tx_errors"])
                    tx_d_delta = int(d["tx_drop"]) - int(pd["tx_drop"])
                    net_rates.append({
                        "name": name,
                        "rx_bps": max(0.0, rx_b_delta / interval_s),
                        "tx_bps": max(0.0, tx_b_delta / interval_s),
                        "rx_pps": max(0.0, rx_p_delta / interval_s),
                        "tx_pps": max(0.0, tx_p_delta / interval_s),
                        "rx_errors_s": max(0.0, rx_e_delta / interval_s),
                        "rx_drops_s": max(0.0, rx_d_delta / interval_s),
                        "tx_errors_s": max(0.0, tx_e_delta / interval_s),
                        "tx_drops_s": max(0.0, tx_d_delta / interval_s),
                        "src": "host",
                    })
            del prev_map
        else:
            net_rates = None

        if block_raw is not None and (prev is None or "block_device_counters" not in prev):
            block_rates = [
                {
                    "name": d["name"],
                    "read_bps": None,
                    "write_bps": None,
                    "read_iops": None,
                    "write_iops": None,
                    "src": d["src"],
                }
                for d in block_raw
            ]
        elif block_raw is not None and prev is not None:
            prev_block = prev.get("block_device_counters", [])
            prev_map = {d["name"]: d for d in prev_block}
            block_rates = []
            for d in block_raw:
                name = str(d["name"])
                pd = prev_map.get(name)
                if pd is None or interval_s <= 0:
                    block_rates.append({"name": name, "read_bps": None, "write_bps": None, "read_iops": None, "write_iops": None, "src": "host"})
                else:
                    rd_s_delta = int(d["rd_sectors"]) - int(pd["rd_sectors"])
                    wr_s_delta = int(d["wr_sectors"]) - int(pd["wr_sectors"])
                    rd_io_delta = int(d["rd_ios"]) - int(pd["rd_ios"])
                    wr_io_delta = int(d["wr_ios"]) - int(pd["wr_ios"])
                    # Convert sectors to bytes (1 sector = 512 bytes)
                    block_rates.append({
                        "name": name,
                        "read_bps": max(0.0, rd_s_delta * 512.0 / interval_s),
                        "write_bps": max(0.0, wr_s_delta * 512.0 / interval_s),
                        "read_iops": max(0.0, rd_io_delta / interval_s),
                        "write_iops": max(0.0, wr_io_delta / interval_s),
                        "src": "host",
                    })
            del prev_map
        else:
            block_rates = None

        if net_rates is not None:
            host_meta["net_devices"] = net_rates
        host_meta.pop("net_device_counters", None)
        if block_rates is not None:
            host_meta["block_devices"] = block_rates
        host_meta.pop("block_device_counters", None)

        # Store current raw counters as previous for next frame
        self._prev_device_counters = {
            "net_device_counters": net_raw or [],
            "block_device_counters": block_raw or [],
        }


def _device_counter_list(host_meta: dict[str, object], key: str) -> list[dict[str, object]] | None:
    devices = host_meta.get(key)
    if not isinstance(devices, list):
        return None
    return [device for device in devices if isinstance(device, dict) and "name" in device]
