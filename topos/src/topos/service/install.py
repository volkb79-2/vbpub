"""Standalone ``toposd install`` and ``toposd uninstall`` lifecycle.

The module intentionally imports only the standard library, cli-extended and
the lightweight ``topos.service`` package. It is usable from the wheel before
the daemon environment exists.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
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

from .config import ConfigError, ServiceConfig, parse_config
from .lock import LockError, parse_lock, parse_pin
from .units import render_units


DEFAULT_RELEASE_BASE = "https://github.com/volkb79-2/vbpub/releases/download"
SOCKET_RELEASE = "0.7.0"
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
        return self.at("/run/topos")

    @property
    def observe_dir(self) -> str:
        return self.at("/run/topos/observe")

    @property
    def socket_node(self) -> str:
        return self.at("/run/topos/observe/topos.sock")

    @property
    def state_dir(self) -> str:
        return self.at("/run/topos/state")

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


def _validate_users(users: Sequence[str]) -> list[str]:
    result: list[str] = []
    for user in users:
        if not USER_RE.fullmatch(user):
            raise InstallError(f"not a usable Unix user name: {user!r}")
        try:
            pwd.getpwnam(user)
        except KeyError:
            raise InstallError(f"user {user!r} does not exist") from None
        if user not in result:
            result.append(user)
    return result


def _group_gid(name: str) -> int | None:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_config(group: str) -> str:
    return (
        'state_dir = "/run/topos/state"\n\n'
        "[sockets.observe]\n"
        'path = "/run/topos/observe/topos.sock"\n'
        f"group = {_toml_string(group)}\n"
        'mode = "0660"\n'
    )


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


def manual_removal_message(found: Iterable[str] = ()) -> str:
    details = list(found)
    message = [
        "toposd install refused an old or unknown installation. Remove it manually, then rerun:",
    ]
    if details:
        message.extend(("Found:", *(f"  {item}" for item in details)))
    message.extend(
        (
            "Manual removal:",
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


def _run_tree_problems(paths: InstallPaths) -> list[str]:
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
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or actual_mode != mode:
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
    if not os.path.lexists(paths.install_record):
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
            if name == "current":
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
        prefix = "cli_extended-" if filename.startswith("cli_extended-") else "topos-"
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


def _record_created_groups(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("created_groups", [])
    if not isinstance(raw, list) or any(not isinstance(name, str) or not USER_RE.fullmatch(name) for name in raw):
        raise InstallError("the existing install record has an invalid created_groups list; repair it manually")
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


def _resolve_group(args: argparse.Namespace, record: Mapping[str, Any], runtime: CliRuntime) -> str:
    value = getattr(args, "observe_group", None) or record.get("observe_group")
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
    return DEFAULT_OBSERVE_GROUP


def _gather_inputs(args: argparse.Namespace, runtime: CliRuntime, paths: InstallPaths) -> dict[str, Any]:
    record = _read_record(paths.install_record)
    created_groups = _record_created_groups(record)

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
    group = _resolve_group(args, record, runtime)
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", group):
        raise InstallError(f"not a usable observe group name: {group!r}")
    if _group_gid(group) is None and group != DEFAULT_OBSERVE_GROUP:
        raise InstallError(f"mapped observe group {group!r} does not exist")

    users_raw = getattr(args, "observe_users", None)
    if users_raw is None:
        users_raw = record.get("observe_users")
    if users_raw is None and runtime.output.is_interactive and not runtime.dry_run and not runtime.json_mode:
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
    users = _validate_users(raw_users)

    if getattr(args, "enable", False) and getattr(args, "no_enable", False):
        raise InstallError("choose only one of --enable and --no-enable")
    if getattr(args, "enable", False):
        enable_now: bool | None = True
    elif getattr(args, "no_enable", False):
        enable_now = False
    elif "enable_now" in record and isinstance(record["enable_now"], bool):
        enable_now = record["enable_now"]
    elif runtime.dry_run or runtime.json_mode or not runtime.output.is_interactive:
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
    version_key(version)
    if lock_version != version:
        raise InstallError(f"--daemon-lock version {lock_version} must equal topos wheel version {version}")
    return {
        "wheel": wheel,
        "cli_extended_wheel": clix,
        "daemon_lock": lock_pin,
        "wheel_source": str(source),
        "observe_group": group,
        "observe_users": users,
        "enable_now": bool(enable_now),
        "allow_downgrade": bool(getattr(args, "allow_downgrade", False)),
        "prior_created_groups": created_groups,
        "version": version,
        "wheel_sha": wheel_sha,
        "cli_extended_version": clix_version,
        "cli_extended_sha": clix_sha,
        "lock_version": lock_version,
        "lock_sha": lock_sha,
    }


def _is_socket_unit_version(version: str) -> bool:
    return version_key(version) >= version_key(SOCKET_RELEASE)


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
        systemctl: str = "systemctl",
    ) -> None:
        self.paths = paths
        self.run = run
        self.fetch = fetch
        self.geteuid = geteuid
        self.sleep = sleep
        self.monotonic = monotonic
        self.python = python
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
            if any(f"{key}={value}" not in lines for key, value in expected.items()):
                raise InstallError(f"{version_dir} is immutable and its manifest differs from the requested pins")
        elif os.path.lexists(version_dir):
            raise InstallError(f"unmarked version directory {version_dir} exists; remove it manually")
        current = self._current_version()
        downgrade = bool(current and version_key(values["version"]) < version_key(current))
        socket_files = (os.path.join(self.paths.unit_dir, "topos-observe.socket"),)
        socket_installed = any(os.path.lexists(path) for path in socket_files)
        legacy_target = not _is_socket_unit_version(values["version"])
        if downgrade and legacy_target and socket_installed and not values["allow_downgrade"]:
            raise InstallError(
                f"refusing downgrade from {current} to {values['version']} while socket units are installed; "
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

        stage = tempfile.mkdtemp(prefix=".stage.", dir=self.paths.opt)
        build_dir = os.path.join(stage, values["version"])
        wheels = os.path.join(stage, "wheels")
        os.makedirs(wheels)
        wheel_name = f"topos-{values['version']}-py3-none-any.whl"
        clix_name = f"cli_extended-{values['cli_extended_version']}-py3-none-any.whl"
        lock_name = f"topos-{values['version']}-daemon.lock"
        wheel_path = os.path.join(wheels, wheel_name)
        clix_path = os.path.join(wheels, clix_name)
        lock_path = os.path.join(wheels, lock_name)
        try:
            _fetch_asset(values["wheel_source"], "topos", wheel_name, wheel_path, self.fetch)
            _fetch_asset(values["wheel_source"], "cli-extended", clix_name, clix_path, self.fetch)
            _fetch_asset(values["wheel_source"], "topos", lock_name, lock_path, self.fetch)
            for label, path, digest in (
                (wheel_name, wheel_path, values["wheel_sha"]),
                (clix_name, clix_path, values["cli_extended_sha"]),
                (lock_name, lock_path, values["lock_sha"]),
            ):
                if _sha256(path) != digest:
                    raise InstallError(f"sha256 of {label} does not match the pin; refusing before unpack")
            try:
                parse_lock(Path(lock_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, LockError) as exc:
                raise InstallError(f"daemon lock {lock_name} is invalid: {exc}") from None
            os.makedirs(build_dir, mode=0o755)
            self._command([self.python, "-m", "venv", os.path.join(build_dir, "venv")], timeout=120)
            venv_python = os.path.join(build_dir, "venv", "bin", "python")
            dep_command = [venv_python, "-m", "pip", "install", "--require-hashes", "-r", lock_path]
            if os.path.isdir(values["wheel_source"]):
                dep_command.extend(("--no-index", "--find-links", values["wheel_source"]))
            self._command(dep_command, timeout=600)
            self._command(
                [venv_python, "-m", "pip", "install", "--no-deps", wheel_path, clix_path],
                timeout=600,
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
                    "installer=toposd install",
                    "",
                )
            )
            _atomic_write(os.path.join(build_dir, "MANIFEST"), manifest_text)
            os.replace(build_dir, version_dir)
        except InstallError:
            raise
        except OSError as exc:
            raise InstallError(f"cannot build immutable version tree {version_dir}: {exc}") from None
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

    def _prune_versions(self, current: str) -> list[str]:
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
            if name not in keep:
                shutil.rmtree(os.path.join(self.paths.opt, name))
                removed.append(name)
        return removed

    def _apply_downgrade(self, values: Mapping[str, Any], resource_paths: Mapping[str, str]) -> None:
        if not values["allow_downgrade"] or _is_socket_unit_version(values["version"]):
            return
        socket_unit = "topos-observe.socket"
        if os.path.lexists(resource_paths[socket_unit]):
            self._command([self.systemctl, "disable", "--now", socket_unit], timeout=30)
            os.unlink(resource_paths[socket_unit])
        try:
            info = os.lstat(self.paths.socket_node)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise InstallError(f"cannot inspect observe socket {self.paths.socket_node}: {exc}") from None
        if stat.S_ISSOCK(info.st_mode):
            os.unlink(self.paths.socket_node)
        else:
            raise InstallError(f"refusing to remove non-socket node at {self.paths.socket_node} during downgrade")

    def install(self, args: argparse.Namespace, runtime: CliRuntime) -> dict[str, Any]:
        values = _gather_inputs(args, runtime, self.paths)
        downgrade, version_drift = self._preflight(values, dry_run=runtime.dry_run)
        config_text = _render_config(values["observe_group"])
        try:
            config = parse_config(config_text, source=self.paths.config)
        except ConfigError as exc:
            raise InstallError(f"rendered config refused: {exc}") from None
        rendered = render_units(config)
        if not _is_socket_unit_version(values["version"]):
            # Pre-0.7 releases have no socket activation. Keep the rollback
            # service explicit and remove the Requires=/Sockets= relationship.
            rendered.pop("topos-observe.socket")
            rendered["topos.service"] = (
                rendered["topos.service"]
                .replace("Requires=topos-observe.socket\n", "")
                .replace("After=network.target topos-observe.socket", "After=network.target")
                .replace("Sockets=topos-observe.socket\n", "")
                .replace(" run --config ", " run --self-bind --config ")
            )
        if runtime.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "version": values["version"],
                "config": self.paths.config,
                "resources": sorted(rendered),
                "downgrade": downgrade,
                "enabled": values["enable_now"],
                "message": "no files, wheels, groups, links, or services changed",
            }
        if not runtime.yes and not runtime.confirm("Install or upgrade the topos host service?"):
            raise InstallError("installation was not confirmed")

        # The PENDING marker makes an interrupted mutation visible to P-11.
        os.makedirs(self.paths.opt, mode=0o755, exist_ok=True)
        _atomic_write(self.paths.pending, values["version"] + "\n")
        venv = self._ensure_version(values)
        resource_paths = self._resource_paths()
        socket_name = "topos-observe.socket"
        try:
            self._apply_downgrade(values, resource_paths)
            config_changed = True
            try:
                config_changed = Path(self.paths.config).read_text(encoding="utf-8") != config_text
            except OSError:
                pass
            resource_changed: dict[str, bool] = {}
            for name, content in rendered.items():
                try:
                    resource_changed[name] = Path(resource_paths[name]).read_text(encoding="utf-8") != content
                except OSError:
                    resource_changed[name] = True

            was_active = self._is_active("topos.service")
            socket_was_active = self._is_active(socket_name) if _is_socket_unit_version(values["version"]) else False
            if self.geteuid() != 0:
                raise InstallError("toposd install needs root")

            _atomic_write(self.paths.config, config_text)
            for name, content in rendered.items():
                _atomic_write(resource_paths[name], content)
            link_target = values["version"]
            os.symlink(link_target, self.paths.current_new)
            os.replace(self.paths.current_new, self.paths.current)

            group_missing_before = _group_gid(values["observe_group"]) is None
            if rendered["topos.sysusers"].startswith("g "):
                self._command(["systemd-sysusers", resource_paths["topos.sysusers"]], timeout=30)
                if group_missing_before and _group_gid(values["observe_group"]) is None:
                    raise InstallError(f"systemd-sysusers did not create configured group {values['observe_group']!r}")
            for user in values["observe_users"]:
                self._command(["usermod", "-a", "-G", values["observe_group"], user], timeout=30)

            units_changed = any(resource_changed.values())
            if units_changed or version_drift:
                self._command([self.systemctl, "daemon-reload"], timeout=30)

            _atomic_symlink(self.paths.at("/opt/topos/current/venv/bin/topos"), self.paths.client_link,
                            self.paths.client_link + ".topos-new")
            _atomic_symlink(self.paths.at("/opt/topos/current/venv/bin/toposd"), self.paths.daemon_link,
                            self.paths.daemon_link + ".toposd-new")

            enabled_now = values["enable_now"]
            legacy = not _is_socket_unit_version(values["version"])
            managed_units = ["topos.service"] + ([] if legacy else [socket_name])
            if enabled_now:
                self._command([self.systemctl, "enable", *managed_units], timeout=60)
                if not legacy and not socket_was_active:
                    self._command([self.systemctl, "start", socket_name], timeout=30)
                if not was_active:
                    self._command([self.systemctl, "start", "topos.service"], timeout=60)
            if was_active:
                if not legacy and resource_changed.get(socket_name, False) and socket_was_active:
                    self._command([self.systemctl, "restart", socket_name], timeout=60)
                elif config_changed or version_drift or resource_changed.get("topos.service", False):
                    self._command([self.systemctl, "restart", "topos.service"], timeout=60)

            if was_active or enabled_now:
                self._wait_ready(self.paths.at(config.sockets.observe.path.as_posix()), values["version"])

            created_groups = list(values["prior_created_groups"])
            if group_missing_before and values["observe_group"] not in created_groups:
                created_groups.append(values["observe_group"])
            removed = self._prune_versions(values["version"])
            record = {
                "wheel": values["wheel"],
                "cli_extended_wheel": values["cli_extended_wheel"],
                "daemon_lock": values["daemon_lock"],
                "wheel_source": values["wheel_source"],
                "observe_group": values["observe_group"],
                "observe_users": values["observe_users"],
                "enable_now": values["enable_now"],
                "created_groups": created_groups,
            }
            _atomic_write(self.paths.install_record, _render_record(record))
            os.unlink(self.paths.pending)
            return {
                "ok": True,
                "dry_run": False,
                "version": values["version"],
                "config_changed": config_changed,
                "resources_changed": sorted(name for name, changed in resource_changed.items() if changed),
                "pruned": removed,
                "enabled": values["enable_now"],
                "downgrade": downgrade,
            }
        except BaseException:
            # Leave PENDING in place after any partial apply; the next install
            # must require manual inspection rather than guessing what landed.
            raise

    def uninstall(self, args: argparse.Namespace, runtime: CliRuntime) -> dict[str, Any]:
        record = _read_record(self.paths.install_record)
        created_groups = _record_created_groups(record)
        if runtime.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "units": ["topos.service", "topos-observe.socket", "topos.slice"],
                "remove_groups": bool(getattr(args, "remove_groups", False)),
            }
        if self.geteuid() != 0:
            raise InstallError("toposd uninstall needs root")
        if not runtime.yes and not runtime.confirm("Stop and remove the topos host service?"):
            raise InstallError("uninstallation was not confirmed")

        units = ("topos.service", "topos-observe.socket")
        unit_paths = self._resource_paths()
        installed_units = [unit for unit in units if os.path.lexists(os.path.join(self.paths.unit_dir, unit))]
        if installed_units:
            self._command([self.systemctl, "disable", "--now", *installed_units], timeout=60)
        for unit in units + ("topos.slice",):
            try:
                os.unlink(os.path.join(self.paths.unit_dir, unit))
            except FileNotFoundError:
                pass
        try:
            os.unlink(self.paths.sysusers)
        except FileNotFoundError:
            pass

        legacy_dropin = os.path.join(self.paths.service_dropins, "50-mdt-socket-group.conf")
        try:
            os.unlink(legacy_dropin)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self.paths.service_dropins)
        except OSError:
            pass

        for path in (self.paths.config, self.paths.install_record):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(os.path.dirname(self.paths.config))
        except OSError:
            pass

        for link in (self.paths.client_link, self.paths.daemon_link):
            try:
            if os.path.islink(link) and os.readlink(link).startswith(self.paths.opt + os.sep):
                    os.unlink(link)
            except FileNotFoundError:
                pass

        for socket_path in (self.paths.socket_node, self.paths.at("/run/topos/topos.sock")):
            try:
                info = os.lstat(socket_path)
            except FileNotFoundError:
                continue
            if stat.S_ISSOCK(info.st_mode):
                os.unlink(socket_path)
            else:
                raise InstallError(f"refusing to remove non-socket node at {socket_path}")
        for directory in (self.paths.observe_dir,):
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot remove runtime directory {directory}: {exc}") from None
        if os.path.islink(self.paths.state_dir):
            os.unlink(self.paths.state_dir)
        elif os.path.isdir(self.paths.state_dir):
            shutil.rmtree(self.paths.state_dir)
        try:
            os.rmdir(self.paths.runtime_root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if os.path.islink(self.paths.runtime_root):
                os.unlink(self.paths.runtime_root)
            elif os.listdir(self.paths.runtime_root):
                raise InstallError(f"cannot remove non-empty runtime directory {self.paths.runtime_root}: {exc}") from None

        for tree in (self.paths.opt, os.path.dirname(self.paths.config)):
            try:
                shutil.rmtree(tree)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot remove {tree}: {exc}") from None

        removed_groups: list[str] = []
        if getattr(args, "remove_groups", False):
            for group in created_groups:
                self._command(["groupdel", group], timeout=30)
                removed_groups.append(group)
        self._command([self.systemctl, "daemon-reload"], timeout=30)
        self._command([self.systemctl, "reset-failed", "topos.service", "topos-observe.socket", "topos.slice"],
                      check=False, timeout=30)
        return {
            "ok": True,
            "dry_run": False,
            "units_removed": ["topos.service", "topos-observe.socket", "topos.slice"],
            "groups_removed": removed_groups,
        }


def _install_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    try:
        body = Installer(InstallPaths(getattr(args, "root", "/"))).install(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _install_summary(body))
    return 0


def _uninstall_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    try:
        body = Installer(InstallPaths(getattr(args, "root", "/"))).uninstall(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _uninstall_summary(body))
    return 0


def _install_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        return f"dry run: topos {body['version']} would be installed; no changes made"
    changed = ", ".join(body.get("resources_changed", [])) or "no resource drift"
    return f"topos {body['version']} installed; {changed}; enabled={body['enabled']}"


def _uninstall_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        return "dry run: topos service, units and install files would be removed"
    return "topos service, units and install files removed"


def register_install(registry: CliRegistry) -> None:
    """Register the install and uninstall verbs with shared cli-extended controls."""
    options = (
        OptionSpec(("--wheel",), "topos wheel pin: <version>@sha256:<64 lowercase hex>", group="WHEELS", metavar="PIN"),
        OptionSpec(("--cli-extended-wheel",), "cli-extended wheel pin in the same form", group="WHEELS", metavar="PIN"),
        OptionSpec(("--daemon-lock",), "topos daemon lock pin in the same form", group="WHEELS", metavar="PIN"),
        OptionSpec(("--wheel-source",), "HTTPS release base or directory containing pinned assets and dependency wheels",
                   group="WHEELS", metavar="SOURCE"),
        OptionSpec(("--observe-group",), "create topos-observe or map observe access to an existing group",
                   group="ACCESS", metavar="GROUP"),
        OptionSpec(("--observe-users",), "comma-separated existing users to add to the observe group",
                   group="ACCESS", metavar="USERS"),
        OptionSpec(("--enable",), "enable and start the installed service", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--no-enable",), "install resources without enabling or starting topos", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--allow-downgrade",), "allow rollback below the socket-unit release", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--root",), "installation root for staging and tests (default /)", group="TESTING", metavar="DIR",
                   parser_kwargs={"default": "/"}),
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
                "toposd install --wheel VERSION@sha256:... --cli-extended-wheel VERSION@sha256:... --daemon-lock VERSION@sha256:... --enable",
                "toposd install --wheel-source ./wheels --observe-group docker --no-enable --yes",
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
                OptionSpec(("--root",), "installation root for staging and tests (default /)", group="TESTING",
                           metavar="DIR", parser_kwargs={"default": "/"}),
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
