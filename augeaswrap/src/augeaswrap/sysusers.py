"""Whole-file sysusers.d resources, membership actions and explicit group deletion."""

from __future__ import annotations

import base64
import hashlib
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping

from . import Change, PlanRefused, PostAction, Problem
from . import _resource_files as file_ops
from . import shadow
from .kinds import KindSpec, RevertHooks, register_kind, require_keys
from .shadow import TARGET_CLASSES, Finding, analyze, command_error, effective_files, observe_layers, settings

_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.conf")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,30}")
_ACCOUNT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}\$?")
_MAX_GID = (1 << 32) - 1
_RESERVED_GIDS = frozenset({65535, _MAX_GID})
_CLASS = TARGET_CLASSES["sysusers"]


def _valid_gid(value: object) -> bool:
    return type(value) is int and 0 <= value < _MAX_GID and value not in _RESERVED_GIDS


@dataclass(frozen=True)
class GroupSpec:
    name: str
    gid: int | None = None
    not_gid_of: tuple[str, ...] = ()
    not_gid: tuple[int, ...] = ()
    distinct_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _NAME_RE.fullmatch(self.name) is None:
            raise ValueError("sysusers group name is invalid")
        if self.gid is not None and not _valid_gid(self.gid):
            raise ValueError("sysusers gid must be a valid non-reserved 32-bit GID")
        for label, values in (("not_gid_of", self.not_gid_of), ("distinct_from", self.distinct_from)):
            if not isinstance(values, tuple) or any(not isinstance(item, str) or _NAME_RE.fullmatch(item) is None for item in values):
                raise ValueError(f"{label} must be a list of group names")
        if not isinstance(self.not_gid, tuple) or any(not _valid_gid(item) for item in self.not_gid):
            raise ValueError("not_gid must be a list of valid non-reserved 32-bit GIDs")


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


def _entries(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} must be an array of tables")
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} entries must be tables")
        require_keys(item, allowed={"user", "group"}, required={"user", "group"}, where=label)
        user, group = item["user"], item["group"]
        if not isinstance(user, str) or _ACCOUNT_NAME_RE.fullmatch(user) is None or not isinstance(group, str) or _NAME_RE.fullmatch(group) is None:
            raise ValueError(f"{label} user and group names are invalid")
        pair = (user, group)
        if pair in result:
            raise ValueError(f"duplicate {label} entry for {user} in {group}")
        result.append(pair)
    return tuple(result)


