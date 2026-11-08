"""Whole-file sysctl.d resources with intent-aware shadow checks."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _resource_files as file_ops
from . import shadow
from .kinds import KindSpec, RevertHooks, register_kind, require_keys
from .shadow import (
    TARGET_CLASSES,
    Assignment,
    Finding,
    analyze,
    command_error,
    effective_files,
    observe_layers,
    settings,
)

_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.conf")
_KEY_RE = re.compile(r"[A-Za-z0-9_*][A-Za-z0-9_.*-]*(?:/[A-Za-z0-9_.*-]+)*")
_CONFIG_KEY_RE = re.compile(r"-?[A-Za-z0-9_*][A-Za-z0-9_.*-]*(?:/[A-Za-z0-9_.*-]+)*")
_SYSTEMD_SYSCTL = "/usr/lib/systemd/systemd-sysctl"
_NATIVE_HEADER_RE = re.compile(
    r"^/(?:etc/sysctl\.conf|(?:etc|run|usr/local/lib|usr/lib|lib)/sysctl\.d/[^/]+\.conf)\Z"
)
_CLASS = TARGET_CLASSES["sysctl"]


@dataclass(frozen=True)
class SysctlEntry:
    key: str
    value: str
    comment: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or _KEY_RE.fullmatch(self.key) is None:
            raise ValueError("sysctl key must start with a letter, digit, underscore, or *; a leading '-' marker is not a key")
        if not isinstance(self.value, str) or not self.value or any(char in self.value for char in "\r\n\x00#"):
            raise ValueError("sysctl value must be a non-empty single line without NUL or comment markers")
        if self.comment is not None and (
            not isinstance(self.comment, str) or any(char in self.comment for char in "\r\n\x00")
        ):
            raise ValueError("sysctl comment must be a single line without NUL")


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
        ):
            raise ValueError("accept_shadow file must be an exact normalized absolute path")
        if not isinstance(key, str) or not key or "\x00" in key:
            raise ValueError("accept_shadow key must be a non-empty string")
        pair = (file, key)
        if pair in result:
            raise ValueError(f"duplicate accept_shadow entry for {file} key {key}")
        result.append(pair)
    return tuple(result)


@dataclass(frozen=True)
class Sysctl:
    file: str
    state: str
    entries: tuple[SysctlEntry, ...]
    apply_now: bool
    intent: str
    accept_shadow: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[str] = "sysctl"

    def __post_init__(self) -> None:
        if not isinstance(self.file, str) or _FILE_RE.fullmatch(self.file) is None or "/" in self.file:
            raise ValueError("sysctl file must be a single .conf filename")
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("sysctl state must be 'present' or 'absent'")
        if type(self.apply_now) is not bool:
            raise ValueError("sysctl apply_now must be a boolean")
        if not isinstance(self.entries, tuple):
            raise ValueError("sysctl entries must be a tuple")
        if self.state == "absent" and (self.entries or self.apply_now):
            raise ValueError("absent sysctl must not have entries or apply_now")
        if self.state == "present" and not self.entries:
            raise ValueError("present sysctl requires at least one entry")
        if any(not isinstance(item, SysctlEntry) for item in self.entries):
            raise ValueError("sysctl entries must be SysctlEntry values")
        keys = [_canonical_key(item.key) for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("sysctl request contains a duplicate key")
        if not isinstance(self.intent, str) or self.intent not in {"authoritative", "fallback"}:
            raise ValueError("sysctl intent must be 'authoritative' or 'fallback'")
        object.__setattr__(self, "accept_shadow", _accept_value(self.accept_shadow))

    @property
    def path(self) -> str:
        return f"/etc/sysctl.d/{self.file}"

    def addresses(self) -> tuple[str, ...]:
        return (f"sysctl:{self.path}",)

    def observe(self, ctx: object) -> dict[str, object]:
        root = Path(getattr(ctx, "root"))
        layers = observe_layers(root, _CLASS)
        native = _native_headers(root, layers) if root == Path("/") else ()
        proc = _live_keys(self.entries) if root == Path("/") and self.state == "present" else ()
        return {
            self.addresses()[0]: {
                "target": file_ops.observe_file(root, self.path),
                "layers": layers,
                "native_headers": list(native),
                "proc_keys": list(proc),
            }
        }

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping) or not isinstance(before.get("target"), Mapping):
            raise ValueError("sysctl observation is invalid")
        data = _render(self.entries) if self.state == "present" else None
        target_values = [_canonical_key(entry.key) for entry in self.entries] if data is not None else []
        details: list[str] = []
        if data is not None:
            _check_live_keys(before.get("proc_keys", ()), self.entries, Path(getattr(ctx, "root")))
            if not isinstance(before.get("layers"), Mapping):
                raise ValueError("sysctl observation has no layer snapshot")
            files, masks = effective_files(before["layers"], _CLASS, overrides={self.path: data})
            parsed = settings(files, _parse_assignments)
            findings = analyze(
                target_file=self.path,
                assignments=parsed,
                target_keys=target_values,
                intent=self.intent,
                merge="last",
                accept_shadow=self.accept_shadow,
                masks=masks,
            )
            details.extend(_finding_details(findings))
        expected = file_ops.expected_image(data, before["target"])
        file_changed = _fingerprint(before["target"]) != _fingerprint(expected)
        action = "change" if file_changed or self.apply_now else "noop"
        if data is not None and self.apply_now:
            details.append("irreversible: apply_now changes live kernel values; revert restores only the fragment")
        elif data is None and file_changed:
            details.append("irreversible: deleting the sysctl fragment leaves the live kernel value unchanged")
        return [Change(self.kind, address, action, dict(before), expected, self, tuple(details))]

    def stage(self, ctx: object, change: Change) -> file_ops.StagedFile:
        return file_ops.stage_file(ctx, change.address, self.path, change.after)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, file_ops.StagedFile):
            raise TypeError("sysctl commit requires a staged file")
        return file_ops.commit_file(ctx, self.path, staged)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        if self.state == "present" and self.apply_now:
            return [PostAction(f"sysctl {self.path}", ("sysctl", "-p", self.path), 30.0)]
        return []

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        problems = file_ops.verify_file(ctx, address, self.path, expected, self.kind)
        root = Path(getattr(ctx, "root"))
        try:
            layers = observe_layers(root, _CLASS)
            if self.state == "present":
                files, masks = effective_files(layers, _CLASS)
                parsed = settings(files, _parse_assignments)
                findings = analyze(
                    target_file=self.path,
                    assignments=parsed,
                    target_keys=[_canonical_key(entry.key) for entry in self.entries],
                    intent=self.intent,
                    merge="last",
                    accept_shadow=self.accept_shadow,
                    masks=masks,
                )
                problems.extend(
                    Problem(item.code, item.message, path=item.file, key=item.key)
                    for item in findings
                    if item.severity == "error"
                )
            if root != Path("/"):
                return problems
            _native_headers(root, layers)
            if self.state == "present" and self.apply_now:
                result = _readback(self.entries, getattr(ctx, "expected", {}).get(address))
                problems.extend(result)
        except PlanRefused as exc:
            problems.extend(exc.problems)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            problems.append(Problem("sysctl.verify.failed", f"cannot verify sysctl state: {exc}", path=self.path))
        return problems


def _fingerprint(image: Mapping[str, Any]) -> str:
    value = {key: image.get(key) for key in ("exists", "sha256", "mode", "uid", "gid")}
    return hashlib.sha256(repr(sorted(value.items())).encode("utf-8")).hexdigest()


def _render(entries: tuple[SysctlEntry, ...]) -> bytes:
    rows: list[str] = []
    for entry in sorted(entries, key=lambda item: item.key):
        if entry.comment:
            rows.append(f"# {entry.comment}")
        rows.append(f"{entry.key} = {entry.value}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def _parse_assignments(data: bytes) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise ValueError(f"invalid sysctl assignment on line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if _CONFIG_KEY_RE.fullmatch(key if key else "") is None or not value:
            raise ValueError(f"invalid sysctl assignment on line {number}")
        rows.append((_canonical_key(key), value))
    return tuple(rows)


def _canonical_key(key: str) -> str:
    """Normalize systemd's accepted slash spelling to proc-style dotted keys."""

    return key.removeprefix("-").replace("/", ".")


