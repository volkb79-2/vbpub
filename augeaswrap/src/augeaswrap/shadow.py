"""Layered configuration order and intent checks shared by resource kinds.

Kinds read a :class:`TargetClass` below the selected root, compute its effective
file sequence in Python, and pass normalized assignments to :func:`analyze`.
The observation includes every candidate file and each directory's entry list,
so both edits and newly-added drop-ins make a saved plan stale.
"""

from __future__ import annotations

import base64
import fnmatch
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import _fs

MergeRule = Literal["first", "last"]


@dataclass(frozen=True)
class TargetClass:
    """One systemd configuration search path and its merge rule."""

    name: str
    directories: tuple[str, ...]
    merge: MergeRule
    extra_files: tuple[str, ...] = ()


TARGET_CLASSES = {
    "sysctl": TargetClass(
        "sysctl",
        ("/etc/sysctl.d", "/run/sysctl.d", "/usr/local/lib/sysctl.d", "/usr/lib/sysctl.d"),
        "last",
        ("/etc/sysctl.conf",),
    ),
    "sysusers": TargetClass(
        "sysusers", ("/etc/sysusers.d", "/run/sysusers.d", "/usr/lib/sysusers.d"), "first"
    ),
    "tmpfiles": TargetClass(
        "tmpfiles", ("/etc/tmpfiles.d", "/run/tmpfiles.d", "/usr/lib/tmpfiles.d"), "first"
    ),
}


@dataclass(frozen=True)
class LayerFile:
    path: str
    data: bytes
    rank: int


@dataclass(frozen=True)
class Assignment:
    """A normalized setting with its file and effective sequence position."""

    file: str
    key: str
    value: str
    order: tuple[int, int]


@dataclass(frozen=True)
class Finding:
    """One setting or whole-file overlap, ready for a plan note or refusal."""

    code: str
    file: str
    key: str
    message: str
    severity: Literal["error", "note"]


def observe_layers(root: Path, target_class: TargetClass) -> dict[str, object]:
    """Capture candidate file bytes and directory membership under ``root``."""

    files: dict[str, str | None] = {}
    directories: dict[str, list[str] | None] = {}
    for directory in target_class.directories:
        absolute = root / directory.lstrip("/")
        _fs.safe_components(root, absolute)
        try:
            info = absolute.lstat()
        except FileNotFoundError:
            directories[directory] = None
            continue
        if not absolute.is_dir() or absolute.is_symlink():
            raise ValueError(f"{directory} is not a safe directory")
        names = sorted(path.name for path in absolute.iterdir() if path.name.endswith(".conf"))
        directories[directory] = names
        for name in names:
            path = f"{directory}/{name}"
            target = root / path.lstrip("/")
            _fs.regular_or_missing(root, target)
            files[path] = base64.b64encode(target.read_bytes()).decode("ascii")
    for path in target_class.extra_files:
        target = root / path.lstrip("/")
        _fs.regular_or_missing(root, target)
        files[path] = (
            base64.b64encode(target.read_bytes()).decode("ascii")
            if target.exists()
            else None
        )
    return {"files": files, "directories": directories}


def effective_files(
    observation: Mapping[str, object],
    target_class: TargetClass,
    *,
    overrides: Mapping[str, bytes | None] | None = None,
) -> tuple[tuple[LayerFile, ...], tuple[tuple[str, str], ...]]:
    """Return files in basename order and ``(masked, winner)`` pairs.

    For a duplicate basename, the first directory in ``TargetClass`` wins.
    ``/etc/sysctl.conf`` follows the drop-in sequence, as specified by the
    sysctl target class.
    """

    raw_files = observation.get("files")
    if not isinstance(raw_files, Mapping):
        raise ValueError("layer observation has no file map")
    contents: dict[str, bytes | None] = {}
    for path, encoded in raw_files.items():
        if not isinstance(path, str) or (encoded is not None and not isinstance(encoded, str)):
            raise ValueError("layer observation contains an invalid file")
        contents[path] = None if encoded is None else base64.b64decode(encoded, validate=True)
    for path, data in (overrides or {}).items():
        contents[path] = data

    rank_by_path: dict[str, int] = {}
    for rank, directory in enumerate(target_class.directories):
        for path in contents:
            if path.startswith(directory + "/"):
                rank_by_path[path] = rank
    for offset, path in enumerate(target_class.extra_files, start=len(target_class.directories)):
        rank_by_path[path] = offset

    candidates = [
        LayerFile(path, data, rank_by_path[path])
        for path, data in contents.items()
        if data is not None and path in rank_by_path
    ]
    candidates.sort(key=lambda item: (Path(item.path).name, item.rank, item.path))
    winner_by_name: dict[str, LayerFile] = {}
    masked: list[tuple[str, str]] = []
    for candidate in sorted(candidates, key=lambda item: (item.rank, Path(item.path).name, item.path)):
        basename = Path(candidate.path).name
        winner = winner_by_name.get(basename)
        if winner is None:
            winner_by_name[basename] = candidate
        else:
            masked.append((candidate.path, winner.path))
    selected = tuple(sorted(winner_by_name.values(), key=lambda item: (Path(item.path).name, item.rank)))
    return selected, tuple(masked)


