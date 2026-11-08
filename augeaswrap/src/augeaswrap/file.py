"""Whole-file resource used by the guarded applier."""

from __future__ import annotations

import base64
import grp
import hashlib
import json
import os
import pwd
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _fs

_ALLOWED_DIRS = ("/etc", "/root", "/home", "/usr/local/etc", "/usr/local/lib/systemd")
_STAGE_PREFIX = ".augeaswrap-stage-"
_FILE_PREFIX = ".augeaswrap-file-"
_JSON_PREFIX = ".augeaswrap-json-"
_BUILTIN_VALIDATORS = frozenset({"json", "toml", "udev", "sudoers", "sshd"})


def _mode_value(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("mode must be an octal string or integer")
    if isinstance(value, int):
        mode = value
    elif isinstance(value, str) and len(value) in {3, 4} and all(char in "01234567" for char in value):
        mode = int(value, 8)
    else:
        raise ValueError("mode must be an octal string or integer")
    if mode < 0 or mode > 0o7777:
        raise ValueError("mode must be between 0000 and 7777")
    return mode


def _name_or_id(value: object, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} must be a name or numeric id")
    if isinstance(value, str) and (not value or "\x00" in value):
        raise ValueError(f"{label} must be a non-empty name")
    if isinstance(value, int) and value < 0:
        raise ValueError(f"{label} id must be non-negative")


def _validate_spec(value: object) -> str | tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) and value in _BUILTIN_VALIDATORS:
        return value
    if isinstance(value, (tuple, list)) and value and value[0] == "cmd":
        command = tuple(value[1:])
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("validate command must contain non-empty argv values")
        if sum(item.count("%s") for item in command) > 1:
            raise ValueError("validate command may contain at most one %s placeholder")
        return ("cmd", *command)
    raise ValueError("validate must be json, toml, udev, sudoers, sshd, or ['cmd', ...]")


