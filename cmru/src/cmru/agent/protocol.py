"""Desired-state / observed-state protocol dataclasses + strict schema validation.

Seam 2 wire format (spec §3.2 / §3.3). Zero third-party dependencies.

DesiredState schema_version=1:
  {
    "schema_version": 1,
    "generation": <int>,
    "action": "install" | "update" | "rollback" | "hold",
    "release": {"tag": "...", "manifest_url": "...", "manifest_sha256": "..."},
    "profiles": ["<profile>", ...],
    "config_hash": "...",
    "plan_id": "...",
    "step_id": "..."
  }

ObservedState:
  {
    "applied_generation": <int|null>,
    "release_digest": "...",
    "adapter_phase": "...",
    "health": "healthy" | "degraded" | "failed" | "applying",
    "started_at": "<iso8601>|null",
    "finished_at": "<iso8601>|null",
    "error_class": "<str>|null",
    "exit_code": <int|null>,
    "message": "<redacted human msg>"
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# ---------------------------------------------------------------------------
# Allowed sets (fail-closed: unknown enum values are rejected)
# ---------------------------------------------------------------------------

_ALLOWED_ACTIONS = frozenset({"install", "update", "rollback", "hold"})
_ALLOWED_HEALTH = frozenset({"healthy", "degraded", "failed", "applying"})

# Exact top-level keys permitted in DesiredState v1 — unknown keys rejected
_DESIRED_KEYS_V1 = frozenset({
    "schema_version", "generation", "action",
    "release", "profiles", "config_hash", "plan_id", "step_id",
})
_RELEASE_KEYS = frozenset({"tag", "manifest_url", "manifest_sha256"})


# ---------------------------------------------------------------------------
# DesiredState
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReleaseRef:
    tag: str
    manifest_url: str
    manifest_sha256: str


@dataclass(frozen=True)
class DesiredState:
    schema_version: int
    generation: int
    action: str
    release: ReleaseRef
    profiles: List[str]
    config_hash: str
    plan_id: str
    step_id: str


# ---------------------------------------------------------------------------
# ObservedState
# ---------------------------------------------------------------------------

@dataclass
class ObservedState:
    applied_generation: Optional[int] = None
    release_digest: str = ""
    adapter_phase: str = ""
    health: str = "healthy"           # healthy | degraded | failed | applying
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_class: Optional[str] = None
    exit_code: Optional[int] = None
    message: str = ""                 # ALWAYS redacted before writing

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "ObservedState":
        data = json.loads(raw)
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class DesiredStateError(ValueError):
    """Raised when incoming desired state fails schema validation."""


def validate_desired(raw_dict: dict) -> DesiredState:
    """Parse + validate a DesiredState dict.  Fails closed on:
    - unknown top-level keys
    - wrong schema_version
    - invalid action enum
    - missing required fields
    - wrong types

    Never executes content; treats all fields as pure data.
    """
    if not isinstance(raw_dict, dict):
        raise DesiredStateError("desired state must be a JSON object")

    # Unknown key guard
    unknown = set(raw_dict) - _DESIRED_KEYS_V1
    if unknown:
        raise DesiredStateError(f"unknown keys in desired state: {sorted(unknown)}")

    # schema_version
    version = raw_dict.get("schema_version")
    if version != 1:
        raise DesiredStateError(f"unsupported schema_version: {version!r}")

    # generation
    generation = raw_dict.get("generation")
    if not isinstance(generation, int) or generation < 0:
        raise DesiredStateError(f"invalid generation: {generation!r}")

    # action — no shell action allowed
    action = raw_dict.get("action")
    if action not in _ALLOWED_ACTIONS:
        raise DesiredStateError(
            f"invalid action {action!r}; allowed: {sorted(_ALLOWED_ACTIONS)}"
        )

    # release
    release_raw = raw_dict.get("release")
    if not isinstance(release_raw, dict):
        raise DesiredStateError("release must be an object")
    unknown_rel = set(release_raw) - _RELEASE_KEYS
    if unknown_rel:
        raise DesiredStateError(f"unknown keys in release: {sorted(unknown_rel)}")
    for k in _RELEASE_KEYS:
        if not isinstance(release_raw.get(k), str) or not release_raw[k]:
            raise DesiredStateError(f"release.{k} must be a non-empty string")
    release = ReleaseRef(
        tag=release_raw["tag"],
        manifest_url=release_raw["manifest_url"],
        manifest_sha256=release_raw["manifest_sha256"],
    )

    # profiles — treat as opaque list of strings
    profiles_raw = raw_dict.get("profiles")
    if not isinstance(profiles_raw, list):
        raise DesiredStateError("profiles must be an array")
    for p in profiles_raw:
        if not isinstance(p, str) or not p:
            raise DesiredStateError("each profile must be a non-empty string")
    profiles = list(profiles_raw)

    # optional string fields
    config_hash = raw_dict.get("config_hash", "")
    plan_id = raw_dict.get("plan_id", "")
    step_id = raw_dict.get("step_id", "")
    for name, val in [("config_hash", config_hash), ("plan_id", plan_id), ("step_id", step_id)]:
        if not isinstance(val, str):
            raise DesiredStateError(f"{name} must be a string")

    return DesiredState(
        schema_version=1,
        generation=generation,
        action=action,
        release=release,
        profiles=profiles,
        config_hash=config_hash,
        plan_id=plan_id,
        step_id=step_id,
    )


def parse_desired_json(payload: bytes) -> DesiredState:
    """Decode JSON bytes and validate; raises DesiredStateError on any problem."""
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DesiredStateError(f"invalid JSON: {exc}") from exc
    return validate_desired(raw)
