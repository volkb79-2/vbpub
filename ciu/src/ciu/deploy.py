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
  - governance slice checks .... governance.check_slice_unit (D-G9 / S15.8)
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
  - S15.G9-1 governance slice preflight: a named, non-default cgroup slice
          must exist on the host (systemd) before any phase runs, else the
          container would silently run unbounded (D-G9 check 1).

Discipline (S7.3 / S8.4): no ``sys.exit`` inside actions — every action
returns an int; ``main()`` is the single exit point and maps exceptions to the
S10.3 taxonomy via engine._exit_code_for. No ``os.environ`` mutation (profile
env_overrides flow into the env dict handed to stacks), no ``eval``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config_model
from . import engine
from . import governance as governance_mod
from . import procutil
from . import warn_policy
from . import worktree as worktree_pkg
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
    enforce_standalone_root,
    ensure_workspace_network,
    parse_workspace_env,
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


def resolve_profiles(global_cfg: dict, names: Optional[list[str]]) -> profiles_pkg.Profile:
    """Resolve profiles: CLI, then worktree-local config, then legacy env."""
    if not names:
        ciu = global_cfg.get("ciu", {})
        instance = ciu.get("instance", {}) if isinstance(ciu, dict) else {}
        configured = instance.get("service_profiles") if isinstance(instance, dict) else None
        if configured is not None:
            if not isinstance(configured, list) or not configured or any(
                not isinstance(item, str) or not item.strip() for item in configured
            ):
                raise ValueError(
                    "[S16] ciu.instance.service_profiles must be a non-empty string array"
                )
            names = [item.strip() for item in configured]
            if len(set(names)) != len(names):
                raise ValueError(
                    "[S16] ciu.instance.service_profiles contains a duplicate"
                )
    return profiles_pkg.resolve_profiles(global_cfg, names)


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

    # profile.stacks → appended last, ONE pseudo-phase PER STACK in list order
    # (S7.4). Distinct phase keys make the deploy loop's per-phase provisioning
    # probe run just-in-time for each stack — a shared pseudo-phase probed all
    # extra stacks' requires up-front, which can never pass when one profile
    # stack provides what a later one requires (e.g. db-core → db-init on a
    # greenfield up). Profile stack ORDER is therefore meaningful.
    selected_paths = {entry["path"] for entry in selection}
    extra_index = 0
    for stack_path in profile.extra_stacks:
        # A profile stack supplements the numbered phases; it must not cause
        # the same stack to run twice when a selected phase already owns it.
        # Profile composition deduplicates across profiles, but retain this
        # defensive boundary for manually-constructed Profile values too.
        if stack_path in selected_paths:
            continue
        selection.append(
            {
                # Large increasing ints (not a shared float('inf')): they sort
                # after every numbered phase AND stay strictly ordered among
                # themselves, so the S7.6 vault-ordering check ("vault in an
                # EARLIER phase") works inside a stacks-only profile too.
                "phase_num": 1_000_000 + extra_index,
                "phase_key": f"{EXTRA_STACKS_KEY}:{extra_index}",
                "path": stack_path,
                "name": Path(stack_path).name,
                "service": {"path": stack_path, "name": Path(stack_path).name, "enabled": True},
            }
        )
        selected_paths.add(stack_path)
        extra_index += 1
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
    ciu_context: Optional[dict] = None,
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
            stack_dir,
            global_config=profile.config,
            preserve_state=True,
            ciu_context=ciu_context,
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


def _producer_profile_stack_paths(config: dict, pdata: dict) -> set[str]:
    """Every stack path a profile deploys: its `stacks` list plus the services
    of its phases (S13.6 — producer presence is about DEPLOYED STACKS, not
    the profile label: an alias profile deploying the same stacks satisfies
    the producer just as well)."""
    paths: set[str] = set()
    stacks = pdata.get("stacks", [])
    if isinstance(stacks, list):
        for s in stacks:
            if isinstance(s, str) and s:
                paths.add(s)
    phases = pdata.get("phases", [])
    if isinstance(phases, list):
        phase_table = config.get("deploy", {}).get("phases", {})
        for pk in phases:
            if not isinstance(pk, str):
                continue
            phase = phase_table.get(pk, {})
            services = phase.get("services", []) if isinstance(phase, dict) else []
            for svc in services or []:
                if isinstance(svc, dict) and svc.get("path"):
                    paths.add(svc["path"])
    return paths


def producer_preflight(
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
) -> None:
    """S13.4 / CIU-42 — refuse a partial selection that excludes a declared producer.

    A stack's ASK_VAULT directive may declare ``produced_by = "<profile>"``:
    the value at its Vault path is provisioned by THAT profile's deployment
    (e.g. an authentik hook writing its bootstrap token), not by this stack.
    When the invocation selects named profiles and a declared producer is not
    among them, the consuming stack can only fail later at materialization
    with the bare path name ([S4.2]) — so this preflight refuses UPFRONT,
    naming the producer profile, the path, and both remedies (deploy the
    producer profile, or seed the path out-of-band).

    Producer PRESENCE is judged by deployed stacks, not the profile label
    (adversarial review): the producer passes when any stack it deploys (its
    `stacks` list or its phases' services) is in the selection — so an alias
    profile deploying the same stacks satisfies it, and a `--phases` filter
    that narrowed the producer's stacks out still refuses.

    The default selection (no named profile = all phases) deploys every
    stack's provisioning, so there is nothing to refuse. An unknown producer
    profile name is a configuration error even under the default selection:
    a typo'd declaration must fail loudly, never silently protect nothing.
    ALL violations are reported together — one run names everything.
    """
    config = profile.config
    profiles_table = config.get("deploy", {}).get("profiles", {})
    selected_names = {
        n.strip() for n in (profile.name or "").split(",") if n.strip()
    }
    named_selection = bool(selected_names)
    selection_paths = {entry["path"] for entry in selection}

    refusals: list[str] = []
    for entry in selection:
        rel = entry["path"]
        if rel not in rendered:
            continue
        # Shipped stacks have no CIU secrets surface (S8.6).
        if phases_pkg.service_shipped(entry.get("service", {})):
            continue
        stack_cfg = rendered[rel]
        root_key = config_model.validate_stack_shape(stack_cfg)
        merged = config_model.deep_merge(config, stack_cfg)
        for spec in secret_directives.discover(root_key, merged):
            if spec.kind != "ASK_VAULT" or not spec.produced_by:
                continue
            producer = spec.produced_by
            pdata = profiles_table.get(producer)
            if not isinstance(pdata, dict):
                refusals.append(
                    f"  stack '{rel}': ASK_VAULT secret '{spec.name}' declares "
                    f"produced_by = {producer!r}, which is not a defined "
                    f"profile in [deploy.profiles]. Defined profiles: "
                    f"{', '.join(sorted(profiles_table)) or '(none)'}."
                )
                continue
            if named_selection and producer not in selected_names:
                producer_paths = _producer_profile_stack_paths(config, pdata)
                if producer_paths & selection_paths:
                    # A stack this producer deploys IS in the selection —
                    # the provisioning will run; the profile label differs.
                    continue
                refusals.append(
                    f"  stack '{rel}': ASK_VAULT secret '{spec.name}' reads "
                    f"Vault path '{spec.locator}', which is provisioned by "
                    f"profile '{producer}' — none of its stacks "
                    f"({', '.join(sorted(producer_paths)) or '(none)'}) are "
                    f"in your selection ({profile.name}). Deploy the producer "
                    f"profile or its stacks, or seed the path out-of-band "
                    "before deploying."
                )

    if refusals:
        raise ValueError(
            "[ERROR] Provisioning producers missing from the selection "
            "(S13.6):\n" + "\n".join(refusals)
        )


# ===========================================================================
# Provisioning preflight (requires/provides graph + live probe)
# ===========================================================================


