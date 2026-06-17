"""
CIU v2 deploy_pkg — health gate and container status classification.

Implements S7.7 (health gate semantics) and S7.8 (anchored name filter).
"""
from __future__ import annotations

import time
from typing import Callable

# ---------------------------------------------------------------------------
# S7.7 — classify
# ---------------------------------------------------------------------------

# Docker inspect .State dict shapes we recognise:
#   .State.Health.Status  → 'healthy' | 'unhealthy' | 'starting'
#   No .State.Health key  → no healthcheck configured

_CLASSIFY_MAP: dict[str, str] = {
    "healthy": "healthy",
    "unhealthy": "unhealthy",
    "starting": "starting",
}


def classify(inspect_state: dict | None) -> str:
    """Classify a container's health from its Docker inspect .State dict.

    Returns one of:
        'healthy'        — healthcheck passing
        'starting'       — healthcheck present but not yet passing (S7.7: NOT passed)
        'unhealthy'      — healthcheck failing
        'no-healthcheck' — container has no healthcheck configured
        'not-found'      — inspect_state is None (container not found)
    """
    if inspect_state is None:
        return "not-found"

    health = inspect_state.get("Health")
    if health is None:
        # No Health key → no healthcheck defined
        return "no-healthcheck"

    status = health.get("Status", "")
    return _CLASSIFY_MAP.get(status, "unhealthy")


# ---------------------------------------------------------------------------
# S7.7 — evaluate_gate
# ---------------------------------------------------------------------------

def evaluate_gate(
    statuses: dict[str, str],
) -> tuple[bool, dict[str, list[str]]]:
    """Evaluate the health gate for a set of container statuses.

    The gate passes iff every status is 'healthy' or 'no-healthcheck'.
    'starting' goes to the 'pending' bucket and causes the gate to FAIL.

    Returns:
        (passed: bool, summary: dict)

    summary buckets (lists of container names):
        'healthy'        — passed health check
        'pending'        — starting / not yet healthy
        'unhealthy'      — health check failing
        'no_healthcheck' — no healthcheck configured (warning, not failing)
        'not_found'      — container not found
    """
    summary: dict[str, list[str]] = {
        "healthy": [],
        "pending": [],
        "unhealthy": [],
        "no_healthcheck": [],
        "not_found": [],
    }

    for name, status in statuses.items():
        if status == "healthy":
            summary["healthy"].append(name)
        elif status == "no-healthcheck":
            summary["no_healthcheck"].append(name)
        elif status == "starting":
            summary["pending"].append(name)
        elif status == "not-found":
            summary["not_found"].append(name)
        else:
            # 'unhealthy' or any unknown status
            summary["unhealthy"].append(name)

    passed = (
        len(summary["pending"]) == 0
        and len(summary["unhealthy"]) == 0
        and len(summary["not_found"]) == 0
    )
    return passed, summary


# ---------------------------------------------------------------------------
# S7.7 — wait_for_gate
# ---------------------------------------------------------------------------

def wait_for_gate(
    check_fn: Callable[[], dict[str, str]],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, dict]:
    """Poll *check_fn* until the health gate passes or *timeout_s* elapses.

    *sleep_fn* and *clock* are injectable for deterministic tests.

    Returns the final evaluate_gate result: (passed, summary).
    """
    deadline = clock() + timeout_s
    passed, summary = evaluate_gate(check_fn())
    while not passed and clock() < deadline:
        sleep_fn(interval_s)
        passed, summary = evaluate_gate(check_fn())
    return passed, summary


# ---------------------------------------------------------------------------
# S7.8 — anchored_name_filter
# ---------------------------------------------------------------------------

def anchored_name_filter(project: str, env_tag: str, name: str) -> str:
    """Return an anchored regex suitable for docker --filter name=.

    S7.8: must not use substring matches; the pattern anchors both ends.

    Example:
        anchored_name_filter("myproj", "prod", "redis") == "^myproj-prod-redis$"
    """
    return f"^{project}-{env_tag}-{name}$"


# ---------------------------------------------------------------------------
# Healthcheck-probe validation (ciu health --preflight)
# ---------------------------------------------------------------------------

import re as _re
import subprocess as _subprocess
from pathlib import Path as _Path

_SHELL_KEYWORDS: frozenset[str] = frozenset({
    "sh", "bash", "dash", "ash", "zsh", "ksh",
    "if", "then", "else", "elif", "fi", "while", "do", "done",
    "until", "for", "in", "case", "esac",
    "echo", "printf", "test", "exit", "set", "export", "unset",
    "cd", "source", ".", ":", "read", "exec",
    "[", "[[", "]]", "command", "type", "which",
    "true", "false",
})

