#!/usr/bin/env python3
"""Select a deterministic, stratified B110 pilot set from an Assay plan."""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}\Z")
_ROW_KEYS = frozenset(
    {
        "id",
        "path",
        "operator",
        "start_byte",
        "end_byte",
        "lineno",
        "description",
        "source_sha256",
    }
)
_GO_PATH = "assay/src/assay/adapters/go.py"
_SCANNER_SPECS = (
    ("_scan_raw_string", "return None if end == -1 else end + 1"),
    ("_strip_comments_and_literals", 'if two == "//":'),
    ("_strip_comments_and_literals", "if end == -1:"),
    ("_strip_comments_and_literals", "if close == -1:"),
)
_PLAN_LIMIT = 32 * 1024 * 1024
_CANDIDATE_FILE_LIMIT = 1024 * 1024
_SEED_LIMIT = 128


class SelectionError(ValueError):
    """A plan or source document cannot support a truthful pilot selection."""


def _close_descriptors(*descriptors: int) -> None:
    first_error: OSError | None = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


@dataclass
class _PinnedOutput:
    """An output basename held relative to its admitted parent directory."""

    path: Path
    requested_path: Path
    parent_fd: int
    parent_path: Path
    name: str

    @property
    def identity(self) -> tuple[int, int, str]:
        parent_stat = os.fstat(self.parent_fd)
        return parent_stat.st_dev, parent_stat.st_ino, self.name

    def close(self) -> None:
        _close_descriptors(self.parent_fd)


