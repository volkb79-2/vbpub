"""Systemd unit files and drop-ins used by the guarded applier."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _fs
from .file import (
    _decode_value,
    _image_fingerprint,
    _mode_value,
    _name_or_id,
    _public_image,
    _resolve_id,
    _snapshot,
)
from .kinds import KindSpec, RevertHooks, register_kind, require_keys

_SYSTEMD_UNIT_DIR = "/etc/systemd/system"
_UNIT_CHAR_RE = re.compile(r"^[A-Za-z0-9:_.\\@-]+$")
_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".device",
    ".mount",
    ".automount",
    ".swap",
    ".target",
    ".path",
    ".timer",
    ".slice",
    ".scope",
)
_EXEC_LINE_RE = re.compile(r"^\s*(Exec[A-Za-z]+)\s*=\s*(.*)$")
_UNIT_REFERENCE_RE = re.compile(
    r"^\s*(?:Wants|Requires|Requisite|BindsTo|PartOf|Upholds|Conflicts|Before|After|"
    r"OnFailure|OnSuccess|PropagatesReloadTo|ReloadPropagatedFrom|PropagatesStopTo|"
    r"StopPropagatedFrom|JoinsNamespaceOf)\s*=\s*(.*)$"
)
_EXEC_PREFIXES = "-@:+!"
_UNSAFE_EXEC_PREFIXES = ("/tmp", "/var/tmp", "/home")
_UNIT_SEARCH_DIRS = (
    "/etc/systemd/system",
    "/run/systemd/system",
    "/usr/local/lib/systemd/system",
    "/usr/lib/systemd/system",
)
_UNIT_TEXT_LIMIT = 1024 * 1024
_ON_CHANGE = frozenset({"restart", "reload", "none", "pending"})
_INTENTS = frozenset({"authoritative", "fallback"})
_STAGE_PREFIX = ".augeaswrap-unit-"
_FILE_PREFIX = ".augeaswrap-unit-file-"
_COMMAND_TIMEOUT = 30.0


class _UnitCommandError(RuntimeError):
    """A command guard or live systemd action failed."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _UnitTimeout(_UnitCommandError):
    """A command guard exceeded its bounded timeout."""


def _run_command(argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    """Command seam used by validation, preflight and live commit actions."""

    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _UnitTimeout(f"timed out after {timeout:g}s: {argv[0]}") from exc
    except OSError as exc:
        raise _UnitCommandError(f"cannot run {argv[0]}: {exc}") from exc
    if result.returncode:
        output = (result.stderr or result.stdout).strip().splitlines()
        diagnostic = output[0][:240] if output else "no diagnostic"
        raise _UnitCommandError(
            f"exit {result.returncode}: {diagnostic}",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return result


def _is_live_root(root: Path) -> bool:
    """Only a live root may execute pre-removal systemd commands."""

    return root == Path("/")


def _unit_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("unit name must be a non-empty string")
    if "/" in value or "\x00" in value:
        raise ValueError("unit name must not contain a path separator or NUL")
    if value.startswith("-"):
        raise ValueError("unit name must not begin with a dash")
    if _UNIT_CHAR_RE.fullmatch(value) is None:
        raise ValueError("unit name contains an unsupported character")
    if not value.endswith(_UNIT_SUFFIXES):
        raise ValueError("unit name must end with a known systemd suffix")
    return value


def _dropin_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("drop-in name must be a non-empty string")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("drop-in name must not contain a path separator or NUL")
    if value in {".", ".."} or not value.endswith(".conf") or value == ".conf":
        raise ValueError("drop-in name must be a *.conf basename")
    stem = value.removesuffix(".conf")
    if _UNIT_CHAR_RE.fullmatch(stem) is None:
        raise ValueError("drop-in name contains an unsupported character")
    return value


def _unit_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} text is required when state is present")
    if "\x00" in value:
        raise ValueError(f"{label} text must not contain NUL")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} text must be valid UTF-8") from exc
    if len(encoded) > _UNIT_TEXT_LIMIT:
        raise ValueError(f"{label} text exceeds the {_UNIT_TEXT_LIMIT}-byte limit")
    return value


def _unit_mode(value: object) -> int:
    mode = _mode_value(value)
    if mode & 0o022:
        raise ValueError("unit mode must not be group/other-writable")
    if mode & 0o7000:
        raise ValueError("unit mode must not include special permission bits")
    return mode


def _unit_owner_ids(root: Path) -> tuple[int, int]:
    """Use root:root for the live host and the invoking identity under --root."""

    return (0, 0) if root == Path("/") else (os.geteuid(), os.getegid())


def _safe_unit_metadata(info: os.stat_result, *, expected_uid: int, path: Path, directory: bool) -> None:
    mode = info.st_mode
    permissions = stat.S_IMODE(mode)
    label = "directory" if directory else "unit file"
    if directory and not stat.S_ISDIR(mode):
        raise _exec_refusal("unit.path.type", f"unit path component is not a directory: {path}", str(path))
    if not directory and not stat.S_ISREG(mode):
        raise _exec_refusal("unit.path.type", f"managed unit is not a regular file: {path}", str(path))
    if info.st_uid != expected_uid:
        raise _exec_refusal("unit.path.owner", f"{label} is not owned by the expected uid {expected_uid}: {path}", str(path))
    if permissions & 0o022:
        raise _exec_refusal("unit.path.writable", f"{label} is group/other-writable: {path}", str(path))
    if permissions & 0o7000:
        raise _exec_refusal("unit.path.mode", f"{label} has special permission bits: {path}", str(path))


def _validate_unit_path(root: Path, path: str) -> None:
    """Validate every existing managed directory and file before planning/writing."""

    target = root / path.lstrip("/")
    _fs.safe_components(root, target)
    expected_uid, _expected_gid = _unit_owner_ids(root)
    relative = target.relative_to(root)
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        try:
            info = _lstat(cursor)
        except FileNotFoundError:
            continue
        _safe_unit_metadata(info, expected_uid=expected_uid, path=cursor, directory=True)
    try:
        info = _lstat(target)
    except FileNotFoundError:
        return
    _safe_unit_metadata(info, expected_uid=expected_uid, path=target, directory=False)


