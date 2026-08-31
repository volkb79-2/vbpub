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
    #: CIU-68 — "not satisfied YET, but on track": a normal deployment
    #: reaches the satisfied state from here without anyone doing anything.
    #: Only the caller's bounded poll reads this; it defaults False so every
    #: existing construction stays fail-promptly, which is correct for the
    #: conditions that will never resolve on their own (container absent,
    #: unhealthy, exited non-zero, docker unavailable, unparseable state).
    #: Distinguishing the two is the whole point: a one-shot probe treated a
    #: dependency reported `starting` identically to one that will never
    #: satisfy.
    retryable: bool = False


# Regex patterns for each ref kind
_VAULT_RE = re.compile(r'^vault:secret/(.+)$')
_PG_RE = re.compile(r'^pg:(role|db|schema)/([a-zA-Z0-9_-]+)$')
_MINIO_RE = re.compile(r'^minio:user/([a-zA-Z0-9_-]+)$')
_CONSUL_RE = re.compile(r'^consul:token/([a-zA-Z0-9_-]+)$')
_STACK_RE = re.compile(r'^stack:([a-zA-Z0-9_/-]+):(healthy|completed)$')

VALID_REF_KINDS = frozenset({"vault", "pg", "minio", "consul", "stack"})

#: Vault path template a ``consul:token/<svc>`` probe uses when
#: ``[registry.consul].token_vault_path`` is not declared. Named once here
#: because :func:`_probe_consul` reads it twice (declared value, then the
#: fallback after a failed substitution) and S13.4b's validator, CONFIG.md and
#: SPEC.md all quote it — a second literal would be a place for them to drift.
CONSUL_TOKEN_VAULT_PATH_DEFAULT = "consul/acl/tokens/{svc}"


def parse_ref(ref: str) -> ProvisioningRef:
    """Parse a typed provisioning ref string into a ProvisioningRef.

    Raises ValueError with clear message on malformed refs.
    """
    # A provisioning ref is an identifier, not a prefix.  ``match`` combined
    # with ``$`` accepts a final newline, which lets malformed rendered input
    # bypass this parser even though configuration validation rejects it.
    m = _VAULT_RE.fullmatch(ref)
    if m:
        return ProvisioningRef(kind='vault', subkind='secret', selector=m.group(1))

    m = _PG_RE.fullmatch(ref)
    if m:
        return ProvisioningRef(kind='pg', subkind=m.group(1), selector=m.group(2))

    m = _MINIO_RE.fullmatch(ref)
    if m:
        return ProvisioningRef(kind='minio', subkind='user', selector=m.group(1))

    m = _CONSUL_RE.fullmatch(ref)
    if m:
        return ProvisioningRef(kind='consul', subkind='token', selector=m.group(1))

    m = _STACK_RE.fullmatch(ref)
    if m:
        # subkind stays '' for the original ':healthy' terminal — additive,
        # byte-identical to every ':healthy' ref parsed before this ever
        # existed (O1, V8-PREP-5) — and becomes 'completed' for the new
        # exit-0-based terminal so `_probe_stack` can dispatch on it.
        terminal = m.group(2)
        subkind = '' if terminal == 'healthy' else terminal
        return ProvisioningRef(kind='stack', subkind=subkind, selector=m.group(1))

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
        f"minio:user/worker, consul:token/myapp, stack:db-core:healthy, "
        f"stack:db-core:completed"
    )


def _resolve_declared_stack_path(selector: str, known_paths) -> Optional[str]:
    """Resolve a ``stack:<selector>:healthy|completed`` ref's selector to the
    declared stack path it refers to, out of *known_paths* (V8-PREP-5,
    shared by O4 and O5 — the two places a ref's selector must be matched
    against the known set of declared stack paths).

    An EXACT match wins outright — this covers a genuine full repo-relative
    path selector (e.g. ``infra/db-init``) and a top-level bare stack with no
    ``/`` in its declared path at all. Otherwise, when *selector* equals the
    FINAL path segment of exactly one entry in *known_paths* (e.g. bare
    ``db-init`` against a declared ``infra/db-init``), that entry resolves —
    this is the only selector form that ever worked for container-name
    resolution before this package (``container_name`` only ever sees a bare
    service name), so it must also be the form ``lint_graph``'s cycle
    detection recognizes.

    An AMBIGUOUS basename (two or more known paths share it) or a selector
    matching no known path at all returns ``None``: the caller MUST leave
    that ref exactly as unresolved as it is today — this fix must never
    manufacture a false edge, or a container name, out of a selector that
    does not actually correspond to a known stack.
    """
    if selector in known_paths:
        return selector
    matches = [p for p in known_paths if p.rsplit("/", 1)[-1] == selector]
    if len(matches) == 1:
        return matches[0]
    return None