def provisioning_preflight(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
    *,
    no_preflight: bool = False,
    lint: bool = True,
    probe: bool = True,
) -> None:
    """Check requires/provides graph and probe live state for each stack's requires.

    Raises ValueError with a precise message on failure.
    Skipped when no_preflight=True (break-glass) or when no stack has requires/provides.

    ``lint`` runs the static graph check (needs the full selection — run ONCE
    up-front). ``probe`` runs the live state check; run it PER-PHASE by passing
    that phase's entries as ``selection`` (with ``lint=False``), so a stack's
    requires are probed only when its phase is about to deploy and the providers
    in earlier phases are already up. (A once-up-front live probe can never pass on
    a greenfield ``ciu up`` because the providers deploy later in the same run.)
    """
    if no_preflight:
        info("[INFO] --no-preflight: skipping provisioning preflight")
        return

    from . import provisioning as provisioning_pkg

    # Collect requires/provides from all rendered stacks
    stacks: dict[str, dict] = {}
    for entry in selection:
        rel = entry["path"]
        if rel not in rendered:
            continue
        stack_cfg = rendered[rel]
        # requires/provides live inside the root key table
        root_key = config_model.validate_stack_shape(stack_cfg)
        root_section = stack_cfg.get(root_key, {})
        requires = root_section.get("requires", [])
        provides = root_section.get("provides", [])
        if requires or provides:
            # Reject malformed typed refs early (spec §2 grammar). ValueError
            # propagates → exit 2 via engine._exit_code_for.
            config_model.validate_stack_provisioning(stack_cfg, source=rel)
            stacks[rel] = {"requires": requires, "provides": provides}

    if not stacks:
        return  # no stack uses requires/provides — skip entirely

    # Graph lint (static; needs the full selection — run once up-front)
    if lint:
        lint_errors = provisioning_pkg.lint_graph(stacks)
        if lint_errors:
            raise ValueError(
                "[ERROR] Provisioning graph lint failed:\n"
                + "\n".join(f"  {e}" for e in lint_errors)
            )
        info("[INFO] Provisioning graph lint passed")

    # Live probing: probe each stack's requires (per-phase when called from the
    # deploy loop with that phase's entries as `selection`).
    if probe:
        config = profile.config
        all_failed: list[str] = []
        for entry in selection:
            rel = entry["path"]
            if rel not in stacks:
                continue
            requires = stacks[rel].get("requires", [])
            for ref in requires:
                result = provisioning_pkg.probe_ref(ref, config, repo_root)
                if not result.satisfied:
                    all_failed.append(f"  stack '{rel}' requires '{ref}': {result.reason}")

        if all_failed:
            raise ValueError(
                "[ERROR] Provisioning preflight failed — unsatisfied requirements:\n"
                + "\n".join(all_failed)
            )
        info("[INFO] Provisioning preflight passed")


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
# Image-revision preflight (S17, CIU-18) — fail CLOSED on stale artifacts
# ===========================================================================


@dataclass(frozen=True)
class ContainerProvenance:
    """One running container's provenance verdict (S17.3, CIU-20).

    ``status`` closed vocabulary (schema 2, CIU-39): ``match`` (baked
    revision equals the commit under test), ``mismatch`` (differs — including
    a DECLARED vendor image running at a different reference, i.e. vendor
    drift), ``unlabelled`` (no revision label and not declared), or
    ``vendor-pinned`` (the running image reference equals a declared
    ``[deploy.provenance] vendor_images`` entry).
    """

    name: str
    image: str
    labelled_revision: Optional[str]  # None (JSON null) when unknown, never ""
    status: str  # "match" | "mismatch" | "unlabelled" | "vendor-pinned"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "image": self.image,
            "labelled_revision": self.labelled_revision,
            "status": self.status,
        }


@dataclass(frozen=True)
class ProvenanceResult:
    """S17.3/CIU-20 — the machine-readable provenance verdict.

    Always built (never bare ``None``); :func:`verify_running_provenance` never
    raises. ``_provenance`` (cli.py) is the only place that turns this into
    prose/raise/warn behaviour. ``to_dict`` field order is the wire order.
    """

    schema_version: int
    instance: Optional[str]
    commit_under_test: Optional[str]
    tree_state: Optional[str]
    containers: Optional[list[ContainerProvenance]]
    overall: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance": self.instance,
            "commit_under_test": self.commit_under_test,
            "tree_state": self.tree_state,
            "containers": (
                None if self.containers is None
                else [c.to_dict() for c in self.containers]
            ),
            "overall": self.overall,
        }


def _image_reference_name(ref: str) -> str:
    """The CANONICAL NAME portion of an image reference (no tag, no digest).

    ``hashicorp/vault:1.15`` → ``hashicorp/vault``; ``vault@sha256:...`` →
    ``vault`` (canonicalized to ``docker.io/library/vault``); registry ports
    (``localhost:5000/img:tag``) are handled by splitting off the tag only
    when the colon sits in the LAST path segment. Canonicalization mirrors
    Docker's own defaults so declaration and running spellings agree even
    when written differently (adversarial review: ``docker.io/library/nginx``
    vs ``nginx``, uppercase registry hosts).
    """
    without_digest = ref.split("@", 1)[0]
    last = without_digest.rsplit("/", 1)[-1]
    if ":" in last:
        tag_len = len(last.rsplit(":", 1)[1])
        name = without_digest[: len(without_digest) - tag_len - 1]
    else:
        name = without_digest

    first, sep, rest = name.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        # explicit registry host — case-insensitive by Docker's rules; the
        # implicit library/ namespace applies on Docker Hub alone
        if first.lower() == "docker.io" and "/" not in rest:
            return "docker.io/library/" + rest
        return f"{first.lower()}/{rest}"
    # no registry host → Docker Hub defaults
    if "/" not in name:
        return f"docker.io/library/{name}"
    return name


def _normalized_image_reference(ref: str) -> str:
    """Full-reference normalization for EXACT pin comparison: the name part
    canonicalizes as above; the tag/digest survive verbatim (tags are
    case-sensitive)."""
    without_digest, digest_sep, digest = ref.partition("@")
    last = without_digest.rsplit("/", 1)[-1]
    if ":" in last:
        tag_with_colon = without_digest[len(without_digest) - len(last.rsplit(":", 1)[1]) - 1:]
        name = without_digest[: -len(tag_with_colon)]
    else:
        name, tag_with_colon = without_digest, ""
    result = _image_reference_name(ref) + tag_with_colon
    if digest_sep:
        result += f"@{digest}"
    return result


