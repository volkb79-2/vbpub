"""Standalone ``cgpd install`` and ``cgpd uninstall`` service lifecycle.

This module is deliberately usable from the cgprofile wheel before a runtime
venv exists. Its dependencies are the standard library, cli-extended, and
cgprofile's stdlib-only config/unit renderers.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from cli_extended import CliFailure, CliRegistry, CliRuntime, OptionSpec, VerbGroup, VerbSpec
from cli_extended.prompts import PromptCancelled

from .. import daemon_config, unitfiles

DEFAULT_RELEASE_BASE = "https://github.com/volkb79-2/vbpub/releases/download"
SOCKET_RELEASE = "2.2.0"
VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)([A-Za-z0-9.+_-]*)$")
PIN_RE = re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*)@sha256:([0-9a-f]{64})$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]*\$?$")


class InstallError(ValueError):
    """A preflight or install condition that must be shown to the operator."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class InstallPaths:
    """Canonical service paths, with a root prefix injectable for tests."""

    root: str = "/"

    def at(self, absolute: str) -> str:
        if not os.path.isabs(absolute):
            raise ValueError(f"expected an absolute service path, got {absolute!r}")
        return absolute if self.root == "/" else os.path.join(self.root, absolute.lstrip("/"))

    @property
    def opt(self) -> str:
        return self.at("/opt/cgprofile")

    @property
    def config(self) -> str:
        return self.at(daemon_config.DEFAULT_CONFIG_PATH)

    @property
    def install_record(self) -> str:
        return self.at("/etc/cgprofile/install.toml")

    @property
    def unit_dir(self) -> str:
        return self.at(unitfiles.DEFAULT_UNIT_DIR)

    @property
    def sysusers_dir(self) -> str:
        return self.at(unitfiles.DEFAULT_SYSUSERS_DIR)

    @property
    def client_link(self) -> str:
        return self.at("/usr/local/bin/cgpctl")

    @property
    def state_data(self) -> str:
        return self.at("/var/lib/cgprofile")

    @property
    def runtime_root(self) -> str:
        return self.at("/run/cgprofile")


def parse_pin(value: str, label: str = "wheel") -> Tuple[str, str]:
    match = PIN_RE.fullmatch(value or "")
    if match is None:
        raise InstallError(f"{label} must be <version>@sha256:<64 lowercase hex>")
    return match.group(1), match.group(2)


def version_key(value: str) -> Tuple[int, int, int, int, str]:
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise InstallError(f"unsupported cgprofile version spelling: {value!r}")
    suffix = match.group(4)
    # A final release sorts after prereleases with the same numeric core.
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(not suffix), suffix)


def has_socket_units(version: str) -> bool:
    return version_key(version) >= version_key(SOCKET_RELEASE)


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smh])", value or "")
    if match is None:
        raise InstallError(f"--drain-timeout={value!r} must be a positive duration such as 10m")
    amount = int(match.group(1))
    return amount * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