def lint_graph(
    stacks: dict[str, dict]  # {stack_path: {"requires": [...], "provides": [...]}}
) -> list[str]:
    """Lint the provides/requires graph across all stacks.

    Returns a list of error messages for:
    - refs that nobody provides (but some stack requires) -- except a
      `stack:<path>:healthy|completed` ref, which is satisfied by the
      referenced stack resolving via `_resolve_declared_stack_path`, not by
      any `provides` declaration (CIU-63)
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
            if ref in all_provided:
                continue
            # A `stack:<path>:healthy|completed` ref is never satisfied by a
            # `provides` declaration -- the live probe (`_probe_stack`) never
            # reads one, it resolves the referenced stack's own existence
            # instead (docker-inspect on that stack's container). This static
            # pass must recognize the ref the same way the cycle-detection
            # pass below already does: match `_STACK_RE` and resolve the
            # selector through `_resolve_declared_stack_path` against the
            # known set of declared stack paths. A resolvable stack:* ref is
            # therefore satisfied here WITHOUT requiring any stack to
            # redundantly self-declare `provides = ["stack:X:..."]` (CIU-63)
            # -- every other ref kind keeps today's exact provides-union
            # check, unchanged.
            m = _STACK_RE.fullmatch(ref)
            if m and _resolve_declared_stack_path(m.group(1), stacks.keys()) is not None:
                continue
            errors.append(
                f"[ERROR] Stack '{stack_path}' requires '{ref}' but nobody provides it"
            )

    # Build stack-level dependency graph from stack:X:healthy|completed refs.
    # stack A depends on stack B if A requires stack:B:healthy|completed.
    stack_deps: dict[str, set[str]] = {sp: set() for sp in stacks}

    for stack_path, stack_info in stacks.items():
        for ref in stack_info.get("requires", []):
            m = _STACK_RE.match(ref)
            if m:
                dep_stack = m.group(1)
                # O5 (V8-PREP-5): `stacks` is keyed by full repo-relative
                # path, but the only selector form that has ever resolved to
                # a real container is a bare basename — so a ref written the
                # only way that actually works today (`stack:db-init:...`
                # against a stack declared at `infra/db-init`) previously
                # never matched a `stack_deps` key and was silently dropped
                # by the WHITE/GRAY/BLACK walk below (`dep not in color`).
                # Resolve it to the declared path first so the edge lands on
                # a real graph node.
                resolved = _resolve_declared_stack_path(dep_stack, stacks.keys())
                stack_deps[stack_path].add(resolved if resolved is not None else dep_stack)

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
                # A GRAY dep is a back-edge to a node on the CURRENT recursion
                # path — a cycle. This invariant only holds because every return
                # below marks the node BLACK; without that, a node left GRAY by
                # an earlier aborted (cycle-returning) DFS tree would be misread
                # here as a back-edge and `path.index(dep)` would raise
                # "x not in list" for a dep that is not on this path.
                cycle_start = path.index(dep)
                color[node] = BLACK
                return path[cycle_start:] + [dep]
            if color[dep] == WHITE:
                result = dfs(dep, path)
                if result is not None:
                    color[node] = BLACK
                    return result
        color[node] = BLACK
        return None

    for node in list(stacks.keys()):
        if color[node] == WHITE:
            cycle = dfs(node, [])
            if cycle is not None:
                # ``dfs`` marks every node on its returning path BLACK, so no
                # later root can rediscover this same cycle.
                cycle_str = " -> ".join(cycle)
                errors.append(f"[ERROR] Dependency cycle detected: {cycle_str}")

    return errors


def provider_index(stacks: dict[str, dict]) -> dict[str, list[str]]:
    """``ref -> [stack_paths that declare it in ``provides``]``, paths sorted.

    *stacks* is the same ``{path: {"requires": [...], "provides": [...]}}``
    shape :func:`lint_graph` consumes. Shared by :func:`render_graph` (which
    draws the consumer --ref--> provider edge) and CIU-70's probe container
    resolution (which needs the provider of the ref being probed) so the two
    can never disagree about who provides what.
    """
    providers: dict[str, list[str]] = {}
    for sp in sorted(stacks):
        for ref in stacks[sp].get("provides", []):
            providers.setdefault(ref, []).append(sp)
    return providers


def render_graph(stacks: dict[str, dict], fmt: str = "mermaid") -> str:
    """Render the requires/provides dependency graph for visualisation.

    *stacks* is the same ``{path: {"requires": [...], "provides": [...]}}`` shape
    used by :func:`lint_graph`. Edges go consumer --ref--> provider (the stack
    whose ``provides`` contains that ref); a require nobody provides is drawn as a
    dashed edge to an ``UNPROVIDED`` sentinel so gaps are obvious.

    fmt: ``mermaid`` (default; pastes into Markdown/docs), ``dot`` (Graphviz), or
    ``json`` (raw nodes+edges for external tooling).
    """
    # provider index: ref -> [stack_paths that provide it]
    providers = provider_index(stacks)

    # group edges by (consumer, provider|None) -> [refs]; provider None = unprovided
    grouped: dict[tuple[str, Optional[str]], list[str]] = {}
    raw_edges: list[tuple[str, Optional[str], str]] = []
    for sp in sorted(stacks):
        for ref in stacks[sp].get("requires", []):
            provs = providers.get(ref) or [None]
            for p in provs:
                grouped.setdefault((sp, p), []).append(ref)
                raw_edges.append((sp, p, ref))

    if fmt == "json":
        import json
        return json.dumps(
            {
                "stacks": {k: stacks[k] for k in sorted(stacks)},
                "edges": [
                    {"from": c, "to": p, "ref": r, "provided": p is not None}
                    for c, p, r in raw_edges
                ],
            },
            indent=2,
        )

    nodes = sorted(stacks)
    nid = {n: f"n{i}" for i, n in enumerate(nodes)}
    has_unprovided = any(p is None for (_, p) in grouped)

    if fmt == "dot":
        lines = ["digraph ciu_provisioning {", "  rankdir=LR;", "  node [shape=box];"]
        for n in nodes:
            lines.append(f'  "{n}";')
        if has_unprovided:
            lines.append('  "UNPROVIDED" [color=red, fontcolor=red, style=dashed];')
        for (c, p), refs in grouped.items():
            label = ", ".join(refs)
            if p is None:
                lines.append(f'  "{c}" -> "UNPROVIDED" [label="{label}", color=red, style=dashed];')
            else:
                lines.append(f'  "{c}" -> "{p}" [label="{label}"];')
        lines.append("}")
        return "\n".join(lines)

    # default: mermaid flowchart
    lines = ["flowchart LR"]
    for n in nodes:
        lines.append(f'  {nid[n]}["{n}"]')
    if has_unprovided:
        lines.append('  UNPROVIDED["⚠ UNPROVIDED"]')
    for (c, p), refs in grouped.items():
        label = "<br/>".join(refs)
        if p is None:
            lines.append(f'  {nid[c]} -.->|"{label}"| UNPROVIDED')
        else:
            lines.append(f'  {nid[c]} -->|"{label}"| {nid[p]}')
    return "\n".join(lines)


def probe_ref(
    ref: str,
    config: dict,          # merged global config (for vault addr/token)
    repo_root: Path,
    *,
    docker_exec_fn=None,   # injectable for testing: fn(container, cmd) -> (rc, stdout)
    vault_client=None,     # injectable VaultKV2 instance for testing
    stacks: Optional[dict[str, dict]] = None,
) -> ProbeResult:
    """Probe live state for a single provisioning ref.

    Injectable dependencies allow full unit testing without Docker/Vault.

    *stacks* is the requires/provides graph — the same
    ``{stack_path: {"requires": [...], "provides": [...]}}`` shape
    :func:`lint_graph` consumes. ``pg:`` and ``minio:`` probes resolve the
    container they ``docker exec`` into from the stack whose ``provides``
    carries *ref* (CIU-70); it MUST therefore cover every stack in the run,
    not just the ones whose ``requires`` are being probed right now — the
    provider of a cross-phase ref lives in an earlier phase by construction.
    ``None`` means "no graph was supplied": those two probe kinds then report
    genuine indeterminacy rather than falling back to a literal service-name
    guess (which is what CIU-70 exists to remove). Every other ref kind
    ignores it.
    """
    try:
        parsed = parse_ref(ref)
    except ValueError as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=str(exc))

    if parsed.kind == 'vault':
        return _probe_vault(ref, parsed, config, repo_root, vault_client=vault_client)
    elif parsed.kind == 'pg':
        return _probe_pg(ref, parsed, config, docker_exec_fn=docker_exec_fn, stacks=stacks)
    elif parsed.kind == 'minio':
        return _probe_minio(ref, parsed, config, docker_exec_fn=docker_exec_fn, stacks=stacks)
    elif parsed.kind == 'consul':
        return _probe_consul(ref, parsed, config, repo_root, vault_client=vault_client)
    return _probe_stack(ref, parsed, config, docker_exec_fn=docker_exec_fn)


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


def _resolve_probe_container(
    ref: str, config: dict, stacks: Optional[dict[str, dict]]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the container a ``pg:``/``minio:`` probe must exec into (CIU-70).

    Returns ``(container_name, None)`` on success or ``(None, reason)`` when it
    cannot be determined; exactly one element is ever non-``None``.

    The target is DERIVED, never guessed: the stack whose ``provides`` declares
    *ref* is the stack that owns it, and that stack's declared path resolves to
    a container through :func:`_stack_container_name` — the same
    declared-path → basename → ``container_name`` path a ``stack:<sel>:healthy``
    ref already uses. Before CIU-70 both probes hardcoded the LITERAL service
    keys ``postgres``/``minio``, which nothing in S13.2 or CONFIG.md ever
    required a consumer to use, so a service keyed ``pg``/``db``/
    ``postgres_primary`` was probed in a container that does not exist and the
    result was worded exactly like "the role is missing".

    Failure is loud in both directions (AGENTS.md "defaults are hazards"):
    nothing declaring *ref* is reported as such rather than silently falling
    back to a literal, and providers that resolve to genuinely DIFFERENT
    containers are refused rather than one being picked arbitrarily. Providers
    that all resolve to the SAME container name are not ambiguous *for this
    probe* and are accepted — two declared stack paths sharing a final segment
    collapse onto one container name, which is CIU-66's separate defect and
    is unchanged (neither made better nor worse) by this resolution.
    """
    if stacks is None:
        return None, (
            f"cannot resolve a container for '{ref}': the probe was given no "
            "requires/provides graph"
        )
    providers = provider_index(stacks).get(ref, [])
    if not providers:
        return None, f"no stack provides '{ref}' — cannot resolve a container to probe"
    names: list[str] = []
    for stack_path in providers:
        cname = _stack_container_name(config, stack_path)
        if cname not in names:
            names.append(cname)
    if len(names) > 1:
        return None, (
            f"'{ref}' is provided by {len(providers)} stacks "
            f"({', '.join(providers)}) resolving to different containers "
            f"({', '.join(names)}) — cannot choose one"
        )
    return names[0], None


