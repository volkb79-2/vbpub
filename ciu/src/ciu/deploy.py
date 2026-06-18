#!/usr/bin/env python3
"""CIU v2 deployment orchestrator (``ciu-deploy``) — S7 / S10.2 / S10.3.

This is the P10 rewrite of the v1 3350-line ``deploy.py``. It is a thin v2
orchestrator over the already-landed building blocks; it owns *no* config /
secret / compose logic of its own:

  - config load + merge ........ config_model.render_global_chain / render_stack
  - host profiles .............. deploy_pkg.profiles (S7.4 / S7.5 / S7.5a)
  - phase ordering + selection . deploy_pkg.phases (S7.1 / S7.2)
  - per-stack execution ........ engine.main_execution (in-process, S8.3)
  - health gate ................ deploy_pkg.health (S7.7 / S7.8)
  - secret directive discovery . secrets.directives (S4 / S7.6 vault preflight)
  - vault address/token ........ secrets.providers (S4.16)
  - registry auth .............. deploy_pkg.registry (S7.9)
  - all subprocess use ......... procutil.run_cmd / procutil.docker

Spec contracts enforced here:

  - S7.3  a failed stack start fails the phase: remaining services in the
          phase and all later phases are skipped; exit 1; ``--ignore-errors``
          keeps going but the final exit is still 1.
  - S7.5  ``[deploy.groups]`` / ``--groups`` do NOT exist; the validator
          (profiles.reject_groups) aborts with a pointer to profiles.
  - S7.6  vault preflight before any phase runs.
  - S7.7  health gate: pending FAILS; ``no-healthcheck`` is a warning.
  - S7.8  container lookups use anchored name filters, never substrings.
  - S10.3 exit codes: 0 ok · 1 runtime · 2 config/validation · 3 env/bootstrap.

Discipline (S7.3 / S8.4): no ``sys.exit`` inside actions — every action
returns an int; ``main()`` is the single exit point and maps exceptions to the
S10.3 taxonomy via engine._exit_code_for. No ``os.environ`` mutation (profile
env_overrides flow into the env dict handed to stacks), no ``eval``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from . import config_model
from . import engine
from . import procutil
from .cli_utils import get_cli_version
from .config_constants import (
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_RENDERED,
    STACK_CONFIG_RENDERED,
)
from .deploy_pkg import health as health_pkg
from .deploy_pkg import phases as phases_pkg
from .deploy_pkg import profiles as profiles_pkg
from .deploy_pkg import registry as registry_pkg
from .secrets import directives as secret_directives
from .secrets.providers import (
    VaultError,
    resolve_vault_token,
    vault_addr_from_config,
)
from .workspace_env import (
    REQUIRED_KEYS_CORE,
    WorkspaceEnvError,
    bootstrap_env_init,
    bootstrap_workspace_env,
    detect_standalone_root,
    ensure_workspace_network,
    resolve_env_root,
)

# Pseudo-phase key used to append a profile's extra ``stacks`` after the
# numbered phases (S7.4). It sorts last by construction (see _build_selection).
EXTRA_STACKS_KEY = "profile_extra_stacks"


# ===========================================================================
# Logging helpers ([INFO]/[ERROR]/... prefixes, matching the rest of CIU)
# ===========================================================================


def info(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", flush=True)


def error(msg: str) -> None:
    """Print an error. Per S7.3 this NEVER exits — actions return an int."""
    print(f"[ERROR] {msg}", flush=True)


def success(msg: str) -> None:
    print(f"[SUCCESS] {msg}", flush=True)


# ===========================================================================
# Small parsers / formatting helpers
# ===========================================================================

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600}


def _seconds(value: object, default: float = 30.0) -> float:
    """Parse a duration into seconds.

    Accepts an int/float (seconds) or a string like ``"30s"`` / ``"2m"`` /
    ``"45"`` (bare = seconds). The v1 config used strings such as ``"30s"``.
    Unparseable values fall back to *default* with a warning.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = _DURATION_RE.match(value)
        if m:
            return float(int(m.group(1)) * _DURATION_UNITS[m.group(2).lower()])
    warn(f"could not parse duration {value!r}; using {default:g}s")
    return default


def container_name(config: dict, service_name: str) -> str:
    """``{project}-{env_tag}-{service_name}`` (S7.7 / S7.8 naming convention).

    project / env_tag come from the (profile-resolved) config's
    ``deploy.project_name`` / ``deploy.environment_tag``.
    """
    deploy_cfg = config.get("deploy", {})
    project = deploy_cfg.get("project_name")
    env_tag = deploy_cfg.get("environment_tag")
    if not project:
        raise ValueError("[ERROR] deploy.project_name not set in config")
    if not env_tag:
        raise ValueError("[ERROR] deploy.environment_tag not set in config")
    return f"{project}-{env_tag}-{service_name}"


# ===========================================================================
# Config + profile loading (S7.4 / S7.5)
# ===========================================================================


def resolve_repo_root(define_root: Optional[Path]) -> Path:
    """Resolve the repo root from --define-root/--root-folder or REPO_ROOT (S1.1).

    Mirrors engine.main_execution's rule: --define-root must match REPO_ROOT
    when both are set.
    """
    if define_root is not None:
        repo_root = Path(define_root).resolve()
        env_repo_root = os.environ.get("REPO_ROOT")
        if env_repo_root and Path(env_repo_root).resolve() != repo_root:
            raise ValueError(
                f"[ERROR] --define-root ({repo_root}) does not match "
                f"REPO_ROOT ({env_repo_root}). Update ciu.env or pass a "
                "matching --define-root."
            )
        return repo_root
    env_repo_root = os.environ.get("REPO_ROOT")
    if not env_repo_root:
        raise WorkspaceEnvError(
            "[ERROR] REPO_ROOT not set. Run 'ciu env generate' and "
            "source ciu.env."
        )
    return Path(env_repo_root).resolve()


def load_global_config(repo_root: Path) -> dict:
    """Render the global chain ONCE per invocation (S3.3, working_dir=repo_root).

    Then reject [deploy.groups] (S7.5) so the operator gets the profiles
    pointer immediately at config load.
    """
    global_cfg = config_model.render_global_chain(repo_root, repo_root)
    profiles_pkg.reject_groups(global_cfg)
    return global_cfg


