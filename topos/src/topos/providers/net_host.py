from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from topos.collect.cgroup import read_text
from topos.model import Entity, EntityKey
from topos.providers.base import NetSample, unavailable_sample

CommandRunner = Callable[[list[str]], str]


def _run_command(argv: list[str]) -> str:
    proc = subprocess.run(argv, check=True, capture_output=True, text=True)
    return proc.stdout


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    interfaces: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, payload = line.partition(":")
        fields = payload.split()
        if len(fields) < 16:
            continue
        try:
            values = [int(field) for field in fields[:16]]
        except ValueError:
            continue
        interfaces[name.strip()] = {
            "rx_bytes": values[0],
            "rx_pkts": values[1],
            "rx_errs": values[2],
            "rx_drop": values[3],
            "tx_bytes": values[8],
            "tx_pkts": values[9],
            "tx_errs": values[10],
            "tx_drop": values[11],
        }
    return interfaces


def parse_softnet_stat(text: str) -> dict[str, int]:
    dropped = 0
    time_squeeze = 0
    cpu_count = 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            dropped += int(fields[1], 16)
            time_squeeze += int(fields[2], 16)
        except ValueError:
            continue
        cpu_count += 1
    return {
        "cpu_count": cpu_count,
        "dropped": dropped,
        "time_squeeze": time_squeeze,
    }


def parse_snmp_like(text: str) -> dict[str, dict[str, int]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: dict[str, dict[str, int]] = {}
    for idx in range(0, len(lines) - 1, 2):
        header = lines[idx].split()
        values = lines[idx + 1].split()
        if len(header) < 2 or len(values) < 2 or header[0] != values[0]:
            continue
        proto = header[0].rstrip(":")
        keys = header[1:]
        raw_values = values[1:]
        parsed: dict[str, int] = {}
        for key, raw in zip(keys, raw_values, strict=False):
            try:
                parsed[key] = int(raw)
            except ValueError:
                continue
        out[proto] = parsed
    return out


def parse_qdisc_stats(text: str) -> dict[str, dict[str, int]]:
    qdisc: dict[str, dict[str, int]] = {}
    current_dev: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("qdisc "):
            parts = stripped.split()
            try:
                dev = parts[parts.index("dev") + 1]
            except (ValueError, IndexError):
                current_dev = None
                continue
            current_dev = dev
            qdisc.setdefault(current_dev, {})
            continue
        if current_dev is None:
            continue
        dropped = re.search(r"dropped (\d+)", stripped)
        overlimits = re.search(r"overlimits (\d+)", stripped)
        backlog = re.search(r"backlog (\d+)b (\d+)p", stripped)
        if dropped:
            qdisc[current_dev]["dropped"] = int(dropped.group(1))
        if overlimits:
            qdisc[current_dev]["overlimits"] = int(overlimits.group(1))
        if backlog:
            qdisc[current_dev]["backlog_bytes"] = int(backlog.group(1))
            qdisc[current_dev]["backlog_packets"] = int(backlog.group(2))
    return qdisc


class NetHostProvider:
    name = "net_host"

    def __init__(self, proc_root: Path = Path("/proc"), command_runner: CommandRunner | None = None) -> None:
        self.proc_root = proc_root
        self.command_runner = command_runner or _run_command
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
        }
        if "" not in entities:
            return {}
        dev_result = read_text(self.proc_root / "net" / "dev")
        if dev_result.value is None:
            self._status["errors"].append(f"/proc/net/dev:{dev_result.src}")
            return {"": unavailable_sample("missing /proc/net/dev", source_label="net:HOST", confidence="n/a")}
        interfaces = parse_net_dev(str(dev_result.value))
        softnet = self._read_softnet()
        snmp = self._read_snmp()
        netstat = self._read_netstat()
        qdisc = self._read_qdisc()
        total = {
            "rx_bytes": sum(values["rx_bytes"] for values in interfaces.values()),
            "tx_bytes": sum(values["tx_bytes"] for values in interfaces.values()),
            "rx_pkts": sum(values["rx_pkts"] for values in interfaces.values()),
            "tx_pkts": sum(values["tx_pkts"] for values in interfaces.values()),
        }
        proto = {
            "tcp": {
                "retrans_segs": snmp.get("Tcp", {}).get("RetransSegs"),
                "out_rsts": snmp.get("Tcp", {}).get("OutRsts"),
                "timeouts": netstat.get("TcpExt", {}).get("TCPTimeouts"),
                "syn_retrans": netstat.get("TcpExt", {}).get("TCPSynRetrans"),
            },
            "udp": {
                "in_errors": snmp.get("Udp", {}).get("InErrors"),
                "rcvbuf_errors": snmp.get("Udp", {}).get("RcvbufErrors"),
                "sndbuf_errors": snmp.get("Udp", {}).get("SndbufErrors"),
            },
        }
        self._status.update(
            {
                "interfaces": interfaces,
                "softnet": softnet,
                "protocols": proto,
                "qdisc": qdisc,
            }
        )
        sample = NetSample(
            rx_bytes=total["rx_bytes"],
            tx_bytes=total["tx_bytes"],
            rx_pkts=total["rx_pkts"],
            tx_pkts=total["tx_pkts"],
            proto=proto,
            source_label="net:HOST",
            confidence="exact",
            aggregation="exact",
            unavailable_reason=None,
        )
        return {"": sample}

    def status(self) -> dict:
        return dict(self._status)

    def _read_softnet(self) -> dict[str, int] | None:
        result = read_text(self.proc_root / "net" / "softnet_stat")
        if result.value is None:
            self._status["errors"].append(f"/proc/net/softnet_stat:{result.src}")
            return None
        return parse_softnet_stat(str(result.value))

    def _read_snmp(self) -> dict[str, dict[str, int]]:
        result = read_text(self.proc_root / "net" / "snmp")
        if result.value is None:
            self._status["errors"].append(f"/proc/net/snmp:{result.src}")
            return {}
        return parse_snmp_like(str(result.value))

    def _read_netstat(self) -> dict[str, dict[str, int]]:
        result = read_text(self.proc_root / "net" / "netstat")
        if result.value is None:
            self._status["errors"].append(f"/proc/net/netstat:{result.src}")
            return {}
        return parse_snmp_like(str(result.value))

    def _read_qdisc(self) -> dict[str, dict[str, int]] | None:
        if shutil.which("tc") is None and self.command_runner is _run_command:
            return None
        try:
            output = self.command_runner(["tc", "-s", "qdisc", "show"])
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._status["errors"].append(f"tc:{exc.__class__.__name__}")
            return None
        return parse_qdisc_stats(output)
