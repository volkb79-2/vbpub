"""Standalone ``toposd install`` and ``toposd uninstall`` lifecycle.

The module keeps its imports lightweight and loads ``augeaswrap.endpoints``
only for installation and removal. It is usable from the wheel before the
daemon environment exists.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import importlib
import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from cli_extended import CliFailure, CliRegistry, CliRuntime, OptionSpec, VerbGroup, VerbSpec
from cli_extended.prompts import PromptCancelled

from .config import DEFAULTS, ConfigError, ServiceConfig, parse_config
from .lock import LockError, parse_lock, parse_pin
from .units import render_units


DEFAULT_RELEASE_BASE = "https://github.com/volkb79-2/vbpub/releases/download"
SOCKET_RELEASE = "0.7.0"
# First release that publishes the host endpoint record and so imports augeaswrap.
# augeaswrap is on no package index (GitHub Releases only), so it is delivered as a
# digest-pinned wheel installed --no-deps, never through the index-resolved daemon lock.
AUGEASWRAP_RELEASE = "0.8.0"
AUGEASWRAP_MINIMUM = "0.1.0"
VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)([A-Za-z0-9.+_-]*)$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]*\$?$")
DEFAULT_OBSERVE_GROUP = "topos-observe"
CONNECT_TIMEOUT = 1.0


class InstallError(ValueError):
    """A preflight or lifecycle condition that must be shown to the operator."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class InstallPaths:
    """Canonical host paths with an injectable root for tests and staging."""

    root: str | Path = "/"

    def at(self, absolute: str) -> str:
        if not os.path.isabs(absolute):
            raise ValueError(f"expected an absolute service path, got {absolute!r}")
        root = os.fspath(self.root)
        return absolute if root == "/" else os.path.join(root, absolute.lstrip("/"))

    @property
    def opt(self) -> str:
        return self.at("/opt/topos")

    @property
    def config(self) -> str:
        return self.at("/etc/topos/toposd.toml")

    @property
    def install_record(self) -> str:
        return self.at("/etc/topos/install.toml")

    @property
    def unit_dir(self) -> str:
        return self.at("/etc/systemd/system")

    @property
    def sysusers(self) -> str:
        return self.at("/etc/sysusers.d/topos.conf")

    @property
    def client_link(self) -> str:
        return self.at("/usr/local/bin/topos")

    @property
    def daemon_link(self) -> str:
        return self.at("/usr/local/bin/toposd")

    @property
    def runtime_root(self) -> str:
        return self.at(DEFAULTS.state_dir.parent.as_posix())

    @property
    def observe_dir(self) -> str:
        return self.at(DEFAULTS.sockets.observe.path.parent.as_posix())

    @property
    def socket_node(self) -> str:
        return self.at(DEFAULTS.sockets.observe.path.as_posix())

    @property
    def state_dir(self) -> str:
        return self.at(DEFAULTS.state_dir.as_posix())

    @property
    def endpoint_fragment(self) -> str:
        return self.at("/etc/host-endpoints.d/topos.env")

    @property
    def tmpfiles_dir(self) -> str:
        return self.at("/etc/tmpfiles.d")

    @property
    def service_dropins(self) -> str:
        return self.at("/etc/systemd/system/topos.service.d")

    @property
    def pending(self) -> str:
        return os.path.join(self.opt, "PENDING")

    @property
    def current(self) -> str:
        return os.path.join(self.opt, "current")

    @property
    def current_new(self) -> str:
        return os.path.join(self.opt, "current.new")


def version_key(value: str) -> tuple[int, int, int, int, str]:
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise InstallError(f"unsupported topos version spelling: {value!r}")
    suffix = match.group(4)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(not suffix), suffix)


def _parse_pin(value: str, label: str) -> tuple[str, str]:
    try:
        return parse_pin(value, label)
    except LockError as exc:
        raise InstallError(str(exc)) from None


def _parse_words(value: str | None) -> list[str]:
    if not value:
        return []
    return [word.strip() for word in value.split(",") if word.strip()]


def _validate_users(users: Sequence[str], forgotten: list[str] | None = None) -> list[str]:
    """Validate user names; with ``forgotten``, users that no longer exist are collected there, not refused."""
    result: list[str] = []
    for user in users:
        if not USER_RE.fullmatch(user):
            raise InstallError(f"not a usable Unix user name: {user!r}")
        try:
            pwd.getpwnam(user)
        except KeyError:
            if forgotten is None:
                raise InstallError(f"user {user!r} does not exist") from None
            if user not in forgotten:
                forgotten.append(user)
            continue
        if user not in result:
            result.append(user)
    return result


def _group_gid(name: str) -> int | None:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _endpoint_entries(config: ServiceConfig, *, resolve_gid: bool) -> dict[str, str]:
    """Build the host endpoint mapping from the validated service configuration."""
    socket = config.sockets.observe
    gid = _group_gid(socket.group)
    if resolve_gid and gid is None:
        raise InstallError(f"configured observe group {socket.group!r} has no GID after systemd-sysusers")
    return {
        "TOPOS_OBSERVE_SOCKET": socket.path.as_posix(),
        "TOPOS_OBSERVE_SOCKET_DIR": socket.path.parent.as_posix(),
        # Before sysusers, the group name reserves and validates the key. The
        # published fragment always contains the resolved numeric GID.
        "TOPOS_OBSERVE_GID": str(gid) if gid is not None else socket.group,
    }


def _endpoint_preview(paths: InstallPaths, config: ServiceConfig, version: str) -> dict[str, Any]:
    entries: dict[str, Any] = _endpoint_entries(config, resolve_gid=False)
    unresolved: dict[str, str] = {}
    gid_value = entries["TOPOS_OBSERVE_GID"]
    if not gid_value.isdecimal():
        unresolved["TOPOS_OBSERVE_GID"] = gid_value
        entries["TOPOS_OBSERVE_GID"] = None
    return {
        "owner": "topos",
        "path": paths.endpoint_fragment,
        "writer": f"toposd install {version}",
        "entries": entries,
        "unresolved_groups": unresolved,
    }


def unsafe_group_reason(gid: int) -> str | None:
    """Why an observe group with this GID is refused, or None: GID 0 and the docker GID are root-equivalent."""
    if gid == 0:
        return "GID 0"
    if gid == _group_gid("docker"):
        return f"the docker group's GID {gid}"
    return None


def _refuse_unsafe_group(group: str) -> None:
    gid = _group_gid(group)
    reason = None if gid is None else unsafe_group_reason(gid)
    if reason is not None:
        raise InstallError(
            f"the observe group {group!r} has {reason}; use a dedicated group such as {DEFAULT_OBSERVE_GROUP}"
        )


def _add_user_names(values: Sequence[str] | None) -> list[str]:
    """User names from repeated --add-user LEVEL=USER values; topos has the one level, observe."""
    users: list[str] = []
    for item in values or ():
        level, separator, user = item.partition("=")
        if not separator or level != "observe" or not user:
            raise InstallError(f"--add-user takes observe=USER (the only level is observe); got {item!r}")
        users.append(user)
    return users


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_config(group: str) -> str:
    return (
        f"state_dir = {_toml_string(DEFAULTS.state_dir.as_posix())}\n\n"
        "[sockets.observe]\n"
        f"path = {_toml_string(DEFAULTS.sockets.observe.path.as_posix())}\n"
        f"group = {_toml_string(group)}\n"
        f"mode = {_toml_string(DEFAULTS.sockets.observe.mode)}\n"
    )


def _config_edit_refusal(path: str, group: str) -> InstallError:
    return InstallError(
        f"cannot safely edit sockets.observe.group in {path}; edit the group under "
        f"[sockets.observe] to {_toml_string(group)} by hand, then rerun"
    )


