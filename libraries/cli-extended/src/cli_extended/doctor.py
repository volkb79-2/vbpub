"""Shared ``doctor`` verb: run a tool's environment checks and report them.

A consumer declares :class:`DoctorCheck` objects and registers them with
:func:`register_doctor`.  When the registry also carries the shared ``skills``
verbs, a built-in ``skills`` check equivalent to ``skills check`` is appended
at run time, whichever registration happened first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .parser import (
    CliFailure,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)
from .skills import SkillState, _destinations, skill_leftovers, skill_states

STATUSES = ("ok", "warn", "fail", "skip")
SKILLS_CHECK = "skills"
_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_LABELS = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check; ``details`` must be JSON-serialisable."""

    status: str
    summary: str
    remedy: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"check status must be one of {', '.join(STATUSES)}; got {self.status!r}"
            )
        if (
            not isinstance(self.summary, str)
            or not self.summary
            or "\n" in self.summary
            or "\r" in self.summary
        ):
            raise ValueError("check summary must be a non-empty single line")


@dataclass(frozen=True)
class DoctorCheck:
    """One named environment check."""

    name: str
    description: str
    run: Callable[[CliRuntime], CheckResult]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"doctor check name {self.name!r} must be lowercase tokens joined by '-'"
            )
        if not self.description:
            raise ValueError(f"doctor check {self.name!r} must define a description")


def _skills_check(package: str, resource_dir: str) -> DoctorCheck:
    def run(runtime: CliRuntime) -> CheckResult:
        tool = runtime.identity.command_name
        destinations = _destinations(None, None)
        rows = skill_states(
            package=package,
            resource_dir=resource_dir,
            tool=tool,
            version=runtime.identity.version,
            destinations=destinations,
        )
        leftovers = skill_leftovers(tool=tool, destinations=destinations)
        entries = [
            {"name": name, "destination": str(destination), "state": state.value}
            for name, destination, state in sorted(
                rows, key=lambda row: (str(row[1]), row[0])
            )
        ]
        bad = [row for row in rows if row[2] is not SkillState.CURRENT]
        details = {"skills": entries, "leftovers": [str(path) for path in leftovers]}
        if not bad and not leftovers:
            return CheckResult("ok", "all skills current", details=details)
        parts = []
        if bad:
            parts.append(f"{len(bad)} skill(s) not current")
        if leftovers:
            parts.append(f"{len(leftovers)} leftover path(s)")
        return CheckResult(
            "fail",
            "; ".join(parts),
            remedy=f"run '{tool} skills install'",
            details=details,
        )

    return DoctorCheck(SKILLS_CHECK, "packaged agent skills are installed and current", run)


def _execute(check: DoctorCheck, runtime: CliRuntime) -> CheckResult:
    try:
        result = check.run(runtime)
        if not isinstance(result, CheckResult):
            raise TypeError(f"returned {type(result).__name__}, not CheckResult")
    except Exception as exc:
        return CheckResult("fail", f"check crashed: {type(exc).__name__}: {exc}")
    try:
        json.dumps(result.details)
    except (TypeError, ValueError):
        return CheckResult("fail", "check returned non-JSON details")
    return result


def _make_handler(registry: CliRegistry, checks: tuple[DoctorCheck, ...]):
    def handler(args: Any, runtime: CliRuntime) -> int:
        available = list(checks)
        skills = getattr(registry, "_cli_extended_skills", None)
        if skills is not None:
            if any(check.name == SKILLS_CHECK for check in available):
                raise CliFailure(
                    "doctor check name 'skills' is reserved for the built-in skills check"
                )
            available.append(_skills_check(*skills))
        names = [check.name for check in available]
        chosen = args.check
        if chosen:
            for name in chosen:
                if name not in names:
                    raise CliFailure(
                        f"unknown doctor check {name!r}; available: {', '.join(names)}",
                        exit_code=2,
                        show_help=True,
                    )
            selected = [check for check in available if check.name in chosen]
        else:
            selected = available
        results = [(check.name, _execute(check, runtime)) for check in selected]
        counts = {
            status: sum(1 for _, result in results if result.status == status)
            for status in STATUSES
        }
        if runtime.json_mode:
            runtime.output.primary({
                "tool": runtime.identity.command_name,
                "version": runtime.identity.version,
                "checks": [
                    {
                        "name": name,
                        "status": result.status,
                        "summary": result.summary,
                        "remedy": result.remedy,
                        "details": dict(result.details),
                    }
                    for name, result in results
                ],
                "summary": counts,
            })
        else:
            for name, result in results:
                runtime.output.primary(
                    f"{_LABELS[result.status]} {name}: {result.summary}"
                )
                if result.remedy:
                    runtime.output.primary(f"    remedy: {result.remedy}")
            runtime.output.primary(
                f"doctor: {counts['ok']} ok, {counts['warn']} warn, "
                f"{counts['fail']} fail, {counts['skip']} skip"
            )
        return 1 if counts["fail"] else 0

    return handler


def register_doctor(
    registry: CliRegistry,
    checks: Sequence[DoctorCheck],
    *,
    description: str = "check this tool's environment and report problems",
) -> None:
    """Register the read-only ``doctor`` verb with ``--check NAME`` and ``--json``."""

    if hasattr(registry, "_cli_extended_doctor"):
        raise ValueError("doctor is already registered on this registry")
    ordered = tuple(checks)
    seen: set[str] = set()
    for check in ordered:
        if check.name in seen:
            raise ValueError(f"duplicate doctor check {check.name!r}")
        seen.add(check.name)
        if check.name == SKILLS_CHECK and hasattr(registry, "_cli_extended_skills"):
            raise ValueError(
                "doctor check name 'skills' is reserved for the built-in skills check"
            )
    registry.register(VerbSpec(
        "doctor",
        description=description,
        group=VerbGroup.MAINTENANCE.value,
        include_json=True,
        include_progress=False,
        options=(
            OptionSpec(
                ("--check",),
                "run only this check (repeatable)",
                metavar="NAME",
                parser_kwargs={"action": "append", "default": None},
            ),
        ),
        handler=_make_handler(registry, ordered),
    ))
    registry._cli_extended_doctor = ordered  # type: ignore[attr-defined]
