"""Whole-file tmpfiles.d resources with syntax, dry-run and shadow guards."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping, cast

from . import Change, PlanRefused, PostAction, Problem
from . import _resource_files as file_ops
from . import shadow
from .kinds import KindSpec, RevertHooks, register_kind, require_keys
from .shadow import TARGET_CLASSES, Finding, analyze, command_error, effective_files, observe_layers, settings

_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.conf")
_TYPE_RE = re.compile(r"[fFwdDvqQpLcbCxXrRzZtTaAhHem][!+=~^+-]*")
_MODE_RE = re.compile(r"(?:[:~])?[0-7]{3,4}")
_CLASS = TARGET_CLASSES["tmpfiles"]


def _c_unescape(value: str, index: int) -> tuple[str, int]:
    if index + 1 >= len(value):
        raise ValueError("trailing backslash")
    char = value[index + 1]
    simple = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "s": " ", "t": "\t", "v": "\v", "\\": "\\", "\"": "\"", "'": "'"}
    if char in simple:
        return simple[char], index + 2
    if char == "x":
        match = re.match(r"[0-9A-Fa-f]{1,2}", value[index + 2 :])
        if match is None:
            raise ValueError("invalid hexadecimal C escape")
        return chr(int(match.group(), 16)), index + 2 + len(match.group())
    if char in "01234567":
        match = re.match(r"[0-7]{1,3}", value[index + 1 :])
        assert match is not None
        return chr(int(match.group(), 8)), index + 1 + len(match.group())
    raise ValueError(f"unknown C escape \\{char}")


def _fields(line: str) -> tuple[list[str], str]:
    """Parse the six fixed tmpfiles columns and leave its argument intact."""

    fields: list[str] = []
    index = 0
    whitespace = " \t\n\r\v\f"
    while len(fields) < 6:
        while index < len(line) and line[index] in whitespace:
            index += 1
        if index >= len(line):
            break
        value: list[str] = []
        quote: str | None = None
        if line[index] in {"'", '"'}:
            quote = line[index]
            index += 1
        while index < len(line):
            char = line[index]
            if char == "\\":
                escaped, index = _c_unescape(line, index)
                value.append(escaped)
            elif quote is not None:
                if char == quote:
                    index += 1
                    quote = None
                    break
                value.append(char)
                index += 1
            elif char in whitespace:
                break
            elif char in {"'", '"'}:
                raise ValueError("quote must enclose a complete field")
            else:
                value.append(char)
                index += 1
        if quote is not None:
            raise ValueError("unterminated quoted field")
        fields.append("".join(value))
        if index < len(line) and line[index] not in whitespace:
            raise ValueError("quoted field must be followed by whitespace")
    while index < len(line) and line[index] in whitespace:
        index += 1
    return fields, line[index:].rstrip()


def _parse_lines(data: bytes, *, strict: bool = True) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fields, argument = _fields(line)
        except ValueError as exc:
            raise ValueError(f"tmpfiles line {number} has invalid quoting or escapes") from exc
        if len(fields) < 2:
            raise ValueError(f"tmpfiles line {number} must have at least a type and path")
        if _TYPE_RE.fullmatch(fields[0]) is None:
            raise ValueError(f"tmpfiles line {number} has an invalid type")
        path = fields[1]
        if strict and path != "-" and (
            not path.startswith("/")
            or ".." in Path(path).parts
            or "\x00" in path
            or Path(path).as_posix() != path
        ):
            raise ValueError(f"tmpfiles line {number} path must be absolute without dot-dot components")
        if strict and len(fields) >= 3 and fields[2] != "-" and _MODE_RE.fullmatch(fields[2]) is None:
            raise ValueError(f"tmpfiles line {number} mode must be octal or '-'")
        if strict and path != "-" and path in seen:
            raise ValueError(f"tmpfiles request contains duplicate path {path}")
        if path != "-":
            seen.add(path)
        normalized = " ".join(fields)
        if argument:
            normalized += " " + argument
        rows.append((path, normalized))
    return tuple(rows)


def _shadow_lines(data: bytes) -> tuple[tuple[str, str], ...]:
    """Return only path-bearing rows for cross-file shadow comparisons."""

    return tuple(row for row in _parse_lines(data, strict=False) if row[0] != "-")


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
                "group": file_ops.observe_file(root, "/etc/group"),
                "passwd": file_ops.observe_file(root, "/etc/passwd"),
            }
        }

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping) or not isinstance(before.get("target"), Mapping):
            raise ValueError("tmpfiles observation is invalid")
        data = cast(str, self.text).encode("utf-8") if self.state == "present" else None
        details: list[str] = []
        if data is not None:
            own = _parse_lines(data)
            files, masks = effective_files(before["layers"], _CLASS, overrides={self.path: data})
            parsed = settings(files, _shadow_lines)
            findings = analyze(
                target_file=self.path,
                assignments=parsed,
                target_keys=[key for key, _value in own if key != "-"],
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
        planned_gids = getattr(ctx, "planned_gids", {})
        planned_group_absent = getattr(ctx, "planned_group_absent", set())
        after = {
            "image": image,
            "planned_gids": dict(planned_gids) if isinstance(planned_gids, Mapping) else {},
            "planned_group_absent": sorted(planned_group_absent) if isinstance(planned_group_absent, set) else [],
        }
        return [Change(self.kind, address, action, dict(before), after, self, tuple(details))]

    def stage(self, ctx: object, change: Change) -> file_ops.StagedFile:
        staged = file_ops.stage_file(ctx, change.address, self.path, change.after["image"])
        if self.state == "present":
            _validate_staged(
                ctx,
                self.path,
                staged,
                change.before,
                change.after.get("planned_gids", {}),
                change.after.get("planned_group_absent", []),
            )
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
        problems = file_ops.verify_file(ctx, address, self.path, getattr(ctx, "expected", {}).get(address), self.kind)
        if self.state == "present":
            try:
                root = Path(getattr(ctx, "root"))
                layers = observe_layers(root, _CLASS)
                files, masks = effective_files(layers, _CLASS)
                parsed = settings(files, _shadow_lines)
                findings = analyze(
                    target_file=self.path,
                    assignments=parsed,
                    target_keys=[key for key, _value in _parse_lines(self.text.encode("utf-8")) if key != "-"],
                    intent=self.intent,
                    merge="first",
                    accept_shadow=self.accept_shadow,
                    masks=masks,
                )
                problems.extend(
                    Problem(item.code, item.message, path=item.file, key=item.key)
                    for item in findings
                    if item.severity == "error"
                )
            except (OSError, ValueError, PlanRefused) as exc:
                if isinstance(exc, PlanRefused):
                    problems.extend(exc.problems)
                else:
                    problems.append(Problem("tmpfiles.shadow.verify", f"cannot verify tmpfiles shadow order: {exc}", path=self.path))
        return problems


def _validate_staged(
    ctx: object,
    path: str,
    staged: file_ops.StagedFile,
    before: Mapping[str, Any],
    planned_gids: object,
    planned_group_absent: object,
) -> None:
    if not staged.expected.get("exists"):
        return
    stage_root = Path(staged.path).parent / "tmpfiles-root"
    conf = stage_root / path.lstrip("/")
    conf.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    conf.write_bytes(Path(staged.path).read_bytes())
    for source, key in (("/etc/group", "group"), ("/etc/passwd", "passwd")):
        image = before.get(key)
        if not isinstance(image, Mapping):
            raise PlanRefused([Problem("tmpfiles.validate.accounts", f"missing {source} snapshot for validation", path=path)])
        if image.get("exists"):
            target = stage_root / source.lstrip("/")
            target.write_bytes(base64.b64decode(str(image["data_b64"]), validate=True))
    if not isinstance(planned_gids, Mapping):
        raise PlanRefused([Problem("tmpfiles.validate.accounts", "planned group map is invalid", path=path)])
    if not isinstance(planned_group_absent, (tuple, list)) or any(
        not isinstance(name, str) for name in planned_group_absent
    ):
        raise PlanRefused([Problem("tmpfiles.validate.accounts", "planned group removal map is invalid", path=path)])
    if planned_gids or planned_group_absent:
        target = stage_root / "etc/group"
        existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        removed = set(planned_group_absent)
        existing = [line for line in existing if line.partition(":")[0] not in removed]
        names = {line.partition(":")[0] for line in existing if ":" in line}
        for name, gid in sorted(planned_gids.items()):
            if not isinstance(name, str) or not isinstance(gid, int) or isinstance(gid, bool) or gid < 0:
                raise PlanRefused([Problem("tmpfiles.validate.accounts", "planned group map is invalid", path=path)])
            if name not in names:
                existing.append(f"{name}:x:{gid}:")
                names.add(name)
        target.write_text("\n".join(existing) + ("\n" if existing else ""), encoding="utf-8")
    argv = ("systemd-tmpfiles", "--root", str(stage_root), "--dry-run", "--create", str(conf))
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
        revert=RevertHooks(
            snapshot=file_ops.snapshot,
            restore=_restore,
            post_image=file_ops.post_image,
            pre_image=file_ops.pre_image,
        ),
    )
)

__all__ = ["Tmpfiles"]
