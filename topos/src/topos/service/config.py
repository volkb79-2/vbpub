"""The systemd-facing topos daemon configuration."""

from __future__ import annotations

import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("/etc/topos/toposd.toml")
_DEFAULT_STATE_DIR = Path("/run/topos/state")
_DEFAULT_SOCKET_PATH = Path("/run/topos/observe/topos.sock")
_DEFAULT_SOCKET_GROUP = "topos-observe"
_DEFAULT_SOCKET_MODE = "0660"
_GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_MODE_RE = re.compile(r"^[0-7]{4}$")


class ConfigError(ValueError):
    """A named toposd.toml validation or loading error."""


@dataclass(frozen=True)
class ObserveSocketConfig:
    path: Path = _DEFAULT_SOCKET_PATH
    group: str = _DEFAULT_SOCKET_GROUP
    mode: str = _DEFAULT_SOCKET_MODE

    @property
    def mode_bits(self) -> int:
        return int(self.mode, 8)


@dataclass(frozen=True)
class SocketsConfig:
    observe: ObserveSocketConfig = ObserveSocketConfig()


@dataclass(frozen=True)
class ServiceConfig:
    state_dir: Path = _DEFAULT_STATE_DIR
    sockets: SocketsConfig = SocketsConfig()


DEFAULTS = ServiceConfig()


def defaults() -> ServiceConfig:
    """Return the immutable built-in configuration."""
    return DEFAULTS


def load_config(path: str | Path | None = None) -> ServiceConfig:
    """Load configuration, allowing an absent implicit default file only."""
    config_path = DEFAULT_CONFIG_PATH if path is None else Path(path)
    try:
        contents = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if path is None:
            return DEFAULTS
        raise ConfigError(f"config file {config_path} does not exist") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc.strerror or exc}") from exc
    return parse_config(contents, source=str(config_path))


def parse_config(contents: str, *, source: str = "<config>") -> ServiceConfig:
    """Parse and validate a TOML document, filling omitted keys from defaults."""
    try:
        values: Any = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: invalid TOML: {exc}") from exc
    if not isinstance(values, dict):  # pragma: no cover - tomllib returns a dict
        raise ConfigError(f"{source}: config must be a table")

    _reject_unknown(values, {"state_dir", "sockets"}, "")

    raw_state_dir = values.get("state_dir", str(_DEFAULT_STATE_DIR))
    if not isinstance(raw_state_dir, str):
        raise ConfigError("state_dir must be a string")
    state_dir = _absolute_path(raw_state_dir, "state_dir")

    raw_sockets = values.get("sockets", {})
    if not isinstance(raw_sockets, dict):
        raise ConfigError("sockets must be a table")
    _reject_unknown(raw_sockets, {"observe"}, "sockets")
    if "sockets" in values and "observe" not in raw_sockets:
        raise ConfigError("sockets must contain exactly one level: observe")
    raw_observe = raw_sockets.get("observe", {})
    if not isinstance(raw_observe, dict):
        raise ConfigError("sockets.observe must be a table")
    _reject_unknown(raw_observe, {"path", "group", "mode"}, "sockets.observe")

    raw_socket_path = raw_observe.get("path", str(_DEFAULT_SOCKET_PATH))
    if not isinstance(raw_socket_path, str):
        raise ConfigError("sockets.observe.path must be a string")
    socket_path = _absolute_path(raw_socket_path, "sockets.observe.path")
    if not socket_path.name.endswith(".sock"):
        raise ConfigError("sockets.observe.path must end in .sock")

    raw_group = raw_observe.get("group", _DEFAULT_SOCKET_GROUP)
    if not isinstance(raw_group, str) or not _GROUP_RE.fullmatch(raw_group):
        raise ConfigError(
            "sockets.observe.group must match ^[a-z_][a-z0-9_-]{0,31}$"
        )
    if raw_group == "docker":
        # docker group membership is root-equivalent; observers join topos-observe instead.
        raise ConfigError(
            "sockets.observe.group must not be docker; add the users who need to observe to "
            "topos-observe (toposd install --add-user observe=USER)"
        )

    raw_mode = raw_observe.get("mode", _DEFAULT_SOCKET_MODE)
    if not isinstance(raw_mode, str) or not _MODE_RE.fullmatch(raw_mode):
        raise ConfigError("sockets.observe.mode must be a 4-digit octal string")
    mode_bits = int(raw_mode, 8)
    if mode_bits & ~0o666:
        raise ConfigError("sockets.observe.mode contains bits outside 0o666")

    if state_dir == Path("/run") or not state_dir.is_relative_to(Path("/run")):
        raise ConfigError("state_dir must lie under /run/")
    socket_dir = socket_path.parent
    if state_dir == socket_dir and socket_path.is_relative_to(state_dir):
        raise ConfigError("sockets.observe.path must not be inside state_dir")
    if (
        state_dir == socket_dir
        or state_dir.is_relative_to(socket_dir)
        or socket_dir.is_relative_to(state_dir)
    ):
        raise ConfigError("state_dir must be neither a parent nor a child of the socket directory")

    return ServiceConfig(
        state_dir=state_dir,
        sockets=SocketsConfig(
            observe=ObserveSocketConfig(path=socket_path, group=raw_group, mode=raw_mode)
        ),
    )


def _absolute_path(value: str, key: str) -> Path:
    if any(
        char.isspace()
        or unicodedata.category(char) == "Cc"
        or char in {"%", chr(92)}
        for char in value
    ):
        raise ConfigError(
            f"{key} must not contain whitespace, control characters, '%', or backslashes"
        )
    if ".." in value.split("/"):
        raise ConfigError(f"{key} must not contain '..' path components")
    if not value.startswith("/"):
        raise ConfigError(f"{key} must be absolute")
    return Path(os.path.normpath(value))


def _reject_unknown(values: dict[str, Any], allowed: set[str], prefix: str) -> None:
    for key in values:
        if key not in allowed:
            name = f"{prefix}.{key}" if prefix else key
            raise ConfigError(f"unknown key {name!r}")


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULTS",
    "ConfigError",
    "ObserveSocketConfig",
    "ServiceConfig",
    "SocketsConfig",
    "defaults",
    "load_config",
    "parse_config",
]
