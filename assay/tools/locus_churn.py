#!/usr/bin/env python3
"""Measure mutation-candidate churn between consecutive Git tags.

This is WP0 for the P4 sub-file candidate identity design.  It reads Python
source directly from Git objects, runs Assay's real Python mutation planner,
and reports how many candidates in the newer tag are re-keyed under:

* ``C`` -- today's whole-file candidate identity;
* ``A'`` -- the outermost enclosing function segment, with exact-id fallback;
* ``B(k)`` -- a ``site.lineno +/- k`` line window clipped to the function.

Function segments run from the first decorator ``@`` through the function's
final statement.  For the literal segment rule, comments after the last
statement of a def are outside the segment and never re-key it, even when they
change the whole-file identity.

The tool never checks out either tag and never writes into the inspected
repository.  It is offline and intended for small measurement runs; all
output is deterministic for the same arguments and Git objects.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from assay.adapters.python import PythonAdapter
from assay.mutation import (
    MUTATION_OPERATORS,
    MAX_CANDIDATE_CEILING,
    MutationTarget,
    UNSUPPORTED,
    candidate_id,
    collect_mutation_sites,
)


_GIT_TIMEOUT_SECONDS = 60
_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_LOCUS_LABEL = "assay-locus/1"
_SELF_TEST_OPERATORS = (
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)


class ChurnError(ValueError):
    """The requested churn measurement cannot produce a truthful answer."""


@dataclass(frozen=True)
class _Segment:
    qualname: str
    start_byte: int
    end_byte: int
    sha256: str
    ast_dump: str
    ambiguous: bool


@dataclass(frozen=True)
class _FileLocus:
    text: bytes
    line_starts: tuple[int, ...]
    segments: tuple[_Segment, ...]
    segment_starts: tuple[int, ...]


@dataclass(frozen=True)
class _Candidate:
    path: str
    start_byte: int
    end_byte: int
    lineno: int
    operator: str
    replacement_sha256: str
    candidate_id: str
    a_key: str | None
    b_keys: tuple[tuple[str, str, str, str, int], ...]
    qualname: str | None
    segment: _Segment | None


@dataclass(frozen=True)
class _Inventory:
    commit: str
    files: tuple[str, ...]
    loci: dict[str, _FileLocus]
    candidates: tuple[_Candidate, ...]
    candidate_ids: frozenset[str]
    a_keys: frozenset[str]
    b_keys: dict[int, frozenset[tuple[str, str, str, str, int]]]
    qualnames_by_path: dict[str, frozenset[str]]
    segments_by_qualname: dict[str, dict[str, tuple[_Segment, ...]]]


@dataclass(frozen=True)
class ReleaseMeasurement:
    previous_tag: str
    next_tag: str
    previous_commit: str
    next_commit: str
    candidates: int
    previous_candidates: int
    c_rekeyed: int
    a_rekeyed: int
    b_rekeyed: dict[int, int]
    fallback_candidates: int
    fallback_percent: float
    whitespace_only_rekeys: int
    new_function_candidates: int
    removed_function_candidates: int
    new_file_candidates: int
    removed_file_candidates: int
    files_touched: int
    lines_added: int


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(
    repo: Path,
    *argv: str,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    command = ["git", "-C", os.fspath(repo), *argv]
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=text,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = f": {exc}" if str(exc) else ""
        raise ChurnError(f"Git command failed ({' '.join(command)}){detail}") from exc


def _parse_relative_directory(value: str, name: str) -> PurePosixPath:
    if not value or "\0" in value or "\\" in value:
        raise ChurnError(f"{name} must be a canonical forward-slash relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ChurnError(
            f"{name} must be a canonical repo-relative path without '.', '..', "
            f"or redundant separators; got {value!r}"
        )
    return path


def _comma_items(value: str, name: str) -> tuple[str, ...]:
    items = tuple(value.split(","))
    if not items or any(not part or part.strip() != part for part in items):
        raise ChurnError(f"{name} must be a non-empty comma-separated list")
    if len(set(items)) != len(items):
        raise ChurnError(f"{name} contains a duplicate entry")
    return items


def _parse_k_values(value: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in _comma_items(value, "--k"):
        if not item.isascii() or not item.isdecimal():
            raise ChurnError(f"--k entries must be positive decimal integers, got {item!r}")
        number = int(item)
        if number < 1:
            raise ChurnError(f"--k entries must be positive, got {number}")
        values.append(number)
    return tuple(sorted(values))


def _resolve_tag(repo: Path, tag: str) -> str:
    if not tag or "\0" in tag or tag.startswith("-"):
        raise ChurnError(f"invalid tag {tag!r}")
    result = _git(repo, "rev-parse", "--verify", f"{tag}^{{commit}}")
    commit = result.stdout.strip()
    if len(commit) == 0:
        raise ChurnError(f"tag {tag!r} did not resolve to a commit")
    return commit


def _line_starts(data: bytes) -> tuple[int, ...]:
    starts = [0]
    starts.extend(index + 1 for index, byte in enumerate(data) if byte == 0x0A)
    return tuple(starts)


def _line_count(data: bytes, starts: tuple[int, ...]) -> int:
    return len(starts) - 1 if data.endswith(b"\n") else len(starts)


def _byte_offset(starts: tuple[int, ...], lineno: int, col_offset: int) -> int:
    if lineno < 1 or lineno > len(starts):
        raise ChurnError(f"AST position line {lineno} is outside the source")
    return starts[lineno - 1] + col_offset


def _decorator_offsets(data: bytes, starts: tuple[int, ...]) -> tuple[int, ...]:
    """Return byte offsets of ``@`` tokens that begin a logical source line."""
    try:
        text = data.decode("utf-8")
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        offsets: list[int] = []
        for token in tokens:
            if token.type != tokenize.OP or token.string != "@":
                continue
            prefix = token.line[: token.start[1]]
            if prefix.strip():
                continue
            offsets.append(starts[token.start[0] - 1] + len(prefix.encode("utf-8")))
    except (SyntaxError, tokenize.TokenError, UnicodeDecodeError) as exc:
        raise ChurnError(
            f"cannot tokenize Python source while locating decorators: {exc}"
        ) from exc
    return tuple(offsets)


def _decorator_start(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    starts: tuple[int, ...],
    decorator_offsets: tuple[int, ...],
) -> int:
    if not node.decorator_list:
        return _byte_offset(starts, node.lineno, node.col_offset)
    first = min(node.decorator_list, key=lambda item: (item.lineno, item.col_offset))
    first_position = _byte_offset(starts, first.lineno, first.col_offset)
    index = bisect.bisect_right(decorator_offsets, first_position) - 1
    if index < 0:
        raise ChurnError(
            f"cannot locate the '@' beginning decorator at line {first.lineno}"
        )
    return decorator_offsets[index]


def _collect_segments(tree: ast.Module, data: bytes) -> tuple[_Segment, ...]:
    starts = _line_starts(data)
    decorator_offsets = _decorator_offsets(data, starts)
    provisional: list[tuple[str, int, int, str, str]] = []

    def visit(node: ast.AST, classes: tuple[str, ...], in_function: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if in_function:
                return
            qualname = ".".join((*classes, node.name))
            start = _decorator_start(node, starts, decorator_offsets)
            if node.end_lineno is None or node.end_col_offset is None:
                raise ChurnError(f"function {qualname!r} has no complete AST span")
            end = _byte_offset(starts, node.end_lineno, node.end_col_offset)
            if end <= start or end > len(data):
                raise ChurnError(f"function {qualname!r} has an invalid byte span")
            segment = data[start:end]
            provisional.append(
                (
                    qualname,
                    start,
                    end,
                    hashlib.sha256(segment).hexdigest(),
                    ast.dump(node, include_attributes=False),
                )
            )
            return
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                visit(child, (*classes, node.name), in_function)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, classes, in_function)

    for child in tree.body:
        visit(child, (), False)

    counts: dict[tuple[str, str], int] = {}
    for qualname, _start, _end, digest, _dump in provisional:
        key = (qualname, digest)
        counts[key] = counts.get(key, 0) + 1
    return tuple(
        _Segment(
            qualname=qualname,
            start_byte=start,
            end_byte=end,
            sha256=digest,
            ast_dump=ast_dump,
            ambiguous=counts[(qualname, digest)] > 1,
        )
        for qualname, start, end, digest, ast_dump in provisional
    )


def _build_locus(path: str, text: str) -> _FileLocus:
    data = text.encode("utf-8")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        raise ChurnError(f"cannot parse Python source {path!r}: {exc}") from exc
    starts = _line_starts(data)
    segments = _collect_segments(tree, data)
    return _FileLocus(
        text=data,
        line_starts=starts,
        segments=segments,
        segment_starts=tuple(segment.start_byte for segment in segments),
    )


def _is_under_source_root(relative: str, source_root: str) -> bool:
    """Include Git's root spelling, needed by the in-process self-test only."""
    if source_root == ".":
        return not PurePosixPath(relative).is_absolute()
    return relative == source_root or relative.startswith(source_root + "/")