def _edit_config_group(text: str, group: str, path: str) -> str:
    """Edit only the observe group declaration, preserving all other text."""
    lines = text.splitlines(keepends=True)
    table = ""
    observe_start: int | None = None
    observe_end = len(lines)
    sockets_start: int | None = None
    sockets_end = len(lines)
    has_root_observe_dotted = False
    has_sockets_observe_dotted = False
    unsafe_inline = False
    target: tuple[int, re.Match[str]] | None = None
    header_pattern = re.compile(r"^\s*\[(?P<name>[^\[\]]+)\]\s*(?:#.*)?(?:\r?\n)?$")
    assignment_pattern = re.compile(
        r"^(?P<prefix>\s*(?:group|observe\.group|sockets\.observe\.group)\s*=\s*)"
        r"(?P<value>\"(?:\\.|[^\"\\])*\"|'[^'\r\n]*')"
        r"(?P<suffix>\s*(?:#.*)?(?:\r?\n)?)$"
    )

    for index, line in enumerate(lines):
        header = header_pattern.fullmatch(line)
        if header is not None:
            if table == "sockets.observe" and observe_end == len(lines):
                observe_end = index
            if table == "sockets" and sockets_end == len(lines):
                sockets_end = index
            table = header.group("name").strip()
            if table == "sockets.observe":
                observe_start = index
            elif table == "sockets":
                sockets_start = index
            elif "sockets" in table:
                raise _config_edit_refusal(path, group)
            continue

        code = line.lstrip()
        if not code or code.startswith("#"):
            continue
        code = code.split("#", 1)[0]
        if table == "" and re.match(r"\s*sockets(?:\.observe)?\s*=\s*\{", code):
            unsafe_inline = True
        elif table == "sockets" and re.match(r"\s*observe\s*=\s*\{", code):
            unsafe_inline = True

        dotted_key = re.match(r"\s*sockets\.observe\.", code) is not None
        nested_dotted_key = re.match(r"\s*observe\.", code) is not None
        has_root_observe_dotted |= table == "" and dotted_key
        has_sockets_observe_dotted |= table == "sockets" and nested_dotted_key

        expected_key = (
            "group" if table == "sockets.observe"
            else "observe.group" if table == "sockets"
            else "sockets.observe.group" if table == ""
            else None
        )
        if expected_key is not None:
            match = assignment_pattern.fullmatch(line)
            if match is not None and re.match(r"\s*" + re.escape(expected_key) + r"\s*=", line):
                if target is not None:
                    raise _config_edit_refusal(path, group)
                target = (index, match)

    if table == "sockets.observe" and observe_end == len(lines):
        observe_end = len(lines)
    if table == "sockets" and sockets_end == len(lines):
        sockets_end = len(lines)

    if target is not None:
        index, match = target
        current = match.group("value")
        if current[1:-1] == group:
            return text
        lines[index] = match.group("prefix") + _toml_string(group) + match.group("suffix")
        return "".join(lines)

    if unsafe_inline:
        raise _config_edit_refusal(path, group)

    entry = f"group = {_toml_string(group)}\n"
    if observe_start is not None:
        index = observe_end
        insertion = entry
    elif has_root_observe_dotted:
        index = next(
            (i for i, line in enumerate(lines) if header_pattern.fullmatch(line) is not None),
            len(lines),
        )
        insertion = f"sockets.observe.group = {_toml_string(group)}\n"
    elif has_sockets_observe_dotted:
        index = sockets_end
        insertion = f"observe.group = {_toml_string(group)}\n"
    elif sockets_start is not None:
        index = sockets_end
        insertion = f"[sockets.observe]\n{entry}"
    else:
        separator = "" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
        return text + separator + f"[sockets.observe]\n{entry}"

    if index > 0 and not lines[index - 1].endswith(("\n", "\r")):
        lines[index - 1] += "\n"
    lines.insert(index, insertion)
    return "".join(lines)


def _atomic_write(path: str, data: str, mode: int = 0o644) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_symlink(target: str, path: str, temporary: str) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


# Findings under /opt/topos come from toposd install itself (an interrupted run) or
# from something unknown there; the old host-setup layout never wrote /opt/topos.
_VERSION_ROOT_FINDINGS = (
    "leftover install marker:",
    "leftover version staging directory:",
    "unmarked version tree:",
    "unknown version tree MANIFEST:",
    "cannot inspect version MANIFEST",
    "cannot inspect version root",
    "version tree is a symlink:",
    "version root is not a real directory:",
)


def manual_removal_message(found: Iterable[str] = ()) -> str:
    details = list(found)
    version_root = [item for item in details if item.startswith(_VERSION_ROOT_FINDINGS)]
    old_host = [item for item in details if item not in version_root]
    message = ["toposd install refused: this host has files toposd install did not write or did not finish."]
    if details:
        message.extend(("Found:", *(f"  {item}" for item in details)))
    if old_host or not details:
        message.extend(
            (
                "Remove the old installation manually (docs/DAEMON.md, \"Manual removal of an old installation\"):",
                "  systemctl stop topos.service",
                "  systemctl disable topos.service",
                "  rm -f /etc/tmpfiles.d/mdt-topos.conf",
                "  rm -f /etc/systemd/system/topos.service.d/50-mdt-socket-group.conf",
                "  rm -f /etc/systemd/system/topos.service /etc/systemd/system/topos.slice",
                "  systemctl daemon-reload",
                "  rm -rf /run/topos",
                "  recreate containers that bind-mount /run/topos",
            )
        )
    if version_root:
        message.extend(
            (
                "Under /opt/topos: these are leftovers of a toposd install that did not finish.",
                "  Remove the listed paths and rerun, or run `toposd uninstall --yes` to remove the",
                "  whole install (it removes only what toposd install writes).",
            )
        )
    message.append("Then rerun toposd install.")
    return "\n".join(message)


def _directory_entries(directory: str, problems: list[str], label: str) -> list[str]:
    try:
        return sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as exc:
        problems.append(f"cannot inspect {directory}: {exc.strerror or exc}")
        return []


def find_foreign_rules(paths: InstallPaths) -> list[str]:
    """Inspect old tmpfiles and service drop-ins without changing them."""
    found: list[str] = []
    for name in _directory_entries(paths.tmpfiles_dir, found, "tmpfiles"):
        path = os.path.join(paths.tmpfiles_dir, name)
        if not name.endswith(".conf") or not os.path.lexists(path):
            continue
        if name.startswith("mdt-topos"):
            found.append(path)
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            found.append(f"{path} (cannot inspect: {exc})")
        else:
            if "/run/topos" in text:
                found.append(path)
    for name in _directory_entries(paths.service_dropins, found, "service drop-ins"):
        path = os.path.join(paths.service_dropins, name)
        if not name.endswith(".conf") or not os.path.lexists(path):
            continue
        if "mdt" in name.lower():
            found.append(path)
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            found.append(f"{path} (cannot inspect: {exc})")
        else:
            if "/run/topos" in text or "Group=" in text:
                found.append(path)
    return found


# Owner (uid, gid) the runtime tree must have; fake-root tests set their own ids.
RUNTIME_TREE_OWNER = (0, 0)


def _run_tree_problems(paths: InstallPaths) -> list[str]:
    owner_uid, owner_gid = RUNTIME_TREE_OWNER
    expected = {
        paths.runtime_root: 0o755,
        paths.observe_dir: 0o755,
        paths.state_dir: 0o700,
    }
    problems: list[str] = []
    for path, mode in expected.items():
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(f"cannot inspect {path}: {exc.strerror or exc}")
            continue
        actual_mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_uid, info.st_gid) != (owner_uid, owner_gid)
            or actual_mode != mode
        ):
            problems.append(
                f"{path} is {info.st_uid}:{info.st_gid} {actual_mode:04o}; "
                f"expected root:root {mode:04o}"
            )
    return problems


