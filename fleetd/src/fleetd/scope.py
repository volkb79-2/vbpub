"""The R2 default scope: candidate ids from the changed lines since a release.

Two declared scopes exist, and each produces a *candidate-id set* handed to
Run-Gate as ``--only-candidates FILE``:

``changed-lines`` (the default)
    A candidate belongs to the scope when its start line is an added line of
    ``git diff -U0 <previous release tag>..<commit>``. A file the release did
    not touch contributes nothing, and its candidates are never part of the
    set -- a stale, hand-prepared id file that still lists them is the failure
    this producer exists to end.

``rolling-whole-target`` (per-tool opt-in)
    Every release judges the next slice of the whole-target candidate list,
    taken from a persisted, versioned cursor, so successive releases cover the
    target without re-judging the whole list at once. The cursor records the
    digest and length of the candidate list it was taken from; a list whose
    digest no longer matches is REFUSED, never resumed at an offset that now
    points at a different candidate.

An empty scope is an explicit ``nothing-to-judge`` result carrying its reason,
never a silent campaign that judged nothing: this module refuses to write an
id file for it and the CLI exits non-zero with the document on stdout.

A renamed path is judged WHOLE. Candidate identity folds in the repo-top
relative path (``assay.candidate_identity.candidate_id_from_fields``), so every
candidate in a renamed file is an identity that has never been judged -- a
rename with no content change produces no hunk at all, and filtering added
lines alone would silently drop the file.

This module deliberately reads plain JSON and plain ``git``: the candidate list
comes from ``assay plan <lane> --json`` and the diff from ``git diff``. It
imports no judge internals, so a run-gate lane, a cmru release step and a
fleetd campaign can all call the same committed producer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ContractError, is_candidate_id

#: The closed scope vocabulary: a tool declares one of these, nothing else.
SCOPE_MODES = ("changed-lines", "rolling-whole-target")
DEFAULT_SCOPE_MODE = "changed-lines"
ROLLING_MODE = "rolling-whole-target"

SCOPE_SCHEMA = "fleet-r2-scope/1"
CURSOR_SCHEMA = "fleet-r2-scope-cursor/1"
SCOPE_FILE_SCHEMA_VERSION = 1

#: Exit statuses of ``fleet scope`` (documented in docs/CONSUMERS.md).
EXIT_OK = 0
EXIT_STALE_CURSOR = 3
EXIT_NOTHING_TO_JUDGE = 4

_HUNK = re.compile(
    r"^@@ -\d+(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class AddedLines:
    """New-side added lines of one changed file, or its whole-file rename."""

    lines: frozenset[int]
    whole_file: bool


def candidate_list_digest(candidate_ids: Sequence[str]) -> str:
    """Return the canonical digest of a candidate-id set.

    One id per line, sorted -- exactly the content a runner job's
    ``list.sha256`` folds, so a cursor records the same number the campaign's
    own id file does.
    """
    content = "".join(f"{candidate_id}\n" for candidate_id in sorted(candidate_ids))
    return hashlib.sha256(content.encode("ascii")).hexdigest()


def parse_added_lines(text: str) -> dict[str, AddedLines]:
    """Parse ``git diff --unified=0`` text into per-file added-line sets.

    A real state machine, not a header regex: a hunk header's own ``-old_count
    / +new_count`` is the only thing that closes a hunk, so a body line whose
    CONTENT begins with ``++`` (patch text ``+++ value``) can never be
    misread as a file header.
    """
    added: dict[str, set[int]] = {}
    whole: set[str] = set()
    current = ""
    old_remaining = 0
    new_remaining = 0
    new_line = 0
    # Split on "\n" ONLY. ``str.splitlines`` also splits on form feed, vertical
    # tab, U+2028/U+2029, U+0085 and the \x1c-\x1e separators, any of which can
    # sit inside one added source line: every extra piece would be counted
    # against the hunk's line budget, shift every later line number and
    # desynchronise the state machine, silently dropping later files.
    for line in text.split("\n"):
        if old_remaining or new_remaining:
            if line.startswith("\\"):
                # `` No newline at end of file`` is a marker, not a body line.
                continue
            if line.startswith("-"):
                old_remaining -= 1
            elif line.startswith("+"):
                added.setdefault(current, set()).add(new_line)
                new_remaining -= 1
                new_line += 1
            else:
                old_remaining -= 1
                new_remaining -= 1
                new_line += 1
            continue
        if line.startswith("diff --git "):
            current = ""
            continue
        if line.startswith("rename to "):
            current = line[len("rename to "):]
            whole.add(current)
            continue
        if line.startswith("+++ "):
            target = line[4:]
            # Git appends a TAB to the file name of a path that contains a
            # space; a TAB cannot be part of a name that is printed unquoted.
            target = target.rstrip("\t")
            current = "" if target == "/dev/null" else (
                target[2:] if target.startswith("b/") else target
            )
            continue
        match = _HUNK.match(line)
        if match:
            old_remaining = int(match.group("old_count") or 1)
            new_remaining = int(match.group("new_count") or 1)
            new_line = int(match.group("new_start"))
    return {
        path: AddedLines(frozenset(added.get(path, ())), path in whole)
        for path in set(added) | whole
    }


def _git(repo: Path, *args: str) -> str:
    """Run one read-only git command and return its stripped stdout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
            # Bytes, decoded below: ``text=True`` would fail on a changed file
            # that is not UTF-8 and would translate a lone carriage return in
            # a source line into an extra line.
            check=False, capture_output=True,
        )
    except OSError as exc:
        raise ContractError(f"git {args[0]}: cannot be started: {exc}") from exc
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        detail = (stderr or stdout or "no diagnostic output").strip()
        raise ContractError(f"git {args[0]} failed with {result.returncode}: {detail[:300]}")
    return stdout.strip("\n") if args[0] == "diff" else stdout.strip()


