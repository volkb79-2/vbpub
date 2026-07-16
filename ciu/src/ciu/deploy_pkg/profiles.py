"""
CIU v3 deploy_pkg — service profile resolution.

Implements S7.4 (profile table), S7.5 (CIU_SERVICES_PROFILE, groups rejection),
and the S7.5a multi-host topology_overrides flow.

Breaking change (v3): CIU_HOST_PROFILE is RETIRED (not aliased). The env var is
now CIU_SERVICES_PROFILE (comma-separated ordered list). The --profile CLI flag
is now repeatable and accepts comma-separated values.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

from ..config_model import deep_merge
from .phases import PHASE_KEY_RE


# ---------------------------------------------------------------------------
# Profile dataclass
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    """Resolved service profile (composite of one or more named profiles).

    Attributes:
        name:              Profile name(s) as a comma-joined string, or None
                           for the default (all phases).
        phase_keys:        Set of phase_<n> keys to deploy, or None = all.
        extra_stacks:      Additional stack paths from profile.stacks (ordered,
                           deduped across all selected profiles).
        compose_profiles:  Compose profile names (→ COMPOSE_PROFILES); ordered
                           and deduped across all selected profiles.
        env_overrides:     Extra env key/value pairs; deep-merged with conflict
                           detection across all selected profiles.
        config:            Global config with topology_overrides from all
                           selected profiles deep-merged in (deep copy; input
                           is never mutated).
    """
    name: str | None = None
    phase_keys: set[str] | None = None
    extra_stacks: list[str] = field(default_factory=list)
    compose_profiles: list[str] = field(default_factory=list)
    env_overrides: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# S7.5 — reject_groups
# ---------------------------------------------------------------------------

def reject_groups(global_cfg: dict) -> None:
    """Abort with [S7.5] if [deploy.groups] is present in global_cfg.

    Called by the CLI / engine before any profile resolution so the operator
    gets an immediate, actionable error message.
    """
    if "groups" in global_cfg.get("deploy", {}):
        raise ValueError(
            "[S7.5] deploy.groups was removed in v2; "
            "define [deploy.profiles.<name>] instead."
        )


# ---------------------------------------------------------------------------
# Helper: order-preserving deduplicate
# ---------------------------------------------------------------------------

def dedupe_keep_order(items) -> list:
    """Return a new list with duplicates removed, preserving first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Helper: deep-merge with conflict detection
# ---------------------------------------------------------------------------

def _check_conflicts_recursive(base: dict, override: dict, path: str,
                                profile_a: str, profile_b: str) -> None:
    """Walk two dicts and raise ValueError on any differing leaf value.

    Equal repeated values are accepted silently (spec: 'Equal repeated values
    are accepted').
    """
    for key, new_val in override.items():
        cur_path = f"{path}.{key}" if path else key
        if key not in base:
            continue  # new key — no conflict
        existing = base[key]
        if isinstance(existing, dict) and isinstance(new_val, dict):
            _check_conflicts_recursive(existing, new_val, cur_path, profile_a, profile_b)
        elif existing != new_val:
            raise ValueError(
                f"[Seam4] Conflict in key '{cur_path}': "
                f"profile '{profile_a}' set it to {existing!r}, "
                f"profile '{profile_b}' set it to {new_val!r}. "
                f"Resolve the conflict in the profile configuration."
            )


def deep_merge_strict(
    accumulated: dict,
    override: dict,
    accumulated_profile: str,
    new_profile: str,
    path: str = "",
) -> dict:
    """Deep-merge *override* into *accumulated*, raising on conflicts.

    A conflict is when the same leaf key is set to two different values
    (equal values are accepted silently).
    """
    _check_conflicts_recursive(accumulated, override, path, accumulated_profile, new_profile)
    return deep_merge(accumulated, override)


# ---------------------------------------------------------------------------
# Private: resolve a single named profile from the profile table
# ---------------------------------------------------------------------------