def old_installation_problems(paths: InstallPaths) -> list[str]:
    """Return every P-11 conflict. This function is strictly inspect-only."""
    problems = [f"foreign rule or drop-in: {path}" for path in find_foreign_rules(paths)]
    problems.extend(_run_tree_problems(paths))
    for absolute in ("/run/topos/topos.sock", "/run/topos/bpf", "/run/topos/squeeze"):
        path = paths.at(absolute)
        if os.path.lexists(path):
            problems.append(f"old-layout path remains: {path}")
    # Units next to PENDING come from an unfinished toposd install; PENDING itself
    # is reported below.
    if not os.path.lexists(paths.install_record) and not os.path.lexists(paths.pending):
        for name in ("topos.service", "topos.slice"):
            path = os.path.join(paths.unit_dir, name)
            if os.path.lexists(path):
                problems.append(f"hand-installed old unit without install record: {path}")
    for marker in (paths.pending, paths.current_new):
        if os.path.lexists(marker):
            problems.append(f"leftover install marker: {marker}")
    if os.path.lexists(paths.opt) and (os.path.islink(paths.opt) or not os.path.isdir(paths.opt)):
        problems.append(f"version root is not a real directory: {paths.opt}")
    if os.path.isdir(paths.opt) and not os.path.islink(paths.opt):
        try:
            entries = sorted(os.listdir(paths.opt))
        except OSError as exc:
            problems.append(f"cannot inspect version root {paths.opt}: {exc.strerror or exc}")
            entries = []
        for name in entries:
            if name in ("current", "current.new"):  # current.new is reported as a marker above
                continue
            version_dir = os.path.join(paths.opt, name)
            if name.startswith(".stage."):
                problems.append(f"leftover version staging directory: {version_dir}")
                continue
            if os.path.islink(version_dir):
                problems.append(f"version tree is a symlink: {version_dir}")
                continue
            if not os.path.isdir(version_dir):
                continue
            manifest = os.path.join(version_dir, "MANIFEST")
            if not os.path.lexists(manifest):
                problems.append(f"unmarked version tree: {version_dir}")
                continue
            try:
                contents = Path(manifest).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(f"cannot inspect version MANIFEST {manifest}: {exc}")
                continue
            if "installer=toposd install" not in contents.splitlines():
                problems.append(f"unknown version tree MANIFEST: {manifest}")
    return problems


def _fetch_asset(
    source: str,
    project: str,
    filename: str,
    dest: str,
    fetch: Callable[[str, str], None] | None,
) -> None:
    if os.path.isdir(source):
        src = os.path.join(source, filename)
        if not os.path.isfile(src):
            raise InstallError(f"{src} not found; --wheel-source directory must contain {filename}")
        shutil.copyfile(src, dest)
        return
    if not source.startswith("https://"):
        raise InstallError("--wheel-source must be an HTTPS release base or a directory")
    if filename.endswith("-daemon.lock"):
        version = filename.removeprefix("topos-").removesuffix("-daemon.lock")
        tag = f"topos-v{version}"
    else:
        prefix = project.replace("-", "_") + "-"
        suffix = "-py3-none-any.whl"
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            raise InstallError(f"unexpected wheel filename: {filename}")
        version = filename[len(prefix):-len(suffix)]
        tag_name = project.replace("_", "-")
        tag = f"{tag_name}-v{version}"
    url = f"{source.rstrip('/')}/{tag}/{filename}"
    if fetch is not None:
        try:
            fetch(url, dest)
        except Exception as exc:  # noqa: BLE001 - surface bounded fetch failures
            raise InstallError(f"download failed for {url}: {type(exc).__name__}: {exc}") from None
        return
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(dest, "wb") as output:
            shutil.copyfileobj(response, output)
    except Exception as exc:  # noqa: BLE001 - surface bounded fetch failures
        raise InstallError(f"download failed for {url}: {type(exc).__name__}: {exc}") from None


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_record(path: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            value = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"cannot read prior install record {path}: {exc}") from None
    if not isinstance(value, dict):  # pragma: no cover - tomllib returns a table
        raise InstallError(f"install record {path} must contain a TOML table")
    return value


def _record_created_groups(record: Mapping[str, Any], path: str) -> list[str]:
    raw = record.get("created_groups", [])
    if not isinstance(raw, list) or any(not isinstance(name, str) or not USER_RE.fullmatch(name) for name in raw):
        raise InstallError(f"install record {path} has an invalid created_groups list; repair it manually")
    unsupported = [name for name in raw if name != DEFAULT_OBSERVE_GROUP]
    if unsupported:
        raise InstallError(
            f"install record {path} contains unsupported created group {unsupported[0]!r}; "
            "refusing to remove it"
        )
    return list(dict.fromkeys(raw))


def _render_record(record: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for key in sorted(record):
        value = record[key]
        if isinstance(value, list):
            encoded = "[" + ", ".join(_toml_string(item) if isinstance(item, str) else str(item) for item in value) + "]"
        elif isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, str):
            encoded = _toml_string(value)
        else:
            raise InstallError(f"unsupported install record value for {key}")
        lines.append(f"{key} = {encoded}\n")
    return "".join(lines)


def _existing_config_group(paths: InstallPaths) -> str | None:
    try:
        text = Path(paths.config).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallError(f"cannot read {paths.config}: {exc}") from None
    try:
        config = parse_config(text, source=paths.config)
    except ConfigError as exc:
        raise InstallError(f"{paths.config} is invalid: {exc}; fix or remove it, then rerun") from None
    # parse_config fills in the default group when the key is absent; only a group
    # the file names itself counts, or a pipe run would silently get the default.
    raw = tomllib.loads(text).get("sockets", {}).get("observe", {})
    return config.sockets.observe.group if "group" in raw else None


def _resolve_group(
    args: argparse.Namespace,
    record: Mapping[str, Any],
    runtime: CliRuntime,
    config_group: str | None,
) -> str:
    value = getattr(args, "observe_group", None)
    if value is None:
        value = config_group
    if value is None:
        value = record.get("observe_group")
    if value:
        if not isinstance(value, str):
            raise InstallError("the saved observe_group value is invalid; repair the install record manually")
        return str(value)
    if runtime.output.is_interactive and not runtime.dry_run and not runtime.json_mode:
        try:
            choice = runtime.prompts.select(
                "Observe access group",
                (f"create {DEFAULT_OBSERVE_GROUP}", "map an existing group"),
                default=f"create {DEFAULT_OBSERVE_GROUP}",
            )
            if choice == f"create {DEFAULT_OBSERVE_GROUP}":
                return DEFAULT_OBSERVE_GROUP
            mapped = runtime.prompts.text("Existing group for observe", required=True)
            if mapped:
                return mapped
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None
        raise InstallError("missing required --observe-group")
    raise InstallError("missing required --observe-group; provide it on a non-interactive run")


