"""Augeas-backed in-place edits, staged and committed as whole-file images."""

from __future__ import annotations

import base64
import difflib
import hashlib
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from . import Change, PlanRefused, PostAction, Problem
from . import _fs
from . import _resource_files as file_ops
from . import augtool, file as file_kind, shadow
from .kinds import KindSpec, RevertHooks, register_kind, require_keys

_EDIT_STAGE_PREFIX = ".augeaswrap-edit-"
_MODE_NEW = 0o644
_TIMEOUT = 30.0
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_LENS_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.lns)?\Z")
_VALIDATORS = frozenset({"findmnt", "sshd", "sudoers", "systemd"})
_ON_CHANGE = frozenset({"daemon-reload", "none", "pending"})


@dataclass(frozen=True)
class _Operation:
    kind: str
    path: str | None = None
    value: str | None = None
    selector: str | None = None
    key: str | None = None
    items: tuple[tuple[tuple[str, str | tuple[str, ...]], ...], ...] = ()
    exclusive: bool = False


@dataclass(frozen=True)
class _StagedEdit:
    path: str
    exists: bool
    data: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None
    staged_path: Path | None
    expected: Mapping[str, Any]


@dataclass(frozen=True)
class _RestoreEdit:
    """Byte restore adapter that keeps the edit kind's revert shape tool-free."""

    restored: file_ops.RestoreFile
    kind: ClassVar[str] = "edit"

    @property
    def file(self) -> str:
        return self.restored.path

    def addresses(self) -> tuple[str, ...]:
        return self.restored.addresses()

    def observe(self, ctx: object) -> dict[str, object]:
        image = file_ops.observe_file(Path(getattr(ctx, "root")), self.file)
        return {self.addresses()[0]: {"image": image, "tree": {}, "matches": {}}}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping):
            raise ValueError("edit restore observation is invalid")
        current_image = before.get("image") if isinstance(before.get("image"), Mapping) else before
        after = _public_image(self.restored.image)
        action = "noop" if _fingerprint(current_image) == _fingerprint(after) else "change"
        return [Change(self.kind, address, action, _public_image(current_image), after, self)]

    def stage(self, ctx: object, change: Change) -> file_ops.StagedFile:
        del change
        return file_ops.stage_file(ctx, self.addresses()[0], self.file, self.restored.image)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        image = file_ops.commit_file(ctx, self.file, staged)
        return {"image": image, "tree": {}, "matches": {}}

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        return self.restored.post_actions(changes)

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        return file_ops.verify_file(
            ctx,
            address,
            self.file,
            getattr(ctx, "expected", {}).get(address),
            self.kind,
        )


