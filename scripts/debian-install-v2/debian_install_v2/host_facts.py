"""Best-effort host-facts collection for the initial/final Telegram reports.

Pure information-gathering, never mutates anything. Every individual fact is
wrapped so a missing tool (e.g. `pciutils` not preinstalled on a minimal
image) or an unexpected failure degrades to a placeholder value rather than
raising - this must never be allowed to break a real disk-provisioning
install over something merely informational.
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


_PUBLIC_IP_SERVICE_URL = "https://ifconfig.me/ip"
_UNKNOWN = "unknown"


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:  # best-effort only - never let a fact break the install
        print(f"[WARN] host_facts: {fn.__name__ if hasattr(fn, '__name__') else fn} failed: {exc}", flush=True)
        return default


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_public_ip(timeout: float = 5.0) -> Optional[str]:
    """Best-effort: this host's own public/outbound IPv4, as seen from outside."""
    try:
        req = urllib.request.Request(_PUBLIC_IP_SERVICE_URL, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ip = resp.read().decode("utf-8", errors="replace").strip()
        if ip.count(".") == 3 and all(part.isdigit() for part in ip.split(".")):
            return ip
    except Exception:
        pass
    return None


def _reverse_dns(ip: str) -> Optional[str]:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def _cpu_numa_facts() -> Dict[str, Any]:
    node_dir = Path("/sys/devices/system/node")
    numa_nodes = list(node_dir.glob("node[0-9]*")) if node_dir.is_dir() else []
    return {"cpu_count": os.cpu_count() or 0, "numa_nodes": len(numa_nodes) or 1}


def _memory_facts() -> Dict[str, Any]:
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", _read_text("/proc/meminfo"), re.MULTILINE)
    return {"memory_total_mib": (int(match.group(1)) // 1024) if match else 0}


def _kernel_facts() -> Dict[str, Any]:
    return {
        "kernel_release": platform.release(),
        "kernel_cmdline": _read_text("/proc/cmdline").strip(),
    }


def _existing_users() -> List[str]:
    users: List[str] = []
    for line in _read_text("/etc/passwd").splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        name, uid, shell = parts[0], parts[2], parts[6]
        try:
            if int(uid) >= 1000 and not shell.strip().endswith(("nologin", "/false")):
                users.append(name)
        except ValueError:
            continue
    return users


def _ssh_password_auth_state(installer: Any) -> str:
    if installer.actions.dry_run:
        return f"{_UNKNOWN} (dry-run)"
    output = installer._run(["/usr/sbin/sshd", "-T"], "check effective sshd config")
    match = re.search(r"^passwordauthentication\s+(\S+)", output, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else _UNKNOWN


def _uefi_or_bios() -> str:
    return "UEFI" if Path("/sys/firmware/efi").is_dir() else "BIOS"


def _virtualization(installer: Any) -> str:
    if installer.actions.dry_run:
        return f"{_UNKNOWN} (dry-run)"
    output = installer._run(["/usr/bin/systemd-detect-virt"], "detect virtualization")
    return output or "none"


def _gpu_devices(installer: Any) -> List[str]:
    if installer.actions.dry_run:
        return []
    output = installer._run(["/usr/bin/lspci"], "list PCI devices")
    return [
        line.split(": ", 1)[-1] for line in output.splitlines()
        if any(marker in line for marker in ("VGA compatible controller", "3D controller", "Display controller"))
    ]


def _storage_devices(installer: Any) -> str:
    if installer.actions.dry_run:
        return f"({_UNKNOWN}: dry-run)"
    return installer._run(
        ["/usr/bin/lsblk", "-o", "NAME,SIZE,TYPE,ROTA,MODEL,MOUNTPOINT"], "list block devices"
    )


def _partition_layout(installer: Any) -> str:
    if installer.actions.dry_run:
        return "(dry-run: not executed)"
    return installer._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{installer.root_disk}"], "dump partition table")


def _network_facts(installer: Any) -> Dict[str, Any]:
    if installer.actions.dry_run:
        return {"interfaces": [], "gateway": f"{_UNKNOWN} (dry-run)", "dns_servers": []}
    addr_json = installer._run(["/usr/sbin/ip", "-j", "addr", "show"], "list network interfaces")
    route_json = installer._run(["/usr/sbin/ip", "-j", "route", "show", "default"], "show default route")

    interfaces: List[Dict[str, Any]] = []
    try:
        for iface in json.loads(addr_json or "[]"):
            ips = [a["local"] for a in iface.get("addr_info", []) if a.get("family") == "inet"]
            if ips:
                interfaces.append({"name": iface.get("ifname"), "mac": iface.get("address"), "ips": ips})
    except (ValueError, TypeError, AttributeError):
        pass

    gateway = _UNKNOWN
    try:
        routes = json.loads(route_json or "[]")
        if routes:
            gateway = routes[0].get("gateway", _UNKNOWN)
    except (ValueError, TypeError, AttributeError):
        pass

    dns_servers = re.findall(r"^nameserver\s+(\S+)", _read_text("/etc/resolv.conf"), re.MULTILINE)
    return {"interfaces": interfaces, "gateway": gateway, "dns_servers": dns_servers}


def _existing_swap(installer: Any) -> str:
    if installer.actions.dry_run:
        return "(dry-run: not executed)"
    return installer._run(["/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"], "list active swap")


def _entropy_available() -> Optional[int]:
    value = _read_text("/proc/sys/kernel/random/entropy_avail").strip()
    return int(value) if value.isdigit() else None


def collect_host_facts(installer: Any) -> Dict[str, Any]:
    """Gather a best-effort snapshot of host facts for a Telegram report.

    Safe to call at any point in the install (before or after any step) -
    every fact degrades to a placeholder on failure rather than raising.
    """
    facts: Dict[str, Any] = {}
    facts.update(_safe(_cpu_numa_facts, {"cpu_count": 0, "numa_nodes": 0}))
    facts.update(_safe(_memory_facts, {"memory_total_mib": 0}))
    facts.update(_safe(_kernel_facts, {"kernel_release": _UNKNOWN, "kernel_cmdline": ""}))
    facts["existing_users"] = _safe(_existing_users, [])
    facts["ssh_password_authentication"] = _safe(lambda: _ssh_password_auth_state(installer), _UNKNOWN)
    facts["boot_mode"] = _safe(_uefi_or_bios, _UNKNOWN)
    facts["virtualization"] = _safe(lambda: _virtualization(installer), _UNKNOWN)
    facts["gpu_devices"] = _safe(lambda: _gpu_devices(installer), [])
    facts["storage_devices"] = _safe(lambda: _storage_devices(installer), "")
    facts["partition_layout"] = _safe(lambda: _partition_layout(installer), "")
    facts.update(_safe(lambda: _network_facts(installer), {"interfaces": [], "gateway": _UNKNOWN, "dns_servers": []}))
    facts["existing_swap"] = _safe(lambda: _existing_swap(installer), "")
    facts["entropy_available"] = _safe(_entropy_available, None)

    public_ip = _safe(_detect_public_ip, None)
    facts["public_ip"] = public_ip
    facts["public_ip_reverse_dns"] = _safe(lambda: _reverse_dns(public_ip), None) if public_ip else None

    return facts


def _code(value: Any) -> str:
    text = "" if value is None else str(value)
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<code>{escaped}</code>"


def _pre(value: Any) -> str:
    text = "" if value is None else str(value)
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre>{escaped}</pre>"


def format_facts_html(facts: Dict[str, Any]) -> str:
    """Render collect_host_facts()'s output as an HTML Telegram message body.

    Every copyable value (paths, IPs, device/interface names) is wrapped in
    <code>; multi-line dumps (storage devices, partition table) in <pre> -
    so they're tap-to-copy in Telegram clients.
    """
    lines: List[str] = ["<b>Host facts</b>"]
    lines.append(
        f"CPU: {facts.get('cpu_count')} core(s), {facts.get('numa_nodes')} NUMA node(s) | "
        f"RAM: {facts.get('memory_total_mib')} MiB | Kernel: {_code(facts.get('kernel_release'))}"
    )
    lines.append(f"Boot mode: {facts.get('boot_mode')} | Virtualization: {_code(facts.get('virtualization'))}")

    gpu_devices = facts.get("gpu_devices") or []
    lines.append("GPU: " + (", ".join(_code(d) for d in gpu_devices) if gpu_devices else "none detected"))

    existing_users = facts.get("existing_users") or []
    lines.append(
        "Existing non-system users: "
        + (", ".join(_code(u) for u in existing_users) if existing_users else "none")
    )
    lines.append(f"SSH password authentication: {_code(facts.get('ssh_password_authentication'))}")

    for iface in facts.get("interfaces") or []:
        ips = ", ".join(_code(ip) for ip in iface.get("ips", []))
        lines.append(f"Interface {_code(iface.get('name'))} ({_code(iface.get('mac'))}): {ips}")
    lines.append(f"Gateway: {_code(facts.get('gateway'))}")

    dns_servers = facts.get("dns_servers") or []
    if dns_servers:
        lines.append("DNS: " + ", ".join(_code(d) for d in dns_servers))

    if facts.get("public_ip"):
        if facts.get("public_ip_reverse_dns"):
            lines.append(
                f"Public IP: {_code(facts['public_ip'])} (reverse-DNS: {_code(facts['public_ip_reverse_dns'])})"
            )
        else:
            lines.append(f"Public IP: {_code(facts['public_ip'])} (no reverse DNS)")

    if facts.get("entropy_available") is not None:
        lines.append(f"Entropy available: {facts['entropy_available']}")

    lines.append(f"Existing swap:\n{_pre(facts.get('existing_swap') or '(none)')}")
    lines.append(f"Storage devices:\n{_pre(facts.get('storage_devices'))}")
    lines.append(f"Current partition table:\n{_pre(facts.get('partition_layout'))}")

    return "\n".join(lines)