def resolve_commit(repo: Path) -> str:
    """Return the repository's current commit id."""
    return _git(repo, "rev-parse", "HEAD")


def refuse_unpinned_checkout(repo: Path, commit: str) -> None:
    """Refuse a checkout that is not exactly the commit the scope names.

    The candidate list comes from ``assay plan --json``, which reads the
    WORKING TREE, while the diff describes the COMMITTED tree. With uncommitted
    changes to tracked files, or with a checkout at another commit, the two
    describe different trees and every candidate line number can be wrong; the
    result would be a valid-looking scope about a tree nobody judges.
    Untracked files do not move a tracked line and are not counted.
    """
    head = resolve_commit(repo)
    if head != commit:
        raise ContractError(
            f"--commit {commit[:12]} is not the checkout's HEAD {head[:12]}: the "
            "candidate plan describes the working tree, so check the commit out first"
        )
    changed = _git(repo, "status", "--porcelain=v1", "--untracked-files=no")
    if changed:
        raise ContractError(
            "the checkout has uncommitted changes to tracked files "
            f"({len(changed.splitlines())} path(s)); the candidate plan and the "
            "release diff would describe different trees. Commit or stash first"
        )


def release_diff(repo: Path, from_tag: str, commit: str) -> str:
    """Return the ``-U0`` diff from *from_tag* to *commit*, renames detected."""
    if not isinstance(from_tag, str) or not from_tag.strip():
        raise ContractError("--from-tag: expected a non-empty release tag")
    if _COMMIT.fullmatch(commit) is None:
        raise ContractError("--commit: expected a full lowercase Git object id")
    return _git(
        repo,
        "diff", "--unified=0", "--no-color", "--no-ext-diff", "--find-renames",
        f"{from_tag}..{commit}",
    )


