"""APT package-plan parsing and the policy-only snapshot window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .ids import StateId

_VERSION_RE = re.compile(r"VERSION (?P<version>[23])\Z")


@dataclass(frozen=True, slots=True)
class PackageAction:
    """One recognized package operation from APT's plan protocol."""

    kind: str
    package: str
    old_version: str
    new_version: str

    def line(self) -> str:
        """Render the stable human-readable plan line."""

        return (
            f"{self.kind} {self.package} "
            f"{self.old_version} -> {self.new_version}"
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """A complete version-2 or version-3 Pre-Install-Pkgs plan."""

    version: int
    actions: tuple[PackageAction, ...]

    def summary(self) -> str:
        """Return the concise text stored in a sidecar."""

        if not self.actions:
            return "no package actions reported"
        return "\n".join(action.line() for action in self.actions)


def parse_plan(text: str) -> Plan | None:
    """Parse an APT plan, returning ``None`` for an unavailable plan."""

    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return None
    version_match = _VERSION_RE.fullmatch(lines[0])
    if version_match is None:
        return None
    version = int(version_match["version"])
    try:
        marker = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == ""
        )
    except StopIteration:
        return None

    actions: list[PackageAction] = []
    for line in lines[marker + 1 :]:
        fields = line.split()
        if version == 2:
            if len(fields) < 5:
                return None
            package, old_version, direction, new_version, operation = fields[:5]
        else:
            if len(fields) < 9:
                return None
            package = fields[0]
            old_version = fields[1]
            direction = fields[4]
            new_version = fields[5]
            operation = fields[8]

        if operation == "**REMOVE**":
            kind = "remove"
        elif old_version == "-":
            kind = "install"
        elif direction == "<":
            kind = "upgrade"
        elif direction == ">":
            kind = "downgrade"
        elif operation == "**CONFIGURE**":
            kind = "configure"
        elif direction == "=":
            kind = "reinstall-or-unchanged"
        else:
            return None
        actions.append(PackageAction(kind, package, old_version, new_version))
    return Plan(version, tuple(actions))


def summarize_plan(text: str) -> str | None:
    """Parse a plan and return its sidecar summary, or ``None`` if unusable."""

    plan = parse_plan(text)
    return None if plan is None else plan.summary()


def window_blocks(state: StateId | None, interval: int, now: datetime) -> bool:
    """Whether ``now`` is in ``[created, created + interval)``."""

    if state is None or interval <= 0:
        return False
    if now.tzinfo is None:
        raise ValueError("window comparison requires an aware datetime")
    age = int(now.timestamp()) - int(state.created.timestamp())
    return 0 <= age < interval