def _docker_exec_probe(cname, cmd, docker_exec_fn) -> tuple[int, str, str]:
    """Run *cmd* inside *cname*, returning ``(rc, stdout, stderr)``.

    The injectable ``docker_exec_fn`` seam keeps its published 2-tuple
    ``(rc, stdout)`` contract unchanged — it reports an empty *stderr*, so a
    fake that wants to exercise :func:`_docker_level_failure` puts the docker
    error text in *stdout*, which is where a fake docker naturally has it.
    ``FileNotFoundError`` (no docker binary) propagates to the caller, which
    turns it into the pre-existing "docker not available" ProbeResult.
    """
    if docker_exec_fn is not None:
        rc, stdout = docker_exec_fn(cname, cmd)
        return rc, stdout or '', ''
    from ciu import procutil
    result = procutil.docker(['exec', cname] + cmd, check=False)
    return result.returncode, result.stdout or '', result.stderr or ''


def _docker_level_failure(stdout: str, stderr: str) -> Optional[str]:
    """Classify a non-zero ``docker exec`` as a DOCKER-level failure.

    Returns a short phrase when the command never ran because the target
    container is absent or stopped, or ``None`` when docker did run it and the
    non-zero status is the in-container command's OWN answer.

    This is the distinction CIU-70 calls out: "the container is not there" and
    "the role/user is not there" are different facts about the world, and
    collapsing them into one "not found" message is AGENTS.md's
    *absence-for-emptiness* anti-pattern — it makes a correct deployment with a
    differently-keyed service read as a missing role.

    KNOWN WEAKNESS, and why it is acceptable here: this matches the CLI's
    ENGLISH error text, so a future Docker wording change or a localized
    client stops it recognizing either phrase. That is a *fail-safe*
    degradation by construction, not a silent one — the only thing lost is the
    more specific phrasing, and the caller falls through to "could not be
    checked (rc=N)", which is still honest about not knowing. It can never
    invert into the dangerous direction (claiming "does not exist" for a
    container that was never reached), because that verdict is reachable only
    from ``rc == 0``, which a failed ``docker exec`` never returns. A stronger
    check would need a machine-readable signal ``docker exec`` does not
    expose, or a second round-trip (``docker inspect``) per probe.
    """
    blob = f"{stdout}\n{stderr}".lower()
    if "no such container" in blob:
        return "no such container"
    if "is not running" in blob:
        return "container is not running"
    return None