_SHELL_OP_RE: _re.Pattern[str] = _re.compile(r"&&|\|\||\||;")


def _parse_cmd_shell_tools(cmd_str: str) -> list[str]:
    """Extract external tool names from a CMD-SHELL healthcheck string.

    Splits on shell operators (&&, ||, |, ;) then takes the first non-flag,
    non-builtin, non-assignment token of each segment as the command name.
    """
    tools: list[str] = []
    for segment in _SHELL_OP_RE.split(cmd_str):
        tokens = segment.strip().split()
        for token in tokens:
            if token.startswith("-"):
                break  # Hit a flag; no command in this segment position
            if "=" in token and not token.startswith("/"):
                continue  # VAR=value assignment — skip
            word = token.rsplit("/", 1)[-1]  # take basename of any /path/tool
            if word and word not in _SHELL_KEYWORDS:
                tools.append(word)
                break
    return tools


def extract_healthcheck_tools(compose_path: _Path) -> dict[str, tuple[str, list[str]]]:
    """Parse a rendered compose file and return {service: (image, [tools])}.

    Only services with CMD or CMD-SHELL healthchecks and a resolved image are
    included. Returns an empty dict when the file cannot be parsed.
    """
    try:
        import yaml  # PyYAML — already a CIU dependency
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    result: dict[str, tuple[str, list[str]]] = {}
    for svc_name, svc in (compose or {}).get("services", {}).items():
        hc = svc.get("healthcheck", {})
        test = hc.get("test", [])
        image = svc.get("image", "")
        if not test or not image:
            continue
        if isinstance(test, str):
            tools = _parse_cmd_shell_tools(test)
        elif isinstance(test, list):
            if not test or test[0] == "NONE":
                continue
            elif test[0] == "CMD" and len(test) >= 2:
                basename = test[1].rsplit("/", 1)[-1]
                tools = [basename] if basename not in _SHELL_KEYWORDS else []
            elif test[0] == "CMD-SHELL" and len(test) >= 2:
                tools = _parse_cmd_shell_tools(test[1])
            else:
                continue
        else:
            continue
        if tools:
            result[svc_name] = (image, tools)
    return result


def probe_image_tools(image: str, tools: list[str], *, timeout_s: float = 20.0) -> dict[str, bool]:
    """Check which tools exist in a Docker image. Returns {tool: available}.

    Uses ``docker run --rm --entrypoint "" <image> sh -c "command -v <tool>"``.
    Returns False for a tool when Docker is unavailable or the probe times out.
    """
    available: dict[str, bool] = {}
    for tool in tools:
        try:
            result = _subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--entrypoint", "",
                    image,
                    "sh", "-c", f"command -v {tool}",
                ],
                capture_output=True,
                timeout=timeout_s,
            )
            available[tool] = result.returncode == 0
        except (FileNotFoundError, _subprocess.TimeoutExpired):
            available[tool] = False
    return available


def preflight_probe(
    compose_paths: list[_Path],
    *,
    warn_fn=None,
    info_fn=None,
) -> list[str]:
    """Probe rendered compose files for healthchecks referencing missing tools.

    Returns a list of warning strings (empty = all probes passed).
    Caches image probes so the same image is only pulled/run once.
    """
    if warn_fn is None:
        warn_fn = print
    if info_fn is None:
        info_fn = print

    warnings: list[str] = []
    image_cache: dict[str, dict[str, bool]] = {}  # image → {tool: available}

    for compose_path in compose_paths:
        if not compose_path.exists():
            continue
        svc_tools = extract_healthcheck_tools(compose_path)
        if not svc_tools:
            info_fn(f"  {compose_path}: no CMD/CMD-SHELL healthchecks found")
            continue
        for svc_name, (image, tools) in svc_tools.items():
            info_fn(f"  probing {svc_name!r} ({image}) — tools: {', '.join(tools)}")
            cached = image_cache.setdefault(image, {})
            need_probe = [t for t in tools if t not in cached]
            if need_probe:
                cached.update(probe_image_tools(image, need_probe))
            availability = {t: cached.get(t, False) for t in tools}
            missing = [t for t, ok in availability.items() if not ok]
            present = [t for t, ok in availability.items() if ok]
            if missing:
                present_str = ", ".join(present) if present else "none found"
                msg = (
                    f"[PREFLIGHT] {svc_name}: healthcheck uses {missing} "
                    f"but image {image!r} lacks them (available: {present_str})"
                )
                warnings.append(msg)
                warn_fn(msg)

    return warnings