def settings(
    files: Sequence[LayerFile],
    parser: Callable[[bytes], Sequence[tuple[str, str]]],
) -> tuple[Assignment, ...]:
    """Parse normalized ``(key, value)`` rows while retaining effective order."""

    result: list[Assignment] = []
    for file_index, item in enumerate(files):
        for line_index, pair in enumerate(parser(item.data)):
            key, value = pair
            result.append(Assignment(item.path, key, value, (file_index, line_index)))
    return tuple(result)


def _overlaps(left: str, right: str) -> bool:
    return left == right or fnmatch.fnmatchcase(left, right) or fnmatch.fnmatchcase(right, left)


def _accepted(accept_shadow: Sequence[tuple[str, str]], file: str, key: str) -> bool:
    return (file, key) in accept_shadow


def analyze(
    *,
    target_file: str,
    assignments: Sequence[Assignment],
    target_keys: Sequence[str],
    intent: str,
    merge: MergeRule,
    accept_shadow: Sequence[tuple[str, str]],
    masks: Sequence[tuple[str, str]] = (),
) -> tuple[Finding, ...]:
    """Check that the caller's target wins or loses according to its intent.

    A setting acceptance is the exact pair ``(other file, other key)``. For a
    same-basename mask, key ``$file`` names that whole-file finding.
    """

    if intent not in {"authoritative", "fallback"}:
        raise ValueError("intent must be 'authoritative' or 'fallback'")
    if merge not in {"first", "last"}:
        raise ValueError("merge must be 'first' or 'last'")
    findings: list[Finding] = []
    for key in target_keys:
        ours = [item for item in assignments if item.file == target_file and _overlaps(item.key, key)]
        if not ours:
            continue
        own = ours[-1] if merge == "last" else ours[0]
        others = [item for item in assignments if item.file != target_file and _overlaps(item.key, key)]
        if not others:
            continue
        sequence = sorted([own, *others], key=lambda item: item.order)
        winner = sequence[-1] if merge == "last" else sequence[0]
        ours_wins = winner.file == target_file
        for other in others:
            equal = other.value == own.value
            if merge == "last" and intent == "authoritative":
                violates = other.order > own.order
            elif merge == "first" and intent == "authoritative":
                violates = other.order < own.order
            else:
                violates = ours_wins
            if not violates and not equal:
                continue
            if merge == "last":
                code = "shadow.later" if other.order > own.order else "shadow.earlier"
            else:
                code = "shadow.earlier" if other.order < own.order else "shadow.later"
            accepted = _accepted(accept_shadow, other.file, other.key)
            authoritative_loses = intent == "authoritative" and violates
            severity: Literal["error", "note"] = "note" if accepted or (equal and not authoritative_loses) else "error"
            if accepted:
                message = f"accepted {other.file} key {other.key} overlapping {key} in {target_file}"
            elif equal and not violates:
                message = f"equal value for {key} also appears in {other.file} key {other.key}"
            else:
                message = f"{intent} setting {key} in {target_file} is shadowed by {other.file} key {other.key}"
            findings.append(Finding(code, other.file, other.key, message, severity))

    for masked, winner in masks:
        if target_file not in {masked, winner}:
            continue
        target_loses = masked == target_file
        violates = (intent == "authoritative" and target_loses) or (intent == "fallback" and not target_loses)
        if not violates:
            continue
        other = winner if target_loses else masked
        accepted = _accepted(accept_shadow, other, "$file")
        findings.append(
            Finding(
                "shadow.mask",
                other,
                "$file",
                f"{target_file} is masked by {winner}" if target_loses else f"{target_file} masks {masked}",
                "note" if accepted else "error",
            )
        )
    return tuple(findings)


def run_command(
    argv: Sequence[str], *, timeout: float = 30.0, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Command seam used by kind validators and live cross-checks.

    Tests replace this function with a canned runner; callers pass argv only.
    """

    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL if input_text is None else None,
        input=input_text,
        text=True,
        timeout=timeout,
    )


def command_error(argv: Sequence[str], result: subprocess.CompletedProcess[str]) -> str | None:
    if result.returncode == 0:
        return None
    diagnostic = (result.stderr or result.stdout).strip().splitlines()
    detail = diagnostic[0] if diagnostic else "no diagnostic"
    return f"{' '.join(argv)} exited {result.returncode}: {detail}"


__all__ = [
    "Assignment",
    "Finding",
    "LayerFile",
    "MergeRule",
    "TARGET_CLASSES",
    "TargetClass",
    "analyze",
    "command_error",
    "effective_files",
    "observe_layers",
    "run_command",
    "settings",
]
