"""Endpoint resource adapter for the frozen ``augeaswrap.endpoints`` format."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping

from cli_extended.parser import CliFailure

from . import Change, PlanRefused, PostAction, Problem
from . import _fs
from . import _resource_files as file_ops
from . import endpoints
from .kinds import KindSpec, RevertHooks, register_kind, require_keys


@dataclass(frozen=True)
class EndpointStage:
    name: str
    state: str
    entries: Mapping[str, str]
    desired_files: Mapping[str, str | None]


def _fragment_path(name: str) -> str:
    return f"/etc/host-endpoints.d/{name}.env"


def _managed_paths() -> tuple[str, ...]:
    return (
        "/etc/host-endpoints.env",
        "/etc/environment",
        "/etc/profile.d/host-endpoints.sh",
    )


def _observe(root: Path, name: str) -> dict[str, Any]:
    files: dict[str, Any] = {
        path: file_ops.observe_file(root, path)
        for path in (*_managed_paths(), _fragment_path(name), "/etc/host-endpoints.d/.lock", "/etc/group")
    }
    directory = root / "etc/host-endpoints.d"
    names: list[str] = []
    _fs.safe_components(root, directory)
    try:
        info = directory.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("/etc/host-endpoints.d is not a directory")
        names = sorted(child.name for child in directory.iterdir())
        for child in names:
            if not child.endswith(".env") or child == f"{name}.env":
                continue
            path = f"/etc/host-endpoints.d/{child}"
            files[path] = file_ops.observe_file(root, path)
    fragments, problems = endpoints._load_fragments(root, skip_owner=name, tolerate_invalid=True)
    serialized = {
        owner: {"writer": fragment.writer, "entries": dict(fragment.entries)}
        for owner, fragment in fragments.items()
    }
    return {
        "files": files,
        "fragment_names": names,
        "fragments": serialized,
        "load_problems": [problem.as_dict() for problem in problems],
    }


def _bytes(image: object) -> bytes:
    if not isinstance(image, Mapping) or not image.get("exists"):
        return b""
    encoded = image.get("data_b64")
    if not isinstance(encoded, str):
        raise ValueError("endpoint observation has no file bytes")
    return base64.b64decode(encoded.encode("ascii"), validate=True)


def _resolve_entries(ctx: object, before: Mapping[str, Any], entries: tuple[tuple[str, str], ...]) -> dict[str, str]:
    planned = getattr(ctx, "planned_gids", {})
    existing: dict[str, int] = {}
    seen: set[str] = set()
    group_data = _bytes(before.get("files", {}).get("/etc/group"))
    if group_data:
        for number, line in enumerate(group_data.decode("utf-8").splitlines(), start=1):
            if not line:
                continue
            fields = line.split(":")
            if len(fields) != 4 or not fields[2].isdigit():
                raise PlanRefused([Problem("endpoint.gid.group-file", f"invalid /etc/group line {number}", path="/etc/group")])
            if fields[0] in seen:
                raise PlanRefused([Problem("endpoint.gid.group-file", f"duplicate group {fields[0]} in /etc/group", path="/etc/group")])
            seen.add(fields[0])
            existing[fields[0]] = int(fields[2])
    result: dict[str, str] = {}
    planned_absent = getattr(ctx, "planned_group_absent", set())
    for key, value in entries:
        if value.startswith("@gid:"):
            group = value[len("@gid:") :]
            if not group:
                raise PlanRefused([Problem("endpoint.gid.invalid", f"invalid @gid value for {key}", key=key)])
            if isinstance(planned_absent, set) and group in planned_absent:
                raise PlanRefused([Problem("endpoint.gid.removed", f"cannot publish @gid:{group}: the same request removes that group", path="/etc/group", key=key)])
            gid = planned.get(group) if isinstance(planned, Mapping) else None
            if gid is None:
                gid = existing.get(group)
            if isinstance(gid, bool) or not isinstance(gid, int) or gid < 0:
                raise PlanRefused([Problem("endpoint.gid.unresolved", f"cannot resolve @gid:{group}; declare its GID in an earlier sysusers resource or use an existing group", path="/etc/group", key=key)])
            result[key] = str(gid)
        else:
            result[key] = value
    return result


def _fragments(value: object) -> dict[str, endpoints.EndpointFragment]:
    if not isinstance(value, Mapping):
        raise ValueError("endpoint fragment snapshot is invalid")
    result: dict[str, endpoints.EndpointFragment] = {}
    for owner, item in value.items():
        if not isinstance(owner, str) or not isinstance(item, Mapping):
            raise ValueError("endpoint fragment snapshot has an invalid entry")
        writer, entries = item.get("writer"), item.get("entries")
        if not isinstance(writer, str) or not isinstance(entries, Mapping):
            raise ValueError("endpoint fragment snapshot has invalid values")
        result[owner] = endpoints.EndpointFragment(owner, writer, dict(entries))
    return result


def _desired_files(root: Path, rendered: Mapping[Path, tuple[bytes, int] | None]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for path, value in rendered.items():
        absolute = "/" + path.relative_to(root).as_posix()
        result[absolute] = None if value is None else base64.b64encode(value[0]).decode("ascii")
    return result


def _same_files(before: Mapping[str, Any], desired: Mapping[str, str | None]) -> bool:
    files = before.get("files")
    if not isinstance(files, Mapping):
        return False
    for path, encoded in desired.items():
        image = files.get(path)
        if encoded is None:
            if isinstance(image, Mapping) and image.get("exists"):
                return False
        elif not isinstance(image, Mapping) or not image.get("exists") or image.get("data_b64") != encoded:
            return False
    return True


@dataclass(frozen=True)
class Endpoint:
    name: str
    state: str
    entries: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[str] = "endpoint"

    def __post_init__(self) -> None:
        try:
            endpoints.validate_owner(self.name)
        except ValueError as exc:
            raise ValueError(f"endpoint name is invalid: {exc}") from exc
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("endpoint state must be 'present' or 'absent'")
        if self.state == "present":
            if not self.entries:
                raise ValueError("present endpoint requires entries")
            if any(not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(item, str) for item in pair) for pair in self.entries):
                raise ValueError("endpoint entries must be (key, value) pairs")
            if len({key for key, _value in self.entries}) != len(self.entries):
                raise ValueError("endpoint request contains a duplicate key")
        elif self.entries:
            raise ValueError("absent endpoint must not have entries")

    def addresses(self) -> tuple[str, ...]:
        return (f"endpoint:{self.name}",)

    def observe(self, ctx: object) -> dict[str, object]:
        root = Path(getattr(ctx, "root"))
        return {self.addresses()[0]: _observe(root, self.name)}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping):
            raise ValueError("endpoint observation is invalid")
        root = Path(getattr(ctx, "root"))
        resolved = _resolve_entries(ctx, before, self.entries) if self.state == "present" else {}
        if self.state == "present":
            problems = endpoints.plan(self.name, resolved, root=root)
            if problems:
                raise PlanRefused(list(problems))
            current_env = _bytes(before.get("files", {}).get("/etc/environment"))
            rendered = endpoints.render(
                self.name,
                resolved,
                writer=getattr(ctx, "writer"),
                current_environment=current_env,
                existing_fragments=_fragments(before.get("fragments")),
                root=root,
            )
            desired = _desired_files(root, rendered)
        else:
            fragment = before.get("files", {}).get(_fragment_path(self.name))
            if not isinstance(fragment, Mapping) or not fragment.get("exists"):
                desired = {}
            else:
                current_env = _bytes(before.get("files", {}).get("/etc/environment"))
                rendered = endpoints.render_withdraw(
                    self.name,
                    current_environment=current_env,
                    existing_fragments=_fragments(before.get("fragments")),
                    root=root,
                )
                desired = _desired_files(root, rendered)
        action = "noop" if _same_files(before, desired) else "change"
        details = (f"endpoint {self.state}: {self.name}",) if action == "change" else ()
        after = {
            "state": self.state,
            "entries": resolved,
            "desired_files": desired,
            "writer": getattr(ctx, "writer"),
        }
        return [Change(self.kind, address, action, dict(before), after, self, details)]

    def stage(self, ctx: object, change: Change) -> EndpointStage:
        del ctx
        return EndpointStage(self.name, change.after["state"], dict(change.after["entries"]), dict(change.after["desired_files"]))

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, EndpointStage):
            raise TypeError("endpoint commit requires a staged endpoint")
        root = Path(getattr(ctx, "root"))
        if root == Path("/") and staged.state == "present":
            # Publish after sysusers post-actions so an @gid value never becomes
            # visible before its group exists.
            return _postimage(root, self.name)
        if staged.state == "present":
            endpoints.publish(self.name, staged.entries, writer=getattr(ctx, "writer"), root=root)
        else:
            endpoints.withdraw(self.name, root=root)
        return _postimage(root, self.name)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        actions: list[PostAction] = []
        for change in changes:
            if change.action != "change" or change.after.get("state") != "present":
                continue
            writer = change.after.get("writer")
            entries = change.after.get("entries")
            if not isinstance(writer, str) or not isinstance(entries, Mapping):
                raise ValueError("endpoint publish action is missing its writer or entries")
            argv = [sys.executable, "-m", "augeaswrap", "endpoints", "publish", "--owner", self.name, "--writer", writer]
            for key, value in entries.items():
                argv.extend(("--entry", f"{key}={value}"))
            actions.append(PostAction(f"endpoint publish {self.name}", tuple(argv), 30.0))
        return actions

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        expected = getattr(ctx, "expected", {}).get(address)
        if not isinstance(expected, EndpointStage):
            return [Problem("endpoint.verify.expected", f"no staged endpoint recorded for {self.name}", owner=self.name)]
        if Path(getattr(ctx, "root")) != Path("/") and not expected.desired_files:
            return []
        actual = _observe(Path(getattr(ctx, "root")), self.name)
        if not _same_files(actual, expected.desired_files):
            return [Problem("endpoint.verify.failed", f"endpoint files differ after {expected.state} for {self.name}", owner=self.name)]
        if expected.state == "present":
            return endpoints.verify(self.name, expected.entries, root=getattr(ctx, "root"))
        return []


@dataclass(frozen=True)
class EndpointRestore:
    name: str
    data: bytes | None
    actions: tuple[PostAction, ...]
    kind: ClassVar[str] = "endpoint"

    def addresses(self) -> tuple[str, ...]:
        return (f"endpoint:{self.name}",)

    def observe(self, ctx: object) -> dict[str, object]:
        return {self.addresses()[0]: _postimage(Path(getattr(ctx, "root")), self.name)}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        del ctx
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping):
            raise ValueError("endpoint restore observation is invalid")
        current = before.get("fragment")
        same = (self.data is None and isinstance(current, Mapping) and not current.get("exists")) or (
            self.data is not None and isinstance(current, Mapping) and current.get("data_b64") == base64.b64encode(self.data).decode("ascii")
        )
        after = {"data_b64": None if self.data is None else base64.b64encode(self.data).decode("ascii")}
        return [Change(self.kind, address, "noop" if same else "change", dict(before), after, self)]

    def stage(self, ctx: object, change: Change) -> EndpointStage:
        del ctx, change
        data = {} if self.data is None else {"fragment": base64.b64encode(self.data).decode("ascii")}
        return EndpointStage(self.name, "restore", data, {})

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, EndpointStage):
            raise TypeError("endpoint restore requires a staged fragment")
        _restore_fragment(Path(getattr(ctx, "root")), self.name, self.data)
        return _postimage(Path(getattr(ctx, "root")), self.name)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        del changes
        return list(self.actions)

    def verify(self, ctx: object) -> list[Problem]:
        path = _fragment_path(self.name)
        actual = file_ops.observe_file(Path(getattr(ctx, "root")), path)
        expected = "absent" if self.data is None else hashlib.sha256(self.data).hexdigest()
        current = "absent" if not actual.get("exists") else actual.get("sha256")
        if current == expected:
            return []
        return [Problem("endpoint.revert.verify", f"previous fragment bytes were not restored for {self.name}", path=path)]


def _restore_fragment(root: Path, name: str, data: bytes | None) -> None:
    endpoints._ensure_publish_directories(root)
    problems = endpoints._target_symlink_problems(root)
    if problems:
        raise CliFailure(endpoints._format_problems("restore", problems), exit_code=2)
    fragments, _ignored = endpoints._load_fragments(root, skip_owner=name, tolerate_invalid=True)
    restored: endpoints.EndpointFragment | None = _decode_fragment(name, data) if data is not None else None
    if restored is not None:
        fragments[name] = restored
    fragment_path = root / _fragment_path(name).lstrip("/")
    files: dict[Path, tuple[bytes, int] | None] = {fragment_path: None if data is None else (data, 0o644)}
    aggregate_path, environment_path, profile_path = endpoints._managed_paths(root)
    aggregate = endpoints._aggregate_bytes(fragments)
    keys = {key for fragment in fragments.values() for key in fragment.entries}
    environment = endpoints._environment_bytes(root)
    files.update(
        {
            aggregate_path: (aggregate, 0o644),
            environment_path: (endpoints.render_block(environment, aggregate, keys), 0o644),
            profile_path: (endpoints._PROFILE, 0o644),
        }
    )
    endpoints._apply_plan(root, files)


def _postimage(root: Path, name: str) -> dict[str, Any]:
    """The fragment is the endpoint address; generated shared files are recomputed."""

    return {"fragment": file_ops.observe_file(root, _fragment_path(name))}


def _decode_fragment(name: str, data: bytes) -> endpoints.EndpointFragment | None:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    if len(lines) < 4 or lines[0].encode("ascii") != endpoints._VERSION or lines[1] != f"# owner={name}" or not lines[2].startswith("# writer="):
        return None
    writer = lines[2][len("# writer=") :]
    entries: dict[str, str] = {}
    for line in lines[3:]:
        key, separator, value = line.partition("=")
        if not separator or endpoints._KEY_RE.fullmatch(key) is None or endpoints._VALUE_RE.fullmatch(value) is None or endpoints._is_reserved(key) or key in entries:
            return None
        entries[key] = value
    fragment = endpoints.EndpointFragment(name, writer, entries)
    return fragment if endpoints._fragment_bytes(fragment) == data else None


def _snapshot(change: Change, observed: object) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise ValueError("endpoint snapshot observation is invalid")
    name = getattr(change.resource, "name", None)
    if not isinstance(name, str):
        raise ValueError("endpoint resource has no name")
    if isinstance(observed.get("fragment"), Mapping):
        image = observed["fragment"]
    else:
        files = observed.get("files")
        image = files.get(_fragment_path(name)) if isinstance(files, Mapping) else None
    if not isinstance(image, Mapping):
        raise ValueError("endpoint snapshot has no fragment image")
    return {
        "name": name,
        "exists": bool(image.get("exists")),
        "sha256": image.get("sha256") if image.get("exists") else None,
        "data_b64": image.get("data_b64") if image.get("exists") else None,
    }


def _restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> EndpointRestore:
    name, exists, encoded = record.get("name"), record.get("exists"), record.get("data_b64")
    if not isinstance(name, str) or address != f"endpoint:{name}":
        raise ValueError("endpoint pre-image name does not match its address")
    try:
        endpoints.validate_owner(name)
    except ValueError as exc:
        raise ValueError("endpoint pre-image name is invalid") from exc
    if not isinstance(exists, bool):
        raise ValueError("endpoint pre-image exists flag must be a boolean")
    if not exists:
        if encoded is not None:
            raise ValueError("absent endpoint pre-image must not contain bytes")
        return EndpointRestore(name, None, _restore_actions(name, actions))
    if not isinstance(encoded, str):
        raise ValueError("endpoint pre-image has no fragment bytes")
    data = base64.b64decode(encoded.encode("ascii"), validate=True)
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise ValueError("endpoint pre-image digest differs from its bytes")
    return EndpointRestore(name, data, _restore_actions(name, actions))


def _restore_actions(name: str, actions: tuple[PostAction, ...]) -> tuple[PostAction, ...]:
    """The restore commit regenerates endpoint files, so don't replay publish."""

    return tuple(action for action in actions if action.name != f"endpoint publish {name}")


