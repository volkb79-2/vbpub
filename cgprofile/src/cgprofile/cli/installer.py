"""Standalone ``cgprofiled install`` and ``cgprofiled uninstall`` service lifecycle.

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


class DrainTimeout(InstallError):
    """Live sessions remained at the drain deadline; PENDING marks the wanted version."""


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
        return self.at("/usr/local/bin/cgprofilectl")

    @property
    def state_data(self) -> str:
        return self.at("/var/lib/cgprofile")

    @property
    def runtime_root(self) -> str:
        return self.at("/run/cgprofile")

    @property
    def control_dir(self) -> str:
        return self.at("/run/cgprofile/control")

    @property
    def observer_dir(self) -> str:
        return self.at("/run/cgprofile/observer")

    @property
    def state_dir(self) -> str:
        return self.at("/run/cgprofile/state")

    @property
    def service_dropins(self) -> str:
        return self.at("/etc/systemd/system/cgprofiled.service.d")

    # The 2.x layout (cgpd/cgpctl). 3.0 never reads or migrates it; P-11 refuses while any of it exists.
    @property
    def v2_paths(self) -> Tuple[str, ...]:
        return (
            os.path.join(self.unit_dir, "cgpd.service"),
            os.path.join(self.unit_dir, "cgpd.service.d"),
            self.at("/etc/cgprofile/cgpd.toml"),
            self.at("/usr/local/bin/cgpctl"),
        )

    @property
    def pending(self) -> str:
        return os.path.join(self.opt, "PENDING")

    @property
    def current(self) -> str:
        return os.path.join(self.opt, "current")

    @property
    def current_new(self) -> str:
        return os.path.join(self.opt, "current.new")

    @property
    def client_target(self) -> str:
        return self.at("/opt/cgprofile/current/venv/bin/cgprofilectl")

    @property
    def user_link(self) -> str:
        return self.at("/usr/local/bin/cgprofile")

    @property
    def user_target(self) -> str:
        return self.at("/opt/cgprofile/current/venv/bin/cgprofile")

    @property
    def client_links(self) -> Tuple[Tuple[str, str], ...]:
        """(link, target) of both clients: the socket client and the end-user client."""
        return ((self.client_link, self.client_target), (self.user_link, self.user_target))


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


# Findings under /opt/cgprofile come from an unfinished cgprofiled install (or from something
# unknown there); the old host-setup layout is reported through the MANIFEST finding.
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
# The first three are what an interrupted cgprofiled install itself leaves behind.
_OWN_INTERRUPTED_FINDINGS = _VERSION_ROOT_FINDINGS[:3]


def manual_removal_message(details: Iterable[str] = (), verb: str = "install") -> str:
    listed = list(details)
    version_root = [item for item in listed if item.startswith(_VERSION_ROOT_FINDINGS)]
    old_host = [item for item in listed if item not in version_root]
    lines = [f"cgprofiled {verb} refused: this host has files cgprofiled install did not write or did not finish."]
    if listed:
        lines.append("Found:")
        lines.extend(f"  {item}" for item in listed)
    if old_host or not listed:
        lines.append("Manual steps: remove the old installation by hand, then rerun:")
        lines.extend(f"  - {step}" for step in (
            "review and remove each listed foreign tmpfiles rule, sysusers file, or cgprofiled service drop-in",
            "remove /etc/sysusers.d/mdt-cgprofile.conf (keep the cgprofile-observer group and its GID)",
            "remove /usr/local/sbin/mdt-cgprofile-install.sh and the old /opt/cgprofile installation",
            "stop and disable the 2.x cgpd.service first, remove /etc/systemd/system/cgpd.service, "
            "/etc/systemd/system/cgpd.service.d/ and the old cgprofile.slice and socket units, "
            "run systemctl daemon-reload, then remove /run/cgprofile",
            "remove the 2.x /usr/local/bin/cgpctl and /etc/cgprofile/cgpd.toml (3.0 uses cgprofilectl, "
            "cgprofile and /etc/cgprofile/cgprofiled.toml; nothing is migrated)",
            "keep /var/lib/cgprofile unless data purge is intended",
        ))
    if version_root:
        lines.extend((
            "Under /opt/cgprofile these are leftovers of a cgprofiled install that did not finish.",
            "  Review and remove the listed paths and rerun, or run `cgprofiled uninstall --yes` to remove",
            "  the whole install (it removes only what cgprofiled install writes).",
        ))
    return "\n".join(lines)


def _read_old_config_keys(config_path: str) -> List[str]:
    try:
        with open(config_path, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"cannot inspect existing config {config_path}: {exc}; refusing to replace it") from None
    return sorted(set(raw) & {"ctl_socket", "observer_socket"})


def find_foreign_rules(paths: InstallPaths) -> List[str]:
    """Find old mdt tmpfiles, sysusers, or service drop-ins that claim cgprofiled."""
    found: List[str] = []
    for directory in (paths.at("/etc/tmpfiles.d"),
                      paths.at("/etc/systemd/system/cgprofiled.service.d")):
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
            elif directory.endswith("cgprofiled.service.d") \
                    and ("mdt" in name.lower() or "/run/cgprofile" in text or "Group=" in text):
                found.append(path)
    old_sysusers = paths.at("/etc/sysusers.d/mdt-cgprofile.conf")
    if os.path.lexists(old_sysusers):
        found.append(old_sysusers)
    return found


# Owner (uid, gid) the runtime tree must have; fake-root tests set their own ids.
RUNTIME_TREE_OWNER = (0, 0)


def _run_tree_problems(paths: InstallPaths) -> List[str]:
    owner = RUNTIME_TREE_OWNER
    expected = {
        paths.runtime_root: 0o755,
        paths.control_dir: 0o755,
        paths.observer_dir: 0o755,
        paths.state_dir: 0o700,
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
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != owner or actual_mode != mode:
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
    problems.extend(f"2.x layout: {path}" for path in paths.v2_paths if os.path.lexists(path))
    problems.extend(f"foreign rule or drop-in: {path}" for path in find_foreign_rules(paths))
    problems.extend(_run_tree_problems(paths))
    # Units next to PENDING come from an unfinished cgprofiled install (PENDING itself is
    # reported below); without a record or PENDING they were not written by cgprofiled.
    if not os.path.lexists(paths.install_record) and not os.path.lexists(paths.pending):
        for name in ("cgprofiled.service", "cgprofile.slice", "cgprofile-control.socket",
                     "cgprofile-observer.socket"):
            unit = os.path.join(paths.unit_dir, name)
            if os.path.lexists(unit):
                problems.append(f"hand-installed or old unit without install record: {unit}")
    for marker in (paths.pending, paths.current_new):
        if os.path.lexists(marker):
            problems.append(f"leftover install marker: {marker}")
    if os.path.lexists(paths.opt) and (os.path.islink(paths.opt) or not os.path.isdir(paths.opt)):
        problems.append(f"version root is not a real directory: {paths.opt}")
    elif os.path.isdir(paths.opt):
        try:
            entries = sorted(os.listdir(paths.opt))
        except OSError as exc:
            problems.append(f"cannot inspect version root {paths.opt}: {exc.strerror or exc}")
            entries = []
        for name in entries:
            if name in ("current", "current.new", "PENDING"):  # markers are reported above
                continue
            version_dir = os.path.join(paths.opt, name)
            if name.startswith((".stage.", ".cgprofiled-render.")):
                problems.append(f"leftover version staging directory: {version_dir}")
            elif os.path.islink(version_dir):
                problems.append(f"version tree is a symlink: {version_dir}")
            elif os.path.isdir(version_dir):
                problems.extend(_version_tree_problems(version_dir))
    for path in ("/run/cgprofile/ctl.sock", "/run/cgprofile/observer/resource-observer.sock"):
        old_path = paths.at(path)
        if os.path.lexists(old_path):
            problems.append(f"old-layout socket path remains: {old_path}")
    return problems


def _version_tree_problems(version_dir: str) -> List[str]:
    manifest = os.path.join(version_dir, "MANIFEST")
    if not os.path.lexists(manifest):
        return [f"unmarked version tree: {version_dir}"]
    try:
        lines = Path(manifest).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot inspect version MANIFEST {manifest}: {exc}"]
    # Any mdt installer spelling counts as the old host-setup install.
    if any(line.strip().startswith("installer=mdt") for line in lines):
        return [f"mdt host-setup MANIFEST: {manifest}"]
    version = os.path.basename(version_dir)
    if f"installer=cgprofiled install {version}" not in lines:
        return [f"unknown version tree MANIFEST: {manifest}"]
    return []


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


# The only groups the installer can create (systemd-sysusers); no other group is ever removed.
CREATABLE_GROUPS = (daemon_config.DEFAULT_CONTROL_GROUP, daemon_config.DEFAULT_OBSERVER_GROUP)


def _record_created_groups(record: Mapping[str, Any]) -> List[str]:
    raw = record.get("created_groups", [])
    if not isinstance(raw, list) or any(name not in CREATABLE_GROUPS for name in raw):
        raise InstallError("the existing install record has an invalid created_groups list; repair it manually")
    return list(dict.fromkeys(raw))


def _pending_created_groups(paths: InstallPaths) -> List[str]:
    """Groups an unfinished install recorded in PENDING (``created_groups=a,b``)."""
    try:
        lines = Path(paths.pending).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    found: List[str] = []
    for line in lines:
        if line.startswith("created_groups="):
            found.extend(name for name in _parse_words(line.partition("=")[2]) if name in CREATABLE_GROUPS)
    return list(dict.fromkeys(found))


def _write_pending(paths: InstallPaths, version: str, new_groups: Iterable[str] = ()) -> None:
    """Write PENDING, keeping the groups it already names (the install record comes last)."""
    groups = list(dict.fromkeys([*_pending_created_groups(paths), *new_groups]))
    text = version + "\n" + (f"created_groups={','.join(groups)}\n" if groups else "")
    os.makedirs(paths.opt, mode=0o755, exist_ok=True)
    _atomic_write(paths.pending, text)


def _render_record(values: Mapping[str, Any]) -> str:
    return "".join(f"{key} = {_toml_value(values[key])}\n" for key in sorted(values))


def _group_gid(name: str) -> Optional[int]:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _directory_names(directory: str) -> List[str]:
    """Sorted entry names of ``directory``; an absent directory has none."""
    try:
        return sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        raise InstallError(f"cannot inspect {directory}: {exc.strerror or exc}") from None


def _mdt_dropins(paths: InstallPaths) -> List[str]:
    """Leftover mdt host-setup drop-ins for the service; uninstall removes exactly these."""
    return [os.path.join(paths.service_dropins, name) for name in _directory_names(paths.service_dropins)
            if "mdt" in name.lower() and name.endswith(".conf")]


def _is_install_link(link: str, paths: InstallPaths) -> bool:
    """True for a /usr/local/bin symlink that points into /opt/cgprofile."""
    return os.path.islink(link) and os.readlink(link).startswith(paths.opt + os.sep)


def _atomic_symlink(target: str, path: str, temporary: str) -> None:
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


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


def _validate_users(users: Sequence[str], forgotten: Optional[List[str]] = None) -> List[str]:
    result: List[str] = []
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


def _parse_add_users(values: Optional[Sequence[str]]) -> Dict[str, List[str]]:
    """Parse repeatable ``LEVEL=USER`` options and validate each account now."""
    result = {level: [] for level in daemon_config.SOCKET_LEVELS}
    for value in values or ():
        if not isinstance(value, str) or "=" not in value:
            raise InstallError("--add-user must use LEVEL=USER, with LEVEL control or observer")
        level, user = value.split("=", 1)
        if level not in result:
            raise InstallError(f"unknown --add-user access level {level!r}; use control or observer")
        if not user:
            raise InstallError("--add-user requires a non-empty user after LEVEL=")
        validated = _validate_users([user])[0]
        if validated not in result[level]:
            result[level].append(validated)
    return result


def _record_words(value: Any, label: str) -> List[str]:
    """A list-valued install-record field (TOML array); a comma string is tolerated."""
    if value is None:
        return []
    if isinstance(value, str):
        return _parse_words(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item.strip() for item in value if item.strip()]
    raise InstallError(f"the saved {label} value is invalid; repair the install record manually")


def _record_uids(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, list) and all(type(item) is int and item >= 0 for item in value):
        return list(dict.fromkeys(value))
    raise InstallError("the saved allow_uids value is invalid; repair the install record manually")


def _resolve_users(runtime: CliRuntime, record: Mapping[str, Any], key: str, level: str,
                   requested: Sequence[str], forgotten: List[str]) -> List[str]:
    """Accumulate saved and requested users; forget deleted accounts, but validate new names strictly."""
    saved = _record_words(record[key], key) if key in record else []
    prompted: List[str] = []
    if not requested and key not in record:
        prompt = _prompt_missing(runtime, None, f"--add-user {level}=USER",
                                 f"Users to add to the {level} group (comma-separated)", required=False)
        prompted = _parse_words(prompt)
    saved_users = _validate_users(saved, forgotten)
    new_users = _validate_users([*requested, *prompted])
    return list(dict.fromkeys([*saved_users, *new_users]))


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
        return _validate_install_group(level, explicit)
    if saved is not None:
        return _validate_install_group(level, saved)
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
            return _validate_install_group(level, default)
        mapped = runtime.prompts.text(f"Existing group for {level}", required=True)
        return _validate_install_group(level, mapped)
    except PromptCancelled:
        raise InstallError("installation prompts cancelled", exit_code=130) from None


def _config_groups(paths: InstallPaths) -> Dict[str, str]:
    """Read existing socket groups; an invalid file cannot be hidden by flags."""
    try:
        text = Path(paths.config).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallError(f"cannot inspect existing config {paths.config}: {exc}") from None
    try:
        parsed = daemon_config.parse_table(text)
    except daemon_config.ConfigError as exc:
        raise InstallError(
            f"existing config {paths.config} is invalid: {exc}; installer flags cannot override it; "
            "fix or remove it, then rerun") from None
    return {level: parsed[f"sockets.{level}"]["group"] for level in daemon_config.SOCKET_LEVELS
            if f"sockets.{level}" in parsed}


def _validate_install_group(level: str, name: Any) -> str:
    try:
        validated = daemon_config.validate(
            f"sockets.{level}",
            {"path": daemon_config.KEYS[f"sockets.{level}"].default["path"],
             "group": name, "mode": "0660"},
        )
        daemon_config._validate_socket_group(level, validated["group"], _group_gid)
    except daemon_config.ConfigError as exc:
        raise InstallError(str(exc)) from None
    return validated["group"]


def _gather_inputs(args: argparse.Namespace, runtime: CliRuntime, paths: InstallPaths) -> Dict[str, Any]:
    record = _read_record(paths.install_record)
    prior_created_groups = list(dict.fromkeys([*_record_created_groups(record), *_pending_created_groups(paths)]))
    saved_control = record.get("control_group", daemon_config.DEFAULT_CONTROL_GROUP)
    saved_observer = record.get("observer_group", daemon_config.DEFAULT_OBSERVER_GROUP)
    if "control_group" in record or "observer_group" in record:
        _validate_group_mapping(saved_control, saved_observer, require_mapped=False)
    wheel = args.wheel or record.get("wheel")
    wheel = _prompt_missing(runtime, wheel, "--wheel", "cgprofile release pin")
    clix = args.cli_extended_wheel or record.get("cli_extended_wheel")
    clix = _prompt_missing(runtime, clix, "--cli-extended-wheel", "cli-extended release pin")
    # A group passed as a flag is applied; with no flag the config file's value wins
    # over the install record (an operator's hand edit is never reverted).
    file_groups = _config_groups(paths)
    if file_groups:
        _validate_group_mapping(
            file_groups.get("control", daemon_config.DEFAULT_CONTROL_GROUP),
            file_groups.get("observer", daemon_config.DEFAULT_OBSERVER_GROUP),
        )
    control_group = _prompt_group(
        runtime, "control", daemon_config.DEFAULT_CONTROL_GROUP,
        file_groups.get("control", record.get("control_group")), args.control_group,
    )
    observer_group = _prompt_group(
        runtime, "observer", daemon_config.DEFAULT_OBSERVER_GROUP,
        file_groups.get("observer", record.get("observer_group")), args.observer_group,
    )
    requested_users = _parse_add_users(getattr(args, "add_user", None))
    users_forgotten: List[str] = []
    control_users = _resolve_users(
        runtime, record, "control_users", "control", requested_users["control"], users_forgotten)
    observer_users = _resolve_users(
        runtime, record, "observer_users", "observer", requested_users["observer"], users_forgotten)
    enable_now: Optional[bool] = None
    if args.enable and args.no_enable:
        raise InstallError("choose only one of --enable and --no-enable")
    if args.enable:
        enable_now = True
    elif args.no_enable:
        enable_now = False
    elif "enable_now" in record:
        enable_now = bool(record["enable_now"])
    elif runtime.dry_run:
        enable_now = False  # a dry run never prompts and changes nothing
    elif runtime.json_mode or not runtime.output.is_interactive:
        raise InstallError("missing required --enable or --no-enable on a non-interactive run")
    else:
        try:
            enable_now = runtime.prompts.confirm("Enable and start cgprofiled now?", default=True)
        except PromptCancelled:
            raise InstallError("installation prompts cancelled", exit_code=130) from None
    values = {
        "wheel": str(wheel),
        "cli_extended_wheel": str(clix),
        "wheel_source": args.wheel_source or record.get("wheel_source") or DEFAULT_RELEASE_BASE,
        "control_group": control_group,
        "observer_group": observer_group,
        "control_users": control_users,
        "observer_users": observer_users,
        "users_forgotten": users_forgotten,
        "users_to_add": {
            level: {"group": group, "users": users}
            for level, group, users in (
                ("control", control_group, control_users), ("observer", observer_group, observer_users))
            if users
        },
        "gates_slice": args.gates_slice or record.get("gates_slice") or "dev-gates.slice",
        "observe_slices": _parse_words(args.observe_slices) if args.observe_slices is not None
        else _record_words(record.get("observe_slices"), "observe_slices"),
        "allow_uids": _parse_uid_list(args.allow_uids) if args.allow_uids is not None
        else _record_uids(record.get("allow_uids")),
        # flags given on this run; an existing config file keeps every other value
        "explicit": sorted(key for key, flag in (
            ("gates_slice", args.gates_slice), ("observe_slices", args.observe_slices),
            ("allow_uids", args.allow_uids)) if flag is not None),
        "drain_timeout": args.drain_timeout or record.get("drain_timeout") or "10m",
        "enable_now": bool(enable_now),
        "force_restart": bool(args.force_restart),
        "allow_downgrade": bool(args.allow_downgrade),
        "prior_created_groups": prior_created_groups,
    }
    values["drain_seconds"] = parse_duration(values["drain_timeout"])
    values["version"], values["wheel_sha"] = parse_pin(values["wheel"], "--wheel")
    values["cli_extended_version"], values["cli_extended_sha"] = parse_pin(
        values["cli_extended_wheel"], "--cli-extended-wheel")
    version_key(values["version"])
    if version_key(values["version"])[0] < 2:
        raise InstallError(f"cgprofile {values['version']} does not provide the native host service")
    if not has_socket_units(values["version"]) and not values["allow_downgrade"]:
        raise InstallError(
            f"cgprofiled install supports cgprofile {SOCKET_RELEASE} and later; {values['version']} has no "
            "socket units. To roll an installed host back, pass --allow-downgrade"
        )
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


def _validate_group_mapping(control: Any, observer: Any, *, require_mapped: bool = True) -> None:
    control = _validate_install_group("control", control)
    observer = _validate_install_group("observer", observer)
    try:
        daemon_config.validate_socket_groups(
            {"control": {"group": control}, "observer": {"group": observer}},
            gid_lookup=_group_gid,
        )
    except daemon_config.ConfigError as exc:
        raise InstallError(str(exc)) from None
    if not require_mapped:
        return
    control_gid = _group_gid(control)
    observer_gid = _group_gid(observer)
    if control != daemon_config.DEFAULT_CONTROL_GROUP and control_gid is None:
        raise InstallError(f"mapped control group {control!r} does not exist")
    if observer != daemon_config.DEFAULT_OBSERVER_GROUP and observer_gid is None:
        raise InstallError(f"mapped observer group {observer!r} does not exist")


class Installer:
    """Injectable implementation of the cgprofiled host installer."""

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
        running_prefix: str = sys.prefix,
    ) -> None:
        self.paths = paths
        self.running_prefix = running_prefix
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

    def _check_config_arg(self, args: argparse.Namespace) -> None:
        """The installer owns /etc/cgprofile/cgprofiled.toml; it never writes another path."""
        explicit = getattr(args, "config", None)
        if explicit and os.path.normpath(explicit) != os.path.normpath(self.paths.config):
            raise InstallError(
                f"cgprofiled install and uninstall manage {self.paths.config} only; --config {explicit} is not supported"
            )

    def _current_version(self) -> Optional[str]:
        """The version the ``current`` link selects, or None when there is no link."""
        current = self.paths.current
        if not os.path.lexists(current):
            return None
        if not os.path.islink(current):
            raise InstallError(f"{current} exists but is not a symlink; refusing to replace it")
        target = os.readlink(current)
        if not target or os.path.basename(target) != target:
            raise InstallError(f"{current} must point to a version directory inside /opt/cgprofile")
        version_key(target)
        version_dir = os.path.join(self.paths.opt, target)
        if not os.path.isdir(version_dir) or not os.path.isfile(os.path.join(version_dir, "MANIFEST")):
            raise InstallError(f"{current} points to an incomplete version tree {version_dir}")
        return target

    def _refuse_old_layout(self, config_path: str) -> None:
        """P-11: refuse, with the manual-removal list, while any old-layout file exists."""
        problems = old_installation_problems(self.paths, config_path)
        if os.path.lexists(self.paths.install_record):
            # A failed upgrade of a finished install: every apply step is idempotent,
            # so a rerun re-applies instead of needing an uninstall.
            reapply = {f"leftover install marker: {marker}"
                       for marker in (self.paths.pending, self.paths.current_new)}
            problems = [item for item in problems if item not in reapply]
        if problems:
            raise InstallError(manual_removal_message(problems))

    def _preflight(self, values: Mapping[str, Any], config_path: str, *, dry_run: bool) -> Tuple[bool, bool]:
        if not dry_run and self.geteuid() != 0:
            raise InstallError("cgprofiled install needs root")
        self._refuse_old_layout(config_path)
        version_dir = os.path.join(self.paths.opt, values["version"])
        manifest_path = os.path.join(version_dir, "MANIFEST")
        if os.path.isfile(manifest_path):
            try:
                manifest = Path(manifest_path).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                raise InstallError(f"cannot read immutable version MANIFEST {manifest_path}") from None
            if (f"cgprofile_sha256={values['wheel_sha']}" not in manifest
                    or f"cli_extended_sha256={values['cli_extended_sha']}" not in manifest):
                raise InstallError(
                    f"{version_dir} is immutable and its wheel digest differs from the pin; release a new version"
                )
        # A /usr/local/bin entry that cgprofiled install did not write is never replaced.
        for link, _target in self.paths.client_links:
            if os.path.lexists(link) and not _is_install_link(link, self.paths):
                raise InstallError(
                    f"{link} exists and was not written by cgprofiled install; move it away, then rerun"
                )
        current = self._current_version()
        if current is None and not has_socket_units(values["version"]):
            raise InstallError(
                f"cgprofile {values['version']} has no socket units and no installation is present to roll back"
            )
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
        ctl = os.path.join(self.paths.opt, current, "venv", "bin", "cgprofilectl")
        result = self._command([ctl, "sessions", "--state", "live", "--json"], check=False, timeout=30)
        if result.returncode != 0:
            raise InstallError("cannot list live cgprofile sessions; refusing to restart")
        try:
            doc = __import__("json").loads(result.stdout)
        except ValueError:
            raise InstallError("cgprofilectl sessions returned invalid JSON; refusing to restart") from None
        rows = doc.get("sessions") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise InstallError("cgprofilectl sessions returned no session list; refusing to restart")
        return [row for row in rows if isinstance(row, dict) and row.get("state", "live") == "live"]

    def _drain_or_refuse(self, current: Optional[str], values: Mapping[str, Any],
                         unavailable: str = "") -> None:
        if values["force_restart"]:
            # --force-restart exists for a daemon that cannot answer: when the session
            # list is unavailable it proceeds, and says so.
            try:
                if current is None:
                    raise InstallError(unavailable)
                sessions = self._live_sessions(current)
            except InstallError as exc:
                values["forced_sessions"] = ["unknown (the session list was unavailable)"]
                values["warnings"] = [f"--force-restart proceeds without knowing the live sessions: {exc}"]
                return
            values["forced_sessions"] = [str(row.get("session", "?")) for row in sessions]
            return
        deadline = self.monotonic() + values["drain_seconds"]
        while True:
            sessions = self._live_sessions(current)
            if not sessions:
                return
            if self.monotonic() >= deadline:
                _write_pending(self.paths, values["version"])
                labels = ", ".join(str(row.get("session", "?")) for row in sessions)
                raise DrainTimeout(
                    f"live sessions remain at the {values['drain_timeout']} drain deadline ({labels}); "
                    f"version {values['version']} is pending; rerun cgprofiled install when the sessions have "
                    "ended, or pass --force-restart",
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
                    # the old-installation scan refuses this earlier; never build over it
                    raise InstallError(f"unmarked version directory {version_dir} exists; remove it manually")
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
                    try:
                        self._command([self.python, "-m", "venv", venv], timeout=120)
                    except InstallError as exc:
                        # C5: a minimal Debian/Ubuntu image has `venv` but not `ensurepip` until
                        # python3-venv is installed; say so instead of a bare exit status.
                        raise InstallError(
                            f"{exc}; the host needs the python3-venv package "
                            "(apt install python3-venv), then rerun") from None
                    # exactly the two sha256-pinned wheels: a new dependency fails closed
                    self._command([
                        os.path.join(venv, "bin", "python"), "-m", "pip", "install", "--quiet",
                        "--no-index", "--no-deps", os.path.join(wheels, wheel), os.path.join(wheels, clix),
                    ], timeout=600)
                    got = self._command([os.path.join(venv, "bin", "cgprofiled"), "--version"], timeout=30)
                    if values["version"] not in (got.stdout + got.stderr):
                        raise InstallError(
                            f"installed cgprofiled reports {(got.stdout + got.stderr).strip()!r}, expected {values['version']}"
                        )
                    manifest_text = "\n".join((
                        f"cgprofile_wheel={wheel}",
                        f"cgprofile_sha256={values['wheel_sha']}",
                        f"cli_extended_wheel={clix}",
                        f"cli_extended_sha256={values['cli_extended_sha']}",
                        f"version={values['version']}",
                        f"installed_at={datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
                        f"installer=cgprofiled install {values['version']}",
                        "",
                    ))
                    _atomic_write(manifest, manifest_text, 0o644)
                except BaseException:
                    shutil.rmtree(version_dir, ignore_errors=True)
                    raise
            if not os.path.isfile(os.path.join(venv, "bin", "cgprofiled")):
                raise InstallError(f"{venv}/bin/cgprofiled is missing")
            return venv
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _target_config(self, config_path: str, values: Mapping[str, Any]) -> Tuple[str, List[str]]:
        """The config text to install and the keys changed in an existing file.

        A missing file is rendered from the installer inputs. An existing file is the
        operator's: it is kept as written, and only the socket groups (the install
        inputs) and the keys passed as flags on this run are merged in, which the
        returned list names. The socket paths are fixed by the layout, so a kept
        file may change the group and mode only.
        """
        try:
            existing: Optional[str] = Path(config_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        except (OSError, UnicodeDecodeError) as exc:
            raise InstallError(f"cannot read {config_path}: {exc}") from None
        if existing is None:
            text = _render_config(values["config_values"], values["version"])
            self._validate_rendered(text, values["version"])
            return text, []
        try:
            merged = dict(daemon_config.parse_table(existing))
        except daemon_config.ConfigError as exc:
            raise InstallError(f"{config_path} is invalid: {exc}; fix or remove it, then rerun") from None
        changes: List[str] = []
        requested = values["config_values"]
        defaults = {"sockets.control": daemon_config.DEFAULT_CONTROL_SOCKET,
                    "sockets.observer": daemon_config.DEFAULT_OBSERVER_SOCKET}
        for key, fixed in defaults.items():
            current = daemon_config.socket_settings(merged)[key.split(".")[1]]
            if current["path"] != fixed:
                raise InstallError(
                    f"{config_path} sets {key}.path = {current['path']!r}; cgprofiled uses the fixed path "
                    f"{fixed!r}, so remove the key or restore that value"
                )
            wanted = requested[key]["group"]
            if current["group"] != wanted:
                merged[key] = {**current, "group": wanted}
                changes.append(f"{key}.group")
        for key in values["explicit"]:
            if merged.get(key, daemon_config.KEYS[key].default) != requested[key]:
                merged[key] = requested[key]
                changes.append(key)
        if not has_socket_units(values["version"]):
            # a rollback to a release without socket tables cannot keep them
            if any(key.startswith("sockets.") for key in merged):
                changes.append("sockets.* (removed for the older release)")
            text = _render_config(merged, values["version"])
        elif changes:
            text = daemon_config.render_config(merged)
        else:
            text = existing
        self._validate_rendered(text, values["version"])
        return text, changes

    @staticmethod
    def _validate_rendered(text: str, version: str) -> None:
        if has_socket_units(version):
            try:
                daemon_config.parse_table(text)
            except daemon_config.ConfigError as exc:
                raise InstallError(f"rendered config refused: {exc}") from None

    def _render_resources(self, venv: str, config_path: str, version: str) -> Dict[str, str]:
        cgprofiled = os.path.join(venv, "bin", "cgprofiled")
        result: Dict[str, str] = {}
        names = ["cgprofile.slice", "cgprofiled.service"]
        if has_socket_units(version):
            names.extend(("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers"))
        for name in names:
            proc = self._command([
                cgprofiled, "--config", config_path, "unit", "print",
                "--install-root", unitfiles.DEFAULT_INSTALL_ROOT, "--unit", name,
            ], check=False, timeout=30)
            if proc.returncode != 0:
                raise InstallError(f"cgprofiled unit print refused {name}: {(proc.stderr or proc.stdout).strip()}")
            result[name] = proc.stdout
        return result

    def _resource_paths(self) -> Dict[str, str]:
        return {
            "cgprofile.slice": os.path.join(self.paths.unit_dir, "cgprofile.slice"),
            "cgprofiled.service": os.path.join(self.paths.unit_dir, "cgprofiled.service"),
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
        cgprofilectl = os.path.join(venv, "bin", "cgprofilectl")
        deadline = self.monotonic() + timeout
        while True:
            result = self._command([cgprofilectl, "status", "--json"],
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
                    "inspect journalctl -u cgprofiled"
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
        running = self._running_version_dir()
        if running is not None:  # never delete the tree this very process runs from
            keep.add(os.path.basename(running))
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
        self._check_config_arg(args)
        config_path = self.paths.config
        # C3: the old-layout refusal and its removal list come before any parsing of the existing
        # config, or a seeded old config dies in _config_groups with a bare "invalid" message.
        self._refuse_old_layout(config_path)
        values = _gather_inputs(args, runtime, self.paths)
        downgrade, version_drift = self._preflight(values, config_path, dry_run=runtime.dry_run)
        config_text, config_changes = self._target_config(config_path, values)
        if runtime.dry_run:
            return {
                "ok": True, "dry_run": True, "version": values["version"],
                "config": config_path, "config_changes": config_changes,
                "socket_mode": "activated" if has_socket_units(values["version"]) else "legacy rollback",
                "users_to_add": values["users_to_add"],
                "users_forgotten": values["users_forgotten"],
                "message": "no files, wheels, groups, units, or services changed",
            }
        if not runtime.yes and not runtime.confirm("Install or upgrade the cgprofile host service?"):
            raise InstallError("installation was not confirmed")

        # PENDING marks an interrupted install, so the next install refuses (or, with
        # an install record, re-applies) and uninstall knows the leftovers are its own.
        # Until the first host change a failed fetch, pin check or config check drops
        # it again; a drain timeout keeps it (it names the wanted version).
        earlier_pending = os.path.lexists(self.paths.pending)
        _write_pending(self.paths, values["version"])
        try:
            plan = self._plan(values, config_path, config_text, version_drift, earlier_pending)
            active = plan["active"]
            if active and plan["need_restart"]:
                missing = ("cgprofiled.service is active but /opt/cgprofile/current is missing; "
                           "cannot list live sessions, refusing to restart")
                current = self._current_version()  # preflight already validated the pointer
                if current is None and not values["force_restart"]:
                    raise InstallError(missing)
                self._drain_or_refuse(current, values, missing)
        except BaseException as exc:
            if not earlier_pending and not isinstance(exc, DrainTimeout):
                Path(self.paths.pending).unlink(missing_ok=True)
            raise
        venv, rendered = plan["venv"], plan["rendered"]
        resource_drift: Dict[str, bool] = plan["resource_drift"]
        config_drift: bool = plan["config_drift"]
        socket_restart: List[str] = plan["socket_restart"]
        service_restart: bool = plan["service_restart"]
        resource_change: bool = plan["resource_change"]
        resource_paths = self._resource_paths()

        # From here on a failure leaves PENDING in place.
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

        if os.path.lexists(self.paths.current_new):  # left by a failed upgrade being re-applied
            os.unlink(self.paths.current_new)
        os.symlink(values["version"], self.paths.current_new)
        os.replace(self.paths.current_new, self.paths.current)

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
            # Name the groups sysusers is about to create in PENDING first: if anything
            # later fails, `uninstall --remove-groups` still knows them (the install
            # record is only written at the very end).
            _write_pending(self.paths, values["version"], sorted(groups_before_sysusers))
            self._command(["systemd-sysusers", sysusers_path], timeout=30)
            for group in groups_before_sysusers:
                if _group_gid(group) is None:
                    raise InstallError(f"systemd-sysusers did not create configured group {group!r}")
        for group, users in ((values["control_group"], values["control_users"]),
                             (values["observer_group"], values["observer_users"])):
            for user in users:
                self._command(["gpasswd", "-a", user, group], timeout=30)

        os.makedirs(self.paths.unit_dir, mode=0o755, exist_ok=True)
        if resource_change or version_drift or earlier_pending:
            self._command([self.systemctl, "daemon-reload"], timeout=30)
        for link, target in self.paths.client_links:
            _atomic_symlink(target, link, link + ".cgprofiled-new")
        if values["enable_now"]:
            enabled = ["cgprofile.slice", "cgprofiled.service"]
            if has_socket_units(values["version"]):
                enabled.extend(("cgprofile-control.socket", "cgprofile-observer.socket"))
            self._command([self.systemctl, "enable", *enabled], timeout=60)
        if not active and values["enable_now"]:
            self._command([self.systemctl, "start", "cgprofile.slice"], timeout=30)
            if has_socket_units(values["version"]):
                self._command([self.systemctl, "start", "cgprofile-control.socket",
                               "cgprofile-observer.socket"], timeout=30)
            self._command([self.systemctl, "start", "cgprofiled.service"], timeout=60)
        elif active:
            if socket_restart:
                for name in socket_restart:
                    self._command([self.systemctl, "restart", name], timeout=60)
            elif service_restart:
                self._command([self.systemctl, "restart", "cgprofiled.service"], timeout=60)
            elif config_drift:
                self._command([self.systemctl, "reload", "cgprofiled.service"], timeout=30)
        if active or values["enable_now"]:
            self._wait_ready(venv, values["version"])

        created_groups = list(dict.fromkeys([
            *values["prior_created_groups"],
            *(group for group in (values["control_group"], values["observer_group"])
              if (values.get("control_group_created") and group == values["control_group"])
              or (values.get("observer_group_created") and group == values["observer_group"])),
        ]))
        effective = daemon_config.parse_table(config_text)
        record = {
            "wheel": values["wheel"], "cli_extended_wheel": values["cli_extended_wheel"],
            "wheel_source": values["wheel_source"], "control_group": values["control_group"],
            "observer_group": values["observer_group"], "control_users": values["control_users"],
            "observer_users": values["observer_users"],
            "gates_slice": effective.get("gates_slice", values["gates_slice"]),
            "observe_slices": effective.get("observe_slices", values["observe_slices"]),
            "allow_uids": effective.get("allow_uids", values["allow_uids"]),
            "drain_timeout": values["drain_timeout"], "enable_now": values["enable_now"],
            "created_groups": created_groups,
        }
        removed = self._prune_versions(values["version"])
        _atomic_write(self.paths.install_record, _render_record(record), 0o644)
        os.unlink(self.paths.pending)
        return {
            "ok": True, "dry_run": False, "version": values["version"],
            "config_drift": config_drift, "config_changes": config_changes,
            "resources_changed": sorted(name for name, changed in resource_drift.items() if changed),
            "pruned": removed, "enabled": values["enable_now"], "downgrade": downgrade,
            "forced_sessions": values.get("forced_sessions", []),
            "warnings": values.get("warnings", []),
            "users_to_add": values["users_to_add"],
            "users_forgotten": values["users_forgotten"],
        }

    def _plan(self, values: Mapping[str, Any], config_path: str, config_text: str,
              version_drift: bool, earlier_pending: bool) -> Dict[str, Any]:
        """Build the version tree, render and compare; nothing on the host is changed."""
        venv = self._ensure_version(values)
        staged_dir = tempfile.mkdtemp(prefix=".cgprofiled-render.", dir=self.paths.opt)
        config_stage = os.path.join(staged_dir, "cgprofiled.toml")
        try:
            _atomic_write(config_stage, config_text, 0o644)
            check = self._command([os.path.join(venv, "bin", "cgprofiled"), "--config", config_stage,
                                   "config", "check"], check=False, timeout=30)
            if check.returncode != 0:
                raise InstallError(f"cgprofiled config check refused the rendered config: {(check.stderr or check.stdout).strip()}")
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
        active = self._is_active("cgprofiled.service")
        socket_active = {name: self._is_active(name) for name in (
            "cgprofile-control.socket", "cgprofile-observer.socket") if has_socket_units(values["version"])}
        fixed_change = self._fixed_config_changed(config_path, values["config_values"], config_text)
        if earlier_pending:
            # A re-apply over an earlier PENDING compares against files the failed
            # attempt may already have written, so drift is invisible: reload and
            # restart anyway.
            resource_drift = dict.fromkeys(resource_drift, True)
            config_drift = True
        resource_change = any(resource_drift.values()) or removed_socket_units
        service_restart = (version_drift or resource_drift.get("cgprofiled.service", False) or fixed_change
                           or earlier_pending)
        socket_restart = [name for name, is_active in socket_active.items()
                          if resource_drift.get(name, False) or not is_active]
        need_restart = service_restart or bool(socket_restart)
        return {
            "venv": venv, "rendered": rendered, "resource_drift": resource_drift,
            "config_drift": config_drift, "socket_restart": socket_restart,
            "service_restart": service_restart, "need_restart": need_restart,
            "resource_change": resource_change, "active": active,
        }

    def _running_version_dir(self) -> Optional[str]:
        """The /opt/cgprofile/<ver> tree this very process runs from, if any."""
        prefix = os.path.realpath(self.running_prefix)
        opt = os.path.realpath(self.paths.opt)
        if not prefix.startswith(opt + os.sep):
            return None
        return os.path.join(opt, prefix[len(opt) + 1:].split(os.sep)[0])

    def _uninstall_targets(self) -> Optional[List[str]]:
        """Refuse before any change unless cgprofiled install owns this host.

        Returns the existing paths uninstall would remove, or None when nothing
        cgprofiled install writes is present.
        """
        paths = self.paths
        owned = os.path.lexists(paths.install_record) or os.path.lexists(paths.pending)
        # The plan has uninstall remove host-setup's leftover service drop-in so a role
        # can leave cleanly; every other foreign rule or old-layout path is refused.
        mdt_dropins = {f"foreign rule or drop-in: {path}" for path in _mdt_dropins(paths)}
        problems = [
            item for item in old_installation_problems(paths, paths.config)
            if item not in mdt_dropins
            and not (owned and item.startswith(_OWN_INTERRUPTED_FINDINGS))
        ]
        for directory, allowed in (
            (paths.runtime_root, {"control", "observer", "state"}),
            (paths.control_dir, {"cgprofile.sock"}),
            (paths.observer_dir, {"cgprofile.sock"}),
        ):
            for name in _directory_names(directory):
                if name not in allowed:
                    problems.append(f"unknown path under the runtime tree: {os.path.join(directory, name)}")
        for name in _directory_names(paths.opt):
            path = os.path.join(paths.opt, name)
            known = name in {"current", "current.new", "PENDING"} or name.startswith((".stage.", ".cgprofiled-render."))
            if not known and not (VERSION_RE.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path)):
                problems.append(f"unknown path under the version root: {path}")
        for node in (paths.at(daemon_config.DEFAULT_CONTROL_SOCKET), paths.at(daemon_config.DEFAULT_OBSERVER_SOCKET)):
            if os.path.lexists(node) and not stat.S_ISSOCK(os.lstat(node).st_mode):
                problems.append(f"{node} is not a socket")
        if problems:
            raise InstallError(manual_removal_message(problems, "uninstall"))
        if not owned:
            return None
        candidates = [
            *(os.path.join(paths.unit_dir, unit) for unit in
              ("cgprofiled.service", "cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.slice")),
            self._resource_paths()["cgprofile.sysusers"],
            *_mdt_dropins(paths),
            *(link for link, _target in paths.client_links if _is_install_link(link, paths)),
            paths.at(daemon_config.DEFAULT_CONTROL_SOCKET),
            paths.at(daemon_config.DEFAULT_OBSERVER_SOCKET),
            paths.control_dir, paths.observer_dir, paths.state_dir, paths.runtime_root,
            paths.opt, paths.config, paths.install_record,
        ]
        return [path for path in candidates if os.path.lexists(path)]

    def uninstall(self, args: argparse.Namespace, runtime: CliRuntime) -> Dict[str, Any]:
        """Remove what cgprofiled install wrote.

        The ownership markers go last (PENDING, then install.toml), preserving the
        group list through cleanup. The tree this process runs from is left for
        ``finish_uninstall``, which the handler calls after printing the result; if
        that deferred step fails, the handler gives manual cleanup steps because this
        executable may already have been removed.
        """
        self._check_config_arg(args)
        record = _read_record(self.paths.install_record)
        # An interrupted first install names its groups in PENDING; the record has the rest.
        created_groups = list(dict.fromkeys(
            [*_record_created_groups(record), *_pending_created_groups(self.paths)]))
        remove_groups = bool(getattr(args, "remove_groups", False))
        purge_data = bool(getattr(args, "purge_data", False))
        if not runtime.dry_run and self.geteuid() != 0:
            raise InstallError("cgprofiled uninstall needs root")
        targets = self._uninstall_targets()
        if targets is None:
            return {"ok": True, "dry_run": runtime.dry_run, "removed": [], "groups_removed": [],
                    "data_purged": False, "message": "nothing installed by cgprofiled install"}
        if runtime.dry_run:
            return {
                "ok": True, "dry_run": True, "removed": targets,
                "groups_removed": created_groups if remove_groups else [],
                "data_purged": purge_data,
            }
        if not runtime.yes and not runtime.confirm("Stop and remove the cgprofile host service?"):
            raise InstallError("uninstallation was not confirmed")

        paths = self.paths
        units = ("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofiled.service")
        # The sockets go first, so a connection during the stop cannot re-activate the service.
        installed_units = [unit for unit in units if os.path.lexists(os.path.join(paths.unit_dir, unit))]
        if installed_units:
            self._command([self.systemctl, "disable", "--now", *installed_units], timeout=60)
        slice_file = os.path.join(paths.unit_dir, "cgprofile.slice")
        if os.path.lexists(slice_file):
            # Removing the unit file alone would leave the slice active (holding its
            # MemoryMin reservation) until the next reboot.
            self._command([self.systemctl, "stop", "cgprofile.slice"], timeout=60)
        # A /usr/local/bin entry that is not ours stays, and is reported as kept.
        kept = [link for link, _target in paths.client_links
                if os.path.lexists(link) and not _is_install_link(link, paths)]
        for path in (
            *(os.path.join(paths.unit_dir, unit) for unit in (*units, "cgprofile.slice")),
            self._resource_paths()["cgprofile.sysusers"],
            *_mdt_dropins(paths),
            *(link for link, _target in paths.client_links if link not in kept),
            paths.at(daemon_config.DEFAULT_CONTROL_SOCKET),
            paths.at(daemon_config.DEFAULT_OBSERVER_SOCKET),
        ):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot remove {path}: {exc.strerror or exc}") from None
        for directory in (paths.service_dropins, paths.control_dir, paths.observer_dir):
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
        # Systemd keeps the removed units loaded until it reloads.
        self._command([self.systemctl, "daemon-reload"], timeout=30)
        self._command([self.systemctl, "reset-failed", *units, "cgprofile.slice"], check=False, timeout=30)

        removed_groups: List[str] = []
        failed_groups: List[str] = []
        failed_group_names: List[str] = []
        if remove_groups:
            for group in created_groups:
                # A rerun after a partial uninstall finds some groups already gone.
                if _group_gid(group) is None:
                    continue
                result = self._command(["groupdel", group], check=False, timeout=30)
                if result.returncode == 0:
                    removed_groups.append(group)
                else:
                    detail = (result.stderr or result.stdout or "").strip().splitlines()
                    failed_group_names.append(group)
                    failed_groups.append(f"{group} ({detail[-1] if detail else 'exit ' + str(result.returncode)})")
        if purge_data:
            try:
                shutil.rmtree(paths.state_data)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise InstallError(f"cannot purge {paths.state_data}: {exc}") from None
        try:
            os.unlink(paths.config)
        except FileNotFoundError:
            pass
        body: Dict[str, Any] = {
            "ok": True, "dry_run": False, "removed": targets, "groups_removed": removed_groups,
            "data_purged": purge_data, "kept": kept,
            "manual_groups": created_groups if remove_groups else [],
        }
        running = self._running_version_dir()
        self._remove_version_root(keep=running)
        if failed_groups:
            # Keep the ownership markers and running version tree for explicit cleanup.
            group_steps = "; ".join(f"run `groupdel {group}` if that group still exists"
                                     for group in failed_group_names)
            raise InstallError(
                "could not remove group(s) " + ", ".join(failed_groups)
                + "; service resources were removed and the ownership markers are kept. Manual cleanup:"
                + f" after this command exits, remove any remaining version tree under {self.paths.opt} "
                f"and {self.paths.current} if present;"
                + f" free the group (it is probably a user's primary group), {group_steps}; then remove "
                f"{self.paths.install_record} and {self.paths.pending} if present after verifying the owned "
                "files are gone"
            )
        if running is None:
            self._remove_markers(body)
        else:
            body["deferred"] = running
        return body

    def _remove_version_root(self, keep: Optional[str]) -> None:
        paths = self.paths
        try:
            for name in sorted(_directory_names(paths.opt)):
                path = os.path.join(paths.opt, name)
                if name == "PENDING" or (keep is not None and not os.path.islink(path)
                                         and os.path.realpath(path) == keep):
                    continue
                if keep is not None and name == "current":
                    continue  # the running process may reach its tree through it; see finish_uninstall
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or paths.opt}: {exc}") from None

    def _remove_markers(self, body: Dict[str, Any]) -> None:
        """PENDING, then the opt directory, then the install record: the markers go last."""
        paths = self.paths
        try:
            if os.path.lexists(paths.pending):
                os.unlink(paths.pending)
            if os.path.isdir(paths.opt):
                os.rmdir(paths.opt)
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or paths.opt}: {exc}") from None
        try:
            os.unlink(paths.install_record)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(os.path.dirname(paths.install_record))
        except FileNotFoundError:
            pass
        except OSError:
            body.setdefault("kept", []).append(os.path.dirname(paths.install_record))

    def finish_uninstall(self, body: Dict[str, Any]) -> None:
        """Remove the version tree the process ran from, then the ownership markers.

        Called after the result is printed: the interpreter and its modules live in
        the tree being deleted, so nothing may need them afterwards.
        """
        running = body.pop("deferred", None)
        if running is None:
            return
        try:
            shutil.rmtree(running)
            Path(self.paths.current).unlink(missing_ok=True)
        except OSError as exc:
            raise InstallError(f"cannot remove {exc.filename or running}: {exc}") from None
        self._remove_markers(body)


def _install_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    try:
        body = Installer().install(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    runtime.output.primary(body if runtime.json_mode else _install_summary(body))
    return 0


def _uninstall_handler(args: argparse.Namespace, runtime: CliRuntime) -> int:
    subject = Installer()
    try:
        body = subject.uninstall(args, runtime)
    except InstallError as exc:
        raise CliFailure(str(exc), exit_code=exc.exit_code) from None
    # Print before the tree this process runs from is deleted (see finish_uninstall).
    shown = {key: value for key, value in body.items() if key not in {"deferred", "manual_groups"}}
    runtime.output.primary(shown if runtime.json_mode else _uninstall_summary(shown))
    deferred = body.get("deferred")
    try:
        subject.finish_uninstall(body)
    except InstallError as exc:
        steps = []
        if deferred:
            steps.append(f"verify and remove the remaining version tree {deferred} and {subject.paths.current} if present")
        steps.extend(f"run `groupdel {group}` if the cgprofiled-created group still exists"
                     for group in body.get("manual_groups", []))
        steps.append(
            f"after verifying the cgprofiled files are gone, remove {subject.paths.install_record} "
            f"and {subject.paths.pending} if present"
        )
        raise CliFailure(f"{exc}; manual cleanup steps: " + "; ".join(steps),
                         exit_code=exc.exit_code) from None
    return 0


def _install_summary(body: Mapping[str, Any]) -> str:
    if body.get("dry_run"):
        lines = [f"dry run: cgprofile {body['version']} would be installed; no changes made"]
        verb = "would add"
    else:
        resources = ", ".join(body.get("resources_changed", [])) or "no resource drift"
        lines = [f"cgprofile {body['version']} installed; {resources}; enabled={body['enabled']}"]
        verb = "added"
    addition_count = 0
    for level in daemon_config.SOCKET_LEVELS:
        entry = body.get("users_to_add", {}).get(level, {})
        group = entry.get("group")
        for user in entry.get("users", []):
            lines.append(f"{verb} user {user} to {group} ({level})")
            addition_count += 1
    if addition_count:
        lines.append("Membership applies on the user's next login or a restarted cgprofiled service.")
    if body.get("users_forgotten"):
        lines.append("dropped recorded users that no longer exist: " + ", ".join(body["users_forgotten"]))
    lines.extend(f"WARNING: {warning}" for warning in body.get("warnings", []))
    return "\n".join(lines)


def _uninstall_summary(body: Mapping[str, Any]) -> str:
    if "message" in body:
        return str(body["message"])
    verb = "would remove" if body.get("dry_run") else "removed"
    lines = [f"{verb}: {path}" for path in body.get("removed", [])]
    lines.extend(f"{verb} group: {group}" for group in body.get("groups_removed", []))
    lines.extend(f"kept (not written by cgprofiled install): {path}" for path in body.get("kept", []))
    return "\n".join(lines) if lines else "nothing to remove"


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
        OptionSpec(("--add-user",), "add an existing user to a socket access group (repeatable)",
                   group="ACCESS", metavar="LEVEL=USER", parser_kwargs={"action": "append"}),
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
        OptionSpec(("--no-enable",), "install resources without enabling or starting cgprofiled", group="SERVICE",
                   parser_kwargs={"action": "store_true"}),
    )
    registry.register(VerbSpec(
        "install", description="install or upgrade cgprofiled, its systemd resources and access groups",
        group=VerbGroup.MODIFICATION.value, mutating=True, dry_run=True,
        include_progress=False, options=options, handler=_install_handler,
        examples=("cgprofiled install --wheel VERSION@sha256:... --cli-extended-wheel VERSION@sha256:... --enable",
                  "cgprofiled install --wheel-source ./wheels --control-group cgprofile-control "
                  "--observer-group cgprofile-observer --add-user control=alice --no-enable"),
    ))
    registry.register(VerbSpec(
        "uninstall", description="stop and remove cgprofiled service resources",
        group=VerbGroup.MODIFICATION.value, mutating=True, dry_run=True,
        include_progress=False,
        options=(
            OptionSpec(("--remove-groups",), "remove groups created by cgprofiled install",
                       group="REMOVE", parser_kwargs={"action": "store_true"}),
            OptionSpec(("--purge-data",), "also remove /var/lib/cgprofile sessions and overrides",
                       group="REMOVE", parser_kwargs={"action": "store_true"}),
        ),
        handler=_uninstall_handler,
        examples=("cgprofiled uninstall", "cgprofiled uninstall --remove-groups --purge-data --yes"),
    ))



