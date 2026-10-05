"""Packaged agent skills: install, check, list and uninstall a tool's skills.

A consumer ships ``<package>/skills/<skill-name>/SKILL.md`` (plus any files) as
package data and registers the shared ``skills`` verb group with
:func:`register_skills_verbs`.  Installed copies are stamped (frontmatter
metadata, a visible banner and an integrity sidecar) so that stale, locally
modified, foreign and orphaned skills can be told apart and never clobbered.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any

from .identity import CliIdentity
from .output import LogLevel
from .parser import (
    CliFailure,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
)

STAMP_FILE = ".cli-extended-stamp.json"
STAMP_SCHEMA_VERSION = 1
SKILL_FILE = "SKILL.md"
_RESERVED_PREFIX = "cli-extended-"
_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_TOP_RE = re.compile(r"([A-Za-z0-9_-]+):(?:[ ]+(.*))?")
_META_RE = re.compile(r"  ([A-Za-z0-9_-]+):[ ]+(\S.*)")
_UNSUPPORTED = "unsupported frontmatter syntax; use a single-line value"


class SkillError(Exception):
    """A packaged skill source is missing, malformed or unreadable."""


class SkillState(str, Enum):
    """Installed state of one skill in one destination."""

    ABSENT = "absent"
    CURRENT = "current"
    STALE = "stale"
    MODIFIED = "modified"
    FOREIGN = "foreign"
    UNMANAGED = "unmanaged"
    ORPHANED = "orphaned"


# --------------------------------------------------------------- validation


def _fail(skill: str, message: str, line: int | None = None) -> SkillError:
    where = f"line {line}: " if line is not None else ""
    return SkillError(f"skill {skill!r}: {where}{message}")


@dataclass(frozen=True)
class _Frontmatter:
    close: int
    metadata_line: int | None
    last_meta: int | None
    values: dict[str, str]
    metadata: dict[str, str]


def _unquote(skill: str, value: str, line: int) -> str:
    if value[0] in "'\"":
        if len(value) < 2 or value[-1] != value[0]:
            raise _fail(skill, _UNSUPPORTED, line)
        return value[1:-1]
    return value


def _parse_frontmatter(skill: str, text: str) -> _Frontmatter:
    lines = text.split("\n")
    if lines[0] != "---":
        raise _fail(skill, "SKILL.md must start with a '---' frontmatter line", 1)
    values: dict[str, str] = {}
    metadata: dict[str, str] = {}
    metadata_line: int | None = None
    last_meta: int | None = None
    in_meta = False
    close: int | None = None
    for index in range(1, len(lines)):
        line = lines[index]
        number = index + 1
        if line == "---":
            close = index
            break
        if line == "":
            continue
        if "\t" in line or "\r" in line:
            raise _fail(skill, _UNSUPPORTED, number)
        top = _TOP_RE.fullmatch(line)
        if top is not None:
            in_meta = False
            key = top.group(1)
            value = (top.group(2) or "").strip()
            if key in values or (key == "metadata" and metadata_line is not None):
                raise _fail(skill, f"duplicate key {key!r}", number)
            if key == "metadata":
                if value:
                    raise _fail(skill, _UNSUPPORTED, number)
                metadata_line = index
                in_meta = True
                continue
            if value == "" or value[0] in "|>":
                raise _fail(skill, _UNSUPPORTED, number)
            values[key] = _unquote(skill, value, number)
            continue
        entry = _META_RE.fullmatch(line) if in_meta else None
        if entry is None:
            raise _fail(skill, _UNSUPPORTED, number)
        key = entry.group(1)
        if key in metadata:
            raise _fail(skill, f"duplicate metadata key {key!r}", number)
        if key.startswith(_RESERVED_PREFIX):
            raise _fail(
                skill,
                f"metadata key {key!r} is reserved (prefix {_RESERVED_PREFIX!r})",
                number,
            )
        metadata[key] = _unquote(skill, entry.group(2).strip(), number)
        last_meta = index
    if close is None:
        raise _fail(skill, "frontmatter has no closing '---' line", len(lines))
    return _Frontmatter(close, metadata_line, last_meta, values, metadata)


def validate_skill_source(name: str, files: Mapping[str, bytes]) -> None:
    """Validate one packaged skill; raise :class:`SkillError` on any problem."""

    if SKILL_FILE not in files:
        raise _fail(name, f"directory has no {SKILL_FILE}")
    if STAMP_FILE in files:
        raise _fail(name, f"source must not contain {STAMP_FILE}")
    raw = files[SKILL_FILE]
    if not raw:
        raise _fail(name, f"{SKILL_FILE} is empty")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _fail(name, f"{SKILL_FILE} starts with a UTF-8 BOM; remove it")
    if b"\r\n" in raw:
        raise _fail(name, f"{SKILL_FILE} uses CRLF line endings; convert to LF")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail(name, f"{SKILL_FILE} is not valid UTF-8: {exc}") from exc
    front = _parse_frontmatter(name, text)
    declared = front.values.get("name")
    if declared is None:
        raise _fail(name, "frontmatter requires 'name'", 1)
    if not 1 <= len(declared) <= 64 or _NAME_RE.fullmatch(declared) is None:
        raise _fail(
            name,
            f"name {declared!r} must be 1-64 characters of lowercase letters, "
            "digits and single hyphens",
            1,
        )
    if declared != name:
        raise _fail(name, f"name {declared!r} must equal the directory name", 1)
    description = front.values.get("description")
    if description is None:
        raise _fail(name, "frontmatter requires 'description'", 1)
    if not 1 <= len(description) <= 1024:
        raise _fail(name, "description must be 1-1024 characters", 1)


# ------------------------------------------------------------------- source


def _walk(node: Any, prefix: str = "") -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for child in node.iterdir():
        relative = f"{prefix}{child.name}"
        if child.is_dir():
            found.update(_walk(child, relative + "/"))
        else:
            found[relative] = child.read_bytes()
    return found


def _source_hash(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        data = files[relative]
        digest.update(
            relative.encode() + b"\0" + str(len(data)).encode() + b"\0" + data
        )
    return "sha256:" + digest.hexdigest()


def _load_sources(package: str, resource_dir: str) -> dict[str, dict[str, bytes]]:
    try:
        root = resources.files(package) / resource_dir
        present = root.is_dir()
    except ModuleNotFoundError as exc:
        raise SkillError(
            f"cannot locate package {package!r} resource dir {resource_dir!r}: {exc}"
        ) from exc
    if not present:
        raise SkillError(
            f"package {package!r} has no skills resource dir {resource_dir!r}"
        )
    sources: dict[str, dict[str, bytes]] = {}
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        files = _walk(child)
        validate_skill_source(child.name, files)
        sources[child.name] = files
    return sources


# ---------------------------------------------------------------- rendering


def _banner(tool: str, version: str) -> str:
    return (
        f"> Installed by {tool} {version} via cli-extended. "
        f"If this disagrees with `{tool} --help`, run `{tool} skills check`."
    )


def _render_skill_md(text: str, name: str, tool: str, version: str, digest: str) -> str:
    front = _parse_frontmatter(name, text)
    lines = text.split("\n")
    stamp = [
        f"  cli-extended-tool: {tool}",
        f"  cli-extended-version: {version}",
        f"  cli-extended-source-hash: {digest}",
    ]
    if front.metadata_line is None:
        insert_at = front.close
        stamp = ["metadata:", *stamp]
    elif front.last_meta is None:
        insert_at = front.metadata_line + 1
    else:
        insert_at = front.last_meta + 1
    lines[insert_at:insert_at] = stamp
    close = front.close + len(stamp)
    lines[close + 1 : close + 1] = [_banner(tool, version), ""]
    return "\n".join(lines)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _render(
    name: str, files: Mapping[str, bytes], tool: str, version: str
) -> dict[str, bytes]:
    digest = _source_hash(files)
    rendered = dict(files)
    rendered[SKILL_FILE] = _render_skill_md(
        files[SKILL_FILE].decode("utf-8"), name, tool, version, digest
    ).encode("utf-8")
    stamp = {
        "schema_version": STAMP_SCHEMA_VERSION,
        "tool": tool,
        "version": version,
        "source_hash": digest,
        "files": {relative: _sha(data) for relative, data in sorted(rendered.items())},
    }
    rendered[STAMP_FILE] = (
        json.dumps(stamp, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    return rendered


# ------------------------------------------------------------------- states


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _load_stamp(tree: Mapping[str, bytes]) -> dict[str, Any] | None:
    raw = tree.get(STAMP_FILE)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("schema_version") != STAMP_SCHEMA_VERSION:
        return None
    if not all(isinstance(data.get(key), str) for key in ("tool", "version", "source_hash")):
        return None
    files = data.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in files.items()
    ):
        return None
    return data


@dataclass(frozen=True)
class _Row:
    name: str
    destination: Path
    state: SkillState
    packaged: bool
    tree: Mapping[str, bytes]


def _inspect(
    target: Path,
    tool: str,
    version: str,
    digest: str | None,
) -> tuple[SkillState, Mapping[str, bytes]]:
    """Classify ``target``; ``digest`` is None when the skill is not packaged."""

    if not os.path.lexists(target):
        return SkillState.ABSENT, {}
    if target.is_symlink() or not target.is_dir():
        return SkillState.UNMANAGED, {}
    tree = _read_tree(target)
    stamp = _load_stamp(tree)
    if stamp is None:
        return SkillState.UNMANAGED, tree
    if stamp["tool"] != tool:
        return SkillState.FOREIGN, tree
    actual = {rel: _sha(data) for rel, data in tree.items() if rel != STAMP_FILE}
    if actual != stamp["files"]:
        return SkillState.MODIFIED, tree
    if digest is None:
        return SkillState.ORPHANED, tree
    if stamp["version"] != version or stamp["source_hash"] != digest:
        return SkillState.STALE, tree
    return SkillState.CURRENT, tree


def _collect(
    sources: Mapping[str, Mapping[str, bytes]],
    tool: str,
    version: str,
    destinations: Sequence[Path],
) -> list[_Row]:
    rows: list[_Row] = []
    for destination in destinations:
        for name, files in sorted(sources.items()):
            state, tree = _inspect(
                destination / name, tool, version, _source_hash(files)
            )
            rows.append(_Row(name, destination, state, True, tree))
        if not destination.is_dir():
            continue
        for child in sorted(destination.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name in sources or not child.is_dir():
                continue
            state, tree = _inspect(child, tool, version, None)
            if state in (SkillState.ORPHANED, SkillState.MODIFIED):
                rows.append(_Row(child.name, destination, state, False, tree))
    return rows


def _leftovers(destination: Path, tool: str) -> list[Path]:
    """This tool's hidden temp/backup siblings left by an interrupted install.

    Other tools' leftovers share the destination and are never touched.
    """

    if not destination.is_dir():
        return []
    pattern = re.compile(
        r"\..+\.cli-extended-" + re.escape(tool) + r"-(?:tmp|old)-[0-9a-f]{16}"
    )
    return [
        child
        for child in sorted(destination.iterdir(), key=lambda item: item.name)
        if pattern.fullmatch(child.name)
    ]


def default_skill_destinations() -> list[Path]:
    """Return the ``--harness all`` skill destinations for the current HOME."""

    return _destinations(None, None)


def skill_leftovers(*, tool: str, destinations: Sequence[Path]) -> list[Path]:
    """Return this tool's interrupted-install leftovers across ``destinations``."""

    return [path for dest in destinations for path in _leftovers(dest, tool)]


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def skill_states(
    *,
    package: str,
    resource_dir: str,
    tool: str,
    version: str,
    destinations: Sequence[Path],
) -> list[tuple[str, Path, SkillState]]:
    """Return ``(skill, destination, state)`` for every packaged skill and orphan."""

    sources = _load_sources(package, resource_dir)
    return [
        (row.name, row.destination, row.state)
        for row in _collect(sources, tool, version, destinations)
    ]