def _probe_pg(ref, parsed, config, *, docker_exec_fn=None, stacks=None) -> ProbeResult:
    """Probe a pg:role/<name>, pg:db/<name> or pg:schema/<name> ref via
    ``docker exec`` + ``psql`` in the container of the stack that PROVIDES the
    ref (CIU-70; see :func:`_resolve_probe_container`)."""
    cname, unresolved = _resolve_probe_container(ref, config, stacks)
    if unresolved is not None:
        return ProbeResult(ref=ref, satisfied=False, reason=unresolved)

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

    try:
        rc, stdout, stderr = _docker_exec_probe(cname, cmd, docker_exec_fn)
    except FileNotFoundError as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=f"docker not available: {exc}")

    if rc == 0:
        # `psql -tAc` exits 0 for a query that ran and matched NOTHING, so
        # rc == 0 is the only status from which "it genuinely does not exist"
        # can honestly be concluded (CIU-70 point 4).
        if '1' in stdout:
            return ProbeResult(ref=ref, satisfied=True, reason=f"pg {parsed.subkind} '{parsed.selector}' exists")
        return ProbeResult(
            ref=ref, satisfied=False,
            reason=f"pg {parsed.subkind} '{parsed.selector}' does not exist "
                   f"(query ran in '{cname}', no matching row)",
        )
    docker_failure = _docker_level_failure(stdout, stderr)
    if docker_failure is not None:
        return ProbeResult(
            ref=ref, satisfied=False,
            reason=f"container '{cname}' unavailable ({docker_failure}) — "
                   f"pg {parsed.subkind} '{parsed.selector}' was NOT checked",
        )
    return ProbeResult(
        ref=ref, satisfied=False,
        reason=f"pg {parsed.subkind} '{parsed.selector}' could not be checked: "
               f"psql in '{cname}' exited rc={rc}",
    )


def _probe_minio(ref, parsed, config, *, docker_exec_fn=None, stacks=None) -> ProbeResult:
    """Probe a minio:user/<name> ref via ``docker exec`` + ``mc`` in the
    container of the stack that PROVIDES the ref (CIU-70; see
    :func:`_resolve_probe_container`)."""
    cname, unresolved = _resolve_probe_container(ref, config, stacks)
    if unresolved is not None:
        return ProbeResult(ref=ref, satisfied=False, reason=unresolved)

    cmd = ['mc', 'admin', 'user', 'info', 'local', parsed.selector]

    try:
        rc, stdout, stderr = _docker_exec_probe(cname, cmd, docker_exec_fn)
    except FileNotFoundError as exc:
        return ProbeResult(ref=ref, satisfied=False, reason=f"docker not available: {exc}")

    if rc == 0:
        return ProbeResult(ref=ref, satisfied=True, reason=f"MinIO user '{parsed.selector}' exists")
    docker_failure = _docker_level_failure(stdout, stderr)
    if docker_failure is not None:
        return ProbeResult(
            ref=ref, satisfied=False,
            reason=f"container '{cname}' unavailable ({docker_failure}) — "
                   f"MinIO user '{parsed.selector}' was NOT checked",
        )
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
    template = consul_cfg.get("token_vault_path", CONSUL_TOKEN_VAULT_PATH_DEFAULT)
    try:
        vault_path = template.format(svc=parsed.selector)
    except (KeyError, IndexError):
        vault_path = CONSUL_TOKEN_VAULT_PATH_DEFAULT.format(svc=parsed.selector)
    vault_ref_obj = ProvisioningRef(kind='vault', subkind='secret', selector=vault_path)
    return _probe_vault(ref, vault_ref_obj, config, repo_root, vault_client=vault_client)


