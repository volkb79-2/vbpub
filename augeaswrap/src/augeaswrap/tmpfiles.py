"""Whole-file tmpfiles.d resources with syntax, dry-run and shadow guards."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _resource_files as file_ops
from . import shadow
from .kinds import KindSpec, RevertHooks, register_kind, require_keys
from .shadow import TARGET_CLASSES, Finding, analyze, command_error, effective_files, observe_layers, settings

_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.conf")
_TYPE_RE = re.compile(r"[fFwdDvqQpLcbCxXrRzZtTaA]")
_CLASS = TARGET_CLASSES["tmpfiles"]


def _parse_lines(data: bytes) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2 or len(fields) > 7:
            raise ValueError(f"tmpfiles line {number} must have 2 to 7 fields")
        row_type = fields[0].lstrip("!+-")
        if _TYPE_RE.fullmatch(row_type) is None:
            raise ValueError(f"tmpfiles line {number} has an invalid type")
        path = fields[1]
        if path != "-" and (
            not path.startswith("/")
            or ".." in Path(path).parts
            or "\x00" in path
            or Path(path).as_posix() != path
        ):
            raise ValueError(f"tmpfiles line {number} path must be absolute without dot-dot components")
        if len(fields) >= 3 and fields[2] != "-" and re.fullmatch(r"[0-7]{3,4}", fields[2]) is None:
            raise ValueError(f"tmpfiles line {number} mode must be octal or '-'")
        if path != "-" and path in seen:
            raise ValueError(f"tmpfiles request contains duplicate path {path}")
        if path != "-":
            seen.add(path)
        rows.append((path, " ".join(fields)))
    return tuple(rows)


def _accept_value(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("accept_shadow must be an array of {file, key} tables")
    result: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            require_keys(item, allowed={"file", "key"}, required={"file", "key"}, where="accept_shadow")
            file, key = item["file"], item["key"]
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            file, key = item
        else:
            raise ValueError("accept_shadow entries must be {file, key} tables or pairs")
        if (
            not isinstance(file, str)
            or not file.startswith("/")
            or "\x00" in file
            or ".." in Path(file).parts
            or Path(file).as_posix() != file
            or not isinstance(key, str)
            or not key
            or "\x00" in key
        ):
            raise ValueError("accept_shadow requires an exact absolute file and non-empty key")
        pair = (file, key)
        if pair in result:
            raise ValueError(f"duplicate accept_shadow entry for {file} key {key}")
        result.append(pair)
    return tuple(result)


@dataclass(frozen=True)
class Tmpfiles:
    file: str
    state: str
    text: str | None
    intent: str
    accept_shadow: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[str] = "tmpfiles"

    def __post_init__(self) -> None:
        if not isinstance(self.file, str) or _FILE_RE.fullmatch(self.file) is None or "/" in self.file:
            raise ValueError("tmpfiles file must be a single .conf filename")
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("tmpfiles state must be 'present' or 'absent'")
        if not isinstance(self.intent, str) or self.intent not in {"authoritative", "fallback"}:
            raise ValueError("tmpfiles intent must be 'authoritative' or 'fallback'")
        if self.state == "present":
            if not isinstance(self.text, str) or not self.text:
                raise ValueError("present tmpfiles requires non-empty text")
            if "\x00" in self.text or "\r" in self.text:
                raise ValueError("tmpfiles text must not contain NUL or carriage returns")
            _parse_lines(self.text.encode("utf-8"))
        elif self.text is not None:
            raise ValueError("absent tmpfiles must not specify text")
        object.__setattr__(self, "accept_shadow", _accept_value(self.accept_shadow))

    @property
    def path(self) -> str:
        return f"/etc/tmpfiles.d/{self.file}"

    def addresses(self) -> tuple[str, ...]:
        return (f"tmpfiles:{self.path}",)

    def observe(self, ctx: object) -> dict[str, object]:
        root = Path(getattr(ctx, "root"))
        return {
            self.addresses()[0]: {
                "target": file_ops.observe_file(root, self.path),
                "layers": observe_layers(root, _CLASS),
            }
        }

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping) or not isinstance(before.get("target"), Mapping):
            raise ValueError("tmpfiles observation is invalid")
        data = self.text.encode("utf-8") if self.state == "present" and self.text is not None else None
        details: list[str] = []
        if data is not None:
            own = _parse_lines(data)
            files, masks = effective_files(before["layers"], _CLASS, overrides={self.path: data})
            parsed = settings(files, _parse_lines)
            findings = analyze(
                target_file=self.path,
                assignments=parsed,
                target_keys=[key for key, _value in own],
                intent=self.intent,
                merge="first",
                accept_shadow=self.accept_shadow,
                masks=masks,
            )
            details.extend(_finding_details(findings))
        image = file_ops.expected_image(data, before["target"])
        file_changed = _fingerprint(before["target"]) != _fingerprint(image)
        action = "change" if file_changed else "noop"
        if data is None and file_changed:
            details.append("irreversible: deleting the tmpfiles file leaves paths already created in place")
        elif data is not None and file_changed:
            details.append("irreversible: systemd-tmpfiles --create may change paths; revert restores only the fragment")
        return [Change(self.kind, address, action, dict(before), image, self, tuple(details))]

    def stage(self, ctx: object, change: Change) -> file_ops.StagedFile:
        staged = file_ops.stage_file(ctx, change.address, self.path, change.after)
        if self.state == "present":
            _validate_staged(ctx, self.path, staged)
        return staged

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, file_ops.StagedFile):
            raise TypeError("tmpfiles commit requires a staged file")
        return file_ops.commit_file(ctx, self.path, staged)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        if self.state == "present" and any(change.action == "change" for change in changes):
            return [PostAction(f"systemd-tmpfiles {self.path}", ("systemd-tmpfiles", "--create", self.path), 30.0)]
        return []

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        return file_ops.verify_file(ctx, address, self.path, getattr(ctx, "expected", {}).get(address), self.kind)


def _validate_staged(ctx: object, path: str, staged: file_ops.StagedFile) -> None:
    if not staged.expected.get("exists"):
        return
    stage_root = Path(staged.path).parent / "tmpfiles-root"
    conf = stage_root / path.lstrip("/")
    conf.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    conf.write_bytes(Path(staged.path).read_bytes())
    argv = ("systemd-tmpfiles", "--root", str(stage_root), "--dry-run", "--create", path)
    try:
        result = shadow.run_command(argv, timeout=30.0)
    except Exception as exc:
        raise PlanRefused([Problem("tmpfiles.validate.failed", f"cannot run systemd-tmpfiles dry-run: {exc}", path=path)]) from exc
    message = command_error(argv, result)
    if message is not None:
        raise PlanRefused([Problem("tmpfiles.validate.failed", message, path=path)])


def _fingerprint(image: Mapping[str, Any]) -> str:
    value = {key: image.get(key) for key in ("exists", "sha256", "mode", "uid", "gid")}
    return hashlib.sha256(repr(sorted(value.items())).encode("utf-8")).hexdigest()


def _finding_details(findings: tuple[Finding, ...]) -> list[str]:
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        raise PlanRefused([Problem(item.code, item.message, path=item.file, key=item.key) for item in errors])
    return [f"note {item.code}: {item.message}" for item in findings]


def _restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> file_ops.RestoreFile:
    filename = record.get("path")
    if not isinstance(filename, str) or not filename.startswith("/etc/tmpfiles.d/") or _FILE_RE.fullmatch(filename.removeprefix("/etc/tmpfiles.d/")) is None:
        raise ValueError("tmpfiles pre-image path is outside /etc/tmpfiles.d")
    resource = file_ops.restore_file("tmpfiles", address, record, actions)
    return resource if resource.image.get("exists") else replace(resource, actions=())


def _parse(table: Mapping[str, Any]) -> Tmpfiles:
    allowed = {"file", "state", "text", "intent", "accept_shadow"}
    require_keys(table, allowed=allowed, required={"file", "state", "intent"}, where="tmpfiles")
    if table["state"] == "present":
        require_keys(table, allowed=allowed, required={"file", "state", "text", "intent"}, where="tmpfiles")
    return Tmpfiles(table["file"], table["state"], table.get("text"), table["intent"], _accept_value(table.get("accept_shadow", [])))


def _values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, Tmpfiles):
        raise ValueError("resource is not Tmpfiles")
    result: dict[str, Any] = {"file": resource.file, "state": resource.state, "intent": resource.intent}
    if resource.text is not None:
        result["text"] = resource.text
    if resource.accept_shadow:
        result["accept_shadow"] = [{"file": file, "key": key} for file, key in resource.accept_shadow]
    return result


register_kind(
    KindSpec(
        kind="tmpfiles",
        parse=_parse,
        to_toml=_values,
        revert=RevertHooks(snapshot=file_ops.snapshot, restore=_restore),
    )
)

__all__ = ["Tmpfiles"]