# ------------------------------------------------------------- destinations


def _destinations(harness: str | None, dest: str | None) -> list[Path]:
    if dest is not None:
        return [Path(dest)]
    chosen = harness or "all"
    found: list[Path] = []
    if chosen in ("claude", "all"):
        config = os.environ.get("CLAUDE_CONFIG_DIR")
        base = Path(config) if config else Path.home() / ".claude"
        found.append(base / "skills")
    if chosen in ("agents", "all"):
        found.append(Path.home() / ".agents" / "skills")
    return found


# ------------------------------------------------------------------ writing


def _write_atomic(
    destination: Path, name: str, tree: Mapping[str, bytes], tool: str
) -> None:
    target = destination / name
    staging = destination / f".{name}.cli-extended-{tool}-tmp-{secrets.token_hex(8)}"
    backup = destination / f".{name}.cli-extended-{tool}-old-{secrets.token_hex(8)}"
    try:
        staging.mkdir()  # not mkdtemp: the umask must decide the mode
        for relative, data in tree.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if os.path.lexists(target):
            os.rename(target, backup)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if os.path.lexists(backup):
            os.rename(backup, target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


# ----------------------------------------------------------------- handlers


@dataclass(frozen=True)
class _Action:
    kind: str  # install | update | remove | skip | refuse
    row: _Row
    note: str = ""
    hint: str = ""


def _refuse_modified(row: _Row, overwrite: bool, kind: str) -> _Action:
    if overwrite:
        return _Action(kind, row)
    return _Action(
        "refuse",
        row,
        "locally modified; refusing to overwrite",
        "rerun with --overwrite-modified to replace it",
    )


def _plan_install(
    row: _Row,
    rendered: Mapping[str, bytes] | None,
    overwrite: bool,
) -> _Action:
    state = row.state
    if state is SkillState.ABSENT:
        return _Action("install", row)
    if state is SkillState.STALE:
        return _Action("update", row)
    if state is SkillState.CURRENT:
        if rendered == row.tree:
            return _Action("skip", row, "unchanged")
        return _Action("update", row)
    if state is SkillState.MODIFIED:
        return _refuse_modified(row, overwrite, "remove" if not row.packaged else "update")
    if state is SkillState.ORPHANED:
        return _Action("remove", row)
    if state is SkillState.FOREIGN:
        return _Action(
            "refuse",
            row,
            "stamped by another tool; refusing to overwrite",
            "remove the directory yourself if it is no longer wanted",
        )
    return _Action(
        "refuse",
        row,
        "exists without a cli-extended stamp; refusing to overwrite",
        "move or remove the directory yourself if it is no longer wanted",
    )


def _plan_uninstall(row: _Row, overwrite: bool) -> _Action:
    state = row.state
    if state is SkillState.MODIFIED:
        return _refuse_modified(row, overwrite, "remove")
    if state in (SkillState.CURRENT, SkillState.STALE, SkillState.ORPHANED):
        return _Action("remove", row)
    return _Action("skip", row, state.value)


_REAL_LABEL = {
    "install": "installed",
    "update": "updated",
    "remove": "removed",
    "skip": "skipped",
}


def _execute(
    action: _Action,
    rendered: Mapping[str, bytes] | None,
    runtime: CliRuntime,
) -> None:
    row = action.row
    suffix = f" ({action.note})" if action.note else ""
    line = f"{row.name} -> {row.destination}{suffix}"
    if runtime.dry_run:
        runtime.output.primary(f"would {action.kind} {line}")
        return
    if action.kind in ("install", "update"):
        assert rendered is not None
        row.destination.mkdir(parents=True, exist_ok=True)
        _write_atomic(row.destination, row.name, rendered, runtime.identity.command_name)
    elif action.kind == "remove":
        shutil.rmtree(row.destination / row.name)
    if action.note == "unchanged":
        runtime.output.primary(f"unchanged {row.name} -> {row.destination}")
    else:
        runtime.output.primary(f"{_REAL_LABEL[action.kind]} {line}")


def _refuse(action: _Action, runtime: CliRuntime) -> None:
    runtime.output.error(
        f"{action.row.name} -> {action.row.destination}: {action.note}",
        hint=action.hint,
    )


def _make_handler(package: str, resource_dir: str, mode: str):
    def handler(args: Any, runtime: CliRuntime) -> int:
        tool = runtime.identity.command_name
        version = runtime.identity.version
        destinations = _destinations(args.harness, args.dest)
        try:
            sources = _load_sources(package, resource_dir)
        except SkillError as exc:
            raise CliFailure(str(exc)) from exc
        rows = _collect(sources, tool, version, destinations)
        if mode in ("check", "list"):
            leftovers = [path for dest in destinations for path in _leftovers(dest, tool)]
            return _report(rows, leftovers, runtime, tool, version, mode)
        overwrite = bool(args.overwrite_modified)
        refused = 0
        for destination in destinations:
            for leftover in _leftovers(destination, tool):
                if runtime.dry_run:
                    runtime.output.primary(f"would remove leftover {leftover}")
                else:
                    _remove_path(leftover)
                    runtime.output.primary(f"removed leftover {leftover}")
        for row in rows:
            rendered = (
                _render(row.name, sources[row.name], tool, version)
                if row.packaged
                else None
            )
            if mode == "install":
                action = _plan_install(row, rendered, overwrite)
            else:
                action = _plan_uninstall(row, overwrite)
            if action.kind == "refuse":
                refused += 1
                _refuse(action, runtime)
            else:
                _execute(action, rendered, runtime)
        if runtime.dry_run:
            runtime.output.emit(LogLevel.INFO, "Dry run: no changes made.", force=True)
        return 1 if refused else 0

    return handler


def _report(
    rows: Sequence[_Row],
    leftovers: Sequence[Path],
    runtime: CliRuntime,
    tool: str,
    version: str,
    mode: str,
) -> int:
    ordered = sorted(rows, key=lambda row: (str(row.destination), row.name))
    entries = [
        {"name": row.name, "destination": str(row.destination), "state": row.state.value}
        for row in ordered
    ]
    if runtime.json_mode:
        runtime.output.primary({
            "tool": tool,
            "version": version,
            "skills": entries,
            "leftovers": [str(path) for path in leftovers],
        })
    else:
        for entry in entries:
            runtime.output.primary(
                f"{entry['state']:<10} {entry['name']}  {entry['destination']}"
            )
        for path in leftovers:
            runtime.output.primary(f"leftover {path}")
    if mode == "list":
        return 0
    bad = [row for row in rows if row.state is not SkillState.CURRENT]
    if bad:
        runtime.output.error(
            f"{len(bad)} skill(s) are not current",
            hint=f"run `{tool} skills install` (see `{tool} skills list`)",
        )
    if leftovers:
        runtime.output.error(
            f"{len(leftovers)} leftover temporary path(s) from an interrupted install",
            hint=f"run `{tool} skills install` to clean them up",
        )
    return 1 if bad or leftovers else 0


def _target_options() -> tuple[OptionSpec, ...]:
    return (
        OptionSpec(
            ("--harness",),
            "target harness: claude, agents or all (default: all)",
            group="TARGET",
            metavar="HARNESS",
            parser_kwargs={"choices": ("claude", "agents", "all"), "default": None},
            mutually_exclusive_group="target",
        ),
        OptionSpec(
            ("--dest",),
            "use exactly this directory instead of a harness directory",
            group="TARGET",
            metavar="DIR",
            parser_kwargs={"default": None},
            mutually_exclusive_group="target",
        ),
    )


def _overwrite_option() -> OptionSpec:
    return OptionSpec(
        ("--overwrite-modified",),
        "replace skills you modified locally",
        group="TARGET",
        parser_kwargs={"action": "store_true", "default": False},
    )


def register_skills_verbs(
    registry: CliRegistry, *, package: str, resource_dir: str = "skills"
) -> None:
    """Add the ``skills`` delegate group (install, uninstall, check, list)."""

    if hasattr(registry, "_cli_extended_skills"):
        raise ValueError("skills verbs are already registered on this registry")
    parent = registry.identity
    child = CliRegistry(
        CliIdentity(
            name=parent.name,
            version=parent.version,
            long_name=f"{parent.long_name} — agent skills",
            command=parent.command_name,
        ),
        prog=f"{registry.prog} skills",
        description=f"Manage the agent skills packaged with {parent.command_name}.",
        logging_logger=registry.logging_logger,
        unexpected_exceptions=registry.unexpected_exceptions,
    )
    mutating = {"mutating": True, "dry_run": True, "confirmation_required": False,
                "include_json": False}
    child.register(VerbSpec(
        "install",
        description="install or update the packaged skills, removing orphans",
        group=VerbGroup.MODIFICATION.value,
        options=(*_target_options(), _overwrite_option()),
        handler=_make_handler(package, resource_dir, "install"),
        **mutating,
    ))
    child.register(VerbSpec(
        "uninstall",
        description="remove the skills this tool installed",
        group=VerbGroup.MODIFICATION.value,
        options=(*_target_options(), _overwrite_option()),
        handler=_make_handler(package, resource_dir, "uninstall"),
        **mutating,
    ))
    child.register(VerbSpec(
        "check",
        description="exit 1 unless every packaged skill is current",
        options=_target_options(),
        handler=_make_handler(package, resource_dir, "check"),
    ))
    child.register(VerbSpec(
        "list",
        description="list packaged skills and their installed state",
        options=_target_options(),
        handler=_make_handler(package, resource_dir, "list"),
    ))
    delegate: RegisteredCli = child.build()
    registry.register(VerbSpec(
        "skills",
        description="install, check, or remove this tool's packaged agent skills",
        group=VerbGroup.MAINTENANCE.value,
        delegate=delegate,
    ))
    registry._cli_extended_skills = (package, resource_dir)  # type: ignore[attr-defined]
