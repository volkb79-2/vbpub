from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    name: str
    unit: str
    kind: str
    locality: str
    branch_policy: str
    aggregatable: bool
    sources: tuple[str, ...]
    glossary: str
    threshold_key: str | None


def _m(name: str, unit: str, kind: str, locality: str, branch_policy: str, aggregatable: bool, sources: tuple[str, ...], glossary: str, threshold_key: str | None = None) -> MetricSpec:
    return MetricSpec(name, unit, kind, locality, branch_policy, aggregatable, sources, glossary, threshold_key)


REGISTRY: dict[str, MetricSpec] = {
    "ram": _m("ram", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.current",), "Physical memory charged to the cgroup, including cache and kernel memory."),
    "anon": _m("anon", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.stat:anon",), "Resident anonymous memory charged to the cgroup."),
    "file": _m("file", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.stat:file",), "File-backed cache and shmem charged to the cgroup."),
    "shmem": _m("shmem", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.stat:shmem",), "Shared memory and tmpfs charged to the cgroup."),
    "sock": _m("sock", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.stat:sock",), "Socket buffer memory charged to the cgroup."),
    "z_pool": _m("z_pool", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.zswap.current",), "Compressed bytes currently held in zswap for this cgroup."),
    "z_eq": _m("z_eq", "bytes", "gauge", "subtree", "kernel_subtree", False, ("memory.stat:zswapped",), "Uncompressed-equivalent bytes represented by the cgroup's zswap pool."),
    "ratio": _m("ratio", "ratio", "derived", "subtree", "kernel_subtree", False, ("memory.stat:zswapped", "memory.zswap.current"), "Compression ratio, computed as z_eq divided by z_pool."),
    "swap_disk": _m("swap_disk", "bytes", "derived", "subtree", "kernel_subtree", False, ("memory.swap.current", "memory.stat:zswapped", "memory.stat:swapcached"), "Legacy name for non-zswap swap-device usage estimate. On zram-only hosts this is logical zram-backed swap, not physical disk IO; mixed hosts cannot attribute backend per cgroup."),
    "rf_z_per_s": _m("rf_z_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:zswpin",), "Zswap refault rate, computed from delta zswpin per second."),
    "rf_d_per_s": _m("rf_d_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:workingset_refault_anon", "memory.stat:zswpin"), "Legacy name for non-zswap anonymous refault rate. Disk-lag predictor on disk-backed swap; zram/mixed hosts require backend-aware wording.", "rf_d_per_s"),
    "rf_f_per_s": _m("rf_f_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:workingset_refault_file",), "File-cache refault rate from delta workingset_refault_file per second."),
    "cpu_pct": _m("cpu_pct", "%", "derived", "local", "local_only", False, ("cpu.stat:usage_usec",), "CPU consumption over the sample interval."),
    "cpu_weight": _m("cpu_weight", "count", "gauge", "local", "local_only", False, ("cpu.weight",), "Cgroup CPU proportional weight."),
    "io_weight": _m("io_weight", "count", "gauge", "local", "local_only", False, ("io.weight",), "Cgroup default I/O proportional weight from io.weight."),
    "cpu_quota_us": _m("cpu_quota_us", "us", "gauge", "local", "local_only", False, ("cpu.max",), "CPU quota in microseconds; src=unlimited when cpu.max has no quota."),
    "cpu_period_us": _m("cpu_period_us", "us", "gauge", "local", "local_only", False, ("cpu.max",), "CPU quota period in microseconds."),
    "cpu_throttled_pct": _m("cpu_throttled_pct", "%", "derived", "local", "local_only", False, ("cpu.stat:throttled_usec",), "Fraction of wall time throttled by CPU quota during the sample interval."),
    "psi_mem_some_avg10": _m("psi_mem_some_avg10", "%", "gauge", "local", "local_only", False, ("memory.pressure",), "Memory PSI some avg10."),
    "psi_mem_full_avg10": _m("psi_mem_full_avg10", "%", "gauge", "local", "local_only", False, ("memory.pressure",), "Memory PSI full avg10.", "psi_full_avg10"),
    "psi_io_some_avg10": _m("psi_io_some_avg10", "%", "gauge", "local", "local_only", False, ("io.pressure",), "I/O PSI some avg10.", "psi_some_avg10"),
    "psi_io_full_avg10": _m("psi_io_full_avg10", "%", "gauge", "local", "local_only", False, ("io.pressure",), "I/O PSI full avg10.", "psi_full_avg10"),
    "psi_cpu_some_avg10": _m("psi_cpu_some_avg10", "%", "gauge", "local", "local_only", False, ("cpu.pressure",), "CPU PSI some avg10."),
    "psi_cpu_full_avg10": _m("psi_cpu_full_avg10", "%", "gauge", "local", "local_only", False, ("cpu.pressure",), "CPU PSI full avg10 when the kernel exposes it."),
    "io_r_bps": _m("io_r_bps", "bytes/s", "derived", "subtree", "kernel_subtree", True, ("io.stat:rbytes",), "Read bandwidth from io.stat deltas."),
    "io_w_bps": _m("io_w_bps", "bytes/s", "derived", "subtree", "kernel_subtree", True, ("io.stat:wbytes",), "Write bandwidth from io.stat deltas."),
    "io_r_iops": _m("io_r_iops", "/s", "derived", "subtree", "kernel_subtree", True, ("io.stat:rios",), "Read operation rate from io.stat deltas."),
    "io_w_iops": _m("io_w_iops", "/s", "derived", "subtree", "kernel_subtree", True, ("io.stat:wios",), "Write operation rate from io.stat deltas."),
    "io_discard_bps": _m("io_discard_bps", "bytes/s", "derived", "subtree", "kernel_subtree", True, ("io.stat:dbytes",), "Discard bandwidth from io.stat deltas."),
    "io_max_capped": _m("io_max_capped", "count", "gauge", "local", "local_only", False, ("io.max",), "Whether any finite io.max cap is configured for the cgroup: 1 when capped, 0 when every io.max limit is unlimited."),
    "io_cap_saturation_pct": _m("io_cap_saturation_pct", "%", "derived", "local", "local_only", False, ("io.max", "io.stat"), "I/O cap saturation: highest ratio of any I/O rate (r/w bytes/s or ops/s) to its finite io.max cap, expressed as a percentage. Values above 100 mean the rate exceeded the cap during the sample. Unavailable when no finite cap exists or rates are missing."),
    "mem_min": _m("mem_min", "bytes", "gauge", "local", "local_only", False, ("memory.min",), "Configured memory.min protection for this cgroup."),
    "mem_low": _m("mem_low", "bytes", "gauge", "local", "local_only", False, ("memory.low",), "Configured memory.low protection for this cgroup."),
    "mem_high": _m("mem_high", "bytes", "gauge", "local", "local_only", False, ("memory.high",), "Configured memory.high throttle threshold; src=unlimited when no throttle is configured."),
    "mem_max": _m("mem_max", "bytes", "gauge", "local", "local_only", False, ("memory.max",), "Configured memory.max hard limit; src=unlimited when no hard limit is configured."),
    "headroom_high_pct": _m("headroom_high_pct", "%", "derived", "local", "local_only", False, ("memory.current", "memory.high"), "Percent of memory.high consumed."),
    "headroom_max_pct": _m("headroom_max_pct", "%", "derived", "local", "local_only", False, ("memory.current", "memory.max"), "Percent of memory.max consumed."),
    "pids_current": _m("pids_current", "count", "gauge", "local", "local_only", False, ("pids.current",), "Current process count in the cgroup."),
    "pids_max": _m("pids_max", "count", "gauge", "local", "local_only", False, ("pids.max",), "Configured pids.max limit; src=unlimited when no process limit is configured."),
    "cgroup_procs": _m("cgroup_procs", "count", "gauge", "local", "local_only", False, ("cgroup.procs",), "Number of process IDs directly listed in cgroup.procs."),
    "mem_events_low_per_s": _m("mem_events_low_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.events:low",), "Rate of memory.events low counter changes."),
    "mem_events_high_per_s": _m("mem_events_high_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.events:high",), "Rate of memory.events high counter changes."),
    "mem_events_max_per_s": _m("mem_events_max_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.events:max",), "Rate of memory.events max counter changes."),
    "mem_events_oom_per_s": _m("mem_events_oom_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.events:oom",), "Rate of memory.events oom counter changes."),
    "mem_events_oom_kill_per_s": _m("mem_events_oom_kill_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.events:oom_kill",), "Rate of memory.events oom_kill counter changes.", "mem_events_oom_kill"),
    "pids_events_max_per_s": _m("pids_events_max_per_s", "/s", "derived", "local", "local_only", False, ("pids.events:max",), "Rate of fork attempts rejected by pids.max."),
    "pgscan_per_s": _m("pgscan_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:pgscan",), "Page scan rate."),
    "pgsteal_per_s": _m("pgsteal_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:pgsteal",), "Page steal rate."),
    "restore_anon_per_s": _m("restore_anon_per_s", "/s", "derived", "subtree", "kernel_subtree", False, ("memory.stat:workingset_restore_anon",), "Anonymous workingset restore rate."),
    "net_rx_bps": _m("net_rx_bps", "bytes/s", "derived", "local", "child_sum", False, ("provider:network-bpf", "provider:network-host", "provider:network-netns"), "Per-entity network receive rate from the active network provider. src=bpf means exact per-cgroup socket accounting from daemon-produced BPF snapshots; src=netns means a private-network-namespace approximation; src=host means host/interface truth or an explicit host-netns n/a state. Branch rows appear only when the provider proved distinct private namespaces."),
    "net_tx_bps": _m("net_tx_bps", "bytes/s", "derived", "local", "child_sum", False, ("provider:network-bpf", "provider:network-host", "provider:network-netns"), "Per-entity network transmit rate from the active network provider. src=bpf means exact per-cgroup socket accounting from daemon-produced BPF snapshots; src=netns means a private-network-namespace approximation; src=host means host/interface truth or an explicit host-netns n/a state. Branch rows appear only when the provider proved distinct private namespaces."),
    "net_rx_pps": _m("net_rx_pps", "/s", "derived", "local", "child_sum", False, ("provider:network-bpf", "provider:network-host", "provider:network-netns"), "Per-entity receive packet rate from the active network provider."),
    "net_tx_pps": _m("net_tx_pps", "/s", "derived", "local", "child_sum", False, ("provider:network-bpf", "provider:network-host", "provider:network-netns"), "Per-entity transmit packet rate from the active network provider."),
    "damon_hot_bytes": _m("damon_hot_bytes", "bytes", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Bytes in DAMON regions classified as hot for the attributed entity."),
    "damon_warm_bytes": _m("damon_warm_bytes", "bytes", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Bytes in DAMON regions classified as warm for the attributed entity."),
    "damon_cold_bytes": _m("damon_cold_bytes", "bytes", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Bytes in DAMON regions classified as cold for the attributed entity."),
    "damon_idle_bytes": _m("damon_idle_bytes", "bytes", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Bytes in DAMON regions classified as idle for the attributed entity."),
    "damon_hot_pct": _m("damon_hot_pct", "%", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of attributed DAMON bytes classified as hot."),
    "damon_warm_pct": _m("damon_warm_pct", "%", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of attributed DAMON bytes classified as warm."),
    "damon_cold_pct": _m("damon_cold_pct", "%", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of attributed DAMON bytes classified as cold."),
    "damon_idle_pct": _m("damon_idle_pct", "%", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of attributed DAMON bytes classified as idle."),
    "damon_sample_age_s": _m("damon_sample_age_s", "count", "derived", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Age in seconds of the newest passive DAMON tried_regions snapshot used for this entity."),
    "damon_mode": _m("damon_mode", "count", "gauge", "local", "local_only", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/operations",), "Numeric DAMON mode code for the attributed snapshot: 1 for vaddr, 2 for paddr."),
    "pressure": _m("pressure", "count", "derived", "local", "n/a", False, ("diag:score",), "Deterministic 0-100 pressure score derived from PSI, refaults, memory.high events, OOM kills, and any other supported diagnostics inputs. It is a triage aid only; drill-down explains the weighted contribution of each input."),
    "governance_origin": _m("governance_origin", "count", "derived", "local", "local_only", False, ("drift:origin",), "Numeric summary of the entity's most salient governance owner: 0 unset, 1 docker_default, 2 systemd_unit, 3 systemd_runtime_dropin, 4 raw_write. The explainable string classification lives in the governance block and is meant to catch Finding D raw writes before a daemon-reload wipes them."),
    "governance_drift": _m("governance_drift", "count", "derived", "local", "local_only", False, ("drift:origin", "drift:effective_memory_min"), "Numeric summary of governance drift severity: 0 none, 1 warn, 2 red. Warn means the live cgroup disagrees with systemd or has an unmanaged raw write; red is reserved for Finding A style effective-protection loss on a protected workload."),
    "effective_memory_min": _m("effective_memory_min", "bytes", "derived", "local", "local_only", False, ("memory.min", "drift:ancestor-clamp"), "Derived effective memory.min after clamping the entity's live value by every ancestor in the cgroup path. This is the registry surface for Finding A: a protected workload can ask for memory.min, yet still receive less when an ancestor's floor is lower."),
    "host_mem_total": _m("host_mem_total", "bytes", "gauge", "local", "n/a", False, ("/proc/meminfo:MemTotal",), "Host total memory."),
    "host_mem_available": _m("host_mem_available", "bytes", "gauge", "local", "n/a", False, ("/proc/meminfo:MemAvailable",), "Host available memory."),
    "host_swap_total": _m("host_swap_total", "bytes", "gauge", "local", "n/a", False, ("/proc/meminfo:SwapTotal",), "Host total swap."),
    "host_swap_free": _m("host_swap_free", "bytes", "gauge", "local", "n/a", False, ("/proc/meminfo:SwapFree",), "Host free swap."),
    "host_swapcached": _m("host_swapcached", "bytes", "gauge", "local", "n/a", False, ("/proc/meminfo:SwapCached",), "Host swapcached memory."),
    "host_zswap_pool": _m("host_zswap_pool", "bytes", "gauge", "local", "n/a", False, ("/sys/kernel/debug/zswap/pool_total_size", "/proc/meminfo:Zswap"), "Host zswap compressed pool bytes."),
    "host_zswap_stored": _m("host_zswap_stored", "bytes", "gauge", "local", "n/a", False, ("/sys/kernel/debug/zswap/stored_pages", "/proc/meminfo:Zswapped"), "Host zswap uncompressed-equivalent bytes."),
    "host_zswap_ratio": _m("host_zswap_ratio", "ratio", "derived", "local", "n/a", False, ("host_zswap_stored", "host_zswap_pool"), "Host zswap compression ratio."),
    "host_disk_swap": _m("host_disk_swap", "bytes", "derived", "local", "n/a", False, ("/proc/swaps", "/proc/meminfo:SwapCached", "/sys/kernel/debug/zswap/stored_pages"), "Estimated host non-zram disk-swap bytes."),
    "host_swap_backend": _m("host_swap_backend", "count", "gauge", "local", "n/a", False, ("/proc/swaps", "/sys/block/zram*", "/sys/module/zswap/parameters/enabled"), "Host swap backend code: 0 none, 1 zswap_only, 2 zram_only, 3 disk_only, 4 zswap_zram, 5 zswap_disk, 6 mixed, 7 unknown."),
    "host_zram_swap_devices": _m("host_zram_swap_devices", "count", "gauge", "local", "n/a", False, ("/proc/swaps",), "Number of active zram swap devices listed in /proc/swaps."),
    "host_disk_swap_devices": _m("host_disk_swap_devices", "count", "gauge", "local", "n/a", False, ("/proc/swaps",), "Number of active non-zram swap devices listed in /proc/swaps."),
    "host_zram_device_count": _m("host_zram_device_count", "count", "gauge", "local", "n/a", False, ("/sys/block/zram*",), "Number of zram block devices visible under /sys/block."),
    "host_zram_orig_bytes": _m("host_zram_orig_bytes", "bytes", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:orig_data_size",), "Host zram uncompressed logical data bytes."),
    "host_zram_compr_bytes": _m("host_zram_compr_bytes", "bytes", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:compr_data_size",), "Host zram compressed payload bytes."),
    "host_zram_mem_used_bytes": _m("host_zram_mem_used_bytes", "bytes", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:mem_used_total",), "Host zram actual memory used including allocator overhead."),
    "host_zram_mem_limit_bytes": _m("host_zram_mem_limit_bytes", "bytes", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:mem_limit",), "Host zram memory limit bytes."),
    "host_zram_mem_used_max_bytes": _m("host_zram_mem_used_max_bytes", "bytes", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:mem_used_max",), "Host zram peak memory used bytes."),
    "host_zram_same_pages": _m("host_zram_same_pages", "count", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:same_pages",), "Host zram same-filled pages."),
    "host_zram_huge_pages": _m("host_zram_huge_pages", "count", "gauge", "local", "n/a", False, ("/sys/block/zram*/mm_stat:huge_pages",), "Host zram incompressible huge pages."),
    "host_zram_ratio": _m("host_zram_ratio", "ratio", "derived", "local", "n/a", False, ("host_zram_orig_bytes", "host_zram_compr_bytes"), "Host zram logical compression ratio."),
    "host_zram_efficiency": _m("host_zram_efficiency", "ratio", "derived", "local", "n/a", False, ("host_zram_compr_bytes", "host_zram_mem_used_bytes"), "Host zram allocator efficiency: compressed payload divided by actual memory used."),
    "host_zram_failed_reads": _m("host_zram_failed_reads", "count", "counter", "local", "n/a", False, ("/sys/block/zram*/io_stat:failed_reads",), "Host zram failed read operations."),
    "host_zram_failed_writes": _m("host_zram_failed_writes", "count", "counter", "local", "n/a", False, ("/sys/block/zram*/io_stat:failed_writes",), "Host zram failed write operations."),
    "host_zram_writeback_bytes": _m("host_zram_writeback_bytes", "bytes", "counter", "local", "n/a", False, ("/sys/block/zram*/bd_stat:bd_count",), "Host zram backing-device writeback bytes when configured."),
    "host_damon_hot_bytes": _m("host_damon_hot_bytes", "bytes", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Host DRAM bytes classified hot by a DAMON paddr session. Unlike vaddr DAMON, paddr observes backed physical memory only and is never attributed to individual cgroups."),
    "host_damon_warm_bytes": _m("host_damon_warm_bytes", "bytes", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Host DRAM bytes classified warm by a DAMON paddr session. paddr heat is a host-wide signal for real RAM, not per-entity virtual address space."),
    "host_damon_cold_bytes": _m("host_damon_cold_bytes", "bytes", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Host DRAM bytes classified cold by a DAMON paddr session. It complements vaddr entity panels by showing whole-host physical memory heat."),
    "host_damon_idle_bytes": _m("host_damon_idle_bytes", "bytes", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Host DRAM bytes classified idle by a DAMON paddr session. These bytes are backed RAM observed at host scope, not cgroup-attributed pages."),
    "host_damon_hot_pct": _m("host_damon_hot_pct", "%", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of host paddr DAMON bytes classified hot."),
    "host_damon_warm_pct": _m("host_damon_warm_pct", "%", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of host paddr DAMON bytes classified warm."),
    "host_damon_cold_pct": _m("host_damon_cold_pct", "%", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of host paddr DAMON bytes classified cold."),
    "host_damon_idle_pct": _m("host_damon_idle_pct", "%", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Percent of host paddr DAMON bytes classified idle."),
    "host_damon_sample_age_s": _m("host_damon_sample_age_s", "count", "derived", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/schemes/*/tried_regions",), "Age in seconds of the paddr tried_regions snapshot used for host DRAM heat."),
    "host_damon_mode": _m("host_damon_mode", "count", "gauge", "local", "n/a", False, ("/sys/kernel/mm/damon/admin/kdamonds/*/contexts/*/operations",), "Numeric DAMON host mode code: 2 means paddr host DRAM heat."),
    "host_load1": _m("host_load1", "count", "gauge", "local", "n/a", False, ("/proc/loadavg",), "Host one-minute load average."),
    "host_load5": _m("host_load5", "count", "gauge", "local", "n/a", False, ("/proc/loadavg",), "Host five-minute load average."),
    "host_load15": _m("host_load15", "count", "gauge", "local", "n/a", False, ("/proc/loadavg",), "Host fifteen-minute load average."),
    "host_uptime_s": _m("host_uptime_s", "count", "gauge", "local", "n/a", False, ("/proc/uptime",), "Host uptime in seconds."),
    "host_psi_mem_some_avg10": _m("host_psi_mem_some_avg10", "%", "gauge", "local", "n/a", False, ("/proc/pressure/memory",), "Host memory PSI some avg10."),
    "host_psi_mem_full_avg10": _m("host_psi_mem_full_avg10", "%", "gauge", "local", "n/a", False, ("/proc/pressure/memory",), "Host memory PSI full avg10."),
    "host_psi_io_some_avg10": _m("host_psi_io_some_avg10", "%", "gauge", "local", "n/a", False, ("/proc/pressure/io",), "Host I/O PSI some avg10."),
    "host_psi_io_full_avg10": _m("host_psi_io_full_avg10", "%", "gauge", "local", "n/a", False, ("/proc/pressure/io",), "Host I/O PSI full avg10."),
    "host_psi_cpu_some_avg10": _m("host_psi_cpu_some_avg10", "%", "gauge", "local", "n/a", False, ("/proc/pressure/cpu",), "Host CPU PSI some avg10."),
    "host_zswap_enabled": _m("host_zswap_enabled", "count", "gauge", "local", "n/a", False, ("/sys/module/zswap/parameters/enabled",), "Whether zswap is enabled, as 1 or 0."),
    "host_zswap_max_pool_percent": _m("host_zswap_max_pool_percent", "%", "gauge", "local", "n/a", False, ("/sys/module/zswap/parameters/max_pool_percent",), "Configured zswap max pool percent."),
}

assert all(name == spec.name for name, spec in REGISTRY.items())

# Metric groups for --metrics compact and other family-level filtering.
# Each group is a tuple of metric names from REGISTRY.
# The compact mode keeps only metrics in groups listed here.
# Defined in registry.py (not inlined in cli.py) so it stays the single source of truth.
METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "mem_usage": (
        "ram", "anon", "file", "shmem", "sock",
        "z_pool", "z_eq", "swap_disk",
    ),
    "psi": (
        "psi_mem_some_avg10", "psi_mem_full_avg10",
        "psi_io_some_avg10", "psi_io_full_avg10",
        "psi_cpu_some_avg10", "psi_cpu_full_avg10",
    ),
    "refault": (
        "rf_z_per_s", "rf_d_per_s", "rf_f_per_s",
    ),
}
"""Compact-mode metric groups kept by ``--metrics compact``."""

# The set of group names that ``--metrics compact`` retains.
# Kept as a literal (not derived from METRIC_GROUPS.keys()) so that the
# net/damon/governance families added for the field-list selector below
# do NOT expand compact mode.
COMPACT_GROUPS: frozenset[str] = frozenset({"mem_usage", "psi", "refault"})

# Add net, damon, and governance families for the free-form --metrics field-list
# selector (P60). These are NOT part of COMPACT_GROUPS — compact mode drops
# them unconditionally. The field-list selector resolves them as family tokens
# that expand to their metric-name tuples and keep the corresponding structured
# per-entity block (eframe.network, eframe.damon, eframe.governance).
METRIC_GROUPS["net"] = (
    "net_rx_bps", "net_tx_bps", "net_rx_pps", "net_tx_pps",
)
METRIC_GROUPS["damon"] = (
    "damon_hot_bytes", "damon_warm_bytes", "damon_cold_bytes", "damon_idle_bytes",
    "damon_hot_pct", "damon_warm_pct", "damon_cold_pct", "damon_idle_pct",
    "damon_sample_age_s", "damon_mode",
)
METRIC_GROUPS["governance"] = (
    "governance_origin", "governance_drift", "effective_memory_min",
)

# Mapping from family token (as used in --metrics field-list) to the structured
# per-entity EntityFrame attribute name. A family in this map keeps that block
# when the family token is present in the selector; omitting it drops the block.
# Defined here (not in cli.py or collector.py) so registry.py is the single
# source of truth for all metric-group and block-ownership rules.
FIELD_LIST_BLOCK_MAP: dict[str, str] = {
    "net": "network",
    "damon": "damon",
    "governance": "governance",
}

# Verify every listed metric name exists in REGISTRY
for _group_name, _metric_names in METRIC_GROUPS.items():
    for _name in _metric_names:
        assert _name in REGISTRY, f"METRIC_GROUPS.{_group_name}: {_name!r} not in REGISTRY"


def parse_metrics_selector(selector: str) -> tuple[frozenset[str], frozenset[str]]:
    """Parse a comma-separated field-list selector into (kept_metric_names, kept_block_families).

    Each comma-separated token is either:
    - a family name in METRIC_GROUPS (expands to that family's metric-name tuple), or
    - an exact metric name in REGISTRY.

    The kept set is the union of all expanded/resolved tokens.
    A token matching a family in FIELD_LIST_BLOCK_MAP also keeps the corresponding
    structured per-entity block (eframe.network, eframe.damon, eframe.governance).

    Raises ValueError with the names of unknown tokens, or for an empty selector.
    On success returns (kept_metric_names, kept_block_families).
    """
    cleaned = selector.strip()
    if not cleaned:
        raise ValueError("empty selector")
    tokens = [t.strip() for t in cleaned.split(",")]
    if not all(tokens):
        raise ValueError("empty selector")

    kept_metrics: set[str] = set()
    kept_blocks: set[str] = set()
    unknown: list[str] = []

    for token in tokens:
        if token in METRIC_GROUPS:
            kept_metrics.update(METRIC_GROUPS[token])
            if token in FIELD_LIST_BLOCK_MAP:
                kept_blocks.add(FIELD_LIST_BLOCK_MAP[token])
        elif token in REGISTRY:
            kept_metrics.add(token)
        else:
            unknown.append(token)

    if unknown:
        raise ValueError(f"unknown metric token(s): {', '.join(sorted(unknown))}")
    if not kept_metrics:
        raise ValueError("empty selector")

    return frozenset(kept_metrics), frozenset(kept_blocks)