def _declared_stack_paths(config: dict) -> set[str]:
    """Every repo-relative stack path declared anywhere in *config* (V8-PREP-5,
    shared source for O4's container-name resolution and O3's one_shot
    cross-reference): each ``deploy.phases.*.services[].path`` plus every
    ``deploy.profiles.*.stacks[]`` entry.

    Mirrors :func:`ciu.deploy._producer_profile_stack_paths`'s two sources,
    but aggregated across ALL phases/profiles rather than one profile at a
    time: a provisioning probe has no active-profile context of its own — it
    only ever receives the merged global *config* — so there is no single
    profile to scope this to. Re-derived here rather than imported because
    neither `_producer_profile_stack_paths` nor ciu-P22's `action_check`
    `deployed_paths` enumeration is exposed as a reusable, profile-agnostic
    helper (`deploy.py` is this package's own forbidden file, so a shared
    helper cannot be extracted there either).
    """
    paths: set[str] = set()
    deploy_cfg = config.get("deploy", {})
    if not isinstance(deploy_cfg, dict):
        return paths

    phases = deploy_cfg.get("phases", {})
    if isinstance(phases, dict):
        for phase in phases.values():
            if not isinstance(phase, dict):
                continue
            for svc in phase.get("services", []) or []:
                if isinstance(svc, dict):
                    path = svc.get("path")
                    if isinstance(path, str) and path:
                        paths.add(path)

    profiles = deploy_cfg.get("profiles", {})
    if isinstance(profiles, dict):
        for pdata in profiles.values():
            if not isinstance(pdata, dict):
                continue
            for s in pdata.get("stacks", []) or []:
                if isinstance(s, str) and s:
                    paths.add(s)

    return paths


def _stack_container_name(config: dict, selector: str) -> str:
    """Resolve a stack ref's selector to the container name for it (O4,
    V8-PREP-5).

    A slash-FREE selector is passed straight through to ``container_name``
    UNCHANGED — byte-identical to every release before this package, which
    is the regression bar this fix must not cross. A slash-bearing selector
    (e.g. ``infra/db-init``) is guaranteed-broken today: ``container_name``
    builds ``{project}-{env_tag}-infra/db-init``, a name no real container
    can ever have (no test exercises a passing slash-bearing selector,
    confirming nothing relies on that brokenness). When such a selector
    matches a KNOWN declared stack path (:func:`_declared_stack_paths`), it
    is resolved to that path's final segment — the same bare-name convention
    every slash-free selector already uses — before being handed to
    ``container_name``. A selector that does not match any known path falls
    through UNCHANGED: a genuine typo still surfaces as "container not
    found", exactly as it always has, rather than being silently
    reinterpreted into some other stack's container.
    """
    if "/" in selector:
        resolved = _resolve_declared_stack_path(selector, _declared_stack_paths(config))
        if resolved is not None:
            selector = resolved.rsplit("/", 1)[-1]

    try:
        from ciu.deploy import container_name as _container_name
        return _container_name(config, selector)
    except (ValueError, KeyError):
        return selector


def _one_shot_stack_service(config: dict, selector: str) -> Optional[dict]:
    """Return the ``deploy.phases.*.services[]`` entry *selector* refers to,
    or ``None`` (O3's ciu-check cross-reference, V8-PREP-5).

    Matches by declared ``path`` — exact, or by that path's final segment
    against a bare selector — the same resolution rule O4's
    :func:`_stack_container_name` uses, so ``stack:db-init:healthy`` and
    ``stack:infra/db-init:healthy`` both find the same phase entry when
    ``infra/db-init`` is the declared path.
    """
    deploy_cfg = config.get("deploy", {})
    if not isinstance(deploy_cfg, dict):
        return None
    phases = deploy_cfg.get("phases", {})
    if not isinstance(phases, dict):
        return None
    for phase in phases.values():
        if not isinstance(phase, dict):
            continue
        for svc in phase.get("services", []) or []:
            if not isinstance(svc, dict):
                continue
            path = svc.get("path")
            if not isinstance(path, str) or not path:
                continue
            if path == selector or path.rsplit("/", 1)[-1] == selector:
                return svc
    return None


