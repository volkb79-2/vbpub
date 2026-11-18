"""Version policy CLI and native artifact writers (FEAT-03).

Resolution is explicit and read-only check never writes. A root target is
resolved once with root policy; a project target (including a project overlay
of a root target) uses project policy and records its state in that project's
cmru.toml.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from cmru import exit_codes
from cmru.config import (
    ForgeConfig,
    InvocationContext,
    effective_versions_for_project,
    load_forge_config,
    resolve_invocation_context,
)
from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cmru.cli_support import (
    TargetSelectionError,
    cmru_registry,
    select_target_names,
    target_argument,
)
from cli_extended import CliFailure, OptionSpec, VerbGroup, VerbSpec
from cmru.version_config import deep_merge_versions, parse_versions_section
from cmru.version_registry import (
    Candidate,
    RegistryError,
    RegistryRateLimitedError,
    candidates,
    candidate_for,
    normalized_version,
    oci_rolling_candidate,
    validate_constraint,
    version_satisfies,
)


DEFAULT_AGE_WINDOW_DAYS = 14
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*"
    r"((?:(?:===|==|!=|~=|<=|>=|<|>)[^;]*)?)\s*$"
)


class VersionsError(RuntimeError):
    """An invalid version command, policy, or generated output."""


class VersionsPrerequisiteError(VersionsError):
    """A required registry, environment value, or executable is unavailable."""


class VersionsRateLimitedError(VersionsPrerequisiteError):
    """A registry rate limit stopped resolution; exits ``RATE_LIMITED`` (5), distinct from 3."""


class VersionsOperationError(VersionsError):
    """A native resolver or writer failed after its prerequisites were found."""


@dataclass(frozen=True)
class TargetResult:
    target_id: str
    version: str
    resolved_at: datetime
    age_cutoff: datetime
    sources: Mapping[str, Candidate]
    override: bool
    reason: str | None
    expires: str | None
    owner: str

    def state(self) -> dict[str, object]:
        return {
            "version": self.version,
            "resolved_at": _timestamp(self.resolved_at),
            "age_cutoff": _timestamp(self.age_cutoff),
            "sources": {
                source_type: {
                    "version": candidate.version,
                    "released_at": _timestamp(candidate.released_at),
                    "age_source": candidate.age_source,
                    **({"tag": candidate.tag} if candidate.tag else {}),
                    **({"digest": candidate.digest} if candidate.digest else {}),
                }
                for source_type, candidate in sorted(self.sources.items())
            },
            **({"provenance": self.reason} if self.reason else {}),
        }


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_document(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            result = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VersionsError(f"could not read {path}: {exc}") from exc
    return result


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _toml_key(value: str) -> str:
    if _BARE_KEY.fullmatch(value):
        return value
    return json.dumps(value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise VersionsError(f"cannot render TOML value of type {type(value).__name__}")


def _table_header(path: tuple[str, ...]) -> str:
    return "[" + ".".join(_toml_key(part) for part in path) + "]"


def _render_table_tree(path: tuple[str, ...], table: Mapping[str, object]) -> list[str]:
    """Text lines for a NEW table subtree: a blank line, then each header with its scalars.

    A table that only holds sub-tables gets no header of its own (TOML defines it implicitly).
    """
    scalars = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
    lines: list[str] = []
    if scalars:
        lines.extend(("", _table_header(path)))
        lines.extend(f"{_toml_key(str(key))} = {_toml_value(value)}" for key, value in scalars)
    for key, child in table.items():
        if isinstance(child, dict):
            lines.extend(_render_table_tree((*path, str(key)), child))
    return lines


def _prune_empty(table: Mapping[str, object]) -> dict[str, object]:
    """Copy of a TOML document without empty tables (an empty header carries no value)."""
    pruned: dict[str, object] = {}
    for key, value in table.items():
        if isinstance(value, dict):
            value = _prune_empty(value)
            if not value:
                continue
        pruned[key] = value
    return pruned


def _split_lines(text: str) -> list[str]:
    """Split on ``\\n`` only (``str.splitlines`` also breaks on U+2028 and others), keeping every byte."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _scan_toml_line(line: str, multiline: str | None, depth: int) -> tuple[str | None, int, int]:
    """Advance the TOML lexical state over one line.

    Returns the open multi-line string delimiter (or None), the open ``[``/``{`` depth and the
    index where code ends (a ``#`` comment start outside any string, else the line length).
    """
    index, length = 0, len(line)
    while index < length:
        char = line[index]
        if multiline:
            if multiline == '"""' and char == "\\":
                index += 2
            elif line.startswith(multiline, index):
                while index < length and line[index] == multiline[0]:
                    index += 1
                multiline = None
            else:
                index += 1
        elif char == "#":
            return multiline, depth, index
        elif line.startswith(('"""', "'''"), index):
            multiline = line[index:index + 3]
            index += 3
        elif char == '"':
            index += 1
            while index < length and line[index] != '"':
                index += 2 if line[index] == "\\" else 1
            index += 1
        elif char == "'":
            closing = line.find("'", index + 1)
            index = length if closing < 0 else closing + 1
        else:
            depth += (char in "[{") - (char in "]}")
            index += 1
    return multiline, depth, length


@dataclass(frozen=True)
class _TomlStatement:
    """One top-level TOML statement: a header, a ``key = value`` entry, or anything else."""

    kind: str  # "table", "array-table", "key" or "other" (comment, blank, dotted key)
    first: int
    last: int
    path: tuple[str, ...] = ()
    key: str | None = None
    value_span: tuple[int, int] = (0, 0)


_KEY_LINE = re.compile(r"""^\s*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*=\s*""")


def _header_path(code: str) -> tuple[str, ...]:
    node: Any = tomllib.loads(code + "\n")
    path: list[str] = []
    while node:
        ((key, node),) = node.items()
        path.append(key)
    return tuple(path)


def _toml_statements(lines: list[str]) -> list[_TomlStatement]:
    statements: list[_TomlStatement] = []
    index = 0
    while index < len(lines):
        first = index
        multiline, depth, code_end = _scan_toml_line(lines[index], None, 0)
        while (multiline or depth > 0) and index + 1 < len(lines):
            index += 1
            multiline, depth, _end = _scan_toml_line(lines[index], multiline, depth)
        code = lines[first][:code_end].rstrip()
        match = _KEY_LINE.match(code)
        if code.lstrip().startswith("[["):
            statement = _TomlStatement("array-table", first, index)
        elif code.lstrip().startswith("["):
            statement = _TomlStatement("table", first, index, path=_header_path(code.strip()))
        elif match:
            key = next(iter(tomllib.loads(match.group(1) + " = 0")))
            statement = _TomlStatement("key", first, index, key=key, value_span=(match.end(), len(code)))
        else:
            statement = _TomlStatement("other", first, index)
        statements.append(statement)
        index += 1
    return statements


@dataclass
class _Block:
    """A ``[table]`` header with the plain ``key = value`` statements that follow it."""

    path: tuple[str, ...]
    header: _TomlStatement
    end: int
    keys: dict[str, _TomlStatement]


def _blocks(statements: list[_TomlStatement]) -> list[_Block]:
    blocks: list[_Block] = []
    current: _Block | None = None
    for statement in statements:
        if statement.kind == "table":
            current = _Block(statement.path, statement, statement.last, {})
            blocks.append(current)
        elif statement.kind == "array-table":
            current = None
        elif statement.kind == "key" and current is not None:
            current.end = statement.last
            current.keys.setdefault(str(statement.key), statement)
    return blocks


def _diff_tables(old: Mapping[str, object], new: Mapping[str, object], path: tuple[str, ...], out: list) -> None:
    """Edit operations ``(kind, table path, key, value)`` that turn ``old`` into ``new``."""
    for key, value in new.items():
        if key not in old:
            out.append(("add-table" if isinstance(value, dict) else "add-key", path, key, value))
        elif isinstance(value, dict) and isinstance(old[key], dict):
            _diff_tables(old[key], value, (*path, key), out)
        elif isinstance(value, dict) or isinstance(old[key], dict):
            out.append(("retype", path, key, value))
        elif value != old[key]:
            out.append(("set", path, key, value))
    for key, value in old.items():
        if key not in new:
            out.append(("del-table" if isinstance(value, dict) else "del-key", path, key, value))


def _describe_edit(operation: tuple) -> str:
    kind, table, key, value = operation
    where = _table_header(table) if table else "the top level"
    name = _toml_key(str(key))
    if kind == "set":
        return f"in {where} change {name} to {_toml_value(value)}"
    if kind == "add-key":
        return f"in {where} add the line {name} = {_toml_value(value)}"
    if kind == "del-key":
        return f"in {where} delete the key {name}"
    if kind == "del-table":
        return f"delete the table {_table_header((*table, key))}"
    if kind == "add-table":
        return "add the table text:" + "\n".join(_render_table_tree((*table, key), value))
    return f"in {where} replace {name} (it changes between a table and a value)"


def _cannot_edit(path: Path, reason: str, edit: str) -> VersionsError:
    return VersionsError(
        f"cannot update {path} without rewriting its [versions] region ({reason}); no file was changed. "
        f"Make this edit by hand, then rerun: {edit}"
    )


def _add_table(lines: list[str], blocks: list[_Block], table: tuple[str, ...], key: str, value: dict, eol: str) -> list[str]:
    rendered = _render_table_tree((*table, key), value)
    anchor = None
    for depth in range(len(table), 0, -1):
        ends = [block.end for block in blocks if block.path[:depth] == table[:depth]]
        if ends:
            anchor = max(ends)
            break
    if anchor is None:
        anchor = len(lines) - 1
        if not lines or not lines[-1].strip():
            rendered = rendered[1:]
    if anchor >= 0 and not lines[anchor].endswith("\n"):
        lines[anchor] += eol
    lines[anchor + 1:anchor + 1] = [line + eol for line in rendered]
    return lines