def _parse(table: Mapping[str, Any]) -> Endpoint:
    allowed = {"name", "state", "entries"}
    require_keys(table, allowed=allowed, required={"name", "state"}, where="endpoint")
    state = table["state"]
    if state == "present":
        require_keys(table, allowed=allowed, required={"name", "state", "entries"}, where="endpoint")
    entries_value = table.get("entries", [])
    if not isinstance(entries_value, list) or any(not isinstance(item, Mapping) for item in entries_value):
        raise ValueError("endpoint entries must be an array of tables")
    entries: list[tuple[str, str]] = []
    for item in entries_value:
        require_keys(item, allowed={"key", "value"}, required={"key", "value"}, where="endpoint entry")
        if not isinstance(item["key"], str) or not isinstance(item["value"], str):
            raise ValueError("endpoint key and value must be strings")
        entries.append((item["key"], item["value"]))
    return Endpoint(table["name"], state, tuple(entries))


def _values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, Endpoint):
        raise ValueError("resource is not Endpoint")
    result: dict[str, Any] = {"name": resource.name, "state": resource.state}
    if resource.state == "present":
        result["entries"] = [{"key": key, "value": value} for key, value in resource.entries]
    return result


register_kind(
    KindSpec(
        kind="endpoint",
        parse=_parse,
        to_toml=_values,
        revert=RevertHooks(snapshot=_snapshot, restore=_restore),
    )
)

__all__ = ["Endpoint"]