def _parse_words(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        import json

        return json.dumps(value)
    return str(value)


def _render_config(values: Mapping[str, Any], version: str) -> str:
    if has_socket_units(version):
        return daemon_config.render_config(values)
    flat = {key: value for key, value in values.items() if not key.startswith("sockets.")}
    return daemon_config.render_table(flat)


def manual_removal_message(details: Iterable[str] = ()) -> str:
    listed = list(details)
    prefix = "cgpd install refused old or mismatched service state. Remove it manually, then rerun:"
    steps = [
        "review and remove each listed foreign tmpfiles rule, sysusers file, or cgpd service drop-in",
        "remove /etc/sysusers.d/mdt-cgprofile.conf (keep the cgprofile-observer group and its GID)",
        "remove /usr/local/sbin/mdt-cgprofile-install.sh and the old /opt/cgprofile installation",
        "remove /usr/local/bin/cgpctl, old cgpd.service/cgprofile.slice units, and /etc/cgprofile/cgpd.toml",
        "stop old cgpd first, run systemctl daemon-reload, then remove /run/cgprofile",
        "keep /var/lib/cgprofile unless data purge is intended",
    ]
    if listed:
        return prefix + "\n  Found: " + ", ".join(listed) + "\n  Manual steps:\n  - " + "\n  - ".join(steps)
    return prefix + "\n  - " + "\n  - ".join(steps)


def _read_old_config_keys(config_path: str) -> List[str]:
    try:
        with open(config_path, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"cannot inspect existing config {config_path}: {exc}; refusing to replace it") from None
    return sorted(set(raw) & {"ctl_socket", "observer_socket"})


def find_foreign_rules(paths: InstallPaths) -> List[str]:
    """Find old mdt tmpfiles, sysusers, or service drop-ins that claim cgpd."""
    found: List[str] = []
    for directory in (paths.at("/etc/tmpfiles.d"),
                      paths.at("/etc/systemd/system/cgpd.service.d")):
        try:
            names = sorted(os.listdir(directory))
        except FileNotFoundError:
            names = []
        except OSError as exc:
            found.append(f"cannot inspect {directory}: {exc}")
            continue
        for name in names:
            path = os.path.join(directory, name)
            if not name.endswith(".conf") or not os.path.lexists(path):
                continue
            if directory.endswith("tmpfiles.d") and name.startswith("mdt-cgprofile"):
                found.append(path)
                continue
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                found.append(f"{path} (cannot inspect: {exc})")
                continue
            if directory.endswith("tmpfiles.d") and "/run/cgprofile" in text:
                found.append(path)
            elif directory.endswith("cgpd.service.d") \
                    and ("mdt" in name.lower() or "/run/cgprofile" in text or "Group=" in text):
                found.append(path)
    old_sysusers = paths.at("/etc/sysusers.d/mdt-cgprofile.conf")
    if os.path.lexists(old_sysusers):
        found.append(old_sysusers)
    return found


def _run_tree_problems(paths: InstallPaths) -> List[str]:
    expected = {
        paths.runtime_root: 0o755,
        os.path.join(paths.runtime_root, "control"): 0o755,
        os.path.join(paths.runtime_root, "observer"): 0o755,
        os.path.join(paths.runtime_root, "state"): 0o700,
    }
    problems: List[str] = []
    for path, mode in expected.items():
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(f"cannot inspect {path}: {exc.strerror}")
            continue
        actual_mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or actual_mode != mode:
            problems.append(
                f"{path} is {info.st_uid}:{info.st_gid} {actual_mode:04o}; expected root:root {mode:04o}"
            )
    return problems


def old_installation_problems(paths: InstallPaths, config_path: Optional[str] = None) -> List[str]:
    """P-11 refusal conditions. This function only inspects; it never repairs."""
    config = config_path or paths.config
    problems: List[str] = []
    old_keys = _read_old_config_keys(config)
    if old_keys:
        problems.append(f"{config}: old config keys: " + ", ".join(old_keys))
    problems.extend(f"foreign rule or drop-in: {path}" for path in find_foreign_rules(paths))
    problems.extend(_run_tree_problems(paths))
    for marker in (os.path.join(paths.opt, "PENDING"), os.path.join(paths.opt, "current.new")):
        if os.path.lexists(marker):
            problems.append(f"leftover install marker: {marker}")
    if os.path.isdir(paths.opt):
        for child in sorted(os.listdir(paths.opt)):
            version_dir = os.path.join(paths.opt, child)
            manifest = os.path.join(version_dir, "MANIFEST")
            if not os.path.isfile(manifest):
                continue
            try:
                lines = Path(manifest).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(f"cannot inspect version MANIFEST {manifest}: {exc}")
                continue
            if any(line.strip() == "installer=mdt host-setup (MDT-055)" for line in lines):
                problems.append(f"mdt host-setup MANIFEST: {manifest}")
    for path in ("/run/cgprofile/ctl.sock", "/run/cgprofile/observer/resource-observer.sock"):
        old_path = paths.at(path)
        if os.path.lexists(old_path):
            problems.append(f"old-layout socket path remains: {old_path}")
    return problems


def _fetch_wheel(source: str, project: str, filename: str, dest: str,
                 fetch: Optional[Callable[[str, str], None]] = None) -> None:
    if os.path.isdir(source):
        src = os.path.join(source, filename)
        if not os.path.isfile(src):
            raise InstallError(f"{src} not found; --wheel-source directory must contain {filename}")
        shutil.copyfile(src, dest)
        return
    base = source or DEFAULT_RELEASE_BASE
    if not base.startswith("https://"):
        raise InstallError("--wheel-source must be an HTTPS release base or a directory")
    url = f"{base.rstrip('/')}/{project}-v{filename_version(filename)}/{filename}"
    if fetch is not None:
        fetch(url, dest)
        return
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(dest, "wb") as output:
            shutil.copyfileobj(response, output)
    except Exception as exc:  # noqa: BLE001 - reported as a bounded install refusal
        raise InstallError(f"download failed for {url}: {type(exc).__name__}: {exc}") from None


def filename_version(filename: str) -> str:
    match = re.search(r"-([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*)-py3-none-any\.whl$", filename)
    if match is None:
        raise InstallError(f"unexpected wheel filename: {filename}")
    return match.group(1)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: str, data: str, mode: int = 0o644) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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


def _read_record(path: str) -> Dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"cannot read prior install record {path}: {exc}") from None
    return raw


def _record_created_groups(record: Mapping[str, Any]) -> List[str]:
    raw = record.get("created_groups", [])
    if not isinstance(raw, list) or any(not isinstance(name, str) or not USER_RE.fullmatch(name)
                                        for name in raw):
        raise InstallError("the existing install record has an invalid created_groups list; repair it manually")
    return list(dict.fromkeys(raw))


def _render_record(values: Mapping[str, Any]) -> str:
    return "".join(f"{key} = {_toml_value(values[key])}\n" for key in sorted(values))


def _group_gid(name: str) -> Optional[int]:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _parse_uid_list(value: str) -> List[int]:
    if not value.strip():
        return []
    try:
        result = [int(item.strip(), 10) for item in value.split(",") if item.strip()]
    except ValueError:
        raise InstallError("--allow-uids must be a comma-separated list of non-negative integers") from None
    if any(uid < 0 for uid in result):
        raise InstallError("--allow-uids must contain only non-negative integers")
    return list(dict.fromkeys(result))


def _validate_users(users: Sequence[str]) -> List[str]:
    result: List[str] = []
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


def _prompt_missing(runtime: CliRuntime, value: Optional[str], flag: str, message: str,
                    *, required: bool = True) -> Optional[str]:
    if value is not None:
        return value
    if runtime.dry_run or runtime.json_mode or not runtime.output.is_interactive:
        if required:
            raise InstallError(f"missing required {flag}; provide it on a non-interactive run")
        return ""
    try:
        return runtime.prompts.text(message, default="", required=required)
    except PromptCancelled:
        raise InstallError("installation prompts cancelled", exit_code=130) from None


def _prompt_group(runtime: CliRuntime, level: str, default: str, saved: Optional[str],
                  explicit: Optional[str]) -> str:
    if explicit is not None:
        return explicit
    if saved:
        return str(saved)
    flag = f"--{level}-group"
    if runtime.dry_run or runtime.json_mode or not runtime.output.is_interactive:
        raise InstallError(f"missing required {flag}; provide it on a non-interactive run")
    try:
        choice = runtime.prompts.select(
            f"{level.capitalize()} access group",
            (f"create {default}", "map an existing group"),
            default=f"create {default}",
        )
        if choice == f"create {default}":
            return default
        mapped = runtime.prompts.text(f"Existing group for {level}", required=True)
        return mapped
    except PromptCancelled:
        raise InstallError("installation prompts cancelled", exit_code=130) from None


