#!/usr/bin/env python3
"""CIU provisioning: requires/provides declarative dependency graph for stacks.

Handles grammar validation of typed refs, graph linting (missing providers,
cycles), and live probing of each ref kind.

This module is strictly optional/additive: stacks without requires/provides
are not affected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ProvisioningRef:
    kind: str        # 'vault', 'pg', 'minio', 'consul', 'stack'
    subkind: str     # 'secret', 'role', 'db', 'schema', 'user', 'token', or '' for stack
    selector: str    # the path/name/etc


@dataclass
class ProbeResult:
    ref: str         # original ref string
    satisfied: bool
    reason: str      # human message


# Regex patterns for each ref kind
_VAULT_RE = re.compile(r'^vault:secret/(.+)$')
_PG_RE = re.compile(r'^pg:(role|db|schema)/([a-zA-Z0-9_-]+)$')
_MINIO_RE = re.compile(r'^minio:user/([a-zA-Z0-9_-]+)$')
_CONSUL_RE = re.compile(r'^consul:token/([a-zA-Z0-9_-]+)$')
_STACK_RE = re.compile(r'^stack:([a-zA-Z0-9_/-]+):healthy$')

VALID_REF_KINDS = frozenset({"vault", "pg", "minio", "consul", "stack"})


def parse_ref(ref: str) -> ProvisioningRef:
    """Parse a typed provisioning ref string into a ProvisioningRef.

    Raises ValueError with clear message on malformed refs.
    """
    m = _VAULT_RE.match(ref)
    if m:
        return ProvisioningRef(kind='vault', subkind='secret', selector=m.group(1))

    m = _PG_RE.match(ref)
    if m:
        return ProvisioningRef(kind='pg', subkind=m.group(1), selector=m.group(2))

    m = _MINIO_RE.match(ref)
    if m:
        return ProvisioningRef(kind='minio', subkind='user', selector=m.group(1))

    m = _CONSUL_RE.match(ref)
    if m:
        return ProvisioningRef(kind='consul', subkind='token', selector=m.group(1))

    m = _STACK_RE.match(ref)
    if m:
        return ProvisioningRef(kind='stack', subkind='', selector=m.group(1))

    # Give useful error messages
    if ':' not in ref:
        raise ValueError(
            f"[ERROR] Malformed provisioning ref {ref!r}: missing kind prefix "
            f"(expected <kind>:<selector>, e.g. vault:secret/path or pg:role/name)"
        )
    kind = ref.split(':', 1)[0]
    if kind not in VALID_REF_KINDS:
        raise ValueError(
            f"[ERROR] Unknown ref kind {kind!r} in {ref!r}. "
            f"Valid kinds: {', '.join(sorted(VALID_REF_KINDS))}"
        )
    raise ValueError(
        f"[ERROR] Malformed provisioning ref {ref!r}: does not match any valid pattern. "
        f"Examples: vault:secret/db/pass, pg:role/myuser, pg:db/mydb, pg:schema/myschema, "
        f"minio:user/worker, consul:token/myapp, stack:db-core:healthy"
    )


def lint_graph(
    stacks: dict[str, dict]  # {stack_path: {"requires": [...], "provides": [...]}}
) -> list[str]:
    """Lint the provides/requires graph across all stacks.

    Returns a list of error messages for:
    - refs that nobody provides (but some stack requires)
    - dependency cycles in the stack graph (via stack:X:healthy refs)
    """
    errors: list[str] = []

    # Build a set of all provided refs
    all_provided: set[str] = set()
    for stack_path, stack_info in stacks.items():
        for ref in stack_info.get("provides", []):
            all_provided.add(ref)

    # Check that every required ref is provided
    for stack_path, stack_info in stacks.items():
        for ref in stack_info.get("requires", []):
            if ref not in all_provided:
                errors.append(
                    f"[ERROR] Stack '{stack_path}' requires '{ref}' but nobody provides it"
                )

    # Build stack-level dependency graph from stack:X:healthy refs
    # stack A depends on stack B if A requires stack:B:healthy
    stack_deps: dict[str, set[str]] = {sp: set() for sp in stacks}

    for stack_path, stack_info in stacks.items():
        for ref in stack_info.get("requires", []):
            m = _STACK_RE.match(ref)
            if m:
                dep_stack = m.group(1)
                stack_deps[stack_path].add(dep_stack)

    # Cycle detection using DFS
    # We only detect cycles among the known stacks
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {sp: WHITE for sp in stacks}

    def dfs(node: str, path: list[str]) -> Optional[list[str]]:
        color[node] = GRAY
        path = path + [node]
        for dep in stack_deps.get(node, set()):
            if dep not in color:
                continue  # dep not in our known stacks - skip
            if color[dep] == GRAY:
                # Found a cycle
                cycle_start = path.index(dep)
                return path[cycle_start:] + [dep]
            if color[dep] == WHITE:
                result = dfs(dep, path)
                if result is not None:
                    return result
        color[node] = BLACK
        return None

    reported_cycles: set[str] = set()
    for node in list(stacks.keys()):
        if color[node] == WHITE:
            cycle = dfs(node, [])
            if cycle is not None:
                # Normalize cycle representation to avoid duplicate reports
                cycle_key = "->".join(sorted(cycle))
                if cycle_key not in reported_cycles:
                    reported_cycles.add(cycle_key)
                    cycle_str = " -> ".join(cycle)
                    errors.append(
                        f"[ERROR] Dependency cycle detected: {cycle_str}"
                    )

    return errors


def probe_ref(
    ref: str,
    config: dict,          # merged global config (for vault addr/token)
    repo_root: Path,
    *,
    docker_exec_fn=None,   # injectable for testing: fn(container, cmd) -> (rc, stdout)
    vault_client=None,     # injectable VaultKV2 instance for testing
) -> ProbeResult:
    """Probe live state for a single provisioning ref.

    Injectable dependencies allow full unit testing without Docker/Vault.
    """
    try:
        parsed = parse_ref(ref)
    except ValueError as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=str(exc))

    if parsed.kind == 'vault':
        return _probe_vault(ref, parsed, config, repo_root, vault_client=vault_client)
    elif parsed.kind == 'pg':
        return _probe_pg(ref, parsed, config, docker_exec_fn=docker_exec_fn)
    elif parsed.kind == 'minio':
        return _probe_minio(ref, parsed, config, docker_exec_fn=docker_exec_fn)
    elif parsed.kind == 'consul':
        return _probe_consul(ref, parsed, config, repo_root, vault_client=vault_client)
    elif parsed.kind == 'stack':
        return _probe_stack(ref, parsed, config, docker_exec_fn=docker_exec_fn)
    else:
        return ProbeResult(ref=ref, satisfied=False, reason=f"Unknown kind: {parsed.kind}")


def _probe_vault(ref, parsed, config, repo_root, *, vault_client=None) -> ProbeResult:
    """Probe a vault:secret/<path> ref."""
    if vault_client is None:
        from ciu.secrets.providers import VaultKV2, VaultError, vault_addr_from_config, resolve_vault_token
        try:
            addr = vault_addr_from_config(config)
            token = resolve_vault_token(config, repo_root)
            if not token:
                return ProbeResult(ref=ref, satisfied=False, reason="No Vault token available")
            vault_client = VaultKV2(addr, token)
        except VaultError as exc:
            return ProbeResult(ref=ref, satisfied=False, reason=str(exc))

    try:
        result = vault_client.read(parsed.selector)
        if result is not None:
            return ProbeResult(ref=ref, satisfied=True, reason=f"Vault secret exists at {parsed.selector!r}")
        return ProbeResult(ref=ref, satisfied=False, reason=f"Vault secret not found at {parsed.selector!r}")
    except Exception as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=f"Vault read error: {exc}")


def _probe_pg(ref, parsed, config, *, docker_exec_fn=None) -> ProbeResult:
    """Probe a pg:role/<name> or pg:db/<name> ref via docker exec psql."""
    from ciu import procutil

    # Derive container name (try 'postgres' as the service name)
    try:
        from ciu.deploy import container_name as _container_name
        cname = _container_name(config, 'postgres')
    except (ValueError, KeyError):
        cname = 'postgres'

    cmd = ['psql', '-U', 'postgres', '-tAc']
    if parsed.subkind == 'role':
        # pg_roles is a cluster-global catalog — the default 'postgres' db is fine.
        sql = f"SELECT 1 FROM pg_roles WHERE rolname='{parsed.selector}'"
    elif parsed.subkind == 'schema':
        # information_schema.schemata is PER-DATABASE, so target the app database
        # (registry.postgresql.database) rather than the default 'postgres' db.
        sql = f"SELECT 1 FROM information_schema.schemata WHERE schema_name='{parsed.selector}'"
        db_name = (config.get('registry', {}) or {}).get('postgresql', {}).get('database')
        if db_name:
            cmd = ['psql', '-U', 'postgres', '-d', str(db_name), '-tAc']
    else:  # db
        sql = f"SELECT 1 FROM pg_database WHERE datname='{parsed.selector}'"
    cmd = cmd + [sql]

    if docker_exec_fn is not None:
        rc, stdout = docker_exec_fn(cname, cmd)
    else:
        try:
            result = procutil.docker(['exec', cname] + cmd, check=False)
            rc = result.returncode
            stdout = result.stdout or ''
        except FileNotFoundError as exc:
            return ProbeResult(ref=ref, satisfied=False, reason=f"docker not available: {exc}")

    if rc == 0 and '1' in stdout:
        return ProbeResult(ref=ref, satisfied=True, reason=f"pg {parsed.subkind} '{parsed.selector}' exists")
    return ProbeResult(ref=ref, satisfied=False, reason=f"pg {parsed.subkind} '{parsed.selector}' not found (rc={rc})")


def _probe_minio(ref, parsed, config, *, docker_exec_fn=None) -> ProbeResult:
    """Probe a minio:user/<name> ref via docker exec mc."""
    from ciu import procutil

    try:
        from ciu.deploy import container_name as _container_name
        cname = _container_name(config, 'minio')
    except (ValueError, KeyError):
        cname = 'minio'

    cmd = ['mc', 'admin', 'user', 'info', 'local', parsed.selector]

    if docker_exec_fn is not None:
        rc, stdout = docker_exec_fn(cname, cmd)
    else:
        try:
            result = procutil.docker(['exec', cname] + cmd, check=False)
            rc = result.returncode
            stdout = result.stdout or ''
        except FileNotFoundError as exc:
            return ProbeResult(ref=ref, satisfied=False, reason=f"docker not available: {exc}")

    if rc == 0:
        return ProbeResult(ref=ref, satisfied=True, reason=f"MinIO user '{parsed.selector}' exists")
    return ProbeResult(ref=ref, satisfied=False, reason=f"MinIO user '{parsed.selector}' not found (rc={rc})")


def _probe_consul(ref, parsed, config, repo_root, *, vault_client=None) -> ProbeResult:
    """Probe a consul:token/<svc> ref via a Vault read.

    The Vault path is config-driven so deployments that store ACL tokens under a
    different layout can point ciu at it. Default: ``consul/acl/tokens/{svc}``.
    Example override (dstdns stores tokens at ``consul/<svc>/token``)::

        [registry.consul]
        token_vault_path = "consul/{svc}/token"
    """
    consul_cfg = (config.get("registry", {}) or {}).get("consul", {}) or {}
    template = consul_cfg.get("token_vault_path", "consul/acl/tokens/{svc}")
    try:
        vault_path = template.format(svc=parsed.selector)
    except (KeyError, IndexError):
        vault_path = f"consul/acl/tokens/{parsed.selector}"
    vault_ref_obj = ProvisioningRef(kind='vault', subkind='secret', selector=vault_path)
    return _probe_vault(ref, vault_ref_obj, config, repo_root, vault_client=vault_client)


def _probe_stack(ref, parsed, config, *, docker_exec_fn=None) -> ProbeResult:
    """Probe a stack:<name>:healthy ref via docker inspect."""
    import json
    from ciu import procutil

    try:
        from ciu.deploy import container_name as _container_name
        # The stack name is used as the service name for container lookup
        cname = _container_name(config, parsed.selector)
    except (ValueError, KeyError):
        cname = parsed.selector

    if docker_exec_fn is not None:
        rc, stdout = docker_exec_fn(cname, ['inspect'])
        # Interpret output as health status
        if rc == 0 and 'healthy' in stdout.lower():
            return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' is healthy")
        return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' not healthy")

    # Use docker inspect directly
    try:
        result = procutil.docker(
            ['inspect', '--format', '{{json .State}}', cname], check=False
        )
    except FileNotFoundError as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=f"docker not available: {exc}")

    if result.returncode != 0:
        return ProbeResult(ref=ref, satisfied=False, reason=f"Container '{cname}' not found")

    out = (result.stdout or '').strip()
    if not out:
        return ProbeResult(ref=ref, satisfied=False, reason=f"No state for container '{cname}'")

    try:
        state = json.loads(out)
    except json.JSONDecodeError:
        return ProbeResult(ref=ref, satisfied=False, reason=f"Could not parse container state for '{cname}'")

    health = state.get('Health', {}) or {}
    status = health.get('Status', '') if isinstance(health, dict) else ''
    if status == 'healthy':
        return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' container is healthy")
    if not status:
        # No healthcheck configured
        running = state.get('Running', False)
        if running:
            return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' is running (no healthcheck)")
        # One-shot stacks (e.g. db-init) exit 0 when they finish successfully —
        # treat a clean exit as satisfied rather than "not running".
        exit_code = state.get('ExitCode')
        if exit_code == 0:
            return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' completed (one-shot, exited 0)")
        return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' is not running (exit code {exit_code})")
    return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' health status: {status}")