def _gather_inputs(args: argparse.Namespace, runtime: CliRuntime, paths: InstallPaths) -> dict[str, Any]:
    record = _read_record(paths.install_record)
    created_groups = _record_created_groups(record, paths.install_record)

    def required(flag: str, label: str, saved: Any) -> str:
        value = getattr(args, flag[2:].replace("-", "_"), None) or saved
        if value:
            return str(value)
        if runtime.dry_run or runtime.json_mode or not runtime.output.is_interactive:
            raise InstallError(f"missing required {flag}; provide it on a non-interactive run")
        try:
            return str(runtime.prompts.text(label, required=True))
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None

    wheel = required("--wheel", "topos release pin", record.get("wheel"))
    clix = required("--cli-extended-wheel", "cli-extended release pin", record.get("cli_extended_wheel"))
    lock_pin = required("--daemon-lock", "topos daemon lock pin", record.get("daemon_lock"))
    try:
        wheel_version = parse_pin(wheel, "--wheel")[0]
    except LockError:
        wheel_version = ""  # the strict pin check below reports it
    needs_augeaswrap = bool(wheel_version) and _needs_augeaswrap(wheel_version)
    augeaswrap = (
        required("--augeaswrap-wheel", "augeaswrap release pin", record.get("augeaswrap_wheel"))
        if needs_augeaswrap else None
    )
    group = _resolve_group(args, record, runtime, _existing_config_group(paths))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", group):
        raise InstallError(f"not a usable observe group name: {group!r}")
    if group == "docker":
        raise InstallError(
            "the observe group must not be docker (membership is root-equivalent); "
            f"use {DEFAULT_OBSERVE_GROUP} and add the users with --add-user observe=USER"
        )
    if _group_gid(group) is None and group != DEFAULT_OBSERVE_GROUP:
        raise InstallError(f"mapped observe group {group!r} does not exist")
    _refuse_unsafe_group(group)

    # Users from earlier runs stay (members are only ever added); the flag adds more.
    flag_users = _add_user_names(getattr(args, "add_user", None))
    users_raw = record.get("observe_users")
    if users_raw is None and not flag_users and runtime.output.is_interactive and not runtime.dry_run \
            and not runtime.json_mode:
        try:
            users_raw = runtime.prompts.text(
                "Users to add to the observe group (comma-separated)", default="", required=False
            )
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None
    if isinstance(users_raw, list):
        if any(not isinstance(user, str) for user in users_raw):
            raise InstallError("the saved observe_users value is invalid; repair the install record manually")
        raw_users = users_raw
    else:
        raw_users = _parse_words(str(users_raw or ""))
    # A recorded user who has since been deleted is forgotten (and reported), so a stale
    # record cannot block every later install; a user named on the command line must exist.
    forgotten: list[str] = []
    users = _validate_users(
        [*_validate_users(raw_users, forgotten), *_validate_users(flag_users)]
    )

    if getattr(args, "enable", False) and getattr(args, "no_enable", False):
        raise InstallError("choose only one of --enable and --no-enable")
    if getattr(args, "enable", False):
        enable_now: bool | None = True
    elif getattr(args, "no_enable", False):
        enable_now = False
    elif "enable_now" in record and isinstance(record["enable_now"], bool):
        enable_now = record["enable_now"]
    elif runtime.dry_run:
        enable_now = False
    elif runtime.json_mode or not runtime.output.is_interactive:
        raise InstallError("missing required --enable or --no-enable on a non-interactive run")
    else:
        try:
            enable_now = runtime.prompts.confirm("Enable and start topos now?", default=True)
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None

    source = getattr(args, "wheel_source", None) or record.get("wheel_source") or DEFAULT_RELEASE_BASE
    if not os.path.isdir(source) and not str(source).startswith("https://"):
        raise InstallError("--wheel-source must be an HTTPS release base or an existing directory")
    version, wheel_sha = _parse_pin(wheel, "--wheel")
    clix_version, clix_sha = _parse_pin(clix, "--cli-extended-wheel")
    lock_version, lock_sha = _parse_pin(lock_pin, "--daemon-lock")
    augeaswrap_version = augeaswrap_sha = None
    if augeaswrap is not None:
        augeaswrap_version, augeaswrap_sha = _parse_pin(augeaswrap, "--augeaswrap-wheel")
        if version_key(augeaswrap_version) < version_key(AUGEASWRAP_MINIMUM):
            raise InstallError(
                f"augeaswrap {augeaswrap_version} is too old; need {AUGEASWRAP_MINIMUM} or later"
            )
    if not _is_socket_unit_version(version):
        raise InstallError(
            f"toposd install supports topos {SOCKET_RELEASE} and later; {version} has no toposd or daemon lock"
        )
    if lock_version != version:
        raise InstallError(f"--daemon-lock version {lock_version} must equal topos wheel version {version}")
    return {
        "wheel": wheel,
        "cli_extended_wheel": clix,
        "daemon_lock": lock_pin,
        "wheel_source": str(source),
        "observe_group": group,
        "observe_users": users,
        "users_forgotten": forgotten,
        "enable_now": bool(enable_now),
        "allow_downgrade": bool(getattr(args, "allow_downgrade", False)),
        "prior_created_groups": created_groups,
        "version": version,
        "wheel_sha": wheel_sha,
        "cli_extended_version": clix_version,
        "cli_extended_sha": clix_sha,
        "lock_version": lock_version,
        "lock_sha": lock_sha,
        "augeaswrap_wheel": augeaswrap,
        "augeaswrap_version": augeaswrap_version,
        "augeaswrap_sha": augeaswrap_sha,
    }


def _needs_augeaswrap(version: str) -> bool:
    """True for topos releases whose installer-managed venv imports augeaswrap."""
    return version_key(version)[:3] >= version_key(AUGEASWRAP_RELEASE)[:3]


def _is_socket_unit_version(version: str) -> bool:
    # Release candidates of 0.7.0 already carry toposd, so compare the release part only.
    return version_key(version)[:3] >= version_key(SOCKET_RELEASE)[:3]


def _is_install_link(link: str, paths: InstallPaths) -> bool:
    """True for a /usr/local/bin symlink that points into /opt/topos."""
    try:
        return os.path.islink(link) and os.readlink(link).startswith(paths.opt + os.sep)
    except OSError:
        return False