def _gather_inputs(args: argparse.Namespace, runtime: CliRuntime, paths: InstallPaths) -> Dict[str, Any]:
    record = _read_record(paths.install_record)
    prior_created_groups = _record_created_groups(record)
    wheel = args.wheel or record.get("wheel")
    wheel = _prompt_missing(runtime, wheel, "--wheel", "cgprofile release pin")
    clix = args.cli_extended_wheel or record.get("cli_extended_wheel")
    clix = _prompt_missing(runtime, clix, "--cli-extended-wheel", "cli-extended release pin")
    control_group = _prompt_group(
        runtime, "control", daemon_config.DEFAULT_CONTROL_GROUP,
        record.get("control_group"), args.control_group,
    )
    observer_group = _prompt_group(
        runtime, "observer", daemon_config.DEFAULT_OBSERVER_GROUP,
        record.get("observer_group"), args.observer_group,
    )
    control_users_raw = args.control_users
    if control_users_raw is None:
        control_users_raw = record.get("control_users")
    if control_users_raw is None:
        control_users_raw = _prompt_missing(
            runtime, None, "--control-users", "Users to add to the control group (comma-separated)",
            required=False,
        )
    observer_users_raw = args.observer_users
    if observer_users_raw is None:
        observer_users_raw = record.get("observer_users")
    if observer_users_raw is None:
        observer_users_raw = _prompt_missing(
            runtime, None, "--observer-users", "Users to add to the observer group (comma-separated)",
            required=False,
        )
    enable_now: Optional[bool] = None
    if args.enable and args.no_enable:
        raise InstallError("choose only one of --enable and --no-enable")
    if args.enable:
        enable_now = True
    elif args.no_enable:
        enable_now = False
    elif "enable_now" in record:
        enable_now = bool(record["enable_now"])
    elif runtime.dry_run or runtime.json_mode or not runtime.output.is_interactive:
        raise InstallError("missing required --enable or --no-enable on a non-interactive run")
    else:
        try:
            enable_now = runtime.prompts.confirm("Enable and start cgpd now?", default=True)
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None
    values = {
        "wheel": str(wheel),
        "cli_extended_wheel": str(clix),
        "wheel_source": args.wheel_source or record.get("wheel_source") or DEFAULT_RELEASE_BASE,
        "control_group": control_group,
        "observer_group": observer_group,
        "control_users": _parse_words(str(control_users_raw or "")),
        "observer_users": _parse_words(str(observer_users_raw or "")),
        "gates_slice": args.gates_slice or record.get("gates_slice") or "dev-gates.slice",
        "observe_slices": _parse_words(args.observe_slices if args.observe_slices is not None
                                        else ",".join(record.get("observe_slices", []))),
        "allow_uids": _parse_uid_list(args.allow_uids if args.allow_uids is not None
                                       else ",".join(str(item) for item in record.get("allow_uids", []))),
        "drain_timeout": args.drain_timeout or record.get("drain_timeout") or "10m",
        "enable_now": bool(enable_now),
        "force_restart": bool(args.force_restart),
        "allow_downgrade": bool(args.allow_downgrade),
        "prior_created_groups": prior_created_groups,
    }
    values["control_users"] = _validate_users(values["control_users"])
    values["observer_users"] = _validate_users(values["observer_users"])
    values["drain_seconds"] = parse_duration(values["drain_timeout"])
    values["version"], values["wheel_sha"] = parse_pin(values["wheel"], "--wheel")
    values["cli_extended_version"], values["cli_extended_sha"] = parse_pin(
        values["cli_extended_wheel"], "--cli-extended-wheel")
    version_key(values["version"])
    if version_key(values["version"])[0] < 2:
        raise InstallError(f"cgprofile {values['version']} does not provide the native host service")
    sockets = {
        "sockets.control": {
            "path": daemon_config.DEFAULT_CONTROL_SOCKET,
            "group": control_group,
            "mode": "0660",
        },
        "sockets.observer": {
            "path": daemon_config.DEFAULT_OBSERVER_SOCKET,
            "group": observer_group,
            "mode": "0660",
        },
    }
    values["config_values"] = {
        "gates_slice": daemon_config.validate("gates_slice", values["gates_slice"]),
        "observe_slices": daemon_config.validate("observe_slices", values["observe_slices"]),
        "allow_uids": daemon_config.validate("allow_uids", values["allow_uids"]),
        **sockets,
    }
    _validate_group_mapping(control_group, observer_group)
    return values


def _validate_group_mapping(control: str, observer: str) -> None:
    if control == observer:
        raise InstallError("control and observer groups must differ")
    control_gid = _group_gid(control)
    observer_gid = _group_gid(observer)
    if control != daemon_config.DEFAULT_CONTROL_GROUP and control_gid is None:
        raise InstallError(f"mapped control group {control!r} does not exist")
    if observer != daemon_config.DEFAULT_OBSERVER_GROUP and observer_gid is None:
        raise InstallError(f"mapped observer group {observer!r} does not exist")
    docker_gid = _group_gid("docker")
    if observer == "docker" or (observer_gid is not None and observer_gid == docker_gid):
        raise InstallError("observer group must not be docker or share its GID")
    if control_gid is not None and observer_gid is not None and control_gid == observer_gid:
        raise InstallError("control and observer groups resolve to the same GID")