def plan_candidates(plan: object) -> tuple[dict[str, Any], ...]:
    """Validate ``assay plan --json`` output and return its candidate rows."""
    if not isinstance(plan, Mapping):
        raise ContractError("plan: expected a JSON object from `assay plan --json`")
    if plan.get("status") != "ok":
        raise ContractError(
            f"plan: status {plan.get('status')!r} has no candidate list "
            f"({plan.get('reason') or 'no reason given'})"
        )
    rows = plan.get("candidates")
    if not isinstance(rows, list):
        raise ContractError("plan.candidates: expected an array")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        where = f"plan.candidates[{index}]"
        if not isinstance(row, Mapping):
            raise ContractError(f"{where}: expected an object")
        candidate_id = row.get("id")
        if not is_candidate_id(candidate_id):
            raise ContractError(
                f"{where}.id: expected a 64-character lowercase SHA-256 candidate id"
            )
        if candidate_id in seen:
            raise ContractError(f"plan.candidates: duplicate id {candidate_id!r}")
        seen.add(candidate_id)
        path = row.get("path")
        if not isinstance(path, str) or not path:
            raise ContractError(f"{where}.path: expected a non-empty repo-relative path")
        lineno = row.get("lineno")
        if isinstance(lineno, bool) or not isinstance(lineno, int) or lineno < 1:
            raise ContractError(f"{where}.lineno: expected a positive line number")
        candidates.append({"id": candidate_id, "path": path, "lineno": lineno})
    return tuple(candidates)


def select_changed_lines(
    candidates: Sequence[Mapping[str, Any]],
    added: Mapping[str, AddedLines],
) -> tuple[list[str], dict[str, int]]:
    """Return the candidates on added (or whole-renamed) lines, plus drop counts."""
    selected: list[str] = []
    unchanged_files = 0
    unchanged_lines = 0
    for candidate in candidates:
        entry = added.get(candidate["path"])
        if entry is None:
            unchanged_files += 1
        elif entry.whole_file or candidate["lineno"] in entry.lines:
            selected.append(candidate["id"])
        else:
            unchanged_lines += 1
    return sorted(selected), {
        "files_unchanged": unchanged_files,
        "lines_unchanged": unchanged_lines,
    }


# ---------------------------------------------------------------- scope file
_SCOPE_FILE_KEYS = {"schema_version", "r2"}
_SCOPE_R2_KEYS = {"mode", "slice_size", "cursor", "note"}


def load_scope_file(path: Path) -> dict[str, Any]:
    """Read a project's declared R2 scope (``r2-scope.toml``) or ``{}``.

    A tool opts in by committing this file; a tool that commits nothing keeps
    the ``changed-lines`` default, so no existing lane changes behaviour.
    """
    source = Path(path)
    if not source.is_file():
        return {"mode": None, "slice_size": None, "cursor": None, "note": None, "path": None}
    try:
        document = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"scope file {source}: cannot be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"scope file {source}: is not valid TOML: {exc}") from exc
    extra = sorted(set(document) - _SCOPE_FILE_KEYS)
    if extra:
        raise ContractError(f"scope file {source}: unknown field(s): {', '.join(extra)}")
    if document.get("schema_version") != SCOPE_FILE_SCHEMA_VERSION:
        raise ContractError(
            f"scope file {source}: schema_version must be {SCOPE_FILE_SCHEMA_VERSION}"
        )
    table = document.get("r2")
    if not isinstance(table, dict):
        raise ContractError(f"scope file {source}: expected an [r2] table")
    extra = sorted(set(table) - _SCOPE_R2_KEYS)
    if extra:
        raise ContractError(f"scope file {source}: [r2] unknown field(s): {', '.join(extra)}")
    mode = table.get("mode")
    if mode not in SCOPE_MODES:
        raise ContractError(
            f"scope file {source}: r2.mode must be one of {', '.join(SCOPE_MODES)}"
        )
    slice_size = table.get("slice_size")
    if slice_size is not None and (
        isinstance(slice_size, bool) or not isinstance(slice_size, int) or slice_size < 1
    ):
        raise ContractError(f"scope file {source}: r2.slice_size must be an integer of at least 1")
    if mode == ROLLING_MODE and slice_size is None:
        raise ContractError(
            f"scope file {source}: r2.mode {ROLLING_MODE} requires r2.slice_size"
        )
    cursor = table.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ContractError(f"scope file {source}: r2.cursor must be a non-empty path")
    note = table.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ContractError(f"scope file {source}: r2.note must be non-empty text")
    return {
        "mode": mode,
        "slice_size": slice_size,
        "cursor": cursor,
        "note": note,
        "path": str(source),
    }


# -------------------------------------------------------------------- cursor
_CURSOR_KEYS = {
    "schema", "mode", "lane", "slice_size", "offset", "pass_index",
    "list_count", "list_digest", "slice_digest", "judged_commits",
}


