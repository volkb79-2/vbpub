"""Read-only exec and path-mode guards for Augeaswrap requests."""

from __future__ import annotations

import grp
import hashlib
import json
import math
import pwd
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, Problem, PostAction
from . import _fs
from .file import _mode_value


def _resolve_id(value: str | int, *, group: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("path_mode owner and group must be names or numeric ids")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("path_mode owner and group ids must be non-negative")
        return value
    if not value or "\x00" in value:
        raise ValueError("path_mode owner and group names must be non-empty")
    try:
        return grp.getgrnam(value).gr_gid if group else pwd.getpwnam(value).pw_uid
    except KeyError as exc:
        label = "group" if group else "owner"
        raise ValueError(f"unknown path_mode {label} {value!r}") from exc


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PathMode:
    """Read-only assertion about one path beneath the selected root."""

    path: str
    owner: str | int
    group: str | int
    mode: str | int
    if_exists: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.startswith("/") or "\x00" in self.path:
            raise ValueError("path_mode path must be absolute")
        if self.path == "/" or ".." in Path(self.path).parts:
            raise ValueError("path_mode path must name a path without dot-dot components")
        if not isinstance(self.if_exists, str) or self.if_exists not in {"must-match", "absent"}:
            raise ValueError("path_mode if_exists must be 'must-match' or 'absent'")
        _resolve_id(self.owner, group=False)
        _resolve_id(self.group, group=True)
        _mode_value(self.mode)


def _path_observation(root: Path, expected: PathMode) -> dict[str, Any]:
    target = root / expected.path.lstrip("/")
    try:
        _fs.safe_components(root, target)
        info = target.lstat()
    except FileNotFoundError:
        if expected.if_exists == "must-match":
            raise PlanRefused(
                [Problem("check.path_mode.missing", f"expected {expected.path} to exist", path=expected.path)]
            )
        return {"exists": False, "fingerprint": "absent"}
    except OSError as exc:
        raise PlanRefused(
            [Problem("check.path_mode.io", f"cannot inspect {expected.path}: {exc}", path=expected.path)]
        ) from exc
    actual = {
        "exists": True,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }
    actual["fingerprint"] = _fingerprint(actual)
    if expected.if_exists == "absent":
        raise PlanRefused(
            [Problem("check.path_mode.present", f"expected {expected.path} to be absent", path=expected.path)]
        )
    if (
        actual["uid"] != _resolve_id(expected.owner, group=False)
        or actual["gid"] != _resolve_id(expected.group, group=True)
        or actual["mode"] != _mode_value(expected.mode)
    ):
        raise PlanRefused(
            [Problem("check.path_mode.mismatch", f"owner, group, or mode differs for {expected.path}", path=expected.path)]
        )
    return actual


def _exec_observation(argv: tuple[str, ...], expect_exit: int, timeout: float) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PlanRefused(
            [Problem("check.exec.timeout", f"check command timed out after {timeout:g}s: {argv[0]}")]
        ) from exc
    except OSError as exc:
        raise PlanRefused(
            [Problem("check.exec.error", f"cannot run check command {argv[0]}: {exc}")]
        ) from exc
    result_record: dict[str, Any] = {
        "argv": list(argv),
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8", errors="replace")).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8", errors="replace")).hexdigest(),
    }
    result_record["fingerprint"] = _fingerprint(result_record)
    if result.returncode != expect_exit:
        output = (result.stderr or result.stdout).strip().splitlines()
        diagnostic = output[0][:240] if output else "no diagnostic"
        raise PlanRefused(
            [Problem("check.exec.exit", f"check command exited {result.returncode}, expected {expect_exit}: {argv[0]}: {diagnostic}")]
        )
    return result_record


@dataclass(frozen=True)
class Check:
    """A read-only exec or path-mode predicate; it never changes host state."""

    state: str
    exec: tuple[str, ...] | None = None
    expect_exit: int | None = None
    timeout: float | None = None
    path_mode: PathMode | Mapping[str, Any] | None = None
    kind: ClassVar[str] = "check"

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("check state must be 'present' or 'absent'")
        if (self.exec is None) == (self.path_mode is None):
            raise ValueError("check requires exactly one of exec or path_mode")
        if self.exec is not None:
            if not isinstance(self.exec, tuple) or not self.exec or any(
                not isinstance(arg, str) or not arg or "\x00" in arg for arg in self.exec
            ):
                raise ValueError("check exec must be a non-empty argv tuple")
            if isinstance(self.expect_exit, bool) or not isinstance(self.expect_exit, int):
                raise ValueError("check exec requires integer expect_exit")
            if (
                isinstance(self.timeout, bool)
                or not isinstance(self.timeout, (int, float))
                or self.timeout <= 0
                or not math.isfinite(self.timeout)
            ):
                raise ValueError("check exec requires positive finite timeout")
        elif self.expect_exit is not None or self.timeout is not None:
            raise ValueError("expect_exit and timeout apply only to exec checks")
        if isinstance(self.path_mode, Mapping):
            expected = self.path_mode
            required = {"path", "owner", "group", "mode", "if_exists"}
            unknown = set(expected) - required
            missing = required - set(expected)
            if unknown:
                raise ValueError(f"unknown path_mode key(s): {', '.join(sorted(unknown))}")
            if missing:
                raise ValueError(f"missing path_mode field(s): {', '.join(sorted(missing))}")
            object.__setattr__(self, "path_mode", PathMode(**expected))
        elif self.path_mode is not None and not isinstance(self.path_mode, PathMode):
            raise ValueError("path_mode must be a PathMode or table")

    def addresses(self) -> tuple[str, ...]:
        if self.exec is not None:
            value = {"exec": self.exec, "expect_exit": self.expect_exit, "timeout": self.timeout}
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
            return (f"check:exec:{digest}",)
        assert isinstance(self.path_mode, PathMode)
        return (f"check:path_mode:{self.path_mode.path}",)

    def observe(self, ctx: object) -> dict[str, dict[str, Any]]:
        address = self.addresses()[0]
        if self.state == "absent":
            result = {"skipped": True, "fingerprint": "absent"}
        elif self.exec is not None:
            assert self.expect_exit is not None and self.timeout is not None
            result = _exec_observation(self.exec, self.expect_exit, float(self.timeout))
        else:
            assert isinstance(self.path_mode, PathMode)
            result = _path_observation(Path(getattr(ctx, "root")), self.path_mode)
        return {address: result}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        address = self.addresses()[0]
        value = observed[address]
        if not isinstance(value, Mapping):
            raise ValueError("check observation must be a mapping")
        public = {key: item for key, item in value.items() if key != "fingerprint"}
        return [Change(self.kind, address, "noop", public, public, self)]

    def stage(self, ctx: object, change: Change) -> None:
        del ctx, change
        return None

    def commit(self, ctx: object, staged: object) -> None:
        del ctx, staged
        return None

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return []

    def verify(self, ctx: object) -> list[Problem]:
        try:
            self.observe(ctx)
        except PlanRefused as exc:
            return list(exc.problems)
        return []


__all__ = ["Check", "PathMode"]
