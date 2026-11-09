"""Planning, guarded apply, journal inspection and explicit transaction revert."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

from cli_extended.parser import CliFailure

from . import Change, Plan, PlanRefused, PostAction, Problem, Request, Resource, StalePlan
from . import _fs
from .kinds import is_revertible, kind_spec, registered_kinds, require_keys, resource_tables

_LOCK_RELATIVE = Path("run/lock/augeaswrap.lock")
_ACTION_PHASE_ORDER = (
    "before_commit",
    "sysusers",
    "tmpfiles",
    "daemon_reload",
    "sysctl",
    "runtime",
    "gpasswd",
    "endpoint",
)


@dataclass
class _Context:
    root: Path
    writer: str
    txn_id: str | None = None
    txn_dir: Path | None = None
    stage_dir: Path | None = None
    pre_dir: Path | None = None
    expected: dict[str, object] = field(default_factory=dict)
    planned_gids: dict[str, int] = field(default_factory=dict)
    planned_group_names: set[str] = field(default_factory=set)
    planned_group_absent: set[str] = field(default_factory=set)


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


def _action_phase(action: PostAction) -> str:
    """Return an explicit or mechanically-derived phase for an action."""

    if action.phase is not None:
        return action.phase
    argv = action.argv
    if argv[0] == "systemd-sysusers" or argv[0] == "groupdel":
        return "sysusers"
    if argv[0] == "systemd-tmpfiles":
        return "tmpfiles"
    if argv[0] == "sysctl":
        return "sysctl"
    if argv[0] == "gpasswd":
        return "gpasswd"
    if argv[0] == "systemctl" and len(argv) > 1:
        if argv[1] == "daemon-reload":
            return "daemon_reload"
        if argv[1] in {"disable", "enable", "reload", "restart", "reset-failed", "start", "stop"}:
            return "runtime"
    if argv[0] == "augeaswrap" and len(argv) > 1 and argv[1] == "endpoints":
        return "endpoint"
    raise ValueError(f"post-action {action.name!r} needs an explicit phase for argv {argv!r}")


def _ordered_post_action_bindings(
    bindings: Sequence[tuple[PostAction, tuple[str, ...]]],
) -> list[tuple[PostAction, tuple[str, ...]]]:
    """Apply the rev3 phases, preserving declaration order within each phase."""

    phase_order = {phase: index for index, phase in enumerate(_ACTION_PHASE_ORDER)}
    indexed = list(enumerate(bindings))
    post = [(index, binding) for index, binding in indexed if _action_phase(binding[0]) != "before_commit"]
    post.sort(key=lambda item: (phase_order[_action_phase(item[1][0])], item[0]))
    return [binding for _index, binding in post]


def _post_action_bindings(changes: Sequence[Change]) -> list[tuple[PostAction, tuple[str, ...]]]:
    grouped: dict[int, tuple[Resource, list[Change]]] = {}
    for change in changes:
        resource = change.resource
        if resource is None:
            raise ValueError(f"change {change.address} has no resource")
        grouped.setdefault(id(resource), (resource, []))[1].append(change)
    actions: list[tuple[PostAction, list[str]]] = []
    seen: dict[tuple[str, tuple[str, ...]], tuple[float, bool, str, int]] = {}
    for resource, resource_changes in grouped.values():
        declared = resource.post_actions(resource_changes)
        if not isinstance(declared, list) or any(not isinstance(action, PostAction) for action in declared):
            raise ValueError(f"{resource.kind}.post_actions must return list[PostAction]")
        for action in declared:
            key = (action.name, action.argv)
            phase = _action_phase(action)
            if key in seen and seen[key][0] != action.timeout:
                raise ValueError(f"conflicting timeouts for post-action {action.name}")
            if key in seen and seen[key][1] != action.pending:
                raise ValueError(f"conflicting pending declarations for post-action {action.name}")
            if key in seen and seen[key][2] != phase:
                raise ValueError(f"conflicting phases for post-action {action.name}")
            if key not in seen:
                seen[key] = (action.timeout, action.pending, phase, len(actions))
                actions.append((action, []))
            addresses = actions[seen[key][3]][1]
            for change in resource_changes:
                if change.address not in addresses:
                    addresses.append(change.address)
    return [(action, tuple(addresses)) for action, addresses in actions]


def _post_actions(changes: Sequence[Change]) -> list[PostAction]:
    bindings = _post_action_bindings(changes)
    before = [binding for binding in bindings if _action_phase(binding[0]) == "before_commit"]
    return [action for action, _addresses in (*before, *_ordered_post_action_bindings(bindings))]


def _plan_locked(req: Request, root: Path) -> Plan:
    changes: list[Change] = []
    observations: dict[str, Any] = {}
    preconditions: dict[str, str | None] = {}
    requested: set[str] = set()
    _validate_dependency_order(req)
    group_removals = {
        resource.name
        for resource in req.resources
        if resource.kind == "group" and getattr(resource, "state", None) == "absent"
    }
    with tempfile.TemporaryDirectory(prefix="augeaswrap-plan-") as scratch:
        ctx = _Context(root, req.writer, stage_dir=Path(scratch), planned_group_absent=group_removals)
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
                spec = kind_spec(resource.kind)
                if spec is not None and spec.shadow_check is not None:
                    problems = spec.shadow_check(resource, root)
                    if not isinstance(problems, list) or any(not isinstance(problem, Problem) for problem in problems):
                        raise ValueError(f"{resource.kind}.shadow_check must return list[Problem]")
                    if problems:
                        raise PlanRefused(problems)
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
    if {change.address for change in plan_value.changes} != addresses:
        raise StalePlan("plan changes do not match the request addresses; plan again before applying")
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
            stdin=subprocess.DEVNULL,
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
            "pending": action.pending,
            "phase": _action_phase(action),
            "status": "pending" if action.pending else "skipped(root)" if skipped else "pending",
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
        if change.action == "change":
            if not is_revertible(change.kind):
                lines.append("         revert: unsupported")
            spec = kind_spec(change.kind)
            if spec is not None and spec.render is not None:
                lines.extend("         " + row for row in spec.render(change, plan_value))
    for action in _post_actions(_resource_changes(plan_value, action="change")):
        suffix = "pending(recorded)" if action.pending else "skipped(root)" if Path(plan_value.root) != Path("/") else "pending"
        label = "pre-commit action" if _action_phase(action) == "before_commit" else "post-action"
        lines.append(f"  {label} {action.name}: {action.argv!r} phase={_action_phase(action)} ({suffix})")
    lines.append("changes are pending" if plan_value.has_changes else "no changes")
    return "\n".join(lines) + "\n"


def load_request(source: str | os.PathLike[str] | Mapping[str, Any]) -> Request:
    """Load strict ``augeaswrap = 1`` TOML or a mapping into a Request."""

    if isinstance(source, Mapping):
        data = dict(source)
    else:
        path = Path(source)
        try:
            raw = path.read_text(encoding="utf-8")
            _validate_toml_resource_order(raw)
            data = tomllib.loads(raw)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot load Augeaswrap request {path}: {exc}") from exc
    kinds = registered_kinds()
    require_keys(data, allowed={"augeaswrap", "writer", *kinds}, required={"augeaswrap", "writer"}, where="request")
    if type(data["augeaswrap"]) is not int or data["augeaswrap"] != 1:
        raise ValueError("Augeaswrap schema must be integer 1")
    resources: list[Resource] = []
    for kind in data:
        if kind not in kinds:
            continue
        spec = kind_spec(kind)
        assert spec is not None
        resources.extend(spec.parse(table) for table in resource_tables(data, kind))
    try:
        return Request(data["writer"], tuple(resources))
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        # json.dumps escapes C0 controls; TOML also forbids a raw DEL, which the loader refuses.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
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
    seen: set[str] = set()
    previous: str | None = None
    for resource in request.resources:
        if resource.kind != previous and resource.kind in seen:
            raise ValueError(
                f"cannot serialize interleaved {resource.kind!r} resources as TOML array tables; "
                "keep each kind contiguous to preserve request order"
            )
        seen.add(resource.kind)
        previous = resource.kind
        spec = kind_spec(resource.kind)
        if spec is None:
            raise ValueError(f"no TOML serializer is registered for {resource.kind!r}")
        values = spec.to_toml(resource)
        lines.extend(("", f"[[{resource.kind}]]"))
        lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
    return "\n".join(lines) + "\n"


_RESOURCE_HEADER_RE = re.compile(
    r"^\s*\[\[\s*(?:\"(?P<double>[a-z][a-z0-9_]{0,31})\"|'(?P<single>[a-z][a-z0-9_]{0,31})'|(?P<bare>[a-z][a-z0-9_]{0,31}))\s*\]\]\s*(?:#.*)?$"
)


def _validate_toml_resource_order(source: str) -> None:
    """Reject TOML array tables whose grouped mapping would change resource order."""

    closed: set[str] = set()
    previous: str | None = None
    multiline: str | None = None
    for line in source.splitlines():
        if multiline is None:
            match = _RESOURCE_HEADER_RE.fullmatch(line)
            if match is not None:
                kind = match.group("double") or match.group("single") or match.group("bare")
                if previous is not None and kind != previous:
                    closed.add(previous)
                if kind in closed:
                    raise ValueError(
                        f"TOML interleaves {kind!r} resources with another kind; keep each kind contiguous "
                        "to preserve request order"
                    )
                previous = kind
        multiline = _toml_multiline_state(line, multiline)


def _toml_multiline_state(line: str, multiline: str | None) -> str | None:
    """Track TOML multiline strings so table-like content is not read as a header."""

    index = 0
    while index < len(line):
        if multiline is not None:
            if multiline == '"""' and line[index] == "\\":
                index += 2
            elif line.startswith(multiline, index):
                index += 3
                multiline = None
            else:
                index += 1
            continue
        if line[index] == "#":
            break
        if line.startswith('"""', index):
            multiline = '"""'
            index += 3
        elif line.startswith("'''", index):
            multiline = "'''"
            index += 3
        elif line[index] == '"':
            index += 1
            while index < len(line):
                if line[index] == "\\":
                    index += 2
                elif line[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        elif line[index] == "'":
            end = line.find("'", index + 1)
            index = len(line) if end < 0 else end + 1
        else:
            index += 1
    return multiline


def _validate_dependency_order(request: Request) -> None:
    endpoint_withdrawals = [
        index
        for index, resource in enumerate(request.resources)
        if resource.kind == "endpoint" and getattr(resource, "state", None) == "absent"
    ]
    unit_removals = [
        index
        for index, resource in enumerate(request.resources)
        if resource.kind == "unit" and getattr(resource, "state", None) == "absent"
    ]
    if endpoint_withdrawals and unit_removals and max(endpoint_withdrawals) > min(unit_removals):
        raise PlanRefused(
            [Problem("endpoint.order", "endpoint withdrawal must precede unit removal in the request")]
        )


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
