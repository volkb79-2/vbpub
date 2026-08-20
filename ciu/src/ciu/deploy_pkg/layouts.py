"""
CIU v3 deploy_pkg — deploy layouts (S7.5c).

A layout is a named host→bundles plan plus the deployment's environment:

    [deploy.layouts.<name>]
    environment = "prod"            # REQUIRED: dev|test|staging|prod
    description = "..."             # optional

    [deploy.layouts.<name>.hosts.edge-a]
    bundles = ["core"]

    [deploy.layouts.<name>.hosts.backend]
    bundles = ["db", "worker-io"]

Pure orchestration data: a layout REFERENCES profiles (S7.4) and the host
inventory (SPEC J / S14.3); it never merges either itself. `ciu up --layout`
resolves + validates here, then delegates each host to the existing
`up --host` path with CIU_SERVICES_PROFILE set to the host's bundles and
CIU_LAYOUT / CIU_LAYOUT_HOST / CIU_DEPLOY_ENVIRONMENT exported into the
remote command environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import resolve_profiles

# S7.5c closed environment vocabulary (dstdns D-105 Q2).
ENVIRONMENTS = ("dev", "test", "staging", "prod")


@dataclass
class Layout:
    """Resolved deploy layout (named host→bundles plan).

    Attributes:
        name:          Layout name as declared in [deploy.layouts.<name>].
        environment:   One of ENVIRONMENTS — the deployment's environment,
                       exported to every remote command as
                       CIU_DEPLOY_ENVIRONMENT.
        hosts:         Ordered list of host names. Declaration order is
                       preserved (dict order) and is the execution order for
                       `ciu up --layout`.
        bundles:       Ordered mapping host name -> list of profile names
                       (the host's CIU_SERVICES_PROFILE value).
        description:   Optional human-readable description.
    """

    name: str
    environment: str
    hosts: list[str] = field(default_factory=list)
    bundles: dict[str, list[str]] = field(default_factory=dict)
    description: str | None = None


# ---------------------------------------------------------------------------
# S7.5c — resolve_layout (primary API)
# ---------------------------------------------------------------------------


def resolve_layout(global_cfg: dict, hosts_cfg: dict, name: str) -> Layout:
    """Resolve and validate one layout from the global config.

    Validation (all tagged [S7.5c], all before any transport opens):
    - the layout exists in [deploy.layouts];
    - `environment` is present and one of ENVIRONMENTS;
    - `hosts` is a non-empty table;
    - every host name exists in the hosts inventory (*hosts_cfg* — the
      load_hosts result);
    - every bundle name resolves via deploy_pkg.profiles (S7.4).

    Declaration order of hosts is preserved and is the execution order.
    Raises ValueError with a tagged, actionable message otherwise.
    """
    layouts_table = global_cfg.get("deploy", {}).get("layouts", {})
    if name not in layouts_table:
        available = ", ".join(sorted(layouts_table)) if layouts_table else "(none)"
        raise ValueError(
            f"[S7.5c] Unknown layout '{name}'. Available layouts: {available}."
        )
    data = layouts_table[name]
    if not isinstance(data, dict):
        raise ValueError(
            f"[S7.5c] Layout '{name}' must be a [deploy.layouts.{name}] table."
        )

    environment = data.get("environment")
    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        raise ValueError(
            f"[S7.5c] Layout '{name}': 'environment' is required and must be one "
            f"of {', '.join(ENVIRONMENTS)} (got {environment!r})."
        )

    hosts = data.get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        raise ValueError(
            f"[S7.5c] Layout '{name}': 'hosts' must be a non-empty table "
            f"([deploy.layouts.{name}.hosts.<host>] with bundles = [...])."
        )

    ordered_hosts: list[str] = []
    bundles_by_host: dict[str, list[str]] = {}
    for host_name, host_data in hosts.items():
        if host_name not in hosts_cfg:
            available = ", ".join(sorted(hosts_cfg)) if hosts_cfg else "(none)"
            raise ValueError(
                f"[S7.5c] Layout '{name}': host '{host_name}' is not in the hosts "
                f"inventory. Available hosts: {available}."
            )
        if not isinstance(host_data, dict):
            raise ValueError(
                f"[S7.5c] Layout '{name}', host '{host_name}': must be a "
                f"[deploy.layouts.{name}.hosts.{host_name}] table."
            )
        bundles = host_data.get("bundles")
        if not isinstance(bundles, list):
            raise ValueError(
                f"[S7.5c] Layout '{name}', host '{host_name}': 'bundles' must be "
                f"a list of profile names."
            )
        if not bundles:
            # An empty bundles list is NOT "deploy nothing": resolve_profiles
            # treats an empty names list as absent and falls through to its
            # ambient-env / all-phases default (deploy_pkg/profiles.py:301-305,
            # the 2026-07-16 dstdns incident). CIU_SERVICES_PROFILE='' would
            # therefore deploy EVERY phase on this host — refuse it here.
            raise ValueError(
                f"[S7.5c] Layout '{name}', host '{host_name}': 'bundles' must "
                f"not be empty (an empty list resolves to ALL phases, not none)."
            )
        for bundle in bundles:
            # env={} so validation never trips on ambient CIU_HOST_PROFILE /
            # CIU_SERVICES_PROFILE — the layout's own list is the only input.
            try:
                resolve_profiles(global_cfg, [bundle], env={})
            except ValueError as exc:
                raise ValueError(
                    f"[S7.5c] Layout '{name}', host '{host_name}': bundle profile "
                    f"'{bundle}' failed to resolve: {exc}"
                ) from None
        # Joint validation (controller nit a): resolve the host's FULL bundle
        # list TOGETHER so a cross-bundle conflict (env_overrides /
        # topology_overrides) fails at declaration time, not mid-sequence on
        # the remote after earlier hosts already deployed. Per-bundle errors
        # above stay precise (which single bundle is unknown); this pass only
        # ever raises on a COMBINATION conflict since each name individually
        # already resolved.
        try:
            resolve_profiles(global_cfg, list(bundles), env={})
        except ValueError as exc:
            raise ValueError(
                f"[S7.5c] Layout '{name}', host '{host_name}': bundles "
                f"{list(bundles)!r} conflict when combined: {exc}"
            ) from None
        ordered_hosts.append(host_name)
        bundles_by_host[host_name] = list(bundles)

    return Layout(
        name=name,
        environment=environment,
        hosts=ordered_hosts,
        bundles=bundles_by_host,
        description=data.get("description"),
    )


def list_layouts(global_cfg: dict) -> list[tuple[str, str, list[str]]]:
    """Return declared layouts in declaration order: (name, environment, hosts).

    Pure listing — deliberately no validation and no inventory requirement:
    `ciu layouts` shows what is DECLARED; `ciu up --layout` is the validating
    consumer. Missing/invalid environment values are reported verbatim.
    """
    layouts_table = global_cfg.get("deploy", {}).get("layouts", {})
    result: list[tuple[str, str, list[str]]] = []
    for name, data in layouts_table.items():
        if not isinstance(data, dict):
            result.append((name, "", []))
            continue
        environment = data.get("environment")
        environment = environment if isinstance(environment, str) else ""
        hosts = data.get("hosts")
        host_names = list(hosts) if isinstance(hosts, dict) else []
        result.append((name, environment, host_names))
    return result