def _apply_edit(path: Path, lines: list[str], operation: tuple) -> list[str]:
    """Apply one edit to the text lines, touching only the lines it owns; refuse otherwise."""
    kind, table, key, value = operation
    statements = _toml_statements(lines)
    blocks = _blocks(statements)
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    edit = _describe_edit(operation)
    if kind == "retype":
        raise _cannot_edit(path, f"{key!r} changes between a table and a value", edit)
    if kind == "add-table":
        return _add_table(lines, blocks, table, key, value, eol)
    if kind == "del-table":
        target = (*table, key)
        doomed = [block for block in blocks if block.path[:len(target)] == target]
        if not doomed:
            raise _cannot_edit(path, f"{_table_header(target)} is not written as a [table] header", edit)
        for block in sorted(doomed, key=lambda item: item.header.first, reverse=True):
            del lines[block.header.first:block.end + 1]
        return lines
    block = next((item for item in blocks if item.path == table), None)
    if block is None:
        raise _cannot_edit(
            path, f"{_table_header(table) if table else 'the top level'} is not written as a [table] header", edit,
        )
    if kind == "add-key":
        if not lines[block.end].endswith("\n"):
            lines[block.end] += eol
        lines.insert(block.end + 1, f"{_toml_key(str(key))} = {_toml_value(value)}{eol}")
        return lines
    statement = block.keys.get(key)
    if statement is None or statement.first != statement.last:
        raise _cannot_edit(
            path, f"{key!r} is not a plain single-line `key = value` entry of {_table_header(table)}", edit,
        )
    if kind == "del-key":
        del lines[statement.first]
        return lines
    line = lines[statement.first]
    start, stop = statement.value_span
    lines[statement.first] = line[:start] + _toml_value(value) + line[stop:]
    return lines


def _edit_versions_text(path: Path, original: str, old_document: Mapping[str, object], new_versions: Mapping[str, object]) -> str:
    """The text of ``original`` with only the [versions] values that changed edited in place.

    The edited text is parsed again and must equal the requested document; otherwise the edit is
    refused (never written) with the exact hand edit. The whole-region rewrite is not a fallback.
    """
    before = _prune_empty(old_document)
    after = _prune_empty({**old_document, "versions": new_versions})
    operations: list[tuple] = []
    _diff_tables(before, after, (), operations)
    lines = _split_lines(original)
    for operation in operations:
        lines = _apply_edit(path, lines, operation)
    text = "".join(lines)
    edits = "; ".join(_describe_edit(operation) for operation in operations)
    try:
        reparsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _cannot_edit(path, f"the edited text would not parse: {exc}", edits) from exc
    if _prune_empty(reparsed) != after:
        raise _cannot_edit(path, "the edited text would not match the requested values", edits)
    return text


def _update_versions_file(path: Path, mutate) -> None:
    original = path.read_bytes().decode("utf-8")
    document = _load_document(path)
    versions = copy.deepcopy(document.get("versions", {}))
    updated = mutate(versions)
    if not isinstance(updated, dict):
        raise VersionsError("internal error: version document update did not return a table")
    try:
        parse_versions_section(updated, f"{path} [versions]", allow_partial=True)
    except ValueError as exc:
        raise VersionsError(str(exc)) from exc
    rendered = _edit_versions_text(path, original, document, updated)
    if rendered != original:
        _write_text_atomic(path, rendered)


