"""Review findings file: the agent-authored list of what is wrong with a CLI.

The library only reads this file.  An open ``blocker`` or ``major`` finding
fails ``cli-extended surface check``; the rest are reported.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FINDINGS_SCHEMA_VERSION = 1
STATUSES = ("open", "fixed", "wontfix")
SEVERITIES = ("blocker", "major", "minor", "note")
CATEGORIES = ("grammar", "help", "semantics", "consistency", "adoption")
BLOCKING_SEVERITIES = ("blocker", "major")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TOP_KEYS = ("schema_version", "cli_id", "findings")
_FINDING_KEYS = (
    "id", "status", "severity", "category", "summary", "remedy", "rationale", "route",
)


class FindingsError(ValueError):
    """A findings file is malformed or belongs to another CLI."""


@dataclass(frozen=True)
class Finding:
    id: str
    status: str
    severity: str
    category: str
    summary: str
    remedy: str = ""
    rationale: str = ""
    route: str = ""


@dataclass(frozen=True)
class FindingsFile:
    cli_id: str
    findings: tuple[Finding, ...]

    def open_findings(self) -> tuple[Finding, ...]:
        """Open findings, most severe first, then by id."""

        return tuple(
            sorted(
                (item for item in self.findings if item.status == "open"),
                key=lambda item: (SEVERITIES.index(item.severity), item.id),
            )
        )


def _text(table: Mapping[str, Any], key: str, where: str, *, required: bool) -> str:
    if key not in table:
        if required:
            raise FindingsError(f"{where} is missing required key {key!r}")
        return ""
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise FindingsError(f"{where}.{key} must be a non-empty string")
    return value


def _choice(table: Mapping[str, Any], key: str, allowed: tuple[str, ...], where: str) -> str:
    value = _text(table, key, where, required=True)
    if value not in allowed:
        raise FindingsError(
            f"{where}.{key} must be one of {', '.join(allowed)}, got {value!r}"
        )
    return value


def _finding(raw: Any, index: int) -> Finding:
    where = f"findings[{index}]"
    if not isinstance(raw, Mapping):
        raise FindingsError(f"{where} must be a table")
    for key in raw:
        if key not in _FINDING_KEYS:
            raise FindingsError(f"unknown key {key!r} in {where}")
    identifier = _text(raw, "id", where, required=True)
    if not _ID.fullmatch(identifier):
        raise FindingsError(
            f"{where}.id {identifier!r} must match [A-Za-z0-9][A-Za-z0-9._-]*"
        )
    status = _choice(raw, "status", STATUSES, where)
    return Finding(
        id=identifier,
        status=status,
        severity=_choice(raw, "severity", SEVERITIES, where),
        category=_choice(raw, "category", CATEGORIES, where),
        summary=_text(raw, "summary", where, required=True),
        remedy=_text(raw, "remedy", where, required=status == "open"),
        rationale=_text(raw, "rationale", where, required=status == "wontfix"),
        route=_text(raw, "route", where, required=False),
    )


def load_review_findings(path: str | Path) -> FindingsFile:
    """Parse and validate a findings TOML file."""

    try:
        with Path(path).open("rb") as stream:
            data = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise FindingsError(f"{path} is not valid TOML: {exc}") from exc
    for key in data:
        if key not in _TOP_KEYS:
            raise FindingsError(f"unknown top-level key {key!r} in {path}")
    version = data.get("schema_version")
    if type(version) is not int or version != FINDINGS_SCHEMA_VERSION:
        raise FindingsError(
            f"schema_version in {path} must be the integer {FINDINGS_SCHEMA_VERSION}, "
            f"got {version!r}"
        )
    cli_id = _text(data, "cli_id", str(path), required=True)
    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        raise FindingsError(f"findings in {path} must be an array of tables")
    findings = tuple(_finding(raw, index) for index, raw in enumerate(raw_findings))
    seen: set[str] = set()
    for item in findings:
        if item.id in seen:
            raise FindingsError(f"duplicate finding id {item.id!r} in {path}")
        seen.add(item.id)
    return FindingsFile(cli_id=cli_id, findings=findings)