def _script_safe(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a non-empty single-line string without NUL")
    return value


def _relative_expression(value: object, label: str) -> str:
    expression = _script_safe(value, label)
    if expression.startswith("/"):
        raise ValueError(f"{label} must be relative to the edited file")
    return expression


def _parse_item(value: Mapping[str, Any]) -> tuple[tuple[str, str | tuple[str, ...]], ...]:
    if not value:
        raise ValueError("records item must contain at least one field")
    fields: list[tuple[str, str | tuple[str, ...]]] = []
    for label, raw in value.items():
        if not isinstance(label, str) or _FIELD_RE.fullmatch(label) is None:
            raise ValueError("records field names must be simple Augeas labels")
        if label == "opt" and isinstance(raw, (list, tuple)) and raw:
            options = tuple(_script_safe(item, "records opt") for item in raw)
            fields.append((label, options))
        elif label == "opt":
            raise ValueError("records opt must be a non-empty string list")
        elif isinstance(raw, str):
            fields.append((label, _script_safe(raw, f"records field {label}")))
        else:
            raise ValueError("records fields must be strings; only opt may be a non-empty string list")
    return tuple(fields)


def _parse_operation(value: Mapping[str, Any]) -> _Operation:
    if any(not isinstance(key, str) for key in value):
        raise ValueError("edit op keys must be strings")
    variants = [name for name in ("set", "rm", "clear", "records") if name in value]
    if len(variants) != 1:
        raise ValueError("edit op must contain exactly one of set, rm, clear, or records")
    kind = variants[0]
    if kind in {"set", "rm", "clear"}:
        allowed = {kind, "value"} if kind == "set" else {kind}
        require_keys(value, allowed=allowed, required=allowed, where=f"edit {kind} op")
        path = _relative_expression(value[kind], f"edit {kind} path")
        if kind == "set":
            return _Operation(kind, path=path, value=_script_safe(value["value"], "edit set value"))
        return _Operation(kind, path=path)
    require_keys(
        value,
        allowed={"records", "key", "items", "exclusive"},
        required={"records", "key", "items", "exclusive"},
        where="edit records op",
    )
    selector = _relative_expression(value["records"], "edit records selector")
    key = value["key"]
    if not isinstance(key, str) or _FIELD_RE.fullmatch(key) is None:
        raise ValueError("edit records key must be a simple Augeas label")
    if type(value["exclusive"]) is not bool:
        raise ValueError("edit records exclusive must be a boolean")
    raw_items = value["items"]
    if not isinstance(raw_items, (list, tuple)) or any(not isinstance(item, Mapping) for item in raw_items):
        raise ValueError("edit records items must be an array of field maps")
    items = tuple(_parse_item(item) for item in raw_items)
    key_values: list[str] = []
    for item in items:
        fields = dict(item)
        key_value = fields.get(key)
        if not isinstance(key_value, str):
            raise ValueError(f"edit records item must contain scalar key field {key!r}")
        key_values.append(key_value)
    if len(key_values) != len(set(key_values)):
        raise ValueError(f"edit records items contain duplicate {key} values")
    return _Operation(
        kind,
        selector=selector,
        key=key,
        items=items,
        exclusive=value["exclusive"],
    )


def _operation_value(operation: _Operation) -> dict[str, Any]:
    if operation.kind in {"set", "rm", "clear"}:
        result: dict[str, Any] = {operation.kind: operation.path}
        if operation.kind == "set":
            result["value"] = operation.value
        return result
    assert operation.kind == "records"
    return {
        "records": operation.selector,
        "key": operation.key,
        "exclusive": operation.exclusive,
        "items": [
            {label: list(value) if isinstance(value, tuple) else value for label, value in item}
            for item in operation.items
        ],
    }


def _decode_image(image: Mapping[str, Any]) -> bytes:
    if not image.get("exists"):
        return b""
    encoded = image.get("data_b64")
    if not isinstance(encoded, str):
        raise ValueError("edit observation has no file bytes")
    return base64.b64decode(encoded.encode("ascii"), validate=True)


def _fingerprint(image: Mapping[str, Any]) -> str:
    if not image.get("exists"):
        return "absent"
    payload = {
        "sha256": image.get("sha256"),
        "mode": image.get("mode"),
        "uid": image.get("uid"),
        "gid": image.get("gid"),
    }
    return hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()


def _image(data: bytes, mode: int, uid: int, gid: int) -> dict[str, Any]:
    return {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mode": mode,
        "uid": uid,
        "gid": gid,
    }


def _public_image(image: Mapping[str, Any]) -> dict[str, Any]:
    if not image.get("exists"):
        return {"exists": False}
    return {key: image.get(key) for key in ("exists", "sha256", "mode", "uid", "gid")}


def _record_values(tree: Mapping[str, Any], path: str, base: str) -> dict[str, Any]:
    record = path.removeprefix(base.rstrip("/") + "/")
    raw_fields = tree.get(record, [])
    fields: dict[str, list[tuple[int, str]]] = {}
    for label_value, value in raw_fields:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\])?", label_value)
        if match is None:
            continue
        label = match.group(1)
        index = int(match.group(2) or "1")
        fields.setdefault(label, []).append((index, value))
    result: dict[str, Any] = {}
    for label, entries in fields.items():
        values = [value for _index, value in sorted(entries)]
        result[label] = values if len(values) > 1 or label == "opt" else values[0]
    return result


