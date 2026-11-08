"""Whole-file snapshot, staging, commit and revert helpers for resource kinds."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import Change, PostAction, Problem
from . import _fs

_STAGE_PREFIX = ".augeaswrap-kind-stage-"
_COMMIT_PREFIX = ".augeaswrap-kind-file-"


def _fingerprint(image: Mapping[str, Any]) -> str:
    if not image.get("exists"):
        return "absent"
    value = {
        "sha256": image.get("sha256"),
        "mode": image.get("mode"),
        "uid": image.get("uid"),
        "gid": image.get("gid"),
    }
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def observe_file(root: Path, path: str) -> dict[str, Any]:
    target = root / path.lstrip("/")
    info = _fs.regular_or_missing(root, target)
    if info is None:
        return {"exists": False, "fingerprint": "absent"}
    data = target.read_bytes()
    image: dict[str, Any] = {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }
    image["fingerprint"] = _fingerprint(image)
    return image


def expected_image(data: bytes | None, before: Mapping[str, Any]) -> dict[str, Any]:
    if data is None:
        return {"exists": False, "fingerprint": "absent"}
    mode = 0o644
    uid = int(before["uid"]) if before.get("exists") else os.geteuid()
    gid = int(before["gid"]) if before.get("exists") else os.getegid()
    image: dict[str, Any] = {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mode": mode,
        "uid": uid,
        "gid": gid,
    }
    image["fingerprint"] = _fingerprint(image)
    return image


@dataclass(frozen=True)
class StagedFile:
    path: str
    expected: Mapping[str, Any]


def stage_file(ctx: object, address: str, path: str, image: Mapping[str, Any]) -> StagedFile:
    if not image.get("exists"):
        return StagedFile(path, dict(image))
    data = base64.b64decode(str(image["data_b64"]), validate=True)
    root = Path(getattr(ctx, "root"))
    stage_dir = Path(getattr(ctx, "stage_dir"))
    name = hashlib.sha256(address.encode("utf-8")).hexdigest()
    staged = stage_dir / name / Path(path).name
    staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged.write_bytes(data)
    os.chmod(staged, int(image["mode"]))
    return StagedFile(str(staged), dict(image))


def commit_file(ctx: object, path: str, staged: StagedFile) -> dict[str, Any]:
    root = Path(getattr(ctx, "root"))
    target = root / path.lstrip("/")
    expected = staged.expected
    if not expected.get("exists"):
        _fs.unlink_and_sync(root, target)
        return observe_file(root, path)
    data = base64.b64decode(str(expected["data_b64"]), validate=True)
    _fs.ensure_directory(root, target.parent)
    _fs.atomic_write(
        root,
        target,
        data,
        int(expected["mode"]),
        temp_prefix=_COMMIT_PREFIX,
        owner=(int(expected["uid"]), int(expected["gid"])),
    )
    return observe_file(root, path)


def verify_file(ctx: object, address: str, path: str, expected: object, kind: str) -> list[Problem]:
    if not isinstance(expected, StagedFile):
        return [Problem(f"{kind}.verify.expected", f"no staged image recorded for {path}", path=path)]
    actual = observe_file(Path(getattr(ctx, "root")), path)
    if _fingerprint(actual) == _fingerprint(expected.expected):
        return []
    return [Problem(f"{kind}.verify.failed", f"verification failed for {path} ({address})", path=path)]


def snapshot(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("file observation must be a mapping")
    image = observed.get("target", observed)
    if not isinstance(image, Mapping):
        raise ValueError("file observation has no target image")
    path = getattr(change.resource, "path", None)
    if not isinstance(path, str):
        raise ValueError("resource has no file path")
    exists = image.get("exists") is True
    result: dict[str, Any] = {
        "path": path,
        "exists": exists,
        "sha256": image.get("sha256") if exists else None,
        "mode": image.get("mode") if exists else None,
        "uid": image.get("uid") if exists else None,
        "gid": image.get("gid") if exists else None,
    }
    if exists:
        result["data_b64"] = image.get("data_b64")
    return result


@dataclass(frozen=True)
class RestoreFile:
    kind: str
    address: str
    path: str
    image: Mapping[str, Any]
    actions: tuple[PostAction, ...]

    def addresses(self) -> tuple[str, ...]:
        return (self.address,)

    def observe(self, ctx: object) -> dict[str, object]:
        return {self.address: observe_file(Path(getattr(ctx, "root")), self.path)}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        current = observed[self.address]
        if not isinstance(current, Mapping):
            raise ValueError("restore observation must be a mapping")
        action = "noop" if _fingerprint(current) == _fingerprint(self.image) else "change"
        return [Change(self.kind, self.address, action, dict(current), dict(self.image), self)]

    def stage(self, ctx: object, change: Change) -> StagedFile:
        del change
        return stage_file(ctx, self.address, self.path, self.image)

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, StagedFile):
            raise TypeError("restore requires a staged file image")
        return commit_file(ctx, self.path, staged)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return list(self.actions)

    def verify(self, ctx: object) -> list[Problem]:
        expected = getattr(ctx, "expected", {}).get(self.address)
        return verify_file(ctx, self.address, self.path, expected, self.kind)


def restore_file(kind: str, address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> RestoreFile:
    path = record.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or ".." in Path(path).parts
        or Path(path).as_posix() != path
    ):
        raise ValueError("pre-image file path is invalid or not normalized")
    if address != f"{kind}:{path}":
        raise ValueError("pre-image path does not match its address")
    exists = record.get("exists")
    if not isinstance(exists, bool):
        raise ValueError("pre-image exists flag must be a boolean")
    if not exists:
        image: dict[str, Any] = {"exists": False, "fingerprint": "absent"}
    else:
        mode, uid, gid = record.get("mode"), record.get("uid"), record.get("gid")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (mode, uid, gid)):
            raise ValueError("pre-image mode and ids must be non-negative integers")
        if mode > 0o7777:
            raise ValueError("pre-image mode is invalid")
        encoded = record.get("data_b64")
        if not isinstance(encoded, str):
            raise ValueError("pre-image has no file bytes")
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
        digest = hashlib.sha256(data).hexdigest()
        if digest != record.get("sha256"):
            raise ValueError("pre-image digest differs from its bytes")
        image = {
            "exists": True,
            "sha256": digest,
            "data_b64": encoded,
            "mode": mode,
            "uid": uid,
            "gid": gid,
        }
        image["fingerprint"] = _fingerprint(image)
    return RestoreFile(kind, address, path, image, actions)


__all__ = [
    "RestoreFile",
    "StagedFile",
    "commit_file",
    "expected_image",
    "observe_file",
    "restore_file",
    "snapshot",
    "stage_file",
    "verify_file",
]
