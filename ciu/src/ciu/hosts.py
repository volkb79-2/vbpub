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
from .secrets.directives import SecretSpec, parse_value

# S14.3a — the only directive kinds legal at host scope (S4.2 six kinds).
# Vault-dependent (ASK_VAULT, GEN_TO_VAULT), ephemeral (GEN_EPHEMERAL) and
# in-place-referenced (ASK_FILE) kinds are meaningless before a host is
# adopted, so they are refused here rather than silently mis-resolved.
HOST_SCOPE_KINDS = frozenset({"ASK_EXTERNAL", "GEN_LOCAL"})


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


def _parse_host_secrets(host_name: str, secrets_table: dict) -> dict[str, SecretSpec]:
    """Parse + validate one host's [deploy.hosts.<host>.secrets] subtable (S14.3a).

    Each entry is parsed with the EXISTING ``directives.parse_value`` (never
    reimplemented) and only ``HOST_SCOPE_KINDS`` (ASK_EXTERNAL, GEN_LOCAL) are
    accepted. Any other directive — or any grammar violation — raises a tagged
    ``[S14.3a]`` error naming host, entry and the reason.
    """
    specs: dict[str, SecretSpec] = {}
    for entry_name, raw_value in secrets_table.items():
        try:
            spec = parse_value(entry_name, raw_value, f"deploy.hosts.{host_name}.secrets")
        except ValueError:
            # Never interpolate the upstream message: parse_value's grammar
            # errors echo the raw token back verbatim (e.g. "[S4.2] Unknown
            # directive '<token>'" where <token> IS the pasted secret value
            # when an operator writes a value instead of a directive). That
            # message must never reach stderr — see cli.py's `[ERROR] {exc}`
            # printers and every get_host() caller (S14.3a / P11-B1).
            raise ValueError(
                f"[S14.3a] host '{host_name}', entry '{entry_name}': not a "
                f"recognized secret directive — value not shown"
            ) from None
        if spec.kind not in HOST_SCOPE_KINDS:
            raise ValueError(
                f"[S14.3a] host '{host_name}', entry '{entry_name}': directive "
                f"'{spec.kind}' is not allowed at host scope; only ASK_EXTERNAL "
                f"and GEN_LOCAL resolve before a host is adopted"
            )
        specs[entry_name] = spec
    return specs


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

    # S14.3a — host-scoped secret directives are validated here (so a malformed
    # table aborts any flow that touches the host) but are POPPED before return:
    # a caller asking for connection facts never receives secret directives.
    if "secrets" in host_cfg:
        _parse_host_secrets(name, host_cfg.pop("secrets"))

    return host_cfg


def get_host_secrets(repo_root: Path, name: str) -> dict[str, SecretSpec]:
    """Return the host's parsed, validated secret directives (S14.3a).

    ``{}`` when the host declares no ``[deploy.hosts.<name>.secrets>`` table.
    Raises ValueError (same contract as ``get_host``) when the hosts file or
    the host is missing, or a tagged ``[S14.3a]`` error on a bad entry.
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
    host_data = hosts[name]
    if not isinstance(host_data, dict) or not isinstance(host_data.get("secrets"), dict):
        return {}
    return _parse_host_secrets(name, host_data["secrets"])
