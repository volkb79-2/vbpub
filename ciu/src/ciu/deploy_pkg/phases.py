"""
CIU v2 deploy_pkg — phase ordering and service traversal.

Implements S7.1 (phase naming + numeric order) and S7.2 (enabled flag semantics).
"""
from __future__ import annotations

import re
from typing import Iterator

# S7.1: the only accepted key pattern under [deploy.phases]
PHASE_KEY_RE: re.Pattern[str] = re.compile(r"^phase_(\d+)$")


# ---------------------------------------------------------------------------
# S7.1 — ordered_phases
# ---------------------------------------------------------------------------

def ordered_phases(phases_cfg: dict) -> list[tuple[int, str, dict]]:
    """Return phases sorted by their numeric suffix.

    Each returned tuple is (phase_num: int, phase_key: str, phase_dict: dict).

    Rules (S7.1):
    - Every key MUST be a string matching ``^phase_(\\d+)$``.
    - Non-string keys or keys that do not match the pattern → ValueError [S7.1].
    - Sorting is NUMERIC, so phase_2 < phase_10 (fixes v1's lexicographic bug).
    """
    result: list[tuple[int, str, dict]] = []
    for key, val in phases_cfg.items():
        if not isinstance(key, str):
            raise ValueError(
                f"[S7.1] Phase key {key!r} is not a string. "
                "All keys under [deploy.phases] must be strings matching phase_<uint> "
                "(e.g. phase_1, phase_2, phase_10)."
            )
        m = PHASE_KEY_RE.match(key)
        if m is None:
            raise ValueError(
                f"[S7.1] Invalid phase key {key!r}. "
                "All keys under [deploy.phases] must match phase_<uint> "
                "(e.g. phase_1, phase_2, phase_10)."
            )
        phase_num = int(m.group(1))
        result.append((phase_num, key, val))
    result.sort(key=lambda t: t[0])
    return result


# ---------------------------------------------------------------------------
# S7.2 — service_enabled
# ---------------------------------------------------------------------------

def service_enabled(service: dict, control: dict) -> bool:
    """Evaluate the 'enabled' field of a service dict (S7.2).

    - Absent → True.
    - bool   → itself.
    - str    → key in control; control[key] must be bool → that value.
    - Any other type (int, list, …) → ValueError [S7.2].
    - Unknown flag name or non-bool control value → ValueError [S7.2].
    - Expressions are forbidden (v1 eval() is withdrawn).
    """
    raw = service.get("enabled", True)

    if isinstance(raw, bool):
        return raw

    if isinstance(raw, str):
        flag = raw
        if flag not in control:
            available = ", ".join(sorted(control.keys())) if control else "(none)"
            raise ValueError(
                f"[S7.2] Unknown control flag '{flag}' in service 'enabled'. "
                f"Available flags in [deploy.control]: {available}."
            )
        value = control[flag]
        if not isinstance(value, bool):
            raise ValueError(
                f"[S7.2] Control flag '{flag}' has non-bool value {value!r}. "
                "All [deploy.control] values used as enabled flags must be bool."
            )
        return value

    # int, list, dict, or anything else: expressions forbidden
    raise ValueError(
        f"[S7.2] 'enabled' must be a bool or a control-flag name (string); "
        f"got {type(raw).__name__} {raw!r}. Expressions are forbidden in v2."
    )


# ---------------------------------------------------------------------------
# S7.1/S7.2 — iter_enabled_services
# ---------------------------------------------------------------------------

def iter_enabled_services(
    phases_cfg: dict,
    control: dict,
    phase_filter: set[str] | None = None,
) -> Iterator[tuple[int, str, dict]]:
    """Yield (phase_num, phase_key, service_dict) for every enabled, path-bearing service.

    Processing order is numeric (S7.1).  phase_filter, when given, restricts
    to the named phase keys.  Services with an empty or missing 'path' are
    silently skipped.  'enabled' is evaluated per S7.2 (ValueError propagates).
    """
    for phase_num, phase_key, phase_data in ordered_phases(phases_cfg):
        if phase_filter is not None and phase_key not in phase_filter:
            continue
        for svc in phase_data.get("services", []):
            if not service_enabled(svc, control):
                continue
            path = svc.get("path", "")
            if not path:
                continue
            yield phase_num, phase_key, svc


# ---------------------------------------------------------------------------
# env_overrides parsing
# ---------------------------------------------------------------------------

def parse_env_overrides(items: list[str]) -> dict:
    """Parse a list of 'KEY=VALUE' strings into a dict.

    Each entry must contain '='.  The value may itself contain '=' characters
    (split on the first '=' only).  Entry without '=' → ValueError.
    """
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(
                f"env_override entry {item!r} is missing '='. "
                "Expected format: KEY=VALUE."
            )
        key, value = item.split("=", 1)
        result[key] = value
    return result