def verify_running_provenance(
    project_prefix: str, *, vendor_images: Optional[list[str]] = None
) -> ProvenanceResult:
    """S17.2/S17.3 — build the machine-readable provenance verdict (CIU-20).

    **This is a TEST-time check, not a deploy-time one, and the distinction is
    the whole point.** At deploy time the question is "did I remember to bake?",
    which the operator discovers immediately. The question that produces bad
    EVIDENCE is asked later, against a stack that is already up: *does this
    passing integration run describe the code I think it does?* By then the
    containers are running, so the thing to inspect is the image each RUNNING
    container actually has — not what a compose file declares it would use.

    `bake` stamps ``org.opencontainers.image.revision`` (S17.1), which makes
    provenance *visible*; this makes it *binding*. Without it a live result is
    evidence about an unknown artifact — it reads as a fact about the code while
    being a fact about whichever image happened to be running. dstdns's own
    policy (AGENTS.md §4.1a) already classes that as a defect, with nothing
    enforcing it.

    Scoped to containers whose compose project starts with *project_prefix*
    (also recorded verbatim as ``instance``), so a sibling worktree instance
    (S16) — legitimately running a DIFFERENT commit — is never mistaken for
    this instance being stale.

    ALWAYS returns a :class:`ProvenanceResult` — never ``None``, and never
    raises. This function only builds the verdict; ``_provenance`` (cli.py) is
    the ONLY place that turns it into prose, a raise, or a warning, so the
    same verdict serves both the human CLI and ``--json`` machine consumers
    without ever mixing prose onto the JSON stream.

    *vendor_images* (CIU-39) is the consumer's declared vendor baseline: the
    exact image references expected to be third-party artifacts (e.g.
    ``hashicorp/vault:1.15``). A running container whose image EQUALS a
    declared entry is ``vendor-pinned`` — expected to be unlabelled, judged
    by reference equality, never by this repo's commit. A container whose
    image NAME matches a declared entry at any OTHER reference is vendor
    drift → ``mismatch`` (the declaration vouches for one artifact; a
    different one is running). Undeclared unlabelled images stay
    ``unlabelled``. This makes ``verified-match`` reachable for deployments
    whose containers are all pinned vendor artifacts (previously pinned at
    ``not-verified-no-evidence`` forever) — and it cannot mask a forgotten
    bake: an unlabelled image nobody declared stays ``unlabelled``, so only
    an operator falsely declaring their own image as vendor escapes, which is
    auditable config.

    ``overall`` is one of six closed values, decided in order: an unresolved
    identity is handled entirely by the CLI before this function is even
    called (``refused-no-identity`` never originates here); a non-checkout
    commit (``get_git_hash() == "dev"``) is ``not-verified-unknown``; a dirty
    tree is ``not-verified-dirty``; enumeration that could not run at all
    (no docker, or ``docker ps`` failed) is ``not-verified-no-evidence`` with
    ``containers: null``; a successful enumeration with at least one
    ``mismatch`` (commit drift OR vendor drift) is ``mismatch``; one with at
    least one ``match`` or ``vendor-pinned`` and no ``mismatch`` is
    ``verified-match`` — every checked container agrees with an expectation,
    the commit under test or a declared vendor reference; anything else
    (empty, or all undeclared-unlabelled) is ``not-verified-no-evidence``
    with the (possibly empty) container list.

    Documents are emitted at schema_version 2 (CIU-39 widened the closed
    vocabularies; strict consumers refuse unknown members, fail-closed).
    """
    commit = engine.get_git_hash()

    if commit == "dev":
        return ProvenanceResult(
            schema_version=2, instance=project_prefix, commit_under_test=commit,
            tree_state="not-a-checkout", containers=None,
            overall="not-verified-unknown",
        )

    if commit.endswith("-dirty"):
        return ProvenanceResult(
            schema_version=2, instance=project_prefix, commit_under_test=commit,
            tree_state="dirty", containers=None, overall="not-verified-dirty",
        )

    raw = _running_containers(project_prefix)
    if raw is None:
        return ProvenanceResult(
            schema_version=2, instance=project_prefix, commit_under_test=commit,
            tree_state="clean", containers=None,
            overall="not-verified-no-evidence",
        )

    declared = {_normalized_image_reference(v) for v in (vendor_images or [])}
    declared_by_name = {_image_reference_name(v) for v in declared}
    containers: list[ContainerProvenance] = []
    has_match = False
    has_mismatch = False
    has_vendor_pinned = False
    for name, image in raw:
        actual = _image_revision_label(image) or None
        # A DECLARED vendor image is judged by CANONICAL REFERENCE EQUALITY
        # ONLY (Docker's own normalization: registry-host case, the implicit
        # docker.io/library/ defaults): its OCI revision label belongs to the
        # upstream build, so comparing it against OUR commit would flag every
        # correctly-pinned vendor artifact as mismatch. The label (if any) is
        # still reported verbatim. Same image NAME at a different reference =
        # the declaration's artifact was swapped → drift, a mismatch.
        normalized_image = _normalized_image_reference(image)
        if normalized_image in declared:
            status = "vendor-pinned"
            has_vendor_pinned = True
        elif _image_reference_name(image) in declared_by_name:
            status = "mismatch"
            has_mismatch = True
        elif actual is None:
            status = "unlabelled"
        elif actual == commit:
            status = "match"
            has_match = True
        else:
            status = "mismatch"
            has_mismatch = True
        containers.append(ContainerProvenance(name, image, actual, status))
    containers.sort(key=lambda c: c.name)

    if has_mismatch:
        overall = "mismatch"
    elif has_match or has_vendor_pinned:
        overall = "verified-match"
    else:
        overall = "not-verified-no-evidence"

    return ProvenanceResult(
        schema_version=2, instance=project_prefix, commit_under_test=commit,
        tree_state="clean", containers=containers, overall=overall,
    )