def _unit_enabled(root: Path, name: str) -> bool | None:
    """Observe declared enablement on the live host; fake roots have no manager."""

    if not _is_live_root(root):
        return None
    argv = ("systemctl", "is-enabled", "--", name)
    try:
        result = _run_command(argv, _COMMAND_TIMEOUT)
        returncode, output = result.returncode, result.stdout
    except _UnitCommandError as exc:
        if exc.returncode not in {1, 4}:
            raise
        returncode, output = exc.returncode, exc.stdout
    status = output.strip()
    known = {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
        "alias",
        "masked",
        "masked-runtime",
        "static",
        "indirect",
        "disabled",
        "generated",
        "transient",
        "bad",
        "not-found",
    }
    if status not in known:
        raise _UnitCommandError(f"unexpected systemctl is-enabled result for {name}: {status!r}", returncode=returncode, stdout=output)
    if status in {"enabled", "enabled-runtime"} and returncode == 0:
        return True
    if status in known - {"enabled", "enabled-runtime"}:
        return False
    raise _UnitCommandError(f"inconsistent systemctl is-enabled result for {name}: exit {returncode}, {status!r}", returncode=returncode, stdout=output)


def _preflight(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("unit preflight must be a non-empty argv array")
    argv = tuple(value)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise ValueError("unit preflight argv values must be non-empty strings without NUL")
    return argv


def _unit_path(name: str, dropin: str | None) -> str:
    if dropin is None:
        return f"{_SYSTEMD_UNIT_DIR}/{name}"
    return f"{_SYSTEMD_UNIT_DIR}/{name}.d/{dropin}"


def _unit_address(path: str) -> str:
    return f"unit:{path}"


def _combined_fingerprint(value: Mapping[str, Any]) -> str:
    target = value.get("target")
    target_fingerprint = target.get("fingerprint") if isinstance(target, Mapping) else "absent"
    encoded = json.dumps(
        {
            "path": value.get("path"),
            "target": target_fingerprint,
            "enabled": value.get("enabled"),
            "exec_paths": value.get("exec_paths"),
            "sources": value.get("sources"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_signature(target: object) -> tuple[object, ...]:
    """Compare the public target image fields carried by Change records."""

    if not isinstance(target, Mapping):
        return (False,)
    return tuple(target.get(key) for key in ("exists", "sha256", "mode", "uid", "gid"))


def _verification_signature(value: object) -> tuple[object, ...]:
    """Compare the managed unit state, excluding validation-only source files."""

    if not isinstance(value, Mapping):
        return (False,)
    if not isinstance(value.get("target"), Mapping):
        return ("fingerprint", value.get("fingerprint"))
    return (
        value.get("path"),
        _target_signature(value.get("target")),
        value.get("enabled"),
        value.get("exec_paths"),
    )


def _lstat(path: Path) -> os.stat_result:
    """A narrow seam for testing ownership without requiring a root test runner."""

    return path.lstat()


def _exec_command(line: str, path: str) -> str | None:
    match = _EXEC_LINE_RE.fullmatch(line)
    if match is None or not match.group(2).strip():
        return None
    try:
        argv = shlex.split(match.group(2), comments=False, posix=True)
    except ValueError as exc:
        raise PlanRefused(
            [Problem("unit.exec.parse", f"cannot parse {match.group(1)} in {path}: {exc}", path=path)]
        ) from exc
    command = argv[0].lstrip(_EXEC_PREFIXES)
    if not command:
        raise PlanRefused([Problem("unit.exec.empty", f"empty executable in {path}", path=path)])
    if not command.startswith("/"):
        raise PlanRefused(
            [Problem("unit.exec.relative", f"executable path must be absolute: {command}", path=path)]
        )
    if "%" in command or "$" in command:
        raise PlanRefused(
            [Problem("unit.exec.specifier", f"systemd specifiers and environment variables are not expanded in executable paths: {command}", path=path)]
        )
    return command


def _exec_paths(text: str, path: str) -> tuple[str, ...]:
    commands = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("#", ";")):
            continue
        command = _exec_command(line, path)
        if command is not None:
            commands.append(command)
    return tuple(sorted(set(commands)))


def _exec_refusal(code: str, message: str, path: str) -> PlanRefused:
    return PlanRefused([Problem(code, message, path=path)])


def _resolve_below_root(root: Path, absolute: str) -> Path:
    """Resolve virtual absolute paths without following symlinks in the host namespace."""

    pending = absolute.split("/")
    if absolute.startswith("/"):
        pending = pending[1:]
    resolved: list[str] = []
    missing = False
    links = 0
    while pending:
        part = pending.pop(0)
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise ValueError("path escapes selected root")
            resolved.pop()
            continue
        candidate = root.joinpath(*resolved, part)
        if missing:
            resolved.append(part)
            continue
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            missing = True
            resolved.append(part)
            continue
        if not stat.S_ISLNK(info.st_mode):
            resolved.append(part)
            continue
        links += 1
        if links > 40:
            raise ValueError("too many symlinks")
        target = os.readlink(candidate)
        target_parts = target.split("/")
        if target.startswith("/"):
            resolved.clear()
            target_parts = target_parts[1:]
        pending = target_parts + pending
    return root.joinpath(*resolved)


def _inspect_exec(root: Path, command: str, unit_path: str) -> dict[str, Any]:
    raw = PurePosixPath(command)
    normalized = posixpath.normpath(command)
    virtual_path = PurePosixPath(normalized)
    try:
        resolved_candidate = _resolve_below_root(root, normalized)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _exec_refusal("unit.exec.path", f"cannot resolve executable path {command}: {exc}", unit_path) from exc
    if not resolved_candidate.is_relative_to(root):
        raise _exec_refusal(
            "unit.exec.outside_root",
            f"executable path resolves outside the selected root: {command}",
            unit_path,
        )

    if ".." in raw.parts:
        raise _exec_refusal("unit.exec.traversal", f"executable path must not contain '..': {command}", unit_path)

    for prefix in _UNSAFE_EXEC_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            raise _exec_refusal(
                "unit.exec.unsafe_path",
                f"refusing executable below {prefix}: {normalized}",
                unit_path,
            )
    relative = virtual_path.parts[1:]
    cursor = root
    component_modes: list[dict[str, Any]] = []
    final: os.stat_result | None = None
    for part in relative:
        cursor = cursor / part
        try:
            info = _lstat(cursor)
        except FileNotFoundError as exc:
            raise _exec_refusal(
                "unit.exec.missing",
                f"executable path component is missing: {command}",
                unit_path,
            ) from exc
        except OSError as exc:
            raise _exec_refusal(
                "unit.exec.io",
                f"cannot inspect executable {command}: {exc}",
                unit_path,
            ) from exc
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            raise _exec_refusal(
                "unit.exec.symlink",
                f"executable path contains a symlink component: {command}",
                unit_path,
            )
        if mode & 0o022:
            raise _exec_refusal(
                "unit.exec.writable",
                f"executable path has a group/other-writable component: {command}",
                unit_path,
            )
        if info.st_uid != 0:
            raise _exec_refusal(
                "unit.exec.owner",
                f"executable path component is not root-owned: {cursor}",
                unit_path,
            )
        component_modes.append({"path": str(cursor), "mode": mode})
        final = info

    assert final is not None
    if not stat.S_ISREG(final.st_mode):
        raise _exec_refusal(
            "unit.exec.not_regular",
            f"executable is not a regular file: {command}",
            unit_path,
        )
    if not stat.S_IMODE(final.st_mode) & 0o111:
        raise _exec_refusal(
            "unit.exec.not_executable",
            f"executable bit is not set: {command}",
            unit_path,
        )
    return {
        "path": command,
        "mode": stat.S_IMODE(final.st_mode),
        "uid": final.st_uid,
        "gid": final.st_gid,
        "component_modes": component_modes,
    }


def _exec_observations(root: Path, text: str, unit_path: str) -> list[dict[str, Any]]:
    return [_inspect_exec(root, command, unit_path) for command in _exec_paths(text, unit_path)]


def _source_file(root: Path, absolute: str, *, allow_symlink: bool = False) -> dict[str, Any] | None:
    target = root / absolute.lstrip("/")
    _fs.safe_components(root, target.parent)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    link_target: str | None = None
    source = target
    masked = False
    if stat.S_ISLNK(info.st_mode):
        if not allow_symlink:
            raise _fs.PathRefusal(
                f"refusing symlinked unit source: {absolute}", kind="symlink", path=absolute
            )
        raw_target = os.readlink(target)
        if raw_target == "/dev/null":
            link_target = "/dev/null"
            masked = True
            data = b""
            info = os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            source_path: str | None = None
        else:
            try:
                source = _resolve_below_root(root, absolute)
                info = source.stat()
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError(f"unit source symlink is broken or recursive: {absolute}") from exc
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"unit source symlink does not resolve to a regular file: {absolute}")
            link_target = "/" + source.relative_to(root).as_posix()
            source_path = link_target
            data = source.read_bytes()
    else:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"systemd unit source is not a regular file: {absolute}")
        data = target.read_bytes()
        source_path = absolute
    return {
        "path": absolute,
        "source_path": source_path,
        "link_target": link_target,
        "masked": masked,
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def _unit_references(data: bytes) -> set[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # This helper only discovers dependency filenames; systemd-analyze
        # remains the syntax authority for any referenced unit it loads.
        return set()
    result: set[str] = set()
    for line in text.splitlines():
        match = _UNIT_REFERENCE_RE.fullmatch(line)
        if match is None:
            continue
        try:
            values = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"cannot parse unit dependency list: {exc}") from exc
        for value in values:
            unit = value.lstrip("-")
            if unit.endswith(_UNIT_SUFFIXES) and _UNIT_CHAR_RE.fullmatch(unit):
                result.add(unit)
    return result


def _source_bytes(root: Path, source: Mapping[str, Any]) -> bytes:
    if source.get("masked") is True:
        return b""
    source_path = source.get("source_path")
    if not isinstance(source_path, str):
        raise ValueError("systemd source has no source path")
    return (root / source_path.lstrip("/")).read_bytes()


def _directory_entries(root: Path, absolute: str) -> list[Path]:
    directory = root / absolute.lstrip("/")
    _fs.safe_components(root, directory)
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"systemd unit search path is not a directory: {absolute}")
    return sorted(directory.iterdir(), key=lambda item: item.name)


def _verify_skip_reason(*, dropin: str | None, base_present: bool) -> str | None:
    """Say exactly why staged systemd syntax verification will not run."""

    if dropin is not None and not base_present:
        return "base unit not found in the selected root"
    if shutil.which("systemd-analyze") is None:
        return "systemd-analyze is not available in PATH"
    return None


def _source_inventory(
    root: Path,
    name: str,
    path: str,
    dropin: str | None,
    unit_text: str | None = None,
) -> list[dict[str, Any]]:
    """Capture the unit search paths and transitive referenced units for verify."""

    sources: dict[str, dict[str, Any]] = {}
    pending_units: list[str] = []
    visited_units: set[str] = set()

    def add_source(absolute: str, *, allow_symlink: bool = True) -> dict[str, Any] | None:
        source = sources.get(absolute)
        if source is not None:
            return source
        source = _source_file(root, absolute, allow_symlink=allow_symlink)
        if source is None:
            return None
        sources[absolute] = source
        resolved_path = source.get("source_path")
        if source.get("link_target") and source.get("masked") is not True and isinstance(resolved_path, str):
            add_source(resolved_path, allow_symlink=True)
        pending_units.extend(_unit_references(_source_bytes(root, source)))
        return source

    def add_dropins(unit_name: str) -> None:
        for directory in _UNIT_SEARCH_DIRS:
            dropin_dir = f"{directory}/{unit_name}.d"
            for entry in _directory_entries(root, dropin_dir):
                absolute = f"{dropin_dir}/{entry.name}"
                if entry.suffix == ".conf" and absolute != path:
                    add_source(absolute)

    def find_base(unit_name: str) -> str | None:
        for directory in _UNIT_SEARCH_DIRS:
            absolute = f"{directory}/{unit_name}"
            source = add_source(absolute)
            if source is not None:
                return absolute
        return None

    def add_search_tree() -> None:
        # Stage the static local/vendor trees in full so systemd's implicit
        # dependencies are present too. Mutable /etc and /run content is added
        # below only when requested or discovered through dependency closure.
        for directory in _UNIT_SEARCH_DIRS[2:]:
            for entry in _directory_entries(root, directory):
                try:
                    _unit_name(entry.name)
                except ValueError:
                    continue
                absolute = f"{directory}/{entry.name}"
                if absolute == path:
                    continue
                try:
                    info = entry.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    continue
                if stat.S_ISLNK(info.st_mode):
                    try:
                        add_source(absolute)
                    except ValueError:
                        # An unrelated broken/out-of-root alias must not make
                        # every unit plan fail. A referenced one is retried by
                        # find_base and then fails closed.
                        continue
                elif stat.S_ISREG(info.st_mode):
                    add_source(absolute)

    add_search_tree()
    if dropin is None:
        if unit_text is not None:
            pending_units.extend(_unit_references(unit_text.encode("utf-8")))
    else:
        find_base(name)
        if unit_text is not None:
            pending_units.extend(_unit_references(unit_text.encode("utf-8")))
    add_dropins(name)

    while pending_units:
        dependency = pending_units.pop()
        if dependency == name or dependency in visited_units:
            continue
        visited_units.add(dependency)
        if find_base(dependency) is not None:
            add_dropins(dependency)

    return [sources[key] for key in sorted(sources)]


def _observe_unit(root: Path, path: str, name: str, dropin: str | None, text: str | None) -> dict[str, Any]:
    _validate_unit_path(root, path)
    target = _snapshot(root, path)
    exec_paths = [] if text is None else _exec_observations(root, text, path)
    enabled = _unit_enabled(root, name) if dropin is None else None
    value = {
        "path": path,
        "target": target,
        "enabled": enabled,
        "exec_paths": exec_paths,
        "sources": _source_inventory(root, name, path, dropin, text),
    }
    value["fingerprint"] = _combined_fingerprint(value)
    return value


def _restored_exec_observations(root: Path, data: bytes, unit_path: str) -> list[dict[str, Any]]:
    """Inspect observable commands when old bytes are valid UTF-8 unit text.

    A revert must preserve pre-existing bytes exactly even when they predate
    Augeaswrap and are not valid UTF-8. Such bytes cannot be parsed for Exec
    directives, but the target-byte and metadata fingerprint still identifies
    the restored state.
    """

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return _exec_observations(root, text, unit_path)


def _public_unit(value: Mapping[str, Any]) -> dict[str, Any]:
    target = value.get("target")
    assert isinstance(target, Mapping)
    return {
        "path": value.get("path"),
        "target": _public_image(target),
        "enabled": value.get("enabled"),
        "exec_paths": value.get("exec_paths"),
        "sources": value.get("sources"),
    }


@dataclass(frozen=True)
class _StagedUnit:
    exists: bool
    data: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None
    expected: dict[str, Any]
    full_unit: bool
    name: str
    path: str
    dropin: str | None


def _ensure_stage_directory(root: Path, directory: Path, mode: int) -> None:
    if directory.is_relative_to(root):
        _fs.ensure_directory(root, directory, mode=mode)
        return
    directory.mkdir(mode=mode, parents=True, exist_ok=True)
    os.chmod(directory, mode)


def _stage_write(root: Path, path: Path, data: bytes, mode: int) -> None:
    _fs.atomic_write(root, path, data, mode, temp_prefix=_STAGE_PREFIX)


def _copy_sources(root: Path, stage_root: Path, sources: list[dict[str, Any]]) -> None:
    for directory in _UNIT_SEARCH_DIRS:
        _ensure_stage_directory(stage_root, stage_root / directory.lstrip("/"), 0o755)
    for source in sources:
        if source.get("link_target") is not None:
            continue
        absolute = source["path"]
        data = _source_bytes(root, source)
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError(f"systemd verify source changed while staging: {absolute}")
        target = stage_root / absolute.lstrip("/")
        _stage_write(stage_root, target, data, int(source["mode"]))
    for source in sources:
        link_target = source.get("link_target")
        if not isinstance(link_target, str):
            continue
        absolute = source["path"]
        target = stage_root / absolute.lstrip("/")
        _ensure_stage_directory(stage_root, target.parent, 0o755)
        if source.get("masked") is True:
            null_target = stage_root / "dev/null"
            if not null_target.exists():
                _stage_write(stage_root, null_target, b"", 0o644)
        else:
            null_target = stage_root / link_target.lstrip("/")
            if not null_target.exists():
                raise ValueError(f"systemd symlink target was not staged: {link_target}")
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(os.path.relpath(null_target, target.parent))


def _command_failure(code: str, label: str, exc: BaseException, path: str) -> PlanRefused:
    return PlanRefused([Problem(code, f"{label} failed for {path}: {exc}", path=path)])


def _verify_staged_tree(
    root: Path,
    stage_dir: Path,
    name: str,
    path: str,
    dropin: str | None,
    data: bytes,
    mode: int,
    preflight: tuple[str, ...] | None,
    verify_skip_reason: str | None,
) -> None:
    hash_dir = stage_dir / hashlib.sha256(_unit_address(path).encode("utf-8")).hexdigest()
    stage_root = hash_dir / "root"
    _ensure_stage_directory(root, stage_root, 0o700)
    base_absolute = f"{_SYSTEMD_UNIT_DIR}/{name}"
    sources = _source_inventory(root, name, path, dropin, data.decode("utf-8", errors="replace"))
    _copy_sources(root, stage_root, sources)
    _stage_write(stage_root, stage_root / path.lstrip("/"), data, mode)

    if verify_skip_reason is None:
        argv = (
            "systemd-analyze",
            f"--root={stage_root}",
            "verify",
            base_absolute,
        )
        try:
            _run_command(argv, _COMMAND_TIMEOUT)
        except (OSError, ValueError, RuntimeError) as exc:
            raise _command_failure("unit.verify.failed", "systemd-analyze verify", exc, path) from exc

    if preflight is not None:
        try:
            _run_command(preflight, _COMMAND_TIMEOUT)
        except (subprocess.TimeoutExpired, _UnitTimeout) as exc:
            raise _command_failure("unit.preflight.timeout", "preflight timed out", exc, path) from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise _command_failure("unit.preflight.failed", "preflight", exc, path) from exc


@dataclass(frozen=True)
class Unit:
    """One systemd unit file or drop-in below ``/etc/systemd/system``."""

    name: str
    state: str
    text: str | None = None
    mode: str | int | None = None
    owner: str | int | None = None
    group: str | int | None = None
    dropin: str | None = None
    intent: str | None = None
    enable: bool | None = None
    on_change: str | None = None
    preflight: tuple[str, ...] | list[str] | None = None
    kind: ClassVar[str] = "unit"

    def __post_init__(self) -> None:
        _unit_name(self.name)
        if self.dropin is not None:
            _dropin_name(self.dropin)
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("unit state must be 'present' or 'absent'")
        _name_or_id(self.owner, "owner")
        _name_or_id(self.group, "group")
        if self.intent is not None and self.intent not in _INTENTS:
            raise ValueError("unit intent must be 'authoritative' or 'fallback'")
        if self.on_change is not None and self.on_change not in _ON_CHANGE:
            raise ValueError("unit on_change must be restart, reload, none, or pending")
        preflight = _preflight(self.preflight)
        if preflight is not None:
            object.__setattr__(self, "preflight", preflight)
        if self.dropin is None:
            if self.state == "present":
                _unit_text(self.text, "unit")
                if self.mode is None:
                    raise ValueError("unit mode is required when state is present")
                _unit_mode(self.mode)
                if self.intent is None:
                    raise ValueError("intent is required for a present unit")
                if type(self.enable) is not bool:
                    raise ValueError("unit enable is required for a present unit")
                if self.on_change is None:
                    raise ValueError("unit on_change is required for a present unit")
            else:
                if any(value is not None for value in (self.text, self.mode, self.owner, self.group)):
                    raise ValueError("absent unit must not specify text, mode, owner, or group")
                if self.enable is not None or self.on_change is not None or self.intent is not None or preflight is not None:
                    raise ValueError("absent unit must not specify intent, enable, on_change, or preflight")
        else:
            if self.enable is not None:
                raise ValueError("enable applies only to a whole unit, not a drop-in")
            if self.state == "present":
                _unit_text(self.text, "drop-in")
                if self.mode is None:
                    raise ValueError("drop-in mode is required when state is present")
                _unit_mode(self.mode)
                if self.intent is None:
                    raise ValueError("intent is required for a present drop-in")
                if self.on_change is None:
                    raise ValueError("drop-in on_change is required when state is present")
            else:
                if any(value is not None for value in (self.text, self.mode, self.owner, self.group)):
                    raise ValueError("absent drop-in must not specify text, mode, owner, or group")
                if self.intent is not None or self.on_change is None or preflight is not None:
                    raise ValueError("absent drop-in requires on_change and takes no preflight")

    @property
    def path(self) -> str:
        return _unit_path(self.name, self.dropin)

    def addresses(self) -> tuple[str, ...]:
        return (_unit_address(self.path),)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        root = Path(getattr(ctx, "root"))
        desired_text = self.text if self.state == "present" else None
        return {
            self.addresses()[0]: _observe_unit(root, self.path, self.name, self.dropin, desired_text)
        }

    def _desired(self, ctx: object, current: Mapping[str, Any]) -> dict[str, Any]:
        path = self.path
        root = Path(getattr(ctx, "root"))
        if self.state == "absent":
            target = {"exists": False, "fingerprint": "absent"}
            exec_paths: list[dict[str, Any]] = []
            enabled = False if self.dropin is None and _is_live_root(root) else current.get("enabled")
        else:
            assert self.text is not None and self.mode is not None
            data = self.text.encode("utf-8")
            try:
                uid = _resolve_id(self.owner, group=False)
                gid = _resolve_id(self.group, group=True)
            except ValueError as exc:
                raise PlanRefused([Problem("unit.owner.invalid", str(exc), path=path)]) from exc
            default_uid, default_gid = _unit_owner_ids(root)
            uid = default_uid if uid is None else uid
            gid = default_gid if gid is None else gid
            if root == Path("/") and uid != 0:
                raise PlanRefused([Problem("unit.owner.invalid", "managed unit files must be root-owned", path=path)])
            target = {
                "exists": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data_b64": None,
                "mode": _unit_mode(self.mode),
                "uid": uid,
                "gid": gid,
            }
            target["fingerprint"] = _image_fingerprint(target)
            exec_paths = _exec_observations(root, self.text, path)
            enabled = self.enable if self.dropin is None and _is_live_root(root) else current.get("enabled")
        value = {
            "path": path,
            "target": target,
            "enabled": enabled,
            "exec_paths": exec_paths,
            "sources": current.get("sources", []),
        }
        value["fingerprint"] = _combined_fingerprint(value)
        return value

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        current = observed[address]
        if not isinstance(current, Mapping):
            raise ValueError("unit observation must be a mapping")
        desired = self._desired(ctx, current)
        action = "noop" if current.get("fingerprint") == desired["fingerprint"] else "change"
        details: tuple[str, ...] = ()
        if action == "change":
            if self.state == "absent":
                details = ("state=absent", "stop and disable before delete", "daemon-reload then reset-failed")
            else:
                rows = []
                if self.dropin is not None:
                    base_paths = {f"{directory}/{self.name}" for directory in _UNIT_SEARCH_DIRS}
                    base_present = any(source.get("path") in base_paths for source in current.get("sources", []))
                else:
                    base_present = True
                skip_reason = _verify_skip_reason(dropin=self.dropin, base_present=base_present)
                if skip_reason is not None:
                    rows.append(f"verify: skipped ({skip_reason}; ExecStart and preflight checks still run)")
                if self.dropin is None and current.get("enabled") != desired.get("enabled"):
                    rows.append(f"enablement: reconcile to {'enabled' if desired.get('enabled') else 'disabled'}")
                if self.on_change == "pending" and self._runtime_change_applies([Change(self.kind, address, action, _public_unit(current), _public_unit(desired), self)]):
                    rows.append("runtime change recorded pending")
                details = tuple(rows)
        return [
            Change(
                self.kind,
                address,
                action,
                _public_unit(current),
                _public_unit(desired),
                self,
                details,
            )
        ]

    def stage(self, ctx: object, change: Change) -> _StagedUnit:
        path = self.path
        observed = self.observe(ctx)[self.addresses()[0]]
        desired = self._desired(ctx, observed)
        if not change.after.get("target", {}).get("exists"):
            return _StagedUnit(
                False,
                None,
                None,
                None,
                None,
                desired,
                self.dropin is None,
                self.name,
                path,
                self.dropin,
            )
        assert self.text is not None and self.mode is not None
        target = desired["target"]
        data = self.text.encode("utf-8")
        mode = int(target["mode"])
        stage_dir = Path(getattr(ctx, "stage_dir"))
        _verify_staged_tree(
            Path(getattr(ctx, "root")),
            stage_dir,
            self.name,
            path,
            self.dropin,
            data,
            mode,
            _preflight(self.preflight),
            next(
                (
                    detail.removeprefix("verify: skipped (").removesuffix(")")
                    for detail in change.details
                    if detail.startswith("verify: skipped (")
                ),
                None,
            ),
        )
        return _StagedUnit(
            True,
            data,
            mode,
            int(target["uid"]),
            int(target["gid"]),
            desired,
            self.dropin is None,
            self.name,
            path,
            self.dropin,
        )

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedUnit):
            raise TypeError("unit commit requires its staged image")
        root = Path(getattr(ctx, "root"))
        target = root / self.path.lstrip("/")
        _validate_unit_path(root, self.path)
        if not staged.exists:
            _fs.unlink_and_sync(root, target)
            return self.observe(ctx)[self.addresses()[0]]
        assert staged.data is not None and staged.mode is not None
        assert staged.uid is not None and staged.gid is not None
        _fs.atomic_write(
            root,
            target,
            staged.data,
            staged.mode,
            temp_prefix=_FILE_PREFIX,
            owner=(staged.uid, staged.gid),
        )
        _validate_unit_path(root, self.path)
        return self.observe(ctx)[self.addresses()[0]]

    def _runtime_action(self) -> PostAction | None:
        if self.on_change in {None, "none"}:
            return None
        assert self.on_change is not None
        verb = "restart" if self.on_change in {"restart", "pending"} else "reload"
        return PostAction(
            f"unit {verb} {self.name}",
            ("systemctl", verb, "--", self.name),
            _COMMAND_TIMEOUT,
            pending=self.on_change == "pending",
            phase="runtime",
        )

    def _runtime_change_applies(self, changes: list[Change]) -> bool:
        """A socket restarts only for a text transition, not metadata-only churn."""

        for change in changes:
            before = change.before.get("target", {}) if isinstance(change.before, Mapping) else {}
            after = change.after.get("target", {}) if isinstance(change.after, Mapping) else {}
            if _target_signature(before) == _target_signature(after):
                continue
            if not self.name.endswith(".socket"):
                return True
            before_sha = before.get("sha256") if isinstance(before, Mapping) and before.get("exists") else None
            after_sha = after.get("sha256") if isinstance(after, Mapping) and after.get("exists") else None
            if before_sha != after_sha:
                return True
        return False

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        changed = [change for change in changes if change.action == "change"]
        if not changed:
            return []
        change = changed[0]
        before = change.before if isinstance(change.before, Mapping) else {}
        after = change.after if isinstance(change.after, Mapping) else {}
        before_target = before.get("target", {}) if isinstance(before, Mapping) else {}
        after_target = after.get("target", {}) if isinstance(after, Mapping) else {}
        file_changed = _target_signature(before_target) != _target_signature(after_target)
        actions: list[PostAction] = []
        if self.dropin is None and self.state == "absent":
            actions.extend(
                (
                    PostAction(
                        f"unit stop {self.name}",
                        ("systemctl", "stop", "--", self.name),
                        _COMMAND_TIMEOUT,
                        phase="before_commit",
                    ),
                    PostAction(
                        f"unit disable {self.name}",
                        ("systemctl", "disable", "--", self.name),
                        _COMMAND_TIMEOUT,
                        phase="before_commit",
                    ),
                )
            )
        if file_changed:
            actions.append(
                PostAction(
                    "daemon-reload",
                    ("systemctl", "daemon-reload"),
                    _COMMAND_TIMEOUT,
                    phase="daemon_reload",
                )
            )
        if self.dropin is None and self.state == "present":
            before_enabled = before.get("enabled")
            after_enabled = after.get("enabled")
            if isinstance(after_enabled, bool) and before_enabled != after_enabled:
                verb = "enable" if after_enabled else "disable"
                actions.append(
                    PostAction(
                        f"unit {verb} {self.name}",
                        ("systemctl", verb, "--", self.name),
                        _COMMAND_TIMEOUT,
                        phase="runtime",
                    )
                )
            elif after_enabled is None and file_changed and self.enable is True:
                actions.append(
                    PostAction(
                        f"unit enable {self.name}",
                        ("systemctl", "enable", "--", self.name),
                        _COMMAND_TIMEOUT,
                        phase="runtime",
                    )
                )
        if self.state == "present" and file_changed and self._runtime_change_applies(changed):
            runtime = self._runtime_action()
            if runtime is not None:
                actions.append(runtime)
        elif self.dropin is None and self.state == "absent" and file_changed:
            actions.append(
                PostAction(
                    f"unit reset-failed {self.name}",
                    ("systemctl", "reset-failed", "--", self.name),
                    _COMMAND_TIMEOUT,
                    phase="runtime",
                )
            )
        elif self.dropin is not None and file_changed and self._runtime_change_applies(changed):
            runtime = self._runtime_action()
            if runtime is not None:
                actions.append(runtime)
        return actions

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedUnit):
            return [Problem("unit.verify.expected", f"no staged image recorded for {self.path}", path=self.path)]
        try:
            actual = self.observe(ctx)[address]
        except (OSError, _fs.PathRefusal, PlanRefused) as exc:
            return [Problem("unit.verify.failed", f"cannot verify {self.path}: {exc}", path=self.path)]
        if _verification_signature(actual) == _verification_signature(expected.expected):
            return []
        return [Problem("unit.verify.failed", f"verification failed for {self.path}", path=self.path)]