def _probe_stack(ref, parsed, config, *, docker_exec_fn=None) -> ProbeResult:
    """Probe a stack:<name>:healthy|completed ref via docker inspect.

    ``parsed.subkind`` carries the terminal: ``''`` for the original
    ``:healthy`` form (behaviour unchanged, O1) or ``'completed'`` for the
    new exit-0-based terminal, which NEVER reads ``Health`` under any code
    path below — it looks only at ``Running``/``ExitCode``. That is the
    exact false-positive gap ``:healthy``'s own exit-0-no-healthcheck
    fallback has: a healthcheck that reports healthy and THEN the container
    exits still satisfies `:healthy` via the ``status == 'healthy'`` branch
    below, even though the container is no longer running.
    """
    import json
    from ciu import procutil

    is_completed = parsed.subkind == 'completed'
    cname = _stack_container_name(config, parsed.selector)

    if not is_completed:
        # O3 (V8-PREP-5): a `:healthy` ref targeting a stack that declares
        # itself `one_shot = true` in its OWN phase entry is exactly the
        # situation `:completed` exists to fix — warn every time, regardless
        # of live outcome, mirroring O2's always-on deprecation warning.
        matched = _one_shot_stack_service(config, parsed.selector)
        if matched is not None:
            from .deploy_pkg import phases as phases_pkg
            try:
                declared_one_shot = phases_pkg.service_one_shot(matched)
            except ValueError:
                declared_one_shot = False
            if declared_one_shot:
                print(
                    f"[WARN] stack:{parsed.selector}:healthy references a stack "
                    "declared 'one_shot = true' (a run-to-completion service). "
                    f"Use 'stack:{parsed.selector}:completed' instead: it checks "
                    "the same clean-exit signal without depending on the "
                    "absence of a Docker healthcheck.",
                    flush=True,
                )

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

    if is_completed:
        # :completed (O1, V8-PREP-5) NEVER reads Health — exit-0-based only.
        running = state.get('Running', False)
        exit_code = state.get('ExitCode')
        if not running and exit_code == 0:
            return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' completed (exited 0)")
        if running:
            # CIU-68: a one-shot job that has not finished YET is on track,
            # not broken — the identical mistake `:healthy`/`starting` made.
            return ProbeResult(
                ref=ref, satisfied=False, retryable=True,
                reason=f"Stack '{parsed.selector}' is still running, not completed",
            )
        return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' did not complete cleanly (exit code {exit_code})")

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
        # treat a clean exit as satisfied rather than "not running". This
        # fallback's BEHAVIOR is unchanged (removing it is V8's own breaking
        # change, not this package's) but it is now DEPRECATED (O2,
        # V8-PREP-5): warn every time it is the reason a probe is satisfied,
        # naming the ref and the `:completed` terminal that checks the same
        # signal without this fallback's false-positive gap (a healthcheck
        # that reports healthy and then the container exits still satisfies
        # `:healthy` via the branch above, even though the container is no
        # longer running).
        exit_code = state.get('ExitCode')
        if exit_code == 0:
            print(
                f"[WARN] stack:{parsed.selector}:healthy was satisfied only via "
                "the exit-0-no-healthcheck fallback (one-shot semantics). Use "
                f"'stack:{parsed.selector}:completed' instead — V8 removes "
                "this fallback.",
                flush=True,
            )
            return ProbeResult(ref=ref, satisfied=True, reason=f"Stack '{parsed.selector}' completed (one-shot, exited 0)")
        return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' is not running (exit code {exit_code})")
    if status == 'starting':
        # CIU-68: `starting` means the container is inside its own declared
        # start_period — Docker's word for "converging normally", not a
        # verdict. It is the one non-satisfied health status a bounded poll
        # can legitimately wait out; `unhealthy` and every other status
        # below still fail promptly.
        return ProbeResult(
            ref=ref, satisfied=False, retryable=True,
            reason=f"Stack '{parsed.selector}' health status: starting "
                   "(within its start_period, not yet converged)",
        )
    return ProbeResult(ref=ref, satisfied=False, reason=f"Stack '{parsed.selector}' health status: {status}")


# ===========================================================================
# S13.4b — `[registry.*]` schema validation (`ciu check` stage 7, ciu-P19)
# ===========================================================================

#: The `[registry.<name>]` sub-tables CIU itself READS, and therefore the only
#: ones it validates. **This list is deliberately not the V8 proposal's five
#: provisioning kinds** (§2.6 sketched PostgreSQL/Redis/MinIO/Consul/Vault
#: models): CIU's own code reads exactly two values out of `[registry.*]` —
#: ``[registry.postgresql].database`` (:func:`_probe_pg`, the ``pg:schema/*``
#: probe's target database) and ``[registry.consul].token_vault_path``
#: (:func:`_probe_consul`, the ``consul:token/*`` probe's Vault path
#: template). No Redis/MinIO/Vault/PostgreSQL-users registry shape exists
#: anywhere in this repo's code or docs to validate against, so CIU ships no
#: model for one: an invented schema that happens to be wrong would REJECT a
#: legitimate consumer table CIU never needed to constrain, which is strictly
#: worse than no schema at all. Everything else under `[registry.*]` stays
#: free-form consumer metadata, validated — if the consumer wants it
#: validated — by the ``validate_registry`` extension point below.
REGISTRY_VALIDATED_TABLES: tuple[str, ...] = ("postgresql", "consul")

#: Global-config key naming a Python file that defines
#: ``validate_registry(config) -> list[str]`` (the V8 proposal's Option C, for
#: consumer-owned registry shapes). Lives under `[ciu]` — CIU's own
#: workspace-switch namespace — rather than inside `[registry]` itself, so a
#: CIU-reserved key can never collide with a consumer's own
#: `[registry.<name>]` table, which is exactly the free-form space this key
#: exists to let them police.
REGISTRY_VALIDATOR_KEY = "registry_validator"


def _load_pydantic():
    """Import the optional ``pydantic`` dependency; fail LOUD if absent.

    Mirrors :func:`ciu.composefile._load_jsonschema` (S5.7/CIU-37) exactly,
    including the lazy, function-local import: when no config declares a
    validated `[registry.*]` sub-table, this is never called and pydantic is
    never imported. When one IS declared but the extra is missing, the caller
    turns this into a tagged finding naming ``ciu[registry]`` — never a
    silent skip, which would let a typo'd registry table pass a green
    ``ciu check``.
    """
    try:
        import pydantic
    except ImportError:
        raise ValueError(
            "[S13.4b] a [registry.postgresql] or [registry.consul] table is "
            "declared, but validating it requires the optional 'pydantic' "
            "dependency. Install it with: pip install 'ciu[registry]' "
            "(or remove the table if nothing reads it)."
        ) from None
    return pydantic


