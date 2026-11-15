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
    Every release judges the release's own changed candidates *plus* the next
    slice of the sweep over the whole target, so successive releases cover the
    target without re-judging the whole list at once (FD-033).

The sweep is keyed by candidate CONTENT, never by Assay's candidate id: an id
folds in the source file's SHA-256 (``assay/candidate_identity.py``), so every
edit to a target file would otherwise rotate the whole resume key. A
candidate's stable key here is ``(repo-relative path, mutation operator,
ordinal within that path's operator group)`` -- location-stable for as long as
the file's content is. The cursor records, per path, the *git blob id* of the
content those marks belong to plus the number of planned candidates and the
judged keys:

* a path whose current blob differs from its mark is **due again immediately**
  (every candidate in it), which is a superset of the changed-lines scope: a
  changed file is judged whole because a site identity (byte span, replacement,
  description) shifts when anything before it moves;
* a path whose blob still matches keeps its judged keys: those candidates are
  not due, so a sweep is never restarted by a release that edited something
  else;
* a mark that cannot be confirmed against the current plan (a different
  planned candidate count, or a judged key that the plan no longer contains,
  which is what an operator-selection change looks like) makes the whole path
  due again and is reported as such -- it is never silently dropped, and it
  can never mark an unjudged candidate as judged;
* a path that no longer has a planned candidate is recorded as **retired** with
  a reason.

A sweep therefore means "changed lines since the last release, plus the next
slice of the target nobody has judged recently". ``--rebaseline`` is the
explicit discard (start one new pass from the current content) and the
migration for a ``fleet-r2-scope-cursor/1`` cursor, which keyed its resume
position to an id list this one does not use.

The sweep order is deterministic: candidates of files whose content changed
since their mark first (least recently judged first), then the never-judged and
not-yet-judged-in-this-content candidates in path/line order. When every
planned candidate has been judged at its current content, the next release
starts a new pass, least recently judged first. A slice is a *prefix* of that
order, so the target is swept in ``ceil(N / slice_size)`` releases with no
edits and no candidate can be starved.

An empty scope is an explicit ``nothing-to-judge`` result carrying its reason,
never a silent campaign that judged nothing: this module refuses to write an
id file for it and the CLI exits non-zero with the document on stdout. In
rolling mode that status means exactly one thing -- the plan is empty -- because
"every candidate has been judged at its current content" is a pass boundary:
the next release starts a recorded new pass (``new_pass``) instead of returning
an empty slice that would look like coverage.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ContractError, is_candidate_id

#: The closed scope vocabulary: a tool declares one of these, nothing else.
SCOPE_MODES = ("changed-lines", "rolling-whole-target")
DEFAULT_SCOPE_MODE = "changed-lines"
ROLLING_MODE = "rolling-whole-target"

SCOPE_SCHEMA = "fleet-r2-scope/1"
CURSOR_SCHEMA = "fleet-r2-scope-cursor/2"
#: Cursors this producer no longer accepts; each is refused with its migration.
LEGACY_CURSOR_SCHEMAS = ("fleet-r2-scope-cursor/1",)
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


@dataclass(frozen=True)
class PlannedCandidate:
    """One planned mutation site, plus the identity the sweep keys it by.

    ``key`` is empty until :func:`assign_site_keys` derives it; the sweep
    refuses to run on a candidate without one rather than inventing an order.
    """

    id: str
    path: str
    lineno: int
    operator: str = ""
    key: str = ""


def candidate_list_digest(candidate_ids: Sequence[str]) -> str:
    """Return the canonical digest of a candidate-id set.

    One id per line, sorted -- exactly the content a runner job's
    ``list.sha256`` folds, so a scope records the same number the campaign's
    own id file does.
    """
    content = "".join(f"{candidate_id}\n" for candidate_id in sorted(candidate_ids))
    return hashlib.sha256(content.encode("ascii")).hexdigest()