@dataclass(frozen=True)
class _RestoredUnit:
    """Resource returned by the unit revert hook."""

    path: str
    image: Mapping[str, Any]
    name: str
    dropin: str | None
    actions: tuple[PostAction, ...] = ()
    kind: str = "unit"

    def addresses(self) -> tuple[str, ...]:
        return (_unit_address(self.path),)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        root = Path(getattr(ctx, "root"))
        _validate_unit_path(root, self.path)
        target = _snapshot(root, self.path)
        exec_paths = [] if not target.get("exists") else _restored_exec_observations(root, _decode_value(target), self.path)
        value = {
            "path": self.path,
            "target": target,
            "enabled": _unit_enabled(root, self.name) if self.dropin is None else None,
            "exec_paths": exec_paths,
            "sources": _source_inventory(
                root,
                self.name,
                self.path,
                self.dropin,
                _decode_value(target).decode("utf-8", errors="replace") if target.get("exists") else None,
            ),
        }
        value["fingerprint"] = _combined_fingerprint(value)
        return {
            self.addresses()[0]: value
        }

    def _desired(self, ctx: object, current: Mapping[str, Any]) -> dict[str, Any]:
        if not self.image.get("exists"):
            value = {
                "path": self.path,
                "target": {"exists": False, "fingerprint": "absent"},
                "enabled": self.image.get("enabled"),
                "exec_paths": [],
                "sources": current.get("sources", []),
            }
            value["fingerprint"] = _combined_fingerprint(value)
            return value
        data = _decode_value(self.image)
        target = {
            "exists": True,
            "sha256": self.image.get("sha256"),
            "data_b64": self.image.get("data_b64"),
            "mode": int(self.image["mode"]),
            "uid": int(self.image["uid"]),
            "gid": int(self.image["gid"]),
        }
        target["fingerprint"] = _image_fingerprint(target)
        wanted = {
            "path": self.path,
            "target": target,
            "enabled": self.image.get("enabled"),
            "exec_paths": _restored_exec_observations(Path(getattr(ctx, "root")), data, self.path),
            "sources": current.get("sources", []),
        }
        wanted["fingerprint"] = _combined_fingerprint(wanted)
        return wanted

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        current = observed[address]
        if not isinstance(current, Mapping):
            raise ValueError("restored unit observation must be a mapping")
        desired = self._desired(ctx, current)
        action = "noop" if current.get("fingerprint") == desired["fingerprint"] else "change"
        return [
            Change(
                self.kind,
                address,
                action,
                _public_unit(current),
                _public_unit(desired),
                self,
            )
        ]

    def stage(self, ctx: object, change: Change) -> _StagedUnit:
        desired = self._desired(ctx, self.observe(ctx)[self.addresses()[0]])
        target = desired["target"]
        if not target.get("exists"):
            return _StagedUnit(
                False,
                None,
                None,
                None,
                None,
                desired,
                self.dropin is None,
                self.name,
                self.path,
                self.dropin,
            )
        data = _decode_value(target)
        return _StagedUnit(
            True,
            data,
            int(target["mode"]),
            int(target["uid"]),
            int(target["gid"]),
            desired,
            self.dropin is None,
            self.name,
            self.path,
            self.dropin,
        )

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedUnit):
            raise TypeError("restore commit requires a staged image")
        root = Path(getattr(ctx, "root"))
        target_path = root / self.path.lstrip("/")
        _validate_unit_path(root, self.path)
        if not staged.exists:
            _fs.unlink_and_sync(root, target_path)
        else:
            assert staged.data is not None and staged.mode is not None
            assert staged.uid is not None and staged.gid is not None
            _fs.atomic_write(
                root,
                target_path,
                staged.data,
                staged.mode,
                temp_prefix=_FILE_PREFIX,
                owner=(staged.uid, staged.gid),
            )
        _validate_unit_path(root, self.path)
        return self.observe(ctx)[self.addresses()[0]]

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        changed = [change for change in changes if change.action == "change"]
        if not changed:
            return []
        change = changed[0]
        before = change.before if isinstance(change.before, Mapping) else {}
        after = change.after if isinstance(change.after, Mapping) else {}
        before_target = before.get("target", {}) if isinstance(before, Mapping) else {}
        after_target = after.get("target", {}) if isinstance(after, Mapping) else {}
        file_changed = _target_signature(before_target) != _target_signature(after_target)
        if not self.image.get("exists") and self.dropin is None:
            actions = [
                PostAction(
                    f"unit stop {self.name}",
                    ("systemctl", "stop", "--", self.name),
                    _COMMAND_TIMEOUT,
                    phase="before_commit",
                ),
                PostAction(
                    f"unit disable {self.name}",
                    ("systemctl", "disable", "--", self.name),
                    _COMMAND_TIMEOUT,
                    phase="before_commit",
                ),
            ]
            if file_changed:
                actions.append(
                    PostAction("daemon-reload", ("systemctl", "daemon-reload"), _COMMAND_TIMEOUT, phase="daemon_reload")
                )
            return actions

        actions: list[PostAction] = []
        if file_changed:
            actions.append(
                PostAction("daemon-reload", ("systemctl", "daemon-reload"), _COMMAND_TIMEOUT, phase="daemon_reload")
            )
        before_enabled = before.get("enabled")
        desired_enabled = self.image.get("enabled") if self.dropin is None else None
        if isinstance(desired_enabled, bool) and before_enabled != desired_enabled:
            verb = "enable" if desired_enabled else "disable"
            actions.append(
                PostAction(
                    f"unit {verb} {self.name}",
                    ("systemctl", verb, "--", self.name),
                    _COMMAND_TIMEOUT,
                    phase="runtime",
                )
            )
        if file_changed:
            for action in self.actions:
                if len(action.argv) > 1 and action.argv[0] == "systemctl" and action.argv[1] in {
                    "restart",
                    "reload",
                    "reset-failed",
                }:
                    actions.append(
                        PostAction(
                            action.name,
                            ("systemctl", action.argv[1], "--", self.name),
                            action.timeout,
                            pending=action.pending,
                            phase="runtime",
                        )
                    )
        return actions

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedUnit):
            return [Problem("revert.verify.expected", f"no staged pre-image for {self.path}", path=self.path)]
        try:
            actual = self.observe(ctx)[address]
        except (OSError, _fs.PathRefusal, PlanRefused) as exc:
            return [Problem("revert.verify.failed", f"cannot verify restored {self.path}: {exc}", path=self.path)]
        if _verification_signature(actual) == _verification_signature(expected.expected):
            return []
        return [Problem("revert.verify.failed", f"verification failed for restored {self.path}", path=self.path)]


