"""Planning, guarded apply, journal inspection and explicit transaction revert."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

from cli_extended.parser import CliFailure

from . import Change, Plan, PlanRefused, PostAction, Problem, Request, Resource, StalePlan
from . import _fs
from .check import Check, PathMode
from .file import File, _decode_value

_LOCK_RELATIVE = Path("run/lock/augeaswrap.lock")


@dataclass
class _Context:
    root: Path
    writer: str
    txn_id: str | None = None
    txn_dir: Path | None = None
    stage_dir: Path | None = None
    pre_dir: Path | None = None
    expected: dict[str, object] = field(default_factory=dict)


def _root_path(root: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(root)
    except TypeError as exc:
        raise CliFailure("root must be a non-empty filesystem path", exit_code=2) from exc
    if not isinstance(raw, str) or not raw:
        raise CliFailure("root must be a non-empty filesystem path", exit_code=2)
    path = Path(os.path.abspath(raw))
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError as exc:
            raise CliFailure(f"root directory does not exist: {cursor}", exit_code=2) from exc
        if stat.S_ISLNK(info.st_mode):
            raise CliFailure(f"refusing symlinked root directory: {cursor}", exit_code=2)
        if not stat.S_ISDIR(info.st_mode):
            raise CliFailure(f"root path component is not a directory: {cursor}", exit_code=2)
    return path


def _lock_path(root: Path) -> Path:
    return root / _LOCK_RELATIVE


@contextmanager
def _locked(root: Path, *, exclusive: bool) -> Iterator[None]:
    lock_path = _lock_path(root)
    if exclusive:
        _fs.ensure_directory(root, lock_path.parent)
    with _fs.lock(root, lock_path, exclusive=exclusive, create=exclusive, mode=0o600):
        yield


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observation_fingerprint(value: object) -> str:
    return _fingerprint(value)


def _validate_addresses(resource: Resource) -> tuple[str, ...]:
    if not isinstance(resource.kind, str) or not resource.kind:
        raise PlanRefused([Problem("resource.kind.invalid", "resource kind must be a non-empty string")])
    addresses = resource.addresses()
    if (
        not isinstance(addresses, tuple)
        or not addresses
        or any(not isinstance(address, str) or not address for address in addresses)
    ):
        raise PlanRefused([Problem("resource.address.invalid", f"{resource.kind} returned invalid addresses")])
    if len(set(addresses)) != len(addresses):
        raise PlanRefused([Problem("resource.address.duplicate", f"{resource.kind} returned duplicate addresses")])
    return addresses


def _resource_changes(plan_value: Plan, *, action: str | None = None) -> list[Change]:
    return [
        change
        for change in plan_value.changes
        if action is None or change.action == action
    ]


def _unique_resources(changes: Sequence[Change]) -> list[Resource]:
    seen: set[int] = set()
    resources: list[Resource] = []
    for change in changes:
        resource = change.resource
        if resource is None or id(resource) in seen:
            continue
        seen.add(id(resource))
        resources.append(resource)
    return resources


def _post_action_bindings(changes: Sequence[Change]) -> list[tuple[PostAction, tuple[str, ...]]]:
    grouped: dict[int, tuple[Resource, list[Change]]] = {}
    for change in changes:
        resource = change.resource
        if resource is None:
            raise ValueError(f"change {change.address} has no resource")
        grouped.setdefault(id(resource), (resource, []))[1].append(change)
    actions: list[tuple[PostAction, list[str]]] = []
    seen: dict[tuple[str, tuple[str, ...]], tuple[float, int]] = {}
    for resource, resource_changes in grouped.values():
        declared = resource.post_actions(resource_changes)
        if not isinstance(declared, list) or any(not isinstance(action, PostAction) for action in declared):
            raise ValueError(f"{resource.kind}.post_actions must return list[PostAction]")
        for action in declared:
            key = (action.name, action.argv)
            if key in seen and seen[key][0] != action.timeout:
                raise ValueError(f"conflicting timeouts for post-action {action.name}")
            if key not in seen:
                seen[key] = (action.timeout, len(actions))
                actions.append((action, []))
            addresses = actions[seen[key][1]][1]
            for change in resource_changes:
                if change.address not in addresses:
                    addresses.append(change.address)
    return [(action, tuple(addresses)) for action, addresses in actions]


def _post_actions(changes: Sequence[Change]) -> list[PostAction]:
    return [action for action, _addresses in _post_action_bindings(changes)]


def _deduplicate_changes(changes: list[Change]) -> list[Change]:
    seen: set[str] = set()
    result: list[Change] = []
    for change in changes:
        if change.address in seen:
            raise PlanRefused([Problem("resource.address.duplicate", f"request contains duplicate address {change.address}")])
        seen.add(change.address)
        result.append(change)
    return result


def _plan_locked(req: Request, root: Path) -> Plan:
    changes: list[Change] = []
    observations: dict[str, Any] = {}
    preconditions: dict[str, str | None] = {}
    requested: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="augeaswrap-plan-") as scratch:
        ctx = _Context(root, req.writer, stage_dir=Path(scratch))
        for resource in req.resources:
            addresses = _validate_addresses(resource)
            overlap = requested.intersection(addresses)
            if overlap:
                raise PlanRefused([Problem("resource.address.duplicate", f"request contains duplicate address {sorted(overlap)[0]}")])
            requested.update(addresses)
            try:
                observed = resource.observe(ctx)
                if not isinstance(observed, Mapping):
                    raise ValueError(f"{resource.kind}.observe must return a mapping by address")
                missing = set(addresses) - set(observed)
                extra = set(observed) - set(addresses)
                if missing:
                    raise ValueError(f"{resource.kind}.observe omitted {sorted(missing)[0]}")
                if extra:
                    raise ValueError(f"{resource.kind}.observe returned unknown address {sorted(extra)[0]}")
                for address in addresses:
                    value = observed[address]
                    observations[address] = value
                    preconditions[address] = _observation_fingerprint(value)
                resource_changes = resource.diff(ctx, observed)
                if not isinstance(resource_changes, list) or any(not isinstance(item, Change) for item in resource_changes):
                    raise ValueError(f"{resource.kind}.diff must return list[Change]")
                returned = [item.address for item in resource_changes]
                if len(returned) != len(addresses) or set(returned) != set(addresses):
                    raise ValueError(f"{resource.kind}.diff must return one change or noop per address")
                for item in resource_changes:
                    if item.kind != resource.kind:
                        raise ValueError(f"{resource.kind}.diff returned a mismatched change kind")
                    if item.resource is not None and item.resource is not resource:
                        raise ValueError(f"{resource.kind}.diff returned a change for another resource")
                    _fingerprint({"before": item.before, "after": item.after, "details": item.details})
                    changes.append(item if item.resource is not None else replace(item, resource=resource))
            except PlanRefused:
                raise
            except Exception as exc:
                raise PlanRefused([Problem("resource.plan.failed", f"planning {resource.kind} failed: {exc}", path=addresses[0])]) from exc

        changes = _deduplicate_changes(changes)
        for change in _resource_changes(Plan(req, str(root), tuple(changes)), action="change"):
            try:
                change.resource.stage(ctx, change)
            except PlanRefused:
                raise
            except Exception as exc:
                raise PlanRefused([Problem("stage.failed", f"staging failed for {change.address}: {exc}", path=change.address)]) from exc
        try:
            _post_actions(_resource_changes(Plan(req, str(root), tuple(changes)), action="change"))
        except Exception as exc:
            raise PlanRefused([Problem("post_action.invalid", str(exc))]) from exc
    return Plan(
        req,
        str(root),
        tuple(changes),
        (),
        tuple(sorted(preconditions.items())),
        observations,
    )


def _normalize_request(value: Request | Mapping[str, Any] | str | os.PathLike[str]) -> Request:
    if isinstance(value, Request):
        return value
    if isinstance(value, Mapping) or isinstance(value, (str, os.PathLike)):
        return load_request(value)
    raise TypeError("request must be a Request, mapping, or TOML path")


def plan(
    request: Request | Mapping[str, Any] | str | os.PathLike[str],
    root: str | os.PathLike[str] = "/",
) -> Plan:
    """Observe requested resources and return changes with preconditions."""

    req = _normalize_request(request)
    host_root = _root_path(root)
    with _locked(host_root, exclusive=False):
        return _plan_locked(req, host_root)


def _check_preconditions(root: Path, plan_value: Plan) -> None:
    expected = dict(plan_value.preconditions)
    ctx = _Context(root, plan_value.request.writer)
    resources: list[tuple[Resource, tuple[str, ...]]] = []
    addresses: set[str] = set()
    for resource in plan_value.request.resources:
        try:
            resource_addresses = _validate_addresses(resource)
        except Exception as exc:
            raise StalePlan(f"resource addresses changed after plan: {exc}") from exc
        if addresses.intersection(resource_addresses):
            raise StalePlan("request contains duplicate addresses after plan")
        addresses.update(resource_addresses)
        resources.append((resource, resource_addresses))
    if set(expected) != addresses:
        raise StalePlan("plan preconditions do not cover the request addresses; plan again before applying")
    for resource, resource_addresses in resources:
        try:
            observed = resource.observe(ctx)
            if not isinstance(observed, Mapping) or set(observed) != set(resource_addresses):
                raise ValueError("resource did not return exactly its planned addresses")
        except Exception as exc:
            address = resource_addresses[0]
            raise StalePlan(f"{address} could not be re-observed after plan: {exc}") from exc
        for address in resource_addresses:
            try:
                current = _observation_fingerprint(observed[address])
            except (TypeError, ValueError) as exc:
                raise StalePlan(f"{address} returned an invalid observation after plan: {exc}") from exc
            if current != expected[address]:
                raise StalePlan(f"{address} changed after plan; plan again before applying")


def _run_post_action(root: Path, action: PostAction) -> str:
    if root != Path("/"):
        return f"{action.name}: skipped(root)"
    try:
        result = subprocess.run(
            list(action.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=action.timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(str(exc)) from exc
    if result.returncode:
        diagnostic = (result.stderr or result.stdout).strip().splitlines()
        detail = diagnostic[0] if diagnostic else "no diagnostic"
        raise RuntimeError(f"exit {result.returncode}: {detail}")
    return f"{action.name}: done"


def plan_action_dicts(plan_value: Plan) -> list[dict[str, Any]]:
    """Return declared argv and fake-root execution status for the plan."""

    skipped = Path(plan_value.root) != Path("/")
    return [
        {
            "name": action.name,
            "argv": list(action.argv),
            "timeout": action.timeout,
            "status": "skipped(root)" if skipped else "pending",
        }
        for action in _post_actions(_resource_changes(plan_value, action="change"))
    ]


def render_plan(plan_value: Plan) -> str:
    """Render a stable plan, including unified diffs for changed files."""

    lines = [f"augeaswrap plan writer={plan_value.request.writer} root={plan_value.root}"]
    if not plan_value.changes:
        lines.append("  no resources")
    for change in plan_value.changes:
        lines.append(f"  {change.action:6} {change.address}")
        for detail in change.details:
            lines.append(f"         {detail}")
        if change.kind == "file" and change.action == "change":
            before = plan_value.observed.get(change.address, {}) if isinstance(plan_value.observed, Mapping) else {}
            old_bytes = _decode_value(before) if isinstance(before, Mapping) and before.get("exists") else b""
            if isinstance(change.resource, File):
                new_bytes = change.resource.text.encode("utf-8") if change.resource.state == "present" and change.resource.text is not None else b""
                path = change.resource.path
            else:
                image = getattr(change.resource, "image", {})
                new_bytes = _decode_value(image) if isinstance(image, Mapping) and image.get("exists") else b""
                path = getattr(change.resource, "path", change.address)
            diff = difflib.unified_diff(
                old_bytes.decode("utf-8", errors="replace").splitlines(True),
                new_bytes.decode("utf-8", errors="replace").splitlines(True),
                fromfile=f"a{path}",
                tofile=f"b{path}",
            )
            lines.extend("         " + row.rstrip("\n") for row in diff)
    for action in _post_actions(_resource_changes(plan_value, action="change")):
        suffix = "skipped(root)" if Path(plan_value.root) != Path("/") else "pending"
        lines.append(f"  post-action {action.name}: {action.argv!r} ({suffix})")
    lines.append("changes are pending" if plan_value.has_changes else "no changes")
    return "\n".join(lines) + "\n"


def _resource_tables(data: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    raw = data.get(kind, [])
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError(f"{kind} must be an array of tables")
    return list(raw)


def _require_keys(table: Mapping[str, Any], *, allowed: set[str], required: set[str], where: str) -> None:
    keys = set(table)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{where} keys must be strings")
    unknown = keys - allowed
    missing = required - set(table)
    if unknown:
        raise ValueError(f"unknown {where} key(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing {where} field(s): {', '.join(sorted(missing))}")


def _parse_file(table: Mapping[str, Any]) -> File:
    allowed = {"path", "state", "text", "mode", "owner", "group", "create_only", "validate"}
    _require_keys(table, allowed=allowed, required={"path", "state"}, where="file")
    state = table["state"]
    required = {"path", "state", "text", "mode"} if state == "present" else {"path", "state"}
    _require_keys(table, allowed=allowed, required=required, where="file")
    validate = table.get("validate")
    if isinstance(validate, list):
        validate = tuple(validate)
    return File(
        path=table["path"],
        state=state,
        text=table.get("text"),
        mode=table.get("mode"),
        owner=table.get("owner"),
        group=table.get("group"),
        create_only=table.get("create_only", False),
        validate=validate,
    )


def _parse_path_mode(value: object) -> PathMode:
    if not isinstance(value, Mapping):
        raise ValueError("check path_mode must be a table")
    required = {"path", "owner", "group", "mode", "if_exists"}
    _require_keys(value, allowed=required, required=required, where="path_mode")
    return PathMode(**value)


def _parse_check(table: Mapping[str, Any]) -> Check:
    allowed = {"state", "exec", "expect_exit", "timeout", "path_mode"}
    _require_keys(table, allowed=allowed, required={"state"}, where="check")
    if "exec" in table and "path_mode" in table:
        raise ValueError("check must specify exactly one of exec or path_mode")
    if "exec" in table:
        _require_keys(table, allowed=allowed, required={"state", "exec", "expect_exit", "timeout"}, where="check")
        argv = table["exec"]
        if not isinstance(argv, list):
            raise ValueError("check exec must be an argv array")
        return Check(table["state"], tuple(argv), table["expect_exit"], table["timeout"])
    if "path_mode" not in table:
        raise ValueError("check requires exec or path_mode")
    if "expect_exit" in table or "timeout" in table:
        raise ValueError("expect_exit and timeout apply only to exec checks")
    return Check(table["state"], path_mode=_parse_path_mode(table["path_mode"]))


def load_request(source: str | os.PathLike[str] | Mapping[str, Any]) -> Request:
    """Load strict ``augeaswrap = 1`` TOML or a mapping into a Request."""

    if isinstance(source, Mapping):
        data = dict(source)
    else:
        path = Path(source)
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot load Augeaswrap request {path}: {exc}") from exc
    allowed = {"augeaswrap", "writer", "file", "check"}
    _require_keys(data, allowed=allowed, required={"augeaswrap", "writer"}, where="request")
    if type(data["augeaswrap"]) is not int or data["augeaswrap"] != 1:
        raise ValueError("Augeaswrap schema must be integer 1")
    resources: list[Resource] = []
    resources.extend(_parse_file(table) for table in _resource_tables(data, "file"))
    resources.extend(_parse_check(table) for table in _resource_tables(data, "check"))
    try:
        return Request(data["writer"], tuple(resources))
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{ " + ", ".join(f"{_toml_value(str(key))} = {_toml_value(item)}" for key, item in value.items()) + " }"
    raise ValueError(f"cannot encode {type(value).__name__} as Augeaswrap TOML")


def request_to_toml(request: Request) -> str:
    """Serialize a normalized request using only the Python standard library."""

    lines = ["augeaswrap = 1", f"writer = {_toml_value(request.writer)}"]
    for resource in request.resources:
        if isinstance(resource, File):
            values: dict[str, Any] = {"path": resource.path, "state": resource.state}
            if resource.text is not None:
                values["text"] = resource.text
            if resource.mode is not None:
                values["mode"] = resource.mode
            if resource.owner is not None:
                values["owner"] = resource.owner
            if resource.group is not None:
                values["group"] = resource.group
            if resource.create_only:
                values["create_only"] = True
            if resource.validate is not None:
                values["validate"] = resource.validate
        elif isinstance(resource, Check):
            values = {"state": resource.state}
            if resource.exec is not None:
                values.update({"exec": resource.exec, "expect_exit": resource.expect_exit, "timeout": resource.timeout})
            else:
                assert isinstance(resource.path_mode, PathMode)
                values["path_mode"] = asdict(resource.path_mode)
        else:
            raise ValueError(f"no TOML serializer is registered for {resource.kind!r}")
        lines.extend(("", f"[[{resource.kind}]]"))
        lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
    return "\n".join(lines) + "\n"


from .transactions import apply, journal, preview_revert, revert


__all__ = [
    "apply",
    "journal",
    "load_request",
    "plan",
    "preview_revert",
    "revert",
    "request_to_toml",
    "render_plan",
    "plan_action_dicts",
]
