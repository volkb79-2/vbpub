"""Guarded JSON object-key updates with whole-file revert images."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _fs, shadow
from .file import _decode_value, _image_fingerprint, _path_value, _public_image, _snapshot
from .kinds import KindSpec, RevertHooks, register_kind, require_keys
from .unit import _unit_name

_STAGE_PREFIX = ".augeaswrap-json-key-stage-"
_FILE_PREFIX = ".augeaswrap-json-key-file-"
_MAX_TOML_INT = (1 << 63) - 1
_MIN_TOML_INT = -(1 << 63)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _toml_json_value(value: object, where: str = "value") -> object:
    """Validate and copy the JSON values expressible in strict TOML requests."""

    if isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not _MIN_TOML_INT <= value <= _MAX_TOML_INT:
            raise ValueError(f"{where} integer is outside TOML's signed 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where} must be a finite JSON number")
        return value
    if isinstance(value, (list, tuple)):
        return [_toml_json_value(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where} object keys must be strings")
            result[key] = _toml_json_value(item, f"{where}.{key}")
        return result
    if value is None:
        raise ValueError(f"{where} cannot be null because TOML has no null value")
    raise ValueError(f"{where} must be a TOML-compatible JSON value, found {_json_type(value)}")


def _duplicate_refusing_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _parse_document(data: bytes, path: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_duplicate_refusing_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PlanRefused(
            [Problem("json_key.document.invalid", f"cannot parse JSON object {path}: {exc}", path=path)]
        ) from exc
    if not isinstance(value, dict):
        raise PlanRefused(
            [
                Problem(
                    "json_key.document.root",
                    f"JSON root in {path} must be an object, found {_json_type(value)}",
                    path=path,
                )
            ]
        )
    return value


def _apply_key(document: dict[str, Any], key: tuple[str, ...], state: str, value: object, path: str) -> bool:
    current: dict[str, Any] = document
    for index, segment in enumerate(key[:-1]):
        if segment not in current:
            if state == "absent":
                return False
            child: dict[str, Any] = {}
            current[segment] = child
            current = child
            continue
        child = current[segment]
        if not isinstance(child, dict):
            raise PlanRefused(
                [
                    Problem(
                        "json_key.path.parent",
                        f"JSON key path in {path} has a non-object intermediate at segment {index} ({segment!r}): {_json_type(child)}",
                        path=path,
                        key=".".join(key[: index + 1]),
                    )
                ]
            )
        current = child

    leaf = key[-1]
    if state == "absent":
        return current.pop(leaf, _MISSING) is not _MISSING
    normalized = _toml_json_value(value)
    current[leaf] = normalized
    return True


class _Missing:
    pass


_MISSING = _Missing()


def _canonical(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _image(data: bytes, *, mode: int, uid: int, gid: int) -> dict[str, Any]:
    value: dict[str, Any] = {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": mode,
        "uid": uid,
        "gid": gid,
    }
    value["fingerprint"] = _image_fingerprint(value)
    return value


def _desired(
    root: Path,
    resource: "JsonKey",
    observed: Mapping[str, Any] | None = None,
) -> tuple[bytes | None, dict[str, Any], tuple[str, ...]]:
    current = _snapshot(root, resource.file) if observed is None else observed
    if not current.get("exists") and resource.state == "absent":
        return None, {"exists": False, "fingerprint": "absent"}, ("key already absent",)

    source = _decode_value(current) if current.get("exists") else b"{}"
    document = _parse_document(source, resource.file)
    try:
        found = _apply_key(document, resource.key, resource.state, resource.value, resource.file)
    except PlanRefused:
        raise
    except ValueError as exc:
        raise PlanRefused([Problem("json_key.value.invalid", str(exc), path=resource.file)]) from exc
    if resource.state == "absent" and not found:
        return source, dict(current), ("key already absent",)
    data = _canonical(document)
    mode = int(current["mode"]) if current.get("exists") else 0o644
    uid = int(current["uid"]) if current.get("exists") else os.geteuid()
    gid = int(current["gid"]) if current.get("exists") else os.getegid()
    absent_note = ("key already absent",) if resource.state == "absent" and not found else ()
    return data, _image(data, mode=mode, uid=uid, gid=gid), absent_note


@dataclass(frozen=True)
class _StagedJsonKey:
    path: str
    data: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None
    expected: dict[str, Any]
    exists: bool = True


@dataclass(frozen=True)
class JsonKey:
    """Set or delete one nested key in a managed JSON object file."""

    file: str
    key: tuple[str, ...] | list[str]
    state: str
    value: object = None
    validate: str | None = None
    on_change: Mapping[str, str] | None = None
    kind: ClassVar[str] = "json_key"

    def __post_init__(self) -> None:
        _path_value(self.file)
        if not isinstance(self.key, (list, tuple)) or not self.key or any(not isinstance(item, str) for item in self.key):
            raise ValueError("json_key key must be a non-empty list of string segments")
        object.__setattr__(self, "key", tuple(self.key))
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("json_key state must be 'present' or 'absent'")
        if self.state == "present":
            if self.value is None:
                raise ValueError("json_key value is required when state is present")
            object.__setattr__(self, "value", _toml_json_value(self.value))
        elif self.value is not None:
            raise ValueError("json_key value cannot be used when state is absent")
        if self.validate is not None and self.validate != "dockerd":
            raise ValueError("json_key validate must be 'dockerd'")
        if not isinstance(self.on_change, Mapping):
            raise ValueError("json_key on_change is required as a table with unit and action")
        if set(self.on_change) != {"unit", "action"}:
            raise ValueError("json_key on_change must contain exactly unit and action")
        unit = _unit_name(self.on_change["unit"])
        action = self.on_change["action"]
        if not isinstance(action, str) or action not in {"restart", "pending", "none"}:
            raise ValueError("json_key on_change action must be 'restart', 'pending', or 'none'")
        object.__setattr__(self, "on_change", {"unit": unit, "action": action})

    def addresses(self) -> tuple[str, ...]:
        return (f"json_key:{self.file}",)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        return {self.addresses()[0]: _snapshot(Path(getattr(ctx, "root")), self.file)}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before_image = observed[address]
        if not isinstance(before_image, Mapping):
            raise ValueError("json_key observation must be a mapping")
        # Reconstruct from the selected root so the staged output is based on the
        # same bytes that were fingerprinted by observe and checked before apply.
        root = Path(getattr(ctx, "root"))
        data, after_image, details = _desired(root, self, before_image)
        if data is None:
            after = {"exists": False}
            action = "noop" if not before_image.get("exists") else "change"
        else:
            after = _public_image(after_image)
            action = "noop" if _image_fingerprint(before_image) == after_image["fingerprint"] else "change"
        return [Change(self.kind, address, action, _public_image(before_image), after, self, details)]

    def stage(self, ctx: object, change: Change) -> _StagedJsonKey:
        root = Path(getattr(ctx, "root"))
        data, expected, _details = _desired(root, self)
        if data is None:
            raise ValueError("cannot stage an absent json_key file")
        if expected["fingerprint"] != _image_fingerprint(change.after):
            raise ValueError(f"JSON source changed while staging {self.file}")
        stage_dir = Path(getattr(ctx, "stage_dir"))
        name_hash = hashlib.sha256(change.address.encode("utf-8")).hexdigest()
        directory = stage_dir / name_hash
        if directory.is_relative_to(root):
            _fs.ensure_directory(root, directory, mode=0o700)
        else:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged_path = directory / Path(self.file).name
        if staged_path.is_relative_to(root):
            _fs.atomic_write(root, staged_path, data, int(expected["mode"]), temp_prefix=_STAGE_PREFIX)
        else:
            descriptor = os.open(staged_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, int(expected["mode"]))
            try:
                os.fchmod(descriptor, int(expected["mode"]))
                stream = os.fdopen(descriptor, "wb")
                descriptor = -1
                with stream:
                    stream.write(data)
                    stream.flush()
            except BaseException:
                if descriptor >= 0:
                    os.close(descriptor)
                staged_path.unlink(missing_ok=True)
                raise
        if self.validate == "dockerd":
            argv = ("dockerd", "--validate", "--config-file", str(staged_path))
            try:
                result = shadow.run_command(argv, timeout=30.0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PlanRefused(
                    [Problem("json_key.validate.failed", f"cannot run dockerd validation for {self.file}: {exc}", path=self.file)]
                ) from exc
            message = shadow.command_error(argv, result)
            if message is not None:
                raise PlanRefused([Problem("json_key.validate.failed", message, path=self.file)])
        return _StagedJsonKey(
            str(staged_path), data, int(expected["mode"]), int(expected["uid"]), int(expected["gid"]), dict(expected)
        )

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedJsonKey):
            raise TypeError("json_key commit requires its staged image")
        root = Path(getattr(ctx, "root"))
        target = root / self.file.lstrip("/")
        if staged.data is None or staged.mode is None or staged.uid is None or staged.gid is None:
            raise ValueError("present json_key image is incomplete")
        _fs.atomic_write(
            root,
            target,
            staged.data,
            staged.mode,
            temp_prefix=_FILE_PREFIX,
            owner=(staged.uid, staged.gid),
        )
        return _snapshot(root, self.file)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        if not any(change.action == "change" for change in changes):
            return []
        assert self.on_change is not None
        action = self.on_change["action"]
        if action == "none":
            return []
        unit = self.on_change["unit"]
        return [
            PostAction(
                f"json_key restart {unit}",
                ("systemctl", "restart", "--", unit),
                30.0,
                pending=action == "pending",
                phase="runtime",
            )
        ]

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedJsonKey):
            return [Problem("json_key.verify.expected", f"no staged JSON image for {self.file}", path=self.file)]
        actual = _snapshot(Path(getattr(ctx, "root")), self.file)
        if _image_fingerprint(actual) == _image_fingerprint(expected.expected):
            return []
        return [Problem("json_key.verify.failed", f"verification failed for {self.file}", path=self.file)]


@dataclass(frozen=True)
class _RestoredJsonKey:
    file: str
    image: Mapping[str, Any]
    actions: tuple[PostAction, ...] = ()
    kind: str = "json_key"

    def addresses(self) -> tuple[str, ...]:
        return (f"json_key:{self.file}",)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        return {self.addresses()[0]: _snapshot(Path(getattr(ctx, "root")), self.file)}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        address = self.addresses()[0]
        current = observed[address]
        if not isinstance(current, Mapping):
            raise ValueError("json_key restore observation must be a mapping")
        action = "noop" if _image_fingerprint(current) == _image_fingerprint(self.image) else "change"
        return [Change(self.kind, address, action, _public_image(current), _public_image(self.image), self)]

    def stage(self, ctx: object, change: Change) -> _StagedJsonKey:
        del change
        root = Path(getattr(ctx, "root"))
        if not self.image.get("exists"):
            return _StagedJsonKey("", None, None, None, None, dict(self.image), exists=False)
        data = _decode_value(self.image)
        mode, uid, gid = int(self.image["mode"]), int(self.image["uid"]), int(self.image["gid"])
        expected = dict(self.image)
        stage_dir = Path(getattr(ctx, "stage_dir"))
        name_hash = hashlib.sha256(self.addresses()[0].encode("utf-8")).hexdigest()
        target = stage_dir / name_hash / Path(self.file).name
        if target.is_relative_to(root):
            _fs.atomic_write(root, target, data, mode, temp_prefix=_STAGE_PREFIX)
        else:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, mode)
        return _StagedJsonKey(str(target), data, mode, uid, gid, expected)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedJsonKey):
            raise TypeError("json_key restore commit requires its staged image")
        root = Path(getattr(ctx, "root"))
        target = root / self.file.lstrip("/")
        if not staged.exists:
            _fs.unlink_and_sync(root, target)
        else:
            if staged.data is None or staged.mode is None or staged.uid is None or staged.gid is None:
                raise ValueError("present json_key restore image is incomplete")
            _fs.atomic_write(
                root,
                target,
                staged.data,
                staged.mode,
                temp_prefix=_FILE_PREFIX,
                owner=(staged.uid, staged.gid),
            )
        return _snapshot(root, self.file)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return list(self.actions)

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        actual = _snapshot(Path(getattr(ctx, "root")), self.file)
        if _image_fingerprint(actual) == _image_fingerprint(self.image):
            return []
        return [Problem("revert.verify.failed", f"verification failed for restored {self.file}", path=self.file)]


def _snapshot_json_key(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("json_key observation must be a mapping")
    exists = bool(observed.get("exists"))
    record: dict[str, Any] = {
        "file": change.resource.file,
        "exists": exists,
        "sha256": observed.get("sha256") if exists else None,
        "mode": observed.get("mode") if exists else None,
        "uid": observed.get("uid") if exists else None,
        "gid": observed.get("gid") if exists else None,
    }
    if exists:
        record["data_b64"] = observed.get("data_b64")
    return record


def _valid_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _restore_json_key(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> _RestoredJsonKey:
    if not address.startswith("json_key:"):
        raise ValueError(f"pre-image address does not match {address}")
    file = _path_value(address.removeprefix("json_key:"))
    if address != f"json_key:{file}" or record.get("file") != file:
        raise ValueError(f"pre-image file does not match {address}")
    exists = record.get("exists")
    if not isinstance(exists, bool):
        raise ValueError("pre-image exists flag must be a boolean")
    if not exists:
        return _RestoredJsonKey(file, {"exists": False, "fingerprint": "absent"}, actions)
    mode, uid, gid = record.get("mode"), record.get("uid"), record.get("gid")
    if not _valid_id(mode) or mode > 0o7777 or not _valid_id(uid) or not _valid_id(gid):
        raise ValueError("invalid metadata in pre-image")
    image: dict[str, Any] = {
        "exists": True,
        "sha256": record.get("sha256"),
        "data_b64": record.get("data_b64"),
        "mode": mode,
        "uid": uid,
        "gid": gid,
    }
    data = _decode_value(image)
    if hashlib.sha256(data).hexdigest() != image["sha256"]:
        raise ValueError("pre-image bytes do not match their digest")
    image["fingerprint"] = _image_fingerprint(image)
    return _RestoredJsonKey(file, image, actions)


def _parse_json_key(table: Mapping[str, Any]) -> JsonKey:
    allowed = {"file", "key", "state", "value", "validate", "on_change"}
    require_keys(table, allowed=allowed, required={"file", "key", "state", "on_change"}, where="json_key")
    state = table["state"]
    if state == "present":
        require_keys(table, allowed=allowed, required={"file", "key", "state", "value", "on_change"}, where="json_key")
    validate = table.get("validate")
    on_change = table["on_change"]
    if not isinstance(on_change, Mapping):
        raise ValueError("json_key on_change must be a table")
    return JsonKey(
        file=table["file"],
        key=table["key"],
        state=state,
        value=table.get("value"),
        validate=validate,
        on_change=on_change,
    )


def _json_key_values(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, JsonKey):
        raise ValueError(f"{type(resource).__name__} has no TOML form")
    values: dict[str, Any] = {
        "file": resource.file,
        "key": list(resource.key),
        "state": resource.state,
        "on_change": dict(resource.on_change or {}),
    }
    if resource.state == "present":
        values["value"] = resource.value
    if resource.validate is not None:
        values["validate"] = resource.validate
    return values


def _render_json_key(change: Change, plan_value: Any) -> list[str]:
    before = plan_value.observed.get(change.address, {}) if isinstance(plan_value.observed, Mapping) else {}
    old = _decode_value(before) if isinstance(before, Mapping) and before.get("exists") else b""
    if change.action == "change":
        root = Path(plan_value.root)
        before = plan_value.observed.get(change.address, {}) if isinstance(plan_value.observed, Mapping) else {}
        data, _after, _details = _desired(root, change.resource, before)
        new = data or b""
    else:
        new = old
    diff = difflib.unified_diff(
        old.decode("utf-8", errors="replace").splitlines(True),
        new.decode("utf-8", errors="replace").splitlines(True),
        fromfile=f"a{change.resource.file}",
        tofile=f"b{change.resource.file}",
    )
    return [row.rstrip("\n") for row in diff]


register_kind(
    KindSpec(
        kind="json_key",
        parse=_parse_json_key,
        to_toml=_json_key_values,
        render=_render_json_key,
        revert=RevertHooks(snapshot=_snapshot_json_key, restore=_restore_json_key),
    )
)

__all__ = ["JsonKey"]