def _live_keys(entries: tuple[SysctlEntry, ...]) -> tuple[str, ...]:
    root = Path("/proc/sys")
    found: list[str] = []
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                key = path.relative_to(root).as_posix().replace("/", ".")
            except ValueError:
                continue
            found.append(key)
    matched = tuple(
        sorted(
            key
            for key in found
            if any(fnmatch.fnmatchcase(key, _canonical_key(item.key)) for item in entries)
        )
    )
    return matched


def _check_live_keys(proc_keys: object, entries: tuple[SysctlEntry, ...], root: Path) -> None:
    if root != Path("/"):
        return
    if not isinstance(proc_keys, (tuple, list)):
        raise ValueError("live proc/sys key list is invalid")
    for entry in entries:
        key_pattern = _canonical_key(entry.key)
        matches = [key for key in proc_keys if isinstance(key, str) and fnmatch.fnmatchcase(key, key_pattern)]
        if not matches:
            raise PlanRefused([Problem("sysctl.key.missing", f"sysctl key {entry.key} does not exist under /proc/sys", path=entry.key, key=entry.key)])


def _native_headers(root: Path, layers: Mapping[str, object]) -> tuple[str, ...]:
    del root
    argv = (_resolve_systemd_sysctl(), "--cat-config")
    try:
        result = shadow.run_command(argv, timeout=30.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlanRefused(
            [Problem("sysctl.native.failed", f"cannot run systemd-sysctl --cat-config: {exc}")]
        ) from exc
    problem = command_error(argv, result)
    if problem is not None:
        raise PlanRefused([Problem("sysctl.native.failed", problem)])
    files, _masks = effective_files(layers, _CLASS)
    python = tuple(item.path for item in files)
    native: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("# /"):
            continue
        header = line[2:]
        if _NATIVE_HEADER_RE.fullmatch(header) is not None:
            native.append(header)
    if tuple(native) != python:
        raise PlanRefused(
            [Problem("sysctl.native.order", f"systemd-sysctl --cat-config order differs from Python: {tuple(native)!r} != {python!r}")]
        )
    return tuple(native)


def _resolve_systemd_sysctl() -> str:
    """Prefer Debian's systemd-sysctl path; use PATH only as a fallback."""

    if os.path.isfile(_SYSTEMD_SYSCTL) and os.access(_SYSTEMD_SYSCTL, os.X_OK):
        return _SYSTEMD_SYSCTL
    resolved = shutil.which("systemd-sysctl")
    if resolved:
        return resolved
    raise PlanRefused(
        [
            Problem(
                "sysctl.native.failed",
                f"systemd-sysctl is unavailable at {_SYSTEMD_SYSCTL} and was not found on PATH",
            )
        ]
    )


def _readback(entries: tuple[SysctlEntry, ...], expected: object) -> list[Problem]:
    del expected
    problems: list[Problem] = []
    proc_keys = _live_keys(entries)
    for entry in entries:
        keys = [key for key in proc_keys if fnmatch.fnmatchcase(key, _canonical_key(entry.key))]
        if not keys:
            problems.append(Problem("sysctl.readback.missing", f"sysctl key {entry.key} disappeared from /proc/sys", path=entry.key, key=entry.key))
        for key in keys:
            result = shadow.run_command(("sysctl", "-n", key), timeout=10.0)
            problem = command_error(("sysctl", "-n", key), result)
            if problem is not None:
                problems.append(Problem("sysctl.readback.failed", problem, path=key, key=key))
            elif " ".join(result.stdout.split()) != " ".join(entry.value.split()):
                problems.append(Problem("sysctl.readback.mismatch", f"{key} is {result.stdout.strip()!r}, expected {entry.value!r}", path=key, key=key))
    return problems


def _finding_details(findings: tuple[Finding, ...]) -> list[str]:
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        raise PlanRefused([Problem(item.code, item.message, path=item.file, key=item.key) for item in errors])
    return [f"note {item.code}: {item.message}" for item in findings]


def _restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> file_ops.RestoreFile:
    filename = record.get("path")
    if not isinstance(filename, str) or not filename.startswith("/etc/sysctl.d/") or _FILE_RE.fullmatch(filename.removeprefix("/etc/sysctl.d/")) is None:
        raise ValueError("sysctl pre-image path is outside /etc/sysctl.d")
    resource = file_ops.restore_file("sysctl", address, record, actions)
    return resource if resource.image.get("exists") else replace(resource, actions=())


def _parse_entry(table: Mapping[str, Any]) -> SysctlEntry:
    require_keys(table, allowed={"key", "value", "comment"}, required={"key", "value"}, where="sysctl entry")
    return SysctlEntry(table["key"], table["value"], table.get("comment"))


def _parse(table: Mapping[str, Any]) -> Sysctl:
    allowed = {"file", "state", "entries", "apply_now", "intent", "accept_shadow"}
    require_keys(table, allowed=allowed, required={"file", "state", "apply_now", "intent"}, where="sysctl")
    state = table["state"]
    if state == "present":
        require_keys(table, allowed=allowed, required={*allowed - {"accept_shadow"}}, where="sysctl")
    elif state == "absent":
        require_keys(table, allowed=allowed, required={"file", "state", "apply_now", "intent"}, where="sysctl")
    raw_entries = table.get("entries", [])
    if not isinstance(raw_entries, list) or any(not isinstance(item, Mapping) for item in raw_entries):
        raise ValueError("sysctl entries must be an array of tables")
    accept = _accept_value(table.get("accept_shadow", []))
    return Sysctl(
        table["file"],
        state,
        tuple(_parse_entry(item) for item in raw_entries),
        table["apply_now"],
        table["intent"],
        accept,
    )


def _values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, Sysctl):
        raise ValueError("resource is not a Sysctl")
    result: dict[str, Any] = {
        "file": resource.file,
        "state": resource.state,
        "apply_now": resource.apply_now,
        "intent": resource.intent,
    }
    if resource.state == "present":
        result["entries"] = [
            {"key": item.key, "value": item.value, **({"comment": item.comment} if item.comment is not None else {})}
            for item in resource.entries
        ]
    if resource.accept_shadow:
        result["accept_shadow"] = [{"file": file, "key": key} for file, key in resource.accept_shadow]
    return result


register_kind(
    KindSpec(
        kind="sysctl",
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

__all__ = ["Sysctl", "SysctlEntry"]