@dataclass
class _PinnedInput:
    """An already-open regular input and the directory entry that names it."""

    path: Path
    parent_fd: int
    file_fd: int
    name: str

    @property
    def identity(self) -> tuple[int, int, str]:
        parent_stat = os.fstat(self.parent_fd)
        return parent_stat.st_dev, parent_stat.st_ino, self.name

    def close(self) -> None:
        _close_descriptors(self.file_fd, self.parent_fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SelectionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _plan_sha256(candidate_ids: list[str]) -> str:
    digest = hashlib.sha256()
    for candidate in candidate_ids:
        digest.update(f"{len(candidate)}:{candidate},".encode("ascii"))
    return digest.hexdigest()


def _rank(seed: str, candidate_id: str) -> str:
    _validate_seed(seed)
    try:
        payload = (seed + candidate_id).encode("ascii")
    except UnicodeEncodeError as exc:
        raise SelectionError("seed must contain printable ASCII characters only") from exc
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def _validate_seed(seed: str) -> None:
    if not isinstance(seed, str) or any(
        not 0x20 <= ord(character) <= 0x7E for character in seed
    ):
        raise SelectionError("seed must contain printable ASCII characters only")
    if len(seed) > _SEED_LIMIT:
        raise SelectionError(f"seed must contain at most {_SEED_LIMIT} characters")


def _parse_plan(raw: bytes) -> tuple[str, str, list[dict[str, Any]]]:
    if len(raw) > _PLAN_LIMIT:
        raise SelectionError(f"plan exceeds the {_PLAN_LIMIT}-byte input bound")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except SelectionError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SelectionError(f"plan is not valid plan JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SelectionError("plan JSON must be one object")
    if document.get("status") != "ok":
        raise SelectionError("plan status must be 'ok'")
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise SelectionError("plan candidates must be a list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(candidates):
        if not isinstance(item, dict) or not _ROW_KEYS <= item.keys():
            raise SelectionError(f"candidate row {index} is missing required plan fields")
        candidate_id = item["id"]
        if not isinstance(candidate_id, str) or _ID_RE.fullmatch(candidate_id) is None:
            raise SelectionError(f"candidate row {index} has an invalid id")
        if candidate_id in seen:
            raise SelectionError(f"candidate id {candidate_id} appears more than once in the plan")
        seen.add(candidate_id)
        path = item["path"]
        if not isinstance(path, str) or not path:
            raise SelectionError(f"candidate row {index} has an invalid path")
        pure = PurePosixPath(path)
        if pure.is_absolute() or any(part in ("", ".", "..") for part in path.split("/")) or "\\" in path:
            raise SelectionError(f"candidate row {index} has an unsafe source path")
        if not isinstance(item["operator"], str) or not item["operator"]:
            raise SelectionError(f"candidate row {index} has an invalid operator")
        if not isinstance(item["description"], str):
            raise SelectionError(f"candidate row {index} has an invalid description")
        if (
            not isinstance(item["source_sha256"], str)
            or _ID_RE.fullmatch(item["source_sha256"]) is None
        ):
            raise SelectionError(f"candidate row {index} has an invalid source_sha256")
        for field in ("start_byte", "end_byte", "lineno"):
            value = item[field]
            if type(value) is not int or value < (1 if field == "lineno" else 0):
                raise SelectionError(f"candidate row {index} has an invalid {field}")
        if item["end_byte"] <= item["start_byte"]:
            raise SelectionError(f"candidate row {index} has an invalid byte span")
        rows.append(dict(item))
    if not rows:
        raise SelectionError("plan contains no candidates")
    commit, tree = document.get("commit"), document.get("tree")
    if not isinstance(commit, str) or _GIT_OID_RE.fullmatch(commit) is None:
        raise SelectionError("plan commit must be a 40-character lowercase Git object id")
    if not isinstance(tree, str) or _GIT_OID_RE.fullmatch(tree) is None:
        raise SelectionError("plan tree must be a 40-character lowercase Git object id")
    return commit, tree, rows


def _git_environment() -> dict[str, str]:
    """Run Git without caller HOME, XDG or Git configuration overrides."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }


def _git(repo_root: Path, *argv: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *argv],
        check=True,
        capture_output=True,
        text=text,
        timeout=30,
        env=_git_environment(),
    )


def _repository_identity(
    repo_root: Path,
    *,
    allowed_untracked: frozenset[bytes] = frozenset(),
) -> tuple[str, str]:
    root = repo_root.resolve(strict=True)
    try:
        top_result = _git(root, "rev-parse", "--show-toplevel")
        commit_result = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        tree_result = _git(root, "rev-parse", "--verify", "HEAD^{tree}")
        status = _git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
            text=False,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SelectionError(f"cannot verify Git identity for repo-root {root}: {exc}") from exc
    reported_root = Path(top_result.stdout.strip()).resolve(strict=True)
    if reported_root != root:
        raise SelectionError(
            f"Git inspected worktree root {reported_root}, not supplied repo-root {root}"
        )
    commit = commit_result.stdout.strip()
    tree = tree_result.stdout.strip()
    if _GIT_OID_RE.fullmatch(commit) is None or _GIT_OID_RE.fullmatch(tree) is None:
        raise SelectionError(f"repo-root {root} returned an invalid Git commit or tree")
    dirty: list[bytes] = []
    for entry in status.stdout.split(b"\0"):
        if not entry:
            continue
        if entry.startswith(b"?? ") and entry[3:] in allowed_untracked:
            continue
        dirty.append(entry)
    if dirty:
        raise SelectionError(f"repo-root {root} must have a clean Git worktree")
    return commit, tree


def _planned_source_paths(
    rows: list[dict[str, Any]], repo_root: Path
) -> dict[str, Path]:
    root = repo_root.resolve(strict=True)
    paths: dict[str, Path] = {}
    for relative in sorted({row["path"] for row in rows}):
        candidate_path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = candidate_path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise SelectionError(f"source path {relative!r} is not a file inside repo-root")
        except SelectionError:
            raise
        except OSError as exc:
            raise SelectionError(f"cannot resolve source {relative!r}: {exc}") from exc
        paths[relative] = resolved
    return paths


def _read_sources(
    rows: list[dict[str, Any]],
    repo_root: Path,
    *,
    commit: str | None = None,
) -> dict[str, bytes]:
    if commit is not None:
        return _read_sources_from_tree(rows, repo_root, commit=commit)
    paths = _planned_source_paths(rows, repo_root)
    sources: dict[str, bytes] = {}
    for relative, source_path in paths.items():
        try:
            sources[relative] = source_path.read_bytes()
        except OSError as exc:
            raise SelectionError(f"cannot read source {relative!r}: {exc}") from exc
    for row in rows:
        actual = hashlib.sha256(sources[row["path"]]).hexdigest()
        if row["source_sha256"] != actual:
            raise SelectionError(
                f"source sha256 does not match plan for {row['path']!r}"
            )
    return sources


def _read_sources_from_tree(
    rows: list[dict[str, Any]], repo_root: Path, *, commit: str
) -> dict[str, bytes]:
    """Read planned source bytes from the immutable committed tree."""
    root = repo_root.resolve(strict=True)
    sources: dict[str, bytes] = {}
    for relative in sorted({row["path"] for row in rows}):
        try:
            listing = _git(
                root,
                "ls-tree",
                "-z",
                "--full-tree",
                commit,
                "--",
                f":(literal){relative}",
                text=False,
            ).stdout
            entries = [entry for entry in listing.split(b"\0") if entry]
            if len(entries) != 1:
                raise SelectionError(
                    f"committed source {relative!r} is missing or ambiguous at {commit}"
                )
            metadata, raw_path = entries[0].split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
            if (
                raw_path != os.fsencode(relative)
                or mode not in {b"100644", b"100755"}
                or object_type != b"blob"
            ):
                raise SelectionError(
                    f"committed source {relative!r} is not a regular file at {commit}"
                )
            raw = _git(
                root,
                "cat-file",
                "blob",
                object_id.decode("ascii"),
                text=False,
            ).stdout
        except SelectionError:
            raise
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
            raise SelectionError(
                f"cannot read committed source {relative!r} at {commit}: {exc}"
            ) from exc
        sources[relative] = raw
    for row in rows:
        actual = hashlib.sha256(sources[row["path"]]).hexdigest()
        if row["source_sha256"] != actual:
            raise SelectionError(
                f"source sha256 does not match plan for {row['path']!r}"
            )
    return sources


def _byte_line_starts(source: bytes) -> list[int]:
    starts = [0]
    for index, byte in enumerate(source):
        if byte == 0x0A:
            starts.append(index + 1)
    return starts


def _node_span(node: ast.AST, starts: list[int]) -> tuple[int, int]:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    column = getattr(node, "col_offset", None)
    end_column = getattr(node, "end_col_offset", None)
    if (
        type(lineno) is not int
        or type(end_lineno) is not int
        or type(column) is not int
        or type(end_column) is not int
        or lineno < 1
        or end_lineno < 1
        or lineno > len(starts)
        or end_lineno > len(starts)
    ):
        raise SelectionError("source AST has an incomplete byte span")
    return starts[lineno - 1] + column, starts[end_lineno - 1] + end_column


def _import_time_bool_ids(rows: list[dict[str, Any]], sources: dict[str, bytes]) -> tuple[list[dict[str, Any]], int]:
    bool_rows = [row for row in rows if row["operator"] == "python:bool-const-flip"]
    body_spans: dict[str, list[tuple[int, int]]] = {}
    for relative in sorted({row["path"] for row in bool_rows}):
        raw = sources[relative]
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=relative)
        except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError) as exc:
            raise SelectionError(f"cannot parse bool candidate source {relative!r}: {exc}") from exc
        starts = _byte_line_starts(raw)
        spans: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.body:
                    spans.append((_node_span(node.body[0], starts)[0], _node_span(node.body[-1], starts)[1]))
            elif isinstance(node, ast.Lambda):
                spans.append(_node_span(node.body, starts))
        body_spans[relative] = spans

    import_time: list[dict[str, Any]] = []
    for row in bool_rows:
        source = sources[row["path"]]
        start, end = row["start_byte"], row["end_byte"]
        if source[start:end] not in (b"True", b"False"):
            raise SelectionError(f"source does not match plan: {row['path']} line {row['lineno']}")
        if not any(start >= body_start and end <= body_end for body_start, body_end in body_spans[row["path"]]):
            import_time.append(row)
    return import_time, len(import_time)


def _scanner_hard_rows(rows: list[dict[str, Any]], sources: dict[str, bytes]) -> list[dict[str, Any]]:
    source = sources.get(_GO_PATH)
    if source is None:
        raise SelectionError(f"known-hard scanner source {_GO_PATH!r} is missing from the plan")
    try:
        lines = source.decode("utf-8").splitlines()
        tree = ast.parse(source.decode("utf-8"), filename=_GO_PATH)
    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError) as exc:
        raise SelectionError(f"cannot parse scanner source {_GO_PATH!r}: {exc}") from exc
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    line_starts = _byte_line_starts(source)
    hard: list[dict[str, Any]] = []
    for function_name, expected_line in _SCANNER_SPECS:
        matches: list[dict[str, Any]] = []
        for row in rows:
            if (
                row["path"] != _GO_PATH
                or row["operator"] != "python:compare-swap"
                or row["description"] != "Eq->NotEq"
            ):
                continue
            lineno = row["lineno"]
            if lineno > len(lines):
                continue
            enclosing = [
                node
                for node in functions
                if node.lineno <= lineno <= node.end_lineno
            ]
            if not enclosing or lines[lineno - 1].strip() != expected_line:
                continue
            innermost = min(
                enclosing,
                key=lambda node: (node.end_lineno - node.lineno, node.col_offset),
            )
            if innermost.name == function_name:
                matches.append(row)
        if len(matches) != 1:
            raise SelectionError(
                f"known-hard scanner spec {function_name}: {expected_line!r} matches {len(matches)} plan rows; expected exactly one"
            )
        row = matches[0]
        line_start = line_starts[row["lineno"] - 1]
        line_end = source.find(b"\n", line_start)
        if line_end < 0:
            line_end = len(source)
        source_line = source[line_start:line_end]
        leading_bytes = len(source_line) - len(source_line.lstrip())
        expected_offset = expected_line.encode("utf-8").find(b"==")
        expected_start = line_start + leading_bytes + expected_offset
        function = next(
            node
            for node in functions
            if node.name == function_name
            and node.lineno <= row["lineno"] <= node.end_lineno
            and source_line.strip() == expected_line.encode("utf-8")
        )
        function_start, function_end = _node_span(function, line_starts)
        if (
            row["start_byte"] != expected_start
            or row["end_byte"] != expected_start + 2
            or not (
                function_start
                <= row["start_byte"]
                < row["end_byte"]
                <= function_end
            )
        ):
            raise SelectionError(f"source does not match plan: {function_name} {expected_line!r}")
        hard.append({**row, "label": f"go scanner {function_name}: {expected_line}"})
    return hard


def _select(
    rows: list[dict[str, Any]],
    *,
    seed: str,
    size: int,
    repo_root: Path,
    sources: dict[str, bytes] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str], int]:
    if type(size) is not int or size < 1:
        raise SelectionError("size must be a positive integer")
    if not rows:
        raise SelectionError("plan contains no candidates")
    if sources is None:
        sources = _read_sources(rows, repo_root)
    row_by_id = {row["id"]: row for row in rows}
    ranks = {row["id"]: _rank(seed, row["id"]) for row in rows}
    selected: dict[str, str] = {}
    files = sorted({row["path"] for row in rows})
    operators = sorted({row["operator"] for row in rows})

    for path in files:
        candidates = [row for row in rows if row["path"] == path]
        if candidates:
            winner = min(candidates, key=lambda row: (ranks[row["id"]], row["id"]))
            selected[winner["id"]] = "per-file"

    missing_operators = [
        operator
        for operator in operators
        if not any(row_by_id[identity]["operator"] == operator for identity in selected)
    ]
    if len(files) + len(missing_operators) > size:
        raise SelectionError(
            f"size {size} is smaller than per-file ({len(files)}) plus missing-operator ({len(missing_operators)}) coverage"
        )
    for operator in missing_operators:
        candidates = [row for row in rows if row["operator"] == operator and row["id"] not in selected]
        if not candidates:
            raise SelectionError(f"operator {operator!r} has no candidate row to select")
        winner = min(candidates, key=lambda row: (ranks[row["id"]], row["id"]))
        selected[winner["id"]] = "operator"

    for row in sorted(rows, key=lambda item: (ranks[item["id"]], item["id"])):
        if len(selected) >= size:
            break
        if row["id"] not in selected:
            selected[row["id"]] = "fill"

    stratified = [
        {**row_by_id[identity], "reason": reason, "rank": ranks[identity]}
        for identity, reason in selected.items()
    ]
    plan_positions = {row["id"]: index for index, row in enumerate(rows)}
    stratified.sort(key=lambda item: plan_positions[item["id"]])
    # The hard rows are identified separately from the stratified reasons.
    scanner_hard = _scanner_hard_rows(rows, sources)
    import_time_rows, import_time_total = _import_time_bool_ids(rows, sources)
    if len(import_time_rows) < 2:
        raise SelectionError(
            f"expected at least two import-time bool-const-flip rows, found {len(import_time_rows)}"
        )
    bool_hard = sorted(
        import_time_rows,
        key=lambda row: (ranks[row["id"]], row["id"]),
    )[:2]
    hard = scanner_hard + [
        {**row, "label": f"import-time bool-const-flip rank {index}"}
        for index, row in enumerate(bool_hard, 1)
    ]
    return stratified, hard, files, operators, import_time_total


def _pin_input(path: Path) -> _PinnedInput:
    requested = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    )
    try:
        parent_path = requested.parent.resolve(strict=True)
        parent_fd = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise SelectionError(f"cannot pin input parent for {requested}: {exc}") from exc
    file_fd: int | None = None
    try:
        before = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise SelectionError(f"input path is not a regular non-symlink file: {requested}")
        file_fd = os.open(
            requested.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SelectionError(f"input path changed while opening {requested}")
        return _PinnedInput(
            path=parent_path / requested.name,
            parent_fd=parent_fd,
            file_fd=file_fd,
            name=requested.name,
        )
    except BaseException:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
        raise


def _pin_output(path: Path) -> _PinnedOutput:
    requested = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    )
    try:
        requested.parent.mkdir(parents=True, exist_ok=True)
        parent_path = requested.parent.resolve(strict=True)
        parent_fd = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise SelectionError(f"cannot pin output parent for {requested}: {exc}") from exc
    try:
        opened = os.fstat(parent_fd)
        named = os.stat(parent_path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise SelectionError(f"output parent changed while opening {requested}")
        try:
            existing = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(existing.st_mode):
                raise SelectionError(f"output destination is a symlink: {requested}")
            if not stat.S_ISREG(existing.st_mode):
                raise SelectionError(f"output destination is not a regular file: {requested}")
        return _PinnedOutput(
            path=parent_path / requested.name,
            requested_path=requested,
            parent_fd=parent_fd,
            parent_path=parent_path,
            name=requested.name,
        )
    except BaseException:
        os.close(parent_fd)
        raise


def _verify_output_parent_identity(output: _PinnedOutput) -> None:
    """Refuse publication if the user's requested parent now names elsewhere."""
    requested = output.requested_path
    try:
        current_parent = requested.parent.resolve(strict=True)
        current_fd = os.open(
            current_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise SelectionError(
            f"output path changed while selection was prepared: {requested}: {exc}"
        ) from exc
    try:
        pinned_stat = os.fstat(output.parent_fd)
        current_stat = os.fstat(current_fd)
        if (current_stat.st_dev, current_stat.st_ino) != (
            pinned_stat.st_dev,
            pinned_stat.st_ino,
        ):
            raise SelectionError(
                f"output path changed while selection was prepared: {requested}"
            )
    finally:
        os.close(current_fd)


def _acquire_output_locks(outputs: tuple[_PinnedOutput, ...]) -> None:
    """Lock each pinned output parent until the caller closes its descriptors.

    Directory-inode locks coordinate symlink aliases and processes that see
    the same output directory through different mount namespaces. The sorted
    identity order avoids lock-order inversions when two outputs use different
    parents. Locking the directory descriptor also avoids a lock-file root
    whose location could vary with TMPDIR.
    """
    parents: dict[tuple[int, int], int] = {}
    try:
        for output in outputs:
            parent_stat = os.fstat(output.parent_fd)
            if not stat.S_ISDIR(parent_stat.st_mode):
                raise SelectionError(
                    f"output parent is no longer a directory: {output.requested_path}"
                )
            parents.setdefault(
                (parent_stat.st_dev, parent_stat.st_ino), output.parent_fd
            )
        for identity in sorted(parents):
            try:
                fcntl.flock(
                    parents[identity], fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError:
                raise SelectionError(
                    "another selector is publishing in an output directory; retry after it finishes"
                ) from None
    except OSError as exc:
        raise SelectionError(f"cannot lock selection output directory: {exc}") from exc


def _path_identity(path: Path) -> tuple[int, int, str]:
    requested = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    )
    try:
        parent_path = requested.parent.resolve(strict=True)
        parent_fd = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise SelectionError(f"cannot inspect path parent for {requested}: {exc}") from exc
    try:
        parent_stat = os.fstat(parent_fd)
        try:
            entry = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SelectionError(f"cannot inspect path {requested}: {exc}") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise SelectionError(f"input path is a symlink: {requested}")
        if not stat.S_ISREG(entry.st_mode):
            raise SelectionError(f"input path is not a regular file: {requested}")
        return parent_stat.st_dev, parent_stat.st_ino, requested.name
    finally:
        os.close(parent_fd)


def _stage_atomic(parent_fd: int, name: str, payload: bytes) -> str:
    temporary = f".{name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    return temporary


def _cleanup_staged(parent_fd: int, temporary: str | None) -> None:
    if temporary is None:
        return
    try:
        os.unlink(temporary, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _publish_pair(
    candidates: _PinnedOutput,
    candidates_temporary: str,
    report: _PinnedOutput,
    report_temporary: str,
) -> None:
    # The report is the commit marker. Remove an older report before the
    # candidate file changes, then publish the new report last.
    try:
        os.unlink(report.name, dir_fd=report.parent_fd)
        os.fsync(report.parent_fd)
    except FileNotFoundError:
        pass
    os.replace(
        candidates_temporary,
        candidates.name,
        src_dir_fd=candidates.parent_fd,
        dst_dir_fd=candidates.parent_fd,
    )
    os.fsync(candidates.parent_fd)
    os.replace(
        report_temporary,
        report.name,
        src_dir_fd=report.parent_fd,
        dst_dir_fd=report.parent_fd,
    )
    os.fsync(report.parent_fd)


def _read_plan(path: Path, *, descriptor: int | None = None) -> bytes:
    try:
        read_descriptor = (
            os.dup(descriptor)
            if descriptor is not None
            else os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        )
    except OSError as exc:
        raise SelectionError(f"cannot read plan {path}: {exc}") from exc
    try:
        info = os.fstat(read_descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SelectionError(f"plan is not a regular file: {path}")
        if info.st_size > _PLAN_LIMIT:
            raise SelectionError(f"plan exceeds the {_PLAN_LIMIT}-byte input bound")
        with os.fdopen(read_descriptor, "rb", closefd=False) as stream:
            raw = stream.read(_PLAN_LIMIT + 1)
    except OSError as exc:
        raise SelectionError(f"cannot read plan {path}: {exc}") from exc
    finally:
        os.close(read_descriptor)
    if len(raw) > _PLAN_LIMIT:
        raise SelectionError(f"plan exceeds the {_PLAN_LIMIT}-byte input bound")
    return raw


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--seed", default="b110-pilot-2026")
    parser.add_argument("--size", type=int, default=64)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidates_output: _PinnedOutput | None = None
    report_output: _PinnedOutput | None = None
    plan_input: _PinnedInput | None = None
    candidates_temporary: str | None = None
    report_temporary: str | None = None
    exit_code = 0
    cleanup_errors: list[str] = []

    def cleanup(label: str, operation: Callable[[], object]) -> None:
        try:
            operation()
        except Exception as exc:
            cleanup_errors.append(f"{label}: {exc}")

    try:
        if args.size < 1:
            raise SelectionError("size must be a positive integer")
        _validate_seed(args.seed)
        # Pin output parents before comparing any user-supplied input or
        # planned source path. Later symlink changes cannot redirect replace.
        candidates_output = _pin_output(args.out)
        report_output = _pin_output(args.report)
        plan_input = _pin_input(args.plan)
        plan_identity = plan_input.identity
        if (
            candidates_output.identity == plan_identity
            or report_output.identity == plan_identity
        ):
            raise SelectionError("--plan, --out, and --report must name different files")
        if candidates_output.identity == report_output.identity:
            raise SelectionError("--out and --report must name different files")
        raw_plan = _read_plan(plan_input.path, descriptor=plan_input.file_fd)
        plan_commit, plan_tree, rows = _parse_plan(raw_plan)
        repo_commit, repo_tree = _repository_identity(args.repo_root)
        if plan_commit != repo_commit:
            raise SelectionError("plan commit does not match repo-root HEAD")
        if plan_tree != repo_tree:
            raise SelectionError("plan tree does not match repo-root HEAD tree")
        planned_sources = _planned_source_paths(rows, args.repo_root)
        planned_source_identities = {
            _path_identity(source_path) for source_path in planned_sources.values()
        }
        if (
            candidates_output.identity in planned_source_identities
            or report_output.identity in planned_source_identities
        ):
            raise SelectionError(
                "--out and --report must not overwrite a source file named by the plan"
            )
        sources = _read_sources(rows, args.repo_root, commit=plan_commit)
        stratified, hard, files, operators, import_time_total = _select(
            rows,
            seed=args.seed,
            size=args.size,
            repo_root=args.repo_root,
            sources=sources,
        )
        selected_ids = {row["id"] for row in stratified} | {row["id"] for row in hard}
        plan_order_ids = [row["id"] for row in rows if row["id"] in selected_ids]
        overlap = [row["id"] for row in rows if row["id"] in {item["id"] for item in stratified} and row["id"] in {item["id"] for item in hard}]
        candidates_text = (
            f"# b110 pilot selection seed={args.seed} size={args.size} plan_candidates={len(rows)}\n"
            + "".join(f"{candidate}\n" for candidate in plan_order_ids)
        ).encode("utf-8")
        if len(candidates_text) > _CANDIDATE_FILE_LIMIT:
            raise SelectionError(
                "selected candidates file exceeds Assay's 1 MiB input limit"
            )
        report = {
            "schema": "b110-pilot-selection/1",
            "plan_commit": plan_commit,
            "plan_tree": plan_tree,
            "plan_sha256": hashlib.sha256(raw_plan).hexdigest(),
            "selected_ids": plan_order_ids,
            "candidates_file_sha256": hashlib.sha256(candidates_text).hexdigest(),
            "seed": args.seed,
            "size": args.size,
            "plan_candidate_count": len(rows),
            "files": files,
            "operators": operators,
            "stratified": stratified,
            "known_hard": hard,
            "overlap": overlap,
            "import_time_total": import_time_total,
            "selection_sha256": _plan_sha256(plan_order_ids),
        }
        report_bytes = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
        candidates_temporary = _stage_atomic(
            candidates_output.parent_fd, candidates_output.name, candidates_text
        )
        report_temporary = _stage_atomic(
            report_output.parent_fd, report_output.name, report_bytes
        )
        allowed_untracked: set[bytes] = set()
        root = args.repo_root.resolve(strict=True)
        for output, temporary in (
            (candidates_output, candidates_temporary),
            (report_output, report_temporary),
        ):
            temporary_path = output.parent_path / temporary
            try:
                relative_temporary = temporary_path.relative_to(root)
            except ValueError:
                continue
            allowed_untracked.add(os.fsencode(relative_temporary.as_posix()))
        _acquire_output_locks((candidates_output, report_output))
        final_commit, final_tree = _repository_identity(
            root,
            allowed_untracked=frozenset(allowed_untracked),
        )
        if final_commit != plan_commit or final_tree != plan_tree:
            raise SelectionError(
                "repo-root commit or tree changed before selection publication"
            )
        _verify_output_parent_identity(candidates_output)
        _verify_output_parent_identity(report_output)
        _publish_pair(
            candidates_output,
            candidates_temporary,
            report_output,
            report_temporary,
        )
        _verify_output_parent_identity(candidates_output)
        _verify_output_parent_identity(report_output)
        candidates_temporary = None
        report_temporary = None
    except (SelectionError, OSError, ValueError) as exc:
        print(f"b110_pilot_select: {exc}", file=sys.stderr)
        exit_code = 2
    finally:
        if plan_input is not None:
            cleanup("plan input", plan_input.close)
        if candidates_output is not None:
            cleanup(
                "candidate temporary",
                lambda: _cleanup_staged(
                    candidates_output.parent_fd, candidates_temporary
                ),
            )
            cleanup("candidate output directory", candidates_output.close)
        if report_output is not None:
            cleanup(
                "report temporary",
                lambda: _cleanup_staged(report_output.parent_fd, report_temporary),
            )
            cleanup("report output directory", report_output.close)
    if cleanup_errors:
        print(
            "b110_pilot_select: cleanup failed: " + "; ".join(cleanup_errors),
            file=sys.stderr,
        )
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