def _item_value(item: tuple[tuple[str, str | tuple[str, ...]], ...]) -> dict[str, Any]:
    return {label: list(value) if isinstance(value, tuple) else value for label, value in item}


def _node_path(base: str, record: str) -> str:
    return f"{base}/{record}"


def _record_ids(tree: Mapping[str, Any]) -> list[str]:
    return list(tree)


def _sequence_index(record: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(01)(?:\[(\d+)\])?", record)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or "1")


def _new_node_path(base: str, record: str, tree: Mapping[str, Any]) -> str:
    current = _sequence_index(record)
    if current is None:
        prior = sum(1 for item in _record_ids(tree) if _sequence_index(item) is not None)
        return f"{base}/01[{prior + 1}]"
    return f"{base}/01[{current[1] + 1}]"


def _path_label(path: str) -> str:
    return path.rsplit("/", 1)[-1].split("[", 1)[0]


def _set_item_commands(path: str, item: tuple[tuple[str, str | tuple[str, ...]], ...]) -> list[str]:
    commands: list[str] = []
    for label, value in item:
        if isinstance(value, tuple):
            commands.append(f"rm {augtool.quote(path + '/' + label)}")
            for index, option in enumerate(value, start=1):
                commands.append(f"set {augtool.quote(path + '/' + label + '[' + str(index) + ']')} {augtool.quote(option)}")
        else:
            commands.append(f"set {augtool.quote(path + '/' + label)} {augtool.quote(value)}")
    return commands


def _operation_commands(
    resource: "Edit",
    observation: Mapping[str, Any],
) -> list[str]:
    base = "/files" + resource.file
    tree = observation.get("tree", {})
    matches = observation.get("matches", {})
    if not isinstance(tree, Mapping) or not isinstance(matches, Mapping):
        raise ValueError("edit observation has invalid Augeas tree data")
    commands: list[str] = []
    for index, operation in enumerate(resource.ops):
        paths = matches.get(str(index), ())
        if not isinstance(paths, (list, tuple)) or any(not isinstance(path, str) for path in paths):
            raise ValueError("edit observation has invalid match paths")
        if operation.kind in {"set", "rm", "clear"}:
            path = f"{base}/{operation.path}"
            quoted_path = augtool.quote(path)
            if operation.kind == "set":
                assert operation.value is not None
                commands.append(f"set {quoted_path} {augtool.quote(operation.value)}")
            elif operation.kind == "rm":
                commands.append(f"rm {quoted_path}")
            else:
                commands.append(f"clear {quoted_path}")
            continue

        assert operation.kind == "records" and operation.key is not None and operation.selector is not None
        selected = [path for path in paths if path.startswith(base.rstrip("/") + "/")]
        wanted_values = {str(dict(item)[operation.key]) for item in operation.items}
        direct = _record_ids(tree)
        append_anchor = _node_path(base, direct[-1]) if direct else None
        for item in operation.items:
            desired = _item_value(item)
            key_value = desired[operation.key]
            found = [
                path
                for path in selected
                if _record_values(tree, path, base).get(operation.key) == key_value
            ]
            if not found:
                if append_anchor is not None:
                    commands.append(f"ins 01 after {augtool.quote(append_anchor)}")
                    current = _sequence_index(_path_label(append_anchor))
                    if current is None:
                        raise ValueError("records sequence has an unsupported node label")
                    node = f"{base}/01[{current[1] + 1}]"
                else:
                    node = f"{base}/01[1]"
                commands.extend(_set_item_commands(node, item))
                append_anchor = node
                continue
            for old_path in found:
                existing = _record_values(tree, old_path, base)
                if existing == desired:
                    continue
                record = old_path.removeprefix(base.rstrip("/") + "/")
                node = _new_node_path(base, record, tree)
                commands.append(f"ins 01 after {augtool.quote(old_path)}")
                commands.extend(_set_item_commands(node, item))
                commands.append(f"rm {augtool.quote(old_path)}")
        if operation.exclusive:
            for path in selected:
                current = _record_values(tree, path, base)
                if current.get(operation.key) not in wanted_values:
                    commands.append(f"rm {augtool.quote(path)}")
    return commands


@dataclass(frozen=True)
class Edit:
    """Edit one allowed file through an explicit Augeas lens and operation list."""

    file: str
    lens: str
    state: str
    ops: Sequence[Mapping[str, Any]]
    create: bool
    on_change: str
    validate: str | Sequence[str] | None = None
    accept_duplicates: Sequence[str] = ()
    kind: ClassVar[str] = "edit"

    def __post_init__(self) -> None:
        file_kind._path_value(self.file)
        if not isinstance(self.lens, str) or _LENS_RE.fullmatch(self.lens) is None:
            raise ValueError("edit lens must be a lens module name, optionally ending in .lns")
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("edit state must be 'present' or 'absent'")
        if type(self.create) is not bool:
            raise ValueError("edit create must be a boolean")
        if not isinstance(self.on_change, str) or self.on_change not in _ON_CHANGE:
            raise ValueError("edit on_change must be daemon-reload, none, or pending")
        if not isinstance(self.ops, (list, tuple)) or any(not isinstance(item, Mapping) for item in self.ops):
            raise ValueError("edit ops must be an array of operation maps")
        operations = tuple(_parse_operation(item) for item in self.ops)
        object.__setattr__(self, "ops", operations)
        if self.state == "absent":
            if self.create or operations or self.validate is not None:
                raise ValueError("absent edit must use create=false, no ops, and no validator")
        validator = _validate_spec(self.validate)
        object.__setattr__(self, "validate", validator)
        if not isinstance(self.accept_duplicates, (list, tuple)):
            raise ValueError("edit accept_duplicates must be an array of selectors")
        duplicates = tuple(_relative_expression(item, "edit accepted duplicate selector") for item in self.accept_duplicates)
        if len(duplicates) != len(set(duplicates)):
            raise ValueError("edit accept_duplicates contains a repeated selector")
        object.__setattr__(self, "accept_duplicates", duplicates)

    def addresses(self) -> tuple[str, ...]:
        return (f"edit:{self.file}",)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        root = Path(getattr(ctx, "root"))
        address = self.addresses()[0]
        try:
            image = file_kind._snapshot(root, self.file)
        except _fs.PathRefusal as exc:
            raise PlanRefused([Problem("edit.path.unsafe", str(exc), path=self.file)]) from exc
        if not image.get("exists") and self.state == "present" and not self.create:
            raise PlanRefused([Problem("edit.create.missing", "file is missing and create=false", path=self.file)])
        augtool.require_available(self.file)
        tree: dict[str, list[tuple[str, str]]] = {}
        selected: dict[str, tuple[str, ...]] = {}
        if self.state == "present":
            source = _decode_image(image)
            tree_output = augtool.run(
                path=self.file,
                lens=self.lens,
                source=source,
                script=augtool.tree_script(self.file),
            )
            base = "/files" + self.file
            tree = augtool.tree_values(tree_output.stdout, base)
            for index, operation in enumerate(self.ops):
                expression = (
                    f"{base}/{operation.path}"
                    if operation.kind in {"set", "rm", "clear"}
                    else f"{base}/{operation.selector}"
                )
                match_output = augtool.run(
                    path=self.file,
                    lens=self.lens,
                    source=source,
                    script=augtool.match_script(expression),
                )
                selected[str(index)] = augtool.match_paths(match_output.stdout, base)
        else:
            source = _decode_image(image)
        return {
            address: {
                "image": image,
                "tree": tree,
                "matches": {key: list(value) for key, value in selected.items()},
            }
        }

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        current = observed[address]
        if not isinstance(current, Mapping) or not isinstance(current.get("image"), Mapping):
            raise ValueError("edit observation must contain an image")
        before_image = current["image"]
        before = _public_image(before_image)
        if self.state == "absent":
            after: dict[str, Any] = {"exists": False}
            action = "noop" if not before_image.get("exists") else "change"
            details = ("state=absent",) if action == "change" else ()
            return [Change(self.kind, address, action, before, after, self, details)]

        self._guard_duplicates(current)
        source = _decode_image(before_image)
        commands = _operation_commands(self, current)
        staged = augtool.run(
            path=self.file,
            lens=self.lens,
            source=source,
            script=augtool.edit_script(commands),
            save_new=True,
        ).saved
        data = source if staged is None else staged
        mode, uid, gid = _metadata(before_image)
        after_image = _image(data, mode, uid, gid)
        after = _public_image(after_image)
        action = "noop" if _fingerprint(before_image) == _fingerprint(after_image) else "change"
        details: list[str] = []
        if action == "change" and self.validate == "findmnt" and Path(getattr(ctx, "root")) != Path("/"):
            details.append("validate: skipped(root)")
        if action == "change":
            details.extend(_unified_diff(self.file, source, data))
        return [Change(self.kind, address, action, before, after, self, tuple(details))]

    def _guard_duplicates(self, observation: Mapping[str, Any]) -> None:
        matches = observation.get("matches", {})
        tree = observation.get("tree", {})
        base = "/files" + self.file
        if not isinstance(matches, Mapping) or not isinstance(tree, Mapping):
            raise ValueError("edit observation has invalid match data")
        accepted = set(self.accept_duplicates)
        for index, operation in enumerate(self.ops):
            paths = matches.get(str(index), [])
            if operation.kind in {"set", "clear"} and len(paths) > 1 and operation.path not in accepted:
                raise PlanRefused(
                    [Problem("edit.duplicate", f"{operation.kind} path matched more than once", path=self.file)]
                )
            if operation.kind == "rm" and len(paths) > 1 and operation.path not in accepted:
                raise PlanRefused(
                    [Problem("edit.duplicate", "rm path matched more than once", path=self.file)]
                )
            if operation.kind == "records":
                assert operation.key is not None
                selected = [path for path in paths if isinstance(path, str)]
                for item in operation.items:
                    key_value = dict(item)[operation.key]
                    duplicates = [
                        path
                        for path in selected
                        if _record_values(tree, path, base).get(operation.key) == key_value
                    ]
                    if len(duplicates) > 1 and operation.selector not in accepted:
                        raise PlanRefused(
                            [Problem("edit.duplicate", f"records key {operation.key} matched more than once", path=self.file, key=operation.key)]
                        )

    def stage(self, ctx: object, change: Change) -> _StagedEdit:
        address = self.addresses()[0]
        observed = self.observe(ctx)[address]
        image = observed["image"]
        if _public_image(image) != change.before:
            raise PlanRefused([Problem("edit.stale", "file changed while staging; plan again", path=self.file)])
        if self.state == "absent":
            return _StagedEdit(self.file, False, None, None, None, None, None, {"exists": False})

        self._guard_duplicates(observed)
        source = _decode_image(image)
        commands = _operation_commands(self, observed)
        staged = augtool.run(
            path=self.file,
            lens=self.lens,
            source=source,
            script=augtool.edit_script(commands),
            save_new=True,
        ).saved
        data = source if staged is None else staged
        mode, uid, gid = _metadata(image)
        expected = _image(data, mode, uid, gid)
        stage_dir = Path(getattr(ctx, "stage_dir"))
        directory = stage_dir / hashlib.sha256(address.encode("utf-8")).hexdigest()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        staged_path = directory / Path(self.file).name
        descriptor = os.open(staged_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_staged(ctx, self.file, staged_path, self.validate)
        return _StagedEdit(self.file, True, data, mode, uid, gid, staged_path, expected)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, _StagedEdit):
            raise TypeError("edit commit requires its staged Augeas image")
        root = Path(getattr(ctx, "root"))
        target = root / self.file.lstrip("/")
        try:
            _fs.safe_components(root, target)
            if staged.exists:
                assert staged.data is not None and staged.mode is not None
                assert staged.uid is not None and staged.gid is not None
                _fs.atomic_write(
                    root,
                    target,
                    staged.data,
                    staged.mode,
                    temp_prefix=_EDIT_STAGE_PREFIX,
                    owner=(staged.uid, staged.gid),
                )
            elif target.exists():
                _fs.unlink_and_sync(root, target)
        except _fs.PathRefusal as exc:
            raise PlanRefused([Problem("edit.path.unsafe", str(exc), path=self.file)]) from exc
        return _public_image(file_ops.observe_file(root, self.file))

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        if self.on_change == "none" or not any(change.action == "change" for change in changes):
            return []
        return [
            PostAction(
                "systemd daemon-reload",
                ("systemctl", "daemon-reload"),
                _TIMEOUT,
                pending=self.on_change == "pending",
                phase="daemon_reload",
            )
        ]

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, _StagedEdit):
            return [Problem("edit.verify.expected", f"no staged image recorded for {self.file}", path=self.file)]
        try:
            actual = file_ops.observe_file(Path(getattr(ctx, "root")), self.file)
        except (_fs.PathRefusal, OSError) as exc:
            return [Problem("edit.verify.failed", f"cannot verify {self.file}: {exc}", path=self.file)]
        if _fingerprint(actual) == _fingerprint(expected.expected):
            return []
        return [Problem("edit.verify.failed", f"verification failed for {self.file}", path=self.file)]