def _running_containers(project_prefix: str) -> Optional[list[tuple[str, str]]]:
    """``(name, image)`` for running containers in this instance's projects.

    Filtered by the compose PROJECT label, which carries the instance id — a
    sibling worktree instance (S16) legitimately runs a different commit and
    must never be reported as this instance being stale. Docker's label filter
    is exact-match only, so the prefix test is applied here.

    Returns ``None`` when enumeration could not run at all (no docker CLI, or
    ``docker ps`` failed) — distinct from ``[]``, a successful enumeration that
    found nothing. Collapsing both to ``[]`` (CIU-20's own defect) let a
    docker-less host emit ``verified-match`` with ``containers: []``: a green
    provenance document attesting nothing.
    """
    try:
        res = procutil.docker(
            ["ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Label \"com.docker.compose.project\"}}"],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if res.returncode != 0:
        return None
    out: list[tuple[str, str]] = []
    for line in (res.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, image, project = (p.strip() for p in parts)
        if project.startswith(project_prefix):
            out.append((name, image))
    return out


def _image_revision_label(image: str) -> str:
    """The image's ``org.opencontainers.image.revision``, or "" when unknown.

    Returns "" for an absent image too: a missing image is compose's problem to
    report, and inventing a mismatch verdict for it would be a wrong answer
    dressed as a safety check.
    """
    try:
        res = procutil.docker(
            ["image", "inspect", image, "--format",
             '{{index .Config.Labels "org.opencontainers.image.revision"}}'],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return ""
    if res.returncode != 0:
        return ""
    value = (res.stdout or "").strip()
    return "" if value in ("", "<no value>") else value


# ===========================================================================
# Governance slice preflight (D-G9 checks 1 & 3, S15.8/S15.16) — fail CLOSED
# ===========================================================================


def governance_slice_preflight(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
    *,
    no_preflight: bool = False,
) -> None:
    """D-G9 checks 1 & 3 — verify every explicitly-configured governance slice
    exists (S15.8) and, where a stack declares ``mem_min`` (S15.16), that the
    slice's live ``MemoryMin=`` actually meets it.

    Background (S15.8): S15 governance can inject
    ``cgroup_parent = "<name>.slice"`` into every non-exempt service. With
    the systemd cgroup driver, a slice with no static unit file is
    implicitly, transiently created by systemd on first reference — with NO
    resource limits of its own. The compose file then "looks" governed
    (``cgroup_parent`` is set) but the container runs completely unconfined
    at the slice level, silently. This preflight catches that BEFORE any
    phase starts, by asking the host's systemd whether the named slice is
    actually loaded (:func:`governance.check_slice_unit`).

    Checks every enabled stack's RESOLVED slice (:func:`governance.resolve_cgroup_parent`
    — explicit config, else the ambient ``CGROUP_PARENT_DEV_BACKGROUND``, else
    that call itself raises) — there is no more "CIU's own shipped default"
    to skip (host dev-tier cgroup governance rollout,
    nyxloom/docs/plan-resource-governance.md): governance.GOVERNANCE_DEFAULTS
    no longer hardcodes a slice name, so every resolved slice gets the same
    existence check, including whatever the ambient default resolves to —
    that used to be silently exempted here, which meant the MOST common case
    (a stack relying on the shipped default rather than naming its own slice)
    was also the one case this preflight never actually checked.

    Skipped entirely when *no_preflight* is set (the same break-glass flag
    :func:`provisioning_preflight` honors), or when the host has no
    ``systemctl`` at all — a non-systemd host cannot honor governance slices
    either way, so that is an informational note, not a CIU error.

    Raises
    ------
    ValueError
        [S15.G9-1] a systemd host is missing a named, non-default governance
        slice (S10.3 → exit 2 — a configuration/setup failure, mirroring
        :func:`registry_preflight`'s missing-`docker login` pattern), or
        [S15.16] a stack declares ``mem_min`` and the resolved slice's live
        ``MemoryMin=`` does not meet it.
    """
    if no_preflight:
        info("[INFO] --no-preflight: skipping governance slice preflight")
        return

    config = profile.config
    # slice_name -> [stack rel paths that would place a container under it]
    checked: dict[str, list[str]] = {}
    # slice_name -> max declared mem_min in bytes across stacks sharing it
    mem_min_required: dict[str, int] = {}
    mem_min_sources: dict[str, list[str]] = {}

    for entry in selection:
        rel = entry["path"]
        # Shipped stacks (S8.6) have no CIU config — nothing to resolve.
        if phases_pkg.service_shipped(entry["service"]):
            continue
        if rel not in rendered:
            continue
        try:
            root_key = config_model.validate_stack_shape(rendered[rel])
        except ValueError:
            continue
        merged = config_model.deep_merge(config, rendered[rel])
        raw_governance = governance_mod.resolve_stack_governance(
            merged.get(root_key, {}).get("governance"), config
        )
        if raw_governance is None:
            continue
        gov_cfg = governance_mod.resolve_config(raw_governance)
        if not gov_cfg.get("enabled"):
            continue
        # May raise ValueError (no hardcoded fallback) if this stack neither
        # names a slice explicitly nor picks up the ambient env var — that is
        # exactly the "misconfigured, must not deploy" case this preflight
        # exists to catch, so let it propagate.
        slice_name = governance_mod.resolve_cgroup_parent(str(gov_cfg.get("cgroup_parent") or ""))
        if not slice_name.endswith(".slice"):
            continue
        checked.setdefault(slice_name, []).append(rel)

        mem_min_raw = str(gov_cfg.get("mem_min") or "")
        if mem_min_raw:
            try:
                required = governance_mod.parse_size_to_bytes(mem_min_raw)
            except ValueError as exc:
                raise ValueError(
                    f"[S15.16] {rel}: [<root>.governance].mem_min={mem_min_raw!r} "
                    f"is not a valid size: {exc}"
                ) from exc
            mem_min_required[slice_name] = max(mem_min_required.get(slice_name, 0), required)
            mem_min_sources.setdefault(slice_name, []).append(rel)

    if not checked:
        return

    missing: list[str] = []
    missing_slice_names: set[str] = set()
    for slice_name, stacks in checked.items():
        exists, note = governance_mod.check_slice_unit(slice_name)
        if exists is None:
            info(f"[INFO] [S15.G9-1] {note}")
            return  # inconclusive/no-systemd applies to every slice equally
        if exists:
            info(f"[INFO] [S15.G9-1] governance slice OK — {note}")
        else:
            missing.append(f"  '{slice_name}' (used by: {', '.join(stacks)}) — {note}")
            missing_slice_names.add(slice_name)

    # S15.16 (D-G9 check 3) — only meaningful for slices that DO exist; a
    # missing slice is already reported (and about to abort) above.
    inadequate: list[str] = []
    for slice_name, required in mem_min_required.items():
        if slice_name in missing_slice_names:
            continue
        adequate, note = governance_mod.check_memory_min_ancestor_chain(slice_name, required)
        if adequate is None:
            info(f"[INFO] [S15.16] {note}")
        elif adequate:
            info(f"[INFO] [S15.16] mem_min OK — {note}")
        else:
            sources = ", ".join(mem_min_sources[slice_name])
            inadequate.append(f"  '{slice_name}' (declared by: {sources}, requires >= {required} bytes) — {note}")

    if missing:
        raise ValueError(
            "[S15.G9-1] governance names a cgroup slice that is not installed "
            "on this host. Docker/systemd will auto-create a missing slice "
            "TRANSIENTLY with NO resource limits (S15.8) — the container will "
            "appear governed (cgroup_parent is set) but run completely "
            "unbounded. Install the slice unit(s) first (a systemd .slice "
            "file under /etc/systemd/system/, then `systemctl daemon-reload`), "
            "or point [<root>.governance].cgroup_parent at a slice that "
            "already exists:\n" + "\n".join(missing)
        )

    if inadequate:
        # S10.7 — exit_on policy, not a bare raise: a broken mem_min ancestor
        # chain is a real, worth-surfacing-loudly gap, but SOME real
        # protection may still be in effect one level down (mem_limit,
        # blkio caps) even though this specific floor is a no-op — an
        # operator who already knows about the host-side gap (S15.16) may
        # legitimately want to proceed anyway. Default exit_on=ERROR means
        # this WARN does NOT abort; ciu.exit_on=WARN makes it fail-fast.
        warn_policy.warn_or_raise(
            "[S15.16] governance declares mem_min (a memory floor) for a stack "
            "whose resolved cgroup slice does not carry a matching MemoryMin= "
            "(cgroup-v2 memory.min). cgroup_parent only PLACES a container "
            "under the named slice (S15.8) — CIU never configures the slice's "
            "own resource properties. Add a matching MemoryMin= to the slice's "
            "systemd unit (e.g. a drop-in under /etc/systemd/system/<slice>.d/, "
            "then `systemctl daemon-reload`) — a host-side companion such as "
            "modern-debian-tools-python-debug's host-setup can provision this, "
            "but CIU never depends on one being present — or lower/remove the "
            "mem_min declaration if no floor is actually required. Set "
            "ciu.exit_on = \"WARN\" to make warnings fatal, or "
            "\"NEVER\" to always proceed:\n"
            + "\n".join(inadequate)
            , severity="WARN", config=config,
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

    return run_container_health_gate(
        list(names.values()), timeout_s=timeout_s, interval_s=interval_s
    )


def run_container_health_gate(
    container_names: list[str],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
) -> tuple[bool, dict]:
    """Poll exact Docker *container_names* and return the S7.7 gate result.

    Orchestration actions resolve these names from the rendered Compose model;
    they must not infer a runtime identity from a phase's human-readable
    ``name``.  ``run_health_gate`` remains the small service-suffix adapter
    used by callers that already have an unambiguous CIU service identity.
    """

    def check_fn() -> dict[str, str]:
        statuses: dict[str, str] = {}
        for cname in container_names:
            statuses[cname] = health_pkg.classify(_inspect_state(cname))
        return statuses

    return health_pkg.wait_for_gate(check_fn, timeout_s=timeout_s, interval_s=interval_s)


def resolve_selection_health_containers(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
) -> list[str]:
    """Resolve exact health-gate targets from selected stacks' Compose models.

    A phase service ``name`` is presentation text for operator output, not a
    container identifier.  One phase entry may also deploy several Compose
    services.  The only authoritative static identities are therefore the
    ``services.*.container_name`` values in the rendered Compose file.

    Services guarded by Compose ``profiles`` are included only when their
    profile intersects the profiles active for that phase entry.  Ambiguous or
    missing identities fail closed with an authoring error instead of polling
    a fabricated container name until timeout.
    """
    import yaml

    from .config_constants import CIU_COMPOSE_OUTPUT, SHIPPED_COMPOSE

    resolved: list[str] = []
    seen: set[str] = set()

    for entry in selection:
        stack_dir = (repo_root / entry["path"]).resolve()
        service_cfg = entry["service"]
        if not phases_pkg.service_health_enabled(service_cfg):
            continue
        shipped = phases_pkg.service_shipped(service_cfg)
        compose_name = SHIPPED_COMPOSE if shipped else CIU_COMPOSE_OUTPUT
        compose_path = stack_dir / compose_name
        if not compose_path.is_file():
            source_kind = "shipped" if shipped else "rendered"
            raise ValueError(
                f"[S7.7] Cannot resolve health targets for stack '{entry['path']}': "
                f"no {source_kind} {compose_name}"
                + ". Run 'ciu render' (or deploy the stack) first."
            )

        try:
            compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(
                f"[S7.7] Cannot read Compose model for stack '{entry['path']}': {exc}"
            ) from exc

        if not isinstance(compose, dict):
            raise ValueError(
                f"[S7.7] Compose model for stack '{entry['path']}' must be a mapping."
            )

        services = compose.get("services")
        if not isinstance(services, dict) or not services:
            raise ValueError(
                f"[S7.7] Compose model for stack '{entry['path']}' has no services."
            )

        active_profiles = set(profile.compose_profiles)
        active_profiles.update(service_cfg.get("profiles") or [])
        active_count = 0
        for compose_service, definition in services.items():
            if not isinstance(definition, dict):
                raise ValueError(
                    f"[S7.7] Compose service '{compose_service}' in stack "
                    f"'{entry['path']}' must be a mapping."
                )
            declared_profiles = definition.get("profiles")
            if declared_profiles is not None:
                if not isinstance(declared_profiles, list) or not all(
                    isinstance(value, str) for value in declared_profiles
                ):
                    raise ValueError(
                        f"[S7.7] Compose service '{compose_service}' in stack "
                        f"'{entry['path']}' has invalid profiles; expected a list of strings."
                    )
                if not active_profiles.intersection(declared_profiles):
                    continue

            active_count += 1
            cname = definition.get("container_name")
            if not isinstance(cname, str) or not cname.strip() or "$" in cname:
                raise ValueError(
                    f"[S7.7] Cannot resolve an exact health target for Compose service "
                    f"'{compose_service}' in stack '{entry['path']}': set a concrete "
                    "container_name in the rendered Compose model."
                )
            cname = cname.strip()
            if cname not in seen:
                seen.add(cname)
                resolved.append(cname)

        if active_count == 0:
            raise ValueError(
                f"[S7.7] Stack '{entry['path']}' has no services active for Compose "
                f"profiles {sorted(active_profiles)}."
            )

    return resolved


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
    rendered = render_selected_stacks(
        repo_root, profile, selection,
        ciu_context=profiles_pkg.render_ciu_context(profile, selection),
    )
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
    rendered: Optional[dict[str, dict]] = None,
    no_preflight: bool = False,
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
    # S3.12 / CIU-44: one selection-facts snapshot for every render/hook of
    # this deploy — the FULL selected set, not per-stack slices.
    ciu_ctx = profiles_pkg.render_ciu_context(profile, selection)
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

        # Per-phase provisioning preflight: probe THIS phase's requires now that
        # earlier phases are up (replaces the old once-up-front probe, which could
        # never pass on a greenfield 'ciu up'). Skipped in dry-run (nothing is
        # actually running to probe) and under --no-preflight.
        if rendered is not None and not no_preflight and not dry_run:
            try:
                provisioning_preflight(
                    repo_root, profile, entries, rendered,
                    no_preflight=False, lint=False, probe=True,
                )
            except ValueError as exc:
                error(str(exc))
                had_failure = True
                phase_failed = True
                for e in entries:
                    failed.append(e["path"])
                if not ignore_errors:
                    stop_remaining = True
                continue

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
                ciu_context=ciu_ctx,
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
            container_names = resolve_selection_health_containers(
                repo_root, profile, started_in_phase
            )
            if not container_names:
                info(
                    f">>> Health gate for phase {phase_key}: no health-enabled "
                    "containers selected; passing"
                )
                passed = True
            else:
                info(
                    f">>> Health gate for phase {phase_key} "
                    f"({len(container_names)} container(s))"
                )
                passed, summary = run_container_health_gate(
                    container_names, timeout_s=timeout_s
                )
                _print_health_summary(summary)
            if not passed:
                error(f"[S7.7] health gate FAILED for phase {phase_key}")
                had_failure = True
                phase_failed = True
                for e in started_in_phase:
                    # Every started entry is appended to ``deployed`` together
                    # in the successful-start path above; preserve that
                    # invariant instead of silently masking a broken plan.
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
    ciu_context: Optional[dict] = None,
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
                ciu_context=ciu_context,
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
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
) -> int:
    """--healthcheck: run the health gate over the whole selection (S7.7)."""
    info("=" * 60)
    info("HEALTHCHECK: gating all selected services (S7.7)")
    info("=" * 60)
    if not selection:
        warn("No services selected to check")
        return 0
    container_names = resolve_selection_health_containers(
        repo_root, profile, selection
    )
    if not container_names:
        info("No health-enabled containers selected; health gate passes")
        return 0
    health_cfg = profile.config.get("deploy", {}).get("health", {})
    timeout_s = _seconds(health_cfg.get("timeout", "30s"))
    passed, summary = run_container_health_gate(
        container_names, timeout_s=timeout_s
    )
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


def action_check(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
    *,
    live: bool = False,
) -> int:
    """--check: validate the requires/provides dependency graph (no deploy).

    Runs lint_graph and optionally probes live state when --live is set.
    Exit non-zero on any failure.
    """
    from . import provisioning as provisioning_pkg

    info("=" * 60)
    info("CHECK: validating requires/provides dependency graph")
    info("=" * 60)

    stacks: dict[str, dict] = {}
    for entry in selection:
        rel = entry["path"]
        if rel not in rendered:
            continue
        stack_cfg = rendered[rel]
        try:
            root_key = config_model.validate_stack_shape(stack_cfg)
        except ValueError as exc:
            error(str(exc))
            return 2
        root_section = stack_cfg.get(root_key, {})
        requires = root_section.get("requires", [])
        provides = root_section.get("provides", [])
        if not (requires or provides):
            continue
        # Reject malformed typed refs (spec §2 grammar) before linting.
        try:
            config_model.validate_stack_provisioning(stack_cfg, source=rel)
        except ValueError as exc:
            error(str(exc))
            return 2
        stacks[rel] = {"requires": requires, "provides": provides}

    if not stacks:
        info("No stacks with requires/provides — nothing to check")
        success("check passed (no provisioning refs)")
        return 0

    lint_errors = provisioning_pkg.lint_graph(stacks)
    if lint_errors:
        for e in lint_errors:
            error(e)
        error("Graph lint failed")
        return 2

    info("Graph lint passed")

    if live:
        info("Probing live state...")
        config = profile.config
        all_failed: list[str] = []
        for entry in selection:
            rel = entry["path"]
            if rel not in stacks:
                continue
            for ref in stacks[rel].get("requires", []):
                result = provisioning_pkg.probe_ref(ref, config, repo_root)
                if result.satisfied:
                    info(f"  OK  {ref}")
                else:
                    error(f"  FAIL {ref}: {result.reason}")
                    all_failed.append(ref)
        if all_failed:
            error(f"Live probe failed: {len(all_failed)} unsatisfied requirement(s)")
            return 1
        info("Live probe passed")

    success("check passed")
    return 0


def action_graph(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    rendered: dict[str, dict],
    *,
    fmt: str = "mermaid",
) -> int:
    """--graph: render the requires/provides dependency graph (no deploy).

    Writes Mermaid (default), Graphviz DOT, or JSON to STDOUT (so it can be piped
    into docs); diagnostics go to the logger (stderr).
    """
    from . import provisioning as provisioning_pkg

    stacks: dict[str, dict] = {}
    for entry in selection:
        rel = entry["path"]
        if rel not in rendered:
            continue
        stack_cfg = rendered[rel]
        try:
            root_key = config_model.validate_stack_shape(stack_cfg)
        except ValueError as exc:
            error(str(exc))
            return 2
        root_section = stack_cfg.get(root_key, {})
        requires = root_section.get("requires", [])
        provides = root_section.get("provides", [])
        if not (requires or provides):
            continue
        # ``--graph`` is an inspection action, but it must not turn malformed
        # provisioning declarations into a plausible-looking topology.  Keep
        # its input contract identical to ``--check`` and the deploy preflight:
        # an invalid typed ref is configuration failure (exit 2), not a graph
        # edge to an invented/unprovided target.
        try:
            config_model.validate_stack_provisioning(stack_cfg, source=rel)
        except ValueError as exc:
            error(str(exc))
            return 2
        stacks[rel] = {"requires": requires, "provides": provides}

    if not stacks:
        info("No stacks with requires/provides — nothing to graph")
        return 0

    print(provisioning_pkg.render_graph(stacks, fmt=fmt))
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


def _workspace_identity_network(repo_root: Path) -> str:
    """Read ``DOCKER_NETWORK_INTERNAL`` from THIS workspace's own ciu.env.

    The generated record (S2.7) is the authority for the workspace's network
    name — never the ambient process environment, which a shell that sourced
    a different checkout's ciu.env may carry (CIU-41). Empty when no
    ciu.env/record exists or it names no network.
    """
    env_path = repo_root / "ciu.env"
    if not env_path.is_file():
        return ""
    try:
        values = parse_workspace_env(env_path)
    except WorkspaceEnvError:
        return ""
    return values.get("DOCKER_NETWORK_INTERNAL", "")


def _is_worktree_instance(repo_root: Path) -> bool:
    """True when THIS checkout carries an S16 worktree-instance lifecycle record.

    The record is the instance/main discriminator for cleanup semantics
    (S6.4a): an instance is ephemeral and must tear down completely; the main
    workspace may deliberately keep its own network (devcontainer residence)
    but must then SAY so.
    """
    return (repo_root / worktree_pkg.WORKTREE_INSTANCE_RECORD).is_file()


def _stack_compose_projects(repo_root: Path, config: dict, selection: list[dict]) -> list[str]:
    """Compose project names (S8.7) of the selected stacks that exist on disk.

    Covers shipped stacks too: their compose runs create ``*_default``
    networks and label their volumes with the same project name.

    CIU-46 cutover: when ``deploy.project_name``/``environment_tag`` are
    absent, shipped mode still deploys — under the WORKSPACE-IDENTITY compose
    project derived from THIS checkout's ciu.env (S8.7; the basename fallback
    is withdrawn). Returning ``[]`` here (the pre-CIU-46 behavior) made every
    S6.4a enumeration pass skip those stacks entirely, so their
    ``*_default`` networks and label-prefixed volumes survived a reported-
    clean teardown. Tags absent → each existing selected stack contributes
    its identity-derived name, computed by the SAME
    ``engine.identity_compose_project_name`` up passed as ``-p`` (a missing
    or key-less ciu.env raises — a teardown that cannot be named refuses,
    never skips); tags present keeps the S8.7 scoped names, unchanged.
    """
    projects: list[str] = []
    deploy_cfg = config.get("deploy", {})
    if not deploy_cfg.get("project_name") or not deploy_cfg.get("environment_tag"):
        seen: set[str] = set()
        for entry in selection:
            stack_dir = (repo_root / entry["path"]).resolve()
            if not stack_dir.is_dir():
                continue
            project = engine.identity_compose_project_name(repo_root, stack_dir)
            if project not in seen:
                seen.add(project)
                projects.append(project)
        return projects
    seen = set()
    for entry in selection:
        stack_dir = (repo_root / entry["path"]).resolve()
        if not stack_dir.is_dir():
            continue
        project = engine.compose_project_name(config, stack_dir)
        if project not in seen:
            seen.add(project)
            projects.append(project)
    return projects


def _stack_project_containers(stack_projects: list[str]) -> list[str]:
    """Containers of the given S8.7 projects via the compose project label.

    CIU-46: the S7.8 name-prefix pass (``^{project}-{env_tag}-``) cannot see
    a tags-absent shipped stack's containers — compose names them after the
    workspace-identity project, not the instance prefix. The compose
    project label is an exact per-project enumeration, all states (an exited
    one-shot init must be removed or it pins the project's volumes, CIU-3).

    Raises ValueError on daemon failure — indeterminate never folds into
    "nothing to remove" (review B3).
    """
    found: list[str] = []
    for project in stack_projects:
        result = procutil.docker(
            [
                "ps", "-a",
                "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Names}}",
            ],
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(
                f"[S6.4a] docker ps failed for compose project {project!r}: "
                f"{(result.stderr or '').strip()}"
            )
        found.extend(
            n.strip() for n in (result.stdout or "").splitlines() if n.strip()
        )
    return sorted(set(found))


def _network_endpoints(network: str) -> Optional[list[dict]]:
    """Endpoint containers attached to *network*; None when it does not exist.

    A failed inspect must NOT read as "no endpoints" (absence-for-emptiness):
    the None return lets callers distinguish a gone network from an
    unresolvable one.
    """
    inspect = procutil.docker(["network", "inspect", network], capture=True, check=False)
    if inspect.returncode != 0:
        return None
    try:
        data = json.loads(inspect.stdout)
    except json.JSONDecodeError:
        raise ValueError(
            f"[S6.4a] docker network inspect {network!r} returned unparsable output; "
            "cannot determine attached endpoints"
        )
    if not isinstance(data, list) or not data:
        return []
    containers = data[0].get("Containers") or {}
    endpoints = []
    for container_id, info in containers.items():
        endpoints.append({"id": container_id, "name": info.get("Name", container_id)})
    return endpoints


def _network_exists(network: str) -> bool:
    """STATE-based network existence via an exact-name ``network ls`` filter.

    Unlike ``inspect`` (whose non-zero exit is ambiguous between "absent" and
    "daemon unreachable"), ``ls --filter name=^<net>$`` exits 0 with empty
    output for an absent network while the daemon is up. A non-zero exit here
    is therefore ALWAYS a daemon/binary failure and raises — callers must
    treat that as INDETERMINATE (fail closed), never as "gone" (review B3).
    """
    result = procutil.docker(
        ["network", "ls", "--filter", f"name=^{network}$",
         "--format", "{{.Name}}"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"[S6.4a] docker network ls failed for {network!r} "
            f"(daemon failure?): {(result.stderr or '').strip()}"
        )
    return any(
        line.strip() == network for line in (result.stdout or "").splitlines()
    )


def _list_stack_project_networks(stack_projects: list[str]) -> list[str]:
    """Networks compose created for the given S8.7 projects (label filter).

    Exact per-project enumeration — covers ``<project>_default`` AND stacks
    declaring custom-named compose networks; never a broad glob (review N1).
    Raises on daemon failure (fail closed).
    """
    found: list[str] = []
    for project in stack_projects:
        result = procutil.docker(
            [
                "network", "ls",
                "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Name}}",
            ],
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(
                f"[S6.4a] docker network ls failed for compose project "
                f"{project!r}: {(result.stderr or '').strip()}"
            )
        found.extend(
            v.strip() for v in (result.stdout or "").splitlines() if v.strip()
        )
    return sorted(set(found))


def _remove_identity_networks(
    networks: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Disconnect lingering endpoints then remove each named network.

    Returns ``(removed, blocked)`` where *blocked* pairs each surviving
    network with the reason (a disconnect failure names the endpoint).
    Networks already absent are silently fine. Removal never silently keeps:
    anything not removed comes back as *blocked* and fails clean (S6.4a).
    """
    removed: list[str] = []
    blocked: list[tuple[str, str]] = []
    for network in networks:
        try:
            if not _network_exists(network):
                continue  # already gone (state-verified)
            endpoints = _network_endpoints(network)
        except ValueError as exc:
            blocked.append((network, str(exc)))
            continue
        if endpoints is None:
            # existed moments ago, gone now — a concurrent teardown won; fine.
            continue
        failed_disconnect = False
        for endpoint in endpoints:
            name = endpoint["name"]
            res = procutil.docker(["network", "disconnect", network, name], check=False)
            if res.returncode != 0:
                warn(f"docker network disconnect failed for {name!r}: {res.stderr}")
                blocked.append((network, f"endpoint {name!r} could not be disconnected"))
                failed_disconnect = True
        if failed_disconnect:
            continue
        rm = procutil.docker(["network", "rm", network], check=False)
        # Re-check from Docker STATE: a zero exit can race a concurrent
        # connect, and a non-zero rm exit may still mean "gone". An ls FAILURE
        # here is indeterminate — blocked, never folded into success (B3).
        try:
            still_there = _network_exists(network)
        except ValueError as exc:
            blocked.append((network, f"could not verify removal: {exc}"))
            continue
        if still_there:
            detail = (rm.stderr or "").strip().splitlines()[-1] if (rm.stderr or "").strip() else "removal refused"
            blocked.append((network, detail))
            continue
        removed.append(network)
    return removed, blocked


def action_clean(
    repo_root: Path,
    profile: profiles_pkg.Profile,
    selection: list[dict],
    *,
    ignore_errors: bool,
) -> int:
    """--clean: stop+remove containers, remove project volumes + networks, reset.

    Lean reimplementation of v1 cleanup (S6.4 semantics, COMPOSE_PROFILES='*'
    for down-with-profiles instead of v1's hardcoded 'full,pgadmin'):
      1. stop + remove project containers — the anchored S7.8 name-prefix pass
         when the deploy tags are set; when absent, the compose-label pass per
         selected stack's legacy project (CIU-46: a tags-absent shipped stack
         runs under the workspace-identity project, invisible to the
         name prefix);
      2. per-stack engine.reset_service (down -v via overlay + vol-*/rendered
         cleanup, B14 stack-dir scoped);
      3. remove project volumes — the ``{project}-{env}-*`` name pass (S6.4)
         plus a compose-label pass per selected stack's compose project
         (CIU-43: catches named volumes like ``<project>-vault-data`` that
         carry the bare project prefix, second reproduction on 6.3.0; CIU-46:
         the project list includes legacy names when the tags are absent);
      4. remove identity-scoped networks (S6.4a, CIU-43): the workspace
         identity network (from THIS workspace's own ciu.env) and each
         selected stack's ``<compose-project>_default``. Lingering endpoints
         are disconnected first; an endpoint that cannot be disconnected is
         named and fails the clean — never silently kept.
         Instance-vs-main split: an S16 worktree instance (lifecycle record
         present) removes ALL of its identity-scoped networks unconditionally;
         the MAIN workspace deliberately keeps its own workspace network (the
         devcontainer stays attached to it) but names the keep in its output.
    The post-clean invariant covers containers, volumes AND networks: any
    survivor that was not a declared keep makes clean exit 1, so a false
    "clean complete" over surviving identity-scoped objects is impossible.
    """
    info("=" * 60)
    info("CLEAN: removing containers, volumes, and rendered artifacts")
    info("=" * 60)
    config = profile.config
    rc = 0

    # CIU-46: resolve the selected stacks' compose projects ONCE — S8.7 scoped
    # names when the deploy tags are set, workspace-identity names derived
    # from THIS checkout's ciu.env when absent (a tagless shipped stack runs
    # under the identity project; clean must enumerate what up actually
    # named). Drives the container, volume, and network passes below.
    deploy_cfg = config.get("deploy", {})
    tagged = bool(deploy_cfg.get("project_name")) and bool(deploy_cfg.get("environment_tag"))
    stack_projects = _stack_compose_projects(repo_root, config, selection)

    # Step 1: stop + remove project containers. all_states=True so an exited
    # one-shot init/sidecar (e.g. *-vault-init) is removed too — otherwise it
    # pins the project's named volumes through teardown (CIU-3, S6.4).
    # Tagged: the anchored S7.8 name-prefix pass (unchanged). Tags absent:
    # that prefix cannot match an identity-named shipped stack's containers,
    # so enumerate by the compose project label instead (CIU-46); a daemon
    # failure here fails the clean but does not abort the remaining passes.
    if tagged:
        containers = _matching_containers(config, all_states=True)
    else:
        try:
            containers = _stack_project_containers(stack_projects)
        except ValueError as exc:
            error(f"container enumeration failed (S6.4a): {exc}")
            rc = 1
            containers = []
    if containers:
        info(f"Removing {len(containers)} container(s): {', '.join(containers)}")
        result = procutil.docker(["rm", "-f", *containers], check=False)
        if result.returncode != 0:
            warn(f"docker rm failed: {result.stderr}")

    # Step 2: per-stack reset (down -v + vol-*/rendered), COMPOSE_PROFILES='*'.
    # The re-render MUST carry the selection facts (S3.12): a consumer template
    # referencing ciu.deployed_stacks renders during clean exactly as during
    # up — omitting the context would crash mid-teardown (review B2).
    rendered = render_selected_stacks(
        repo_root, profile, selection,
        ciu_context=profiles_pkg.render_ciu_context(profile, selection),
    )
    saved_profiles = os.environ.get("COMPOSE_PROFILES")
    os.environ["COMPOSE_PROFILES"] = "*"
    try:
        for entry in selection:
            rel = entry["path"]
            stack_dir = (repo_root / rel).resolve()
            if not stack_dir.is_dir():
                continue
            # A shipped stack deliberately has no rendered CIU configuration
            # (S8.5/S8.6): ``run_shipped`` consumes its maintainer-owned
            # docker-compose.yml directly.  Step 1's project-container
            # removal and Step 3's project-volume removal already clean its
            # runtime state; routing it through reset_service would index a
            # non-existent rendered config and turn an otherwise successful
            # clean into a false failure.
            if phases_pkg.service_shipped(entry.get("service", {})):
                info(f"Skipping CIU-native reset for shipped stack: {rel}")
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

    # Step 3: remove project volumes — the {project}-{env}-* name pass (S6.4)
    # plus the compose-label pass per selected stack's project (CIU-43: named
    # volumes like <project>-vault-data carry the bare project prefix and no
    # instance tag, so the name pass never saw them; CIU-46: stack_projects
    # also carries the identity-derived names when the deploy tags are absent).
    volumes_unverified = False
    try:
        survivors = _remove_project_volumes(config, stack_projects=stack_projects)
    except (ValueError, FileNotFoundError) as exc:
        # Indeterminate volume state fails the clean (review B3): never fold
        # "could not enumerate" into "nothing to remove".
        error(f"volume enumeration failed (S6.4a): {exc}")
        volumes_unverified = True
        rc = 1
        survivors = []

    # Step 4 (S6.4a / CIU-43): remove identity-scoped networks. The workspace
    # identity network comes from THIS workspace's own ciu.env; the per-stack
    # ``<compose-project>_default`` networks come from the S8.7 project names.
    is_instance = _is_worktree_instance(repo_root)
    identity_network = _workspace_identity_network(repo_root)
    keep_networks: dict[str, str] = {}
    if identity_network and not is_instance:
        keep_networks[identity_network] = (
            "workspace network of the main workspace (devcontainer residence)"
        )
    target_networks: list[str] = []
    if identity_network and identity_network not in keep_networks:
        target_networks.append(identity_network)
    try:
        # Exact per-project enumeration (S6.4a): compose-labeled networks of
        # the selected stacks — <project>_default AND custom-named ones. A
        # daemon failure here is indeterminate and fails the clean (B3).
        for net in _list_stack_project_networks(stack_projects):
            if net not in target_networks and net not in keep_networks:
                target_networks.append(net)
    except ValueError as exc:
        error(str(exc))
        rc = 1
    removed_nets, blocked_nets = _remove_identity_networks(target_networks)
    if removed_nets:
        info(f"Removed {len(removed_nets)} network(s): {', '.join(removed_nets)}")
    announced_keeps: list[str] = []
    for net, reason in keep_networks.items():
        try:
            if _network_exists(net):
                info(f"kept: {net} ({reason})")
                announced_keeps.append(net)
        except ValueError as exc:
            warn(f"kept-network check failed for {net!r}: {exc}")

    # Step 5: enforce the post-clean invariant — zero project containers, zero
    # project volumes, and zero UNKEPT identity-scoped networks remain. A
    # survivor is an ERROR (not a warning): it almost always means a container
    # still references it or an endpoint refused to disconnect, exactly the
    # silent-stale-state failures CIU-3 and CIU-43 closed. Degrade gracefully
    # if docker became unavailable mid-clean so the action still returns its
    # own typed result instead of escaping as an exception.
    untagged_unverifiable = False
    try:
        if tagged:
            remaining_containers = _matching_containers(config, all_states=True)
        else:
            # CIU-46: a tags-absent selection's containers carry the
            # workspace-identity project, not the S7.8 name prefix —
            # enumerate by label.
            remaining_containers = _stack_project_containers(stack_projects)
    except ValueError as exc:
        if tagged:
            # Tagged path keeps its pre-CIU-46 degradation: docker gone
            # mid-clean degrades to warn (documented S6.4 behavior).
            warn(f"post-clean container check skipped (docker unavailable): {exc}")
            remaining_containers = []
        else:
            # Review fix (B3 symmetry): the tags-absent enumeration is this
            # wave's own proof-of-removal pass; an UNVERIFIABLE set fails the
            # clean like unverifiable volumes/networks — indeterminacy never
            # folds into 'clean complete'.
            error(
                f"post-clean invariant unverifiable (S6.4): project containers "
                f"could not be enumerated — {exc}"
            )
            rc = 1
            untagged_unverifiable = True
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
    if volumes_unverified:
        error(
            "post-clean invariant unverifiable (S6.4): project volumes could "
            "not be enumerated — clean cannot certify a complete teardown"
        )
        rc = 1

    # Network invariant from Docker STATE (never from command diagnostics):
    # every targeted network must be gone. An UNVERIFIABLE network is a
    # violation too — indeterminate never folds into "gone" (review B3).
    for net in dict.fromkeys([*target_networks, *keep_networks]):
        try:
            still_there = _network_exists(net)
        except ValueError as exc:
            error(
                f"post-clean invariant unverifiable (S6.4a): network {net!r} "
                f"could not be checked — {exc}"
            )
            rc = 1
            continue
        if net in keep_networks or not still_there:
            continue
        reason = next((r for n, r in blocked_nets if n == net), "survived removal")
        error(f"post-clean invariant violated (S6.4a): network {net!r} remains — {reason}")
        rc = 1

    if rc == 0:
        # Name only what was verified present — claiming a keep of an absent
        # network would overstate what happened.
        if announced_keeps:
            kept = ", ".join(sorted(announced_keeps))
            success(f"clean complete (kept: {kept})")
        else:
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


def _list_stack_project_volumes(stack_projects: list[str]) -> list[str]:
    """Volumes compose created for the given S8.7 projects (label filter).

    CIU-43: a stack's named volumes (e.g. ``<project>-vault-data``) carry the
    bare project prefix without the instance tag, so the ``{project}-{env}-*``
    name pass never matched them. Compose labels every volume it creates with
    ``com.docker.compose.project``, which is an exact per-project enumeration —
    no broad glob that could eat an unrelated project's volumes.
    """
    found: list[str] = []
    for project in stack_projects:
        result = procutil.docker(
            [
                "volume", "ls",
                "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Name}}",
            ],
            check=False,
        )
        if result.returncode != 0:
            # Indeterminate is NOT empty (review B3): the caller fails the
            # clean rather than reporting success over unverified volumes.
            raise ValueError(
                f"[S6.4a] docker volume ls failed for compose project "
                f"{project!r}: {(result.stderr or '').strip()}"
            )
        found.extend(v.strip() for v in (result.stdout or "").splitlines() if v.strip())
    return sorted(set(found))


def _remove_project_volumes(
    config: dict, *, stack_projects: Optional[list[str]] = None
) -> list[str]:
    """Remove project volumes (name pass + optional compose-label pass).

    Returns the list of volumes that **survived** removal (empty = fully clean).
    ``docker volume rm`` only warns on failure here; the caller re-checks and
    treats survivors as a hard error so a "volume is in use" no longer passes
    silently and leaves stale state behind (CIU-3, S6.4 post-clean invariant).
    *stack_projects* (CIU-43) adds the per-stack compose-label enumeration that
    catches bare-project-prefix named volumes; ``None`` preserves the old
    name-pass-only behavior for callers without selection context.
    """
    vols = _list_project_volumes(config)
    if stack_projects:
        # A label-pass failure is indeterminate (B3): surface it so clean
        # fails closed instead of reporting success over unverified volumes.
        labeled = _list_stack_project_volumes(stack_projects)
        vols = sorted(set(vols) | set(labeled))
    if not vols:
        return []
    info(f"Removing {len(vols)} project volume(s): {', '.join(vols)}")
    rm = procutil.docker(["volume", "rm", *vols], check=False)
    if rm.returncode != 0:
        warn(f"docker volume rm reported errors: {rm.stderr}")
    # Re-list: rm may have partially failed (a volume pinned by a surviving
    # container). Survivors are reported as an error by action_clean.
    remaining = _list_project_volumes(config)
    if stack_projects:
        remaining_labeled = _list_stack_project_volumes(stack_projects)
        remaining = sorted(set(remaining) | set(remaining_labeled))
    return remaining


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
    # Provenance: stamp the source revision so a running container can be traced
    # back to the commit it was built from (engine.bake_revision_args).
    cmd += engine.bake_revision_args()
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
        "--check": "check",
        "--graph": "graph",
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
    actions.add_argument("--check", action="store_true",
                         help="Validate the requires/provides dependency graph (no deploy)")
    actions.add_argument("--graph", action="store_true",
                         help="Render the requires/provides dependency graph (no deploy)")
    actions.add_argument("--render-toml", dest="render_toml", action="store_true",
                         help="Render global + selected stack configs and stop (S8.3 step 3)")
    actions.add_argument("--list-phases", dest="list_phases", action="store_true",
                         help="List numbered phases with service counts (S7.1)")
    actions.add_argument("--list-profiles", dest="list_profiles", action="store_true",
                         help="List host profiles (replaces v1 --list-groups) (S7.4)")

    control = parser.add_argument_group("Control")
    control.add_argument("--profile", action="append", default=None, metavar="NAME",
                         help=(
                             "Service profile(s) to activate (repeatable; comma form also "
                             "accepted: --profile core,db). "
                             "Default: env CIU_SERVICES_PROFILE (Seam 4 / S7.5)."
                         ))
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
    control.add_argument("--no-preflight", dest="no_preflight", action="store_true",
                         help="Skip provisioning + governance-slice preflight checks (break-glass)")
    control.add_argument("--ignore-mismatch", "--force", dest="ignore_mismatch",
                         action="store_true",
                         help="S17.2 (`ciu provenance`): run even when a "
                              "container was built from a different commit — "
                              "the result then describes the OLD artifact")
    control.add_argument("--live", action="store_true",
                         help="With --check: also probe live state (Vault/Postgres/MinIO/Consul/Docker)")
    control.add_argument("--format", dest="graph_format", default="mermaid",
                         choices=["mermaid", "dot", "json"],
                         help="With --graph: output format (default: mermaid)")
    control.add_argument("--host", default=None, metavar="NAME",
                         help="Remote host name (from hosts inventory): push-deploy via SSH (SPEC J)")
    control.add_argument("--thin", action="store_true", default=False,
                         help="With --host: docker-optional push→activate mode (S14.6)")
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

    # S1.2 — enforce from the INVOCATION dir (cwd), NOT the resolved repo_root.
    # A contaminated $REPO_ROOT is exactly what this guards, and detecting from the
    # resolved root would inspect the wrong repo's marker and silently pass (the
    # render-path bug). Mirrors engine.main_execution's working_dir call.
    enforce_standalone_root(Path.cwd())

    repo_root = resolve_repo_root(define_root)

    # --- config + profile (S3.3 / S7.4 / Seam 4) ---
    global_cfg = load_global_config(repo_root)
    # args.profile is now a list[str] | None (action="append"). Expand any
    # comma forms so --profile core,db behaves like --profile core --profile db.
    raw_profiles = args.profile  # list[str] | None
    if raw_profiles:
        expanded: list[str] = []
        for entry in raw_profiles:
            for part in entry.split(","):
                part = part.strip()
                if part:
                    expanded.append(part)
        cli_profiles: list[str] | None = expanded if expanded else None
    else:
        cli_profiles = None
    profile = resolve_profiles(global_cfg, cli_profiles)
    info(f"Active service profile(s): {profile.name or '(default — all phases)'}")

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
    rendered: Optional[dict[str, dict]] = None
    if deploy_needs_preflight and not args.dry_run:
        rendered = render_selected_stacks(
            repo_root, profile, selection,
            ciu_context=profiles_pkg.render_ciu_context(profile, selection),
        )
        vault_preflight(repo_root, profile, selection, rendered)
        producer_preflight(profile, selection, rendered)
        provisioning_preflight(
            repo_root, profile, selection, rendered,
            no_preflight=getattr(args, 'no_preflight', False),
            probe=False,  # static lint up-front; live probing runs per-phase in action_deploy
        )
        registry_preflight(profile.config)
        # NOT an image-provenance check. S17.2 is deliberately a TEST-time gate
        # (`ciu provenance`), not a deploy-time one: at deploy the question is
        # "did I bake?", which surfaces immediately; the question that produces
        # bad EVIDENCE is asked later, against an already-running stack.
        governance_slice_preflight(
            repo_root, profile, selection, rendered,
            no_preflight=getattr(args, 'no_preflight', False),
        )
        # Ensure the workspace network exists before compose (devcontainer no-op
        # off-devcontainer); reads the profile-resolved auto_connect setting.
        ensure_workspace_network(
            auto_connect=profile.config.get("ciu", {}).get("auto_connect_network", True)
        )
    elif deploy_needs_preflight and args.dry_run:
        # Dry-run still validates misplaced directives + vault ordering (no token
        # I/O is forced because the engine won't start anything), matching S8.3
        # "everything else runs" intent.
        rendered = render_selected_stacks(
            repo_root, profile, selection,
            ciu_context=profiles_pkg.render_ciu_context(profile, selection),
        )
        vault_preflight(repo_root, profile, selection, rendered)
        producer_preflight(profile, selection, rendered)
        provisioning_preflight(
            repo_root, profile, selection, rendered,
            no_preflight=getattr(args, 'no_preflight', False),
            probe=False,  # static lint up-front; live probing runs per-phase in action_deploy
        )
        governance_slice_preflight(
            repo_root, profile, selection, rendered,
            no_preflight=getattr(args, 'no_preflight', False),
        )

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
            ac = action_healthcheck(repo_root, profile, selection)
        elif action == "preflight":
            ac = action_healthcheck_preflight(
                repo_root, profile, selection,
                strict=getattr(args, "strict", False),
            )
        elif action == "check":
            if rendered is None:
                rendered = render_selected_stacks(
                    repo_root, profile, selection,
                    ciu_context=profiles_pkg.render_ciu_context(profile, selection),
                )
            ac = action_check(
                repo_root, profile, selection, rendered,
                live=getattr(args, 'live', False),
            )
        elif action == "graph":
            if rendered is None:
                rendered = render_selected_stacks(
                    repo_root, profile, selection,
                    ciu_context=profiles_pkg.render_ciu_context(profile, selection),
                )
            ac = action_graph(
                repo_root, profile, selection, rendered,
                fmt=getattr(args, 'graph_format', 'mermaid'),
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
                rendered=rendered,
                no_preflight=getattr(args, 'no_preflight', False),
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
            args.preflight,
            args.check,
            args.graph,
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
    """Filter phase dicts by selected phase keys (each dict has a 'key').

    ``None`` means unrestricted; an EMPTY set means no phases (S7.5 narrowing
    — a stacks-only profile selects zero phases, so falsy-checking the set
    would wrongly widen it back to all).
    """
    if selected_phase_keys is None:
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