def resolve_profile(global_cfg: dict, name: Optional[str]) -> profiles_pkg.Profile:
    """Resolve the active host profile (S7.4 / S7.5). default env CIU_HOST_PROFILE."""
    return profiles_pkg.resolve_profile(global_cfg, name)


def profile_env(profile: profiles_pkg.Profile) -> dict:
    """Build the env dict handed to stacks: os.environ + profile.env_overrides.

    S7.4 — env_overrides are applied to a COPY of os.environ, never mutated in
    place (no os.environ mutation rule).
    """
    env = dict(os.environ)
    for k, v in profile.env_overrides.items():
        env[k] = str(v)
    return env


# ===========================================================================
# Selection (S7.1 / S7.2 / S7.4) — phases ∩ --phases, plus extra_stacks
# ===========================================================================


def _phase_filter(profile: profiles_pkg.Profile, cli_phases: Optional[set[str]]) -> Optional[set[str]]:
    """Intersect profile.phase_keys with the --phases CLI filter (both optional).

    Returns None when neither restricts (= all phases), else the intersection.
    """
    keys = profile.phase_keys
    if keys is None and cli_phases is None:
        return None
    if keys is None:
        return set(cli_phases)
    if cli_phases is None:
        return set(keys)
    return set(keys) & set(cli_phases)


def build_selection(
    profile: profiles_pkg.Profile,
    cli_phases: Optional[set[str]] = None,
) -> list[dict]:
    """Build the ordered list of stacks to act on (S7.1 numeric order, S7.4).

    Reads everything from ``profile.config`` (the profile-resolved global
    config). Each returned entry is::

        {phase_num, phase_key, path, name, service}

    where *service* is the raw service dict ({path, name, enabled, profiles?,
    env_overrides?}). Numbered phases come first in numeric order (S7.1);
    profile.extra_stacks (S7.4) are appended afterwards as a pseudo-phase
    (key=EXTRA_STACKS_KEY) so admins get a documented, deterministic ordering:
    **numbered phases, then profile `stacks`**.
    """
    deploy_cfg = profile.config.get("deploy", {})
    phases_cfg = deploy_cfg.get("phases", {})
    control = deploy_cfg.get("control", {})
    pfilter = _phase_filter(profile, cli_phases)

    selection: list[dict] = []
    for phase_num, phase_key, svc in phases_pkg.iter_enabled_services(
        phases_cfg, control=control, phase_filter=pfilter
    ):
        selection.append(
            {
                "phase_num": phase_num,
                "phase_key": phase_key,
                "path": svc["path"],
                "name": svc.get("name") or Path(svc["path"]).name,
                "service": svc,
            }
        )

    # profile.stacks → pseudo-phase appended last (S7.4 ordering).
    for stack_path in profile.extra_stacks:
        selection.append(
            {
                "phase_num": float("inf"),
                "phase_key": EXTRA_STACKS_KEY,
                "path": stack_path,
                "name": Path(stack_path).name,
                "service": {"path": stack_path, "name": Path(stack_path).name, "enabled": True},
            }
        )
    return selection


