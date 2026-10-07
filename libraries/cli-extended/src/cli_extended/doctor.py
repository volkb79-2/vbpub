"""Shared ``doctor`` verb: run a tool's environment checks and report them.

A consumer declares :class:`DoctorCheck` objects and registers them with
:func:`register_doctor`.  When the registry also carries the shared ``skills``
verbs, a built-in ``skills`` check equivalent to ``skills check`` is appended
at run time, whichever registration happened first.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .parser import (
    CliFailure,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)
from .contract import common_control_table
from .skills import (
    SkillState,
    default_skill_destinations,
    skill_leftovers,
    skill_states,
)

STATUSES = ("ok", "warn", "fail", "skip")
SKILLS_CHECK = "skills"
_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_LABELS = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check; ``details`` must be JSON-serialisable.

    ``lines`` are extra single-line strings printed in human output, indented
    four spaces under the check line and before the remedy; JSON output carries
    them as ``"lines"`` only when there are some.
    """

    status: str
    summary: str
    remedy: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.lines, tuple) or not all(
            isinstance(line, str) and "\n" not in line and "\r" not in line
            for line in self.lines
        ):
            raise ValueError("check lines must be a tuple of single-line strings")
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
    run: Callable[[CliRuntime, argparse.Namespace], CheckResult]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"doctor check name {self.name!r} must be lowercase tokens joined by '-'"
            )
        if not self.description:
            raise ValueError(f"doctor check {self.name!r} must define a description")


def _skills_check(package: str, resource_dir: str) -> DoctorCheck:
    def run(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
        tool = runtime.identity.command_name
        destinations = default_skill_destinations()
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
        broken = [row for row in bad if row[2] is not SkillState.ABSENT]
        details = {"skills": entries, "leftovers": [str(path) for path in leftovers]}
        if not bad and not leftovers:
            return CheckResult("ok", "all skills current", details=details)
        if not broken and not leftovers:
            return CheckResult(
                "warn",
                f"{len(bad)} skill(s) not installed",
                remedy=f"run '{tool} skills install'",
                details=details,
            )
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


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _execute(
    check: DoctorCheck, runtime: CliRuntime, args: argparse.Namespace
) -> CheckResult:
    try:
        result = check.run(runtime, args)
        if not isinstance(result, CheckResult):
            raise TypeError(f"returned {type(result).__name__}, not CheckResult")
    except Exception as exc:
        message = _one_line(exc)
        suffix = f": {message}" if message else ""
        return CheckResult("fail", f"check crashed: {type(exc).__name__}{suffix}")
    try:
        json.dumps(result.details, allow_nan=False)
    except (TypeError, ValueError):
        return CheckResult("fail", "check returned non-JSON details")
    if result.remedy is not None:
        return replace(result, remedy=_one_line(result.remedy) or None)
    return result


def _check_record(name: str, result: CheckResult) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": name,
        "status": result.status,
        "summary": result.summary,
        "remedy": result.remedy,
        "details": dict(result.details),
    }
    if result.lines:
        # Only when non-empty: the JSON of tools that never set lines is unchanged.
        record["lines"] = list(result.lines)
    return record


def _make_handler(
    registry: CliRegistry, checks: tuple[DoctorCheck, ...], fail_exit_code: int
):
    def handler(args: Any, runtime: CliRuntime) -> int:
        available = list(checks)
        skills = getattr(registry, "_cli_extended_skills", None)
        if skills is not None:
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
        results = [(check.name, _execute(check, runtime, args)) for check in selected]
        counts = {
            status: sum(1 for _, result in results if result.status == status)
            for status in STATUSES
        }
        if runtime.json_mode:
            runtime.output.primary({
                "tool": runtime.identity.command_name,
                "version": runtime.identity.version,
                "checks": [_check_record(name, result) for name, result in results],
                "summary": counts,
            })
        else:
            for name, result in results:
                runtime.output.primary(
                    f"{_LABELS[result.status]} {name}: {result.summary}"
                )
                for line in result.lines:
                    runtime.output.primary(f"    {line}")
                if result.remedy:
                    runtime.output.primary(f"    remedy: {result.remedy}")
            runtime.output.primary(
                f"doctor: {counts['ok']} ok, {counts['warn']} warn, "
                f"{counts['fail']} fail, {counts['skip']} skip"
            )
        return fail_exit_code if counts["fail"] else 0

    return handler


def register_doctor(
    registry: CliRegistry,
    checks: Sequence[DoctorCheck],
    *,
    description: str = "check this tool's environment and report problems",
    options: Sequence[OptionSpec] = (),
    fail_exit_code: int = 1,
) -> None:
    """Register the read-only ``doctor`` verb with ``--check NAME`` and ``--json``.

    ``options`` are extra consumer options on the verb; checks read them from
    the ``args`` namespace.  They may not reuse ``--check`` or a library flag.
    ``fail_exit_code`` (1..255, default 1) is the exit status when any check
    fails; a clean or warn-only run exits 0.
    """

    if (
        not isinstance(fail_exit_code, int)
        or isinstance(fail_exit_code, bool)
        or not 1 <= fail_exit_code <= 255
    ):
        raise ValueError(
            f"fail_exit_code must be an integer from 1 to 255, got {fail_exit_code!r}"
        )
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
    reserved = {"--check", "-h"}
    for entry in common_control_table().values():
        reserved.update(entry["flags"])
    for option in options:
        for flag in option.flags:
            if flag in reserved:
                raise ValueError(
                    f"doctor option {flag!r} shadows --check or a library control"
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
            *options,
        ),
        handler=_make_handler(registry, ordered, fail_exit_code),
    ))
    registry._cli_extended_doctor = ordered  # type: ignore[attr-defined]