def candidate_key(path: str, operator: str, ordinal: int) -> str:
    """Return the stable sweep identity of a planned site.

    ``path`` + ``operator`` + the site's zero-based ordinal among that path's
    candidates of the same operator (ordered by line, then id) is location
    stable for a fixed file content, and independent of Assay's candidate id
    (which folds the file's hash). It is deliberately NOT expected to survive
    an edit of its own file: the cursor invalidates a whole path whose blob
    changed instead, and documents that rule.
    """
    if not isinstance(path, str) or not path:
        raise ContractError("candidate key: expected a non-empty repo-relative path")
    if not isinstance(operator, str) or not operator:
        raise ContractError("candidate key: expected a non-empty mutation operator")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ContractError("candidate key: expected a non-negative site ordinal")
    identity = "\0".join((path, operator, str(ordinal)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def assign_site_keys(
    candidates: Sequence[PlannedCandidate],
) -> dict[str, list[PlannedCandidate]]:
    """Derive every candidate's key and return them grouped per path.

    Ordinals count within one ``(path, operator)`` group in line order, so the
    key of a site depends on its file's content and its operator, never on the
    list's global order. Each path's rows are then returned in line/id order --
    the order a slice is taken in, so a slice of one file is always a prefix.
    """
    ordinal_by_group: dict[tuple[str, str], int] = {}
    keyed: list[PlannedCandidate] = []
    for candidate in sorted(candidates, key=lambda row: (row.path, row.operator, row.lineno, row.id)):
        group = (candidate.path, candidate.operator)
        ordinal = ordinal_by_group.get(group, 0)
        ordinal_by_group[group] = ordinal + 1
        keyed.append(replace(candidate, key=candidate_key(candidate.path, candidate.operator, ordinal)))
    by_path: dict[str, list[PlannedCandidate]] = {}
    for candidate in keyed:
        by_path.setdefault(candidate.path, []).append(candidate)
    for path, rows in by_path.items():
        rows.sort(key=lambda row: (row.lineno, row.id))
        by_path[path] = rows
    return by_path


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


def _git(repo: Path, *args: str, strip: bool = True) -> str:
    """Run one read-only git command and return its stdout."""
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
    return stdout.strip("\n") if args[0] == "diff" else (stdout.strip() if strip else stdout)


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


def tree_blobs(repo: Path, commit: str) -> dict[str, str]:
    """Return ``{path: object id}`` for every file of *commit*'s tree.

    ``-z`` keeps a path with a newline or a tab parseable: the record is
    ``<mode> <type> <oid>\\t<path>\\0`` and only the FIRST tab separates the
    header from the path. A gitlink (submodule) is not a file this producer can
    key a mutation site to, so it is skipped rather than mapped to a commit id.
    """
    if _COMMIT.fullmatch(commit) is None:
        raise ContractError("--commit: expected a full lowercase Git object id")
    text = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, strip=False)
    blobs: dict[str, str] = {}
    for record in text.split("\0"):
        if not record:
            continue
        header, separator, path = record.partition("\t")
        fields = header.split(" ")
        if not separator or len(fields) != 3:
            raise ContractError(f"git ls-tree: unreadable record {record[:120]!r}")
        if fields[1] != "blob":
            continue
        blobs[path] = fields[2]
    return blobs


def planned_file_blobs(repo: Path, commit: str, paths: Sequence[str]) -> dict[str, str]:
    """Return the blob id of every planned target path at *commit*.

    A plan row whose path the commit does not contain is REFUSED: the sweep
    keys a candidate to its file's content, so a planned path whose content at
    the judged commit cannot be determined is a fact this producer does not
    have -- not one it may guess (an untracked target file, or a plan produced
    in another checkout).
    """
    blobs = tree_blobs(repo, commit)
    missing = sorted(set(paths) - set(blobs))
    if missing:
        raise ContractError(
            f"rolling scope: {len(missing)} planned path(s) are not files in commit "
            f"{commit[:12]} (first: {missing[0]!r}); the sweep keys every candidate "
            "to its file's content at that commit, so such a path cannot be swept"
        )
    return {path: blobs[path] for path in sorted(set(paths))}


def plan_candidates(
    plan: object, *, require_site_identity: bool = False
) -> tuple[PlannedCandidate, ...]:
    """Validate ``assay plan --json`` output and return its candidate rows.

    ``require_site_identity`` asks for the mutation operator as well: the
    rolling sweep keys a site by it, while the changed-lines scope needs only
    the path and the line.
    """
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
    candidates: list[PlannedCandidate] = []
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
        operator = row.get("operator")
        if operator is None:
            if require_site_identity:
                raise ContractError(
                    f"{where}.operator: a rolling scope keys each candidate by its "
                    "mutation operator, and this row has none"
                )
            operator = ""
        elif not isinstance(operator, str) or not operator:
            raise ContractError(f"{where}.operator: expected a non-empty operator name")
        candidates.append(PlannedCandidate(candidate_id, path, lineno, operator))
    return tuple(candidates)


def select_changed_lines(
    candidates: Sequence[PlannedCandidate],
    added: Mapping[str, AddedLines],
) -> tuple[list[str], dict[str, int]]:
    """Return the candidates on added (or whole-renamed) lines, plus drop counts."""
    selected: list[str] = []
    unchanged_files = 0
    unchanged_lines = 0
    for candidate in candidates:
        entry = added.get(candidate.path)
        if entry is None:
            unchanged_files += 1
        elif entry.whole_file or candidate.lineno in entry.lines:
            selected.append(candidate.id)
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
    "schema", "mode", "lane", "slice_size", "pass_index", "write_index",
    "commit", "list_count", "list_digest", "files", "judged_commits",
    "state_digest",
}
_MARK_KEYS = {"blob", "count", "keys", "last_pass"}


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


