"""Landscape plan → ordered (global phase, host set, profile subset) steps (spec §7).

A plan TOML describes waves of work across hosts, with per-wave profile subsets
and a canary/production classification.  The planner validates the plan and
returns an ordered list of PlanStep objects.

Plan TOML schema (example):
  [plan]
  id = "plan-2024-01-15-v1"
  landscape = "prod"
  release_tag = "dstdns-v1.2.3"
  manifest_url = "https://github.com/org/repo/releases/download/dstdns-v1.2.3/manifest.json"
  manifest_sha256 = "abc123..."
  config_hash = "def456..."

  [[plan.waves]]
  phase = 1
  name = "canary"
  type = "canary"          # auto-applies
  required = true
  nodes = ["r1001.vxxu.de"]
  profiles = ["core"]

  [[plan.waves]]
  phase = 2
  name = "production"
  type = "production"      # requires cmru-controller approve
  required = true
  nodes = ["r1002.vxxu.de", "r1003.vxxu.de"]
  profiles = ["core", "worker-io"]

Cross-host ordering is expressed by listing the same host in multiple waves
at different phases (host A phase 1 → host B phase 2 → host A phase 3).
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


_ALLOWED_WAVE_TYPES = frozenset({"canary", "dev", "staging", "production"})
_PRODUCTION_WAVE_TYPES = frozenset({"production"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanStep:
    """A single unit of work: one wave for a subset of nodes + profiles."""
    plan_id: str
    wave_name: str
    phase: int
    wave_type: str          # canary | dev | staging | production
    nodes: List[str]
    profiles: List[str]
    release_tag: str
    manifest_url: str
    manifest_sha256: str
    config_hash: str
    step_id: str            # "{plan_id}.phase-{phase}.{wave_name}"
    required: bool = True
    requires_approval: bool = False   # True for production waves


@dataclass
class LandscapePlan:
    plan_id: str
    landscape: str
    steps: List[PlanStep]
    raw: dict = field(default_factory=dict, compare=False, repr=False)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def load_plan(plan_path: Path) -> LandscapePlan:
    """Parse a plan TOML file into a LandscapePlan."""
    with plan_path.open("rb") as fh:
        raw = tomllib.load(fh)
    return _parse_plan(raw, str(plan_path))


def load_plan_json(plan_json: str) -> LandscapePlan:
    """Parse a plan JSON string into a LandscapePlan (for tests)."""
    raw = json.loads(plan_json)
    return _parse_plan(raw, "<json>")


def _parse_plan(raw: dict, source: str) -> LandscapePlan:
    plan_section = raw.get("plan")
    if not isinstance(plan_section, dict):
        raise ValueError(f"[plan] section required in {source}")

    plan_id = plan_section.get("id", "").strip()
    if not plan_id:
        raise ValueError(f"plan.id is required in {source}")

    landscape = plan_section.get("landscape", "").strip()
    if not landscape:
        raise ValueError(f"plan.landscape is required in {source}")

    release_tag = plan_section.get("release_tag", "").strip()
    manifest_url = plan_section.get("manifest_url", "").strip()
    manifest_sha256 = plan_section.get("manifest_sha256", "").strip()
    config_hash = plan_section.get("config_hash", "").strip()

    if not release_tag or not manifest_url or not manifest_sha256:
        raise ValueError(
            f"plan.release_tag, plan.manifest_url, plan.manifest_sha256 are required in {source}"
        )

    waves_raw = plan_section.get("waves") or []
    if not isinstance(waves_raw, list) or not waves_raw:
        raise ValueError(f"plan.waves must be a non-empty list in {source}")

    steps: List[PlanStep] = []
    seen_phases: set = set()
    for i, wave in enumerate(waves_raw):
        if not isinstance(wave, dict):
            raise ValueError(f"plan.waves[{i}] must be a table in {source}")

        phase = wave.get("phase")
        if not isinstance(phase, int) or phase < 1:
            raise ValueError(f"plan.waves[{i}].phase must be a positive int in {source}")

        wave_name = (wave.get("name") or "").strip()
        if not wave_name:
            raise ValueError(f"plan.waves[{i}].name is required in {source}")

        wave_type = (wave.get("type") or "production").strip()
        if wave_type not in _ALLOWED_WAVE_TYPES:
            raise ValueError(
                f"plan.waves[{i}].type must be one of {sorted(_ALLOWED_WAVE_TYPES)} in {source}"
            )

        nodes = wave.get("nodes") or []
        if not isinstance(nodes, list) or not nodes:
            raise ValueError(f"plan.waves[{i}].nodes must be a non-empty list in {source}")
        for n in nodes:
            if not isinstance(n, str) or not n.strip():
                raise ValueError(f"plan.waves[{i}].nodes entries must be non-empty strings")

        profiles = wave.get("profiles") or []
        if not isinstance(profiles, list):
            raise ValueError(f"plan.waves[{i}].profiles must be a list in {source}")
        for p in profiles:
            if not isinstance(p, str) or not p.strip():
                raise ValueError(f"plan.waves[{i}].profiles entries must be non-empty strings")

        required = bool(wave.get("required", True))
        requires_approval = wave_type in _PRODUCTION_WAVE_TYPES

        step_id = f"{plan_id}.phase-{phase}.{wave_name}"

        steps.append(PlanStep(
            plan_id=plan_id,
            wave_name=wave_name,
            phase=phase,
            wave_type=wave_type,
            nodes=[n.strip() for n in nodes],
            profiles=[p.strip() for p in profiles],
            release_tag=release_tag,
            manifest_url=manifest_url,
            manifest_sha256=manifest_sha256,
            config_hash=config_hash,
            step_id=step_id,
            required=required,
            requires_approval=requires_approval,
        ))

    # Sort steps by phase (cross-host ordering: A→B→A is expressed via phases)
    steps.sort(key=lambda s: s.phase)

    return LandscapePlan(
        plan_id=plan_id,
        landscape=landscape,
        steps=steps,
        raw=raw,
    )
