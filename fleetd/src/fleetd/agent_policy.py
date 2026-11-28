"""Host-owned identity and request allow-list policy for the fleetd agent."""

from __future__ import annotations

import math
import re
import socket
import struct
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .admission import AdmissionError, ExitCode, GROUP_RE
from .contracts import ContractError

DEFAULT_AGENT_POLICY_PATH = Path("/etc/fleetd/agent.toml")
_REPO_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,40}\Z")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class AgentIdentity:
    """Identity asserted by a trusted transport boundary."""

    principal: str
    group: str
    transport: str
    uid: int | None = None
    gid: int | None = None


@dataclass(frozen=True)
class AgentPolicy:
    idle_connection_s: float
    query_timeout_s: float
    legacy_jobs_dir: Path
    lease_reconcile_interval_s: float
    lease_bind_timeout_s: float
    max_connections: int
    runner_user: str | None
    identities: tuple[AgentIdentity, ...]
    repo_roots: Mapping[str, Path]
    requested_by_clients: frozenset[str]
    extra_argv: frozenset[tuple[str, ...]]
    dev_argv: frozenset[tuple[str, ...]]
    env_values: Mapping[str, frozenset[str]]
    executor_enabled: bool = False

    def resolve_peer(self, uid: int, gid: int) -> AgentIdentity:
        """Map Linux SO_PEERCRED's UID and primary GID to one identity."""
        for identity in self.identities:
            if identity.transport == "peer" and identity.uid == uid and identity.gid == gid:
                return identity
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unknown local socket peer UID/GID")

    def resolve_socket_peer(self, peer: socket.socket) -> AgentIdentity:
        """Read SO_PEERCRED and map the peer's UID and primary GID exactly."""
        try:
            credential_size = struct.calcsize("3i")
            raw = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, credential_size)
            if len(raw) != credential_size:
                raise OSError("SO_PEERCRED returned a truncated credential")
            _pid, uid, gid = struct.unpack("3i", raw)
        except (AttributeError, OSError, struct.error) as exc:
            raise AdmissionError(
                ExitCode.IDENTITY_OR_TRANSPORT, f"cannot authenticate local socket peer: {exc}",
            ) from exc
        return self.resolve_peer(uid, gid)

    def resolve_forced_group(self, group: str) -> AgentIdentity:
        """Resolve the group fixed by a forced-command entry, never request data."""
        for identity in self.identities:
            if identity.transport == "forced-command" and identity.group == group:
                return identity
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unknown forced-command group")


