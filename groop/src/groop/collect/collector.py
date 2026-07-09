from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from groop.collect.cgroup import CgroupSample, collect_cgroup, walk_entities
from groop.collect.dockerjoin import DockerInspect, enrich_entities
from groop.collect.host import collect_host
from groop.config import GroopConfig, load
from groop.damon import DEFAULT_DAMON_ROOT, annotate_frame_damon
from groop.diag import annotate as annotate_frame_diagnostics
from groop.drift.origin import SystemctlShowRunner, annotate_frame_governance
from groop.model import Entity, EntityFrame, EntityKey, Frame, MetricSource, MetricValue
from groop.providers.base import NetSample, Provider, sample_rank
from groop.providers.net_host import NetHostProvider
from groop.providers.net_netns import NetnsProvider


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
        damon_root: Path = DEFAULT_DAMON_ROOT,
        damon_state_dir: Path | None = None,
        systemctl_show_runner: SystemctlShowRunner | None = None,
    ) -> None:
        self.config = config or load()
        self.cgroup_root = cgroup_root or self.config.cgroup_root
        self.docker_inspect = docker_inspect
        self.host_collector = host_collector or collect_host
        self.now = now or time.time
        self.proc_root = proc_root
        self.damon_root = damon_root
        self.damon_state_dir = damon_state_dir
        self.systemctl_show_runner = systemctl_show_runner
        self.network_providers = network_providers if network_providers is not None else (
            NetnsProvider(self.cgroup_root, proc_root=self.proc_root),
            NetHostProvider(proc_root=self.proc_root),
        )
        self._prev_ts: float | None = None
        self._prev_counters: dict[tuple[EntityKey, str], int] = {}

    def collect_once(self) -> Frame:
        ts = self.now()
        interval_s = self.config.interval if self._prev_ts is None else max(0.0, ts - self._prev_ts)
        entities = walk_entities(self.cgroup_root)
        self._apply_config(entities)
        entities = enrich_entities(entities, self.docker_inspect)
        frames: dict[EntityKey, EntityFrame] = {}
        for key, entity in entities.items():
            sample = collect_cgroup(self.cgroup_root, key, entity)
            metrics = dict(sample.metrics)
            metrics.update(self._derived_rates(key, sample, interval_s))
            frames[key] = EntityFrame(entity=sample.entity, metrics=metrics)
        self._apply_network_metrics(frames, entities, interval_s)
        self._prev_ts = ts
        frame = Frame(schema_version=1, ts=ts, interval_s=interval_s, host=self.host_collector(), entities=frames)
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
        return annotate_frame_diagnostics(frame, self.config)

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
        return out