def _non_negative(directory: Mapping[str, Any], key: str, where: str) -> int:
    value = directory.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{where}.{key}: expected a non-negative integer")
    return value


def _digest_value(directory: Mapping[str, Any], key: str, where: str) -> str:
    value = directory.get(key)
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ContractError(f"{where}.{key}: expected a lowercase SHA-256 digest")
    return value


def validate_cursor(document: object, where: str = "cursor") -> dict[str, Any]:
    """Validate one ``fleet-r2-scope-cursor/1`` document."""
    if not isinstance(document, dict) or document.get("schema") != CURSOR_SCHEMA:
        raise ContractError(f"{where}: expected schema {CURSOR_SCHEMA}")
    extra = sorted(set(document) - _CURSOR_KEYS)
    if extra:
        raise ContractError(f"{where}: unknown field(s): {', '.join(extra)}")
    missing = sorted(_CURSOR_KEYS - set(document))
    if missing:
        raise ContractError(f"{where}: missing field(s): {', '.join(missing)}")
    if document["mode"] != ROLLING_MODE:
        raise ContractError(f"{where}.mode: expected {ROLLING_MODE}")
    if not isinstance(document["lane"], str) or not document["lane"]:
        raise ContractError(f"{where}.lane: expected a non-empty lane name")
    for key in ("slice_size", "offset", "pass_index", "list_count"):
        _non_negative(document, key, where)
    if document["slice_size"] < 1:
        raise ContractError(f"{where}.slice_size: expected at least 1")
    commits = document["judged_commits"]
    if not isinstance(commits, list) or any(
        not isinstance(item, str) or _COMMIT.fullmatch(item) is None for item in commits
    ):
        raise ContractError(f"{where}.judged_commits: expected full Git object ids")
    _digest_value(document, "list_digest", where)
    _digest_value(document, "slice_digest", where)
    return dict(document)


def read_cursor(path: Path) -> dict[str, Any]:
    """Read and strictly validate a ``fleet-r2-scope-cursor/1`` file."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cursor {source}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"cursor {source}: is not valid JSON: {exc}") from exc
    return validate_cursor(document, f"cursor {source}")


def write_cursor(path: Path, document: Mapping[str, Any]) -> None:
    """Validate and atomically replace the cursor file with *document*."""
    target = Path(path)
    validated = validate_cursor(document, f"cursor {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)


@dataclass(frozen=True)
class RollingSlice:
    """One rolling slice plus the cursor position it advances to."""

    selected: tuple[str, ...]
    offset: int
    pass_index: int
    list_count: int
    list_digest: str
    slice_digest: str
    stale_reason: str | None


def rolling_slice(
    candidate_ids: Sequence[str],
    *,
    slice_size: int,
    cursor: Mapping[str, Any] | None,
    rebaseline: bool = False,
) -> RollingSlice:
    """Take the next slice of the rolling whole-target list.

    A cursor whose list digest, length or slice size no longer describes this
    invocation is REFUSED (``stale_reason``) unless the caller explicitly
    rebaselines. A list whose slices are exhausted starts a new pass at offset
    0, so a rolling baseline never stops covering the target.
    """
    if isinstance(slice_size, bool) or not isinstance(slice_size, int) or slice_size < 1:
        raise ContractError("--slice-size: expected an integer of at least 1")
    ordered = tuple(sorted(candidate_ids))
    digest = candidate_list_digest(ordered)
    offset = 0
    pass_index = 0
    stale: str | None = None
    if cursor is not None:
        if cursor["list_digest"] != digest:
            stale = (
                "the whole-target candidate list changed since the cursor was "
                f"written (cursor {cursor['list_digest'][:12]}, now {digest[:12]})"
            )
        elif cursor["list_count"] != len(ordered):
            stale = (
                f"the cursor was taken from a list of {cursor['list_count']} "
                f"candidates, this list has {len(ordered)}"
            )
        elif cursor["slice_size"] != slice_size:
            stale = "the slice size changed since the cursor was written"
        elif cursor["offset"] > len(ordered):
            stale = "the cursor offset is past the end of the candidate list"
        if stale is not None and not rebaseline:
            return RollingSlice(
                (), cursor["offset"], cursor["pass_index"], len(ordered), digest, "", stale
            )
        if stale is None:
            offset = cursor["offset"]
            pass_index = cursor["pass_index"]
    if offset >= len(ordered):
        offset = 0
        pass_index = pass_index + 1 if cursor is not None else 0
    selected = ordered[offset:offset + slice_size]
    return RollingSlice(
        selected,
        offset + len(selected),
        pass_index,
        len(ordered),
        digest,
        candidate_list_digest(selected),
        None,
    )


def cursor_document(
    *,
    lane: str,
    slice_size: int,
    result: RollingSlice,
    previous: Mapping[str, Any] | None,
    commit: str,
) -> dict[str, Any]:
    """Return the cursor document that records *result*'s slice."""
    commits = [] if previous is None else list(previous["judged_commits"])
    if commit not in commits:
        commits.append(commit)
    return {
        "schema": CURSOR_SCHEMA,
        "mode": ROLLING_MODE,
        "lane": lane,
        "slice_size": slice_size,
        "offset": result.offset,
        "pass_index": result.pass_index,
        "list_count": result.list_count,
        "list_digest": result.list_digest,
        "slice_digest": result.slice_digest,
        "judged_commits": commits,
    }