def _groups_value(value: object) -> tuple[GroupSpec, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("sysusers groups must be an array of tables")
    result: list[GroupSpec] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("sysusers group entries must be tables")
        allowed = {"name", "gid", "not_gid_of", "not_gid", "distinct_from"}
        require_keys(item, allowed=allowed, required={"name"}, where="sysusers group")
        for key in ("not_gid_of", "not_gid", "distinct_from"):
            if key in item and not isinstance(item[key], list):
                raise ValueError(f"sysusers {key} must be an array")
        result.append(
            GroupSpec(
                item["name"],
                item.get("gid"),
                tuple(item.get("not_gid_of", ())),
                tuple(item.get("not_gid", ())),
                tuple(item.get("distinct_from", ())),
            )
        )
    names = [item.name for item in result]
    if len(names) != len(set(names)):
        raise ValueError("sysusers request contains a duplicate group name")
    return tuple(result)


def _passwd_groups(root: Path) -> tuple[dict[str, tuple[int, tuple[str, ...]]], set[str], dict[int, list[str]]]:
    groups: dict[str, tuple[int, tuple[str, ...]]] = {}
    gids: dict[int, list[str]] = {}
    group_image = file_ops.observe_file(root, "/etc/group")
    if group_image.get("exists"):
        group_data = base64.b64decode(group_image["data_b64"], validate=True)
        for number, line in enumerate(group_data.decode("utf-8").splitlines(), start=1):
            if not line:
                continue
            parts = line.split(":")
            if len(parts) != 4 or not parts[0] or not parts[2].isdigit() or not _valid_gid(int(parts[2])):
                raise PlanRefused([Problem("sysusers.group.invalid", f"invalid /etc/group line {number}", path="/etc/group")])
            name, gid, members = parts[0], int(parts[2]), tuple(item for item in parts[3].split(",") if item)
            if name in groups:
                raise PlanRefused([Problem("sysusers.group.invalid", f"duplicate group {name!r} in /etc/group", path="/etc/group")])
            groups[name] = (gid, members)
            gids.setdefault(gid, []).append(name)
    duplicate_gids = {gid: names for gid, names in gids.items() if len(names) > 1}
    if duplicate_gids:
        gid, names = next(iter(duplicate_gids.items()))
        raise PlanRefused([Problem("sysusers.group.invalid", f"GID {gid} is shared by groups {', '.join(names)}", path="/etc/group")])
    users: set[str] = set()
    primary: dict[int, list[str]] = {}
    passwd_image = file_ops.observe_file(root, "/etc/passwd")
    if passwd_image.get("exists"):
        passwd_data = base64.b64decode(passwd_image["data_b64"], validate=True)
        for number, line in enumerate(passwd_data.decode("utf-8").splitlines(), start=1):
            if not line:
                continue
            parts = line.split(":")
            if len(parts) != 7 or not parts[0] or not parts[3].isdigit() or not _valid_gid(int(parts[3])):
                raise PlanRefused([Problem("sysusers.passwd.invalid", f"invalid /etc/passwd line {number}", path="/etc/passwd")])
            users.add(parts[0])
            primary.setdefault(int(parts[3]), []).append(parts[0])
    return groups, users, primary


def _group_lines(data: bytes) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fields = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid sysusers definition on line {number}") from exc
        if not fields:
            continue
        kind = fields[0]
        if kind in {"g", "u", "u!"}:
            if len(fields) < 2:
                raise ValueError(f"invalid sysusers group definition on line {number}")
            result.append((fields[1], " ".join(fields)))
        elif kind == "m":
            if len(fields) < 3:
                raise ValueError(f"invalid sysusers membership definition on line {number}")
            result.append((fields[2], " ".join(fields)))
        elif kind != "r":
            raise ValueError(f"unknown sysusers directive on line {number}")
    return tuple(result)


@dataclass(frozen=True)
class Sysusers:
    file: str
    state: str
    groups: tuple[GroupSpec, ...]
    members_add: tuple[tuple[str, str], ...]
    members_absent: tuple[tuple[str, str], ...]
    intent: str
    accept_shadow: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[str] = "sysusers"

    def __post_init__(self) -> None:
        if not isinstance(self.file, str) or _FILE_RE.fullmatch(self.file) is None or "/" in self.file:
            raise ValueError("sysusers file must be a single .conf filename")
        if not isinstance(self.state, str) or self.state not in {"present", "absent"}:
            raise ValueError("sysusers state must be 'present' or 'absent'")
        if not isinstance(self.groups, tuple) or not isinstance(self.members_add, tuple) or not isinstance(self.members_absent, tuple):
            raise ValueError("sysusers groups and memberships must be tuples")
        if self.state == "absent" and (self.groups or self.members_add or self.members_absent):
            raise ValueError("absent sysusers must not have groups or memberships")
        if any(not isinstance(item, GroupSpec) for item in self.groups):
            raise ValueError("sysusers groups must be GroupSpec values")
        names = [item.name for item in self.groups]
        if len(names) != len(set(names)):
            raise ValueError("sysusers request contains a duplicate group name")
        for label, pairs in (("members_add", self.members_add), ("members_absent", self.members_absent)):
            if any(
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or _ACCOUNT_NAME_RE.fullmatch(pair[0]) is None
                or not isinstance(pair[1], str)
                or _NAME_RE.fullmatch(pair[1]) is None
                for pair in pairs
            ):
                raise ValueError(f"{label} must contain user/group name pairs")
            if len(pairs) != len(set(pairs)):
                raise ValueError(f"{label} contains a duplicate user/group pair")
        if set(self.members_add) & set(self.members_absent):
            raise ValueError("membership cannot be both added and absent")
        if not isinstance(self.intent, str) or self.intent not in {"authoritative", "fallback"}:
            raise ValueError("sysusers intent must be 'authoritative' or 'fallback'")
        object.__setattr__(self, "accept_shadow", _accept_value(self.accept_shadow))

    @property
    def path(self) -> str:
        return f"/etc/sysusers.d/{self.file}"

    def addresses(self) -> tuple[str, ...]:
        return (f"sysusers:{self.path}",)

    def observe(self, ctx: object) -> dict[str, object]:
        root = Path(getattr(ctx, "root"))
        groups, users, _primary = _passwd_groups(root)
        gids = {spec.name: spec.gid if spec.gid is not None else groups.get(spec.name, (None, ()))[0] for spec in self.groups}
        return {
            self.addresses()[0]: {
                "target": file_ops.observe_file(root, self.path),
                "layers": observe_layers(root, _CLASS),
                "group": file_ops.observe_file(root, "/etc/group"),
                "passwd": file_ops.observe_file(root, "/etc/passwd"),
                "gids": gids,
                "groups": {name: {"gid": value[0], "members": list(value[1])} for name, value in groups.items()},
                "users": sorted(users),
            }
        }

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping) or not isinstance(before.get("target"), Mapping):
            raise ValueError("sysusers observation is invalid")
        data = _render(self.groups) if self.state == "present" else None
        details: list[str] = []
        if data is not None:
            resolved_gids = dict(before.get("gids", {}))
            planned_gids = getattr(ctx, "planned_gids", None)
            if isinstance(planned_gids, Mapping):
                resolved_gids.update(planned_gids)
            _check_gid_constraints(self.groups, resolved_gids, before.get("groups"))
            files, masks = effective_files(before["layers"], _CLASS, overrides={self.path: data})
            parsed = settings(files, _group_lines)
            effective_by_name: dict[str, shadow.Assignment] = {}
            for assignment in parsed:
                effective_by_name.setdefault(assignment.key, assignment)
            findings = analyze(
                target_file=self.path,
                assignments=parsed,
                target_keys=[item.name for item in self.groups],
                intent=self.intent,
                merge="first",
                accept_shadow=self.accept_shadow,
                masks=masks,
            )
            details.extend(_finding_details(findings))
            names_in_request = getattr(ctx, "planned_group_names", None)
            if isinstance(names_in_request, set):
                duplicate = names_in_request.intersection(item.name for item in self.groups)
                if duplicate:
                    name = sorted(duplicate)[0]
                    raise PlanRefused([Problem("sysusers.group.duplicate-request", f"group {name} is both removed and defined, or declared by multiple sysusers resources", path=self.path, key=name)])
                names_in_request.update(item.name for item in self.groups)
            planned = getattr(ctx, "planned_gids", None)
            if isinstance(planned, dict):
                for spec in self.groups:
                    current = before.get("groups", {}).get(spec.name) if isinstance(before.get("groups"), Mapping) else None
                    gid = current.get("gid") if isinstance(current, Mapping) else None
                    if isinstance(gid, int) and not isinstance(gid, bool):
                        planned[spec.name] = gid
                        continue
                    if spec.gid is not None:
                        effective = effective_by_name.get(spec.name)
                        fields = effective.value.split() if effective is not None else []
                        if (
                            effective is not None
                            and effective.file == self.path
                            and len(fields) >= 3
                            and fields[0] == "g"
                            and fields[2].isdigit()
                            and int(fields[2]) == spec.gid
                        ):
                            planned[spec.name] = spec.gid
        elif before["target"].get("exists"):
            details.append("irreversible: deleting the sysusers file leaves created accounts and groups in place")
        member_actions = _membership_actions(
            self, before, getattr(ctx, "planned_group_absent", ())
        )
        if member_actions:
            details.extend(f"irreversible: gpasswd {op} {user} {group}" for op, user, group in member_actions)
        image = (
            dict(before["target"])
            if self.state == "present" and not self.groups
            else file_ops.expected_image(data, before["target"])
        )
        file_changed = _fingerprint(before["target"]) != _fingerprint(image)
        current_groups = before.get("groups", {})
        needs_sysusers = self.state == "present" and bool(self.groups) and (
            file_changed
            or any(not isinstance(current_groups, Mapping) or item.name not in current_groups for item in self.groups)
        )
        if needs_sysusers:
            details.append("irreversible: systemd-sysusers may create groups; revert restores only the fragment")
        action = "change" if needs_sysusers or file_changed or member_actions else "noop"
        after = {"image": image, "file_changed": file_changed, "run_sysusers": needs_sysusers, "member_actions": [list(item) for item in member_actions]}
        return [Change(self.kind, address, action, dict(before), after, self, tuple(details))]

    def stage(self, ctx: object, change: Change) -> file_ops.StagedFile:
        staged = file_ops.stage_file(ctx, change.address, self.path, change.after["image"])
        if self.state == "present" and self.groups:
            _validate_staged(ctx, self.path, staged, change.before.get("layers"))
        return staged

    def commit(self, ctx: object, staged: object) -> dict[str, Any]:
        if not isinstance(staged, file_ops.StagedFile):
            raise TypeError("sysusers commit requires a staged file")
        return file_ops.commit_file(ctx, self.path, staged)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        actions: list[PostAction] = []
        for change in changes:
            if change.after.get("run_sysusers") and self.state == "present":
                actions.append(PostAction(f"systemd-sysusers {self.path}", ("systemd-sysusers", self.path), 30.0))
            for action in change.after.get("member_actions", []):
                op, user, group = action
                argv = ("gpasswd", "-a" if op == "add" else "-d", user, "--", group)
                actions.append(PostAction(f"gpasswd {op} {user} {group}", argv, 30.0))
        return actions

    def verify(self, ctx: object) -> list[Problem]:
        address = self.addresses()[0]
        problems = file_ops.verify_file(ctx, address, self.path, getattr(ctx, "expected", {}).get(address), self.kind)
        root = Path(getattr(ctx, "root"))
        if self.state == "present":
            try:
                layers = observe_layers(root, _CLASS)
                files, masks = effective_files(layers, _CLASS)
                parsed = settings(files, _group_lines)
                findings = analyze(
                    target_file=self.path,
                    assignments=parsed,
                    target_keys=[item.name for item in self.groups],
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
                    problems.append(Problem("sysusers.shadow.verify", f"cannot verify sysusers shadow order: {exc}", path=self.path))
        if root != Path("/") or self.state == "absent":
            return problems
        actual, users, _primary = _passwd_groups(Path("/"))
        for spec in self.groups:
            found = actual.get(spec.name)
            if found is None:
                problems.append(Problem("sysusers.group.verify", f"group {spec.name} was not created", path="/etc/group", key=spec.name))
            elif spec.gid is not None and found[0] != spec.gid:
                problems.append(Problem("sysusers.gid.verify", f"group {spec.name} has GID {found[0]}, expected {spec.gid}", path="/etc/group", key=spec.name))
        for user, group in self.members_add:
            found = actual.get(group)
            if user not in users or found is None or user not in found[1]:
                problems.append(Problem("sysusers.member.verify", f"{user} is not a member of {group}", path="/etc/group", key=group))
        for user, group in self.members_absent:
            found = actual.get(group)
            if found is not None and user in found[1]:
                problems.append(Problem("sysusers.member.verify", f"{user} remains a member of {group}", path="/etc/group", key=group))
        return problems


@dataclass(frozen=True)
class GroupRemoval:
    name: str
    state: str = "absent"
    kind: ClassVar[str] = "group"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _NAME_RE.fullmatch(self.name) is None:
            raise ValueError("group name is invalid")
        if self.state != "absent":
            raise ValueError("group only supports explicit state='absent'")

    def addresses(self) -> tuple[str, ...]:
        return (f"group:{self.name}",)

    def observe(self, ctx: object) -> dict[str, object]:
        groups, _users, primary = _passwd_groups(Path(getattr(ctx, "root")))
        current = groups.get(self.name)
        value = {"exists": current is not None, "gid": current[0] if current else None, "members": list(current[1]) if current else []}
        value["primary_users"] = list(primary.get(current[0], ())) if current else []
        return {self.addresses()[0]: value}

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list[Change]:
        address = self.addresses()[0]
        before = observed[address]
        if not isinstance(before, Mapping):
            raise ValueError("group observation is invalid")
        names = getattr(ctx, "planned_group_names", None)
        if isinstance(names, set):
            if self.name in names:
                raise PlanRefused([Problem("sysusers.group.duplicate-request", f"group {self.name} is both removed and defined in one request", path=self.name, key=self.name)])
            names.add(self.name)
        if before.get("primary_users"):
            users = ", ".join(before["primary_users"])
            raise PlanRefused([Problem("group.primary", f"cannot delete group {self.name}: it is the primary group of {users}", path=f"/etc/group", key=self.name)])
        if before.get("exists") and before.get("gid") == 0:
            raise PlanRefused([Problem("group.gid.protected", f"refusing to delete group {self.name} with GID 0", path="/etc/group", key=self.name)])
        after = {"exists": False, "gid": None, "members": [], "primary_users": []}
        action = "change" if before.get("exists") else "noop"
        details = (f"irreversible: groupdel {self.name}",) if action == "change" else ()
        return [Change(self.kind, address, action, dict(before), after, self, details)]

    def stage(self, ctx: object, change: Change) -> object:
        del ctx
        return dict(change.after)

    def commit(self, ctx: object, staged: object) -> dict[str, object]:
        del ctx
        if not isinstance(staged, Mapping):
            raise TypeError("group commit requires its staged absence image")
        return dict(staged)

    def post_actions(self, changes: list[Change]) -> list[PostAction]:
        if any(change.action == "change" for change in changes):
            return [PostAction(f"groupdel {self.name}", ("groupdel", "--", self.name), 30.0)]
        return []

    def verify(self, ctx: object) -> list[Problem]:
        if Path(getattr(ctx, "root")) != Path("/"):
            return []
        groups, _users, _primary = _passwd_groups(Path("/"))
        if self.name in groups:
            return [Problem("group.verify.failed", f"group {self.name} still exists after groupdel", path="/etc/group")]
        return []


def _fingerprint(image: Mapping[str, Any]) -> str:
    value = {key: image.get(key) for key in ("exists", "sha256", "mode", "uid", "gid")}
    return hashlib.sha256(repr(sorted(value.items())).encode("utf-8")).hexdigest()


def _render(groups: tuple[GroupSpec, ...]) -> bytes:
    if not groups:
        return b""
    lines = [f"g {item.name} {item.gid if item.gid is not None else '-'} -" for item in sorted(groups, key=lambda value: value.name)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_gid_constraints(groups: tuple[GroupSpec, ...], gids: object, actual_groups: object) -> None:
    if not isinstance(gids, Mapping) or not isinstance(actual_groups, Mapping):
        raise ValueError("resolved group map is invalid")
    resolved = dict(gids)
    for spec in groups:
        current = actual_groups.get(spec.name)
        if (
            spec.gid is not None
            and isinstance(current, Mapping)
            and isinstance(current.get("gid"), int)
            and current["gid"] != spec.gid
        ):
            raise PlanRefused([Problem("sysusers.gid.mismatch", f"existing group {spec.name} has GID {current['gid']}, requested {spec.gid}", path=spec.name)])
        if spec.gid is not None:
            resolved[spec.name] = spec.gid
    for spec in groups:
        gid = resolved.get(spec.name)
        constraints = bool(spec.not_gid_of or spec.not_gid or spec.distinct_from)
        if constraints and gid is None:
            raise PlanRefused([Problem("sysusers.gid.unresolved", f"GID for {spec.name} must be resolved before checking its constraints", path=spec.name)])
        for forbidden in spec.not_gid:
            if gid == forbidden:
                raise PlanRefused([Problem("sysusers.gid.constraint", f"GID {gid} for {spec.name} is forbidden by not_gid", path=spec.name)])
        for other_name in (*spec.not_gid_of, *spec.distinct_from):
            other_gid = resolved.get(other_name)
            if other_gid is None:
                other = actual_groups.get(other_name)
                other_gid = other.get("gid") if isinstance(other, Mapping) else None
            if other_gid is None:
                raise PlanRefused([Problem("sysusers.gid.unresolved", f"GID for referenced group {other_name} is not resolved", path=spec.name)])
            if gid == other_gid:
                raise PlanRefused([Problem("sysusers.gid.constraint", f"GID for {spec.name} must differ from {other_name}", path=spec.name)])
    for spec in groups:
        if spec.gid is None:
            continue
        for other_name, other in actual_groups.items():
            if other_name != spec.name and isinstance(other, Mapping) and other.get("gid") == spec.gid:
                raise PlanRefused(
                    [
                        Problem(
                            "sysusers.gid.in-use",
                            f"GID {spec.gid} for {spec.name} is already held by group {other_name}",
                            path="/etc/group",
                            key=spec.name,
                        )
                    ]
                )
        for other_name, other_gid in resolved.items():
            if other_name != spec.name and other_gid == spec.gid:
                raise PlanRefused(
                    [
                        Problem(
                            "sysusers.gid.in-use",
                            f"GID {spec.gid} for {spec.name} is already assigned to group {other_name}",
                            path=spec.name,
                            key=spec.name,
                        )
                    ]
                )


def _membership_actions(
    resource: Sysusers,
    before: Mapping[str, Any],
    planned_group_absent: object = (),
) -> tuple[tuple[str, str, str], ...]:
    groups = before.get("groups")
    users = before.get("users")
    if not isinstance(groups, Mapping) or not isinstance(users, (tuple, list)):
        raise ValueError("sysusers membership observation is invalid")
    user_names = set(users)
    result: list[tuple[str, str, str]] = []
    declared = {item.name for item in resource.groups}
    removing = planned_group_absent if isinstance(planned_group_absent, set) else set()
    for user, group in resource.members_add:
        if group in removing:
            raise PlanRefused(
                [
                    Problem(
                        "sysusers.member.group.removed",
                        f"cannot change membership in {group}: the same request removes that group",
                        path="/etc/group",
                        key=group,
                    )
                ]
            )
        if user not in user_names:
            raise PlanRefused([Problem("sysusers.member.user.missing", f"cannot add unknown user {user}", path="/etc/passwd", key=user)])
        current = groups.get(group) if isinstance(groups, Mapping) else None
        if current is None and group not in declared:
            raise PlanRefused([Problem("sysusers.member.group.missing", f"cannot add {user} to missing group {group}", path="/etc/group", key=group)])
        members = current.get("members", []) if isinstance(current, Mapping) else []
        if user not in members:
            result.append(("add", user, group))
    for user, group in resource.members_absent:
        if group in removing:
            raise PlanRefused(
                [
                    Problem(
                        "sysusers.member.group.removed",
                        f"cannot change membership in {group}: the same request removes that group",
                        path="/etc/group",
                        key=group,
                    )
                ]
            )
        current = groups.get(group) if isinstance(groups, Mapping) else None
        members = current.get("members", []) if isinstance(current, Mapping) else []
        if user in members:
            result.append(("remove", user, group))
    return tuple(result)


def _validate_staged(ctx: object, path: str, staged: file_ops.StagedFile, layers: object) -> None:
    if not staged.expected.get("exists"):
        return
    root = Path(getattr(ctx, "root"))
    stage_root = Path(staged.path).parent / "sysusers-root"
    files = layers.get("files") if isinstance(layers, Mapping) else None
    if not isinstance(files, Mapping):
        raise PlanRefused([Problem("sysusers.validate.layers", "sysusers layer snapshot has no file map", path=path)])
    for source, encoded in files.items():
        if source == path or encoded is None:
            continue
        if not isinstance(source, str) or not any(source.startswith(directory + "/") for directory in _CLASS.directories):
            raise PlanRefused([Problem("sysusers.validate.layers", "sysusers layer snapshot contains an invalid path", path=path)])
        if not isinstance(encoded, str):
            raise PlanRefused([Problem("sysusers.validate.layers", "sysusers layer snapshot contains invalid bytes", path=source)])
        target = stage_root / source.lstrip("/")
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded.encode("ascii"), validate=True))
    conf = stage_root / path.lstrip("/")
    conf.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    conf.write_bytes(Path(staged.path).read_bytes())
    for source in ("/etc/group", "/etc/passwd"):
        target = stage_root / source.lstrip("/")
        observed = file_ops.observe_file(root, source)
        if observed.get("exists"):
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.write_bytes(root.joinpath(source.lstrip("/")).read_bytes())
    argv = ("systemd-sysusers", "--root", str(stage_root), "--dry-run")
    try:
        result = shadow.run_command(argv, timeout=30.0)
    except Exception as exc:
        raise PlanRefused([Problem("sysusers.validate.failed", f"cannot run systemd-sysusers dry-run: {exc}", path=path)]) from exc
    message = command_error(argv, result)
    if message is not None:
        raise PlanRefused([Problem("sysusers.validate.failed", message, path=path)])
    # These are the native name and fixed-GID diagnostics relevant to a
    # rendered ``g <name> <gid-or-dash> -`` row. Generic parser phrases can
    # describe an unrelated layer and must not cause a false refusal.
    diagnostic_terms = ("not a valid", "suggested group id", "conflict with earlier configuration")
    output = "\n".join((result.stderr or "", result.stdout or ""))
    diagnostic = next(
        (line.strip() for line in output.splitlines() if any(term in line.lower() for term in diagnostic_terms)),
        None,
    )
    if diagnostic:
        raise PlanRefused(
            [Problem("sysusers.validate.failed", f"systemd-sysusers dry-run reported a diagnostic: {diagnostic}", path=path)]
        )