def _split_python_paths(raw: bytes, source_root: str) -> tuple[str, ...]:
    adapter = PythonAdapter()
    paths: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, object_type, _object_id = metadata.split(b" ", 2)
            relative = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ChurnError(f"cannot decode git ls-tree entry {entry!r}: {exc}") from exc
        if mode not in {b"100644", b"100755"} or object_type != b"blob":
            continue
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or not _is_under_source_root(relative, source_root)
            or not any(fnmatch.fnmatch(relative, pattern) for pattern in adapter.source_globs)
            or any(part in adapter.excluded_dir_names for part in pure.parts[:-1])
            or adapter.is_test_path(relative)
        ):
            continue
        paths.append(relative)
    if len(paths) != len(set(paths)):
        raise ChurnError(f"git ls-tree returned a duplicate source path under {source_root}")
    return tuple(sorted(paths))


def _python_paths(repo: Path, commit: str, source_root: str) -> tuple[str, ...]:
    listing = _git(
        repo,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
        f":(literal){source_root}",
        text=False,
    ).stdout
    return _split_python_paths(listing, source_root)


def _read_source(repo: Path, commit: str, path: str) -> str:
    raw = _git(repo, "show", f"{commit}:{path}", text=False).stdout
    if len(raw) > _MAX_SOURCE_BYTES:
        raise ChurnError(
            f"source {path!r} at {commit} exceeds the {_MAX_SOURCE_BYTES}-byte bound"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChurnError(f"source {path!r} at {commit} is not UTF-8: {exc}") from exc


def _locus_key(
    path: str,
    qualname: str,
    segment_sha256: str,
    rel_start: int,
    rel_end: int,
    operator: str,
    replacement_sha256: str,
) -> str:
    fields = (
        _LOCUS_LABEL,
        path,
        qualname,
        segment_sha256,
        str(rel_start),
        str(rel_end),
        operator,
        replacement_sha256,
    )
    if any("\0" in field for field in fields):
        raise ChurnError("locus key input contains NUL")
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def _window_key(
    candidate: Any,
    *,
    path: str,
    locus: _FileLocus,
    segment: _Segment | None,
    k: int,
) -> tuple[str, str, str, str, int]:
    data = locus.text
    starts = locus.line_starts
    total_lines = _line_count(data, starts)
    first_line = max(1, candidate.site.lineno - k)
    last_line = min(total_lines, candidate.site.lineno + k)
    window_start = starts[first_line - 1]
    if last_line < len(starts):
        window_end = starts[last_line]
    else:
        window_end = len(data)
    bound_start = segment.start_byte if segment is not None else 0
    bound_end = segment.end_byte if segment is not None else len(data)
    window_start = max(window_start, bound_start)
    window_end = min(window_end, bound_end)
    window_sha256 = hashlib.sha256(data[window_start:window_end]).hexdigest()
    return (
        path,
        window_sha256,
        candidate.site.operator,
        candidate.site.replacement_sha256,
        candidate.site.start_byte - window_start,
    )


def _candidate_context(
    candidate: Any,
    *,
    path: str,
    locus: _FileLocus,
    k_values: tuple[int, ...],
) -> tuple[
    str | None,
    tuple[tuple[str, str, str, str, int], ...],
    str | None,
    _Segment | None,
]:
    index = bisect.bisect_right(locus.segment_starts, candidate.site.start_byte) - 1
    segment = locus.segments[index] if index >= 0 else None
    if segment is not None and candidate.site.end_byte > segment.end_byte:
        segment = None
    qualname = segment.qualname if segment is not None else None
    if segment is not None and not segment.ambiguous:
        chosen = segment
        a_key = _locus_key(
            path,
            chosen.qualname,
            chosen.sha256,
            candidate.site.start_byte - chosen.start_byte,
            candidate.site.end_byte - chosen.start_byte,
            candidate.site.operator,
            candidate.site.replacement_sha256,
        )
    else:
        a_key = None
    b_keys = tuple(
        _window_key(
            candidate,
            path=path,
            locus=locus,
            segment=segment,
            k=k,
        )
        for k in k_values
    )
    return a_key, b_keys, qualname, segment


def _inventory(
    repo: Path,
    commit: str,
    *,
    source_root: str,
    operators: tuple[str, ...],
    k_values: tuple[int, ...],
) -> _Inventory:
    files = _python_paths(repo, commit, source_root)
    texts = {path: _read_source(repo, commit, path) for path in files}
    loci = {path: _build_locus(path, text) for path, text in texts.items()}
    targets: list[MutationTarget] = []
    for path in files:
        text = texts[path]
        count = text.count("\n") + (0 if text.endswith("\n") else 1)
        if count == 0:
            continue
        targets.append(
            MutationTarget(
                path=path,
                text=text,
                lines=frozenset(range(1, count + 1)),
            )
        )
    jobs = collect_mutation_sites(
        targets,
        adapter=PythonAdapter(),
        operators=operators,
        limit=MAX_CANDIDATE_CEILING,
    )
    if jobs == UNSUPPORTED:
        raise ChurnError("the Python adapter unexpectedly reported UNSUPPORTED")
    if len(jobs) >= MAX_CANDIDATE_CEILING:
        raise ChurnError(
            "candidate inventory reached Assay's planning ceiling "
            f"({MAX_CANDIDATE_CEILING}); the measurement could be truncated"
        )

    candidates: list[_Candidate] = []
    b_sets: dict[int, set[tuple[str, str, str, str, int]]] = {
        k: set() for k in k_values
    }
    qualnames_by_path: dict[str, frozenset[str]] = {}
    segments_by_qualname: dict[str, dict[str, tuple[_Segment, ...]]] = {}
    for path in files:
        grouped_segments = _segments_by_qualname(loci[path])
        segments_by_qualname[path] = grouped_segments
        qualnames_by_path[path] = frozenset(grouped_segments)
    for job in jobs:
        a_key, b_keys, qualname, segment = _candidate_context(
            job,
            path=job.path,
            locus=loci[job.path],
            k_values=k_values,
        )
        identity = candidate_id(job)
        candidates.append(
            _Candidate(
                path=job.path,
                start_byte=job.site.start_byte,
                end_byte=job.site.end_byte,
                lineno=job.site.lineno,
                operator=job.site.operator,
                replacement_sha256=job.site.replacement_sha256,
                candidate_id=identity,
                a_key=a_key,
                b_keys=b_keys,
                qualname=qualname,
                segment=segment,
            )
        )
        for key, value in zip(k_values, b_keys):
            b_sets[key].add(value)
    return _Inventory(
        commit=commit,
        files=files,
        loci=loci,
        candidates=tuple(candidates),
        candidate_ids=frozenset(candidate.candidate_id for candidate in candidates),
        a_keys=frozenset(
            candidate.a_key for candidate in candidates if candidate.a_key is not None
        ),
        b_keys={key: frozenset(value) for key, value in b_sets.items()},
        qualnames_by_path=qualnames_by_path,
        segments_by_qualname=segments_by_qualname,
    )


def _numstat(
    repo: Path,
    previous_commit: str,
    next_commit: str,
    source_root: str,
) -> tuple[int, int]:
    raw = _git(
        repo,
        "diff",
        "--numstat",
        "--no-renames",
        "-z",
        previous_commit,
        next_commit,
        "--",
        f":(literal){source_root}",
        text=False,
    ).stdout
    files = 0
    lines_added = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            added_raw, _deleted_raw, path_raw = record.split(b"\t", 2)
            path = path_raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ChurnError(f"cannot decode git numstat entry {record!r}: {exc}") from exc
        if not _is_under_source_root(path, source_root):
            raise ChurnError(
                f"git numstat returned {path!r} outside source root {source_root!r}"
            )
        files += 1
        if added_raw != b"-":
            try:
                lines_added += int(added_raw)
            except ValueError as exc:
                raise ChurnError(
                    f"git numstat returned invalid additions {added_raw!r} for {path!r}"
                ) from exc
    return files, lines_added


def _segments_by_qualname(locus: _FileLocus) -> dict[str, tuple[_Segment, ...]]:
    grouped: dict[str, list[_Segment]] = {}
    for segment in locus.segments:
        grouped.setdefault(segment.qualname, []).append(segment)
    return {qualname: tuple(items) for qualname, items in grouped.items()}


def _measure_pair(
    repo: Path,
    *,
    previous_tag: str,
    next_tag: str,
    previous: _Inventory,
    following: _Inventory,
    k_values: tuple[int, ...],
    source_root: str,
) -> ReleaseMeasurement:
    c_rekeyed = 0
    a_rekeyed = 0
    b_rekeyed = {k: 0 for k in k_values}
    fallback_candidates = 0
    whitespace_only_rekeys = 0
    new_function_candidates = 0
    removed_function_candidates = 0
    new_file_candidates = 0
    removed_file_candidates = 0

    previous_files = frozenset(previous.files)
    following_files = frozenset(following.files)
    for candidate in following.candidates:
        if candidate.candidate_id not in previous.candidate_ids:
            c_rekeyed += 1
        if candidate.a_key is None:
            fallback_candidates += 1
            if candidate.candidate_id not in previous.candidate_ids:
                a_rekeyed += 1
        elif candidate.a_key not in previous.a_keys:
            a_rekeyed += 1
            if candidate.qualname is not None:
                previous_segments = previous.segments_by_qualname.get(
                    candidate.path, {}
                ).get(candidate.qualname, ())
                if (
                    candidate.segment is not None
                    and len(previous_segments) == 1
                    and previous_segments[0].ast_dump
                    == candidate.segment.ast_dump
                ):
                    whitespace_only_rekeys += 1
        for index, k in enumerate(k_values):
            if candidate.b_keys[index] not in previous.b_keys[k]:
                b_rekeyed[k] += 1
        if candidate.qualname is not None:
            prior_qualnames = previous.qualnames_by_path.get(
                candidate.path, frozenset()
            )
            if candidate.qualname not in prior_qualnames:
                new_function_candidates += 1
        if candidate.path not in previous_files:
            new_file_candidates += 1

    for candidate in previous.candidates:
        if candidate.qualname is not None:
            following_qualnames = following.qualnames_by_path.get(
                candidate.path, frozenset()
            )
            if candidate.qualname not in following_qualnames:
                removed_function_candidates += 1
        if candidate.path not in following_files:
            removed_file_candidates += 1

    total = len(following.candidates)
    fallback_percent = (
        fallback_candidates * 100.0 / total if total else 0.0
    )
    files_touched, lines_added = _numstat(
        repo, previous.commit, following.commit, source_root
    )
    return ReleaseMeasurement(
        previous_tag=previous_tag,
        next_tag=next_tag,
        previous_commit=previous.commit,
        next_commit=following.commit,
        candidates=total,
        previous_candidates=len(previous.candidates),
        c_rekeyed=c_rekeyed,
        a_rekeyed=a_rekeyed,
        b_rekeyed=b_rekeyed,
        fallback_candidates=fallback_candidates,
        fallback_percent=fallback_percent,
        whitespace_only_rekeys=whitespace_only_rekeys,
        new_function_candidates=new_function_candidates,
        removed_function_candidates=removed_function_candidates,
        new_file_candidates=new_file_candidates,
        removed_file_candidates=removed_file_candidates,
        files_touched=files_touched,
        lines_added=lines_added,
    )


def _measure_release_pairs(
    repo: Path,
    *,
    project_dir: str,
    source_root: str,
    tags: Sequence[str],
    operators: tuple[str, ...],
    k_values: tuple[int, ...],
) -> tuple[ReleaseMeasurement, ...]:
    source_prefix = (PurePosixPath(project_dir) / source_root).as_posix()
    commits = tuple(_resolve_tag(repo, tag) for tag in tags)
    following = _inventory(
        repo,
        commits[0],
        source_root=source_prefix,
        operators=operators,
        k_values=k_values,
    )
    result: list[ReleaseMeasurement] = []
    for index in range(1, len(tags)):
        previous = following
        following = _inventory(
            repo,
            commits[index],
            source_root=source_prefix,
            operators=operators,
            k_values=k_values,
        )
        if not previous.files and not following.files:
            raise ChurnError(
                f"--source-root {source_root!r} under --project-dir {project_dir!r} "
                f"has no tracked Python files at either tag {tags[index - 1]!r} "
                f"or {tags[index]!r}"
            )
        result.append(
            _measure_pair(
                repo,
                previous_tag=tags[index - 1],
                next_tag=tags[index],
                previous=previous,
                following=following,
                k_values=k_values,
                source_root=source_prefix,
            )
        )
    return tuple(result)


def _render_markdown(
    measurements: Sequence[ReleaseMeasurement],
    k_values: tuple[int, ...],
) -> str:
    b_headers = [f"B{k}" for k in k_values]
    headers = [
        "release",
        "candidates",
        "C",
        "A'",
        *b_headers,
        "fallback%",
        "ws-only",
        "files",
        "added lines",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for measurement in measurements:
        values = [
            f"{measurement.previous_tag}..{measurement.next_tag}",
            str(measurement.candidates),
            str(measurement.c_rekeyed),
            str(measurement.a_rekeyed),
            *(str(measurement.b_rekeyed[k]) for k in k_values),
            f"{measurement.fallback_percent:.1f}%",
            str(measurement.whitespace_only_rekeys),
            str(measurement.files_touched),
            str(measurement.lines_added),
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _json_document(
    repo: Path,
    *,
    project_dir: str,
    source_root: str,
    tags: Sequence[str],
    operators: tuple[str, ...],
    k_values: tuple[int, ...],
    measurements: Sequence[ReleaseMeasurement],
) -> dict[str, Any]:
    return {
        "schema": "assay-locus-churn/1",
        "repo": os.fspath(repo),
        "project_dir": project_dir,
        "source_root": source_root,
        "tags": list(tags),
        "operators": list(operators),
        "k": list(k_values),
        "diff_metrics_scope": (
            "all git diff --numstat entries under project_dir/source_root; "
            "binary entries contribute zero added lines"
        ),
        "releases": [
            {
                "previous_tag": item.previous_tag,
                "next_tag": item.next_tag,
                "previous_commit": item.previous_commit,
                "next_commit": item.next_commit,
                "candidates": item.candidates,
                "previous_candidates": item.previous_candidates,
                "c_rekeyed": item.c_rekeyed,
                "a_prime_rekeyed": item.a_rekeyed,
                "b_rekeyed": {str(k): item.b_rekeyed[k] for k in k_values},
                "fallback_candidates": item.fallback_candidates,
                "fallback_percent": item.fallback_percent,
                "whitespace_only_rekeys": item.whitespace_only_rekeys,
                "new_function_candidates": item.new_function_candidates,
                "removed_function_candidates": item.removed_function_candidates,
                "new_file_candidates": item.new_file_candidates,
                "removed_file_candidates": item.removed_file_candidates,
                "files_touched": item.files_touched,
                "lines_added": item.lines_added,
            }
            for item in measurements
        ],
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    try:
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ChurnError(f"cannot write JSON report {path}: {exc}") from exc


def _self_test() -> None:
    help_text = " ".join(_parser().format_help().split())
    rule = (
        "comments after the last statement of a def are outside the segment "
        "and never re-key it."
    )
    if rule not in help_text:
        raise ChurnError(
            "self-test --help does not state the trailing-comment segment rule"
        )

    fixtures_t1 = {
        "edited_function.py": (
            "def edited(value):\n"
            "    return value < 1\n"
        ),
        "edited_elsewhere.py": (
            "def untouched(value):\n"
            "    return value == 2\n"
            "\n"
            "def edited_elsewhere(value):\n"
            "    return value > 3\n"
        ),
        "whitespace_only.py": (
            "def whitespace_only(value):\n"
            "    return value != 0\n"
        ),
        "parenthesized_decorator.py": (
            "@(\n"
            "    identity\n"
            ")\n"
            "def decorated(value):\n"
            "    return value < 4\n"
        ),
        "trailing_comment.py": (
            "def trailing_comment(value):\n"
            "    return value != 9\n"
        ),
    }
    fixtures_t2 = {
        "edited_function.py": (
            "def edited(value):\n"
            "    return value <= 1\n"
        ),
        "edited_elsewhere.py": (
            "def untouched(value):\n"
            "    return value == 2\n"
            "\n"
            "def edited_elsewhere(value):\n"
            "    return value >= 3\n"
        ),
        "whitespace_only.py": (
            "def whitespace_only(value):\n"
            "    # comment and whitespace only\n"
            "\n"
            "    return value != 0\n"
        ),
        "parenthesized_decorator.py": (
            "@(\n"
            "    identity\n"
            ")\n"
            "def decorated(value):\n"
            "    return value < 4\n"
        ),
        "trailing_comment.py": (
            "def trailing_comment(value):\n"
            "    return value != 9\n"
            "# trailing comment after the final statement\n"
        ),
        "one_sided/source.py": "value = 1\n",
    }

    decorated_locus = _build_locus(
        "parenthesized_decorator.py",
        fixtures_t1["parenthesized_decorator.py"],
    )
    if (
        len(decorated_locus.segments) != 1
        or decorated_locus.segments[0].start_byte != 0
    ):
        raise ChurnError(
            "self-test parenthesized decorator did not begin the function segment "
            "at the '@'"
        )

    trailing_before = _build_locus(
        "trailing_comment.py",
        fixtures_t1["trailing_comment.py"],
    )
    trailing_after = _build_locus(
        "trailing_comment.py",
        fixtures_t2["trailing_comment.py"],
    )
    if trailing_before.text == trailing_after.text:
        raise ChurnError("self-test trailing-comment fixture did not change its source")
    if len(trailing_before.segments) != 1 or len(trailing_after.segments) != 1:
        raise ChurnError(
            "self-test trailing-comment fixture has an unexpected segment count"
        )
    before_segment = trailing_before.segments[0]
    after_segment = trailing_after.segments[0]
    if before_segment.sha256 != after_segment.sha256:
        raise ChurnError(
            "self-test trailing comment re-keyed the function segment; "
            "it must be outside"
        )
    if b"# trailing comment" in trailing_after.text[
        after_segment.start_byte : after_segment.end_byte
    ]:
        raise ChurnError(
            "self-test trailing comment was included in the function segment"
        )

    with tempfile.TemporaryDirectory(prefix="assay-locus-churn-") as directory:
        repo = Path(directory)
        _git(repo, "init", "--quiet")
        for files, message, tag in (
            (fixtures_t1, "t1", "t1"),
            (fixtures_t2, "t2", "t2"),
        ):
            for relative, text in files.items():
                destination = repo / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8")
            _git(repo, "add", "-A")
            _git(
                repo,
                "-c",
                "user.name=Locus Churn Self Test",
                "-c",
                "user.email=locus-churn@example.invalid",
                "commit",
                "--quiet",
                "-m",
                message,
            )
            _git(repo, "tag", tag)
        measurements = _measure_release_pairs(
            repo,
            project_dir=".",
            source_root=".",
            tags=("t1", "t2"),
            operators=_SELF_TEST_OPERATORS,
            k_values=(2, 3),
        )
        one_sided_measurements = _measure_release_pairs(
            repo,
            project_dir=".",
            source_root="one_sided",
            tags=("t1", "t2"),
            operators=_SELF_TEST_OPERATORS,
            k_values=(2, 3),
        )
        if (
            len(one_sided_measurements) != 1
            or one_sided_measurements[0].previous_candidates != 0
        ):
            raise ChurnError(
                "self-test source root present at only the newer tag was not measured"
            )
        try:
            _measure_release_pairs(
                repo,
                project_dir=".",
                source_root="missing-root",
                tags=("t1", "t2"),
                operators=_SELF_TEST_OPERATORS,
                k_values=(2, 3),
            )
        except ChurnError as exc:
            message = str(exc)
            if (
                "'missing-root'" not in message
                or "'t1'" not in message
                or "'t2'" not in message
            ):
                raise ChurnError(
                    f"self-test empty source-root error omitted root or tags: {message}"
                ) from exc
        else:
            raise ChurnError(
                "self-test source root empty at both tags did not fail loudly"
            )

    expected_c = 5
    expected_a = 3
    expected_b2 = 3
    measurement = measurements[0]
    actual = (
        measurement.candidates,
        measurement.c_rekeyed,
        measurement.a_rekeyed,
        measurement.b_rekeyed[2],
    )
    # t2 has six compare-swap candidates: one each in edited_function.py,
    # whitespace_only.py, parenthesized_decorator.py, and trailing_comment.py,
    # plus two in edited_elsewhere.py. Five files change their whole-file SHA;
    # parenthesized_decorator.py is unchanged. C therefore re-keys five.
    # The trailing comment changes only bytes beyond the def's final statement:
    # C re-keys that function, while A' does not. A' and B(2) match the
    # untouched/unchanged functions exactly and re-key the other three.
    if not (
        measurement.candidates == 6
        and measurement.c_rekeyed == expected_c
        and measurement.a_rekeyed == expected_a
        and measurement.b_rekeyed[2] == expected_b2
    ):
        raise ChurnError(f"self-test metric mismatch: expected (6,5,3,3), got {actual}")
    if (
        measurement.fallback_candidates != 0
        or measurement.whitespace_only_rekeys != 1
    ):
        raise ChurnError(
            "self-test fallback/whitespace mismatch: expected 0 fallback and "
            f"1 whitespace-only re-key, got {measurement.fallback_candidates} "
            f"and {measurement.whitespace_only_rekeys}"
        )
    print(
        "PASS locus_churn self-test: "
        f"candidates={measurement.candidates} C={measurement.c_rekeyed} "
        f"A'={measurement.a_rekeyed} B2={measurement.b_rekeyed[2]}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Segment rule: comments after the last statement of a def are outside "
            "the segment and never re-key it."
        ),
    )
    parser.add_argument("--repo", type=Path, help="Git repository root")
    parser.add_argument("--project-dir", help="project directory relative to repo")
    parser.add_argument("--source-root", help="source root relative to project dir")
    parser.add_argument(
        "--tags",
        help="comma-separated tags in oldest-to-newest order",
    )
    parser.add_argument(
        "--operators",
        help="comma-separated Assay Python mutation operators",
    )
    parser.add_argument("--k", default="2,3,5", help="comma-separated line radii")
    parser.add_argument("--json", type=Path, help="write the detailed JSON report")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="build a throwaway Git fixture and check known churn counts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_test:
        try:
            _self_test()
        except (ChurnError, OSError, ValueError) as exc:
            print(f"FAIL locus_churn self-test: {exc}", file=sys.stderr)
            return 1
        return 0
    missing = [
        option
        for option, value in (
            ("--repo", args.repo),
            ("--project-dir", args.project_dir),
            ("--source-root", args.source_root),
            ("--tags", args.tags),
            ("--operators", args.operators),
        )
        if value in {None, ""}
    ]
    if missing:
        print(
            "locus_churn: missing required option(s): " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    try:
        repo = args.repo.resolve(strict=True)
        project_dir = _parse_relative_directory(args.project_dir, "--project-dir")
        source_root = _parse_relative_directory(args.source_root, "--source-root")
        tags = _comma_items(args.tags, "--tags")
        if len(tags) < 2:
            raise ChurnError("--tags requires at least two tags")
        operators = _comma_items(args.operators, "--operators")
        unknown = tuple(item for item in operators if item not in MUTATION_OPERATORS)
        if unknown:
            raise ChurnError(
                f"--operators names unknown operator(s) {list(unknown)}; "
                f"the catalogue is {list(MUTATION_OPERATORS)}"
            )
        k_values = _parse_k_values(args.k)
        measurements = _measure_release_pairs(
            repo,
            project_dir=project_dir.as_posix(),
            source_root=source_root.as_posix(),
            tags=tags,
            operators=operators,
            k_values=k_values,
        )
        document = _json_document(
            repo,
            project_dir=project_dir.as_posix(),
            source_root=source_root.as_posix(),
            tags=tags,
            operators=operators,
            k_values=k_values,
            measurements=measurements,
        )
        if args.json is not None:
            _write_json(args.json, document)
        print(_render_markdown(measurements, k_values))
    except (ChurnError, OSError, ValueError) as exc:
        print(f"locus_churn: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