def _resolve_one(
    global_cfg: dict,
    name: str,
) -> tuple[str | None, set[str] | None, list[str], list[str], dict, dict | None]:
    """Resolve one profile name from global_cfg.

    Returns (name, phase_keys, extra_stacks, compose_profiles, env_overrides,
             topology_overrides_raw).

    Raises ValueError for unknown/invalid profiles.
    """
    deploy_cfg = global_cfg.get("deploy", {})
    profiles_table = deploy_cfg.get("profiles", {})

    if name not in profiles_table:
        available = ", ".join(sorted(profiles_table.keys())) if profiles_table else "(none)"
        raise ValueError(
            f"Unknown profile '{name}'. "
            f"Available profiles: {available}."
        )

    pdata: dict = profiles_table[name]

    # --- phases ---
    phase_keys: set[str] | None = None
    if "phases" in pdata:
        raw_phases = pdata["phases"]
        if not isinstance(raw_phases, list):
            raise ValueError(
                f"[S7.1] Profile '{name}': 'phases' must be a list of phase_<uint> strings."
            )
        validated: set[str] = set()
        for pk in raw_phases:
            if not isinstance(pk, str) or not PHASE_KEY_RE.match(pk):
                raise ValueError(
                    f"[S7.1] Profile '{name}': invalid phase key '{pk}'. "
                    "Must match phase_<uint> (e.g. phase_1, phase_2)."
                )
            validated.add(pk)
        phase_keys = validated

    # --- stacks ---
    extra_stacks: list[str] = list(pdata.get("stacks", []))

    # S7.5 narrowing: a stacks-only profile (no 'phases' key, 'stacks' given)
    # contributes NO phases — selecting it deploys just its stacks. Only a
    # profile with NEITHER key (a pure env/topology override profile) keeps
    # the "all phases" meaning. This matches S7.5a's multi-host example
    # (`--profile core,db` deploys core+db, not the whole phase set).
    if phase_keys is None and extra_stacks:
        phase_keys = set()

    # --- compose_profiles ---
    compose_profiles: list[str] = list(pdata.get("compose_profiles", []))

    # --- env_overrides ---
    raw_env = pdata.get("env_overrides", {})
    if not isinstance(raw_env, dict):
        raise ValueError(
            f"Profile '{name}': 'env_overrides' must be a dict."
        )
    env_overrides: dict = dict(raw_env)

    # --- topology_overrides (raw) ---
    topo_override: dict | None = None
    if "topology_overrides" in pdata:
        topo_override = pdata["topology_overrides"]
        if not isinstance(topo_override, dict):
            raise ValueError(
                f"Profile '{name}': 'topology_overrides' must be a dict."
            )

    return (name, phase_keys, extra_stacks, compose_profiles, env_overrides, topo_override)


# ---------------------------------------------------------------------------
# S7.4 / S7.5 — resolve_profiles (primary API)
# ---------------------------------------------------------------------------