def _finding_details(findings: tuple[Finding, ...]) -> list[str]:
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        raise PlanRefused([Problem(item.code, item.message, path=item.file, key=item.key) for item in errors])
    return [f"note {item.code}: {item.message}" for item in findings]


def _parse(table: Mapping[str, Any]) -> Sysusers:
    allowed = {"file", "state", "groups", "members_add", "members_absent", "intent", "accept_shadow"}
    require_keys(table, allowed=allowed, required={"file", "state", "intent"}, where="sysusers")
    state = table["state"]
    if state == "present":
        required = {"file", "state", "groups", "members_add", "members_absent", "intent"}
        require_keys(table, allowed=allowed, required=required, where="sysusers")
    groups = _groups_value(table.get("groups", []))
    add = _entries(table.get("members_add", []), "members_add")
    absent = _entries(table.get("members_absent", []), "members_absent")
    overlap = set(add) & set(absent)
    if overlap:
        raise ValueError(f"membership cannot be both added and absent: {sorted(overlap)[0]}")
    return Sysusers(table["file"], state, groups, add, absent, table["intent"], _accept_value(table.get("accept_shadow", [])))


def _parse_removal(table: Mapping[str, Any]) -> GroupRemoval:
    require_keys(table, allowed={"name", "state"}, required={"name", "state"}, where="group")
    return GroupRemoval(table["name"], table["state"])


