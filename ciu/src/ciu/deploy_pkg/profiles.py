"""
CIU v2 deploy_pkg — host profile resolution.

Implements S7.4 (profile table), S7.5 (CIU_HOST_PROFILE, groups rejection),
and the S7.5a multi-host topology_overrides flow.
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
    """Resolved host profile.

    Attributes:
        name:              Profile name, or None for the default (all phases).
        phase_keys:        Set of phase_<n> keys to deploy, or None = all.
        extra_stacks:      Additional stack paths from profile.stacks.
        compose_profiles:  Compose profile names (→ COMPOSE_PROFILES).
        env_overrides:     Extra env key/value pairs for this profile.
        config:            Global config with topology_overrides deep-merged in
                           when the profile specifies them (deep copy; input
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
# S7.4 / S7.5 — resolve_profile
# ---------------------------------------------------------------------------

def resolve_profile(
    global_cfg: dict,
    name: str | None,
    env: dict | None = None,
) -> Profile:
    """Resolve a host profile from *global_cfg*.

    Resolution order (S7.5):
    1. *name* argument (from --profile CLI flag).
    2. CIU_HOST_PROFILE in *env* (defaults to os.environ when env=None).
    3. Default profile: all phases, no overrides.

    Profile table lives at global_cfg['deploy']['profiles'][<name>] (S7.4).
    Recognised keys: phases, stacks, compose_profiles, env_overrides,
    topology_overrides.

    topology_overrides is deep-merged over a **deep copy** of
    global_cfg['topology'] so the caller's config is never mutated.
    """
    if env is None:
        env = os.environ

    # Resolve name
    if name is None:
        name = env.get("CIU_HOST_PROFILE") or None

    deploy_cfg = global_cfg.get("deploy", {})
    profiles_table = deploy_cfg.get("profiles", {})

    # Default profile (no name, or name resolves to empty string)
    if not name:
        return Profile(
            name=None,
            phase_keys=None,
            config=global_cfg,
        )

    # Validate name
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

    # --- compose_profiles ---
    compose_profiles: list[str] = list(pdata.get("compose_profiles", []))

    # --- env_overrides ---
    raw_env = pdata.get("env_overrides", {})
    if not isinstance(raw_env, dict):
        raise ValueError(
            f"Profile '{name}': 'env_overrides' must be a dict."
        )
    env_overrides: dict = dict(raw_env)

    # --- topology_overrides (S7.4 / S7.5a) ---
    # Deep-copy the entire config so we never mutate the caller's dict.
    merged_config = copy.deepcopy(global_cfg)
    if "topology_overrides" in pdata:
        topo_override = pdata["topology_overrides"]
        if not isinstance(topo_override, dict):
            raise ValueError(
                f"Profile '{name}': 'topology_overrides' must be a dict."
            )
        existing_topo = merged_config.get("topology", {})
        merged_config["topology"] = deep_merge(existing_topo, topo_override)

    return Profile(
        name=name,
        phase_keys=phase_keys,
        extra_stacks=extra_stacks,
        compose_profiles=compose_profiles,
        env_overrides=env_overrides,
        config=merged_config,
    )