def _hello(path: str, timeout: float = CONNECT_TIMEOUT) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    payload = {"id": request_id, "op": "hello", "v": 1}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(path)
        client.sendall((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        with client.makefile("r", encoding="utf-8", newline="") as stream:
            line = stream.readline(65537)
    if not line or len(line.encode("utf-8")) > 65536:
        raise OSError("observe socket returned no bounded hello response")
    body = json.loads(line)
    if not isinstance(body, dict) or body.get("id") != request_id or body.get("ok") is not True:
        raise OSError("observe socket returned no successful hello envelope")
    result = body.get("result")
    if not isinstance(result, dict):
        raise OSError("observe socket hello response has no result")
    return result


class Installer:
    """Injected installer; all host commands and time are replaceable."""

    def __init__(
        self,
        paths: InstallPaths = InstallPaths(),
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        fetch: Callable[[str, str], None] | None = None,
        geteuid: Callable[[], int] = os.geteuid,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        python: str = sys.executable,
        running_prefix: str | os.PathLike[str] = sys.prefix,
        systemctl: str = "systemctl",
    ) -> None:
        self.paths = paths
        self.run = run
        self.fetch = fetch
        self.geteuid = geteuid
        self.sleep = sleep
        self.monotonic = monotonic
        self.python = python
        self.running_prefix = os.fspath(running_prefix)
        self.systemctl = systemctl

    def _command(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        timeout: float = 60.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self.run(list(argv), capture_output=True, text=True, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"command failed to run ({' '.join(argv)}): {exc}") from None
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise InstallError(f"command failed ({' '.join(argv)}), exit {result.returncode}{suffix}")
        return result

    def _endpoints_api(self):
        """Load the endpoint publisher required by this topos runtime."""
        try:
            return importlib.import_module("augeaswrap.endpoints")
        except ModuleNotFoundError as exc:
            if exc.name not in ("augeaswrap", "augeaswrap.endpoints"):
                raise
            raise InstallError(
                "augeaswrap.endpoints is unavailable; install the topos runtime dependencies"
            ) from None

    def _preflight_endpoint_record(self, config: ServiceConfig) -> None:
        try:
            problems = self._endpoints_api().plan(
                "topos", _endpoint_entries(config, resolve_gid=False), root=self.paths.root
            )
        except InstallError:
            raise
        except Exception as exc:
            raise InstallError(f"augeaswrap.endpoints.plan failed: {exc}") from None
        if problems:
            raise InstallError(
                "augeaswrap.endpoints.plan refused: " + "; ".join(str(problem) for problem in problems)
            )

    def _current_version(self) -> str | None:
        if not os.path.lexists(self.paths.current):
            return None
        if not os.path.islink(self.paths.current):
            raise InstallError(f"{self.paths.current} exists but is not a symlink; refusing to replace it")
        target = os.readlink(self.paths.current)
        if not target or os.path.basename(target) != target:
            raise InstallError(f"{self.paths.current} must point to a version directory inside /opt/topos")
        version = target
        version_key(version)
        version_dir = os.path.join(self.paths.opt, version)
        if not os.path.isdir(version_dir) or not os.path.isfile(os.path.join(version_dir, "MANIFEST")):
            raise InstallError(f"{self.paths.current} points to an incomplete version tree {version_dir}")
        return version

    def _preflight(self, values: Mapping[str, Any], *, dry_run: bool) -> tuple[bool, bool]:
        if not dry_run and self.geteuid() != 0:
            raise InstallError("toposd install needs root")
        problems = old_installation_problems(self.paths)
        if os.path.lexists(self.paths.install_record):
            # A failed upgrade of a finished install: every apply step is
            # idempotent, so a rerun re-applies instead of needing an uninstall.
            reapply = {f"leftover install marker: {marker}" for marker in (self.paths.pending, self.paths.current_new)}
            problems = [item for item in problems if item not in reapply]
        if problems:
            raise InstallError(manual_removal_message(problems))
        version_dir = os.path.join(self.paths.opt, values["version"])
        manifest = os.path.join(version_dir, "MANIFEST")
        if os.path.isfile(manifest):
            try:
                lines = Path(manifest).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                raise InstallError(f"cannot read immutable version MANIFEST {manifest}") from None
            expected = {
                "version": values["version"],
                "wheel_pin": values["wheel"],
                "cli_extended_wheel_pin": values["cli_extended_wheel"],
                "daemon_lock_pin": values["daemon_lock"],
                "wheel_sha256": values["wheel_sha"],
                "cli_extended_sha256": values["cli_extended_sha"],
                "daemon_lock_sha256": values["lock_sha"],
                "installer": "toposd install",
            }
            if values.get("augeaswrap_wheel"):
                expected["augeaswrap_wheel_pin"] = values["augeaswrap_wheel"]
                expected["augeaswrap_sha256"] = values["augeaswrap_sha"]
            if any(f"{key}={value}" not in lines for key, value in expected.items()):
                raise InstallError(f"{version_dir} is immutable and its manifest differs from the requested pins")
        elif os.path.lexists(version_dir):
            raise InstallError(f"unmarked version directory {version_dir} exists; remove it manually")
        for link in (self.paths.client_link, self.paths.daemon_link):
            if os.path.lexists(link) and not _is_install_link(link, self.paths):
                raise InstallError(
                    f"{link} exists and was not written by toposd install; move it away, then rerun"
                )
        current = self._current_version()
        downgrade = bool(current and version_key(values["version"]) < version_key(current))
        if downgrade and not values["allow_downgrade"]:
            raise InstallError(
                f"refusing downgrade from {current} to {values['version']}; "
                "review the rollback and rerun with --allow-downgrade"
            )
        return downgrade, bool(current != values["version"])

    def _ensure_version(self, values: Mapping[str, Any]) -> str:
        os.makedirs(self.paths.opt, mode=0o755, exist_ok=True)
        version_dir = os.path.join(self.paths.opt, values["version"])
        manifest = os.path.join(version_dir, "MANIFEST")
        venv = os.path.join(version_dir, "venv")
        if os.path.isfile(manifest):
            if not os.path.isfile(os.path.join(venv, "bin", "toposd")):
                raise InstallError(f"{venv}/bin/toposd is missing from an immutable version tree")
            return venv

        # The stage holds only the downloads. The venv is built at its final path,
        # because pip writes absolute interpreter paths into its console scripts;
        # a failed build removes the version tree, and MANIFEST is written last.
        stage = tempfile.mkdtemp(prefix=".stage.", dir=self.paths.opt)
        wheels = os.path.join(stage, "wheels")
        os.makedirs(wheels)
        wheel_name = f"topos-{values['version']}-py3-none-any.whl"
        clix_name = f"cli_extended-{values['cli_extended_version']}-py3-none-any.whl"
        lock_name = f"topos-{values['version']}-daemon.lock"
        aug_name = (
            f"augeaswrap-{values['augeaswrap_version']}-py3-none-any.whl"
            if values.get("augeaswrap_wheel") else None
        )
        wheel_path = os.path.join(wheels, wheel_name)
        clix_path = os.path.join(wheels, clix_name)
        lock_path = os.path.join(wheels, lock_name)
        aug_path = os.path.join(wheels, aug_name) if aug_name else None
        try:
            _fetch_asset(values["wheel_source"], "topos", wheel_name, wheel_path, self.fetch)
            _fetch_asset(values["wheel_source"], "cli-extended", clix_name, clix_path, self.fetch)
            _fetch_asset(values["wheel_source"], "topos", lock_name, lock_path, self.fetch)
            pinned = [
                (wheel_name, wheel_path, values["wheel_sha"]),
                (clix_name, clix_path, values["cli_extended_sha"]),
                (lock_name, lock_path, values["lock_sha"]),
            ]
            if aug_name and aug_path:
                _fetch_asset(values["wheel_source"], "augeaswrap", aug_name, aug_path, self.fetch)
                pinned.append((aug_name, aug_path, values["augeaswrap_sha"]))
            for label, path, digest in pinned:
                if _sha256(path) != digest:
                    raise InstallError(f"sha256 of {label} does not match the pin; refusing before unpack")
            try:
                parse_lock(Path(lock_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, LockError) as exc:
                raise InstallError(f"daemon lock {lock_name} is invalid: {exc}") from None
            os.makedirs(version_dir, mode=0o755)
        except InstallError:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(stage, ignore_errors=True)
            raise InstallError(f"cannot prepare version tree {version_dir}: {exc}") from None
        try:
            self._command([self.python, "-m", "venv", venv], timeout=120)
            venv_python = os.path.join(venv, "bin", "python")
            dep_command = [venv_python, "-m", "pip", "install", "--require-hashes", "-r", lock_path]
            if os.path.isdir(values["wheel_source"]):
                dep_command.extend(("--no-index", "--find-links", values["wheel_source"]))
            self._command(dep_command, timeout=600)
            # The first-party wheels never go through an index: --no-deps, by pinned path.
            first_party = [wheel_path, clix_path, *([aug_path] if aug_path else [])]
            self._command(
                [venv_python, "-m", "pip", "install", "--no-deps", *first_party],
                timeout=600,
            )
            augeaswrap_lines = (
                (
                    f"augeaswrap_wheel={aug_name}",
                    f"augeaswrap_wheel_pin={values['augeaswrap_wheel']}",
                    f"augeaswrap_sha256={values['augeaswrap_sha']}",
                )
                if aug_name else ()
            )
            manifest_text = "\n".join(
                (
                    f"version={values['version']}",
                    f"wheel={wheel_name}",
                    f"wheel_pin={values['wheel']}",
                    f"wheel_sha256={values['wheel_sha']}",
                    f"cli_extended_wheel={clix_name}",
                    f"cli_extended_wheel_pin={values['cli_extended_wheel']}",
                    f"cli_extended_sha256={values['cli_extended_sha']}",
                    f"daemon_lock={lock_name}",
                    f"daemon_lock_pin={values['daemon_lock']}",
                    f"daemon_lock_sha256={values['lock_sha']}",
                    *augeaswrap_lines,
                    "installer=toposd install",
                    "",
                )
            )
            _atomic_write(manifest, manifest_text)
        except BaseException as exc:
            shutil.rmtree(version_dir, ignore_errors=True)
            if isinstance(exc, OSError):
                raise InstallError(f"cannot build immutable version tree {version_dir}: {exc}") from None
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return venv

    def _resource_paths(self) -> dict[str, str]:
        return {
            "topos-observe.socket": os.path.join(self.paths.unit_dir, "topos-observe.socket"),
            "topos.service": os.path.join(self.paths.unit_dir, "topos.service"),
            "topos.slice": os.path.join(self.paths.unit_dir, "topos.slice"),
            "topos.sysusers": self.paths.sysusers,
        }

    def _is_active(self, unit: str) -> bool:
        return self._command([self.systemctl, "is-active", "--quiet", unit], check=False, timeout=15).returncode == 0

    def _wait_ready(self, socket_path: str, version: str, timeout: float = 30.0) -> None:
        deadline = self.monotonic() + timeout
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise InstallError(
                    f"observe socket did not report topos version {version} within {timeout:g}s; "
                    "inspect journalctl -u topos.service"
                )
            try:
                result = _hello(socket_path, timeout=min(CONNECT_TIMEOUT, remaining))
                identity = result.get("identity")
                if isinstance(identity, dict) and identity.get("version") == version:
                    return
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            now = self.monotonic()
            if now >= deadline:
                raise InstallError(
                    f"observe socket did not report topos version {version} within {timeout:g}s; "
                    "inspect journalctl -u topos.service"
                )
            self.sleep(min(1.0, deadline - now))

    def _running_version_dir(self) -> str | None:
        """Return the real /opt/topos version tree this process runs from."""
        prefix = os.path.realpath(self.running_prefix)
        opt = os.path.realpath(self.paths.opt)
        try:
            if os.path.commonpath((opt, prefix)) != opt or prefix == opt:
                return None
        except ValueError:
            return None
        name = os.path.relpath(prefix, opt).split(os.sep, 1)[0]
        running = os.path.join(opt, name)
        if not os.path.isdir(running) or os.path.islink(running):
            return None
        return running

    def _prune_versions(self, current: str, running: str | None = None) -> list[str]:
        if not os.path.isdir(self.paths.opt):
            return []
        candidates = []
        for name in os.listdir(self.paths.opt):
            path = os.path.join(self.paths.opt, name)
            if VERSION_RE.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path):
                candidates.append(name)
        keep = set(sorted(candidates, key=version_key, reverse=True)[:2]) | {current}
        removed = []
        for name in candidates:
            path = os.path.join(self.paths.opt, name)
            if name not in keep and (running is None or os.path.realpath(path) != running):
                shutil.rmtree(path)
                removed.append(name)
        return removed

    def install(self, args: argparse.Namespace, runtime: CliRuntime) -> dict[str, Any]:
        values = _gather_inputs(args, runtime, self.paths)
        running_version = self._running_version_dir()
        downgrade, version_drift = self._preflight(values, dry_run=runtime.dry_run)
        # Read before PENDING is written, so a failure is a clean refusal. An absent
        # file and a dangling symlink are both "new": _config renders a fresh file.
        try:
            config_mode: int | None = stat.S_IMODE(os.stat(self.paths.config).st_mode)
        except FileNotFoundError:
            config_mode = None
        except OSError as exc:
            raise InstallError(f"cannot inspect {self.paths.config}: {exc}") from None
        config_existed = config_mode is not None
        # A flag is applied to the file; so is a group from the record or a prompt
        # when the file names none. A group the file names itself is never replaced.
        config_text, config, config_changed = self._config(
            values["observe_group"],
            apply_group=getattr(args, "observe_group", None) is not None
            or _existing_config_group(self.paths) is None,
        )
        values["observe_group"] = config.sockets.observe.group
        if _group_gid(values["observe_group"]) is None and values["observe_group"] != DEFAULT_OBSERVE_GROUP:
            raise InstallError(f"mapped observe group {values['observe_group']!r} does not exist")
        _refuse_unsafe_group(values["observe_group"])
        self._preflight_endpoint_record(config)
        # A new file is created, not edited: only an existing file can have changes.
        config_changes = ["sockets.observe.group"] if config_changed and config_existed else []
        rendered = render_units(config)
        if runtime.dry_run:
            endpoint_fragment = _endpoint_preview(self.paths, config, values["version"])
            return {
                "ok": True,
                "dry_run": True,
                "version": values["version"],
                "config": self.paths.config,
                "observe_group": values["observe_group"],
                "config_changes": config_changes,
                "users_to_add": values["observe_users"],
                "users_forgotten": values["users_forgotten"],
                "resources": sorted(rendered),
                "endpoint_fragment": endpoint_fragment,
                "downgrade": downgrade,
                "enabled": values["enable_now"],
                "message": "no files, wheels, groups, links, or services changed",
            }
        if not runtime.yes and not runtime.confirm("Install or upgrade the topos host service?"):
            raise InstallError("installation was not confirmed")

        # PENDING marks an interrupted install, so the next install refuses and
        # uninstall knows the leftovers are its own. A failed fetch, pin check or
        # venv build has already removed its version tree, so it drops PENDING too.
        os.makedirs(self.paths.opt, mode=0o755, exist_ok=True)
        # A PENDING left by an earlier failed upgrade still marks that partial apply.
        earlier_pending = os.path.lexists(self.paths.pending)
        _atomic_write(self.paths.pending, values["version"] + "\n")
        try:
            self._ensure_version(values)
        except InstallError:
            if not earlier_pending:
                os.unlink(self.paths.pending)
            raise
        resource_paths = self._resource_paths()
        socket_name = "topos-observe.socket"
        # From here on a failure leaves PENDING in place: the next install refuses
        # and points at `toposd uninstall`, which accepts a PENDING-marked host.
        resource_changed: dict[str, bool] = {}
        for name, content in rendered.items():
            try:
                resource_changed[name] = Path(resource_paths[name]).read_text(encoding="utf-8") != content
            except OSError:
                resource_changed[name] = True

        was_active = self._is_active("topos.service")
        socket_was_active = self._is_active(socket_name)

        if config_changed:
            # An edit keeps the operator's file mode; only a new file gets 0644.
            _atomic_write(self.paths.config, config_text, config_mode if config_mode is not None else 0o644)
        for name, content in rendered.items():
            _atomic_write(resource_paths[name], content)
        if os.path.lexists(self.paths.current_new):  # left by a failed upgrade being re-applied
            os.unlink(self.paths.current_new)
        os.symlink(values["version"], self.paths.current_new)
        os.replace(self.paths.current_new, self.paths.current)

        group_missing_before = _group_gid(values["observe_group"]) is None
        if rendered["topos.sysusers"].startswith("g "):
            self._command(["systemd-sysusers", resource_paths["topos.sysusers"]], timeout=30)
            if group_missing_before and _group_gid(values["observe_group"]) is None:
                raise InstallError(f"systemd-sysusers did not create configured group {values['observe_group']!r}")
        created_groups = list(values["prior_created_groups"])
        if group_missing_before and values["observe_group"] not in created_groups:
            created_groups.append(values["observe_group"])
        record = {
            "wheel": values["wheel"],
            "cli_extended_wheel": values["cli_extended_wheel"],
            "daemon_lock": values["daemon_lock"],
            **({"augeaswrap_wheel": values["augeaswrap_wheel"]} if values.get("augeaswrap_wheel") else {}),
            "wheel_source": values["wheel_source"],
            "observe_group": values["observe_group"],
            "observe_users": values["observe_users"],
            "enable_now": values["enable_now"],
            "created_groups": created_groups,
        }
        record_text = _render_record(record)
        # PENDING makes this complete record a recoverable failed first install.
        _atomic_write(self.paths.install_record, record_text)
        # Members are only ever added: install and uninstall never remove one.
        for user in values["observe_users"]:
            self._command(["gpasswd", "-a", user, values["observe_group"]], timeout=30)

        # A re-apply over an earlier PENDING compares against files the failed
        # attempt already wrote, so drift is invisible: reload and restart anyway.
        if earlier_pending:
            resource_changed = dict.fromkeys(resource_changed, True)
        if any(resource_changed.values()) or version_drift:
            self._command([self.systemctl, "daemon-reload"], timeout=30)

        _atomic_symlink(self.paths.at("/opt/topos/current/venv/bin/topos"), self.paths.client_link,
                        self.paths.client_link + ".topos-new")
        _atomic_symlink(self.paths.at("/opt/topos/current/venv/bin/toposd"), self.paths.daemon_link,
                        self.paths.daemon_link + ".toposd-new")

        enabled_now = values["enable_now"]
        if enabled_now:
            self._command([self.systemctl, "enable", "topos.service", socket_name], timeout=60)
            if not socket_was_active:
                self._command([self.systemctl, "start", socket_name], timeout=30)
            if not was_active:
                self._command([self.systemctl, "start", "topos.service"], timeout=60)
        if was_active:
            # A socket restart restarts the service through Requires=.
            if resource_changed.get(socket_name, False) and socket_was_active:
                self._command([self.systemctl, "restart", socket_name], timeout=60)
            elif config_changed or version_drift or resource_changed.get("topos.service", False):
                self._command([self.systemctl, "restart", "topos.service"], timeout=60)

        if was_active or enabled_now:
            self._wait_ready(self.paths.at(config.sockets.observe.path.as_posix()), values["version"])

        removed = self._prune_versions(values["version"], running_version)
        _atomic_write(self.paths.install_record, record_text)
        endpoint_entries = _endpoint_entries(config, resolve_gid=True)
        endpoint_writer = f"toposd install {values['version']}"
        try:
            self._endpoints_api().publish(
                "topos", endpoint_entries, writer=endpoint_writer, root=self.paths.root
            )
        except InstallError:
            raise
        except Exception as exc:
            raise InstallError(f"cannot publish topos host endpoint record: {exc}") from None
        endpoint_fragment = {
            "owner": "topos",
            "path": self.paths.endpoint_fragment,
            "writer": endpoint_writer,
            "entries": endpoint_entries,
            "unresolved_groups": {},
        }
        os.unlink(self.paths.pending)
        return {
            "ok": True,
            "dry_run": False,
            "version": values["version"],
            "config": self.paths.config,
            "observe_group": values["observe_group"],
            "config_changes": config_changes,
            "endpoint_fragment": endpoint_fragment,
            "config_changed": config_changed,
            "users_added": values["observe_users"],
            "users_forgotten": values["users_forgotten"],
            "resources_changed": sorted(name for name, changed in resource_changed.items() if changed),
            "pruned": removed,
            "enabled": values["enable_now"],
            "downgrade": downgrade,
        }

    def _config(self, group: str, *, apply_group: bool = True) -> tuple[str, ServiceConfig, bool]:
        """Return validated config text, applying only an explicitly chosen group."""
        try:
            existing: str | None = Path(self.paths.config).read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        except (OSError, UnicodeDecodeError) as exc:
            raise InstallError(f"cannot read {self.paths.config}: {exc}") from None
        text = _render_config(group) if existing is None else existing
        try:
            config = parse_config(text, source=self.paths.config)
        except ConfigError as exc:
            raise InstallError(f"{self.paths.config} is invalid: {exc}; fix or remove it, then rerun") from None
        if existing is not None and apply_group and group != config.sockets.observe.group:
            text = _edit_config_group(existing, group, self.paths.config)
            if text != existing:
                try:
                    config = parse_config(text, source=self.paths.config)
                except ConfigError as exc:
                    raise InstallError(
                        f"editing sockets.observe.group would make {self.paths.config} invalid: {exc}; "
                        f"edit [sockets.observe] group = {_toml_string(group)} by hand, then rerun"
                    ) from None
        # The layout is fixed (plan 3.1): the old-layout scan, doctor and uninstall
        # all rely on it, so a kept config may change the group and mode only.
        for key, value, fixed in (
            ("state_dir", config.state_dir, DEFAULTS.state_dir),
            ("sockets.observe.path", config.sockets.observe.path, DEFAULTS.sockets.observe.path),
        ):
            if value != fixed:
                raise InstallError(
                    f"{self.paths.config} sets {key} = {value.as_posix()!r}; topos uses the fixed path "
                    f"{fixed.as_posix()!r}, so remove the key or restore that value"
                )
        return text, config, existing is None or text != existing

    def _uninstall_targets(self) -> list[str] | None:
        """Refuse before any change unless toposd install owns this host.

        Returns the existing paths uninstall would remove, or None when nothing
        toposd install writes is present.
        """
        paths = self.paths
        owned = (
            os.path.lexists(paths.install_record)
            or os.path.lexists(paths.pending)
            or os.path.lexists(paths.endpoint_fragment)
        )
        # The plan has uninstall remove host-setup's socket-group drop-in so a role can
        # leave cleanly; every other foreign rule or old-layout path is refused.
        mdt_dropin = os.path.join(paths.service_dropins, "50-mdt-socket-group.conf")
        problems = [
            item for item in old_installation_problems(paths)
            if item != f"foreign rule or drop-in: {mdt_dropin}"
            and not (owned and item.startswith(_VERSION_ROOT_FINDINGS[:3]))
        ]
        # Uninstall unlinks these, so a directory there would fail it midway. A host
        # toposd install does not own is reported as "nothing installed" instead.
        for path in (
            *(os.path.join(paths.unit_dir, unit) for unit in ("topos-observe.socket", "topos.service", "topos.slice")),
            paths.sysusers,
            mdt_dropin,
        ) if owned else ():
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                problems.append(f"cannot inspect unit path {path}: {exc}")
            else:
                if stat.S_ISDIR(info.st_mode):
                    problems.append(f"unit path is a directory: {path}")
        for directory, allowed in (
            (paths.runtime_root, {DEFAULTS.sockets.observe.path.parent.name, DEFAULTS.state_dir.name}),
            (paths.observe_dir, {DEFAULTS.sockets.observe.path.name}),
        ):
            for name in _directory_entries(directory, problems, "runtime"):
                if name not in allowed:
                    problems.append(f"unknown path under the runtime tree: {os.path.join(directory, name)}")
        for name in _directory_entries(paths.opt, problems, "version root"):
            path = os.path.join(paths.opt, name)
            known = name in {"current", "current.new", "PENDING"} or name.startswith(".stage.")
            if not known and not (VERSION_RE.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path)):
                problems.append(f"unknown path under the version root: {path}")
        if os.path.lexists(paths.socket_node) and not stat.S_ISSOCK(os.lstat(paths.socket_node).st_mode):
            problems.append(f"{paths.socket_node} is not a socket")
        if problems:
            raise InstallError(manual_removal_message(problems).replace("toposd install refused", "toposd uninstall refused"))
        if not owned:
            return None
        candidates = [
            *(os.path.join(paths.unit_dir, unit) for unit in ("topos-observe.socket", "topos.service", "topos.slice")),
            paths.sysusers,
            mdt_dropin,
            *(link for link in (paths.client_link, paths.daemon_link) if _is_install_link(link, paths)),
            paths.socket_node,
            paths.observe_dir,
            paths.state_dir,
            paths.runtime_root,
            paths.opt,
            paths.endpoint_fragment,
            paths.config,
            paths.install_record,
        ]
        return [path for path in candidates if os.path.lexists(path)]

    def _remove_version_root(self, keep: str | None) -> None:
        paths = self.paths
        try:
            for name in sorted(_directory_entries(paths.opt, [], "version root")):
                path = os.path.join(paths.opt, name)
                if name == "PENDING":
                    continue
                if keep is not None and (
                    (name == "current" and os.path.islink(path))
                    or (not os.path.islink(path) and os.path.realpath(path) == keep)
                ):
                    continue
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or paths.opt}: {exc}") from None

    def _remove_markers(self, body: dict[str, Any]) -> None:
        """Remove PENDING, /opt/topos, then ownership records and empty /etc/topos."""
        paths = self.paths
        try:
            if os.path.lexists(paths.pending):
                os.unlink(paths.pending)
            if os.path.isdir(paths.opt):
                os.rmdir(paths.opt)
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or paths.opt}: {exc}") from None
        if not body.get("groups_kept"):
            try:
                os.unlink(paths.install_record)
            except FileNotFoundError:
                pass
        config_dir = os.path.dirname(paths.config)
        try:
            os.rmdir(config_dir)
        except FileNotFoundError:
            pass
        except OSError:
            if config_dir not in body.setdefault("kept", []):
                body["kept"].append(config_dir)

    def uninstall(self, args: argparse.Namespace, runtime: CliRuntime) -> dict[str, Any]:
        record = _read_record(self.paths.install_record)
        created_groups = _record_created_groups(record, self.paths.install_record)
        remove_groups = bool(getattr(args, "remove_groups", False))
        targets = self._uninstall_targets()
        if targets is None:
            return {"ok": True, "dry_run": runtime.dry_run, "removed": [], "groups_removed": [],
                    "message": "nothing installed by toposd install"}
        if runtime.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "removed": targets,
                "groups_removed": created_groups if remove_groups else [],
            }
        if self.geteuid() != 0:
            raise InstallError("toposd uninstall needs root")
        if not runtime.yes and not runtime.confirm("Stop and remove the topos host service?"):
            raise InstallError("uninstallation was not confirmed")

        paths = self.paths
        try:
            self._endpoints_api().withdraw("topos", root=paths.root)
        except InstallError:
            raise
        except Exception as exc:
            raise InstallError(f"cannot withdraw topos host endpoint record: {exc}") from None
        # The socket goes first, so a connection during the stop cannot
        # re-activate the service.
        installed_units = [
            unit for unit in ("topos-observe.socket", "topos.service")
            if os.path.lexists(os.path.join(paths.unit_dir, unit))
        ]
        if installed_units:
            self._command([self.systemctl, "disable", "--now", *installed_units], timeout=60)
        # A /usr/local/bin entry that is not ours stays, and is reported as kept.
        kept = [link for link in (paths.client_link, paths.daemon_link)
                if os.path.lexists(link) and not _is_install_link(link, paths)]
        for path in (
            *(os.path.join(paths.unit_dir, unit) for unit in ("topos-observe.socket", "topos.service", "topos.slice")),
            paths.sysusers,
            os.path.join(paths.service_dropins, "50-mdt-socket-group.conf"),
            *(link for link in (paths.client_link, paths.daemon_link) if link not in kept),
            paths.socket_node,
        ):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot remove {path}: {exc}") from None
        for directory in (paths.service_dropins, paths.observe_dir):
            try:
                os.rmdir(directory)
            except OSError:
                pass
        for remove, directory in ((shutil.rmtree, paths.state_dir), (os.rmdir, paths.runtime_root)):
            try:
                remove(directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot remove runtime directory {directory}: {exc}") from None

        removed_groups: list[str] = []
        groups_kept: list[str] = []
        group_failures: list[str] = []
        if remove_groups:
            for group in created_groups:
                # A rerun after a partial uninstall finds some groups already gone.
                if _group_gid(group) is None:
                    continue
                try:
                    self._command(["groupdel", group], timeout=30)
                except InstallError as exc:
                    groups_kept.append(group)
                    group_failures.append(f"{group}: {exc}")
                    continue
                removed_groups.append(group)
        self._command([self.systemctl, "daemon-reload"], timeout=30)
        self._command([self.systemctl, "reset-failed", "topos.service", "topos-observe.socket", "topos.slice"],
                      check=False, timeout=30)
        try:
            os.unlink(paths.config)
        except FileNotFoundError:
            pass
        config_dir = os.path.dirname(paths.config)
        if groups_kept:
            kept.append(config_dir)
        else:
            try:
                remaining = os.listdir(config_dir)
            except FileNotFoundError:
                remaining = []
            except OSError as exc:
                raise InstallError(f"cannot inspect {config_dir}: {exc}") from None
            if any(name != os.path.basename(paths.install_record) for name in remaining):
                kept.append(config_dir)
        body: dict[str, Any] = {
            "ok": not group_failures,
            "dry_run": False,
            "removed": [path for path in targets if not (groups_kept and path == paths.install_record)],
            "groups_removed": removed_groups,
            "groups_kept": groups_kept,
            "group_failures": group_failures,
            "kept": kept,
        }
        running = self._running_version_dir()
        self._remove_version_root(keep=running)
        if running is None:
            self._remove_markers(body)
        else:
            body["deferred"] = running
        return body

    def finish_uninstall(self, body: dict[str, Any]) -> None:
        """Remove the running version after output, then remove ownership markers."""
        running = body.pop("deferred", None)
        if running is None:
            return
        try:
            shutil.rmtree(running)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or running}: {exc}") from None
        try:
            os.unlink(self.paths.current)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise InstallError(f"cannot remove {self.paths.current}: {exc}") from None
        self._remove_markers(body)


