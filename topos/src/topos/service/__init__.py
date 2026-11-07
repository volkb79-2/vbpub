"""Topos host-service configuration and command entry point.

This package stays import-safe before the daemon or terminal UI is needed.
"""

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULTS,
    ConfigError,
    ObserveSocketConfig,
    ServiceConfig,
    SocketsConfig,
    load_config,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULTS",
    "ConfigError",
    "ObserveSocketConfig",
    "ServiceConfig",
    "SocketsConfig",
    "load_config",
]