def cursor_state_digest(document: Mapping[str, Any]) -> str:
    """Return the digest of every cursor field except ``state_digest``.

    The cursor is evidence a later release resumes from, so a hand edit that
    silently changes a mark must be refused rather than trusted: the digest
    covers the canonical JSON of the rest of the document.
    """
    payload = {key: value for key, value in document.items() if key != "state_digest"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_cursor(document: object, where: str = "cursor") -> dict[str, Any]:
    """Validate one ``fleet-r2-scope-cursor/2`` document."""
    if not isinstance(document, dict) or document.get("schema") != CURSOR_SCHEMA:
        if isinstance(document, dict) and document.get("schema") in LEGACY_CURSOR_SCHEMAS:
            raise ContractError(
                f"{where}: is a {document['schema']} document, and this producer writes "
                f"{CURSOR_SCHEMA} (its resume key is the file content, not the "
                "candidate-id list). Migrate it explicitly: pass --rebaseline for one "
                "fresh pass from the current content, or delete the cursor file"
            )
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
    for key in ("slice_size", "pass_index", "write_index", "list_count"):
        _non_negative(document, key, where)
    for key in ("slice_size", "write_index"):
        if document[key] < 1:
            raise ContractError(f"{where}.{key}: expected at least 1")
    if not isinstance(document["commit"], str) or _COMMIT.fullmatch(document["commit"]) is None:
        raise ContractError(f"{where}.commit: expected a full Git object id")
    commits = document["judged_commits"]
    if not isinstance(commits, list) or any(
        not isinstance(item, str) or _COMMIT.fullmatch(item) is None for item in commits
    ):
        raise ContractError(f"{where}.judged_commits: expected full Git object ids")
    _digest_value(document, "list_digest", where)
    _digest_value(document, "state_digest", where)
    files = document["files"]
    if not isinstance(files, dict):
        raise ContractError(f"{where}.files: expected an object keyed by repo-relative path")
    for path, mark in files.items():
        mark_where = f"{where}.files[{path!r}]"
        if not isinstance(path, str) or not path:
            raise ContractError(f"{where}.files: a key must be a non-empty repo-relative path")
        if not isinstance(mark, dict):
            raise ContractError(f"{mark_where}: expected an object")
        extra = sorted(set(mark) - _MARK_KEYS)
        if extra:
            raise ContractError(f"{mark_where}: unknown field(s): {', '.join(extra)}")
        missing = sorted(_MARK_KEYS - set(mark))
        if missing:
            raise ContractError(f"{mark_where}: missing field(s): {', '.join(missing)}")
        if not isinstance(mark["blob"], str) or _COMMIT.fullmatch(mark["blob"]) is None:
            raise ContractError(f"{mark_where}.blob: expected a full Git object id")
        _non_negative(mark, "count", mark_where)
        _non_negative(mark, "last_pass", mark_where)
        keys = mark["keys"]
        if not isinstance(keys, list) or any(
            not isinstance(key, str) or _DIGEST.fullmatch(key) is None for key in keys
        ):
            raise ContractError(f"{mark_where}.keys: expected candidate-key digests")
        if keys != sorted(set(keys)):
            raise ContractError(f"{mark_where}.keys: expected unique digests in sorted order")
        if len(keys) > mark["count"]:
            raise ContractError(
                f"{mark_where}.keys: {len(keys)} judged key(s) of {mark['count']} candidate(s)"
            )
        if mark["last_pass"] > document["pass_index"]:
            raise ContractError(f"{mark_where}.last_pass: is newer than the cursor's pass_index")
    if document["state_digest"] != cursor_state_digest(document):
        raise ContractError(
            f"{where}.state_digest: does not match the rest of the document; the cursor "
            "file was edited after this producer wrote it"
        )
    return dict(document)


def _cursor_document_from_json(source: Path) -> object:
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cursor {source}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"cursor {source}: is not valid JSON: {exc}") from exc