def _unit_snapshot(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("unit observation must be a mapping")
    target = observed.get("target")
    if not isinstance(target, Mapping):
        raise ValueError("unit observation has no target image")
    exists = bool(target.get("exists"))
    enabled = observed.get("enabled")
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("unit observation has an invalid enabled state")
    record: dict[str, Any] = {
        "path": change.resource.path,
        "exists": exists,
        "enabled": enabled,
        "sha256": target.get("sha256") if exists else None,
        "mode": target.get("mode") if exists else None,
        "uid": target.get("uid") if exists else None,
        "gid": target.get("gid") if exists else None,
    }
    if exists:
        record["data_b64"] = target.get("data_b64")
    return record


def _id_value(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _unit_restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> _RestoredUnit:
    path = address.removeprefix("unit:")
    if not path.startswith(_SYSTEMD_UNIT_DIR + "/") or address != _unit_address(path):
        raise ValueError(f"pre-image path does not match {address}")
    remainder = path.removeprefix(_SYSTEMD_UNIT_DIR + "/")
    if "/" in remainder:
        name, _separator, dropin = remainder.partition(".d/")
        if not _separator or "/" in dropin:
            raise ValueError(f"pre-image path is not a unit or drop-in: {path}")
        _dropin_name(dropin)
    else:
        name, dropin = remainder, None
    _unit_name(name)
    if record.get("path") != path:
        raise ValueError(f"pre-image path does not match {address}")
    exists = record.get("exists")
    if type(exists) is not bool:
        raise ValueError("pre-image exists flag must be a boolean")
    enabled = record.get("enabled")
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("pre-image enabled state must be a boolean or null")
    if not exists:
        image = {"exists": False, "enabled": enabled, "fingerprint": "absent"}
    else:
        mode, uid, gid = record.get("mode"), record.get("uid"), record.get("gid")
        if not _id_value(mode) or mode > 0o7777 or not _id_value(uid) or not _id_value(gid):
            raise ValueError("invalid metadata in pre-image")
        image = {
            "exists": True,
            "enabled": enabled,
            "sha256": record.get("sha256"),
            "data_b64": record.get("data_b64"),
            "mode": mode,
            "uid": uid,
            "gid": gid,
        }
        data = _decode_value(image)
        if hashlib.sha256(data).hexdigest() != image["sha256"]:
            raise ValueError("pre-image digest does not match its bytes")
        image["fingerprint"] = _image_fingerprint(image)
    return _RestoredUnit(path, image, name, dropin, actions)


def _parse_unit(table: Mapping[str, Any]) -> Unit:
    allowed = {"name", "state", "text", "mode", "owner", "group", "dropin", "intent", "enable", "on_change", "preflight"}
    require_keys(table, allowed=allowed, required={"name", "state"}, where="unit")
    state = table["state"]
    dropin = table.get("dropin")
    if dropin is None:
        required = {"name", "state", "text", "mode", "intent", "enable", "on_change"} if state == "present" else {"name", "state"}
    else:
        required = (
            {"name", "state", "dropin", "text", "mode", "intent", "on_change"}
            if state == "present"
            else {"name", "state", "dropin", "on_change"}
        )
    require_keys(table, allowed=allowed, required=required, where="unit")
    preflight = table.get("preflight")
    if isinstance(preflight, list):
        preflight = tuple(preflight)
    return Unit(
        name=table["name"],
        state=state,
        text=table.get("text"),
        mode=table.get("mode"),
        owner=table.get("owner"),
        group=table.get("group"),
        dropin=dropin,
        intent=table.get("intent"),
        enable=table.get("enable"),
        on_change=table.get("on_change"),
        preflight=preflight,
    )


def _unit_values(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, Unit):
        raise ValueError(f"{type(resource).__name__} has no unit TOML form")
    values: dict[str, Any] = {"name": resource.name, "state": resource.state}
    for key in ("text", "mode", "owner", "group", "dropin", "intent", "enable", "on_change", "preflight"):
        value = getattr(resource, key)
        if value is not None:
            values[key] = value
    return values


def _render_unit(change: Change, plan_value: Any) -> list[str]:
    before = plan_value.observed.get(change.address, {}) if isinstance(plan_value.observed, Mapping) else {}
    old_target = before.get("target", {}) if isinstance(before, Mapping) else {}
    old_bytes = _decode_value(old_target) if isinstance(old_target, Mapping) and old_target.get("exists") else b""
    after_target = change.after.get("target", {}) if isinstance(change.after, Mapping) else {}
    if isinstance(change.resource, Unit):
        new_bytes = change.resource.text.encode("utf-8") if change.resource.state == "present" and change.resource.text is not None else b""
    elif after_target.get("exists"):
        new_bytes = _decode_value(change.resource.image)
    else:
        new_bytes = b""
    path = change.resource.path
    rows = difflib.unified_diff(
        old_bytes.decode("utf-8", errors="replace").splitlines(True),
        new_bytes.decode("utf-8", errors="replace").splitlines(True),
        fromfile=f"a{path}",
        tofile=f"b{path}",
    )
    return [row.rstrip("\n") for row in rows]


def shadow_check(resource: Unit, root: Path) -> list[Problem]:
    """No-op hook for the shared HC-1b-s layered-unit shadow analysis."""

    del resource, root
    return []


register_kind(
    KindSpec(
        kind="unit",
        parse=_parse_unit,
        to_toml=_unit_values,
        render=_render_unit,
        revert=RevertHooks(snapshot=_unit_snapshot, restore=_unit_restore),
        shadow_check=shadow_check,
    )
)


__all__ = ["Unit", "shadow_check"]
