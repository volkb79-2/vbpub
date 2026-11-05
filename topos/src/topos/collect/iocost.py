"""Host io.cost state from the root cgroup (``io.cost.qos`` and ``io.stat`` ``cost.vrate``).

``cost.vrate`` is io.cost's global rate multiplier in percent of the nominal device
model (100 means the device performs as modelled). When it is pinned at the QoS
``min`` the device cannot meet its latency target. Both metrics are typed
unavailable when io.cost is off or the kernel does not expose it.
"""

from __future__ import annotations

from pathlib import Path

from topos.collect.cgroup import read_io_stat, read_text
from topos.model import MetricValue


def parse_io_cost_qos(text: str) -> dict[str, dict[str, str]]:
    """Parse ``io.cost.qos`` into ``{maj:min: {key: value}}``."""
    devices: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        fields = {k: v for k, _, v in (part.partition("=") for part in parts[1:])}
        devices[parts[0]] = fields
    return devices


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def collect_io_cost(cgroup_root: Path) -> tuple[dict[str, MetricValue], list[dict[str, object]]]:
    """Return ``(host metrics, per-device detail list for host_meta)``."""
    qos_file = read_text(cgroup_root / "io.cost.qos")
    if qos_file.value is None:
        unavailable = MetricValue(None, qos_file.src)
        return {"host_io_cost_enabled": unavailable, "host_io_cost_vrate_pct": MetricValue(None, qos_file.src)}, []
    qos = parse_io_cost_qos(str(qos_file.value))
    enabled_devices = {dev for dev, fields in qos.items() if fields.get("enable") == "1"}
    enabled = MetricValue(1 if enabled_devices else 0, "exact")
    if not enabled_devices:
        return {"host_io_cost_enabled": enabled, "host_io_cost_vrate_pct": MetricValue(None, "unavail_kernel")}, []
    stat, stat_src = read_io_stat(cgroup_root / "io.stat")
    if stat_src != "exact":
        return {"host_io_cost_enabled": enabled, "host_io_cost_vrate_pct": MetricValue(None, stat_src)}, []
    details: list[dict[str, object]] = []
    for dev in sorted(enabled_devices):
        vrate = stat.get(dev, {}).get("cost.vrate")
        fields = qos[dev]
        details.append({
            "device": dev,
            "vrate_pct": vrate,
            "qos_min_pct": _float(fields.get("min")),
            "qos_max_pct": _float(fields.get("max")),
        })
    vrates = [d["vrate_pct"] for d in details if isinstance(d["vrate_pct"], float)]
    # The worst device is the lowest multiplier.
    vrate_metric = MetricValue(min(vrates), "exact") if vrates else MetricValue(None, "unavail_kernel")
    return {"host_io_cost_enabled": enabled, "host_io_cost_vrate_pct": vrate_metric}, details