def _svc_template_problem(value: str) -> Optional[str]:
    """Return why *value* is an unsafe ``.format(svc=…)`` template, or ``None``.

    Every constraint here is grounded in what :func:`_probe_consul` ACTUALLY
    does with the string — nothing stricter:

    * unbalanced/invalid braces (``"consul/{svc"``) raise ``ValueError`` from
      ``str.format``, which that probe does **not** catch: the whole
      provisioning probe run dies with a traceback;
    * any placeholder other than ``svc`` (``"{service}"``, ``"{}"``, ``"{0}"``)
      raises ``KeyError``/``IndexError``, which the probe DOES catch — and
      then silently falls back to :data:`CONSUL_TOKEN_VAULT_PATH_DEFAULT`,
      i.e. reads a completely different Vault path than the operator wrote,
      with no warning at all.

    Deliberately NOT enforced: the presence of ``{svc}``. A template without
    it substitutes cleanly and yields one constant path for every service —
    degenerate for most deployments, but a legitimate shape for one shared
    ACL token, and the probe code requires nothing of the sort. Rejecting it
    would be a constraint stricter than the real consumer, which is precisely
    how a schema starts rejecting valid configs.
    """
    import string

    try:
        parsed = list(string.Formatter().parse(value))
    except ValueError as exc:
        return (
            f"is not a valid format template ({exc}); `str.format` raises here "
            "and the consul:token probe does not catch ValueError, so this "
            "aborts the whole probe run"
        )
    for _literal, field_name, _spec, _conv in parsed:
        if field_name is None:
            continue
        root = field_name.split(".")[0].split("[")[0]
        if root != "svc":
            shown = "{}" if field_name == "" else "{" + field_name + "}"
            return (
                f"references {shown}, but the consul:token probe substitutes "
                "only {svc}; the substitution fails and the probe SILENTLY "
                f"falls back to '{CONSUL_TOKEN_VAULT_PATH_DEFAULT}'"
            )
    return None


def _build_registry_models() -> dict:
    """Build the `[registry.*]` models, one fresh set per call.

    The classes are defined INSIDE this function because pydantic is an
    optional extra: a module-scope ``class X(pydantic.BaseModel)`` would make
    importing :mod:`ciu.provisioning` — and therefore every ``ciu`` command —
    hard-depend on it. They are deliberately NOT memoized in a module global
    either: the cost is one-off (this runs once per ``ciu check``), and a
    cached set would silently satisfy a later call whose whole point was that
    pydantic is unavailable.

    ``extra="allow"`` is load-bearing, not laziness. CONFIG.md documents
    `[registry.*]` as free-form cross-stack metadata (PostgreSQL users, Redis
    ACLs, …) referenced by hooks and templates; CIU reads ONE key out of each
    of these two tables. Forbidding extras would reject every consumer table
    that carries anything else — which is most of them. ``strict=True`` stops
    pydantic from coercing a wrong-typed value into a plausible-looking string
    and calling it valid.
    """
    from typing import Annotated

    pydantic = _load_pydantic()

    NonEmptyStr = Annotated[str, pydantic.StringConstraints(min_length=1)]

    class RegistryPostgresql(pydantic.BaseModel):
        """`[registry.postgresql]` — CIU reads ``database`` only.

        Consumed by :func:`_probe_pg` as the ``-d`` argument of the
        ``pg:schema/<name>`` probe's ``psql`` invocation. It is optional
        (absent ⇒ the probe uses the default ``postgres`` database), but a
        DECLARED value must be a non-empty string: the probe does
        ``str(db_name)``, so a non-string is coerced into a nonsense database
        name rather than rejected, and an empty string is falsy, so it is
        silently ignored and the probe quietly targets the wrong database.
        """

        model_config = pydantic.ConfigDict(extra="allow", strict=True)

        database: Optional[NonEmptyStr] = None

    class RegistryConsul(pydantic.BaseModel):
        """`[registry.consul]` — CIU reads ``token_vault_path`` only.

        Consumed by :func:`_probe_consul` as a ``.format(svc=…)`` template.
        See :func:`_svc_template_problem` for exactly which shapes break, and
        for the one plausible constraint (``{svc}`` must appear) deliberately
        NOT enforced because the probe does not require it.
        """

        model_config = pydantic.ConfigDict(extra="allow", strict=True)

        token_vault_path: Optional[NonEmptyStr] = None

        @pydantic.field_validator("token_vault_path")
        @classmethod
        def _check_svc_template(cls, value):
            if value is not None:
                problem = _svc_template_problem(value)
                if problem is not None:
                    raise ValueError(problem)
            return value

    return {"postgresql": RegistryPostgresql, "consul": RegistryConsul}