def _values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, Sysusers):
        raise ValueError("resource is not Sysusers")
    result: dict[str, Any] = {
        "file": resource.file,
        "state": resource.state,
        "intent": resource.intent,
    }
    if resource.state == "present":
        result.update(
            groups=[
                {
                    "name": item.name,
                    **({"gid": item.gid} if item.gid is not None else {}),
                    **({"not_gid_of": list(item.not_gid_of)} if item.not_gid_of else {}),
                    **({"not_gid": list(item.not_gid)} if item.not_gid else {}),
                    **({"distinct_from": list(item.distinct_from)} if item.distinct_from else {}),
                }
                for item in resource.groups
            ],
            members_add=[{"user": user, "group": group} for user, group in resource.members_add],
            members_absent=[{"user": user, "group": group} for user, group in resource.members_absent],
        )
    if resource.accept_shadow:
        result["accept_shadow"] = [{"file": file, "key": key} for file, key in resource.accept_shadow]
    return result


def _group_values(resource: object) -> dict[str, Any]:
    if not isinstance(resource, GroupRemoval):
        raise ValueError("resource is not GroupRemoval")
    return {"name": resource.name, "state": resource.state}


def _restore(address: str, record: Mapping[str, Any], actions: tuple[PostAction, ...]) -> file_ops.RestoreFile:
    filename = record.get("path")
    if not isinstance(filename, str) or not filename.startswith("/etc/sysusers.d/") or _FILE_RE.fullmatch(filename.removeprefix("/etc/sysusers.d/")) is None:
        raise ValueError("sysusers pre-image path is outside /etc/sysusers.d")
    resource = file_ops.restore_file("sysusers", address, record, actions)
    replay = tuple(
        action
        for action in actions
        if action.argv[0] != "gpasswd"
        and (resource.image.get("exists") or action.argv[0] != "systemd-sysusers")
    )
    return replace(resource, actions=replay)


register_kind(
    KindSpec(
        kind="sysusers",
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
register_kind(KindSpec(kind="group", parse=_parse_removal, to_toml=_group_values, revert=None))

__all__ = ["GroupRemoval", "GroupSpec", "Sysusers"]
