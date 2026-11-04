"""``check``: the offline drift and validity check of one project's ``rigor/`` tree (memo 6.1), and the waiver preflight.

It fails only on invalid or hand-edited files, never on lag: a published tag without a record is the online
``check --published`` of a later package. Nothing here touches the network; git is read for object ids only.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .derive import targets_at_factory
from .gitfacts import GitFacts
from .render import RenderError, render_views
from .validate import (
    canonical_json,
    is_expired,
    load_waivers,
    validate_index,
    validate_record,
)

RELEASES = "rigor/commits"


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _read(path: Path, shown: str) -> tuple[str | None, list[Problem]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeDecodeError) as exc:
        return None, [Problem(shown, f"cannot be read ({type(exc).__name__})")]


def load_records(
    project_dir: Path, hosts: tuple[str, ...], users: tuple[str, ...],
) -> tuple[list[dict], list[Problem]]:
    records: list[dict] = []
    problems: list[Problem] = []
    base = project_dir / RELEASES
    entries = []
    for bucket in sorted(base.iterdir()) if base.is_dir() else []:
        shown_bucket = f"{RELEASES}/{bucket.name}"
        if not bucket.is_dir() or bucket.is_symlink() or not re.fullmatch(r"[0-9a-f]{2}", bucket.name):
            problems.append(Problem(shown_bucket, "only <aa>/ directories of record files belong here"))
            continue
        entries += sorted(bucket.iterdir())
    for entry in entries:
        shown = f"{RELEASES}/{entry.parent.name}/{entry.name}"
        if not entry.is_file() or entry.is_symlink() or not entry.name.endswith(".json"):
            problems.append(Problem(shown, "only regular <commit>.json record files belong here"))
            continue
        text, read_problems = _read(entry, shown)
        problems += read_problems
        if text is None:
            continue
        try:
            record = json.loads(text)
        except ValueError:
            problems.append(Problem(shown, "not valid JSON"))
            continue
        violations = validate_record(record, hosts=hosts, users=users)
        problems += [Problem(shown, str(v)) for v in violations]
        if violations:
            continue
        if entry.name != record["commit"] + ".json" or entry.parent.name != record["commit"][:2]:
            problems.append(Problem(shown, "the path is not <first two hex>/<commit>.json"))
        if text != canonical_json(record):
            problems.append(Problem(shown, "not in canonical form (hand-edited?); regenerate with rigor-record"))
        records.append(record)
    return records, problems


def _check_views(
    project_dir: Path, records: list[dict], hosts: tuple[str, ...], users: tuple[str, ...], git: GitFacts,
) -> list[Problem]:
    projects = sorted({r["project"] for r in records})
    if len(projects) != 1:
        return [Problem(RELEASES, f"records name {len(projects)} projects, expected exactly one")]
    try:
        expected = render_views(records, projects[0], targets_at_factory(records, git))
    except RenderError as exc:
        return [Problem("RIGOR.md", p) for p in exc.problems]
    problems: list[Problem] = []
    for relative, want in expected.items():
        if not (project_dir / relative).exists():
            problems.append(Problem(relative, "is missing; run rigor-record render"))
            continue
        text, read_problems = _read(project_dir / relative, relative)
        problems += read_problems
        if text is None:
            continue
        if text != want:
            problems.append(Problem(relative, "differs from its regeneration (stale or hand-edited); run rigor-record render"))
        if relative.endswith("index.json"):
            try:
                problems += [Problem(relative, str(v)) for v in validate_index(json.loads(text), hosts=hosts, users=users)]
            except ValueError:
                problems.append(Problem(relative, "not valid JSON"))
    return problems


def _check_hand_written(project_dir: Path, hosts: tuple[str, ...], users: tuple[str, ...]) -> list[Problem]:
    problems: list[Problem] = []
    for name, loader in (("waivers.toml", load_waivers),):
        path = project_dir / "rigor" / name
        if not path.exists():
            continue
        text, read_problems = _read(path, f"rigor/{name}")
        problems += read_problems
        if text is not None:
            problems += [Problem(f"rigor/{name}", str(v)) for v in loader(text, hosts=hosts, users=users)[1]]
    return problems


def check_project(project_dir: Path, *, git: GitFacts, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Problem]:
    """Validate every record, the hand-written files, and that both views equal their regeneration (git supplies the object ids that decide which results are valid at which commit)."""
    hosts, users = tuple(hosts), tuple(users)
    records, problems = load_records(project_dir, hosts, users)
    problems += _check_hand_written(project_dir, hosts, users)
    if records:
        problems += _check_views(project_dir, records, hosts, users, git)
    return problems


def check_waivers(
    project_dir: Path, version: str, *, hosts: Iterable[str] = (), users: Iterable[str] = (),
) -> list[Problem]:
    """The pre-gate preflight (memo 3.6): the waivers file is valid and no lane waiver is expired at ``version``."""
    path = project_dir / "rigor" / "waivers.toml"
    if not path.exists():
        return []
    text, problems = _read(path, "rigor/waivers.toml")
    if text is None:
        return problems
    document, violations = load_waivers(text, hosts=hosts, users=users)
    problems += [Problem("rigor/waivers.toml", str(v)) for v in violations]
    if violations:
        return problems
    return problems + [
        Problem("rigor/waivers.toml", f"waiver for lane {w['lane']} ({w['level']}) expired: expires_before {w['expires_before']} is not after {version}")
        for w in document.get("lane", []) if is_expired(w["expires_before"], version)
    ]


def load_privacy_config(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The operator's local denylists (``hosts`` substrings, ``users`` whole words); never committed."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"privacy config is not valid TOML: {exc}") from exc
    unknown = sorted(set(document) - {"hosts", "users"})
    if unknown:
        raise ValueError(f"privacy config has unknown keys: {', '.join(unknown)}")
    lists = []
    for key in ("hosts", "users"):
        values = document.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise ValueError(f"privacy config {key} must be a list of non-empty strings")
        lists.append(tuple(values))
    return lists[0], lists[1]
