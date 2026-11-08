"""Systemd unit files and drop-ins used by the guarded applier."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
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
_EXEC_PREFIXES = "-@:+!"
_UNSAFE_EXEC_PREFIXES = ("/tmp", "/var/tmp", "/home")
_ON_CHANGE = frozenset({"restart", "reload", "none", "pending"})
_STAGE_PREFIX = ".augeaswrap-unit-"
_FILE_PREFIX = ".augeaswrap-unit-file-"
_COMMAND_TIMEOUT = 30.0


class _UnitCommandError(RuntimeError):
    """A command guard or live systemd action failed."""


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
        raise _UnitCommandError(f"exit {result.returncode}: {diagnostic}")
    return result


def _is_live_root(root: Path) -> bool:
    """Only a live root may execute pre-removal systemd commands."""

    return root == Path("/")


def _unit_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("unit name must be a non-empty string")
    if "/" in value or "\x00" in value:
        raise ValueError("unit name must not contain a path separator or NUL")
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
            "exec_paths": value.get("exec_paths"),
            "sources": value.get("sources"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _inspect_exec(root: Path, command: str, unit_path: str) -> dict[str, Any]:
    for prefix in _UNSAFE_EXEC_PREFIXES:
        if command == prefix or command.startswith(prefix + "/"):
            raise _exec_refusal(
                "unit.exec.unsafe_path",
                f"refusing executable below {prefix}: {command}",
                unit_path,
            )

    relative = PurePosixPath(command).parts[1:]
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
        if mode & 0o022:
            raise _exec_refusal(
                "unit.exec.writable",
                f"executable path has a group/other-writable component: {command}",
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
    if final.st_uid != 0:
        raise _exec_refusal(
            "unit.exec.owner",
            f"executable is not root-owned: {command}",
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


def _source_file(root: Path, absolute: str) -> dict[str, Any] | None:
    target = root / absolute.lstrip("/")
    info = _fs.regular_or_missing(root, target)
    if info is None:
        return None
    data = target.read_bytes()
    return {
        "path": absolute,
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def _source_inventory(root: Path, name: str, path: str, dropin: str | None) -> list[dict[str, Any]]:
    """Filesystem inputs a staged verify tree copies from the managed root."""

    values: list[dict[str, Any]] = []
    if dropin is not None:
        base = _source_file(root, f"{_SYSTEMD_UNIT_DIR}/{name}")
        if base is not None:
            values.append(base)
    dropin_dir = root / f"{_SYSTEMD_UNIT_DIR}/{name}.d".lstrip("/")
    try:
        _fs.safe_components(root, dropin_dir)
        entries = sorted(dropin_dir.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        entries = []
    for entry in entries:
        absolute = f"{_SYSTEMD_UNIT_DIR}/{name}.d/{entry.name}"
        if entry.suffix != ".conf" or absolute == path:
            continue
        source = _source_file(root, absolute)
        if source is not None:
            values.append(source)
    return values


def _observe_unit(root: Path, path: str, name: str, dropin: str | None, text: str | None) -> dict[str, Any]:
    target = _snapshot(root, path)
    exec_paths = [] if text is None else _exec_observations(root, text, path)
    value = {
        "path": path,
        "target": target,
        "exec_paths": exec_paths,
        "sources": _source_inventory(root, name, path, dropin),
    }
    value["fingerprint"] = _combined_fingerprint(value)
    return value


def _public_unit(value: Mapping[str, Any]) -> dict[str, Any]:
    target = value.get("target")
    assert isinstance(target, Mapping)
    return {
        "path": value.get("path"),
        "target": _public_image(target),
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
    for source in sources:
        absolute = source["path"]
        data = (root / absolute.lstrip("/")).read_bytes()
        target = stage_root / absolute.lstrip("/")
        _stage_write(stage_root, target, data, int(source["mode"]))


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
) -> None:
    hash_dir = stage_dir / hashlib.sha256(_unit_address(path).encode("utf-8")).hexdigest()
    stage_root = hash_dir / "root"
    _ensure_stage_directory(root, stage_root, 0o700)
    base_absolute = f"{_SYSTEMD_UNIT_DIR}/{name}"
    sources = _source_inventory(root, name, path, dropin)
    dropin_unit_present = any(source["path"] == base_absolute for source in sources)
    _copy_sources(root, stage_root, sources)
    _stage_write(stage_root, stage_root / path.lstrip("/"), data, mode)

    if dropin is None or dropin_unit_present:
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
        if self.on_change is not None and self.on_change not in _ON_CHANGE:
            raise ValueError("unit on_change must be restart, reload, none, or pending")
        preflight = _preflight(self.preflight)
        if preflight is not None:
            object.__setattr__(self, "preflight", preflight)
        if self.dropin is None:
            if self.state == "present":
                if not isinstance(self.text, str):
                    raise ValueError("unit text is required when state is present")
                if self.mode is None:
                    raise ValueError("unit mode is required when state is present")
                _mode_value(self.mode)
                if type(self.enable) is not bool:
                    raise ValueError("unit enable is required for a present unit")
                if self.on_change is None:
                    raise ValueError("unit on_change is required for a present unit")
            else:
                if any(value is not None for value in (self.text, self.mode, self.owner, self.group)):
                    raise ValueError("absent unit must not specify text, mode, owner, or group")
                if self.enable is not None or self.on_change is not None or preflight is not None:
                    raise ValueError("absent unit must not specify enable, on_change, or preflight")
        else:
            if self.enable is not None:
                raise ValueError("enable applies only to a whole unit, not a drop-in")
            if self.state == "present":
                if not isinstance(self.text, str):
                    raise ValueError("drop-in text is required when state is present")
                if self.mode is None:
                    raise ValueError("drop-in mode is required when state is present")
                _mode_value(self.mode)
                if self.on_change is None:
                    raise ValueError("drop-in on_change is required when state is present")
            else:
                if any(value is not None for value in (self.text, self.mode, self.owner, self.group)):
                    raise ValueError("absent drop-in must not specify text, mode, owner, or group")
                if self.on_change is None or preflight is not None:
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
        if self.state == "absent":
            target = {"exists": False, "fingerprint": "absent"}
            exec_paths: list[dict[str, Any]] = []
        else:
            assert self.text is not None and self.mode is not None
            data = self.text.encode("utf-8")
            try:
                uid = _resolve_id(self.owner, group=False)
                gid = _resolve_id(self.group, group=True)
            except ValueError as exc:
                raise PlanRefused([Problem("unit.owner.invalid", str(exc), path=path)]) from exc
            before = current.get("target")
            if isinstance(before, Mapping) and before.get("exists"):
                uid = int(before["uid"]) if uid is None else uid
                gid = int(before["gid"]) if gid is None else gid
            else:
                uid = os.geteuid() if uid is None else uid
                gid = os.getegid() if gid is None else gid
            target = {
                "exists": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data_b64": None,
                "mode": _mode_value(self.mode),
                "uid": uid,
                "gid": gid,
            }
            target["fingerprint"] = _image_fingerprint(target)
            exec_paths = _exec_observations(Path(getattr(ctx, "root")), self.text, path)
        value = {
            "path": path,
            "target": target,
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
                details = (
                    "state=absent",
                    "stop before delete",
                    "daemon-reload then reset-failed",
                )
            elif self.on_change == "pending":
                details = ("runtime change recorded pending",)
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
        if not staged.exists:
            if staged.full_unit and _is_live_root(root):
                _run_command(("systemctl", "stop", self.name), _COMMAND_TIMEOUT)
                _run_command(("systemctl", "disable", self.name), _COMMAND_TIMEOUT)
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
        return self.observe(ctx)[self.addresses()[0]]

    def _runtime_action(self) -> PostAction | None:
        if self.on_change in {None, "none"}:
            return None
        assert self.on_change is not None
        verb = "restart" if self.on_change in {"restart", "pending"} else "reload"
        return PostAction(
            f"unit {verb} {self.name}",
            ("systemctl", verb, self.name),
            _COMMAND_TIMEOUT,
            pending=self.on_change == "pending",
        )

    def _runtime_change_applies(self, changes: list[Change]) -> bool:
        """A socket restarts only for a text transition, not metadata-only churn."""

        if not self.name.endswith(".socket"):
            return True
        for change in changes:
            before = change.before.get("target", {}) if isinstance(change.before, Mapping) else {}
            after = change.after.get("target", {}) if isinstance(change.after, Mapping) else {}
            before_sha = before.get("sha256") if isinstance(before, Mapping) and before.get("exists") else None
            after_sha = after.get("sha256") if isinstance(after, Mapping) and after.get("exists") else None
            if before_sha != after_sha:
                return True
        return False

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        if not any(change.action == "change" for change in changes):
            return []
        actions = [
            PostAction("daemon-reload", ("systemctl", "daemon-reload"), _COMMAND_TIMEOUT),
        ]
        if self.dropin is None and self.state == "present":
            if self.enable:
                actions.append(
                    PostAction(
                        f"unit enable {self.name}",
                        ("systemctl", "enable", self.name),
                        _COMMAND_TIMEOUT,
                    )
                )
        if self.state == "present" and self._runtime_change_applies(changes):
            runtime = self._runtime_action()
            if runtime is not None:
                actions.append(runtime)
        elif self.dropin is None:
            actions.append(
                PostAction(
                    f"unit reset-failed {self.name}",
                    ("systemctl", "reset-failed", self.name),
                    _COMMAND_TIMEOUT,
                )
            )
        else:
            if self._runtime_change_applies(changes):
                runtime = self._runtime_action()
                assert runtime is not None
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
        if actual.get("fingerprint") == expected.expected.get("fingerprint"):
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
        target = _snapshot(root, self.path)
        text = None if not target.get("exists") else _decode_value(target).decode("utf-8")
        return {
            self.addresses()[0]: _observe_unit(root, self.path, self.name, self.dropin, text)
        }

    def _desired(self, ctx: object, current: Mapping[str, Any]) -> dict[str, Any]:
        if not self.image.get("exists"):
            value = {
                "path": self.path,
                "target": {"exists": False, "fingerprint": "absent"},
                "exec_paths": [],
                "sources": current.get("sources", []),
            }
            value["fingerprint"] = _combined_fingerprint(value)
            return value
        data = _decode_value(self.image)
        text = data.decode("utf-8")
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
            "exec_paths": _exec_observations(Path(getattr(ctx, "root")), text, self.path),
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
        return self.observe(ctx)[self.addresses()[0]]

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return list(self.actions)

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedUnit):
            return [Problem("revert.verify.expected", f"no staged pre-image for {self.path}", path=self.path)]
        try:
            actual = self.observe(ctx)[address]
        except (OSError, _fs.PathRefusal, PlanRefused) as exc:
            return [Problem("revert.verify.failed", f"cannot verify restored {self.path}: {exc}", path=self.path)]
        if actual.get("fingerprint") == expected.expected.get("fingerprint"):
            return []
        return [Problem("revert.verify.failed", f"verification failed for restored {self.path}", path=self.path)]


def _unit_snapshot(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("unit observation must be a mapping")
    target = observed.get("target")
    if not isinstance(target, Mapping):
        raise ValueError("unit observation has no target image")
    exists = bool(target.get("exists"))
    record: dict[str, Any] = {
        "path": change.resource.path,
        "exists": exists,
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
    if not exists:
        image = {"exists": False, "fingerprint": "absent"}
    else:
        mode, uid, gid = record.get("mode"), record.get("uid"), record.get("gid")
        if not _id_value(mode) or mode > 0o7777 or not _id_value(uid) or not _id_value(gid):
            raise ValueError("invalid metadata in pre-image")
        image = {
            "exists": True,
            "sha256": record.get("sha256"),
            "data_b64": record.get("data_b64"),
            "mode": mode,
            "uid": uid,
            "gid": gid,
        }
        data = _decode_value(image)
        if hashlib.sha256(data).hexdigest() != image["sha256"]:
            raise ValueError("pre-image digest does not match its bytes")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("pre-image unit text is not UTF-8") from exc
        image["fingerprint"] = _image_fingerprint(image)
    return _RestoredUnit(path, image, name, dropin, actions)


def _parse_unit(table: Mapping[str, Any]) -> Unit:
    allowed = {"name", "state", "text", "mode", "owner", "group", "dropin", "enable", "on_change", "preflight"}
    require_keys(table, allowed=allowed, required={"name", "state"}, where="unit")
    state = table["state"]
    dropin = table.get("dropin")
    if dropin is None:
        required = {"name", "state", "text", "mode", "enable", "on_change"} if state == "present" else {"name", "state"}
    else:
        required = (
            {"name", "state", "dropin", "text", "mode", "on_change"}
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
        enable=table.get("enable"),
        on_change=table.get("on_change"),
        preflight=preflight,
    )


def _unit_values(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, Unit):
        raise ValueError(f"{type(resource).__name__} has no unit TOML form")
    values: dict[str, Any] = {"name": resource.name, "state": resource.state}
    for key in ("text", "mode", "owner", "group", "dropin", "enable", "on_change", "preflight"):
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


register_kind(
    KindSpec(
        kind="unit",
        parse=_parse_unit,
        to_toml=_unit_values,
        render=_render_unit,
        revert=RevertHooks(snapshot=_unit_snapshot, restore=_unit_restore),
    )
)

def shadow_check(*args: object, **kwargs: object) -> list[Problem]:
    """Reserved seam for directive-level shadow checks added by HC-1b-s.

    The unit kind deliberately performs no shadow validation yet. The shared
    core will be connected here after its registry API lands.
    """

    del args, kwargs
    return []


__all__ = ["Unit", "shadow_check"]