class _FileTransaction:
    """Restore all declared output files if any native writer fails."""

    def __init__(self) -> None:
        self._before: dict[Path, tuple[bytes, int] | None] = {}

    def track(self, path: Path) -> None:
        path = path.absolute()
        if path.is_symlink():
            raise VersionsError(f"refusing to replace symlink output {path}")
        if path.exists() and not path.is_file():
            raise VersionsError(f"refusing to replace non-file output {path}")
        path = path.resolve(strict=False)
        if path in self._before:
            return
        if path.is_file():
            self._before[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
        else:
            self._before[path] = None

    def rollback(self) -> None:
        for path, data in reversed(list(self._before.items())):
            if data is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                content, mode = data
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.rollback.", dir=path.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(temporary, mode)
                    os.replace(temporary, path)
                except BaseException:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise


def _version_key(candidate: Candidate) -> tuple:
    from cmru.version_registry import _semver_key

    key = _semver_key(candidate.version)
    if key is None:
        raise VersionsError(f"unsupported version spelling {candidate.version!r}")
    return key


def _target_sources(target: Mapping[str, object]) -> dict[str, Mapping[str, str]]:
    return {
        source_type: value
        for source_type, value in target.items()
        if source_type in {"npm", "pypi", "go", "oci"} and isinstance(value, dict)
    }


def _resolve_target(
    target_id: str,
    target: Mapping[str, object],
    *,
    age_window_days: int,
    resolved_at: datetime,
    owner: str,
) -> TargetResult:
    source_tables = _target_sources(target)
    if not source_tables:
        raise VersionsError(f"target {target_id!r} has no configured registry sources")
    constraint = str(target["constraint"])
    cutoff = resolved_at - timedelta(days=age_window_days)
    override = target.get("version")
    selected: dict[str, Candidate] = {}
    try:
        oci_source = source_tables.get("oci")
        if isinstance(oci_source, dict) and oci_source.get("selection", "semver") == "rolling":
            if set(source_tables) != {"oci"} or target.get("mode") != "single" or constraint != "*":
                raise VersionsError(
                    f"target {target_id!r} rolling OCI selection requires one OCI source, mode='single', and constraint='*'"
                )
            if override is not None:
                raise VersionsError(f"target {target_id!r} rolling OCI selection does not support exact overrides")
            candidate = oci_rolling_candidate(oci_source)
            if candidate.released_at > cutoff:
                warning = _age_evidence_warning(owner, target_id, candidate)
                warning_text = f"; {warning}" if warning else ""
                raise VersionsError(
                    f"target {target_id!r} rolling OCI tag {candidate.tag!r} is newer than the "
                    f"{age_window_days}-day age cutoff ({_timestamp(candidate.released_at)} > "
                    f"{_timestamp(cutoff)}){warning_text}"
                )
            return TargetResult(
                target_id=target_id, version=candidate.version, resolved_at=resolved_at,
                age_cutoff=cutoff, sources={"oci": candidate}, override=False, reason=None,
                expires=None, owner=owner,
            )
        if override is not None:
            version = str(override)
            if not version_satisfies(version, constraint):
                raise VersionsError(
                    f"target {target_id!r} exact override {version!r} violates {constraint!r}"
                )
            for source_type, source in source_tables.items():
                selected[source_type] = candidate_for(source_type, source, version)
                if normalized_version(selected[source_type].version) != normalized_version(version):
                    raise VersionsError(
                        f"target {target_id!r} source {source_type} does not publish exact version {version!r}"
                    )
            too_new = [candidate for candidate in selected.values() if candidate.released_at > cutoff]
            reason = str(target["reason"])
            expires = target.get("expires")
            if expires is not None and str(expires) <= resolved_at.date().isoformat():
                raise VersionsError(
                    f"target {target_id!r} exact override expired on {expires}; update or remove it"
                )
            if too_new and expires is None:
                freshest = max(too_new, key=lambda item: item.released_at)
                warning = _age_evidence_warning(owner, target_id, freshest)
                warning_text = f"; {warning}" if warning else ""
                raise VersionsError(
                    f"target {target_id!r} override {version!r} bypasses the {age_window_days}-day age window "
                    f"(published {_timestamp(freshest.released_at)}){warning_text}; "
                    "set a future expires date after review"
                )
            return TargetResult(
                target_id=target_id, version=version, resolved_at=resolved_at,
                age_cutoff=cutoff, sources=selected, override=True, reason=reason,
                expires=str(expires) if expires is not None else None, owner=owner,
            )

        pools: dict[str, dict[str, Candidate]] = {}
        for source_type, source in source_tables.items():
            # A single-source OCI target needs only the newest age-eligible tag,
            # so let the scan stop there instead of fetching every tag's
            # manifests (Docker Hub meters each manifest GET).
            scan_args = (
                (cutoff,) if source_type == "oci" and len(source_tables) == 1 and target.get("mode") == "single"
                else ()
            )
            records = candidates(source_type, source, constraint, *scan_args)
            compatible = {
                key: candidate for key, candidate in records.items()
                if version_satisfies(candidate.version, constraint)
            }
            eligible = {
                key: candidate for key, candidate in compatible.items()
                if candidate.released_at <= cutoff
            }
            if not eligible:
                if not compatible:
                    detail = "registry returned no compatible candidates"
                else:
                    newest = max(compatible.values(), key=lambda item: item.released_at)
                    detail = f"newest compatible release is {_timestamp(newest.released_at)}, newer than the cutoff"
                    warning = _age_evidence_warning(owner, target_id, newest)
                    if warning:
                        detail += f"; {warning}"
                raise VersionsError(
                    f"target {target_id!r} source {source_type} has no age-eligible version: {detail} "
                    f"(age_window_days={age_window_days}, cutoff={_timestamp(cutoff)})"
                )
            pools[source_type] = eligible

        mode = target["mode"]
        if mode == "single":
            source_type = next(iter(pools))
            chosen = max(pools[source_type].values(), key=_version_key)
            selected[source_type] = chosen
            version = chosen.version
        else:
            common = set.intersection(*(set(pool) for pool in pools.values()))
            if not common:
                raise VersionsError(
                    f"aligned target {target_id!r} has no age-eligible version shared by "
                    f"{', '.join(sorted(pools))}"
                )
            selected_key = max(
                common,
                key=lambda key: max(_version_key(pool[key]) for pool in pools.values()),
            )
            selected = {source_type: pool[selected_key] for source_type, pool in pools.items()}
            version = selected[next(iter(source_tables))].version
            if any(
                normalized_version(candidate.version) != normalized_version(version)
                for candidate in selected.values()
            ):
                raise VersionsError(
                    f"aligned target {target_id!r} matched inconsistent source spellings for {selected_key!r}"
                )
        return TargetResult(
            target_id=target_id, version=version, resolved_at=resolved_at,
            age_cutoff=cutoff, sources=selected, override=False, reason=None,
            expires=None, owner=owner,
        )
    except RegistryError as exc:
        message = str(exc)
        if isinstance(exc, RegistryRateLimitedError):
            raise VersionsRateLimitedError(f"target {target_id!r}: {message}") from exc
        if message.startswith("registry does not publish exact version"):
            raise VersionsError(f"target {target_id!r}: {message}") from exc
        raise VersionsPrerequisiteError(f"target {target_id!r}: {message}") from exc


RESUME_TTL = timedelta(hours=24)


def _result_record(result: TargetResult) -> dict[str, object]:
    return {
        "target_id": result.target_id,
        "version": result.version,
        "resolved_at": _timestamp(result.resolved_at),
        "age_cutoff": _timestamp(result.age_cutoff),
        "override": result.override,
        "reason": result.reason,
        "expires": result.expires,
        "owner": result.owner,
        "sources": {
            source_type: {
                "version": candidate.version,
                "released_at": _timestamp(candidate.released_at),
                "age_source": candidate.age_source,
                "tag": candidate.tag,
                "digest": candidate.digest,
            }
            for source_type, candidate in result.sources.items()
        },
    }


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _result_from_record(record: Mapping[str, Any]) -> TargetResult:
    return TargetResult(
        target_id=record["target_id"], version=record["version"],
        resolved_at=_parse_timestamp(record["resolved_at"]),
        age_cutoff=_parse_timestamp(record["age_cutoff"]),
        override=bool(record["override"]), reason=record["reason"], expires=record["expires"],
        owner=record["owner"],
        sources={
            source_type: Candidate(
                version=item["version"], released_at=_parse_timestamp(item["released_at"]),
                age_source=item["age_source"], tag=item["tag"], digest=item["digest"],
            )
            for source_type, item in record["sources"].items()
        },
    )


def _resume_path(config_path: Path) -> Path:
    """Per-config resume file in the user cache, never inside the repository."""
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    digest = hashlib.sha256(str(config_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(cache_home) / "cmru" / f"versions-resume-{digest}.json"


class _ResumeStore:
    """Targets resolved by an interrupted ``cmru versions resolve`` (registry rate limit).

    Each entry is keyed by owner and target id and carries a fingerprint of the
    target declaration plus the age window, so an edited target is re-resolved.
    Entries older than ``RESUME_TTL`` are ignored. Saving is best-effort: a
    cache that cannot be written must not hide the rate-limit message.
    """

    def __init__(self, path: Path, now: datetime, *, persist: bool = True):
        self.path = path
        self.now = now
        # A dry run must not leave entries a later real resolve would reuse.
        self.persist = persist
        self.entries: dict[str, dict[str, Any]] = {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            loaded = document["entries"] if document["schema"] == 1 else {}
            if isinstance(loaded, dict):
                self.entries = loaded
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def fingerprint(target: Mapping[str, object], age_window_days: int, owner: str) -> str:
        declared = {key: value for key, value in target.items() if key != "resolved"}
        text = json.dumps([declared, age_window_days, owner], sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _key(owner: str, target_id: str) -> str:
        return f"{owner}\0{target_id}"

    def get(self, owner: str, target_id: str, fingerprint: str) -> TargetResult | None:
        entry = self.entries.get(self._key(owner, target_id))
        try:
            if entry is None or entry["fingerprint"] != fingerprint:
                return None
            result = _result_from_record(entry["result"])
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return result if self.now - result.resolved_at <= RESUME_TTL else None

    def reusable(self, target: Mapping[str, object]) -> bool:
        """False for targets whose result is time- or tag-movement-dependent.

        Rolling OCI tags can move their digest within the TTL; an override
        whose ``expires`` date falls inside the TTL window must be re-checked.
        """
        oci = target.get("oci")
        if isinstance(oci, dict) and oci.get("selection", "semver") == "rolling":
            return False
        expires = target.get("expires")
        if expires is not None and str(expires) <= (self.now + RESUME_TTL).date().isoformat():
            return False
        return True

    def put(self, owner: str, fingerprint: str, result: TargetResult) -> None:
        self.entries[self._key(owner, result.target_id)] = {
            "fingerprint": fingerprint, "result": _result_record(result),
        }
        if not self.persist:
            return
        try:
            _write_text_atomic(self.path, json.dumps({"schema": 1, "entries": self.entries}, indent=1, sort_keys=True) + "\n")
        except OSError:
            pass

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _resolve_targets(
    targets: Mapping[str, object],
    *,
    age_window_days: int,
    resolved_at: datetime,
    owner: str,
    resume: _ResumeStore | None = None,
) -> dict[str, TargetResult]:
    results: dict[str, TargetResult] = {}
    reused = 0
    for target_id, target in targets.items():
        if not isinstance(target, dict):
            raise VersionsError(f"target {target_id!r} is not a table")
        usable = bool(resume) and resume.reusable(target)
        fingerprint = _ResumeStore.fingerprint(target, age_window_days, owner) if usable else ""
        cached = resume.get(owner, target_id, fingerprint) if usable else None
        if cached is not None:
            results[target_id] = cached
            reused += 1
            continue
        results[target_id] = _resolve_target(
            target_id, target, age_window_days=age_window_days,
            resolved_at=resolved_at, owner=owner,
        )
        if usable:
            resume.put(owner, fingerprint, results[target_id])
    if reused:
        print(f"cmru versions: reused {reused} target(s) from an earlier interrupted run ({owner})", file=sys.stderr)
    return results


def _selected_projects(forge: ForgeConfig, context: InvocationContext, target: object) -> list[str]:
    """``target`` is a parsed ``target_argument()`` value (or a legacy string)."""
    if context.config_kind == "project":
        order = list(forge.projects)
    elif forge.orchestration:
        order = list(forge.orchestration.project_order)
    else:
        order = list(forge.projects)
    try:
        return select_target_names(
            target,
            forge.projects,
            order,
            context_project=context.project_name,
        )
    except TargetSelectionError as exc:
        raise VersionsError(str(exc)) from exc


def _project_root(forge: ForgeConfig, name: str) -> Path:
    project = forge.projects[name]
    if project.project_root is not None:
        return project.project_root.resolve()
    if forge.orchestration is not None:
        path = forge.orchestration.project_configs.get(name)
        if path is not None:
            return path.parent.resolve()
    return forge.repo_root.resolve()


def _versions_for_project(forge: ForgeConfig, name: str) -> dict[str, object]:
    try:
        return effective_versions_for_project(forge, name)
    except SystemExit as exc:
        raise VersionsError(f"could not construct effective version config for {name}") from exc


def _local_target_overlays(forge: ForgeConfig, name: str) -> dict[str, dict[str, object]]:
    local = forge.projects[name].versions.get("targets", {})
    root = forge.versions.get("targets", {})
    if not isinstance(local, dict) or not isinstance(root, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for target_id, overlay in local.items():
        if not isinstance(overlay, dict):
            continue
        base = root.get(target_id)
        merged = (
            deep_merge_versions(base, overlay)
            if isinstance(base, dict) else copy.deepcopy(overlay)
        )
        try:
            result[target_id] = parse_versions_section(
                {"targets": {target_id: merged}}, f"projects.{name}.versions",
            )["targets"][target_id]
        except ValueError as exc:
            raise VersionsError(f"invalid effective project target {name}:{target_id}: {exc}") from exc
    return result


def _merged_outputs(forge: ForgeConfig, name: str) -> dict[str, dict[str, str]]:
    root_outputs = forge.versions.get("outputs", {})
    project_outputs = forge.projects[name].versions.get("outputs", {})
    merged = deep_merge_versions(
        root_outputs if isinstance(root_outputs, dict) else {},
        project_outputs if isinstance(project_outputs, dict) else {},
    )
    return merged


def _report_record(result: TargetResult, declaration: Mapping[str, object], current: object = None) -> dict:
    recorded_version = current.get("version") if isinstance(current, dict) else current
    recorded_sources = current.get("sources", {}) if isinstance(current, dict) else {}
    matches = recorded_version == result.version
    if matches:
        for source_type, candidate in result.sources.items():
            if candidate.digest is None:
                continue
            recorded_source = recorded_sources.get(source_type, {}) if isinstance(recorded_sources, dict) else {}
            if not isinstance(recorded_source, dict) or recorded_source.get("digest") != candidate.digest:
                matches = False
                break
    return {
        "target": result.target_id,
        "owner": result.owner,
        "eligible_version": result.version,
        "recorded_version": recorded_version if isinstance(recorded_version, str) else None,
        "mode": declaration.get("mode"),
        "constraint": declaration.get("constraint"),
        "override": result.override,
        "reason": result.reason,
        "expires": result.expires,
        "resolved_at": _timestamp(result.resolved_at),
        "age_cutoff": _timestamp(result.age_cutoff),
        "sources": {
            source_type: {
                "version": candidate.version,
                "released_at": _timestamp(candidate.released_at),
                "age_source": candidate.age_source,
                **({"tag": candidate.tag} if candidate.tag else {}),
                **({"digest": candidate.digest} if candidate.digest else {}),
            }
            for source_type, candidate in sorted(result.sources.items())
        },
        "status": (
            "override" if result.override else
            "matches" if matches else
            "refresh-available" if current is not None else
            "unresolved"
        ),
    }


def _emit_age_evidence_warnings(
    root_results: Mapping[str, TargetResult],
    project_results: Mapping[str, Mapping[str, TargetResult]],
) -> None:
    """Call out timestamp evidence that is weaker than registry publication time."""
    warnings: set[str] = set()
    all_results = [("root", root_results), *sorted(project_results.items())]
    for owner, results in all_results:
        for target_id, result in results.items():
            for candidate in result.sources.values():
                warning = _age_evidence_warning(owner, target_id, candidate)
                if warning:
                    warnings.add(warning)
    for warning in sorted(warnings):
        print(warning, file=sys.stderr)


def _age_evidence_warning(owner: str, target_id: str, candidate: Candidate) -> str | None:
    if candidate.age_source == "go-proxy-info-vcs-commit-time":
        return (
            f"CMRU versions: warning: {owner}:{target_id} uses Go proxy .info Time "
            "(VCS commit time), not proxy publication time"
        )
    if candidate.age_source == "oci-image-created-fallback":
        return (
            f"CMRU versions: warning: {owner}:{target_id} uses publisher-supplied OCI "
            "image-created time because the registry has no Last-Modified timestamp"
        )
    return None


def _read_recorded(target: Mapping[str, object]) -> object:
    resolved = target.get("resolved")
    return resolved if isinstance(resolved, dict) else None


def _target_declarations_for_project(forge: ForgeConfig, name: str) -> dict[str, dict[str, object]]:
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    result = {key: value for key, value in root_targets.items() if isinstance(value, dict)}
    result.update(_local_target_overlays(forge, name))
    return result




def _recorded_versions(
    forge: ForgeConfig,
    root_results: Mapping[str, TargetResult],
    project_results: Mapping[str, Mapping[str, TargetResult]],
    declarations: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> list[dict]:
    records: list[dict] = []
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    for target_id, result in root_results.items():
        declaration = root_targets.get(target_id, {})
        records.append(_report_record(
            result,
            declaration if isinstance(declaration, dict) else {},
            _read_recorded(declaration) if isinstance(declaration, dict) else None,
        ))
    for name, project_targets in project_results.items():
        raw_local = forge.projects[name].versions.get("targets", {})
        if not isinstance(raw_local, dict):
            raw_local = {}
        for target_id, result in project_targets.items():
            declaration = declarations[name].get(target_id, {})
            local = raw_local.get(target_id, {})
            recorded = _read_recorded(local) if isinstance(local, dict) else None
            records.append(_report_record(result, declaration, recorded))
    return records


def _safe_relative_path(root: Path, value: str, *, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise VersionsError(f"{label} must be a relative path within {root}")
    unresolved = root / relative
    current = unresolved
    while current != root and current != current.parent:
        if current.is_symlink():
            raise VersionsError(f"{label} traverses symlink path {current}")
        current = current.parent
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise VersionsError(f"{label} resolves outside project directory: {candidate}") from exc
    return candidate


def _template_artifacts(
    project_root: Path,
    outputs: Mapping[str, Mapping[str, str]],
    results: Mapping[str, TargetResult],
    date_string: str,
) -> list[tuple[Path, str]]:
    if not outputs:
        return []
    try:
        from jinja2 import Environment, StrictUndefined
    except ImportError as exc:
        raise VersionsError(
            "configured [versions.outputs] require Jinja2; install CMRU with its "
            "versions-templates extra"
        ) from exc
    context = {
        "resolved_at": max((_timestamp(item.resolved_at) for item in results.values()), default=None),
        "targets": {
            target_id: {
                "version": result.version,
                "override": result.override,
                "reason": result.reason,
                "expires": result.expires,
                "owner": result.owner,
                "age_cutoff": _timestamp(result.age_cutoff),
                "sources": {
                    source_type: {
                        "version": item.version,
                        "released_at": _timestamp(item.released_at),
                        "age_source": item.age_source,
                        "tag": item.tag,
                        "digest": item.digest,
                    }
                    for source_type, item in sorted(result.sources.items())
                },
            }
            for target_id, result in sorted(results.items())
        },
    }
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    artifacts: list[tuple[Path, str]] = []
    for output_id, config in outputs.items():
        template_path = _safe_relative_path(
            project_root, config["template"], label=f"versions.outputs.{output_id}.template",
        )
        if not template_path.is_file():
            raise VersionsError(f"configured version template does not exist: {template_path}")
        try:
            source = template_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise VersionsError(f"could not read version template {output_id!r}: {exc}") from exc
        try:
            rendered = environment.from_string(source).render(**context)
        except Exception as exc:  # noqa: BLE001
            # The one justified broad catch (CMRU-C): the render evaluates
            # user-authored template expressions, which may raise anything
            # ({{ 1/0 }}, {{ 'a' + 1 }}). That is bad user data (exit 2), not a
            # programmer error; every other site keeps a narrow tuple.
            raise VersionsError(f"could not render version output {output_id!r}: {exc}") from exc
        stable_path = _safe_relative_path(project_root, config["path"], label=f"versions.outputs.{output_id}.path")
        dated_name = config["dated_path"].replace("{date}", date_string)
        dated_path = _safe_relative_path(
            project_root, dated_name, label=f"versions.outputs.{output_id}.dated_path",
        )
        artifacts.extend(((dated_path, rendered), (stable_path, rendered)))
    return artifacts


def _native_output_files(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
    *,
    now: datetime,
    outputs: Mapping[str, Mapping[str, str]],
    oci_results: Mapping[str, TargetResult] | None = None,
) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    date_string = now.astimezone(timezone.utc).strftime("%Y%m%d")
    oci_items: dict[str, dict[str, object]] = {}

    for target_id, result in (oci_results if oci_results is not None else results).items():
        declaration = declarations.get(target_id, {})
        for source_type, candidate in result.sources.items():
            source = declaration.get(source_type, {})
            if not isinstance(source, dict):
                continue
            if source_type == "oci":
                oci_items[target_id] = {
                    "image": source["image"],
                    "version": candidate.version,
                    "tag": candidate.tag,
                    "released_at": _timestamp(candidate.released_at),
                    "age_source": candidate.age_source,
                    "reason": result.reason,
                    **({"digest": candidate.digest} if candidate.digest else {}),
                }

    if oci_items:
        payload = {
            "schema_version": 1,
            "generated_by": "cmru versions resolve",
            "resolved_at": _timestamp(now),
            "targets": oci_items,
        }
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        oci_paths = (
            _safe_relative_path(project_root, f"versions/oci-images-{date_string}.json", label="generated OCI path"),
            _safe_relative_path(project_root, "versions/oci-images.json", label="generated OCI path"),
        )
        for path in oci_paths:
            if path.exists():
                try:
                    previous = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise VersionsError(f"refusing to overwrite an unrecognized OCI output: {path}") from exc
                if not isinstance(previous, dict) or previous.get("generated_by") != "cmru versions resolve":
                    raise VersionsError(f"refusing to overwrite an OCI output not generated by CMRU: {path}")
        files.extend((path, content) for path in oci_paths)

    files.extend(_template_artifacts(project_root, outputs, results, date_string))
    _assert_unique_output_paths(files)
    return files


def _assert_unique_output_paths(files: list[tuple[Path, str]]) -> None:
    seen: set[Path] = set()
    for path, _content in files:
        canonical = path.resolve(strict=False)
        if canonical in seen:
            raise VersionsError(f"multiple version outputs target the same file: {canonical}")
        seen.add(canonical)


def _compile_python_constraints(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
) -> list[tuple[Path, str]]:
    selected: dict[str, tuple[str, str, TargetResult, Mapping[str, str]]] = {}
    for target_id, result in results.items():
        candidate = result.sources.get("pypi")
        if candidate is None:
            continue
        declaration = declarations.get(target_id, {})
        source = declaration.get("pypi", {})
        if not isinstance(source, dict):
            raise VersionsError(f"PyPI source declaration missing for target {target_id!r}")
        name = re.sub(r"[-_.]+", "-", str(source["name"])).lower()
        previous = selected.get(name)
        if previous and previous[0] != candidate.version:
            raise VersionsError(f"Python package {name!r} has conflicting target versions")
        if previous and previous[1] != target_id:
            raise VersionsError(f"multiple PyPI targets resolve the same Python package {name!r}")
        selected[name] = (candidate.version, target_id, result, source)
    if not selected:
        return []

    registries = {str(item[3]["registry"]).rstrip("/") for item in selected.values()}
    if len(registries) != 1:
        raise VersionsError(
            "all PyPI targets written to one constraints artifact must use the same registry; "
            "split them into separate CMRU projects or use separate native outputs"
        )
    registry = next(iter(registries))
    if registry == "https://pypi.org":
        index_url = "https://pypi.org/simple"
    elif registry.endswith("/simple"):
        index_url = registry
    elif registry.endswith("/pypi"):
        index_url = registry[:-5].rstrip("/") + "/simple"
    else:
        index_url = registry + "/simple"

    global_cutoff = min(item[2].age_cutoff for item in selected.values())
    date_string = max(item[2].resolved_at for item in selected.values()).astimezone(timezone.utc).strftime("%Y%m%d")
    output_paths = [
        _safe_relative_path(
            project_root, f"constraints/constraints-{date_string}.txt", label="generated constraints path",
        ),
        _safe_relative_path(project_root, "constraints/constraints.txt", label="generated constraints path"),
    ]
    for path in output_paths:
        if path.exists():
            try:
                marker = path.read_text(encoding="utf-8").startswith(
                    "# Generated by cmru versions resolve"
                )
            except OSError as exc:
                raise VersionsError(f"could not inspect generated constraints output {path}: {exc}") from exc
            if not marker:
                raise VersionsError(f"refusing to overwrite a non-CMRU constraints file: {path}")
    command = [
        "uv", "pip", "compile", "-",
        "--index", f"cmru={index_url}",
        "--default-index", index_url,
        "--index-strategy", "first-index",
        "--exclude-newer", _timestamp(global_cutoff),
    ]
    for name, (_version, _target_id, result, _source) in sorted(selected.items()):
        candidate = result.sources["pypi"]
        if candidate.released_at > result.age_cutoff:
            command.extend(("--exclude-newer-package", f"{name}=false"))
        elif result.age_cutoff > global_cutoff:
            command.extend(("--exclude-newer-package", f"{name}={_timestamp(result.age_cutoff)}"))

    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for _version, _target_id, _result, source in selected.values()
    }
    if len(auth_refs) != 1:
        raise VersionsPrerequisiteError(
            "PyPI targets using one constraints index must share one credential environment-variable mapping"
        )
    environment = os.environ.copy()
    token_env, username_env, password_env = next(iter(auth_refs))
    if token_env:
        token = os.environ.get(str(token_env), "")
        if not token:
            raise VersionsPrerequisiteError(
                f"required registry token environment variable {token_env} is unset or empty"
            )
        environment["UV_INDEX_CMRU_USERNAME"] = "__token__"
        environment["UV_INDEX_CMRU_PASSWORD"] = token
    elif username_env:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} "
                f"and {password_env} must be set"
            )
        environment["UV_INDEX_CMRU_USERNAME"] = username
        environment["UV_INDEX_CMRU_PASSWORD"] = password
    requirements = "\n".join(
        f"{name}=={version}" for name, (version, _target_id, _result, _source) in sorted(selected.items())
    ) + "\n"
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            input=requirements,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=900,
        )
    except FileNotFoundError as exc:
        raise VersionsPrerequisiteError("Python targets require uv on PATH (install uv before resolving)") from exc
    except subprocess.TimeoutExpired as exc:
        raise VersionsOperationError("uv pip compile did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"uv pip compile failed (exit {completed.returncode}): {detail[-2000:]}")
    if not completed.stdout.strip():
        raise VersionsError("uv pip compile completed without producing constraints output")
    content = (
        "# Generated by cmru versions resolve; edit [versions.targets] instead.\n"
        f"# resolved-at: {_timestamp(max(item[2].resolved_at for item in selected.values()))}\n"
        f"# age-cutoff: {_timestamp(global_cutoff)}\n"
        + completed.stdout.rstrip()
        + "\n"
    )
    return [(path, content) for path in output_paths]


def _age_window(versions: Mapping[str, object]) -> int:
    value = versions.get("age_window_days", DEFAULT_AGE_WINDOW_DAYS)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VersionsError("effective versions.age_window_days must be a positive integer")
    return value


def _apply_resolved_state(path: Path, results: Mapping[str, TargetResult]) -> None:
    states = {target_id: result.state() for target_id, result in results.items()}

    def mutate(versions: dict) -> dict:
        targets = versions.setdefault("targets", {})
        if not isinstance(targets, dict):
            raise VersionsError(f"{path} [versions].targets is not a table")
        for target_id, state in states.items():
            target = targets.get(target_id)
            if not isinstance(target, dict):
                raise VersionsError(
                    f"resolved target {target_id!r} is not declared in {path}; "
                    "refusing to create a generated-only target"
                )
            target["resolved"] = state
        return versions

    _update_versions_file(path, mutate)


def _render_report(records: list[dict]) -> str:
    if not records:
        return "No version targets are configured. Run cmru versions init after declaring project dependencies."
    headers = ("OWNER", "TARGET", "RECORDED", "ELIGIBLE", "STATUS", "AGE SOURCE")
    rows = []
    for record in records:
        age_sources = ",".join(sorted({source["age_source"] for source in record["sources"].values()}))
        rows.append((
            str(record["owner"]), str(record["target"]), str(record["recorded_version"] or "—"),
            str(record["eligible_version"]), str(record["status"]), age_sources,
        ))
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows)
    return "\n".join(lines)


def _resolve_context(config_arg: str | None) -> tuple[InvocationContext, ForgeConfig]:
    config_path = Path(config_arg).expanduser() if config_arg else None
    context = resolve_invocation_context(config_path)
    forge = load_forge_config(context.config_path)
    return context, forge


















def _versions_init(forge: ForgeConfig, projects: list[str], *, dry_run: bool) -> list[str]:
    summaries: list[str] = []
    pending: dict[Path, tuple[str, str]] = {}
    for name in projects:
        project_root = _project_root(forge, name)
        config_path = (
            forge.orchestration.project_configs[name]
            if forge.orchestration is not None
            else project_root / PROJECT_CONFIG_FILENAME
        )
        document = _load_document(config_path)
        pristine = copy.deepcopy(document)
        versions = document.get("versions", {})
        if not isinstance(versions, dict):
            raise VersionsError(f"{config_path} [versions] must be a table")
        targets = versions.setdefault("targets", {})
        if not isinstance(targets, dict):
            raise VersionsError(f"{config_path} [versions].targets must be a table")
        effective = (
            deep_merge_versions(dict(forge.versions), versions)
            if forge.orchestration else copy.deepcopy(versions)
        )
        try:
            effective = parse_versions_section(effective, f"{config_path}: effective [versions]")
        except ValueError as exc:
            raise VersionsError(str(exc)) from exc
        discovery = effective.get("discovery", {})
        scope = discovery.get("scope", "shipped")
        pypi_extras = discovery.get("pypi_extras", [])
        requirements_files = discovery.get("requirements_files", [])
        discovery_note = f"{name}: dependency discovery scope={scope!r}"
        if pypi_extras:
            discovery_note += f"; PyPI extras={', '.join(pypi_extras)}"
        if requirements_files:
            discovery_note += f"; requirements files={', '.join(requirements_files)}"
        summaries.append(discovery_note)
        added = []
        shared = []
        facts, skipped = _init_facts(
            project_root,
            scope=scope,
            pypi_extras=pypi_extras,
            requirements_files=requirements_files,
        )
        for family, slug, constraint, source in facts:
            target_id = _target_id(family, slug)
            if target_id in targets:
                continue
            root_targets = forge.versions.get("targets", {})
            root_target = root_targets.get(target_id) if isinstance(root_targets, dict) else None
            if isinstance(root_target, dict):
                root_source = root_target.get(family)
                identity_key = "name" if family in {"npm", "pypi"} else "module"
                if not isinstance(root_source, dict) or not _same_source_identity(
                    family, root_source.get(identity_key), source.get(identity_key),
                ):
                    raise VersionsError(
                        f"root version target {target_id!r} does not match manifest source "
                        f"{family}:{source.get(identity_key)!r} in {project_root}"
                    )
                shared.append(target_id)
                continue
            targets[target_id] = {"mode": "single", "constraint": constraint, family: source}
            added.append(target_id)
        if not added:
            if shared:
                summaries.append(
                    f"{name}: shared root targets already cover {', '.join(shared)}; "
                    "project declarations are unchanged"
                )
            else:
                summaries.append(f"{name}: no new targets derived; declarations are unchanged")
        else:
            try:
                effective = (
                    deep_merge_versions(dict(forge.versions), versions)
                    if forge.orchestration else versions
                )
                parse_versions_section(effective, f"{config_path}: effective [versions]")
            except ValueError as exc:
                raise VersionsError(str(exc)) from exc
            if not dry_run:
                original = config_path.read_bytes().decode("utf-8")
                rendered = _edit_versions_text(config_path, original, pristine, versions)
                pending[config_path] = (original, rendered)
            summaries.append(f"{name}: {'would add' if dry_run else 'added'} {', '.join(added)}")
        for item in skipped:
            summaries.append(f"{name}: skipped manifest dependency {item}")

    if pending and not dry_run:
        try:
            for path, (_original, rendered) in pending.items():
                _write_text_atomic(path, rendered)
            validation_path = (
                forge.repo_root / ORCHESTRATION_CONFIG_FILENAME
                if forge.orchestration is not None
                else next(iter(pending))
            )
            load_forge_config(validation_path)
        except BaseException as exc:
            for path, (original, _rendered) in pending.items():
                _write_text_atomic(path, original)
            if isinstance(exc, SystemExit):
                raise VersionsError("generated version declarations failed CMRU config validation") from exc
            raise
    return summaries


def _init_facts(
    project_root: Path,
    *,
    scope: str = "shipped",
    pypi_extras: list[str] | tuple[str, ...] = (),
    requirements_files: list[str] | tuple[str, ...] = (),
) -> tuple[list[tuple[str, str, str, dict[str, str]]], list[str]]:
    """Derive declared package facts from the configured dependency scope."""
    if scope not in {"shipped", "all"}:
        raise VersionsError("versions.discovery.scope must be one of: shipped, all")
    if scope == "all" and pypi_extras:
        raise VersionsError("versions.discovery.pypi_extras is redundant when scope = 'all'")
    findings: list[tuple[str, str, str, dict[str, str]]] = []
    skipped: list[str] = []
    pypi_registry = "https://pypi.org"
    npm_registry = "https://registry.npmjs.org"
    go_proxy = "https://proxy.golang.org"

    def add_python_requirement(value: object, origin: str) -> None:
        if not isinstance(value, str):
            skipped.append(f"{origin}: dependency must be a string")
            return
        match = _REQUIREMENT.fullmatch(value.strip())
        if not match:
            if value.strip():
                skipped.append(f"{origin} {value!r}: unsupported requirement syntax")
            return
        name, constraint = match.groups()
        name = re.sub(r"[-_.]+", "-", name).lower()
        constraint = re.sub(r"\s*,\s*", ",", constraint.strip()) or "*"
        try:
            validate_constraint(constraint)
        except RegistryError as exc:
            skipped.append(f"{origin} {name}: {exc}")
            return
        findings.append((
            "pypi", name, constraint,
            {"name": name, "registry": pypi_registry},
        ))

    requirement_paths: dict[str, Path] = {}
    if scope == "all":
        candidates = [*project_root.glob("requirements*.in"), *project_root.glob("requirements*.txt")]
        requirements_dir = project_root / "requirements"
        if requirements_dir.is_dir():
            candidates.extend(requirements_dir.rglob("*.in"))
            candidates.extend(requirements_dir.rglob("*.txt"))
        for candidate in sorted(candidates):
            if candidate.is_file():
                requirement_paths[candidate.relative_to(project_root).as_posix()] = candidate
    else:
        root_requirements = project_root / "requirements.in"
        if root_requirements.is_file():
            requirement_paths["requirements.in"] = root_requirements
    for relative in requirements_files:
        candidate = project_root / Path(*relative.split("/"))
        try:
            candidate.resolve().relative_to(project_root.resolve())
        except ValueError as exc:
            raise VersionsError(
                f"versions.discovery.requirements_files path {relative!r} resolves outside {project_root}"
            ) from exc
        if not candidate.is_file():
            raise VersionsError(
                f"versions.discovery.requirements_files path {relative!r} is not a regular file"
            )
        requirement_paths[relative] = candidate

    pyproject = project_root / "pyproject.toml"
    if pypi_extras and not pyproject.is_file():
        raise VersionsError(
            "versions.discovery.pypi_extras requires a pyproject.toml with "
            "[project.optional-dependencies]"
        )

    project_root_resolved = project_root.resolve()
    for relative, requirements in sorted(requirement_paths.items()):
        try:
            requirements.resolve().relative_to(project_root_resolved)
        except ValueError as exc:
            raise VersionsError(
                f"requirements manifest {relative!r} resolves outside {project_root}"
            ) from exc
        for line in requirements.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if not clean:
                continue
            if clean.startswith(("-", "http:", "https:", ".", "/")):
                skipped.append(
                    f"{relative} line {line.strip()!r}: includes/options/URL/path dependencies "
                    "need a named registry target"
                )
                continue
            add_python_requirement(clean, f"{relative} line {line.strip()!r}")

    if pyproject.is_file():
        try:
            with pyproject.open("rb") as stream:
                metadata = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise VersionsError(f"could not read {pyproject}: {exc}") from exc
        project_metadata = metadata.get("project", {})
        if not isinstance(project_metadata, dict):
            raise VersionsError(f"{pyproject} [project] must be a table")
        dynamic_fields = project_metadata.get("dynamic", [])
        if not isinstance(dynamic_fields, list) or any(not isinstance(item, str) for item in dynamic_fields):
            raise VersionsError(f"{pyproject} [project].dynamic must be an array of strings")
        if "dependencies" in dynamic_fields:
            raise VersionsError(
                f"{pyproject} declares project.dependencies as dynamic; CMRU cannot safely derive "
                "the shipped dependency set from a dynamic value"
            )
        if "dependencies" not in project_metadata:
            dependencies = []
        elif isinstance(project_metadata["dependencies"], list):
            dependencies = project_metadata["dependencies"]
        else:
            skipped.append("pyproject.toml project.dependencies: expected an array of requirement strings")
            dependencies = []
        for value in dependencies:
            add_python_requirement(value, "pyproject.toml project.dependencies")

        optional_dependencies = project_metadata.get("optional-dependencies", {})
        if pypi_extras or scope == "all":
            if not isinstance(optional_dependencies, dict):
                raise VersionsError(
                    "pyproject.toml project.optional-dependencies must be a table "
                    "when selected by versions.discovery"
                )
            normalized_groups: dict[str, tuple[str, object]] = {}
            for name, values in optional_dependencies.items():
                normalized_name = re.sub(r"[-_.]+", "-", name).lower()
                if normalized_name in normalized_groups:
                    previous_name = normalized_groups[normalized_name][0]
                    raise VersionsError(
                        f"pyproject.toml project.optional-dependencies has ambiguous normalized "
                        f"extra names {previous_name!r} and {name!r}"
                    )
                normalized_groups[normalized_name] = (name, values)
            if scope == "all":
                selected_groups = list(optional_dependencies.items())
            else:
                selected_groups = []
                for requested in pypi_extras:
                    normalized = re.sub(r"[-_.]+", "-", requested).lower()
                    found = normalized_groups.get(normalized)
                    if found is None:
                        raise VersionsError(
                            f"versions.discovery.pypi_extras names {requested!r}, which is absent "
                            "from pyproject.toml [project.optional-dependencies]"
                        )
                    selected_groups.append(found)
            for group_name, group_values in selected_groups:
                origin = f"pyproject.toml project.optional-dependencies.{group_name}"
                if not isinstance(group_values, list):
                    skipped.append(f"{origin}: expected an array of requirement strings")
                    continue
                for value in group_values:
                    add_python_requirement(value, origin)

        if scope == "all":
            dependency_groups = metadata.get("dependency-groups", {})
            if not isinstance(dependency_groups, dict):
                skipped.append("pyproject.toml dependency-groups: expected a table")
            else:
                for group_name, group_values in dependency_groups.items():
                    origin = f"pyproject.toml dependency-groups.{group_name}"
                    if not isinstance(group_values, list):
                        skipped.append(f"{origin}: expected an array of requirement strings")
                        continue
                    for value in group_values:
                        add_python_requirement(value, origin)
            build_system = metadata.get("build-system", {})
            if not isinstance(build_system, dict):
                skipped.append("pyproject.toml build-system: expected a table")
            elif "requires" in build_system:
                requires = build_system["requires"]
                if not isinstance(requires, list):
                    skipped.append("pyproject.toml build-system.requires: expected an array of requirement strings")
                else:
                    for value in requires:
                        add_python_requirement(value, "pyproject.toml build-system.requires")

    package_json = project_root / "package.json"
    if package_json.is_file():
        try:
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not read {package_json}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise VersionsError(f"{package_json} must contain a JSON object")
        fields = ["dependencies", "optionalDependencies", "peerDependencies"]
        if scope == "all":
            fields.append("devDependencies")
        for field in fields:
            if field not in metadata:
                continue
            values = metadata[field]
            if not isinstance(values, dict):
                skipped.append(f"package.json {field}: expected a dependency object")
                continue
            for name, constraint in values.items():
                if not isinstance(constraint, str):
                    skipped.append(f"package.json {name}: dependency constraint must be a string")
                    continue
                if constraint.startswith(("file:", "git+", "http:", "https:", "workspace:")):
                    skipped.append(f"package.json {name}@{constraint}: non-registry dependency")
                    continue
                try:
                    validate_constraint(constraint or "*")
                except RegistryError as exc:
                    skipped.append(f"package.json {name}@{constraint}: {exc}")
                    continue
                slug = name.lower().replace("/", ".").replace("@", "")
                findings.append(("npm", slug, constraint or "*", {"name": name, "registry": npm_registry}))

    go_mod = project_root / "go.mod"
    if go_mod.is_file():
        in_require = False
        for line in go_mod.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require and stripped == ")":
                in_require = False
                continue
            if stripped.startswith("require "):
                parts = stripped.removeprefix("require ").split()
            elif in_require:
                parts = stripped.split()
            else:
                continue
            if not parts or parts[0].startswith("//"):
                continue
            if len(parts) < 2:
                skipped.append(f"go.mod require entry {stripped!r}: expected a module and version")
                continue
            if len(parts) >= 2 and not parts[0].startswith("//") and parts[1].startswith("v"):
                module, version = parts[:2]
                try:
                    validate_constraint(f">={version}")
                except RegistryError as exc:
                    skipped.append(f"go.mod {module}@{version}: {exc}")
                    continue
                slug = re.sub(r"[^A-Za-z0-9_.-]+", ".", module).strip(".")
                findings.append(("go", slug, f">={version}", {"module": module, "proxy": go_proxy}))
            else:
                skipped.append(f"go.mod require entry {stripped!r}: unsupported module version")

    deduplicated: dict[tuple[str, str], tuple[str, str, str, dict[str, str]]] = {}
    for item in findings:
        family, slug, constraint, source = item
        identity = (family, slug)
        previous = deduplicated.get(identity)
        if previous is None:
            deduplicated[identity] = item
        elif previous[3] != source:
            raise VersionsError(f"manifest facts for {family}:{slug} disagree on registry coordinates")
        elif constraint != "*":
            merged = constraint if previous[2] == "*" else f"{previous[2]},{constraint}"
            try:
                validate_constraint(merged)
            except RegistryError as exc:
                raise VersionsError(
                    f"cannot combine constraints for manifest target {family}:{slug}: "
                    f"{previous[2]!r} and {constraint!r}"
                ) from exc
            deduplicated[identity] = (family, slug, merged, source)
    return list(deduplicated.values()), skipped


def _target_id(family: str, slug: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", ".", slug).strip("._-")
    if not cleaned:
        raise VersionsError(f"cannot derive a target identifier for {family}:{slug}")
    return f"{family}.{cleaned}"


def _same_source_identity(family: str, left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if family == "pypi":
        normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
        return normalize(left) == normalize(right)
    if family == "npm":
        return left.casefold() == right.casefold()
    return left == right


def _recorded_target(forge: ForgeConfig, project_name: str, target_id: str, source_type: str) -> str | None:
    local = forge.projects[project_name].versions.get("targets", {})
    target = local.get(target_id, {}) if isinstance(local, dict) else {}
    resolved = target.get("resolved", {}) if isinstance(target, dict) else {}
    sources = resolved.get("sources", {}) if isinstance(resolved, dict) else {}
    source = sources.get(source_type, {}) if isinstance(sources, dict) else {}
    if isinstance(source, dict) and isinstance(source.get("version"), str):
        return source["version"]
    root = forge.versions.get("targets", {})
    root_target = root.get(target_id, {}) if isinstance(root, dict) else {}
    root_resolved = root_target.get("resolved", {}) if isinstance(root_target, dict) else {}
    root_sources = root_resolved.get("sources", {}) if isinstance(root_resolved, dict) else {}
    root_source = root_sources.get(source_type, {}) if isinstance(root_sources, dict) else {}
    return root_source.get("version") if isinstance(root_source, dict) and isinstance(root_source.get("version"), str) else None
































@contextmanager
def _temporary_npm_config(
    registry: str,
    token_env: str | None,
    username_env: str | None,
    password_env: str | None,
):
    if not token_env and not username_env:
        yield None
        return
    parsed = urlsplit(registry)
    if not parsed.hostname:
        raise VersionsError(f"invalid npm registry URL {registry!r}")
    registry_key = f"//{parsed.netloc}{parsed.path.rstrip('/')}/"
    if token_env:
        if not os.environ.get(token_env):
            raise VersionsPrerequisiteError(f"required registry token environment variable {token_env} is unset or empty")
        npm_variable = "$" + "{" + token_env + "}"
        config = f"{registry_key}:_authToken={npm_variable}\n"
    else:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} and {password_env} "
                "must be set"
            )
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        config = f"{registry_key}:_auth={encoded}\n"
    fd, temporary = tempfile.mkstemp(prefix="cmru-npmrc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(config)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        yield Path(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _run_npm(
    project_root: Path,
    package_versions: Mapping[str, tuple[str, str]],
    results: Mapping[str, TargetResult],
    previous: Mapping[str, str | None],
    package_sources: Mapping[str, Mapping[str, str]],
) -> None:
    if not package_versions:
        return
    package_json = project_root / "package.json"
    if not package_json.is_file():
        raise VersionsError(f"npm version targets require {package_json}")
    try:
        document = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionsError(f"could not parse {package_json}: {exc}") from exc
    if not isinstance(document, dict):
        raise VersionsError(f"{package_json} must contain a JSON object")
    lock_path = project_root / (
        "npm-shrinkwrap.json" if (project_root / "npm-shrinkwrap.json").exists() else "package-lock.json"
    )
    lock_doc: dict[str, Any] = {}
    if lock_path.exists():
        try:
            loaded = json.loads(lock_path.read_text(encoding="utf-8"))
            lock_doc = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not parse {lock_path}: {exc}") from exc
    all_declared = set()
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = document.get(field, {})
        if isinstance(values, dict):
            all_declared.update(values)
    lock_packages = lock_doc.get("packages", {})
    lock_names = set()
    if isinstance(lock_packages, dict):
        for package_path, item in lock_packages.items():
            if not package_path or not isinstance(item, dict):
                continue
            package_name = item.get("name")
            if isinstance(package_name, str):
                lock_names.add(package_name)
            elif package_path.startswith("node_modules/"):
                lock_names.add(package_path.rsplit("node_modules/", 1)[-1])
    for name in package_versions:
        if name not in all_declared and name not in lock_names:
            raise VersionsError(
                f"npm target {name!r} is absent from package.json dependencies and package-lock.json; "
                "add the dependency before resolving its override"
            )
    registries = {source["registry"].rstrip("/") for source in package_sources.values()}
    if len(registries) != 1:
        raise VersionsError(
            "all npm targets in one project must use the same registry for native lockfile generation"
        )
    registry = next(iter(registries))
    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for source in package_sources.values()
    }
    if len(auth_refs) != 1:
        raise VersionsError(
            "npm targets using one registry must share one credential environment-variable mapping"
        )
    overrides = document.setdefault("overrides", {})
    if not isinstance(overrides, dict):
        raise VersionsError(f"{package_json}.overrides must be an object")
    for name, (version, target_id) in package_versions.items():
        existing = overrides.get(name)
        old_version = previous.get(target_id)
        if existing is not None and str(existing) not in {version, old_version}:
            raise VersionsError(
                f"{package_json} has an existing npm override for {name!r} that is not owned "
                "by this CMRU target; reconcile it before resolving"
            )
        overrides[name] = version
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            values = document.get(field, {})
            if not isinstance(values, dict):
                continue
            if name in values:
                values[name] = version
    package_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    npm_results = [results[target_id] for _name, (_version, target_id) in package_versions.items()]
    cutoff = min(result.age_cutoff for result in npm_results)
    command = ["npm", "install", "--package-lock-only", "--ignore-scripts", "--before", _timestamp(cutoff)]
    command.extend(("--registry", registry))
    scopes: set[str] = set()
    for name in package_versions:
        if not name.startswith("@"):
            continue
        if "/" not in name:
            continue
        scopes.add(name.split("/", 1)[0])
    command.extend(f"--{scope}:registry={registry}" for scope in sorted(scopes))
    age_exclusions = []
    for name, (_version, target_id) in package_versions.items():
        result = results[target_id]
        candidate = result.sources.get("npm")
        if candidate and (
            candidate.released_at > result.age_cutoff or result.age_cutoff > cutoff
        ):
            age_exclusions.append(name)
    if age_exclusions:
        try:
            version_probe = subprocess.run(
                ["npm", "--version"], cwd=project_root, text=True, capture_output=True,
                check=False, timeout=20,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("npm targets require npm on PATH") from exc
        version_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_probe.stdout.strip())
        if version_probe.returncode or not version_match or tuple(map(int, version_match.groups())) < (11, 5, 0):
            raise VersionsPrerequisiteError(
                "this npm target needs the package-specific age exception flag; "
                "use npm 11.5.0 or newer"
            )
        for name in age_exclusions:
            command.extend(("--min-release-age-exclude", name))
    command.extend(("--audit=false", "--fund=false"))
    token_env, username_env, password_env = next(iter(auth_refs))
    with _temporary_npm_config(registry, token_env, username_env, password_env) as npm_config:
        if npm_config is not None:
            command.extend(("--userconfig", str(npm_config)))
        try:
            completed = subprocess.run(
                command, cwd=project_root, text=True, capture_output=True, check=False, timeout=900,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("npm targets require npm on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise VersionsOperationError("npm package-lock refresh did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"npm lockfile refresh failed (exit {completed.returncode}): {detail[-1600:]}")


@contextmanager
def _temporary_netrc(
    proxy: str,
    token_env: str | None,
    username_env: str | None,
    password_env: str | None,
):
    if not token_env and not username_env:
        yield None
        return
    host = urlsplit(proxy).netloc
    if not host:
        raise VersionsError(f"invalid Go proxy URL {proxy!r}")
    if token_env:
        username = "__token__"
        password = os.environ.get(token_env, "")
        if not password:
            raise VersionsPrerequisiteError(f"required registry token environment variable {token_env} is unset or empty")
    else:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} and {password_env} "
                "must be set"
            )
    if any(char.isspace() or char in {'"', "\\", "#"} for char in username + password):
        raise VersionsError("Go proxy credentials cannot contain whitespace, quotes, backslashes, or #")
    fd, temporary = tempfile.mkstemp(prefix="cmru-netrc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"machine {host} login {username} password {password}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        yield Path(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _go_workspace_files(project_root: Path, environment: Mapping[str, str]) -> list[Path]:
    """Ask Go which workspace it will use and return every mutable workspace file."""
    try:
        completed = subprocess.run(
            ["go", "env", "GOWORK"], cwd=project_root, text=True,
            capture_output=True, check=False, timeout=20, env=dict(environment),
        )
    except FileNotFoundError as exc:
        raise VersionsPrerequisiteError("Go targets require go on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise VersionsOperationError("Go workspace detection did not finish within 20 seconds") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(
            f"could not determine the Go workspace (exit {completed.returncode}): {detail[-1200:]}"
        )
    value = completed.stdout.strip()
    if not value or value == "off":
        return []
    workspace = Path(value)
    if not workspace.is_absolute():
        workspace = project_root / workspace
    return [workspace, Path(str(workspace) + ".sum")]


def _run_go(
    project_root: Path,
    module_versions: Mapping[str, tuple[str, str]],
    module_sources: Mapping[str, Mapping[str, str]],
    transaction: _FileTransaction | None = None,
) -> None:
    if not module_versions:
        return
    go_mod = project_root / "go.mod"
    if not go_mod.is_file():
        raise VersionsError(f"Go version targets require {go_mod}")
    proxies = {source["proxy"].rstrip("/") for source in module_sources.values()}
    if len(proxies) != 1:
        raise VersionsError("all Go targets in one project must use the same proxy for native go.mod updates")
    proxy = next(iter(proxies))
    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for source in module_sources.values()
    }
    if len(auth_refs) != 1:
        raise VersionsError(
            "Go targets using one proxy must share one credential environment-variable mapping"
        )
    command = [
        "go", "get",
        *[f"{module}@{version}" for module, (version, _target) in sorted(module_versions.items())],
    ]
    environment = os.environ.copy()
    environment["GOPROXY"] = proxy
    environment["GONOPROXY"] = "none"
    token_env, username_env, password_env = next(iter(auth_refs))
    with _temporary_netrc(proxy, token_env, username_env, password_env) as netrc:
        if netrc is not None:
            environment["NETRC"] = str(netrc)
        if transaction is not None:
            for workspace_path in _go_workspace_files(project_root, environment):
                transaction.track(workspace_path)
        try:
            completed = subprocess.run(
                command, cwd=project_root, text=True, capture_output=True, check=False,
                timeout=900, env=environment,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("Go targets require go on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise VersionsOperationError("Go module update did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"Go module update failed (exit {completed.returncode}): {detail[-1600:]}")




def _project_has_python_manifest(project_root: Path) -> bool:
    return (
        any(project_root.glob("requirements*.in"))
        or any(project_root.glob("requirements*.txt"))
        or (project_root / "pyproject.toml").is_file()
    )


def _npm_declared_names(project_root: Path) -> set[str]:
    package_json = project_root / "package.json"
    if not package_json.is_file():
        return set()
    try:
        document = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionsError(f"could not parse {package_json}: {exc}") from exc
    if not isinstance(document, dict):
        raise VersionsError(f"{package_json} must contain a JSON object")
    names: set[str] = set()
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = document.get(field, {})
        if isinstance(values, dict):
            names.update(key for key in values if isinstance(key, str))
    for lock_name in ("npm-shrinkwrap.json", "package-lock.json"):
        lock_path = project_root / lock_name
        if not lock_path.is_file():
            continue
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not parse {lock_path}: {exc}") from exc
        if not isinstance(lock, dict):
            raise VersionsError(f"{lock_path} must contain a JSON object")
        packages = lock.get("packages", {})
        if isinstance(packages, dict):
            for package_path, item in packages.items():
                if not package_path or not isinstance(item, dict):
                    continue
                package_name = item.get("name")
                if isinstance(package_name, str):
                    names.add(package_name)
                elif package_path.startswith("node_modules/"):
                    names.add(package_path.rsplit("node_modules/", 1)[-1])
    return names


def _go_required_modules(project_root: Path) -> set[str]:
    go_mod = project_root / "go.mod"
    if not go_mod.is_file():
        return set()
    modules: set[str] = set()
    in_require = False
    for line in go_mod.read_text(encoding="utf-8").splitlines():
        stripped = line.split("//", 1)[0].strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        parts = stripped.removeprefix("require ").split() if stripped.startswith("require ") else (
            stripped.split() if in_require else []
        )
        if len(parts) >= 2 and parts[1].startswith("v"):
            modules.add(parts[0])
    return modules


def _native_results_for_project(
    forge: ForgeConfig,
    project_name: str,
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, dict[str, TargetResult]], dict[str, TargetResult]]:
    """Select source-native writers relevant to this project.

    Root records are shared and resolved once. They update a language's native
    files only where the project has a matching manifest; project-local targets
    are explicit requests and reach their configured writer directly.
    """
    project = forge.projects[project_name]
    local_ids = set(_local_target_overlays(forge, project_name))
    python_present = _project_has_python_manifest(project_root)
    npm_names = _npm_declared_names(project_root)
    go_modules = _go_required_modules(project_root)
    native: dict[str, dict[str, TargetResult]] = {}
    oci: dict[str, TargetResult] = {}
    for target_id, result in results.items():
        declaration = declarations.get(target_id, {})
        is_local = target_id in local_ids
        for source_type, source in _target_sources(declaration).items():
            used = (
                is_local
                or source_type == "pypi" and python_present
                or source_type == "npm" and (project_root / "package.json").is_file()
                and str(source["name"]) in npm_names
                or source_type == "go" and str(source["module"]) in go_modules
            )
            if source_type == "oci":
                if is_local or "oci-image" in project.artifacts or bool(_merged_outputs(forge, project_name)):
                    oci[target_id] = result
            elif used:
                native.setdefault(source_type, {})[target_id] = result
    return native, oci


def _resolve_all_for_command(
    forge: ForgeConfig,
    projects: list[str],
    *,
    resolved_at: datetime,
    resume: _ResumeStore | None = None,
) -> tuple[dict[str, TargetResult], dict[str, dict[str, TargetResult]], dict[str, dict[str, dict[str, object]]]]:
    try:
        return _resolve_all_targets(forge, projects, resolved_at=resolved_at, resume=resume)
    except VersionsRateLimitedError as exc:
        if resume is None:
            raise
        saved = len(resume.entries)
        if not resume.persist:
            raise VersionsRateLimitedError(
                f"{exc}. Rate limited during a dry run: nothing saved; rerun after the rate-limit window "
                "(Docker Hub's window is 3600 s)"
            ) from exc
        raise VersionsRateLimitedError(
            f"{exc}. Rate limited: {saved} target(s) resolved and saved to {resume.path}; "
            "rerun the same command after the rate-limit window to continue "
            "(Docker Hub's window is 3600 s)"
        ) from exc


def _resolve_all_targets(
    forge: ForgeConfig,
    projects: list[str],
    *,
    resolved_at: datetime,
    resume: _ResumeStore | None,
) -> tuple[dict[str, TargetResult], dict[str, dict[str, TargetResult]], dict[str, dict[str, dict[str, object]]]]:
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    root_results = _resolve_targets(
        root_targets,
        age_window_days=_age_window(forge.versions),
        resolved_at=resolved_at,
        owner="root",
        resume=resume,
    )
    project_results: dict[str, dict[str, TargetResult]] = {}
    declarations: dict[str, dict[str, dict[str, object]]] = {}
    for name in projects:
        declarations[name] = _target_declarations_for_project(forge, name)
        local_targets = _local_target_overlays(forge, name)
        versions = _versions_for_project(forge, name)
        project_results[name] = _resolve_targets(
            local_targets,
            age_window_days=_age_window(versions),
            resolved_at=resolved_at,
            owner=name,
            resume=resume,
        )
        _validate_manifest_target_compatibility(
            _project_root(forge, name),
            {**root_results, **project_results[name]},
            declarations[name],
            discovery=versions.get("discovery", {}),
        )
    return root_results, project_results, declarations


def _validate_manifest_target_compatibility(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
    *,
    discovery: Mapping[str, object] | None = None,
) -> None:
    """Refuse a registry target that contradicts this project's declared range."""
    discovery = discovery or {}
    facts, _skipped = _init_facts(
        project_root,
        scope=str(discovery.get("scope", "shipped")),
        pypi_extras=discovery.get("pypi_extras", ()),
        requirements_files=discovery.get("requirements_files", ()),
    )
    problems: list[str] = []
    for family, _slug, constraint, manifest_source in facts:
        for target_id, result in results.items():
            declaration = declarations.get(target_id, {})
            source = declaration.get(family)
            candidate = result.sources.get(family)
            if not isinstance(source, dict) or candidate is None:
                continue
            identity_key = "name" if family in {"npm", "pypi"} else "module" if family == "go" else "image"
            if identity_key not in source or identity_key not in manifest_source:
                continue
            source_identity = str(source[identity_key])
            manifest_identity = str(manifest_source[identity_key])
            matches = _same_source_identity(family, source_identity, manifest_identity)
            if matches and not version_satisfies(candidate.version, constraint):
                problems.append(_manifest_problem(
                    project_root, family, target_id, source_identity, candidate.version, constraint,
                ))
    if problems:
        raise VersionsError(
            f"{len(problems)} version target(s) resolve to versions the committed manifests do not "
            "allow; the manifests (package.json among them) are hand-owned and cmru does not rewrite "
            "them, so nothing was written. Make these edits, then rerun cmru versions resolve:\n- "
            + "\n- ".join(problems)
        )


def _manifest_problem(
    project_root: Path, family: str, target_id: str, name: str, version: str, constraint: str,
) -> str:
    """One target the manifest contradicts, with the edit that fixes it."""
    line = (
        f"target {target_id!r} resolves {name}@{version}, but "
        f"{project_root} manifest requires {constraint!r}"
    )
    if family != "npm":
        return f"{line}; update the manifest entry for {name} to allow {version}"
    document = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    edits = [
        f'package.json: set "{field}"."{name}" from "{document[field][name]}" to "{version}"'
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "overrides")
        if isinstance(document.get(field), dict)
        and isinstance(document[field].get(name), str)
        and document[field][name] != version
    ]
    return f"{line}; " + "; ".join(edits)


def _run_resolve(
    forge: ForgeConfig,
    context: InvocationContext,
    projects: list[str],
    *,
    dry_run: bool,
) -> list[str]:
    resolved_at = datetime.now(timezone.utc).replace(microsecond=0)
    resume = _ResumeStore(_resume_path(context.config_path), resolved_at, persist=not dry_run)
    root_results, project_results, declarations = _resolve_all_for_command(
        forge, projects, resolved_at=resolved_at, resume=resume,
    )
    _emit_age_evidence_warnings(root_results, project_results)
    if dry_run:
        records = _recorded_versions(forge, root_results, project_results, declarations)
        return ["Dry run: no files written.", _render_report(records)]

    root_path = context.config_path if context.config_kind == "orchestration" else None
    project_paths = {
        name: (
            forge.orchestration.project_configs[name]
            if forge.orchestration is not None
            else context.config_path
        )
        for name in projects
    }
    results_by_project = {
        name: {**root_results, **project_results[name]}
        for name in projects
    }
    outputs_by_project = {name: _merged_outputs(forge, name) for name in projects}
    native_results_by_project: dict[str, dict[str, dict[str, TargetResult]]] = {}
    oci_results_by_project: dict[str, dict[str, TargetResult]] = {}
    prepared_native: dict[str, list[tuple[Path, str]]] = {}
    for name in projects:
        project_root = _project_root(forge, name)
        native_results_by_project[name], oci_results_by_project[name] = _native_results_for_project(
            forge, name, project_root, results_by_project[name], declarations[name],
        )
        prepared_native[name] = _native_output_files(
            project_root,
            results_by_project[name],
            declarations[name],
            now=resolved_at,
            outputs=outputs_by_project[name],
            oci_results=oci_results_by_project[name],
        )
        prepared_native[name].extend(
            _compile_python_constraints(
                project_root,
                native_results_by_project[name].get("pypi", {}),
                declarations[name],
            )
        )
        _assert_unique_output_paths(prepared_native[name])

    transaction = _FileTransaction()
    if root_path is not None:
        if root_results:
            transaction.track(root_path)
    for name, path in project_paths.items():
        if project_results[name]:
            transaction.track(path)
        project_root = _project_root(forge, name)
        for output_path, _content in prepared_native[name]:
            transaction.track(output_path)
        if native_results_by_project[name].get("npm"):
            transaction.track(project_root / "package.json")
            lock_path = project_root / (
                "npm-shrinkwrap.json" if (project_root / "npm-shrinkwrap.json").exists() else "package-lock.json"
            )
            transaction.track(lock_path)
        if native_results_by_project[name].get("go"):
            transaction.track(project_root / "go.mod")
            transaction.track(project_root / "go.sum")

    try:
        if root_path is not None:
            if root_results:
                _apply_resolved_state(root_path, root_results)
        for name, path in project_paths.items():
            if project_results[name]:
                _apply_resolved_state(path, project_results[name])
        for name in projects:
            project_root = _project_root(forge, name)
            results = results_by_project[name]
            package_versions: dict[str, tuple[str, str]] = {}
            module_versions: dict[str, tuple[str, str]] = {}
            package_sources: dict[str, Mapping[str, str]] = {}
            module_sources: dict[str, Mapping[str, str]] = {}
            previous: dict[str, str | None] = {}
            for target_id, result in native_results_by_project[name].get("npm", {}).items():
                declaration = declarations[name].get(target_id, {})
                source = declaration.get("npm", {})
                candidate = result.sources.get("npm")
                if not isinstance(source, dict):
                    raise VersionsError(f"npm source declaration missing for target {target_id!r}")
                if candidate is None:
                    raise VersionsError(f"npm candidate missing for target {target_id!r}")
                package_name = str(source["name"])
                previous_package = package_versions.get(package_name)
                if previous_package and previous_package[0] != candidate.version:
                    raise VersionsError(f"npm package {package_name!r} has conflicting target versions")
                if previous_package and previous_package[1] != target_id:
                    raise VersionsError(f"multiple npm targets resolve the same package {package_name!r}")
                package_versions[package_name] = (candidate.version, target_id)
                package_sources[package_name] = source
                previous[target_id] = _recorded_target(forge, name, target_id, "npm")
            for target_id, result in native_results_by_project[name].get("go", {}).items():
                declaration = declarations[name].get(target_id, {})
                source = declaration.get("go", {})
                candidate = result.sources.get("go")
                if not isinstance(source, dict):
                    raise VersionsError(f"Go source declaration missing for target {target_id!r}")
                if candidate is None:
                    raise VersionsError(f"Go candidate missing for target {target_id!r}")
                module_name = str(source["module"])
                previous_module = module_versions.get(module_name)
                if previous_module and normalized_version(previous_module[0]) != normalized_version(candidate.version):
                    raise VersionsError(f"Go module {module_name!r} has conflicting target versions")
                if previous_module and previous_module[1] != target_id:
                    raise VersionsError(f"multiple Go targets resolve the same module {module_name!r}")
                module_versions[module_name] = (candidate.version, target_id)
                module_sources[module_name] = source
            if package_versions:
                _run_npm(project_root, package_versions, results, previous, package_sources)
            if module_versions:
                _run_go(project_root, module_versions, module_sources, transaction)
            for path, content in prepared_native[name]:
                _write_text_atomic(path, content)
    except BaseException:
        transaction.rollback()
        raise

    resume.clear()
    records = _recorded_versions(forge, root_results, project_results, declarations)
    for record in records:
        record["recorded_version"] = record["eligible_version"]
        record["status"] = "override" if record["override"] else "matches"
    return [f"Resolved version targets at {_timestamp(resolved_at)}.", _render_report(records)]


def main(argv: list[str] | None = None) -> int:
    return versions_cli().run(argv=argv)


def versions_cli():
    # Exit 1 (operation failed) is the library's rendering of an expected
    # exception; exits 2 and 3 are translated explicitly in ``_run_versions``.
    registry = cmru_registry(
        "cmru versions",
        "Resolve declared package versions under a supply-chain age window.",
        expected_exceptions=(VersionsOperationError, OSError),
    )
    for action in ("init", "resolve", "check"):
        options = [OptionSpec(
            ("--config",),
            f"path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}",
            metavar="FILE", parser_kwargs={"default": None},
        )]
        registry.register(VerbSpec(
            action,
            description={
                "init": "Derive version targets from supported project manifests.",
                "resolve": "Resolve declared versions and write their records and artifacts.",
                "check": "Compare recorded and currently eligible versions without writing.",
            }[action],
            group=VerbGroup.EXPLORATION.value if action == "check" else VerbGroup.MODIFICATION.value,
            mutating=action != "check",
            dry_run=action != "check",
            include_confirmation=False,
            arguments=(target_argument(),),
            options=tuple(options),
            include_json=action == "check",
            include_progress=False,
            handler=_run_versions,
        ))
    return registry.build()


def _run_versions(args, _runtime) -> int | None:
    """Run one verb; domain failures keep their documented exit codes.

    5 = a registry rate limit stopped resolution (partial results saved; rerun),
    3 = a prerequisite or registry is unavailable, 2 = invalid configuration or
    selection, 1 = the operation failed (rendered by the library from
    ``expected_exceptions``: :class:`VersionsOperationError`, ``OSError``).
    """
    try:
        return _dispatch_versions(args)
    except (VersionsRateLimitedError, RegistryRateLimitedError) as exc:
        raise CliFailure(
            f"CMRU versions: {exc}", exit_code=exit_codes.RATE_LIMITED,
        ) from exc
    except (VersionsPrerequisiteError, RegistryError) as exc:
        raise CliFailure(
            f"CMRU versions: {exc}", exit_code=exit_codes.PREREQ_MISSING,
        ) from exc
    except VersionsOperationError:
        raise
    except (VersionsError, ValueError) as exc:
        raise CliFailure(
            f"CMRU versions: {exc}", exit_code=exit_codes.CONFIG_ERROR,
        ) from exc


def _dispatch_versions(args) -> int | None:
    action = args.verb
    context_path = Path(args.config).expanduser() if args.config else None
    context = resolve_invocation_context(context_path)
    forge = load_forge_config(context.config_path)
    projects = _selected_projects(forge, context, args.target)
    if action == "init":
        for line in _versions_init(forge, projects, dry_run=args.dry_run):
            print(line)
        return None
    if action == "check":
        now = datetime.now(timezone.utc).replace(microsecond=0)
        root_results, project_results, declarations = _resolve_all_for_command(
            forge, projects, resolved_at=now,
        )
        _emit_age_evidence_warnings(root_results, project_results)
        records = _recorded_versions(forge, root_results, project_results, declarations)
        if args.json:
            # Deliberately not ``runtime.output.primary``: the report keeps its
            # sorted, indented form (a stable-order contract for consumers).
            print(json.dumps({"schema_version": 1, "targets": records}, indent=2, sort_keys=True))
        else:
            print(_render_report(records))
        return None
    for line in _run_resolve(forge, context, projects, dry_run=args.dry_run):
        print(line)
    return None
