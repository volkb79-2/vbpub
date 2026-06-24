"""CIU host inventory loader — render-safe hosts file (SPEC J §4 / §4.1).

Reads [deploy.hosts.*] from a dedicated file that ciu render/clean never
touches. Precedence (first found wins):
  1. $CIU_HOSTS_FILE environment variable
  2. <repo_root>/.ciu.hosts.toml
  3. ~/.ciu/hosts.toml
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .config_constants import MACHINE_DIR


def load_hosts(repo_root: Path) -> dict:
    """Load the host inventory. Returns {} if no hosts file found."""
    # Precedence: env override > repo-local > user-global
    candidates = []
    env_file = os.environ.get("CIU_HOSTS_FILE")
    if env_file:
        candidates.append(Path(env_file))
    candidates.append(Path(repo_root) / ".ciu.hosts.toml")
    candidates.append(Path.home() / MACHINE_DIR / "hosts.toml")

    for path in candidates:
        if path.exists():
            with path.open("rb") as fh:
                doc = tomllib.load(fh)
            # Support both [deploy.hosts.*] and top-level [hosts.*]
            hosts = doc.get("deploy", {}).get("hosts")
            if hosts is None:
                hosts = doc.get("hosts")
            return hosts if isinstance(hosts, dict) else {}
    return {}


def get_host(repo_root: Path, name: str, *, admin: bool = False) -> dict:
    """Return the config dict for a named host (merged with .admin if admin=True).

    Raises ValueError if the host or hosts file is missing.
    """
    hosts = load_hosts(repo_root)
    if not hosts:
        raise ValueError(
            f"[SPEC J] No hosts file found. Create <repo>/.ciu.hosts.toml or "
            f"~/.ciu/hosts.toml with [deploy.hosts.{name}] entries."
        )
    if name not in hosts:
        available = sorted(hosts.keys())
        raise ValueError(
            f"[SPEC J] Host '{name}' not found in the hosts inventory. "
            f"Available hosts: {available or '(none)'}"
        )

    host_cfg = dict(hosts[name])

    if admin:
        admin_cfg = host_cfg.pop("admin", None)
        if admin_cfg and isinstance(admin_cfg, dict):
            host_cfg.update(admin_cfg)
    else:
        # Remove admin sub-table from the base config to avoid confusion
        host_cfg.pop("admin", None)

    return host_cfg