def _metadata(image: Mapping[str, Any]) -> tuple[int, int, int]:
    if image.get("exists"):
        mode, uid, gid = image.get("mode"), image.get("uid"), image.get("gid")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (mode, uid, gid)):
            raise ValueError("edit file metadata is invalid")
        return stat.S_IMODE(mode), uid, gid
    return _MODE_NEW, os.geteuid(), os.getegid()


def _validate_spec(value: object) -> str | tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) and value in _VALIDATORS:
        return value
    if isinstance(value, (list, tuple)) and value and value[0] == "cmd":
        argv = tuple(value[1:])
        if any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("edit cmd validator must contain non-empty argv strings")
        if sum(item.count("%s") for item in argv) != 1:
            raise ValueError("edit cmd validator requires exactly one %s staged-file placeholder")
        return ("cmd", *argv)
    raise ValueError("edit validate must be findmnt, sshd, sudoers, systemd, or ['cmd', ..., '%s']")


def _validator_argv(spec: str | tuple[str, ...], staged: Path) -> tuple[str, ...]:
    if isinstance(spec, tuple):
        return tuple(item.replace("%s", str(staged)) for item in spec[1:])
    commands = {
        "findmnt": ("findmnt", "--verify", "--tab-file", str(staged)),
        "sshd": ("sshd", "-t", "-f", str(staged)),
        "sudoers": ("visudo", "-cf", str(staged)),
        "systemd": ("systemd-analyze", "verify", str(staged)),
    }
    return commands[spec]


