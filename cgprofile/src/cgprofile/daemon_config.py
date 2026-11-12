"""The ``cgprofiled`` configuration: schema, file, runtime overrides, effective values.

Authority: ``docs/CGPROFILECTL-SPEC.md`` sections 3.2 to 3.5. Root keys and fixed
``[sockets.<level>]`` tables in ``/etc/cgprofile/cgprofiled.toml``, one validation
function shared by the file loader, ``cgprofiled config check`` and the
``config-set`` verb, and runtime overrides above the file (uid 0 only,
optionally persisted to ``/var/lib/cgprofile/overrides.toml``). Precedence,
highest first: override, file, built-in default.

Stdlib only (``tomllib``). Nothing here touches the sockets or the sessions;
:class:`cgprofile.serve.SessionServer` reads the effective values.
"""

from __future__ import annotations

import difflib
import grp
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

DEFAULT_CONFIG_PATH = "/etc/cgprofile/cgprofiled.toml"
DEFAULT_OVERRIDES_PATH = "/var/lib/cgprofile/overrides.toml"
DEFAULT_CONTROL_SOCKET = "/run/cgprofile/control/cgprofile.sock"
DEFAULT_OBSERVER_SOCKET = "/run/cgprofile/observer/cgprofile.sock"
DEFAULT_DAMON_LOCK_PATH = "/run/cgprofile/state/damon.lock"
DEFAULT_CONTROL_GROUP = "cgprofile-control"
DEFAULT_OBSERVER_GROUP = "cgprofile-observer"
SOCKET_LEVELS = ("control", "observer")


class ConfigError(ValueError):
    """A config file, key or value the schema refuses."""


class PersistError(OSError):
    """The overrides file could not be written; nothing was applied."""


@dataclass(frozen=True)
class KeySpec:
    name: str
    kind: str  # path | string | int | number | onoff | slice | slices | uids
    default: Any
    reloadable: bool
    tunable: bool
    low: Optional[int] = None
    high: Optional[int] = None


def _spec(name: str, kind: str, default: Any, *, reloadable: bool = True,
          tunable: bool = False, low: Optional[int] = None,
          high: Optional[int] = None) -> KeySpec:
    return KeySpec(name, kind, default, reloadable, tunable, low, high)


KEYS: Dict[str, KeySpec] = {
    spec.name: spec for spec in (
        _spec("sessions_dir", "path", "/var/lib/cgprofile/sessions", reloadable=False),
        _spec("sockets.control", "socket", {
            "path": DEFAULT_CONTROL_SOCKET, "group": DEFAULT_CONTROL_GROUP, "mode": "0660",
        }, reloadable=False),
        _spec("sockets.observer", "socket", {
            "path": DEFAULT_OBSERVER_SOCKET, "group": DEFAULT_OBSERVER_GROUP, "mode": "0660",
        }, reloadable=False),
        _spec("damon_lock", "path", DEFAULT_DAMON_LOCK_PATH, reloadable=False),
        _spec("proc_root", "path", "/proc", reloadable=False),
        _spec("busctl", "string", "busctl", reloadable=False),
        _spec("export_max_bytes", "int", 256 * 1024 * 1024, tunable=True, low=1),
        _spec("max_exports", "int", 4, tunable=True, low=1, high=16),
        _spec("max_watchers", "int", 32, tunable=True, low=1, high=128),
        _spec("interval", "number", 1.0, tunable=True),
        _spec("damon_default", "onoff", "on", tunable=True),
        _spec("keep_sessions", "int", 200, tunable=True, low=1),
        _spec("keep_days", "int", 14, tunable=True, low=1),
        _spec("max_sessions", "int", 16, tunable=True, low=1, high=64),
        _spec("observe_slices", "slices", [], tunable=False),
        _spec("gates_slice", "slice", "dev-gates.slice", tunable=False),
        _spec("allow_uids", "uids", [], tunable=True),
    )
}
TUNABLE_KEYS: Tuple[str, ...] = tuple(k for k, s in KEYS.items() if s.tunable)