class Installer:
    """Injectable implementation of the cgpd host installer."""

    def __init__(
        self,
        paths: InstallPaths = InstallPaths(),
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        fetch: Optional[Callable[[str, str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        geteuid: Callable[[], int] = os.geteuid,
        systemctl: str = "systemctl",
        python: str = sys.executable,
    ) -> None:
        self.paths = paths
        self.run = run
        self.fetch = fetch
        self.sleep = sleep
        self.monotonic = monotonic
        self.geteuid = geteuid
        self.systemctl = systemctl
        self.python = python

    def _command(self, argv: Sequence[str], *, check: bool = True, timeout: float = 60.0,
                 capture: bool = True) -> subprocess.CompletedProcess:
        try:
            result = self.run(list(argv), capture_output=capture, text=True, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"command failed to run ({' '.join(argv)}): {exc}") from None
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise InstallError(f"command failed ({' '.join(argv)}), exit {result.returncode}{suffix}")
        return result

    def _preflight(self, values: Mapping[str, Any], config_path: str, *, dry_run: bool) -> Tuple[bool, bool]:
        if self.geteuid() != 0:
            raise InstallError("cgpd install needs root")
        problems = old_installation_problems(self.paths, config_path)
        if problems:
            raise InstallError(manual_removal_message(problems))
        version_dir = os.path.join(self.paths.opt, values["version"])
        manifest_path = os.path.join(version_dir, "MANIFEST")
        if os.path.isfile(manifest_path):
            try:
                manifest = Path(manifest_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raise InstallError(f"cannot read immutable version MANIFEST {manifest_path}") from None
            if (f"cgprofile_sha256={values['wheel_sha']}" not in manifest
                    or f"cli_extended_sha256={values['cli_extended_sha']}" not in manifest):
                raise InstallError(
                    f"{version_dir} is immutable and its wheel digest differs from the pin; release a new version"
                )
        current = os.readlink(os.path.join(self.paths.opt, "current")) \
            if os.path.islink(os.path.join(self.paths.opt, "current")) else None
        if current and os.path.isabs(current):
            current = os.path.basename(current.rstrip("/"))
        if current:
            version_key(current)
        downgrade = bool(current and version_key(values["version"]) < version_key(current))
        socket_units = [
            os.path.join(self.paths.unit_dir, "cgprofile-control.socket"),
            os.path.join(self.paths.unit_dir, "cgprofile-observer.socket"),
        ]
        sockets_installed = any(os.path.lexists(path) for path in socket_units)
        if downgrade and not values["allow_downgrade"]:
            if sockets_installed and not has_socket_units(values["version"]):
                raise InstallError(
                    f"refusing downgrade from {current} to {values['version']} while socket units are installed; "
                    "review the rollback and rerun with --allow-downgrade"
                )
            raise InstallError(
                f"refusing downgrade from {current} to {values['version']}; rerun with --allow-downgrade"
            )
        if values["allow_downgrade"] and has_socket_units(values["version"]) is False and sockets_installed:
            downgrade = True
        return downgrade, current != values["version"]

    def _live_sessions(self, current: str) -> List[Dict[str, Any]]:
        ctl = os.path.join(self.paths.opt, current, "venv", "bin", "cgpctl")
        result = self._command([ctl, "sessions", "--state", "live", "--json"], check=False, timeout=30)
        if result.returncode != 0:
            raise InstallError("cannot list live cgprofile sessions; refusing to restart")
        try:
            doc = __import__("json").loads(result.stdout)
        except ValueError:
            raise InstallError("cgpctl sessions returned invalid JSON; refusing to restart") from None
        rows = doc.get("sessions") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise InstallError("cgpctl sessions returned no session list; refusing to restart")
        return [row for row in rows if isinstance(row, dict) and row.get("state", "live") == "live"]

    def _drain_or_refuse(self, current: str, values: Mapping[str, Any]) -> None:
        if values["force_restart"]:
            sessions = self._live_sessions(current)
            values["forced_sessions"] = [str(row.get("session", "?")) for row in sessions]
            return
        deadline = self.monotonic() + values["drain_seconds"]
        while True:
            sessions = self._live_sessions(current)
            if not sessions:
                return
            if self.monotonic() >= deadline:
                marker = os.path.join(self.paths.opt, "PENDING")
                os.makedirs(self.paths.opt, exist_ok=True)
                _atomic_write(marker, values["version"] + "\n")
                labels = ", ".join(str(row.get("session", "?")) for row in sessions)
                raise InstallError(
                    f"live sessions remain at the {values['drain_timeout']} drain deadline ({labels}); "
                    f"version {values['version']} is pending; remove {marker} after reviewing it and retry",
                    exit_code=3,
                )
            self.sleep(min(5.0, max(0.1, deadline - self.monotonic())))

    def _ensure_version(self, values: Mapping[str, Any]) -> str:
        opt = self.paths.opt
        os.makedirs(opt, mode=0o755, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".stage.", dir=opt)
        version_dir = os.path.join(opt, values["version"])
        venv = os.path.join(version_dir, "venv")
        manifest = os.path.join(version_dir, "MANIFEST")
        try:
            if not os.path.isfile(manifest):
                if os.path.lexists(version_dir):
                    shutil.rmtree(version_dir)
                wheels = os.path.join(stage, "wheels")
                os.makedirs(wheels)
                wheel = f"cgprofile-{values['version']}-py3-none-any.whl"
                clix = f"cli_extended-{values['cli_extended_version']}-py3-none-any.whl"
                _fetch_wheel(values["wheel_source"], "cgprofile", wheel,
                             os.path.join(wheels, wheel), self.fetch)
                _fetch_wheel(values["wheel_source"], "cli-extended", clix,
                             os.path.join(wheels, clix), self.fetch)
                if _sha256(os.path.join(wheels, wheel)) != values["wheel_sha"]:
                    raise InstallError(f"sha256 of {wheel} does not match the pin; refusing before unpack")
                if _sha256(os.path.join(wheels, clix)) != values["cli_extended_sha"]:
                    raise InstallError(f"sha256 of {clix} does not match the pin; refusing before unpack")
                os.makedirs(version_dir, mode=0o755)
                try:
                    self._command([self.python, "-m", "venv", venv], timeout=120)
                    self._command([
                        os.path.join(venv, "bin", "python"), "-m", "pip", "install", "--quiet",
                        "--no-index", "--find-links", wheels, f"cgprofile=={values['version']}",
                    ], timeout=600)
                    got = self._command([os.path.join(venv, "bin", "cgpd"), "--version"], timeout=30)
                    if values["version"] not in (got.stdout + got.stderr):
                        raise InstallError(
                            f"installed cgpd reports {(got.stdout + got.stderr).strip()!r}, expected {values['version']}"
                        )
                    manifest_text = "\n".join((
                        f"cgprofile_wheel={wheel}",
                        f"cgprofile_sha256={values['wheel_sha']}",
                        f"cli_extended_wheel={clix}",
                        f"cli_extended_sha256={values['cli_extended_sha']}",
                        f"version={values['version']}",
                        f"installed_at={datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
                        f"installer=cgpd install {values['version']}",
                        "",
                    ))
                    _atomic_write(manifest, manifest_text, 0o644)
                except BaseException:
                    shutil.rmtree(version_dir, ignore_errors=True)
                    raise
            if not os.path.isfile(os.path.join(venv, "bin", "cgpd")):
                raise InstallError(f"{venv}/bin/cgpd is missing")
            return venv
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _target_config(self, args: argparse.Namespace, values: Mapping[str, Any]) -> str:
        text = _render_config(values["config_values"], values["version"])
        if has_socket_units(values["version"]):
            try:
                daemon_config.parse_table(text)
            except daemon_config.ConfigError as exc:
                raise InstallError(f"rendered config refused: {exc}") from None
        return text

    def _render_resources(self, venv: str, config_path: str, version: str) -> Dict[str, str]:
        cgpd = os.path.join(venv, "bin", "cgpd")
        result: Dict[str, str] = {}
        names = ["cgprofile.slice", "cgpd.service"]
        if has_socket_units(version):
            names.extend(("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers"))
        for name in names:
            proc = self._command([
                cgpd, "--config", config_path, "unit", "print",
                "--install-root", unitfiles.DEFAULT_INSTALL_ROOT, "--unit", name,
            ], check=False, timeout=30)
            if proc.returncode != 0:
                raise InstallError(f"cgpd unit print refused {name}: {(proc.stderr or proc.stdout).strip()}")
            result[name] = proc.stdout
        return result

    def _resource_paths(self) -> Dict[str, str]:
        return {
            "cgprofile.slice": os.path.join(self.paths.unit_dir, "cgprofile.slice"),
            "cgpd.service": os.path.join(self.paths.unit_dir, "cgpd.service"),
            "cgprofile-control.socket": os.path.join(self.paths.unit_dir, "cgprofile-control.socket"),
            "cgprofile-observer.socket": os.path.join(self.paths.unit_dir, "cgprofile-observer.socket"),
            "cgprofile.sysusers": os.path.join(self.paths.sysusers_dir, "cgprofile.conf"),
        }

    def _is_active(self, unit: str) -> bool:
        return self._command([self.systemctl, "is-active", "--quiet", unit],
                             check=False, timeout=15).returncode == 0

    def _fixed_config_changed(self, old_path: str, new_values: Mapping[str, Any],
                              config_text: str) -> bool:
        try:
            old_values, _old_digest = daemon_config.read_config_file(old_path, explicit=False)
            new_values_parsed = daemon_config.parse_table(config_text)
        except daemon_config.ConfigError:
            return True
        for key, spec in daemon_config.KEYS.items():
            if spec.reloadable:
                continue
            if old_values.get(key, spec.default) != new_values_parsed.get(key, spec.default):
                return True
        return False

    def _wait_ready(self, venv: str, version: str, timeout: float = 30.0) -> None:
        cgpctl = os.path.join(venv, "bin", "cgpctl")
        deadline = self.monotonic() + timeout
        while True:
            result = self._command([cgpctl, "status", "--json"],
                                   check=False, timeout=10)
            if result.returncode == 0:
                try:
                    body = __import__("json").loads(result.stdout)
                except ValueError:
                    body = {}
                daemon = body.get("daemon") if isinstance(body, dict) else None
                config = body.get("config") if isinstance(body, dict) else None
                if (isinstance(daemon, dict) and daemon.get("version") == version
                        and isinstance(config, dict) and config.get("last_reload_error") is None):
                    return
            if self.monotonic() >= deadline:
                raise InstallError(
                    f"daemon did not report version {version} with a clean config within {timeout:g}s; "
                    "inspect journalctl -u cgpd"
                )
            self.sleep(min(1.0, max(0.1, deadline - self.monotonic())))

    def _prune_versions(self, current: str) -> List[str]:
        if not os.path.isdir(self.paths.opt):
            return []
        candidates: List[str] = []
        for name in os.listdir(self.paths.opt):
            path = os.path.join(self.paths.opt, name)
            if VERSION_RE.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path):
                candidates.append(name)
        newest = sorted(candidates, key=version_key, reverse=True)[:2]
        keep = set(newest) | {current}
        removed: List[str] = []
        for name in candidates:
            if name in keep:
                continue
            shutil.rmtree(os.path.join(self.paths.opt, name))
            removed.append(name)
        return removed

    def _apply_downgrade(self, values: Mapping[str, Any], resource_paths: Mapping[str, str]) -> None:
        if not values["allow_downgrade"]:
            return
        socket_names = ("cgprofile-control.socket", "cgprofile-observer.socket")
        if not has_socket_units(values["version"]):
            installed_sockets = [name for name in socket_names
                                 if os.path.lexists(resource_paths[name])]
            if installed_sockets:
                self._command([self.systemctl, "disable", "--now", *installed_sockets], timeout=30)
            for name in socket_names:
                try:
                    os.unlink(resource_paths[name])
                except FileNotFoundError:
                    pass
            sysusers = resource_paths["cgprofile.sysusers"]
            try:
                os.unlink(sysusers)
            except FileNotFoundError:
                pass
            sockets = daemon_config.socket_settings(values.get("config_values", {}))
            for level in daemon_config.SOCKET_LEVELS:
                socket_path = self.paths.at(sockets[level]["path"])
                try:
                    info = os.lstat(socket_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise InstallError(f"cannot inspect socket node {socket_path}: {exc}") from None
                else:
                    if stat.S_ISSOCK(info.st_mode):
                        os.unlink(socket_path)
                    else:
                        raise InstallError(f"refusing to remove non-socket node at {socket_path} during downgrade")
            for path in ("/run/cgprofile/control", "/run/cgprofile/observer"):
                try:
                    os.rmdir(self.paths.at(path))
                except OSError:
                    pass
    def install(self, args: argparse.Namespace, runtime: CliRuntime) -> Dict[str, Any]:
        config_path = args.config or self.paths.config
        values = _gather_inputs(args, runtime, self.paths)
        downgrade, version_drift = self._preflight(values, config_path, dry_run=runtime.dry_run)
        config_text = self._target_config(args, values)
        try:
            daemon_config.parse_table(config_text)
        except daemon_config.ConfigError as exc:
            raise InstallError(f"rendered config refused: {exc}") from None
        if runtime.dry_run:
            return {
                "ok": True, "dry_run": True, "version": values["version"],
                "config": config_path, "socket_mode": "activated" if has_socket_units(values["version"]) else "legacy rollback",
                "message": "no files, wheels, groups, units, or services changed",
            }

        venv = self._ensure_version(values)
        staged_dir = tempfile.mkdtemp(prefix=".cgpd-render.", dir=self.paths.opt)
        config_stage = os.path.join(staged_dir, "cgpd.toml")
        try:
            _atomic_write(config_stage, config_text, 0o644)
            check = self._command([os.path.join(venv, "bin", "cgpd"), "--config", config_stage,
                                   "config", "check"], check=False, timeout=30)
            if check.returncode != 0:
                raise InstallError(f"cgpd config check refused the rendered config: {(check.stderr or check.stdout).strip()}")
            rendered = self._render_resources(venv, config_stage, values["version"])
        finally:
            shutil.rmtree(staged_dir, ignore_errors=True)

        resource_paths = self._resource_paths()
        config_drift = True
        try:
            config_drift = Path(config_path).read_text(encoding="utf-8") != config_text
        except OSError:
            pass
        resource_drift: Dict[str, bool] = {}
        for name, text in rendered.items():
            path = resource_paths[name]
            try:
                resource_drift[name] = Path(path).read_text(encoding="utf-8") != text
            except OSError:
                resource_drift[name] = True
        removed_socket_units = not has_socket_units(values["version"]) and any(
            os.path.lexists(resource_paths[name])
            for name in ("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers")
        )
        active = self._is_active("cgpd.service")
        socket_active = {name: self._is_active(name) for name in (
            "cgprofile-control.socket", "cgprofile-observer.socket") if has_socket_units(values["version"])}
        fixed_change = self._fixed_config_changed(config_path, values["config_values"], config_text)
        resource_change = any(resource_drift.values()) or removed_socket_units
        service_restart = version_drift or resource_drift.get("cgpd.service", False) or fixed_change
        socket_restart = [name for name, is_active in socket_active.items()
                          if resource_drift.get(name, False) or not is_active]
        need_restart = service_restart or bool(socket_restart)
        if active and need_restart:
            current_link = os.readlink(os.path.join(self.paths.opt, "current"))
            if os.path.isabs(current_link):
                current_link = os.path.basename(current_link.rstrip("/"))
            self._drain_or_refuse(current_link, values)

        if runtime.output.is_interactive and not runtime.yes and active and values["force_restart"]:
            raise InstallError("--force-restart cannot bypass the live-session drain without --yes")

        self._apply_downgrade(values, resource_paths)
        _atomic_write(config_path, config_text, 0o644)
        for name, text in rendered.items():
            _atomic_write(resource_paths[name], text, 0o644)
        if not has_socket_units(values["version"]):
            for name in ("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers"):
                try:
                    os.unlink(resource_paths[name])
                except FileNotFoundError:
                    pass

        current_link = os.path.join(self.paths.opt, "current")
        next_link = os.path.join(self.paths.opt, "current.new")
        try:
            os.symlink(values["version"], next_link)
            os.replace(next_link, current_link)
        finally:
            try:
                os.unlink(next_link)
            except FileNotFoundError:
                pass

        groups_before_sysusers = {
            group for group in (values["control_group"], values["observer_group"])
            if group in (daemon_config.DEFAULT_CONTROL_GROUP, daemon_config.DEFAULT_OBSERVER_GROUP)
            and _group_gid(group) is None
        }
        for group, flag in ((values["control_group"], "control_group"),
                            (values["observer_group"], "observer_group")):
            values[flag + "_created"] = group in groups_before_sysusers
        sysusers_path = resource_paths["cgprofile.sysusers"]
        if has_socket_units(values["version"]):
            self._command(["systemd-sysusers", sysusers_path], timeout=30)
            for group in groups_before_sysusers:
                if _group_gid(group) is None:
                    raise InstallError(f"systemd-sysusers did not create configured group {group!r}")
        for group, users in ((values["control_group"], values["control_users"]),
                             (values["observer_group"], values["observer_users"])):
            for user in users:
                self._command(["usermod", "-a", "-G", group, user], timeout=30)

        os.makedirs(self.paths.unit_dir, mode=0o755, exist_ok=True)
        if resource_change or version_drift:
            self._command([self.systemctl, "daemon-reload"], timeout=30)
        link_dir = os.path.dirname(self.paths.client_link)
        os.makedirs(link_dir, mode=0o755, exist_ok=True)
        link_target = "/opt/cgprofile/current/venv/bin/cgpctl"
        link_tmp = self.paths.client_link + ".cgpd-new"
        try:
            os.symlink(link_target, link_tmp)
            os.replace(link_tmp, self.paths.client_link)
        finally:
            try:
                os.unlink(link_tmp)
            except FileNotFoundError:
                pass
        if values["enable_now"]:
            enabled = ["cgprofile.slice", "cgpd.service"]
            if has_socket_units(values["version"]):
                enabled.extend(("cgprofile-control.socket", "cgprofile-observer.socket"))
            self._command([self.systemctl, "enable", *enabled], timeout=60)
        if not active and values["enable_now"]:
            self._command([self.systemctl, "start", "cgprofile.slice"], timeout=30)
            if has_socket_units(values["version"]):
                self._command([self.systemctl, "start", "cgprofile-control.socket",
                               "cgprofile-observer.socket"], timeout=30)
            self._command([self.systemctl, "start", "cgpd.service"], timeout=60)
        elif active:
            if socket_restart:
                for name in socket_restart:
                    self._command([self.systemctl, "restart", name], timeout=60)
            elif service_restart:
                self._command([self.systemctl, "restart", "cgpd.service"], timeout=60)
            elif config_drift:
                self._command([self.systemctl, "reload", "cgpd.service"], timeout=30)
        if active or values["enable_now"]:
            self._wait_ready(venv, values["version"])

        created_groups = list(dict.fromkeys([
            *values["prior_created_groups"],
            *(group for group in (values["control_group"], values["observer_group"])
              if (values.get("control_group_created") and group == values["control_group"])
              or (values.get("observer_group_created") and group == values["observer_group"])),
        ]))
        record = {
            "wheel": values["wheel"], "cli_extended_wheel": values["cli_extended_wheel"],
            "wheel_source": values["wheel_source"], "control_group": values["control_group"],
            "observer_group": values["observer_group"], "control_users": values["control_users"],
            "observer_users": values["observer_users"], "gates_slice": values["gates_slice"],
            "observe_slices": values["observe_slices"], "allow_uids": values["allow_uids"],
            "drain_timeout": values["drain_timeout"], "enable_now": values["enable_now"],
            "created_groups": created_groups,
        }
        _atomic_write(self.paths.install_record, _render_record(record), 0o644)
        removed = self._prune_versions(values["version"])
        return {
            "ok": True, "dry_run": False, "version": values["version"],
            "config_drift": config_drift, "resources_changed": sorted(
                name for name, changed in resource_drift.items() if changed),
            "pruned": removed, "enabled": values["enable_now"], "downgrade": downgrade,
            "forced_sessions": values.get("forced_sessions", []),
        }

    def uninstall(self, args: argparse.Namespace, runtime: CliRuntime) -> Dict[str, Any]:
        record = _read_record(self.paths.install_record)
        created_groups = _record_created_groups(record)
        units = ("cgpd.service", "cgprofile-control.socket", "cgprofile-observer.socket")
        resources = self._resource_paths()
        config_values: Dict[str, Any] = {}
        config_path = args.config or self.paths.config
        try:
            config_values, _digest = daemon_config.read_config_file(config_path, explicit=bool(args.config))
        except daemon_config.ConfigError as exc:
            raise InstallError(
                f"cannot inspect {config_path} before uninstall: {exc}; refusing to guess socket paths"
            ) from None
        sockets = daemon_config.socket_settings(config_values)
        socket_paths = [sockets[level]["path"] for level in daemon_config.SOCKET_LEVELS]
        if runtime.dry_run:
            return {
                "ok": True, "dry_run": True, "units": list(units),
                "remove_groups": bool(args.remove_groups), "purge_data": bool(args.purge_data),
            }
        if self.geteuid() != 0:
            raise InstallError("cgpd uninstall needs root")
        installed_units = [unit for unit in units if os.path.lexists(resources[unit])]
        if installed_units:
            self._command([self.systemctl, "disable", "--now", *installed_units], timeout=60)
        for unit in units + ("cgprofile.slice",):
            try:
                os.unlink(os.path.join(self.paths.unit_dir, unit))
            except FileNotFoundError:
                pass
        try:
            os.unlink(resources["cgprofile.sysusers"])
        except FileNotFoundError:
            pass
        for dropin_root in (self.paths.at("/etc/systemd/system/cgpd.service.d"),):
            if os.path.isdir(dropin_root):
                for name in os.listdir(dropin_root):
                    if name.lower().startswith("mdt") and name.endswith(".conf"):
                        try:
                            os.unlink(os.path.join(dropin_root, name))
                        except OSError as exc:
                            raise InstallError(f"cannot remove old host-setup drop-in {name}: {exc}") from None
                try:
                    os.rmdir(dropin_root)
                except OSError:
                    pass
        for path in (config_path, self.paths.install_record):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(os.path.dirname(config_path))
        except OSError:
            pass
        expected_link = "/opt/cgprofile/current/venv/bin/cgpctl"
        try:
            if os.path.islink(self.paths.client_link) and os.readlink(self.paths.client_link) == expected_link:
                os.unlink(self.paths.client_link)
        except OSError:
            pass
        for socket_path in socket_paths:
            path = self.paths.at(socket_path) if self.paths.root != "/" else socket_path
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISSOCK(info.st_mode):
                os.unlink(path)
            else:
                raise InstallError(f"refusing to remove non-socket node at {path} during uninstall")
        for path in ("/run/cgprofile/control", "/run/cgprofile/observer"):
            target = self.paths.at(path)
            try:
                os.rmdir(target)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if os.path.islink(target):
                    os.unlink(target)
                else:
                    raise InstallError(f"cannot remove runtime directory {target}: {exc}") from None
        state = self.paths.at("/run/cgprofile/state")
        if os.path.isdir(state) and not os.path.islink(state):
            shutil.rmtree(state)
        elif os.path.islink(state):
            os.unlink(state)
        try:
            os.rmdir(self.paths.runtime_root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if os.path.islink(self.paths.runtime_root):
                os.unlink(self.paths.runtime_root)
            else:
                raise InstallError(f"cannot remove runtime directory {self.paths.runtime_root}: {exc}") from None
        try:
            shutil.rmtree(self.paths.opt)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise InstallError(f"cannot remove {self.paths.opt}: {exc}") from None
        if args.remove_groups:
            for group in created_groups:
                self._command(["groupdel", group], timeout=30)
        if args.purge_data:
            try:
                shutil.rmtree(self.paths.state_data)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot purge {self.paths.state_data}: {exc}") from None
        self._command([self.systemctl, "daemon-reload"], timeout=30)
        self._command([self.systemctl, "reset-failed", *units, "cgprofile.slice"],
                      check=False, timeout=30)
        return {
            "ok": True, "dry_run": False, "units_removed": list(units) + ["cgprofile.slice"],
            "groups_removed": created_groups if args.remove_groups else [],
            "data_purged": bool(args.purge_data),
        }


def _install_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    try:
        body = Installer().install(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _install_summary(body))
    return 0


def _uninstall_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    try:
        body = Installer().uninstall(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _uninstall_summary(body))
    return 0


def _install_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        return f"dry run: cgprofile {body['version']} would be installed; no changes made"
    resources = ", ".join(body.get("resources_changed", [])) or "no resource drift"
    return f"cgprofile {body['version']} installed; {resources}; enabled={body['enabled']}"


def _uninstall_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        return "dry run: cgprofile service, units and install files would be removed"
    return "cgprofile service, units and install files removed"


def register_install(registry: CliRegistry) -> None:
    """Register the standalone install and uninstall verbs."""
    options = (
        OptionSpec(("--wheel",), "cgprofile pin: <version>@sha256:<64 lowercase hex>",
                   group="WHEELS", metavar="PIN"),
        OptionSpec(("--cli-extended-wheel",), "cli-extended pin in the same form", group="WHEELS", metavar="PIN"),
        OptionSpec(("--wheel-source",), "HTTPS release base or a directory containing both wheels",
                   group="WHEELS", metavar="SOURCE"),
        OptionSpec(("--control-group",), "create cgprofile-control or map control access to an existing group",
                   group="ACCESS", metavar="GROUP"),
        OptionSpec(("--observer-group",), "create cgprofile-observer or map observer access to an existing group",
                   group="ACCESS", metavar="GROUP"),
        OptionSpec(("--control-users",), "comma-separated existing users to add to the control group",
                   group="ACCESS", metavar="USERS"),
        OptionSpec(("--observer-users",), "comma-separated existing users to add to the observer group",
                   group="ACCESS", metavar="USERS"),
        OptionSpec(("--gates-slice",), "configured admission-capacity slice", group="CONFIG", metavar="SLICE"),
        OptionSpec(("--observe-slices",), "comma-separated observed slice names", group="CONFIG", metavar="SLICES"),
        OptionSpec(("--allow-uids",), "comma-separated control-socket UID allowlist", group="CONFIG", metavar="UIDS"),
        OptionSpec(("--drain-timeout",), "wait for live sessions before restart (default 10m)",
                   group="RESTART", metavar="DURATION"),
        OptionSpec(("--force-restart",), "restart even when live sessions cannot drain", group="RESTART",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--allow-downgrade",), "allow rollback to an older cgprofile release",
                   group="RESTART", parser_kwargs={"action": "store_true"}),
        OptionSpec(("--enable",), "enable and start the installed service", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
        OptionSpec(("--no-enable",), "install resources without enabling or starting cgpd", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
    )
    registry.register(VerbSpec(
        "install", description="install or upgrade cgpd, its systemd resources and access groups",
        group=VerbGroup.MODIFICATION.value, mutating=True, dry_run=True,
        include_progress=False, options=options, handler=_install_handler,
        examples=("cgpd install --wheel VERSION@sha256:... --cli-extended-wheel VERSION@sha256:... --enable",
                  "cgpd install --wheel-source ./wheels --control-group docker --observer-group cgprofile-observer --no-enable"),
    ))
    registry.register(VerbSpec(
        "uninstall", description="stop and remove cgpd service resources",
        group=VerbGroup.MODIFICATION.value, mutating=True, dry_run=True,
        include_progress=False,
        options=(
            OptionSpec(("--remove-groups",), "remove groups created by cgpd install",
                       group="REMOVE", parser_kwargs={"action": "store_true"}),
            OptionSpec(("--purge-data",), "also remove /var/lib/cgprofile sessions and overrides",
                       group="REMOVE", parser_kwargs={"action": "store_true"}),
        ),
        handler=_uninstall_handler,
        examples=("cgpd uninstall", "cgpd uninstall --remove-groups --purge-data --yes"),
    ))