def _validate_staged(ctx: object, path: str, staged: Path, spec: object) -> None:
    if spec is None:
        return
    if spec == "findmnt" and Path(getattr(ctx, "root")) != Path("/"):
        return
    assert isinstance(spec, (str, tuple))
    argv = _validator_argv(spec, staged)
    try:
        result = shadow.run_command(argv, timeout=_TIMEOUT)
    except Exception as exc:
        raise PlanRefused([Problem("edit.validate.failed", f"validator could not run: {exc}", path=path)]) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip() or "no diagnostic"
        raise PlanRefused(
            [Problem("edit.validate.failed", f"validator exited {result.returncode}: {detail}", path=path)]
        )


def _unified_diff(path: str, before: bytes, after: bytes) -> list[str]:
    rows = difflib.unified_diff(
        before.decode("utf-8", errors="replace").splitlines(True),
        after.decode("utf-8", errors="replace").splitlines(True),
        fromfile=f"a{path}",
        tofile=f"b{path}",
    )
    return [row.rstrip("\n") for row in rows]


def _snapshot(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("edit observation has no file image")
    image_value = observed.get("image", observed)
    if not isinstance(image_value, Mapping):
        raise ValueError("edit observation has no file image")
    image = image_value
    exists = image.get("exists") is True
    result: dict[str, Any] = {
        "path": getattr(change.resource, "file", None),
        "exists": exists,
        "sha256": image.get("sha256") if exists else None,
        "mode": image.get("mode") if exists else None,
        "uid": image.get("uid") if exists else None,
        "gid": image.get("gid") if exists else None,
    }
    if exists:
        result["data_b64"] = image.get("data_b64")
    return result


def _restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> _RestoreEdit:
    path = record.get("path")
    if not isinstance(path, str):
        raise ValueError("edit pre-image has no path")
    file_kind._path_value(path)
    if address != f"edit:{path}":
        raise ValueError("edit pre-image path does not match its address")
    return _RestoreEdit(file_ops.restore_file("edit", address, record, actions))


def _post_image(resource: object, ctx: object, address: str) -> object:
    del address
    path = getattr(resource, "file", None)
    if not isinstance(path, str):
        raise ValueError("edit post-image resource has no file path")
    return {
        "image": file_ops.observe_file(Path(getattr(ctx, "root")), path),
        "tree": {},
        "matches": {},
    }


def _pre_image(change: Change) -> object:
    if not isinstance(change.before, Mapping):
        raise ValueError("edit change has no prior image")
    return {"image": dict(change.before), "tree": {}, "matches": {}}


def _parse(table: Mapping[str, Any]) -> Edit:
    allowed = {"file", "lens", "state", "ops", "create", "validate", "on_change", "accept_duplicates"}
    require_keys(
        table,
        allowed=allowed,
        required={"file", "lens", "state", "ops", "create", "on_change"},
        where="edit",
    )
    if not isinstance(table["ops"], list) or any(not isinstance(item, Mapping) for item in table["ops"]):
        raise ValueError("edit ops must be an array of operation maps")
    accept = table.get("accept_duplicates", [])
    if not isinstance(accept, list):
        raise ValueError("edit accept_duplicates must be an array")
    return Edit(
        table["file"],
        table["lens"],
        table["state"],
        tuple(table["ops"]),
        table["create"],
        table["on_change"],
        table.get("validate"),
        tuple(accept),
    )


def _values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, Edit):
        raise ValueError("resource is not Edit")
    result: dict[str, Any] = {
        "file": resource.file,
        "lens": resource.lens,
        "state": resource.state,
        "ops": [_operation_value(item) for item in resource.ops],
        "create": resource.create,
        "on_change": resource.on_change,
    }
    if resource.validate is not None:
        result["validate"] = resource.validate
    if resource.accept_duplicates:
        result["accept_duplicates"] = list(resource.accept_duplicates)
    return result


def _render(change: Change, plan_value: Any) -> list[str]:
    del plan_value
    # The staged unified diff is stored as Change detail lines so it is visible
    # in both text plans and their JSON representation without exposing bytes.
    return []


register_kind(
    KindSpec(
        kind="edit",
        parse=_parse,
        to_toml=_values,
        render=_render,
        revert=RevertHooks(
            snapshot=_snapshot,
            restore=_restore,
            post_image=_post_image,
            pre_image=_pre_image,
        ),
    )
)


__all__ = ["Edit"]
