"""Read-only Docker diagnostics for common CIU stack failures."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    severity: str
    container: str
    code: str
    summary: str
    remedy: str


_LOG_RULES = (
    (re.compile(r"no permissions to access a channel", re.I), "redis_channel_acl", "Redis denied Pub/Sub channel access", "Grant an ACL channel pattern (&channel), not only a key pattern (~key)."),
    (re.compile(r"out of memory|oom.?kill|cannot allocate memory", re.I), "memory_exhaustion", "Log indicates memory exhaustion", "Inspect the container and parent-cgroup limits, then size memory and swap from observed peak usage."),
    (re.compile(r"no space left on device", re.I), "disk_full", "Log indicates exhausted storage", "Check filesystem, inode, Docker layer, and volume usage before retrying."),
    (re.compile(r"segmentation fault|segfault", re.I), "segfault", "Log indicates a native crash", "Collect the image version and crash context; restart only after preserving evidence."),
)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def _inspect(project: str | None) -> list[dict[str, Any]]:
    # Compose's canonical project label survives even when a service-specific
    # `labels:` mapping replaces a CIU template anchor's generic labels.
    argv = ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"]
    if project:
        argv.extend(["--filter", f"label=com.docker.compose.project={project}"])
    listed = _run(argv)
    if listed.returncode:
        raise RuntimeError(listed.stderr.strip() or "docker ps failed")
    ids = listed.stdout.split()
    if not ids:
        return []
    inspected = _run(["docker", "inspect", *ids])
    if inspected.returncode:
        raise RuntimeError(inspected.stderr.strip() or "docker inspect failed")
    value = json.loads(inspected.stdout)
    return value if isinstance(value, list) else []


def collect(*, project: str | None = None, log_lines: int = 100) -> list[Finding]:
    findings: list[Finding] = []
    for item in _inspect(project):
        name = str(item.get("Name", "unknown")).lstrip("/")
        state = item.get("State", {})
        host = item.get("HostConfig", {})
        status = str(state.get("Status", "unknown"))
        exit_code = int(state.get("ExitCode", 0) or 0)
        if state.get("OOMKilled"):
            findings.append(Finding("error", name, "oom_killed", "Docker recorded an OOM kill", "Raise or rebalance the container/parent memory limit and inspect memory.events before restarting."))
        elif exit_code == 137 and status != "running":
            findings.append(Finding("warning", name, "exit_137", "Exited with SIGKILL (137); OOM is a common cause", "Correlate with host and cgroup memory events; Docker's OOMKilled flag is not conclusive in every kill path."))
        if status in {"dead", "restarting", "removing"} or (status == "exited" and exit_code):
            findings.append(Finding("error", name, "bad_state", f"Container state is {status}, exit={exit_code}", "Inspect its bounded recent logs and health history."))
        health = state.get("Health") or {}
        if health.get("Status") == "unhealthy":
            history = health.get("Log") or []
            detail = str(history[-1].get("Output", "")).strip()[:240] if history else ""
            findings.append(Finding("error", name, "unhealthy", f"Healthcheck is unhealthy{': ' + detail if detail else ''}", "Run the health command manually once and verify its interval, timeout, and command syntax."))
        restarts = int(item.get("RestartCount", 0) or 0)
        if restarts:
            findings.append(Finding("warning", name, "restarted", f"Container has restarted {restarts} time(s)", "Correlate StartedAt/FinishedAt with OOM, health, and bounded logs."))
        memory = int(host.get("Memory", 0) or 0)
        memory_swap = int(host.get("MemorySwap", 0) or 0)
        if memory and memory_swap == memory:
            findings.append(Finding("info", name, "swap_disabled", "Container memory+swap limit equals its RAM limit", "Increase memswap_limit if this workload should be allowed to page."))

        logs = _run(["docker", "logs", "--tail", str(log_lines), name])
        text = f"{logs.stdout}\n{logs.stderr}"
        for regex, code, summary, remedy in _LOG_RULES:
            if regex.search(text):
                findings.append(Finding("error", name, code, summary, remedy))
    return findings


def run(*, project: str | None, log_lines: int, json_output: bool) -> int:
    try:
        findings = collect(project=project, log_lines=log_lines)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] diagnose failed: {exc}")
        return 2
    if json_output:
        print(json.dumps([asdict(item) for item in findings], indent=2))
    elif not findings:
        print("[OK] No common container failure signatures found.")
    else:
        for item in findings:
            print(f"[{item.severity.upper()}] {item.container}: {item.summary} ({item.code})")
            print(f"  remedy: {item.remedy}")
    return 1 if any(item.severity == "error" for item in findings) else 0