def resolve_profiles(
    global_cfg: dict,
    names: list[str] | None,
    env: dict | None = None,
) -> Profile:
    """Resolve a composite profile from an ordered list of profile names.

    Resolution order (Seam 4):
    1. *names* argument (from --profile CLI flags).
    2. CIU_SERVICES_PROFILE in *env* (comma-split; defaults to os.environ).
    3. Default profile: all phases, no overrides.

    CIU_HOST_PROFILE is RETIRED: if set it is ignored and a deprecation error
    is emitted to stderr so operators with stale environments notice.

    Composition rules (union, order-preserving, deduped):
    - phase_keys: union of sets; None (= all phases) absorbs everything.
    - extra_stacks, compose_profiles: order-preserving dedup across profiles.
    - env_overrides, topology_overrides: deep-merged with conflict detection.
      Two profiles setting the same key to DIFFERENT values → ValueError (exit 2).
      Two profiles setting the same key to the SAME value → accepted silently.

    Profile-table shape is unchanged ([deploy.profiles.<name>] with
    phases/stacks/compose_profiles/env_overrides/topology_overrides).
    """
    if env is None:
        env = os.environ

    # Detect retired CIU_HOST_PROFILE and warn the operator.
    if "CIU_HOST_PROFILE" in env:
        import sys
        print(
            "[DEPRECATED] CIU_HOST_PROFILE is retired and is NOT used as a fallback. "
            "Set CIU_SERVICES_PROFILE instead (comma-separated profile list). "
            "Unset CIU_HOST_PROFILE to suppress this error.",
            file=sys.stderr,
            flush=True,
        )
        raise ValueError(
            "[Seam4] CIU_HOST_PROFILE is retired. "
            "Use CIU_SERVICES_PROFILE=<name>[,<name>...] instead."
        )

    # Resolve the name list
    if not names:
        raw_env = env.get("CIU_SERVICES_PROFILE", "").strip()
        if raw_env:
            names = [n.strip() for n in raw_env.split(",") if n.strip()]

    # Default profile (no names)
    if not names:
        return Profile(
            name=None,
            phase_keys=None,
            config=global_cfg,
        )

    # Resolve each name individually
    resolved = [_resolve_one(global_cfg, n) for n in names]

    # --- Compose: phase_keys (union, None = all absorbs) ---
    combined_phase_keys: set[str] | None = set()
    for (rname, phase_keys, *_rest) in resolved:
        if phase_keys is None:
            # This profile means "all phases" → the union also means all phases
            combined_phase_keys = None
            break
        assert combined_phase_keys is not None
        combined_phase_keys.update(phase_keys)
    # S7.5 narrowing: an empty union stays EMPTY (stacks-only profiles deploy
    # only their stacks). The former empty→all coercion made stacks-only
    # profiles deploy every phase PLUS their stacks — the destructive surprise
    # behind the 2026-07-16 dstdns multi-stack incident.

    # --- Compose: extra_stacks + compose_profiles (order-preserving dedup) ---
    all_stacks: list[str] = []
    all_compose_profiles: list[str] = []
    for (rname, _pk, extra_stacks, compose_profiles, _env, _topo) in resolved:
        all_stacks.extend(extra_stacks)
        all_compose_profiles.extend(compose_profiles)
    combined_stacks = dedupe_keep_order(all_stacks)
    combined_compose_profiles = dedupe_keep_order(all_compose_profiles)

    # --- Compose: env_overrides (deep-merge with conflict detection) ---
    combined_env: dict = {}
    accumulated_env_name: str = ""
    for (rname, _pk, _stacks, _cp, env_overrides, _topo) in resolved:
        if combined_env:
            combined_env = deep_merge_strict(
                combined_env, env_overrides,
                accumulated_env_name, rname,
                path="env_overrides",
            )
        else:
            combined_env = dict(env_overrides)
        accumulated_env_name = f"{accumulated_env_name},{rname}" if accumulated_env_name else rname

    # --- Compose: topology_overrides (deep-merge with conflict detection) ---
    # Start from a deep copy of global_cfg so the caller is never mutated.
    merged_config = copy.deepcopy(global_cfg)
    accumulated_topo: dict = {}
    accumulated_topo_name: str = ""
    for (rname, _pk, _stacks, _cp, _env, topo_override) in resolved:
        if topo_override is None:
            continue
        if accumulated_topo:
            accumulated_topo = deep_merge_strict(
                accumulated_topo, topo_override,
                accumulated_topo_name, rname,
                path="topology_overrides",
            )
        else:
            accumulated_topo = copy.deepcopy(topo_override)
        accumulated_topo_name = (
            f"{accumulated_topo_name},{rname}" if accumulated_topo_name else rname
        )
    if accumulated_topo:
        existing_topo = merged_config.get("topology", {})
        merged_config["topology"] = deep_merge(existing_topo, accumulated_topo)

    # --- Compose: name label ---
    composite_name = ",".join(names)

    return Profile(
        name=composite_name,
        phase_keys=combined_phase_keys,
        extra_stacks=combined_stacks,
        compose_profiles=combined_compose_profiles,
        env_overrides=combined_env,
        config=merged_config,
    )


# ---------------------------------------------------------------------------
# Legacy single-name shim (kept for dev.py internal call; callers should migrate)
# ---------------------------------------------------------------------------

def resolve_profile(
    global_cfg: dict,
    name: str | None,
    env: dict | None = None,
) -> Profile:
    """Thin shim over resolve_profiles for a single profile name.

    NOTE: this still reads CIU_SERVICES_PROFILE from env (not CIU_HOST_PROFILE).
    The *name* argument, if given, is passed as a single-element list.
    """
    names: list[str] | None = [name] if name else None
    return resolve_profiles(global_cfg, names, env=env)