# Environment variables the 1.x daemon read, and what replaced them. Setting
# one is a refusal to start (spec 3.4): a trust decision silently ignored is
# worse than a refusal.
STALE_ENV: Dict[str, str] = {
    "CGPROFILE_ALLOW_UIDS": "set allow_uids in {config}",
    "CGPROFILE_SESSIONS": "set sessions_dir in {config}",
    "CGPROFILE_DAMON_LOCK_PATH": "set damon_lock in {config}",
    "CGPROFILE_PROC_ROOT": "set proc_root in {config}",
    "CGPROFILE_BUSCTL": "set busctl in {config}",
    "CGPROFILE_PID_NAMESPACE_MODE": "it was removed (the pid-namespace check always runs)",
    "CGPROFILE_HELPER_IMAGE": "it was removed (helper mode is gone)",
    "CGPROFILE_PLACEMENT_PROBE_IMAGE": "it was removed (the placement probe is gone)",
    "CGPROFILE_HELPER_CGROUP_PARENT": "it was removed (helper mode is gone)",
    "CGPROFILE_IN_HELPER": "it was removed (helper mode is gone)",
    "CGPROFILE_VERSION": "it was removed (the version is the installed distribution's)",
}


def stale_env_problems(environ: Mapping[str, str], config_path: str) -> List[str]:
    """One message per stale daemon variable present in ``environ``."""
    problems = []
    for name in sorted(STALE_ENV):
        if name in environ:
            hint = STALE_ENV[name].format(config=config_path)
            problems.append(f"{name} is no longer read; {hint}")
    return problems


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _slice_ok(name: str) -> str:
    from . import targets as targets_mod

    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"not a usable slice name: {name!r}")
    try:
        targets_mod.slice_to_path(name)
    except targets_mod.TargetError as exc:
        raise ConfigError(str(exc)) from None
    return name.strip()


def nearest_key(name: str) -> Optional[str]:
    matches = difflib.get_close_matches(name, list(KEYS), n=1, cutoff=0.5)
    return matches[0] if matches else None