def _load_consumer_validator(path: Path):
    """Import *path* and return its ``validate_registry`` callable.

    Reuses :func:`ciu.hooks_runner._load_hook_module` — the loader ciu-P18
    extracted out of ``load_hook`` precisely so a second caller could get a
    module out of a file path with the same ``[S9.2]`` missing-file semantics
    — instead of a second ``spec_from_file_location`` block that could drift
    from it. This file is NOT a hook: it need not define ``run``, so
    ``load_hook``/``load_hook_for_check`` (both of which require one) are the
    wrong entry points.

    Bytecode writing is suppressed for the duration of the import and restored
    in a ``finally``: ``ciu check`` is contractually side-effect-free
    (S13.4a/CIU-QOL-12) and CPython would otherwise drop a ``__pycache__/``
    directory beside the consumer's validator file — the same real write
    ciu-P18 had to suppress for hook imports.
    """
    import sys

    from . import hooks_runner

    saved_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module = hooks_runner._load_hook_module(path)
    finally:
        sys.dont_write_bytecode = saved_dont_write_bytecode
    return getattr(module, "validate_registry", None)


def _run_consumer_validator(config: dict, repo_root: Path) -> list[str]:
    """Run the consumer-declared ``validate_registry(config)``, if any (S13.4b).

    The V8 proposal's Option C: CIU owns models for what CIU itself reads, and
    hands everything else under `[registry.*]` to whoever does read it. The
    consumer declares ONE path in the global config::

        [ciu]
        registry_validator = "infra/registry_validate.py"

    and that file defines a module-level ``validate_registry(config)``
    returning a list of error strings (empty ⇒ OK). The whole global config is
    passed, not just `[registry]`, so a validator can cross-check a registry
    entry against the rest of the workspace.

    Return-value handling mirrors ``validate_config``'s (S9.5) exactly,
    including the ``str``-is-iterable trap: a bare string is ONE malformed
    return, not one finding per character.
    """
    ciu_table = config.get("ciu") or {}
    if not isinstance(ciu_table, dict):
        # [ciu] itself being a non-table is somebody else's finding (S3.x);
        # from here it just means no validator is declared.
        return []
    declared = ciu_table.get(REGISTRY_VALIDATOR_KEY)
    if declared is None:
        return []
    if not isinstance(declared, str) or not declared:
        return [
            f"[S13.4b] [ciu].{REGISTRY_VALIDATOR_KEY} must be a non-empty path "
            f"string, got {type(declared).__name__}"
        ]

    path = Path(declared)
    if not path.is_absolute():
        path = repo_root / path
    try:
        validator = _load_consumer_validator(path)
    except Exception as exc:  # noqa: BLE001 — a consumer file can raise anything
        return [
            f"[S13.4b] [ciu].{REGISTRY_VALIDATOR_KEY} '{declared}' could not be "
            f"loaded: {type(exc).__name__}: {exc}"
        ]
    if not callable(validator):
        return [
            f"[S13.4b] [ciu].{REGISTRY_VALIDATOR_KEY} '{declared}' defines no "
            "callable validate_registry(config) -> list[str]"
        ]

    try:
        result = validator(config)
    except Exception as exc:  # noqa: BLE001 — the validator's own defect
        return [
            f"[S13.4b] validate_registry in '{declared}' raised "
            f"{type(exc).__name__}: {exc}"
        ]
    if result is None:
        return []
    if isinstance(result, (str, bytes)) or not isinstance(result, (list, tuple)):
        return [
            f"[S13.4b] validate_registry in '{declared}' returned "
            f"{type(result).__name__}; S13.4b requires a list of error strings "
            "(empty = OK)"
        ]
    return [f"[S13.4b] {item}" for item in result]


def validate_registries(config: dict, repo_root: Path) -> list[str]:
    """Validate `[registry.*]` against what CIU reads; return findings (S13.4b).

    *config* is the GLOBAL config (``profile.config``) — the same object
    :func:`probe_ref` is handed, and the only place `[registry.*]` can live: a
    stack config carrying a top-level ``[registry]`` table already fails S3.5
    at ``ciu check``'s stage 2, because ``registry`` is not stack-reserved, so
    it would be a second non-reserved root key.

    Returns a list of finding strings and **raises nothing** for any expected
    condition, including pydantic being absent (which becomes a finding naming
    ``ciu[registry]``). One contract for the caller: findings ⇒ the stage
    failed ⇒ exit 2. Only the sub-tables in
    :data:`REGISTRY_VALIDATED_TABLES` are modelled — see that constant for why
    the V8 proposal's other three kinds are deliberately absent — and the rest
    of `[registry.*]` is the consumer's, via
    :func:`_run_consumer_validator`'s extension point.
    """
    findings: list[str] = []
    registry = config.get("registry")

    if registry is not None and not isinstance(registry, dict):
        findings.append(
            f"[S13.4b] [registry] must be a table, got {type(registry).__name__}"
        )
        registry = None

    declared = [
        name for name in REGISTRY_VALIDATED_TABLES if name in (registry or {})
    ]
    if declared:
        try:
            models = _build_registry_models()
        except ValueError as exc:
            # pydantic absent while a validated table IS declared: loud,
            # actionable, and fatal to the stage — never a silent skip.
            findings.append(str(exc))
            models = None
        if models is not None:
            import pydantic

            for name in declared:
                try:
                    models[name].model_validate(registry[name])
                except pydantic.ValidationError as exc:
                    for err in exc.errors():
                        loc = ".".join(str(part) for part in err["loc"])
                        where = f".{loc}" if loc else ""
                        findings.append(
                            f"[S13.4b] [registry.{name}]{where}: {err['msg']}"
                        )

    findings.extend(_run_consumer_validator(config, repo_root))
    return findings