def read_cursor(path: Path) -> dict[str, Any]:
    """Read and strictly validate a ``fleet-r2-scope-cursor/2`` file."""
    source = Path(path)
    return validate_cursor(_cursor_document_from_json(source), f"cursor {source}")


def load_cursor(
    path: Path, *, rebaseline: bool = False, allow_absent: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    """Read a rolling cursor for one invocation.

    Returns ``(cursor, legacy_schema)``. A legacy cursor is refused with its
    migration unless the caller passes ``--rebaseline``, which starts one fresh
    pass from the current content and reports the schema it migrated from.

    ``allow_absent`` is for a cursor path the *project's committed scope file*
    names: a declaration is configuration, and its first release legitimately
    has no state yet. A path the caller typed (``--cursor``) is not allowed to
    be absent, because a typo there would restart the sweep quietly on every
    release instead of resuming it.
    """
    source = Path(path)
    if allow_absent and not source.exists():
        return None, None
    document = _cursor_document_from_json(source)
    if (
        rebaseline
        and isinstance(document, dict)
        and document.get("schema") in LEGACY_CURSOR_SCHEMAS
    ):
        return None, str(document["schema"])
    return validate_cursor(document, f"cursor {source}"), None


def write_cursor(path: Path, document: Mapping[str, Any]) -> None:
    """Validate and atomically replace the cursor file with *document*."""
    target = Path(path)
    if not isinstance(document, Mapping):
        # One refusal path for both directions: ``validate_cursor`` owns the
        # message a caller reads, and a non-mapping must not fail as a
        # ValueError from ``dict(...)`` instead of a contract refusal.
        validate_cursor(document, f"cursor {target}")
    body = dict(document)
    body["state_digest"] = cursor_state_digest(body)
    validated = validate_cursor(body, f"cursor {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)


@dataclass(frozen=True)
class RollingSlice:
    """One rolling slice, its pass position and the marks it advances to."""

    selected: tuple[str, ...]
    pass_index: int
    new_pass: bool
    list_count: int
    list_digest: str
    slice_digest: str
    changed_files: int
    changed_candidates: int
    never_judged_candidates: int
    pending_candidates: int
    judged_candidates: int
    retired: tuple[dict[str, Any], ...]
    files: dict[str, dict[str, Any]]
    stale_reason: str | None


def _mark_is_current(
    mark: Mapping[str, Any], rows: Sequence[PlannedCandidate], blob: str
) -> bool:
    """Return whether *mark* was written for exactly this path content and plan.

    All three conditions matter and each one alone is a way to mark an
    unjudged candidate as judged: the blob pins the content (the site keys are
    only stable for one content), the count pins the planned shape of the file,
    and every judged key must still be a planned candidate of that path (an
    operator-selection change reuses a path and blob with other sites).
    """
    keys = {row.key for row in rows}
    return (
        mark["blob"] == blob
        and mark["count"] == len(rows)
        and set(mark["keys"]) <= keys
    )


def _due_entries(
    by_path: Mapping[str, Sequence[PlannedCandidate]],
    marks: Mapping[str, Mapping[str, Any]],
    blobs: Mapping[str, str],
    pass_index: int,
) -> tuple[list[tuple[int, int, str, PlannedCandidate]], dict[str, int], int]:
    """Return the due candidates, their accounting, and how many paths changed.

    One entry is ``(rank, last_pass, path, candidate)``: rank 0 is a path whose
    content changed since its mark (judged first, least recently judged first),
    rank 1 is a never-judged or not-yet-judged-in-this-content candidate
    (path order), and rank 2 is a path fully judged in an EARLIER pass than
    *pass_index* (least recently judged first, then path order): a mark only
    counts for the pass it was written in, so every later pass sweeps the whole
    target again instead of re-judging the first slice for ever.
    """
    due: list[tuple[int, int, str, PlannedCandidate]] = []
    accounting = {
        "changed_candidates": 0,
        "never_judged_candidates": 0,
        "pending_candidates": 0,
        "judged_candidates": 0,
    }
    changed_files = 0
    for path, rows in by_path.items():
        if path not in blobs:
            raise ContractError(
                f"rolling scope: the candidate plan names {path!r}, which the commit "
                "does not describe; the sweep cannot key it"
            )
        blob = blobs[path]
        mark = marks.get(path)
        if mark is None:
            due.extend((1, -1, path, row) for row in rows)
            accounting["never_judged_candidates"] += len(rows)
            continue
        if not _mark_is_current(mark, rows, blob):
            due.extend((0, mark["last_pass"], path, row) for row in rows)
            accounting["changed_candidates"] += len(rows)
            changed_files += 1
            continue
        if mark["last_pass"] < pass_index:
            due.extend((2, mark["last_pass"], path, row) for row in rows)
            accounting["pending_candidates"] += len(rows)
            continue
        judged = set(mark["keys"])
        judged_rows = [row for row in rows if row.key in judged]
        pending_rows = [row for row in rows if row.key not in judged]
        accounting["judged_candidates"] += len(judged_rows)
        accounting["pending_candidates"] += len(pending_rows)
        due.extend((1, -1, path, row) for row in pending_rows)
    return due, accounting, changed_files


def _slice_order(due: Sequence[tuple[int, int, str, PlannedCandidate]]) -> list[tuple[int, int, str, PlannedCandidate]]:
    """Sort the due entries in the deterministic sweep order."""
    return sorted(due, key=lambda item: (item[0], item[1], item[2], item[3].lineno, item[3].id))


def rolling_slice(
    candidates: Sequence[PlannedCandidate],
    *,
    slice_size: int,
    lane: str,
    blobs: Mapping[str, str],
    cursor: Mapping[str, Any] | None = None,
    rebaseline: bool = False,
) -> RollingSlice:
    """Take the next slice of the rolling whole-target sweep.

    A cursor written for another lane is REFUSED (``stale_reason``); a cursor
    whose marks no longer describe this plan is not stale but *due again*, and
    is accounted instead of resumed (and never dropped silently). ``slice_size``
    is a budget: changing it re-slices the same sweep, so it is adopted rather
    than refused, and the new value is recorded.
    """
    if isinstance(slice_size, bool) or not isinstance(slice_size, int) or slice_size < 1:
        raise ContractError("--slice-size: expected an integer of at least 1")
    by_path = assign_site_keys(candidates)
    digest = candidate_list_digest([candidate.id for candidate in candidates])
    if cursor is not None and cursor["lane"] != lane:
        return RollingSlice(
            (), cursor["pass_index"], False, len(candidates), digest, "",
            0, 0, 0, 0, 0, (),
            {path: dict(mark) for path, mark in cursor["files"].items()},
            f"the cursor was written for lane {cursor['lane']!r}, this scope is lane {lane!r}",
        )
    previous_files = {} if cursor is None else dict(cursor["files"])
    marks = {} if cursor is None or rebaseline else previous_files
    previous_pass = 0 if cursor is None else cursor["pass_index"]
    pass_index = previous_pass
    due, accounting, changed_files = _due_entries(by_path, marks, blobs, pass_index)
    # Retirement is accounted from the PREVIOUS cursor even under --rebaseline:
    # a discarded mark is still a mark this release is choosing not to resume,
    # and the evidence says so instead of the path vanishing from the record.
    retired = tuple(
        {
            "path": path,
            "candidates": previous_files[path]["count"],
            "last_judged_pass": previous_files[path]["last_pass"],
            "reason": "no planned candidate remains at this path",
        }
        for path in sorted(set(previous_files) - set(by_path))
    )
    # A complete sweep over the current content does not idle: the next release
    # starts a NEW pass at the least recently judged file, and the document says
    # so (``new_pass``). A rebaseline is the explicit discard instead: it
    # starts a new pass from the current content, dropping every mark.
    new_pass = bool(cursor is not None and not rebaseline and not due and by_path)
    if rebaseline and cursor is not None:
        pass_index = previous_pass + 1
    if new_pass:
        pass_index = previous_pass + 1
        # Every path now carries a mark of an earlier pass, so each one is due
        # at rank 2, least recently judged first (the accounting above keeps
        # describing the finished pass the cursor was found in).
        due = _due_entries(by_path, marks, blobs, pass_index)[0]
    chosen = _slice_order(due)[:slice_size]
    selected = tuple(row.id for _, _, _, row in chosen)
    files: dict[str, dict[str, Any]] = {}
    for path, rows in by_path.items():
        mark = marks.get(path)
        judgements = [row for _, _, chosen_path, row in chosen if chosen_path == path]
        if not judgements:
            if mark is not None:
                # Not judged in this slice: the previous mark stands, so a file
                # whose content changed stays due and keeps its recency.
                files[path] = dict(mark)
            continue
        reusable = (
            ()
            if mark is None
            or mark["last_pass"] < pass_index
            or not _mark_is_current(mark, rows, blobs[path])
            else tuple(mark["keys"])
        )
        files[path] = {
            "blob": blobs[path],
            "count": len(rows),
            "keys": sorted(set(reusable) | {row.key for row in judgements}),
            "last_pass": pass_index,
        }
    return RollingSlice(
        selected,
        pass_index,
        new_pass,
        len(candidates),
        digest,
        candidate_list_digest(selected),
        changed_files,
        accounting["changed_candidates"],
        accounting["never_judged_candidates"],
        accounting["pending_candidates"],
        accounting["judged_candidates"],
        retired,
        files,
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
        "pass_index": result.pass_index,
        "write_index": 1 if previous is None else previous["write_index"] + 1,
        "commit": commit,
        "list_count": result.list_count,
        "list_digest": result.list_digest,
        "files": result.files,
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
    blobs: Mapping[str, str] | None = None,
    legacy_cursor: str | None = None,
) -> dict[str, Any]:
    """Build the ``fleet-r2-scope/1`` document for one invocation."""
    if mode not in SCOPE_MODES:
        raise ContractError(f"scope mode: expected one of {', '.join(SCOPE_MODES)}")
    candidates = plan_candidates(plan, require_site_identity=mode == ROLLING_MODE)
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
    if blobs is None:
        raise ContractError(
            f"scope mode {mode}: the target files' blob ids at commit {commit[:12]} are "
            "required; the sweep keys each candidate to its file's content"
        )
    result = rolling_slice(
        candidates,
        slice_size=slice_size,
        lane=lane,
        blobs=blobs,
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
            "pass_index": result.pass_index,
            "new_pass": result.new_pass,
            "slice_size": slice_size,
            "list_count": result.list_count,
            "list_digest": result.list_digest,
            "slice_digest": result.slice_digest,
            "selected_count": len(result.selected),
            "changed_files": result.changed_files,
            "changed_candidates": result.changed_candidates,
            "never_judged_candidates": result.never_judged_candidates,
            "pending_candidates": result.pending_candidates,
            "judged_candidates": result.judged_candidates,
            "retired": list(result.retired),
            "rebaselined": rebaseline,
            "legacy_cursor": legacy_cursor,
            "stale_reason": result.stale_reason,
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
        # The sweep always has a due candidate when the plan is non-empty (a
        # finished pass wraps around to the least recently judged file), so
        # an empty rolling scope is exactly one fact: there is nothing to plan.
        document["status"] = "nothing-to-judge"
        document["reason"] = "the whole-target candidate list is empty"
    return document