def validate(key: str, value: Any) -> Any:
    """Return the normalised value of ``key`` or raise :class:`ConfigError`."""
    spec = KEYS.get(key)
    if spec is None:
        near = nearest_key(key)
        hint = f"; did you mean {near!r}?" if near else ""
        raise ConfigError(f"unknown key {key!r}{hint}")
    kind = spec.kind
    if kind == "socket":
        if not isinstance(value, dict):
            raise ConfigError(f"{key} must be a table with path, group and mode")
        required = {"path", "group", "mode"}
        if set(value) != required:
            missing = sorted(required - set(value))
            extra = sorted(set(value) - required)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if extra:
                detail.append("unknown " + ", ".join(extra))
            raise ConfigError(f"{key}: " + "; ".join(detail))
        path = value["path"]
        group = value["group"]
        mode = value["mode"]
        if not isinstance(path, str) or not path.startswith("/") or not path.strip():
            raise ConfigError(f"{key}.path must be an absolute path")
        if not isinstance(group, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", group):
            raise ConfigError(f"{key}.group is not a usable group name: {group!r}")
        if not isinstance(mode, str) or not re.fullmatch(r"0?[0-7]{3}", mode):
            raise ConfigError(f"{key}.mode must be an octal mode string such as \"0660\"")
        return {"path": path, "group": group, "mode": mode.zfill(4)}
    if kind in ("path", "string"):
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{key} must be a non-empty string")
        if kind == "path" and not value.startswith("/"):
            raise ConfigError(f"{key} must be an absolute path, got {value!r}")
        return value
    if kind == "int":
        if not _is_int(value):
            raise ConfigError(f"{key} must be an integer")
        if spec.low is not None and value < spec.low:
            raise ConfigError(f"{key} must be at least {spec.low}, got {value}")
        if spec.high is not None and value > spec.high:
            raise ConfigError(f"{key} must be at most {spec.high}, got {value}")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{key} must be a number of seconds")
        if not math.isfinite(value) or value <= 0:
            raise ConfigError(f"{key} must be a finite number greater than 0")
        return float(value)
    if kind == "onoff":
        if value not in ("on", "off"):
            raise ConfigError(f"{key} must be \"on\" or \"off\"")
        return value
    if kind == "slice":
        return _slice_ok(value)
    if kind == "slices":
        if not isinstance(value, list):
            raise ConfigError(f"{key} must be an array of slice names")
        return [_slice_ok(item) for item in value]
    # uids
    if not isinstance(value, list):
        raise ConfigError(f"{key} must be an array of integers")
    uids: List[int] = []
    for item in value:
        if not _is_int(item) or item < 0:
            raise ConfigError(f"{key} entries must be non-negative integers, got {item!r}")
        if item not in uids:
            uids.append(item)
    return uids


def _group_gid(name: str) -> Optional[int]:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def _validate_socket_group(level: str, name: str,
                           gid_lookup: Callable[[str], Optional[int]] = _group_gid,
                           docker_gid: Optional[int] = None) -> Optional[int]:
    """Validate one dedicated socket group and return its GID when it exists."""
    default_group = KEYS[f"sockets.{level}"].default["group"]
    if name == "docker":
        raise ConfigError(
            f"the {level} group {name!r} is the docker group (membership is root-equivalent); "
            f"use a dedicated group such as {default_group}"
        )
    gid = gid_lookup(name)
    if gid == 0:
        raise ConfigError(
            f"the {level} group {name!r} resolves to GID 0 (root-equivalent); "
            f"use a dedicated group such as {default_group}"
        )
    if docker_gid is None:
        docker_gid = gid_lookup("docker")
    if gid is not None and docker_gid is not None and gid == docker_gid:
        raise ConfigError(
            f"the {level} group {name!r} has the docker group's GID {gid} (membership is root-equivalent); "
            f"use a dedicated group such as {default_group}"
        )
    return gid


def validate_socket_groups(
    sockets: Mapping[str, Mapping[str, str]],
    *,
    gid_lookup: Callable[[str], Optional[int]] = _group_gid,
) -> None:
    """Reject Docker/root access groups and duplicate same-service level GIDs."""
    docker_gid = gid_lookup("docker")
    gids: Dict[str, Optional[int]] = {}
    for level in SOCKET_LEVELS:
        gids[level] = _validate_socket_group(
            level, sockets[level]["group"], gid_lookup, docker_gid)
    if sockets["control"]["group"] == sockets["observer"]["group"]:
        raise ConfigError(
            f"the control and observer groups both name {sockets['control']['group']!r}; "
            "use separate dedicated groups for the two access levels"
        )
    if gids["control"] is not None and gids["control"] == gids["observer"]:
        raise ConfigError(
            f"the control group {sockets['control']['group']!r} and observer group "
            f"{sockets['observer']['group']!r} resolve to the same GID {gids['control']}; "
            "use separate dedicated groups for the two access levels"
        )


def parse_table(text: str, *, validate_groups: bool = True) -> Dict[str, Any]:
    """Parse and validate one flat TOML table (the file and the overrides)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"not valid TOML: {exc}") from None
    old = sorted(set(raw) & {"ctl_socket", "observer_socket"})
    if old:
        replacements = {"ctl_socket": "[sockets.control] path", "observer_socket": "[sockets.observer] path"}
        key = old[0]
        raise ConfigError(f"{key} was removed; use {replacements[key]}")
    flattened: Dict[str, Any] = {}
    for key, value in raw.items():
        if key != "sockets":
            flattened[key] = value
            continue
        if not isinstance(value, dict):
            raise ConfigError("sockets must contain [sockets.control] and [sockets.observer] tables")
        for level, socket_values in value.items():
            flattened[f"sockets.{level}"] = socket_values
    values = {key: validate(key, value) for key, value in flattened.items()}
    control = values.get("sockets.control", KEYS["sockets.control"].default)
    observer = values.get("sockets.observer", KEYS["sockets.observer"].default)
    if validate_groups:
        validate_socket_groups({"control": control, "observer": observer})
    if os.path.normpath(control["path"]) == os.path.normpath(observer["path"]):
        raise ConfigError("sockets.control.path and sockets.observer.path must differ")
    return values


def socket_settings(values: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    """Return validated control and observer socket settings from config values."""
    result = {}
    for level in SOCKET_LEVELS:
        key = f"sockets.{level}"
        result[level] = validate(key, values.get(key, KEYS[key].default))
    validate_socket_groups(result)
    if os.path.normpath(result["control"]["path"]) == os.path.normpath(result["observer"]["path"]):
        raise ConfigError("sockets.control.path and sockets.observer.path must differ")
    return result


def render_config(values: Mapping[str, Any]) -> str:
    """Render TOML with root keys before entering the socket tables."""
    sockets = socket_settings(values)
    flat = {key: value for key, value in values.items() if not key.startswith("sockets.")}
    lines: List[str] = list(render_table(flat).splitlines(keepends=True))
    if lines:
        lines.append("\n")
    for level in SOCKET_LEVELS:
        lines.append(f"[sockets.{level}]\n")
        for name in ("path", "group", "mode"):
            lines.append(f"{name} = {_toml_value(sockets[level][name])}\n")
        lines.append("\n")
    return "".join(lines)


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


def render_table(values: Mapping[str, Any]) -> str:
    return "".join(f"{key} = {_toml_value(values[key])}\n" for key in sorted(values))


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_config_file(path: str, *, explicit: bool, validate_groups: bool = True) -> Tuple[Dict[str, Any], str]:
    """``(values, digest)`` for ``path``; a missing default file is empty."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"config file {path} does not exist") from None
        return {}, digest_of(b"")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError(f"config file {path} is not UTF-8") from None
    try:
        return parse_table(text, validate_groups=validate_groups), digest_of(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


class Settings:
    """Effective settings: override, then file, then default (thread-safe)."""

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_PATH,
        *,
        explicit: bool = False,
        overrides_path: str = DEFAULT_OVERRIDES_PATH,
        owner_uid: int = 0,
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.config_path = config_path
        self.explicit = explicit
        self.overrides_path = overrides_path
        self.owner_uid = owner_uid
        self.clock = clock
        self.log = log
        self._lock = threading.RLock()
        self._file: Dict[str, Any] = {}
        self._overrides: Dict[str, Any] = {}
        self._persisted: set = set()
        self._fixed_at_start: Dict[str, Any] = {}
        self._fixed_file: Dict[str, Any] = {}
        self.digest = digest_of(b"")
        self.loaded_at: Optional[float] = None
        self.reloads = 0
        self.last_reload_error: Optional[str] = None
        self.overrides_ignored = 0
        self._audit_seq = 0

    # ── loading ──────────────────────────────────────────────────────────

    def load(self) -> None:
        """First load. Raises :class:`ConfigError` for an invalid file."""
        values, digest = read_config_file(self.config_path, explicit=self.explicit)
        with self._lock:
            self._file = values
            self.digest = digest
            self.loaded_at = self.clock()
            self._fixed_file = {k: v for k, v in values.items() if not KEYS[k].reloadable}
            self._fixed_at_start = {
                k: values.get(k, KEYS[k].default) for k, s in KEYS.items() if not s.reloadable
            }
            self._load_overrides()

    def seed(self, values: Mapping[str, Any]) -> None:
        """Take in-process values (a ``SessionServer`` built from keyword
        arguments, no config file) as the file layer. Values equal to the
        default are skipped so their source stays ``default``. The values
        are the caller's own constructor arguments and are not re-validated
        (a test may use an interval no config file could carry)."""
        with self._lock:
            for key, value in values.items():
                if value != KEYS[key].default:
                    self._file[key] = value

    def _load_overrides(self) -> None:
        path = self.overrides_path
        self._overrides = {}
        self._persisted = set()
        self.overrides_ignored = 0
        try:
            st = os.stat(path)
        except OSError:
            return
        if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_uid != self.owner_uid):
            self.overrides_ignored = 1
            self.log(f"overrides file {path} ignored: it must be a regular file, "
                     f"mode 0600, owned by uid {self.owner_uid}")
            return
        try:
            with open(path, "rb") as fh:
                raw = tomllib.loads(fh.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            self.overrides_ignored = 1
            self.log(f"overrides file {path} ignored: {exc}")
            return
        for key, value in raw.items():
            spec = KEYS.get(key)
            try:
                if spec is None or not spec.tunable:
                    raise ConfigError("not a runtime-tunable key in this version")
                value = validate(key, value)
            except ConfigError as exc:
                self.overrides_ignored += 1
                self.log(f"overrides entry {key!r} ignored: {exc}")
                continue
            self._overrides[key] = value
            self._persisted.add(key)

    def reload(self) -> bool:
        """Re-read the file (SIGHUP). Never raises; True when fully applied."""
        try:
            values, digest = read_config_file(self.config_path, explicit=self.explicit)
        except ConfigError as exc:
            with self._lock:
                self.last_reload_error = str(exc)
            self.log(f"reload refused: {exc}")
            return False
        with self._lock:
            changed = [
                k for k, v in self._fixed_at_start.items()
                if values.get(k, KEYS[k].default) != v
            ]
            self._file = {k: v for k, v in values.items() if KEYS[k].reloadable}
            self._file.update(self._fixed_file)
            self.digest = digest
            self.loaded_at = self.clock()
            self.reloads += 1
            if changed:
                self.last_reload_error = f"restart required for {changed[0]}"
                self.log(f"reload applied with a fixed-key change ignored: "
                         f"{self.last_reload_error}")
                return False
            self.last_reload_error = None
            self.log(f"reload applied digest={digest}")
            return True

    # ── reading ──────────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
            if key in self._file:
                return self._file[key]
            return KEYS[key].default

    def source(self, key: str) -> str:
        with self._lock:
            if key in self._overrides:
                return "override"
            return "file" if key in self._file else "default"

    def _below(self, key: str) -> Dict[str, Any]:
        if key in self._file:
            return {"value": self._file[key], "source": "file"}
        return {"value": KEYS[key].default, "source": "default"}

    def entry(self, key: str) -> Dict[str, Any]:
        with self._lock:
            spec = KEYS[key]
            doc: Dict[str, Any] = {
                "value": self.get(key), "source": self.source(key),
                "reloadable": spec.reloadable, "tunable": spec.tunable,
                "persisted": key in self._persisted,
            }
            if key in self._overrides:
                doc["shadows"] = self._below(key)
            return doc

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            loaded = None
            if self.loaded_at is not None:
                loaded = datetime.fromtimestamp(
                    self.loaded_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "file": self.config_path, "digest": self.digest, "loaded_at": loaded,
                "reloads": self.reloads, "last_reload_error": self.last_reload_error,
                "overrides_file": self.overrides_path,
                "overrides_ignored": self.overrides_ignored,
                "keys": {key: self.entry(key) for key in KEYS},
            }

    @property
    def override_count(self) -> int:
        with self._lock:
            return len(self._overrides)

    # ── runtime overrides ────────────────────────────────────────────────

    def next_audit_id(self) -> str:
        with self._lock:
            self._audit_seq += 1
            return str(self._audit_seq)

    def check_tunable(self, key: Any) -> KeySpec:
        spec = KEYS.get(key) if isinstance(key, str) else None
        if spec is None or not spec.tunable:
            raise ConfigError(
                f"{key!r} is not a runtime-tunable key; tunable keys: "
                + ", ".join(TUNABLE_KEYS)
            )
        return spec

    def set_override(self, key: str, value: Any, *, persist: bool = False,
                     dry_run: bool = False) -> Dict[str, Any]:
        """Validate and (unless ``dry_run``) apply one override.

        Raises :class:`ConfigError` (nothing changed) or :class:`PersistError`
        (``persist`` could not write; the override is NOT applied)."""
        self.check_tunable(key)
        new_value = validate(key, value)
        with self._lock:
            old = {"value": self.get(key), "source": self.source(key)}
            if not dry_run:
                if persist:
                    wanted = {k: self._overrides[k] for k in self._persisted}
                    wanted[key] = new_value
                    self._write_overrides(wanted)
                    self._persisted.add(key)
                else:
                    if key in self._persisted:
                        wanted = {k: self._overrides[k] for k in self._persisted if k != key}
                        self._write_overrides(wanted)
                        self._persisted.discard(key)
                self._overrides[key] = new_value
            return {
                "key": key, "old": old,
                "new": {"value": new_value, "source": "override"},
                "persisted": persist,
                "applied": not dry_run,
            }

    def reset(self, key: Optional[str]) -> Dict[str, Any]:
        """Drop one override (or all when ``key`` is None)."""
        with self._lock:
            if key is None:
                keys = sorted(self._overrides)
            else:
                self.check_tunable(key)
                keys = [key] if key in self._overrides else []
            if any(k in self._persisted for k in keys):
                wanted = {k: self._overrides[k] for k in self._persisted if k not in keys}
                self._write_overrides(wanted)
                self._persisted -= set(keys)
            for k in keys:
                del self._overrides[k]
            return {
                "reset": keys,
                "values": {k: {"value": self.get(k), "source": self.source(k)} for k in keys},
            }

    def _write_overrides(self, values: Mapping[str, Any]) -> None:
        directory = os.path.dirname(self.overrides_path) or "."
        tmp = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".overrides-", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.fchmod(fh.fileno(), 0o600)
                fh.write(render_table(values))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.overrides_path)
            tmp = None
        except OSError as exc:
            raise PersistError(
                f"cannot write {self.overrides_path}: {exc.strerror or exc}") from None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