def _path_value(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ValueError("file path must be an absolute path")
    path = PurePosixPath(value)
    if path == PurePosixPath("/") or any(part in {".", ".."} for part in path.parts):
        raise ValueError("file path must name a file without dot components")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("file path must be normalized")
    if not any(normalized.startswith(prefix + "/") for prefix in _ALLOWED_DIRS):
        raise ValueError(
            "file path must be below an allowed configuration directory: "
            + ", ".join(_ALLOWED_DIRS)
        )
    return normalized


def _resolve_id(value: str | int | None, *, group: bool) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return grp.getgrnam(value).gr_gid if group else pwd.getpwnam(value).pw_uid
    except KeyError as exc:
        kind = "group" if group else "user"
        raise ValueError(f"unknown {kind} {value!r}") from exc


def _image_fingerprint(value: Mapping[str, Any]) -> str:
    if not value.get("exists"):
        return "absent"
    payload = {
        "sha256": value.get("sha256"),
        "mode": value.get("mode"),
        "uid": value.get("uid"),
        "gid": value.get("gid"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot(root: Path, absolute: str) -> dict[str, Any]:
    target = root / absolute.lstrip("/")
    info = _fs.regular_or_missing(root, target)
    if info is None:
        return {"exists": False, "fingerprint": "absent"}
    data = target.read_bytes()
    value = {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }
    value["fingerprint"] = _image_fingerprint(value)
    return value


def _public_image(value: Mapping[str, Any]) -> dict[str, Any]:
    if not value.get("exists"):
        return {"exists": False}
    return {
        "exists": True,
        "sha256": value.get("sha256"),
        "mode": value.get("mode"),
        "uid": value.get("uid"),
        "gid": value.get("gid"),
    }


def _decode_value(value: Mapping[str, Any]) -> bytes:
    encoded = value.get("data_b64")
    if not isinstance(encoded, str):
        raise ValueError("stored file value has no content")
    return base64.b64decode(encoded.encode("ascii"), validate=True)


def _validate_json(data: bytes) -> None:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs)


def _run_validator(argv: tuple[str, ...], staged: Path) -> None:
    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"validator failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"validator exited {result.returncode}: {detail or 'no diagnostic'}")


def _builtin_argv(spec: str, staged: Path) -> tuple[str, ...]:
    commands = {
        "udev": ("udevadm", "verify", str(staged)),
        "sudoers": ("visudo", "-cf", str(staged)),
        "sshd": ("sshd", "-t", "-f", str(staged)),
    }
    return commands[spec]


@dataclass(frozen=True)
class _StagedFile:
    path: str
    exists: bool
    data: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None
    expected: dict[str, Any]


@dataclass(frozen=True)
class File:
    """A whole file or drop-in beneath the managed configuration allowlist."""

    path: str
    state: str
    text: str | None = None
    mode: str | int | None = None
    owner: str | int | None = None
    group: str | int | None = None
    create_only: bool = False
    validate: str | tuple[str, ...] | list[str] | None = None
    kind: ClassVar[str] = "file"

    def __post_init__(self) -> None:
        _path_value(self.path)
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("file state must be 'present' or 'absent'")
        if type(self.create_only) is not bool:
            raise ValueError("create_only must be a boolean")
        _name_or_id(self.owner, "owner")
        _name_or_id(self.group, "group")
        validator = _validate_spec(self.validate)
        if validator is not None:
            object.__setattr__(self, "validate", validator)
        if self.state == "present":
            if not isinstance(self.text, str):
                raise ValueError("file text is required when state is present")
            if self.mode is None:
                raise ValueError("file mode is required when state is present")
            _mode_value(self.mode)
        else:
            if any(value is not None for value in (self.text, self.mode, self.owner, self.group)):
                raise ValueError("absent file must not specify text, mode, owner, or group")
            if self.create_only:
                raise ValueError("create_only cannot be used with absent state")
            if validator is not None:
                raise ValueError("validate cannot be used with absent state")

    def addresses(self) -> tuple[str, ...]:
        return (f"file:{self.path}",)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        root = Path(getattr(ctx, "root"))
        return {self.addresses()[0]: _snapshot(root, self.path)}

    def _desired(self, observed: Mapping[str, Any]) -> dict[str, Any]:
        if self.state == "absent":
            return {"exists": False}
        assert self.text is not None and self.mode is not None
        data = self.text.encode("utf-8")
        try:
            uid = _resolve_id(self.owner, group=False)
            gid = _resolve_id(self.group, group=True)
        except ValueError as exc:
            raise PlanRefused([Problem("file.owner.invalid", str(exc), path=self.path)]) from exc
        if observed.get("exists"):
            uid = int(observed["uid"]) if uid is None else uid
            gid = int(observed["gid"]) if gid is None else gid
        else:
            uid = os.geteuid() if uid is None else uid
            gid = os.getegid() if gid is None else gid
        value: dict[str, Any] = {
            "exists": True,
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": _mode_value(self.mode),
            "uid": uid,
            "gid": gid,
        }
        value["fingerprint"] = _image_fingerprint(value)
        return value

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        address = self.addresses()[0]
        current = observed[address]
        if not isinstance(current, Mapping):
            raise ValueError("file observation must be a mapping")
        desired = self._desired(current)
        before = _public_image(current)
        after = _public_image(desired)
        if self.create_only and current.get("exists"):
            return [Change(self.kind, address, "noop", before, after, self, ("create_only", "preserved-existing-file"))]
        if self.state == "absent":
            action = "noop" if not current.get("exists") else "change"
        else:
            action = "noop" if _image_fingerprint(current) == desired["fingerprint"] else "change"
        details = ("state=absent",) if self.state == "absent" and action == "change" else ()
        return [Change(self.kind, address, action, before, after, self, details)]

    def stage(self, ctx: object, change: Change) -> _StagedFile:
        if not change.after.get("exists"):
            return _StagedFile("", False, None, None, None, None, {"exists": False})
        assert self.text is not None
        data = self.text.encode("utf-8")
        mode = int(change.after["mode"])
        uid = int(change.after["uid"])
        gid = int(change.after["gid"])
        root = Path(getattr(ctx, "root"))
        stage_dir = Path(getattr(ctx, "stage_dir"))
        address_hash = hashlib.sha256(change.address.encode("utf-8")).hexdigest()
        directory = stage_dir / address_hash
        if directory.is_relative_to(root):
            _fs.ensure_directory(root, directory, mode=0o700)
        else:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged_path = directory / Path(self.path).name
        if staged_path.is_relative_to(root):
            _fs.atomic_write(root, staged_path, data, mode, temp_prefix=_STAGE_PREFIX)
        else:
            descriptor = os.open(staged_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            try:
                os.fchmod(descriptor, mode)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
            except BaseException:
                staged_path.unlink(missing_ok=True)
                raise
        try:
            self._validate(staged_path, data)
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PlanRefused([Problem("file.validate.failed", f"validation failed for {self.path}: {exc}", path=self.path)]) from exc
        return _StagedFile(str(staged_path), True, data, mode, uid, gid, dict(change.after))

    def _validate(self, staged_path: Path, data: bytes) -> None:
        spec = _validate_spec(self.validate)
        if spec is None:
            return
        if spec == "json":
            _validate_json(data)
            return
        if spec == "toml":
            tomllib.loads(data.decode("utf-8"))
            return
        if isinstance(spec, str):
            argv = _builtin_argv(spec, staged_path)
        else:
            argv = tuple(item.replace("%s", str(staged_path)) for item in spec[1:])
            if not any("%s" in item for item in spec[1:]):
                argv = (*argv, str(staged_path))
        _run_validator(argv, staged_path)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedFile):
            raise TypeError("file commit requires its staged image")
        root = Path(getattr(ctx, "root"))
        target = root / self.path.lstrip("/")
        if not staged.exists:
            _fs.unlink_and_sync(root, target)
            return _snapshot(root, self.path)
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
        return _snapshot(root, self.path)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return []

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedFile):
            return [Problem("file.verify.expected", f"no staged image recorded for {self.path}", path=self.path)]
        actual = _snapshot(Path(getattr(ctx, "root")), self.path)
        if _image_fingerprint(actual) == _image_fingerprint(expected.expected):
            return []
        return [Problem("file.verify.failed", f"verification failed for {self.path}", path=self.path)]


__all__ = ["File"]