def scope_document(
    *,
    mode: str,
    mode_source: str,
    lane: str,
    from_tag: str,
    commit: str,
    plan: Mapping[str, Any],
    diff_text: str = "",
    slice_size: int | None = None,
    cursor: Mapping[str, Any] | None = None,
    rebaseline: bool = False,
) -> dict[str, Any]:
    """Build the ``fleet-r2-scope/1`` document for one invocation."""
    if mode not in SCOPE_MODES:
        raise ContractError(f"scope mode: expected one of {', '.join(SCOPE_MODES)}")
    candidates = plan_candidates(plan)
    document: dict[str, Any] = {
        "schema": SCOPE_SCHEMA,
        "mode": mode,
        "mode_source": mode_source,
        "lane": lane,
        "from_tag": from_tag,
        "commit": commit,
        "candidates_total": len(candidates),
    }
    if mode == DEFAULT_SCOPE_MODE:
        added = parse_added_lines(diff_text)
        selected, counts = select_changed_lines(candidates, added)
        document.update({
            "files_changed": len(added),
            "files_renamed": sum(1 for entry in added.values() if entry.whole_file),
            **counts,
            "candidate_ids": selected,
            "slice": None,
            "cursor": None,
        })
        if selected:
            document["status"] = "ok"
            document["reason"] = None
            document["candidate_file_sha256"] = candidate_list_digest(selected)
        else:
            document["status"] = "nothing-to-judge"
            document["reason"] = (
                f"no candidate starts on a line added since {from_tag}; "
                "the release changed no mutation target line"
            )
        return document
    if slice_size is None:
        raise ContractError(
            f"scope mode {mode}: --slice-size (or the scope file's r2.slice_size) is required"
        )
    result = rolling_slice(
        [row["id"] for row in candidates],
        slice_size=slice_size,
        cursor=cursor,
        rebaseline=rebaseline,
    )
    document.update({
        "files_changed": None,
        "files_renamed": None,
        "files_unchanged": None,
        "lines_unchanged": None,
        "candidate_ids": list(result.selected),
        "slice": {
            "offset": result.offset,
            "pass_index": result.pass_index,
            "list_count": result.list_count,
            "list_digest": result.list_digest,
            "slice_digest": result.slice_digest,
            "stale_reason": result.stale_reason,
            "rebaselined": rebaseline,
        },
        "cursor": None,
    })
    if result.stale_reason is not None:
        document["status"] = "stale-cursor"
        document["reason"] = result.stale_reason
        return document
    document["cursor"] = cursor_document(
        lane=lane,
        slice_size=slice_size,
        result=result,
        previous=cursor,
        commit=commit,
    )
    if result.selected:
        document["status"] = "ok"
        document["reason"] = None
        document["candidate_file_sha256"] = candidate_list_digest(result.selected)
    else:
        document["status"] = "nothing-to-judge"
        document["reason"] = "the whole-target candidate list is empty"
    return document