def group_by_phase(selection: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group a selection into ordered (phase_key, [entries]) pairs (S7.1)."""
    grouped: list[tuple[str, list[dict]]] = []
    for entry in selection:
        if grouped and grouped[-1][0] == entry["phase_key"]:
            grouped[-1][1].append(entry)
        else:
            grouped.append((entry["phase_key"], [entry]))
    return grouped


def render_selected_stacks(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
) -> dict[str, dict]:
    """Render ciu.toml for every selected stack ONCE (S3.4, preserve_state).

    Returns ``{stack_path_str: rendered_stack_config}``. The renders are reused
    by the vault preflight (S7.6) and by ``--render-toml``. Uses
    ``profile.config`` as the global context (topology_overrides applied).
    """
    rendered: dict[str, dict] = {}
    for entry in selection:
        rel = entry["path"]
        if rel in rendered:
            continue
        # Shipped stacks (S8.6) have no CIU config to render; skip them so
        # --render-toml and the vault preflight (S7.6) don't choke on a
        # missing ciu.defaults.toml.j2.
        if phases_pkg.service_shipped(entry["service"]):
            continue
        stack_dir = (repo_root / rel).resolve()
        rendered[rel] = config_model.render_stack(
            stack_dir, global_config=profile.config, preserve_state=True
        )
    return rendered


# ===========================================================================
# Vault preflight (S7.6) + misplaced-directive validation (S4.1/S4.5)
# ===========================================================================


def _is_vault_stack_path(config: dict, rel_path: str) -> bool:
    """Identify the vault stack by config key or a ``vault*`` directory name.

    vault.stack_path (default 'infra/vault') OR a stack directory whose final
    component starts with ``vault`` (e.g. ``infra/vault-core``).
    """
    configured = config.get("vault", {}).get("stack_path", "infra/vault")
    if rel_path == configured:
        return True
    return Path(rel_path).name.lower().startswith("vault")


def vault_preflight(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
) -> None:
    """S7.6 vault ordering + S4.1/S4.5 misplaced-directive check.

    For each selected stack (using the already-rendered configs):
      - discover its secret specs (root key via validate_stack_shape) and
        check for misplaced directives (abort listing violations);
      - track whether ANY ``*_VAULT`` directive (ASK_VAULT / GEN_TO_VAULT)
        exists across the selection, and the earliest phase index of the
        vault stack.

    If vault-backed directives exist, the gate passes only when either the
    vault stack is in an EARLIER phase of the selection, or a Vault
    token+address resolve via S4.16.

    Raises
    ------
    ValueError
        Static validation failures (S10.3 → exit 2):
          - validate_stack_shape failure
          - misplaced-directive violation [S4.5/S4.1]
          - S7.6 no-token failure (configuration error: the stack TOML
            declares vault-backed secrets but the operator has not provided
            a token or placed the vault stack first)
    VaultError
        Runtime I/O failure resolving the Vault address (S10.3 → exit 1).
    """
    config = profile.config
    needs_vault = False
    needs_vault_at: Optional[float] = None
    vault_stack_at: Optional[float] = None

    for entry in selection:
        rel = entry["path"]
        # Shipped stacks (S8.6) have no CIU config/secrets — not rendered, not
        # vault-checked.
        if phases_pkg.service_shipped(entry["service"]):
            continue
        merged = config_model.deep_merge(config, rendered[rel])
        # validate_stack_shape raises ValueError on bad config (exit 2).
        root_key = config_model.validate_stack_shape(rendered[rel])

        misplaced = secret_directives.find_misplaced(merged, stack_root_key=root_key)
        if misplaced:
            paths = ", ".join(p for p, _ in misplaced)
            raise ValueError(
                f"[S4.5/S4.1] secret directive(s) or secrets table(s) outside "
                f"the '{root_key}.secrets' scope in stack '{rel}' at: {paths}"
            )

        specs = secret_directives.discover(root_key, merged)
        if any(s.kind in ("ASK_VAULT", "GEN_TO_VAULT") for s in specs):
            needs_vault = True
            if needs_vault_at is None or entry["phase_num"] < needs_vault_at:
                needs_vault_at = entry["phase_num"]

        if _is_vault_stack_path(config, rel):
            if vault_stack_at is None or entry["phase_num"] < vault_stack_at:
                vault_stack_at = entry["phase_num"]

    if not needs_vault:
        return

    # The vault stack runs strictly earlier in the selection → ordering satisfied.
    if vault_stack_at is not None and needs_vault_at is not None and vault_stack_at < needs_vault_at:
        info("[S7.6] vault stack precedes vault-backed stacks in the selection — OK")
        return

    # Otherwise a token + address must resolve now (S4.16).
    # VaultError from I/O issues propagates as-is (exit 1).
    token = resolve_vault_token(config, repo_root)
    addr = vault_addr_from_config(config)
    if not token:
        raise ValueError(
            "[S7.6] the selection declares *_VAULT secrets but the vault stack "
            "is not in an earlier phase and no Vault token resolved (VAULT_TOKEN "
            "env, vault.token_file, or the vault stack's [state].root_token). "
            "Aborting before any phase runs."
        )
    info(f"[S7.6] Vault token + address ({addr}) resolved — OK")


# ===========================================================================
# Registry preflight (S7.9)
# ===========================================================================


def registry_preflight(config: dict) -> None:
    """S7.9 — when deploy.registry.url is set, require Docker credentials.

    Raises ValueError (S10.3 → exit 2) when credentials are missing for the
    configured registry URL: the operator must run ``docker login`` first —
    this is a configuration/setup failure, not a runtime I/O failure.
    """
    url = config.get("deploy", {}).get("registry", {}).get("url", "")
    if not url:
        return
    if registry_pkg.check_registry_auth(url):
        info(f"[S7.9] registry credentials present for {url} — OK")
        return
    raise ValueError(
        f"[S7.9] deploy.registry.url is '{url}' but no credentials were found "
        "in the Docker config (auths/credHelpers/credsStore). Run `docker "
        "login` for that registry, then retry."
    )


# ===========================================================================
# Health gate (S7.7 / S7.8)
# ===========================================================================


def _inspect_state(name: str) -> Optional[dict]:
    """Return a container's docker-inspect ``.State`` dict, or None if missing.

    S7.8 — exact container name (anchored at the engine level by construction:
    the name is the full ``{project}-{env}-{service}``).
    """
    try:
        result = procutil.docker(
            ["inspect", "--format", "{{json .State}}", name], check=False
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    if not out:
        return None
    import json

    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def run_health_gate(
    config: dict,
    service_names: list[str],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
) -> tuple[bool, dict]:
    """Poll the health gate for *service_names* (S7.7). Returns (passed, summary).

    Each name maps to container ``{project}-{env}-{name}``; the aggregate
    check_fn inspects every container each poll and classifies it
    (deploy_pkg.health.classify). pending/unhealthy/not_found FAIL (S7.7).
    """
    names = {name: container_name(config, name) for name in service_names}

    def check_fn() -> dict[str, str]:
        statuses: dict[str, str] = {}
        for svc, cname in names.items():
            statuses[cname] = health_pkg.classify(_inspect_state(cname))
        return statuses

    return health_pkg.wait_for_gate(check_fn, timeout_s=timeout_s, interval_s=interval_s)


def _print_health_summary(summary: dict) -> None:
    """Print the bucket summary (S7.7)."""
    info("Health gate buckets:")
    for bucket in ("healthy", "pending", "unhealthy", "no_healthcheck", "not_found"):
        members = summary.get(bucket, [])
        if members:
            info(f"  {bucket}: {', '.join(members)}")
        else:
            info(f"  {bucket}: -")
    pending = summary.get("pending", [])
    if pending:
        warn(
            "Containers still in 'starting' state — they may still be within "
            "their start_period. If the service is up, the probe may be misconfigured "
            "(e.g. references a tool the image lacks). "
            f"Inspect with: docker logs {pending[0]}"
        )
        warn("  Run 'ciu health --preflight' to probe image/tool availability.")


# ===========================================================================
# ACTIONS — each returns an int exit code; NO sys.exit (S7.3)
# ===========================================================================


def action_render_toml(repo_root: Path, profile: profiles_pkg.Profile, selection: list[dict]) -> int:
    """--render-toml: render the global chain + every selected stack (S8.3 step 3)."""
    info("=" * 60)
    info("RENDER-TOML: rendering global + selected stack configs")
    info("=" * 60)
    info(f"Rendered global config: {repo_root / GLOBAL_CONFIG_RENDERED}")
    rendered = render_selected_stacks(repo_root, profile, selection)
    if not rendered:
        warn("No stacks selected to render")
        return 0
    for rel in sorted(rendered):
        info(f"  rendered: {rel}/{STACK_CONFIG_RENDERED}")
    success(f"Rendered {len(rendered)} stack config(s)")
    return 0


def action_deploy(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    *,
    dry_run: bool,
    ignore_errors: bool,
    health_after_phase: bool,
    update_cert_permission: bool,
) -> int:
    """--deploy: run each phase in numeric order, in-process (S7.3 / S8.3).

    For each enabled service the engine pipeline runs via
    engine.main_execution(working_dir=stack, dry_run=..., yes=True,
    compose_profiles=service.profiles + profile.compose_profiles). A non-success
    result fails the phase: remaining services and all later phases are skipped
    (S7.3). ``--ignore-errors`` keeps going but the return is still 1.

    With *health_after_phase* (S7.7) the health gate runs after each
    successfully-started phase; a gate failure is treated as a phase failure.
    """
    info("=" * 60)
    info(f"DEPLOY: profile={profile.name or '(default/all)'}  dry_run={dry_run}")
    info("=" * 60)

    grouped = group_by_phase(selection)
    if not grouped:
        warn("No phases/stacks selected to deploy")
        return 0

    env = profile_env(profile)
    health_cfg = profile.config.get("deploy", {}).get("health", {})
    timeout_s = _seconds(health_cfg.get("timeout", "30s"))

    deployed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    had_failure = False
    stop_remaining = False

    for phase_key, entries in grouped:
        if stop_remaining:
            for e in entries:
                skipped.append(e["path"])
            info(f">>> SKIP phase {phase_key} ({len(entries)} stack(s)) — earlier phase failed")
            continue

        info("#" * 60)
        info(f">>> PHASE {phase_key} — {len(entries)} stack(s)")
        info("#" * 60)
        phase_failed = False
        started_in_phase: list[dict] = []

        for entry in entries:
            if phase_failed and not ignore_errors:
                skipped.append(entry["path"])
                continue

            stack_dir = (repo_root / entry["path"]).resolve()
            svc = entry["service"]
            compose_profiles = list(svc.get("profiles", [])) + list(profile.compose_profiles)
            svc_env = dict(env)
            for k, v in (svc.get("env_overrides") or {}).items():
                svc_env[k] = str(v)
            shipped = phases_pkg.service_shipped(svc)

            shipped_note = " [shipped]" if shipped else ""
            info(f"--- deploying {entry['path']} (service '{entry['name']}'){shipped_note} ---")
            ok = _run_stack(
                stack_dir,
                env=svc_env,
                compose_profiles=compose_profiles,
                dry_run=dry_run,
                update_cert_permission=update_cert_permission,
                shipped=shipped,
            )
            if ok:
                deployed.append(entry["path"])
                started_in_phase.append(entry)
            else:
                failed.append(entry["path"])
                had_failure = True
                phase_failed = True
                error(f"stack '{entry['path']}' failed to start (S7.3) — phase {phase_key} FAILED")
                if not ignore_errors:
                    stop_remaining = True

        # Health gate after a successfully-started phase (S7.7).
        if health_after_phase and started_in_phase and not dry_run and not phase_failed:
            svc_names = [e["name"] for e in started_in_phase]
            info(f">>> Health gate for phase {phase_key} ({len(svc_names)} service(s))")
            passed, summary = run_health_gate(profile.config, svc_names, timeout_s=timeout_s)
            _print_health_summary(summary)
            if not passed:
                error(f"[S7.7] health gate FAILED for phase {phase_key}")
                had_failure = True
                phase_failed = True
                for e in started_in_phase:
                    if e["path"] in deployed:
                        deployed.remove(e["path"])
                    failed.append(e["path"])
                if not ignore_errors:
                    stop_remaining = True

    _print_deploy_summary(deployed, failed, skipped)
    return 1 if had_failure else 0


def _run_stack(
    stack_dir: Path,
    *,
    env: dict,
    compose_profiles: list[str],
    dry_run: bool,
    update_cert_permission: bool,
    shipped: bool = False,
) -> bool:
    """Run engine.main_execution for one stack in-process. Returns success bool.

    The engine restores cwd and never mutates env (S8.4). Profile env_overrides
    reach the compose process via *env*: we apply them to os.environ-derived
    *env* only for the duration of this call (saved/restored), because the
    engine reads os.environ for the compose process env. No permanent mutation.
    """
    if not stack_dir.is_dir():
        error(f"stack directory not found: {stack_dir}")
        return False

    # Temporarily overlay env_overrides onto os.environ for this in-process
    # call (engine.composefile.compose_process_env reads os.environ); restore
    # after — never a permanent mutation (S8.4 / no-environ-mutation rule).
    saved: dict[str, Optional[str]] = {}
    for k, v in env.items():
        if os.environ.get(k) != v:
            saved[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        if shipped:
            result = engine.run_shipped(
                working_dir=stack_dir,
                dry_run=dry_run,
                update_cert_permission=update_cert_permission,
                compose_profiles=compose_profiles or None,
            )
        else:
            result = engine.main_execution(
                working_dir=stack_dir,
                dry_run=dry_run,
                yes=True,
                update_cert_permission=update_cert_permission,
                compose_profiles=compose_profiles or None,
            )
    except engine.ComposeError as exc:
        error(str(exc))
        return False
    except (engine.DependencyError, engine.DooDPreflightError, WorkspaceEnvError) as exc:
        # Environment/bootstrap failures are fatal to the whole run; re-raise so
        # main() maps them to exit 3.
        raise exc
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev

    return result.get("status") == "success"


def _print_deploy_summary(deployed: list[str], failed: list[str], skipped: list[str]) -> None:
    info("=" * 60)
    info("DEPLOY SUMMARY")
    info(f"  deployed: {len(deployed)}")
    for d in deployed:
        info(f"    + {d}")
    info(f"  failed:   {len(failed)}")
    for f in failed:
        info(f"    x {f}")
    info(f"  skipped:  {len(skipped)}")
    for s in skipped:
        info(f"    - {s}")
    info("=" * 60)
    if failed or skipped:
        error("deployment did not complete cleanly")
    else:
        success("all selected stacks deployed")


def action_healthcheck(
    profile: profiles_pkg.Profile,
    selection: list[dict],
) -> int:
    """--healthcheck: run the health gate over the whole selection (S7.7)."""
    info("=" * 60)
    info("HEALTHCHECK: gating all selected services (S7.7)")
    info("=" * 60)
    svc_names = [e["name"] for e in selection]
    if not svc_names:
        warn("No services selected to check")
        return 0
    health_cfg = profile.config.get("deploy", {}).get("health", {})
    timeout_s = _seconds(health_cfg.get("timeout", "30s"))
    passed, summary = run_health_gate(profile.config, svc_names, timeout_s=timeout_s)
    _print_health_summary(summary)
    if passed:
        success("health gate passed")
        return 0
    error("[S7.7] health gate failed")
    return 1


def action_healthcheck_preflight(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    *,
    strict: bool = False,
) -> int:
    """--preflight: probe healthcheck tool availability in service images.

    Reads the rendered compose file (ciu.compose.yml or docker-compose.yml) for
    each selected stack and checks whether the tools referenced in
    CMD/CMD-SHELL healthcheck.test entries exist in the declared image.

    Returns 0 (with WARNs) unless --strict is set, in which case any missing
    tool is a hard failure (exit 1). Requires Docker to be running and images
    to be available locally (pull or already present).
    """
    from .config_constants import CIU_COMPOSE_OUTPUT, SHIPPED_COMPOSE
    from .deploy_pkg.health import preflight_probe

    info("=" * 60)
    info("PREFLIGHT: probing healthcheck tool availability in images")
    info("=" * 60)

    compose_paths: list[Path] = []
    for entry in selection:
        stack_dir = (repo_root / entry["path"]).resolve()
        for fname in (CIU_COMPOSE_OUTPUT, SHIPPED_COMPOSE):
            cp = stack_dir / fname
            if cp.exists():
                compose_paths.append(cp)
                info(f"  found: {entry['path']}/{fname}")
                break
        else:
            warn(f"  no compose file in {entry['path']} — run 'ciu render' first")

    if not compose_paths:
        warn("No rendered compose files found. Run 'ciu render' before --preflight.")
        return 0

    warnings = preflight_probe(compose_paths, warn_fn=warn, info_fn=info)

    if not warnings:
        success("Preflight probe passed — all healthchecks reference available tools")
        return 0

    count = len(warnings)
    if strict:
        error(f"Preflight probe: {count} issue(s) found (--strict → exit 1)")
        return 1
    warn(f"Preflight probe: {count} potential issue(s). Use --strict to fail the build.")
    return 0


def _matching_containers(config: dict, *, all_states: bool = False) -> list[str]:
    """Return containers whose name matches ``^{project}-{env}-`` (S7.8).

    Uses ``docker ps --filter name=`` (substring) then re-filters in Python with
    an anchored regex, so unrelated projects sharing a substring are excluded.

    With ``all_states=True`` the listing adds ``-a`` so **exited** containers are
    included — required for teardown (``clean``), where an exited one-shot
    init/sidecar (``*-vault-init``, ``Exited (0)``) is invisible to a plain
    ``docker ps`` yet still pins the project's named volumes (CIU-3, S6.4).
    Callers that only want running containers (``--stop``) keep the default.
    """
    deploy_cfg = config.get("deploy", {})
    project = deploy_cfg.get("project_name")
    env_tag = deploy_cfg.get("environment_tag")
    if not project or not env_tag:
        raise ValueError("[ERROR] deploy.project_name/environment_tag not set in config")
    prefix = f"{project}-{env_tag}-"
    anchored = re.compile(rf"^{re.escape(prefix)}")
    cmd = ["ps"]
    if all_states:
        cmd.append("-a")
    cmd += ["--filter", f"name={prefix}", "--format", "{{.Names}}"]
    try:
        result = procutil.docker(cmd, check=False)
    except FileNotFoundError as exc:
        raise ValueError(f"[ERROR] docker not available: {exc}") from exc
    if result.returncode != 0:
        warn(f"docker ps failed: {result.stderr}")
        return []
    return [
        n.strip()
        for n in (result.stdout or "").splitlines()
        if n.strip() and anchored.match(n.strip())
    ]


def action_stop(config: dict) -> int:
    """--stop: stop all project containers (volumes preserved) — S7.8 / B4.

    Anchored prefix match via ``docker ps --filter name=`` + a Python regex
    re-filter, then ONE batched ``docker stop c1 c2 ...``. No infra_containers
    NameError (the v1 B4 path is gone).
    """
    info("=" * 60)
    info("STOP: stopping project containers (volumes preserved)")
    info("=" * 60)
    containers = _matching_containers(config)
    if not containers:
        info("No matching containers running")
        return 0
    info(f"Stopping {len(containers)} container(s): {', '.join(containers)}")
    try:
        result = procutil.docker(["stop", *containers], check=False)
    except FileNotFoundError as exc:
        error(f"docker not available: {exc}")
        return 1
    if result.returncode != 0:
        error(f"docker stop failed: {result.stderr}")
        return 1
    success(f"stopped {len(containers)} container(s)")
    return 0


def action_clean(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    *,
    ignore_errors: bool,
) -> int:
    """--clean: stop+remove containers, remove project-prefixed volumes, reset stacks.

    Lean reimplementation of v1 cleanup (S6.4 semantics, COMPOSE_PROFILES='*'
    for down-with-profiles instead of v1's hardcoded 'full,pgadmin'):
      1. stop + remove project containers (anchored, S7.8);
      2. per-stack engine.reset_service (down -v via overlay + vol-*/rendered
         cleanup, B14 stack-dir scoped);
      3. remove project-prefixed named volumes in a single ls pass + one rm batch.
    Network removal is NOT performed (v1 had no explicit --clean-networks; the
    network is left in place).
    """
    info("=" * 60)
    info("CLEAN: removing containers, volumes, and rendered artifacts")
    info("=" * 60)
    config = profile.config
    rc = 0

    # Step 1: stop + remove project containers (anchored). all_states=True so an
    # exited one-shot init/sidecar (e.g. *-vault-init) is removed too — otherwise
    # it pins the project's named volumes through teardown (CIU-3, S6.4).
    containers = _matching_containers(config, all_states=True)
    if containers:
        info(f"Removing {len(containers)} container(s): {', '.join(containers)}")
        result = procutil.docker(["rm", "-f", *containers], check=False)
        if result.returncode != 0:
            warn(f"docker rm failed: {result.stderr}")

    # Step 2: per-stack reset (down -v + vol-*/rendered), COMPOSE_PROFILES='*'.
    rendered = render_selected_stacks(repo_root, profile, selection)
    saved_profiles = os.environ.get("COMPOSE_PROFILES")
    os.environ["COMPOSE_PROFILES"] = "*"
    try:
        for entry in selection:
            rel = entry["path"]
            stack_dir = (repo_root / rel).resolve()
            if not stack_dir.is_dir():
                continue
            merged = config_model.deep_merge(config, rendered[rel])
            try:
                engine.reset_service(merged, stack_dir, assume_yes=True, repo_root=repo_root)
            except Exception as exc:  # noqa: BLE001 — clean is best-effort per stack
                error(f"reset failed for {rel}: {exc}")
                rc = 1
                if not ignore_errors:
                    break
    finally:
        if saved_profiles is None:
            os.environ.pop("COMPOSE_PROFILES", None)
        else:
            os.environ["COMPOSE_PROFILES"] = saved_profiles

    # Step 3: remove project-prefixed named volumes (single ls + one rm batch).
    survivors = _remove_project_volumes(config)

    # Step 4: enforce the S6.4 post-clean invariant — zero project containers AND
    # zero project volumes remain. A surviving volume is an ERROR (not a warning):
    # it almost always means a container still references it, exactly the failure
    # that silently left stale Vault/Postgres state behind before (CIU-3).
    # Degrade gracefully if docker became unavailable mid-clean so the action
    # still returns its own typed result instead of escaping as an exception.
    try:
        remaining_containers = _matching_containers(config, all_states=True)
    except ValueError as exc:
        warn(f"post-clean container check skipped (docker unavailable): {exc}")
        remaining_containers = []
    if remaining_containers:
        error(
            f"post-clean invariant violated (S6.4): {len(remaining_containers)} "
            f"project container(s) remain: {', '.join(remaining_containers)}"
        )
        rc = 1
    if survivors:
        error(
            f"post-clean invariant violated (S6.4): {len(survivors)} project "
            f"volume(s) survived removal: {', '.join(survivors)} — most likely "
            "still referenced by a container that was not torn down"
        )
        rc = 1

    if rc == 0:
        success("clean complete")
    else:
        error("clean completed with errors")
    return rc


def _list_project_volumes(config: dict) -> list[str]:
    """Return docker volume names matching ``{project}-{env}-*`` (or [] if none/
    no project naming / docker unavailable)."""
    deploy_cfg = config.get("deploy", {})
    project = deploy_cfg.get("project_name")
    env_tag = deploy_cfg.get("environment_tag")
    if not project or not env_tag:
        return []
    prefix = f"{project}-{env_tag}-"
    try:
        result = procutil.docker(["volume", "ls", "--format", "{{.Name}}"], check=False)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [
        v.strip()
        for v in (result.stdout or "").splitlines()
        if v.strip().startswith(prefix)
    ]


def _remove_project_volumes(config: dict) -> list[str]:
    """Remove docker volumes named ``{project}-{env}-*`` (single ls + rm batch).

    Returns the list of volumes that **survived** removal (empty = fully clean).
    ``docker volume rm`` only warns on failure here; the caller re-checks and
    treats survivors as a hard error so a "volume is in use" no longer passes
    silently and leaves stale state behind (CIU-3, S6.4 post-clean invariant).
    """
    vols = _list_project_volumes(config)
    if not vols:
        return []
    info(f"Removing {len(vols)} project volume(s): {', '.join(vols)}")
    rm = procutil.docker(["volume", "rm", *vols], check=False)
    if rm.returncode != 0:
        warn(f"docker volume rm reported errors: {rm.stderr}")
    # Re-list: rm may have partially failed (a volume pinned by a surviving
    # container). Survivors are reported as an error by action_clean.
    return _list_project_volumes(config)


def action_build(repo_root: Path, selection: list[dict], *, use_cache: bool) -> int:
    """--build: thin ``docker buildx bake`` invocation over selected targets.

    Targets are the final path component of services under applications/ or
    tools/ (v1 rule). No selected targets → bake 'all'. Kept thin (the v1
    behaviour); ``--build-no-cache`` toggles cache.
    """
    info("=" * 60)
    info(f"BUILD: docker buildx bake (cache={'on' if use_cache else 'off'})")
    info("=" * 60)
    targets = collect_bake_targets_from_selection(selection)
    cmd = ["buildx", "bake", *(targets or ["all"]), "--load"]
    if not use_cache:
        cmd.append("--no-cache")
    info(f"Running: docker {' '.join(cmd)}")
    try:
        result = procutil.docker(cmd, capture=False, check=False)
    except FileNotFoundError as exc:
        error(f"docker not available: {exc}")
        return 1
    if result.returncode != 0:
        error("docker buildx bake failed")
        return 1
    success("build complete")
    return 0


# ===========================================================================
# list-profiles / list-phases (S7.4 / S7.1, J)
# ===========================================================================


def action_list_phases(config: dict) -> int:
    """--list-phases: print numerically-ordered phases with service counts (S7.1)."""
    phases_cfg = config.get("deploy", {}).get("phases", {})
    control = config.get("deploy", {}).get("control", {})
    info("Deployment phases (numeric order, S7.1):")
    try:
        ordered = phases_pkg.ordered_phases(phases_cfg)
    except ValueError as exc:
        error(str(exc))
        return 2
    if not ordered:
        info("  (none defined)")
        return 0
    for _num, key, data in ordered:
        services = data.get("services", [])
        enabled = [s for s in services if phases_pkg.service_enabled(s, control) and s.get("path")]
        name = data.get("name", key)
        info(f"  {key} ({name}): {len(enabled)} enabled service(s)")
        for s in enabled:
            label = s.get("name") or Path(s.get("path", "")).name
            info(f"      - {label} [{s.get('path')}]")
    return 0


def action_list_profiles(config: dict) -> int:
    """--list-profiles: print profiles with phases/stacks/compose_profiles/topology (S7.4)."""
    profiles_table = config.get("deploy", {}).get("profiles", {})
    info("Host profiles (S7.4):")
    if not profiles_table:
        info("  (none defined — the default profile deploys all phases)")
        return 0
    for name in sorted(profiles_table):
        pdata = profiles_table[name] or {}
        info(f"  {name}:")
        info(f"      phases:           {pdata.get('phases', [])}")
        info(f"      stacks:           {pdata.get('stacks', [])}")
        info(f"      compose_profiles: {pdata.get('compose_profiles', [])}")
        topo = pdata.get("topology_overrides", {})
        info(f"      topology_overrides keys: {sorted(topo.keys()) if isinstance(topo, dict) else topo}")
    return 0


# ===========================================================================
# generate-env (S2.8 bootstrap)
# ===========================================================================


def action_generate_env(define_root: Optional[Path], dir_hint: Path) -> int:
    """--generate-env: the single bootstrap entry point (S2.8). Returns exit code."""
    info("Generating ciu.env (S2.8 bootstrap)...")
    env_root = resolve_env_root(
        start_dir=dir_hint,
        define_root=define_root,
        defaults_filename=GLOBAL_CONFIG_DEFAULTS,
    )
    env_path = bootstrap_env_init(env_root)
    success(f"Generated {env_path}")
    return 0


# ===========================================================================
# CLI parsing (S10.2)
# ===========================================================================


def build_action_sequence(argv: list[str]) -> list[str]:
    """Ordered action list from CLI args (S10.2 retained action surface).

    *argv* is the argument list WITHOUT the program name (i.e. ``sys.argv[1:]``).
    Recognised action flags (in the order they appear): --deploy, --stop,
    --clean, --healthcheck, --render-toml, --build, --build-no-cache,
    --list-phases, --list-profiles. Unknown args are ignored here (argparse
    validates them). Returns canonical action names.
    """
    action_flags = {
        "--deploy": "deploy",
        "--stop": "stop",
        "--clean": "clean",
        "--healthcheck": "healthcheck",
        "--preflight": "preflight",
        "--render-toml": "render_toml",
        "--list-phases": "list_phases",
        "--list-profiles": "list_profiles",
    }
    return [action_flags[arg] for arg in argv if arg in action_flags]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Argparse for ``ciu-deploy`` (S10.2). NOTE: --groups is NOT defined (S7.5)."""
    parser = argparse.ArgumentParser(
        description=f"CIU-deploy {get_cli_version()}: deployment orchestrator (S7).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Host profiles (--profile) select which stacks run on THIS host (S7.4); compose
profiles (compose_profiles in a profile/service) select which services inside a
stack are activated. They are distinct (S7.5a).

Examples:
  ciu-deploy --deploy                       # deploy the active profile's phases
  ciu-deploy --profile core_infra --deploy  # deploy a named host profile
  ciu-deploy --phases 1,2 --deploy          # restrict to phase_1, phase_2
  ciu-deploy --deploy --healthcheck         # deploy then gate health (S7.7)
  ciu-deploy --render-toml                  # render global + selected stack TOML
  ciu-deploy --stop                         # stop project containers
  ciu-deploy --clean -y                     # remove containers/volumes/rendered
  ciu-deploy --list-profiles                # show host profiles
  ciu-deploy --list-phases                  # show numbered phases
""",
    )

    actions = parser.add_argument_group("Actions")
    actions.add_argument("--deploy", action="store_true", help="Deploy selected stacks (default)")
    actions.add_argument("--stop", action="store_true", help="Stop project containers (preserve volumes)")
    actions.add_argument("--clean", action="store_true", help="Remove containers, volumes, rendered artifacts")
    actions.add_argument("--healthcheck", action="store_true", help="Run the health gate over the selection (S7.7)")
    actions.add_argument("--preflight", action="store_true",
                         help="Probe healthcheck tool availability in service images (ciu health --preflight)")
    actions.add_argument("--render-toml", dest="render_toml", action="store_true",
                         help="Render global + selected stack configs and stop (S8.3 step 3)")
    actions.add_argument("--list-phases", dest="list_phases", action="store_true",
                         help="List numbered phases with service counts (S7.1)")
    actions.add_argument("--list-profiles", dest="list_profiles", action="store_true",
                         help="List host profiles (replaces v1 --list-groups) (S7.4)")

    control = parser.add_argument_group("Control")
    control.add_argument("--profile", default=None, metavar="NAME",
                         help="Host profile to activate (default: env CIU_HOST_PROFILE) (S7.5)")
    control.add_argument("--phases", default=None, metavar="N,M",
                         help="Comma-separated phase numbers to restrict to (e.g. 1,2)")
    control.add_argument("-y", "--yes", action="store_true",
                         help="Non-interactive: auto-confirm prompts")
    control.add_argument("--ignore-errors", dest="ignore_errors", action="store_true",
                         help="Continue past failures (final exit is still 1) (S7.3)")
    control.add_argument("--dry-run", dest="dry_run", action="store_true",
                         help="Run the pipeline but skip docker compose up (S8.3)")
    control.add_argument("--root-folder", "--define-root", dest="define_root", type=Path, default=None,
                         metavar="PATH", help="Repository root override (S1.1)")
    control.add_argument("--update-cert-permission", dest="update_cert_permission", action="store_true",
                         help="Update Let's Encrypt cert permissions (requires root)")
    control.add_argument("--strict", action="store_true",
                         help="Preflight: treat any missing-tool warning as a hard failure (exit 1)")
    control.add_argument("--version", action="version", version=f"ciu-deploy {get_cli_version()}")

    return parser.parse_args(argv)


def _parse_phase_filter(raw: Optional[str]) -> Optional[set[str]]:
    """Parse ``--phases 1,2,10`` into {'phase_1','phase_2','phase_10'} (S7.1).

    Raises ValueError (→ exit 2) on a non-numeric entry.
    """
    if not raw:
        return None
    keys: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(
                f"[ERROR] invalid --phases entry {part!r}; use comma-separated "
                "numbers (e.g. 1,2,10)"
            )
        keys.add(f"phase_{int(part)}")
    return keys or None


# ===========================================================================
# main() — single exit point; maps to S10.3 via engine._exit_code_for
# ===========================================================================


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(raw)
    except SystemExit as exc:  # argparse error → S10.3 exit 2
        return 2 if exc.code not in (0, None) else 0

    try:
        return _run(args, raw)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — single exit point (S7.3 / S10.3)
        error(str(exc))
        return engine._exit_code_for(exc)


def _run(args: argparse.Namespace, raw: list[str]) -> int:
    """Drive the requested actions. Returns an int (no sys.exit; S7.3)."""
    # --- env bootstrap (S2 / S2.8) ---
    define_root = Path(args.define_root).resolve() if args.define_root else None

    bootstrap_workspace_env(
        start_dir=Path.cwd(),
        define_root=define_root,
        defaults_filename=GLOBAL_CONFIG_DEFAULTS,
        generate_env=False,
        update_cert_permission=args.update_cert_permission,
        required_keys=REQUIRED_KEYS_CORE,
    )

    repo_root = resolve_repo_root(define_root)

    standalone_root = detect_standalone_root(repo_root)
    if standalone_root:
        env_repo_root = Path(os.environ.get("REPO_ROOT", "")).resolve()
        if env_repo_root and env_repo_root != standalone_root:
            raise WorkspaceEnvError(
                "[S1.2] standalone_root is true but REPO_ROOT does not match. "
                f"Expected: {standalone_root}, got: {env_repo_root}."
            )

    # --- config + profile (S3.3 / S7.4 / S7.5) ---
    global_cfg = load_global_config(repo_root)
    profile = resolve_profile(global_cfg, args.profile)
    info(f"Active host profile: {profile.name or '(default — all phases)'}")

    cli_phases = _parse_phase_filter(args.phases)
    selection = build_selection(profile, cli_phases)

    # --- action ordering (S10.2): explicit order, else default deploy ---
    actions = build_action_sequence(raw)
    if not actions:
        actions = ["deploy"]
        info("No action specified; defaulting to --deploy")
    # Health gate after a successful deploy phase when --healthcheck is also
    # requested alongside --deploy.
    health_after_phase = "deploy" in actions and "healthcheck" in actions

    rc = 0
    deploy_needs_preflight = any(a in ("deploy",) for a in actions)

    # Vault + registry preflight BEFORE any phase runs (S7.6 / S7.9), only for
    # actions that actually start stacks.
    # vault_preflight / registry_preflight now raise on failure (ValueError →
    # exit 2 via engine._exit_code_for; VaultError → exit 1) — the outer
    # try/except in main() catches and maps them.
    if deploy_needs_preflight and not args.dry_run:
        rendered = render_selected_stacks(repo_root, profile, selection)
        vault_preflight(repo_root, profile, selection, rendered)
        registry_preflight(profile.config)
        # Ensure the workspace network exists before compose (devcontainer no-op
        # off-devcontainer); reads the profile-resolved auto_connect setting.
        ensure_workspace_network(
            auto_connect=profile.config.get("ciu", {}).get("auto_connect_network", True)
        )
    elif deploy_needs_preflight and args.dry_run:
        # Dry-run still validates misplaced directives + vault ordering (no token
        # I/O is forced because the engine won't start anything), matching S8.3
        # "everything else runs" intent.
        rendered = render_selected_stacks(repo_root, profile, selection)
        vault_preflight(repo_root, profile, selection, rendered)

    for action in actions:
        info(f">>> action: {action}")
        if action == "render_toml":
            ac = action_render_toml(repo_root, profile, selection)
        elif action == "list_phases":
            ac = action_list_phases(profile.config)
        elif action == "list_profiles":
            ac = action_list_profiles(profile.config)
        elif action == "stop":
            ac = action_stop(profile.config)
        elif action == "clean":
            ac = action_clean(repo_root, profile, selection, ignore_errors=args.ignore_errors)
        elif action == "healthcheck":
            ac = action_healthcheck(profile, selection)
        elif action == "preflight":
            ac = action_healthcheck_preflight(
                repo_root, profile, selection,
                strict=getattr(args, "strict", False),
            )
        elif action == "deploy":
            ac = action_deploy(
                repo_root,
                profile,
                selection,
                dry_run=args.dry_run,
                ignore_errors=args.ignore_errors,
                health_after_phase=health_after_phase,
                update_cert_permission=args.update_cert_permission,
            )
        else:  # pragma: no cover — build_action_sequence only yields known names
            warn(f"unknown action: {action}")
            ac = 0

        if ac != 0:
            rc = ac
            if not args.ignore_errors:
                return rc

    return rc


def _other_actions_requested(args: argparse.Namespace) -> bool:
    """True when any explicit action was requested."""
    return any(
        (
            args.deploy,
            args.stop,
            args.clean,
            args.healthcheck,
            args.render_toml,
            args.list_phases,
            args.list_profiles,
        )
    )


# ===========================================================================
# Test-facing helpers (kept stable for the v2 test suite)
# ===========================================================================


def filter_deployment_phases(
    deployment_phases: list[dict], selected_phase_keys: Optional[set[str]]
) -> list[dict]:
    """Filter phase dicts by selected phase keys (each dict has a 'key')."""
    if not selected_phase_keys:
        return deployment_phases
    return [p for p in deployment_phases if p.get("key") in selected_phase_keys]


def collect_bake_targets_from_selection(selection: list[dict]) -> list[str]:
    """Bake targets from a selection: final path component under applications/ or tools/."""
    targets: set[str] = set()
    for entry in selection:
        parts = Path(entry["path"]).parts
        if parts and parts[0] in {"applications", "tools"}:
            targets.add(parts[-1])
    return sorted(targets)


def collect_bake_targets_from_phases(phases: list[dict]) -> list[str]:
    """Bake targets from phase dicts (v1-shaped: {key, services:[{path, enabled}]})."""
    targets: set[str] = set()
    for phase in phases:
        for service in phase.get("services", []):
            if not service.get("enabled", True):
                continue
            path = service.get("path")
            if not path:
                continue
            parts = Path(path).parts
            if parts and parts[0] in {"applications", "tools"}:
                targets.add(parts[-1])
    return sorted(targets)


if __name__ == "__main__":
    raise SystemExit(main())