def _object(
    value: object,
    where: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected table")
    optional_keys = set() if optional is None else optional
    extra = sorted(set(value) - required - optional_keys)
    missing = sorted(required - set(value))
    if extra:
        raise ContractError(f"{where}: unknown key(s): {', '.join(extra)}")
    if missing:
        raise ContractError(f"{where}: missing key(s): {', '.join(missing)}")
    return value


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ContractError(f"{where}: expected non-empty string")
    return value


def _safe_name(value: object, where: str) -> str:
    result = _string(value, where)
    if len(result) > 128 or GROUP_RE.fullmatch(result) is None:
        raise ContractError(f"{where}: expected a safe identifier")
    return result


def _positive_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where}: expected finite positive number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ContractError(f"{where}: expected finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ContractError(f"{where}: expected finite positive number")
    return result


def _positive_integer(value: object, where: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError(f"{where}: expected positive integer")
    return value


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{where}: expected non-negative integer")
    return value


def _absolute_path(value: object, where: str) -> Path:
    raw = _string(value, where)
    path = PurePosixPath(raw)
    if not path.is_absolute() or ".." in path.parts or raw == "/":
        raise ContractError(f"{where}: expected an absolute path without '..' components")
    return Path(raw)


def _string_set(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array of strings")
    values = tuple(_string(item, f"{where}[{index}]") for index, item in enumerate(value))
    if len(set(values)) != len(values):
        raise ContractError(f"{where}: duplicate value")
    return frozenset(values)


def _argv_set(value: object, where: str) -> frozenset[tuple[str, ...]]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array of argv arrays")
    result: set[tuple[str, ...]] = set()
    for index, argv in enumerate(value):
        if not isinstance(argv, list) or not argv:
            raise ContractError(f"{where}[{index}]: expected non-empty argv array")
        item = tuple(_string(token, f"{where}[{index}]") for token in argv)
        if item in result:
            raise ContractError(f"{where}: duplicate argv entry")
        result.add(item)
    return frozenset(result)


def _identities(value: object) -> tuple[AgentIdentity, ...]:
    if not isinstance(value, list):
        raise ContractError("agent.toml.identities: expected array of tables")
    result: list[AgentIdentity] = []
    peer_pairs: set[tuple[int, int]] = set()
    seen_groups: set[str] = set()
    for index, raw in enumerate(value):
        where = f"agent.toml.identities[{index}]"
        if not isinstance(raw, dict):
            raise ContractError(f"{where}: expected table")
        transport = raw.get("transport")
        if transport == "peer":
            row = _object(raw, where, {"transport", "principal", "group", "uid", "gid"})
            uid = _nonnegative_int(row["uid"], f"{where}.uid")
            gid = _nonnegative_int(row["gid"], f"{where}.gid")
            pair = (uid, gid)
            if pair in peer_pairs:
                raise ContractError(f"{where}: duplicate peer UID/GID pair")
            peer_pairs.add(pair)
        elif transport == "forced-command":
            row = _object(raw, where, {"transport", "principal", "group"})
            uid = None
            gid = None
        else:
            raise ContractError(f"{where}.transport: expected 'peer' or 'forced-command'")
        principal = _safe_name(row["principal"], f"{where}.principal")
        group = _safe_name(row["group"], f"{where}.group")
        if group in seen_groups:
            raise ContractError(f"{where}.group: duplicate authenticated group {group!r}")
        seen_groups.add(group)
        result.append(AgentIdentity(principal, group, transport, uid, gid))

    groups = sorted(seen_groups)
    for index, group in enumerate(groups):
        for other in groups[index + 1:]:
            if group.startswith(other + "-") or other.startswith(group + "-"):
                raise ContractError(
                    f"agent.toml.identities: overlapping authenticated group prefixes {group!r} and {other!r}"
                )
    return tuple(result)


def load_agent_policy(path: Path = DEFAULT_AGENT_POLICY_PATH) -> AgentPolicy:
    """Read the closed, host-owned agent and request validation policy."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"agent policy {path}: cannot read required config ({exc.strerror or 'I/O error'})") from exc
    except UnicodeError as exc:
        raise ContractError(f"agent policy {path}: config is not UTF-8 text") from exc
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"agent policy {path}: invalid TOML: {exc}") from exc

    policy_keys = {
        "schema_version", "idle_connection_s", "query_timeout_s", "identities", "repo_roots", "requested_by_clients",
        "extra_argv", "dev_argv", "env_values", "lease_reconcile_interval_s", "lease_bind_timeout_s",
        "max_connections", "legacy_jobs_dir",
    }
    root = _object(parsed, "agent.toml", policy_keys, {"runner_user", "executor_enabled"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ContractError("agent.toml.schema_version: expected integer 1")
    idle_connection_s = _positive_number(root["idle_connection_s"], "agent.toml.idle_connection_s")
    query_timeout_s = _positive_number(root["query_timeout_s"], "agent.toml.query_timeout_s")
    legacy_jobs_dir = _absolute_path(root["legacy_jobs_dir"], "agent.toml.legacy_jobs_dir")
    lease_reconcile_interval_s = _positive_number(
        root["lease_reconcile_interval_s"], "agent.toml.lease_reconcile_interval_s",
    )
    lease_bind_timeout_s = _positive_number(
        root["lease_bind_timeout_s"], "agent.toml.lease_bind_timeout_s",
    )
    max_connections = _positive_integer(root["max_connections"], "agent.toml.max_connections")
    executor_enabled = root.get("executor_enabled", False)
    if type(executor_enabled) is not bool:
        raise ContractError("agent.toml.executor_enabled: expected boolean")
    runner_user = (
        _safe_name(root["runner_user"], "agent.toml.runner_user")
        if "runner_user" in root else None
    )
    if executor_enabled and runner_user is None:
        raise ContractError("agent.toml: missing key(s): runner_user (required when executor_enabled is true)")
    identities = _identities(root["identities"])

    raw_roots = root["repo_roots"]
    if not isinstance(raw_roots, dict):
        raise ContractError("agent.toml.repo_roots: expected table")
    repo_roots: dict[str, Path] = {}
    for name, value in raw_roots.items():
        if not isinstance(name, str) or _REPO_NAME_RE.fullmatch(name) is None or name.endswith(".git"):
            raise ContractError(f"agent.toml.repo_roots: invalid repo name {name!r}")
        repo_roots[name] = _absolute_path(value, f"agent.toml.repo_roots.{name}")

    requested_by_clients = _string_set(root["requested_by_clients"], "agent.toml.requested_by_clients")
    extra_argv = _argv_set(root["extra_argv"], "agent.toml.extra_argv")
    dev_argv = _argv_set(root["dev_argv"], "agent.toml.dev_argv")
    raw_env = root["env_values"]
    if not isinstance(raw_env, dict):
        raise ContractError("agent.toml.env_values: expected table")
    env_values: dict[str, frozenset[str]] = {}
    for name, values in raw_env.items():
        if not isinstance(name, str) or _ENV_NAME_RE.fullmatch(name) is None:
            raise ContractError(f"agent.toml.env_values: invalid environment name {name!r}")
        env_values[name] = _string_set(values, f"agent.toml.env_values.{name}")

    return AgentPolicy(
        idle_connection_s, query_timeout_s, legacy_jobs_dir, lease_reconcile_interval_s, lease_bind_timeout_s,
        max_connections, runner_user, identities, repo_roots, requested_by_clients,
        extra_argv, dev_argv, env_values, executor_enabled,
    )
