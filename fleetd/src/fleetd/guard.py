"""Parsing and validation for host-owned guard rules; no rule is applied here."""

from __future__ import annotations

import tomllib
from typing import Any

from .contracts import ContractError, GuardConfig, _boolean, _integer, _number, _object, _string, parse_memory_bytes


def validate_guard(value: object) -> GuardConfig:
    root = _object(
        value,
        "guard.toml",
        {"enabled", "mode", "sample_interval_s", "trigger", "thaw", "victim", "reclaim", "log"},
    )
    _boolean(root["enabled"], "guard.toml.enabled")
    mode = _string(root["mode"], "guard.toml.mode")
    if mode not in {"report", "enforce"}:
        raise ContractError("guard.toml.mode: expected report or enforce")
    _integer(root["sample_interval_s"], "guard.toml.sample_interval_s", minimum=1)

    trigger = _object(
        root["trigger"], "guard.toml.trigger",
        {"soft_full_pct", "soft_sustained_s", "useful_cpu_min", "hard_full_pct", "hard_sustained_s"},
    )
    soft = _number(trigger["soft_full_pct"], "guard.toml.trigger.soft_full_pct", minimum=0, maximum=100)
    _integer(trigger["soft_sustained_s"], "guard.toml.trigger.soft_sustained_s", minimum=1)
    _number(trigger["useful_cpu_min"], "guard.toml.trigger.useful_cpu_min", minimum=0, maximum=1)
    hard = _number(trigger["hard_full_pct"], "guard.toml.trigger.hard_full_pct", minimum=0, maximum=100)
    if hard < soft:
        raise ContractError("guard.toml.trigger.hard_full_pct: must be at least soft_full_pct")
    _integer(trigger["hard_sustained_s"], "guard.toml.trigger.hard_sustained_s", minimum=1)

    thaw = _object(root["thaw"], "guard.toml.thaw", {"full_below_pct", "sustained_s"})
    _number(thaw["full_below_pct"], "guard.toml.thaw.full_below_pct", minimum=0, maximum=100)
    _integer(thaw["sustained_s"], "guard.toml.thaw.sustained_s", minimum=1)

    victim = _object(root["victim"], "guard.toml.victim", {"policy"})
    policy = _string(victim["policy"], "guard.toml.victim.policy")
    if policy != "least-progress-per-ram":
        raise ContractError("guard.toml.victim.policy: expected least-progress-per-ram")

    reclaim = _object(
        root["reclaim"], "guard.toml.reclaim",
        {"step_bytes", "swappiness", "max_parked", "max_park_s"},
    )
    _string(reclaim["step_bytes"], "guard.toml.reclaim.step_bytes")
    parse_memory_bytes(reclaim["step_bytes"], "guard.toml.reclaim.step_bytes")
    _integer(reclaim["swappiness"], "guard.toml.reclaim.swappiness", minimum=0)
    if reclaim["swappiness"] > 200:
        raise ContractError("guard.toml.reclaim.swappiness: must be at most 200")
    _integer(reclaim["max_parked"], "guard.toml.reclaim.max_parked", minimum=0)
    _integer(reclaim["max_park_s"], "guard.toml.reclaim.max_park_s", minimum=1)

    log = _object(root["log"], "guard.toml.log", {"io_pressure"})
    _boolean(log["io_pressure"], "guard.toml.log.io_pressure")
    return GuardConfig(root)


def parse_guard_toml(text: str) -> GuardConfig:
    try:
        parsed: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"guard.toml: invalid TOML: {exc}") from exc
    return validate_guard(parsed)