def _handler_paths(args: argparse.Namespace, runtime: CliRuntime) -> InstallPaths:
    root = getattr(args, "root", "/")
    # systemctl, systemd-sysusers and gpasswd always act on the live host, so a
    # staging root is only safe for a dry run.
    if os.fspath(root) != "/" and not runtime.dry_run:
        raise CliFailure("--root is only valid with --dry-run", exit_code=2)
    return InstallPaths(root)


def _install_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    paths = _handler_paths(args, runtime)
    try:
        body = Installer(paths).install(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _install_summary(body))
    return 0


def _uninstall_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    paths = _handler_paths(args, runtime)
    subject = Installer(paths)
    try:
        body = subject.uninstall(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    shown = {key: value for key, value in body.items() if key != "deferred"}
    runtime.output.primary(shown if runtime.json_mode else _uninstall_summary(shown))
    try:
        subject.finish_uninstall(body)
    except InstallError as exc:
        # The toposd that ran this lives in the tree being removed, so the operator
        # cannot rerun it; name what is left instead.
        advice = f"{exc}; finish by hand: remove {paths.opt}, then {paths.install_record}"
        if body.get("group_failures"):
            advice += f"; run groupdel for {', '.join(body.get('groups_kept', []))} once free"
        raise CliFailure(advice, exit_code=exc.exit_code) from None
    if body.get("group_failures"):
        groups = ", ".join(body.get("groups_kept", []))
        # This toposd is gone with its version tree, so it cannot be rerun.
        raise CliFailure(
            f"could not remove groups {groups}; run groupdel for them once they are free, "
            f"then remove {paths.install_record}",
            exit_code=2,
        )
    return 0


def _install_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        summary = f"dry run: topos {body['version']} would be installed; no changes made"
    else:
        changed = ", ".join(body.get("resources_changed", [])) or "no resource drift"
        summary = f"topos {body['version']} installed; {changed}; enabled={body['enabled']}"
    endpoint_fragment = body.get("endpoint_fragment")
    if isinstance(endpoint_fragment, Mapping):
        action = "would publish" if body.get("dry_run") else "published"
        summary += f"; {action} host endpoint fragment {endpoint_fragment['path']}"
    if body.get("config_changes"):
        summary += f"; set sockets.observe.group = {body['observe_group']} in {body['config']}"
    if body.get("dry_run") and body.get("users_to_add"):
        summary += f"; would add to {body['observe_group']}: {', '.join(body['users_to_add'])}"
    if body.get("users_forgotten"):
        summary += f"; dropped recorded users that no longer exist: {', '.join(body['users_forgotten'])}"
    if body.get("users_added"):
        summary += (
            f"; added to {body['observe_group']}: {', '.join(body['users_added'])} "
            "(a new login session or a restarted service picks up the membership)"
        )
    return summary


def _uninstall_summary(body: Mapping[str, Any]) -> str:
    if "message" in body:
        return str(body["message"])
    verb = "would remove" if body.get("dry_run") else "removed"
    lines = [f"{verb}: {path}" for path in body.get("removed", [])]
    lines.extend(f"{'would remove' if body.get('dry_run') else 'removed'} group: {group}"
                 for group in body.get("groups_removed", []))
    lines.extend(f"kept group: {group}" for group in body.get("groups_kept", []))
    lines.extend(f"group removal failed: {failure}" for failure in body.get("group_failures", []))
    lines.extend(f"kept (not written by toposd install): {path}" for path in body.get("kept", []))
    return "\n".join(lines) if lines else "nothing to remove"


def register_install(registry: CliRegistry) -> None:
    """Register the install and uninstall verbs with shared cli-extended controls."""
    options = (
        OptionSpec(("--wheel",), "topos wheel pin: <version>@sha256:<64 lowercase hex>", group="WHEELS", metavar="PIN"),
        OptionSpec(("--cli-extended-wheel",), "cli-extended wheel pin in the same form", group="WHEELS", metavar="PIN"),
        OptionSpec(("--augeaswrap-wheel",),
                   "augeaswrap wheel pin in the same form (required for topos 0.8.0 and later; not on any package index)",
                   group="WHEELS", metavar="PIN"),
        OptionSpec(("--daemon-lock",), "topos daemon lock pin in the same form", group="WHEELS", metavar="PIN"),
        OptionSpec(("--wheel-source",), "HTTPS release base or directory containing pinned assets and dependency wheels",
                   group="WHEELS", metavar="SOURCE"),
        OptionSpec(("--observe-group",), "create topos-observe or map observe access to an existing group (never docker)",
                   group="ACCESS", metavar="GROUP"),
        OptionSpec(("--add-user",), "add an existing user to the observe group (repeatable; members are never removed)",
                   group="ACCESS", metavar="LEVEL=USER", parser_kwargs={"action": "append", "default": None}),
        OptionSpec(("--enable",), "enable and start the installed service", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--no-enable",), "install resources without enabling or starting topos", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--allow-downgrade",), "allow installing a version older than the current one", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--root",), "installation root for dry runs and tests (default /)", group="TESTING", metavar="DIR",
                   parser_kwargs={"default": "/"}, hidden=True),
    )
    registry.register(
        VerbSpec(
            "install",
            description="install or upgrade topos and its systemd service",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            include_progress=False,
            options=options,
            handler=_install_handler,
            examples=(
                "toposd install --wheel VERSION@sha256:... --cli-extended-wheel VERSION@sha256:... --augeaswrap-wheel VERSION@sha256:... --daemon-lock VERSION@sha256:... --enable",
                "toposd install --wheel-source ./wheels --add-user observe=alice --add-user observe=bob --no-enable --yes",
            ),
        )
    )
    registry.register(
        VerbSpec(
            "uninstall",
            description="stop and remove the topos host service",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            include_progress=False,
            options=(
                OptionSpec(("--remove-groups",), "remove only groups recorded as created by toposd install",
                           group="REMOVE", parser_kwargs={"action": "store_true"}),
                OptionSpec(("--root",), "installation root for dry runs and tests (default /)", group="TESTING",
                           metavar="DIR", parser_kwargs={"default": "/"}, hidden=True),
            ),
            handler=_uninstall_handler,
            examples=("toposd uninstall --dry-run", "toposd uninstall --remove-groups --yes"),
        )
    )


__all__ = [
    "DEFAULT_RELEASE_BASE", "InstallError", "InstallPaths", "Installer", "_gather_inputs",
    "find_foreign_rules", "manual_removal_message", "old_installation_problems", "register_install",
    "version_key",
]
