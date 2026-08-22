#!/usr/bin/env python3
"""CIU — Container Infrastructure Utility (compose · init · up)"""

from __future__ import annotations

import os
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .cli_utils import get_cli_version
from .config_constants import GLOBAL_CONFIG_DEFAULTS, WORKSPACE_ENV
from .output import consume_cli_flags

_USAGE = """\
CIU {ver} — Container Infrastructure Utility (compose · init · up)
Uses: ciu.global.toml + ciu.env (run from a CIU-enabled repository)

Usage: ciu <verb> [options]
       ciu version

Run-scoped overrides (never written back to the TOML layer):
  --ksm / --no-ksm       inject / do not inject CIU's KSM shim, THIS run only.
                         --no-ksm is PASSTHROUGH: it stops CIU injecting, it
                         does NOT disable KSM an image enables itself (S15.18)
  --log-prefix-time-short  prefix severity messages with HH:MM:SS. Interactive
                           terminals colour INFO/WARN/ERROR; pipes and logs stay plain.

Run `ciu <verb> --help` for the complete options and examples for one verb.
Exit codes: 0 success · 1 runtime failure · 2 configuration/validation error
            · 3 environment/bootstrap error

  ENVIRONMENT
    env                         show ciu.env key=value pairs (read-only)
    env generate [--define-root PATH]
                                generate or refresh ciu.env from system state
    iops-baseline [--path P] [--runtime N] [--force]
                                measure disk randread IOPS (fio) → io-baseline.env (S15.9)

  AUTHORING
    render                      render ciu.global.toml from Jinja2 template
    profiles                    list available host profiles
    layouts                     list declared deploy layouts

  WORKTREE INSTANCES (S16)
    worktree create LOGICAL [--prefix P --feature F] [--json]
                                allocate a new managed instance
    worktree adopt LOGICAL PATH [--profile P1,P2] [--json]
    worktree ensure LOGICAL     reuse/create/resume an exact managed instance
    worktree add NAME           human shorthand for create
    worktree rm LOGICAL [-y] [--json]   ciu clean, THEN remove the checkout
    worktree list [--json]      list linked checkouts
    worktree inspect LOGICAL [--json]   exact record + freshly read Git facts
    worktree up LOGICAL         start the selected ready instance, exactly
    worktree exec LOGICAL [--target ALIAS] -- ARGV...
                                run exact argv (no shell) in the selected root
                                or inside its declared container target

  MACHINE INTERFACES (D-009)
    capabilities [--json]       versioned, closed capability allowlist

  EVIDENCE
    provenance [--ignore-mismatch | --no-preflight] [--json]
                                verify RUNNING containers were built from the
                                commit under test, before a live lane runs (S17.2)

  STACK ORCHESTRATION
    up   [--profile NAME | --dir PATH | --layout NAME]   start Docker Compose stack
    down [--profile NAME]                stop stack (preserve volumes)
    clean                                remove containers and volumes
    health [--profile NAME]              health gate check
    health --preflight [--strict]        probe images for missing healthcheck tools
    diagnose [--project NAME] [--logs N] [--json]
                                explain common container failures (read-only)

  PROVISIONING (requires/provides graph)
    check [--profile NAME] [--live]      validate the dependency graph (no deploy)
    graph [--format mermaid|dot|json]    render the dependency graph (no deploy)

  DEV-LOOP BUILDS
    bake [targets ...] [--no-cache]      docker buildx bake --load
    ksm build [--force]                  build the KSM shim into .ciu/ksm/ (S15.17)
    dev <stack> [--profile NAME]          run a stack's live dev loop (HMR)

  SECRETS
    secrets list   [-d PATH]             list materialised secret names
    secrets reset  [--name N] [-y]       delete secret store files
    host-secrets <host> [--materialize | --list | --path NAME] [-y]
                                host-scoped local secrets (S14.3a, explicit-only)

  REMOTE (requires hosts file — see .ciu.hosts.toml)
    ssh <host> [--admin] [-- cmd...]            remote shell or command (access plane)
    render --host <name> [selection flags]      render on a remote host
    up  --host <name> [selection flags]         push-deploy: bundle-sync + render-on-target
    down/health --host <name> [--thin]          remote lifecycle/health action
"""


# ---------------------------------------------------------------------------
# S10.1 / CIU-7 — per-verb, verb-scoped help.
#
# `ciu <verb> -h|--help` MUST print the verb's OWN synopsis/options, never the
# legacy `ciu-deploy` argparse surface (which exposes withdrawn flags like
# --deploy/--stop). Each entry lists only the flags that actually reach the
# verb's handler. `env generate --help` is intentionally NOT intercepted here —
# it has its own argparse help one level down.
# ---------------------------------------------------------------------------

_VERB_HELP: dict[str, str] = {
    "worktree": """\
ciu worktree create LOGICAL [--name DISPLAY | --prefix P --feature F]
                             [--branch BRANCH] [--path PATH] [--json]
ciu worktree adopt LOGICAL PATH [--profile P1,P2] [--json]
ciu worktree ensure LOGICAL [create options] [--json]
ciu worktree add NAME [--base REF] [--profile P1,P2]
ciu worktree rm LOGICAL [-y] [--force] [--json]
ciu worktree list [--json]
ciu worktree inspect LOGICAL [--json]
ciu worktree up LOGICAL
ciu worktree exec LOGICAL [--target ALIAS] -- ARGV...
  Manage durable, family-scoped worktree identities. Creation and ensure do
  not start the instance. Generated UTC branch/directory names are identical;
  adopt is the only operation that owns an unmanaged existing checkout.
  `inspect` reports the persisted record plus freshly read Git facts; `list
  --json`/`inspect --json`/`rm --json` emit one versioned JSON document on
  stdout (S16.4). `up` starts the selected ready instance under its OWN
  ciu.env; `exec` runs exact argv (no shell) in that root and never starts
  anything implicitly (S16.6). `exec --target ALIAS` runs inside the ONE
  already-running declared container (S16.7).
""",
    "capabilities": """\
ciu capabilities [--json]
  Print CIU's versioned, closed capability allowlist (D-009). Consumers
  allowlist shipped machine-contract identifiers instead of inferring
  features from SemVer. `--json` emits the separately versioned document.
""",
    "env": """\
ciu env — show ciu.env key=value pairs (read-only)
ciu env generate [--define-root PATH] — (re)generate ciu.env from system state

  --define-root PATH   override repo root (no parent walking); for `generate`
""",
    "iops-baseline": """\
ciu iops-baseline [--path PATH] [--runtime N] [--force]
  Measure this host's disk randread IOPS ceiling with fio and write a
  shell-style baseline file (RIOPS_MAX=<n>, RIOPS_ENGINE=<engine>) that
  governance read_iops derivation consumes (S15.4/S15.9). Explicit opt-in
  only — CIU never runs this automatically. WARNING: generates ~10s of
  saturating read I/O; avoid running while latency-sensitive workloads are
  active. Uses fio's libaio engine (psync fallback is flagged: queue-depth-1
  latency, not the ceiling). Requires fio; without it the command prints a
  notice and exits 0 (derivation then uses the fallback 200).

  --path PATH     output file (default: /var/lib/ciu/io-baseline.env)
  --runtime N     fio measurement seconds (default: 10)
  --force         re-measure even when the existing result is < 30 days old
""",
    "render": """\
ciu render [--profile NAME] [--phases N,M] [--define-root PATH]
ciu render --host NAME [selection flags]
  Render ciu.global.toml + per-stack ciu.toml from their Jinja2 templates.

  --profile NAME       host profile to render for (repeatable; default: active profile)
  --phases N,M         restrict rendering to the given phase numbers
  --define-root PATH   override repo root (no parent walking)
  --host NAME          sync/execute the render on a configured remote host
""",
    "profiles": """\
ciu profiles
  List available host profiles. Takes no options.
""",
    "layouts": """\
ciu layouts
  List declared deploy layouts ([deploy.layouts.<name>]) with their
  environment and ordered host list (S7.5c). Takes no options; shows what is
  DECLARED — `ciu up --layout` is the validating consumer.
""",
    "up": """\
ciu up [--profile NAME | --dir PATH | --layout NAME] [selection/options]
ciu up --host NAME [selection...]                              # render-on-target
ciu up --host NAME --thin [--bootstrap | --rollback] [selection...] # docker-optional
  Render + materialise secrets + start the Docker Compose stack(s).

  Profile/multi-stack mode:
  --profile NAME     deploy the named host profile (repeatable; default: active profile)
  --phases N,M       restrict to the given phase numbers
  --dry-run          render and validate, but do not call Docker
  --no-preflight     skip host/provisioning preflight checks (break-glass)
  --define-root PATH override repo root (alias: --root-folder)
  -y, --yes          assume yes to prompts
  --ignore-errors    continue past a failing stack

  Layout mode (S7.5c) — push-deploy a named host→bundles plan:
  --layout NAME      resolve [deploy.layouts.<name>] and push to each host in
                     declaration order. Each host's remote command runs with
                     CIU_SERVICES_PROFILE set to that host's bundles and
                     CIU_LAYOUT / CIU_LAYOUT_HOST / CIU_DEPLOY_ENVIRONMENT
                     exported (S7.5c). A host failure aborts the sequence.
                     Mutually exclusive with --host and --profile.

  Single-stack mode (`--dir`) additionally accepts engine options:
  --dir PATH         deploy one stack directory
  --render-toml      stop after rendering TOML
  --print-context    print the template context
  --reset            remove this stack's containers/volumes/artifacts first
  --skip-hostdir-check / --skip-hooks / --skip-secrets
                     skip the named single-stack step
  --shipped          run the committed docker-compose.yml path
  -f NAME            select the stack template (advanced)

  Remote (SPEC S14):
  --host NAME        push-deploy to a host from the inventory (.ciu.hosts.toml)
  --thin             docker-optional: push an artifact to bundle_dir, then run the
                     host's shell activation contract (bootstrap|apply|health|
                     rollback) — no docker/python needed on the target (S14.6)
  --bootstrap        (with --thin) run the 'bootstrap' verb before 'apply'
  --rollback         (with --thin) run the 'rollback' verb only (no fresh push)
""",
    "down": """\
ciu down [--profile NAME] [--phases N,M] [--define-root PATH]
ciu down --host NAME [--profile NAME]
  Stop project containers; volumes are preserved (use `ciu clean` to remove them).

  --profile NAME     restrict to the named host profile (repeatable)
  --phases N,M       restrict to the given phase numbers
  --define-root PATH override repo root (alias: --root-folder)
  --host NAME        run the stop action on a configured remote host
""",
    "clean": """\
ciu clean [--profile NAME] [--phases N,M] [-y] [--ignore-errors]
  Tear down completely: remove ALL project containers (running AND exited, incl.
  init/sidecars), `docker compose down -v --remove-orphans`, remove project
  volumes and `vol-*` hostdirs, and remove rendered artifacts. The post-clean
  invariant (S6.4) is enforced: zero project containers AND zero project volumes
  remain, else clean fails (exit 1).

  --profile NAME     restrict to the named host profile (repeatable)
  --phases N,M       restrict to the given phase numbers
  --define-root PATH override repo root (alias: --root-folder)
  -y, --yes          assume yes to prompts
  --ignore-errors    continue past a failing stack (best-effort per stack)
""",
    "health": """\
ciu health [--profile NAME] [--phases N,M] [--define-root PATH]
ciu health --preflight [--strict]
ciu health --host NAME [--thin] [selection]
  Run the health gate (S7.7) over the selection, or probe images for missing
  healthcheck tools (--preflight).

  --profile NAME   restrict to the named host profile (default: active profile)
  --preflight      probe rendered compose images for missing healthcheck tools
  --strict         (with --preflight) treat missing tools as an error
  --host NAME      run the health gate on a remote host (SPEC S14)
  --thin           (with --host) run the docker-optional 'health' activation
                   verb instead of remote `ciu health` (S14.6)
  --phases N,M     restrict to the given phase numbers
  --define-root PATH override repo root (alias: --root-folder)
""",
    "diagnose": """\
ciu diagnose [--project NAME] [--logs N] [--json]
  Read-only scan of CIU-labelled containers. Correlates Docker state, OOM and
  exit evidence, restart counts, health history, memory/swap configuration,
  and bounded recent logs with known failure signatures.

  --project NAME   restrict to one Compose/CIU project label
  --logs N         recent log lines per container to scan (default: 100)
  --json           machine-readable findings
""",
    "bake": """\
ciu bake [targets ...] [--no-cache]
  Thin wrapper over `docker buildx bake --load`. No targets → bake `all`.
  (For an iterative dev server with HMR, see `ciu dev`.)

  --no-cache   pass --no-cache to buildx
""",
    "dev": """\
ciu dev <stack> [--profile NAME] [--no-prebuild]
  Run the stack's [<root>.dev] dev-loop profile (S5a): ordered `prebuild` steps
  (gated on `depends_on` health), then the long-running `command` with the
  source bind-mounted and `port` exposed. For HMR dev servers (Vite/Next/
  uvicorn --reload) and contract-coupled pre-build chains (codegen vs a live
  service) that a production `bake` does not model.

  <stack>            stack directory (relative to repo root) carrying [<root>.dev]
  --profile NAME     host profile to render for (default: active profile)
  --no-prebuild      skip the prebuild steps (re-run the dev server only)
  --define-root PATH override repo root (no parent walking)
""",
    "secrets": """\
ciu secrets list [-d PATH] [--define-root PATH]
ciu secrets reset [-d PATH] [--name N] [-y] [--define-root PATH]
  Inspect or delete materialised secret store files (S4.25).

  -d PATH        stack directory (default: cwd)
  --define-root PATH
                 override repository root (alias: --root-folder)
  --name N       restrict reset to one secret name
  -y, --yes      assume yes to prompts
""",
    "host-secrets": """\
ciu host-secrets <host> [--materialize | --list | --path NAME] [-y]
  Host-scoped local secrets (S14.3a / CIU-35): ASK_EXTERNAL / GEN_LOCAL
  entries declared under [deploy.hosts.<host>.secrets], materialized under
  the project store's hosts/<host>/ namespace — resolvable BEFORE any Vault
  exists on the target. Explicit-only: values are never printed and nothing
  materializes implicitly inside ssh/up.

  --materialize   resolve all declared entries (prompt rules identical to
                  stack ASK_EXTERNAL: TTY + not -y); prints store file paths
  --list          print entry names + store-file existence (never values)
  --path NAME     print the store file path for one declared entry
  -y, --yes       with --materialize, skip interactive prompts (S4.13)
""",
    "check": """\
ciu check [--profile NAME] [--live] [--phases N,M] [--define-root PATH]
  Validate the requires/provides dependency graph across the selection (no deploy).

  --profile NAME     restrict to the named host profile (repeatable)
  --live             probe live Vault/Postgres/MinIO/Consul/Docker state too
  --define-root PATH override repo root (alias: --root-folder)
  --phases N,M       restrict to the given phase numbers
""",
    "graph": """\
ciu graph [--format mermaid|dot|json] [--profile NAME] [--phases N,M]
  Render the requires/provides dependency graph to STDOUT; this never deploys.
  Pipe Mermaid or DOT output into documentation/Graphviz as needed.

  --format FMT       mermaid (default), dot (Graphviz), or json
  --profile NAME     restrict to the named host profile (repeatable)
  --phases N,M       restrict to the given phase numbers
  --define-root PATH override repo root (alias: --root-folder)
""",
    "ssh": """\
ciu ssh <host> [--admin] [-- <cmd...>]
  Open an interactive shell or run a command on a remote host.
  Host config is read from .ciu.hosts.toml or ~/.ciu/hosts.toml.

  <host>         name of the host in the hosts inventory
  --admin        use the admin key/user (higher-privilege access)
  -- <cmd...>    command to run (default: interactive shell)
""",
}


def _print_verb_help(verb: str) -> None:
    """Print the verb-scoped help block (CIU-7); falls back to top-level usage."""
    block = _VERB_HELP.get(verb)
    if block is None:
        print(_USAGE.format(ver=get_cli_version()))
    else:
        print(f"CIU {get_cli_version()}\n")
        print(block, end="")


def _load_remote_config(repo_root: Path) -> dict:
    """Load configuration before a remote SSH/activation operation.

    Remote transports may need this configuration for Vault-backed host
    credentials. Treating a malformed config as an empty mapping changes that
    security contract, so failure stops before any transport is opened.
    """
    from .deploy import load_global_config

    try:
        return load_global_config(repo_root)
    except Exception as exc:
        print(
            f"[ERROR] could not load global configuration for remote operation: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def _push_host(
    host_cfg: dict,
    host_label: str,
    *,
    repo_root: Path,
    config: dict,
    remaining: list[str],
    extra_exports: str = "",
) -> tuple[str, int]:
    """Push-deploy ONE host: docker_optional advisory, bundle sync, remote up.

    The ONE push implementation (review finding B3) shared by `ciu up --host`
    and each host in `ciu up --layout` (S7.5c) — the two used to reimplement
    this loop independently and had already drifted (the layout path lacked
    the docker_optional advisory). *extra_exports* is a pre-quoted
    ``export VAR=val; ...`` prefix for the remote command (the layout's
    CIU_LAYOUT* vars); empty for the plain --host path.

    Returns ``(stage, rc)`` where *stage* is ``"sync"`` if the bundle
    transfer failed (never reaching the remote) or ``"exec"`` for the
    remote-command result (rc == 0 on success). Never raises SystemExit —
    callers own their own error text and exit semantics, which already
    differ between --host (silent propagation) and --layout (named
    remainder).
    """
    # Advisory (S14.6): a docker-optional host has no docker; the
    # render-on-target path here needs it. Nudge, but do not block.
    if host_cfg.get("docker_optional"):
        print(
            f"[WARN] Host '{host_label}' is marked docker_optional but you are using "
            "the docker render-on-target path. Did you mean 'ciu up --host "
            f"{host_label} --thin'? (S14.6)",
            file=sys.stderr,
        )
    bundle_dir = host_cfg.get("bundle_dir", "/opt/ciu/current")
    from .transport_ssh import ssh_exec, ssh_sync
    rc = ssh_sync(host_cfg, str(repo_root), bundle_dir, config=config, repo_root=repo_root)
    if rc != 0:
        return "sync", rc
    # Pass the whole command as ONE argv element: ssh space-joins remote
    # args into a single string for the remote login shell to re-parse, so
    # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
    # shell ssh spawns already interprets the operators natively.
    remote_cmd = (
        extra_exports
        + f"cd {shlex.quote(str(bundle_dir))} && ciu env generate && ciu render && "
        + shlex.join(["ciu", "up", *remaining])
    )
    rc = ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root)
    return "exec", rc


def _wants_verb_help(verb: str, rest: list[str]) -> bool:
    """True when `-h`/`--help` should print the verb's own help.

    `env generate --help` is excluded so its argparse help is reachable.
    """
    if "-h" not in rest and "--help" not in rest:
        return False
    if verb == "env" and rest and rest[0] == "generate":
        return False
    return True


def _env_show() -> int:
    """Walk up from cwd to find and print ciu.env key=value pairs."""
    current = Path.cwd()
    while True:
        candidate = current / WORKSPACE_ENV
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    print(line)
            return 0
        parent = current.parent
        if parent == current:
            break
        current = parent
    print(f"[INFO] No {WORKSPACE_ENV} found. Run: ciu env generate", file=sys.stderr)
    return 1


def _env_generate(rest: list[str]) -> int:
    """Handle `ciu env generate [--define-root PATH]`."""
    import argparse as _ap
    p = _ap.ArgumentParser(prog="ciu env generate", add_help=True)
    p.add_argument("--define-root", "--root-folder", dest="define_root",
                   type=Path, default=None, metavar="PATH",
                   help="Override repository root directory (no parent walking)")
    p.add_argument("--identity-only", action="store_true", default=False,
                   help=_ap.SUPPRESS)
    opts = p.parse_args(rest)
    if opts.identity_only:
        from .workspace_env import generate_ciu_env, resolve_env_root
        root = resolve_env_root(Path.cwd(), opts.define_root, GLOBAL_CONFIG_DEFAULTS)
        generate_ciu_env(root)
        return 0
    from .deploy import action_generate_env
    return action_generate_env(opts.define_root, Path.cwd())


def _iops_baseline(rest: list[str]) -> int:
    """Handle `ciu iops-baseline [--path P] [--runtime N] [--force]` (S15.9)."""
    import argparse as _ap
    from .governance import run_iops_baseline
    p = _ap.ArgumentParser(prog="ciu iops-baseline", add_help=False)
    p.add_argument("--path", dest="path", type=Path, default=None, metavar="PATH")
    p.add_argument("--runtime", dest="runtime", type=int, default=10, metavar="N")
    p.add_argument("--force", action="store_true", default=False)
    opts = p.parse_args(rest)
    if opts.runtime < 1:
        print("ciu iops-baseline: --runtime must be a positive integer.", file=sys.stderr)
        return 2
    return run_iops_baseline(opts.path, runtime_s=opts.runtime, force=opts.force)


def _ksm(rest: list[str]) -> int:
    """Handle `ciu ksm build [--force]` (S15.17, CIU-17).

    The explicit verb. `ciu up`/`ciu render` also build the shim implicitly when
    `governance.ksm_optin = "builtin"` and no cached artifact exists, so this is
    a convenience (pre-warm a fresh worktree, force a rebuild, see the errors
    directly) rather than a required step.
    """
    import argparse as _ap

    from . import ksm as ksm_mod
    from .dev import resolve_repo_root
    # CIU-10's reconciliation lives here (a pre-set PHYSICAL_REPO_ROOT wins only
    # when it agrees with mountinfo) — reuse it rather than re-deriving a second,
    # weaker answer to the same question.
    from .workspace_env import _detect_physical_repo_root

    p = _ap.ArgumentParser(prog="ciu ksm", add_help=False)
    p.add_argument("action", choices=["build"])
    p.add_argument("--force", action="store_true", default=False)
    p.add_argument("--define-root", dest="define_root", default=None, metavar="PATH")
    opts = p.parse_args(rest)

    repo_root = resolve_repo_root(opts.define_root, Path.cwd())
    try:
        physical_root = _detect_physical_repo_root(repo_root)
        path = ksm_mod.build(repo_root, physical_root, force=opts.force)
    except ksm_mod.KsmBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(path)
    return 0


def _provenance(rest: list[str]) -> int:
    """Handle `ciu provenance [--ignore-mismatch] [--no-preflight] [--json]`.

    Verify that the RUNNING containers were built from the commit under test,
    before a live/integration lane is allowed to produce evidence. Standalone
    today; this is the check `ciu test` will call once that surface exists
    (docs/DESIGN-NOTES.md D7).

    This is the ONLY place that turns `verify_running_provenance`'s verdict
    into prose, a raise, or a warning — the function itself never prints and
    never raises, so `--json` can print ONLY the JSON document, never prose
    mixed onto the same stream.
    """
    import argparse as _ap
    import json as _json

    p = _ap.ArgumentParser(prog="ciu provenance", add_help=False)
    p.add_argument("--ignore-mismatch", "--force", dest="ignore_mismatch",
                   action="store_true", default=False)
    p.add_argument("--no-preflight", action="store_true", default=False)
    p.add_argument("--json", dest="json_output", action="store_true", default=False)
    p.add_argument("--define-root", dest="define_root", default=None, metavar="PATH")
    opts = p.parse_args(rest)

    # This is an explicit break-glass bypass, not a lesser provenance verdict.
    # It must happen before configuration, Git, and Docker access: each of those
    # could independently refuse or fail while the operator requested no check.
    # A JSON result would falsely look like evidence, so reject that combination
    # rather than inventing a synthetic "skipped" verdict outside S17.3's grammar.
    if opts.no_preflight:
        if opts.json_output:
            p.error("--no-preflight cannot be combined with --json: no provenance verdict is produced")
        print("[INFO] --no-preflight: skipping provenance check")
        return 0

    # The evidence path deliberately imports its deployment closure only after
    # the break-glass return.  A requested no-check bypass must remain useful
    # even when that closure (or its optional environment) is itself broken.
    from .deploy import (
        ProvenanceResult,
        load_global_config,
        verify_running_provenance,
        warn,
    )
    from .dev import resolve_repo_root

    repo_root = resolve_repo_root(opts.define_root, Path.cwd())
    try:
        config = load_global_config(repo_root)
    except Exception as exc:
        print(f"ciu provenance: could not load the global config: {exc}",
              file=sys.stderr)
        return 2

    deploy_cfg = config.get("deploy", {})
    project, env_tag = deploy_cfg.get("project_name"), deploy_cfg.get("environment_tag")
    if not project or not env_tag:
        # No instance identity means no way to tell THIS instance's containers
        # from a sibling worktree's, and a host-wide verdict would be wrong in
        # both directions. Refuse to answer rather than answer wrongly. Emitted
        # here, BEFORE verify_running_provenance is ever called — there is no
        # project_prefix to scope a check with.
        result = ProvenanceResult(
            schema_version=2, instance=None, commit_under_test=None,
            tree_state=None, containers=None, overall="refused-no-identity",
        )
        if opts.json_output:
            print(_json.dumps(result.to_dict(), indent=2))
        else:
            print("ciu provenance: deploy.project_name and deploy.environment_tag "
                  "are required to scope the check to this instance (S8.7).",
                  file=sys.stderr)
        return 2

    # CIU-39: the declared vendor baseline ([deploy.provenance] vendor_images).
    # Malformed declarations refuse loudly — a silently ignored declaration
    # would certify exactly the deployment it was written to vouch for.
    raw_vendor = deploy_cfg.get("provenance", {}).get("vendor_images", [])
    if not isinstance(raw_vendor, list) or any(
        not isinstance(v, str) or not v.strip() for v in raw_vendor
    ):
        print(
            "ciu provenance: [deploy.provenance] vendor_images must be a list "
            "of non-empty image references (e.g. \"hashicorp/vault:1.15\").",
            file=sys.stderr,
        )
        return 2
    vendor_images = [v.strip() for v in raw_vendor]

    result = verify_running_provenance(f"{project}-{env_tag}", vendor_images=vendor_images)

    if opts.json_output:
        print(_json.dumps(result.to_dict(), indent=2))
        if result.overall == "mismatch" and not opts.ignore_mismatch:
            return 2
        return 0

    if result.overall == "mismatch":
        from .deploy import _image_reference_name

        mismatches = [c for c in (result.containers or []) if c.status == "mismatch"]
        declared_names = {_image_reference_name(v) for v in vendor_images}
        detail_lines = []
        for c in mismatches:
            if _image_reference_name(c.image) in declared_names:
                # Vendor drift: the declaration's artifact was swapped — the
                # evidence is reference-vs-declaration, not commit-vs-label.
                detail_lines.append(
                    f"  {c.name} ({c.image}): not the declared vendor reference"
                )
            else:
                detail_lines.append(
                    f"  {c.name} ({c.image}): running {c.labelled_revision}, "
                    f"testing {result.commit_under_test}"
                )
        detail = "\n".join(detail_lines)
        message = (
            f"[S17] {len(mismatches)} running container(s) disagree with their "
            f"expectation:\n{detail}\n"
            "Rebuild and redeploy (`ciu bake` + `ciu up`) so the result describes "
            "the code under test, correct the declaration for drifted vendor "
            "images, or pass --ignore-mismatch to run anyway (the result then "
            "describes whatever is actually running)."
        )
        if not opts.ignore_mismatch:
            print(message, file=sys.stderr)
            return 2
        # --ignore-mismatch downgrades the refusal to a warning and FALLS
        # THROUGH to the "provenance OK" print at the bottom of this
        # function — byte-identical to the OLD CLI: verify_running_provenance
        # used to warn and return normally on this path, and the old
        # cli._provenance then always printed the OK line afterward for any
        # clean run that didn't raise. Yes, a warning immediately followed by
        # "OK" reads as self-contradictory — that contradiction is the
        # documented old behaviour this function promises to reproduce
        # byte-for-byte (S17.3/O1), not a new design choice.
        warn(message)

    if result.overall == "not-verified-dirty":
        warn(
            "[S17] working tree is dirty — provenance NOT verified. Uncommitted "
            "changes are in no image, so no running container can match this "
            "tree; commit before treating a live result as evidence about this "
            "code."
        )
        return 0

    if result.overall == "not-verified-unknown":
        # Not a git checkout — nothing to compare against. Silent, as before:
        # this is not an adverse finding, just nothing to check.
        return 0

    if result.overall == "not-verified-no-evidence":
        warn(
            "[S17] provenance could not be verified — no checkable evidence "
            "(docker unavailable, or no containers with a usable revision "
            "label were found). This is not a refusal; rerun where docker is "
            "reachable for a real verdict, or inspect --json for the full "
            "document."
        )
        return 0

    # Reached for "verified-match", and for "mismatch" + --ignore-mismatch
    # (the fall-through above). Every other case returned earlier, so this
    # never claims "OK" for a run that could not check anything. With declared
    # vendor pins (CIU-39) the OK names what was actually verified: the commit
    # under test and/or the declared vendor references.
    vendor_pinned = [
        c for c in (result.containers or []) if c.status == "vendor-pinned"
    ]
    if vendor_pinned:
        print(
            f"provenance OK — running containers match {result.commit_under_test} "
            f"or their declared vendor references ({len(vendor_pinned)} pinned)"
        )
    else:
        print(f"provenance OK — running containers match {result.commit_under_test}")
    return 0


def _worktree_exec(rest: list[str], resolve_repo_root) -> int:
    """`ciu worktree exec LOGICAL [--target ALIAS] -- ARGV...`.

    Parsed manually because argparse REMAINDER cannot both consume a
    `--target` option and keep a `--` separator byte-identical for the child.
    The separator is mandatory; a leading-dash argument is never misparsed as
    a CIU flag. Returns the exact child exit code.
    """
    from . import worktree as wt_mod

    if len(rest) < 2:
        raise wt_mod.WorktreeError(
            "[S16] `ciu worktree exec LOGICAL [--target ALIAS] -- ARGV...` "
            "requires a logical name and a `--` separator"
        )
    logical_name = rest[1]
    define_root: str | None = None
    target_alias: str | None = None
    argv: list[str] = []
    i = 2
    while i < len(rest):
        token = rest[i]
        if token == "--define-root":
            if i + 1 >= len(rest):
                raise wt_mod.WorktreeError("[S16] --define-root requires a PATH")
            define_root = rest[i + 1]
            i += 2
            continue
        if token == "--target":
            if i + 1 >= len(rest):
                raise wt_mod.WorktreeError("[S16] --target requires an alias")
            target_alias = rest[i + 1]
            i += 2
            continue
        if token == "--":
            argv = ["--"] + rest[i + 1:]
            break
        raise wt_mod.WorktreeError(
            f"[S16] unexpected argument {token!r} for `ciu worktree exec`; "
            "usage: ciu worktree exec LOGICAL [--target ALIAS] -- ARGV..."
        )
    repo_root = resolve_repo_root(define_root, Path.cwd())
    if target_alias is not None:
        return wt_mod.exec_target_instance(
            repo_root, logical_name, target_alias, argv
        )
    return wt_mod.exec_instance(repo_root, logical_name, argv)


def _worktree(rest: list[str]) -> int:
    """Handle the S16 managed-worktree lifecycle."""
    import argparse as _ap

    from . import worktree as wt_mod
    from .dev import resolve_repo_root

    if rest and rest[0] == "exec":
        try:
            return _worktree_exec(rest, resolve_repo_root)
        except wt_mod.WorktreeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    p = _ap.ArgumentParser(prog="ciu worktree", add_help=False)
    sub = p.add_subparsers(dest="action", required=True)

    p_add = sub.add_parser("add", add_help=False)
    p_add.add_argument("name")
    p_add.add_argument("--base", default="main", metavar="REF")
    p_add.add_argument("--profile", default=None, metavar="P1,P2")
    p_add.add_argument("--worktree-dir", dest="worktree_dir",
                       default=wt_mod.DEFAULT_WORKTREE_DIR, metavar="DIR")
    p_add.add_argument("--shared-infra", dest="shared_infra", default=None,
                       metavar="REF")
    p_add.add_argument("--shared-infra-services", dest="shared_infra_services",
                       default=None, metavar="S1,S2")
    p_add.add_argument("--shared-infra-ref-projects", dest="shared_infra_ref_projects",
                       default=None, metavar="R1,R2")
    p_add.add_argument("--json", action="store_true", default=False)

    def add_create_options(parser) -> None:
        parser.add_argument("--base", default="main", metavar="REF")
        parser.add_argument("--profile", default=None, metavar="P1,P2")
        parser.add_argument("--worktree-dir", dest="worktree_dir",
                            default=wt_mod.DEFAULT_WORKTREE_DIR, metavar="DIR")
        parser.add_argument("--name", dest="display_name", default=None, metavar="DISPLAY")
        parser.add_argument("--prefix", default=None, metavar="PROJECT_OR_COMPONENT")
        parser.add_argument("--feature", default=None, metavar="FEATURE")
        parser.add_argument("--branch", default=None, metavar="BRANCH")
        parser.add_argument("--path", type=Path, default=None, metavar="PATH")
        parser.add_argument("--shared-infra", dest="shared_infra", default=None, metavar="REF")
        parser.add_argument("--shared-infra-services", dest="shared_infra_services",
                            default=None, metavar="S1,S2")
        parser.add_argument("--shared-infra-ref-projects", dest="shared_infra_ref_projects",
                            default=None, metavar="R1,R2")
        parser.add_argument("--json", action="store_true", default=False)

    p_create = sub.add_parser("create", add_help=False)
    p_create.add_argument("logical_name")
    add_create_options(p_create)

    p_ensure = sub.add_parser("ensure", add_help=False)
    p_ensure.add_argument("logical_name")
    add_create_options(p_ensure)

    p_adopt = sub.add_parser("adopt", add_help=False)
    p_adopt.add_argument("logical_name")
    p_adopt.add_argument("path")
    p_adopt.add_argument("--profile", default=None, metavar="P1,P2")
    p_adopt.add_argument("--shared-infra", dest="shared_infra", default=None, metavar="REF")
    p_adopt.add_argument("--shared-infra-services", dest="shared_infra_services",
                         default=None, metavar="S1,S2")
    p_adopt.add_argument("--shared-infra-ref-projects", dest="shared_infra_ref_projects",
                         default=None, metavar="R1,R2")
    p_adopt.add_argument("--json", action="store_true", default=False)

    p_rm = sub.add_parser("rm", add_help=False)
    p_rm.add_argument("name")
    p_rm.add_argument("-y", "--yes", action="store_true", default=False)
    p_rm.add_argument("--force", action="store_true", default=False)
    p_rm.add_argument("--json", action="store_true", default=False)

    p_list = sub.add_parser("list", add_help=False)
    p_list.add_argument("--json", action="store_true", default=False)

    p_inspect = sub.add_parser("inspect", add_help=False)
    p_inspect.add_argument("logical_name")
    p_inspect.add_argument("--json", action="store_true", default=False)

    p_up = sub.add_parser("up", add_help=False)
    p_up.add_argument("logical_name")

    # `exec` is parsed manually in `_worktree_exec` (argparse REMAINDER can
    # neither consume a `--target` option nor keep a `--` separator intact),
    # so no exec subparser is registered here.

    for parser in (
        p, p_add, p_create, p_ensure, p_adopt, p_rm, p_list, p_inspect,
        p_up,
    ):
        parser.add_argument("--define-root", dest="define_root", default=None,
                            metavar="PATH")
    opts = p.parse_args(rest)

    # The PRIMARY checkout. `git worktree` operations are repo-wide, so they run
    # from here even when the target is another checkout.
    repo_root = resolve_repo_root(getattr(opts, "define_root", None), Path.cwd())

    try:
        def emit_record(operation: str, record) -> None:
            if getattr(opts, "json", False):
                print(json.dumps(
                    wt_mod.build_instance_document(operation, record),
                    sort_keys=True,
                ))
            else:
                print(f"worktree ready: {record.git_worktree_path}")
                print(f"  CIU root: {record.ciu_root}")
                print(f"  next: cd {record.ciu_root} && ciu up")

        if opts.action == "add":
            path = wt_mod.add(
                repo_root, opts.name, base=opts.base, profile=opts.profile,
                worktree_dir=opts.worktree_dir,
                shared_infra=opts.shared_infra,
                shared_infra_services=opts.shared_infra_services,
                shared_infra_ref_projects=opts.shared_infra_ref_projects,
            )
            if getattr(opts, "json", False):
                record = wt_mod.find_instance_record(repo_root, opts.name)
                if record is None:
                    raise wt_mod.WorktreeError(
                        f"[S16] add completed at {path}, but no managed record was found"
                    )
                emit_record("add", record)
            else:
                print(f"worktree ready: {path}")
                print(f"  next: cd {path} && ciu up")
            return 0

        if opts.action in ("create", "ensure"):
            lifecycle = wt_mod.create if opts.action == "create" else wt_mod.ensure
            record = lifecycle(
                repo_root, opts.logical_name, base=opts.base, profile=opts.profile,
                worktree_dir=opts.worktree_dir, display_name=opts.display_name,
                prefix=opts.prefix, feature=opts.feature, branch=opts.branch,
                path=opts.path, shared_infra=opts.shared_infra,
                shared_infra_services=opts.shared_infra_services,
                shared_infra_ref_projects=opts.shared_infra_ref_projects,
            )
            emit_record(opts.action, record)
            return 0

        if opts.action == "adopt":
            record = wt_mod.adopt(
                repo_root, opts.logical_name, opts.path, profile=opts.profile,
                shared_infra=opts.shared_infra,
                shared_infra_services=opts.shared_infra_services,
                shared_infra_ref_projects=opts.shared_infra_ref_projects,
            )
            emit_record("adopt", record)
            return 0

        if opts.action == "rm":
            if getattr(opts, "json", False):
                print(json.dumps(
                    wt_mod.remove_document(
                        repo_root, opts.name, yes=opts.yes, force=opts.force
                    ),
                    sort_keys=True,
                ))
            else:
                path = wt_mod.remove(
                    repo_root, opts.name, yes=opts.yes, force=opts.force
                )
                print(f"removed: {path}")
            return 0

        if opts.action == "inspect":
            doc = wt_mod.inspect_instance(repo_root, opts.logical_name)
            if getattr(opts, "json", False):
                print(json.dumps(doc, sort_keys=True))
            else:
                git = doc["git"]
                print(f"worktree inspect: {doc['instance']['logical_name']}")
                print(f"  status: {doc['status']}")
                print(f"  git path: {git['path']}")
                print(f"  branch: {git['branch']}")
                print(f"  HEAD: {git['head']}")
                print(f"  dirty: {git['dirty']}")
            return 0

        if opts.action == "up":
            return wt_mod.up_instance(repo_root, opts.logical_name)

        # Every action above returned; the only remaining action is "list"
        # (argparse's required subparsers make it one of the registered set).
        if getattr(opts, "json", False):
            print(json.dumps(wt_mod.list_instances(repo_root), sort_keys=True))
        else:
            for info in wt_mod.list_worktrees(repo_root):
                tag = "  (primary)" if info.is_primary else ""
                print(f"{info.head}  {info.branch:<40} {info.path}{tag}")
        return 0
    except wt_mod.WorktreeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def main() -> None:
    raw = consume_cli_flags(sys.argv[1:])

    if not raw or raw[0] in ("-h", "--help"):
        print(_USAGE.format(ver=get_cli_version()))
        raise SystemExit(0)

    # CIU-16: `version` is a VERB, matching every other CIU verb and the
    # estate's other CLIs. There is deliberately no `--version` alias — a
    # greenfield tool carries one spelling per thing, not two.
    if raw[0] == "version":
        print(f"ciu {get_cli_version()}")
        raise SystemExit(0)

    verb = raw[0]
    rest = raw[1:]

    # S15.18 (CIU-17) — ad-hoc KSM override, stripped here so it works on any
    # verb that renders (up, render, ...) without each one declaring it. It sets
    # the ambient CIU_KSM that governance.resolve_ksm_optin reads, so the flag
    # and the env var are ONE resolution point, not two that can disagree.
    if "--ksm" in rest or "--no-ksm" in rest:
        if "--ksm" in rest and "--no-ksm" in rest:
            print("ciu: --ksm and --no-ksm are mutually exclusive.", file=sys.stderr)
            raise SystemExit(2)
        from .governance import BUILTIN_KSM, KSM_ENV_VAR
        os.environ[KSM_ENV_VAR] = BUILTIN_KSM if "--ksm" in rest else "off"
        rest = [a for a in rest if a not in ("--ksm", "--no-ksm")]

    # CIU-7 / S10.1: intercept `-h`/`--help` per verb BEFORE forwarding to the
    # legacy deploy/engine argparse, so each verb shows its own options.
    if _wants_verb_help(verb, rest):
        _print_verb_help(verb)
        raise SystemExit(0)

    if verb == "env":
        if rest and rest[0] == "generate":
            raise SystemExit(_env_generate(rest[1:]))
        raise SystemExit(_env_show())

    elif verb == "iops-baseline":
        raise SystemExit(_iops_baseline(rest))

    elif verb == "render":
        if "--host" in rest:
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            opts, remaining = p.parse_known_args(rest)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            from .transport_ssh import ssh_exec
            host_cfg = get_host(repo_root, opts.host)
            remote_cmd = shlex.join(["ciu", "render", *remaining])
            # Pass the whole command as ONE argv element: ssh space-joins remote
            # args into a single string for the remote login shell to re-parse, so
            # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
            # shell ssh spawns already interprets the operators natively.
            raise SystemExit(ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root))
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--render-toml"] + rest))

    elif verb == "profiles":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--list-profiles"] + rest))

    elif verb == "layouts":
        # S7.5c — pure listing of DECLARED layouts (no validation, no
        # inventory requirement); `ciu up --layout` is the validating consumer.
        repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
        config = _load_remote_config(repo_root)
        from .deploy_pkg.layouts import list_layouts
        rows = list_layouts(config)
        if not rows:
            print("(no layouts declared)")
            raise SystemExit(0)
        for name, environment, hosts in rows:
            env_part = f" environment={environment}" if environment else ""
            hosts_part = ", ".join(hosts) if hosts else "(no hosts)"
            print(f"{name}:{env_part} hosts=[{hosts_part}]")
        raise SystemExit(0)

    elif verb == "up":
        if "--layout" in rest:
            # S7.5c — push-deploy a named host→bundles plan. The layout owns
            # host order and bundles, so --host/--profile are excluded: a
            # passthrough --profile would silently override the exported
            # CIU_SERVICES_PROFILE (S7.5 CLI precedence).
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--layout", dest="layout", default=None)
            opts, remaining = p.parse_known_args(rest)
            # B2 (review): a plain `"--host" in rest` membership check misses
            # the `--profile=core` equals form (argparse leaves it in
            # `remaining` untouched since only --layout is registered here),
            # and did not guard --dir/--thin/--bootstrap/--rollback at all —
            # all of which forward into the remote `ciu up` argv and either
            # silently override the layout's exported CIU_SERVICES_PROFILE
            # (--profile, S7.5 CLI precedence) or die opaquely on the remote
            # (--dir/--thin/--bootstrap/--rollback have no meaning without a
            # local --host push). Prefix-aware so both `--flag value` and
            # `--flag=value` forms are caught.
            _LAYOUT_FORBIDDEN = (
                "--profile", "--host", "--dir", "--thin", "--bootstrap", "--rollback",
            )
            if any(
                a == flag or a.startswith(flag + "=")
                for a in remaining
                for flag in _LAYOUT_FORBIDDEN
            ):
                print(
                    "[S7.5c] --layout is mutually exclusive with --host and "
                    "--profile (and with --dir/--thin/--bootstrap/--rollback, "
                    "which only apply to the --host push path) — the layout "
                    "owns the host order and the bundles.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            config = _load_remote_config(repo_root)
            from .deploy_pkg.layouts import resolve_layout
            from .hosts import get_host, load_hosts
            try:
                layout = resolve_layout(config, load_hosts(repo_root), opts.layout)
            except ValueError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                raise SystemExit(2)
            # Pure orchestration: delegate each host to _push_host (shared
            # with `up --host` — review finding B3, was reimplemented and had
            # already drifted), with the layout's exports prepended to the
            # single remote argv string (one-argv discipline).
            for index, host in enumerate(layout.hosts):
                not_deployed = layout.hosts[index + 1:]
                remainder = ", ".join(not_deployed) or "(none)"
                try:
                    host_cfg = get_host(repo_root, host)
                except ValueError as exc:
                    print(
                        f"[ERROR] layout '{layout.name}': {exc}; "
                        f"not deployed: {remainder}.",
                        file=sys.stderr,
                    )
                    raise SystemExit(2)
                exports = (
                    f"export CIU_SERVICES_PROFILE={shlex.quote(','.join(layout.bundles[host]))}; "
                    f"export CIU_LAYOUT={shlex.quote(layout.name)}; "
                    f"export CIU_LAYOUT_HOST={shlex.quote(host)}; "
                    f"export CIU_DEPLOY_ENVIRONMENT={shlex.quote(layout.environment)}; "
                )
                stage, rc = _push_host(
                    host_cfg, host,
                    repo_root=repo_root, config=config, remaining=remaining,
                    extra_exports=exports,
                )
                if rc != 0:
                    verb_text = "bundle sync failed" if stage == "sync" else "up failed"
                    print(
                        f"[ERROR] layout '{layout.name}': {verb_text} on host "
                        f"'{host}' ({rc}); not deployed: {remainder}.",
                        file=sys.stderr,
                    )
                    raise SystemExit(rc)
            raise SystemExit(0)
        elif "--host" in rest:
            # Remote push-deploy path
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            p.add_argument("--thin", action="store_true", default=False)
            p.add_argument("--bootstrap", action="store_true", default=False)
            p.add_argument("--rollback", action="store_true", default=False)
            opts, remaining = p.parse_known_args(rest)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            bundle_dir = host_cfg.get("bundle_dir", "/opt/ciu/current")

            if opts.thin:
                # Docker-optional push→activate path (S14.6). Pushes an artifact
                # to bundle_dir, then runs the project's shell activation contract
                # (bootstrap|apply|health|rollback) — no Docker/Python on target.
                if opts.bootstrap and opts.rollback:
                    print("[ERROR] --bootstrap and --rollback are mutually exclusive.", file=sys.stderr)
                    raise SystemExit(2)
                from .activate import run_thin_up
                try:
                    rc = run_thin_up(
                        host_cfg,
                        config=config,
                        repo_root=repo_root,
                        bundle_dir=bundle_dir,
                        bootstrap=opts.bootstrap,
                        rollback=opts.rollback,
                        remaining=remaining,
                    )
                except ValueError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    raise SystemExit(2)
                raise SystemExit(rc)

            # --bootstrap/--rollback only apply to the --thin activation contract.
            if opts.bootstrap or opts.rollback:
                print("[ERROR] --bootstrap/--rollback require --thin (the docker-optional activation path).", file=sys.stderr)
                raise SystemExit(2)

            # B3 (review): the ONE push implementation, shared with the
            # `--layout` loop above — behavior here is unchanged (same
            # docker_optional advisory, same sync-then-exec, same silent rc
            # propagation on failure the existing tests pin).
            _stage, rc = _push_host(
                host_cfg, opts.host,
                repo_root=repo_root, config=config, remaining=remaining,
            )
            raise SystemExit(rc)
        elif "--dir" in rest:
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--dir", dest="dir", default=None)
            opts, remaining = p.parse_known_args(rest)
            dir_arg = opts.dir or "."
            from .engine import main as engine_main
            raise SystemExit(engine_main(["-d", dir_arg] + remaining))
        else:
            # Profile-based deploy (defaults to active profile when no --profile given)
            from .deploy import main as deploy_main
            raise SystemExit(deploy_main(rest))

    elif verb == "down":
        if "--host" in rest:
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            opts, remaining = p.parse_known_args(rest)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            from .transport_ssh import ssh_exec
            host_cfg = get_host(repo_root, opts.host)
            remote_cmd = shlex.join(["ciu", "down", *remaining])
            # Pass the whole command as ONE argv element: ssh space-joins remote
            # args into a single string for the remote login shell to re-parse, so
            # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
            # shell ssh spawns already interprets the operators natively.
            raise SystemExit(ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root))
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--stop"] + rest))

    elif verb == "clean":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--clean"] + rest))

    elif verb == "health":
        if "--host" in rest:
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            p.add_argument("--thin", action="store_true", default=False)
            opts, remaining = p.parse_known_args(rest)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            if opts.thin:
                # Docker-optional path (S14.6): run the project's 'health' verb
                # of the activation contract instead of remote `ciu health`.
                bundle_dir = host_cfg.get("bundle_dir", "/opt/ciu/current")
                from .activate import run_activation
                try:
                    rc = run_activation(
                        host_cfg, "health",
                        config=config, repo_root=repo_root,
                        bundle_dir=bundle_dir, remaining=remaining,
                    )
                except ValueError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    raise SystemExit(2)
                raise SystemExit(rc)
            from .transport_ssh import ssh_exec
            remote_cmd = shlex.join(["ciu", "health", *remaining])
            # Pass the whole command as ONE argv element: ssh space-joins remote
            # args into a single string for the remote login shell to re-parse, so
            # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
            # shell ssh spawns already interprets the operators natively.
            raise SystemExit(ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root))
        from .deploy import main as deploy_main
        if "--preflight" in rest:
            extra = [r for r in rest if r != "--preflight"]
            raise SystemExit(deploy_main(["--preflight"] + extra))
        raise SystemExit(deploy_main(["--healthcheck"] + rest))

    elif verb == "diagnose":
        import argparse as _ap
        from .diagnose import run as diagnose_run
        p = _ap.ArgumentParser(prog="ciu diagnose", add_help=False)
        p.add_argument("--project", default=None)
        p.add_argument("--logs", type=int, default=100)
        p.add_argument("--json", dest="json_output", action="store_true")
        opts = p.parse_args(rest)
        if opts.logs < 0 or opts.logs > 10_000:
            print("ciu diagnose: --logs must be between 0 and 10000.", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(diagnose_run(project=opts.project, log_lines=opts.logs, json_output=opts.json_output))

    elif verb == "ksm":
        raise SystemExit(_ksm(rest))

    elif verb == "worktree":
        raise SystemExit(_worktree(rest))

    elif verb == "capabilities":
        import argparse as _ap

        from . import worktree as wt_mod
        p = _ap.ArgumentParser(prog="ciu capabilities", add_help=False)
        p.add_argument("--json", action="store_true", default=False)
        opts = p.parse_args(rest)
        if opts.json:
            print(json.dumps(wt_mod.capabilities_document(), sort_keys=True))
        else:
            for identifier in sorted(wt_mod.WORKTREE_CAPABILITIES):
                print(identifier)
        raise SystemExit(0)

    elif verb == "provenance":
        raise SystemExit(_provenance(rest))

    elif verb == "bake":
        from .engine import bake_revision_args
        no_cache = "--no-cache" in rest
        targets = [a for a in rest if a != "--no-cache"]
        cmd = ["docker", "buildx", "bake"] + (targets or ["all"]) + ["--load"]
        # Provenance: stamp the source revision so a running container can be
        # traced back to the commit it was built from (engine.bake_revision_args).
        cmd += bake_revision_args()
        if no_cache:
            cmd.append("--no-cache")
        raise SystemExit(subprocess.call(cmd))

    elif verb == "dev":
        import argparse as _ap
        from .dev import run_dev, resolve_repo_root
        p = _ap.ArgumentParser(prog="ciu dev", add_help=False)
        p.add_argument("stack", nargs="?", default=None)
        p.add_argument("--profile", default=None, metavar="NAME")
        p.add_argument("--no-prebuild", dest="no_prebuild", action="store_true")
        p.add_argument("--define-root", "--root-folder", dest="define_root",
                       type=Path, default=None, metavar="PATH")
        opts = p.parse_args(rest)
        if not opts.stack:
            print("ciu dev: missing <stack>. Run 'ciu dev --help'.", file=sys.stderr)
            raise SystemExit(2)
        repo_root = resolve_repo_root(opts.define_root, Path.cwd())
        raise SystemExit(run_dev(
            opts.stack,
            repo_root=repo_root,
            profile_name=opts.profile,
            no_prebuild=opts.no_prebuild,
        ))

    elif verb == "secrets":
        from .engine import main as engine_main
        raise SystemExit(engine_main(["secrets"] + rest))

    elif verb == "host-secrets":
        # S14.3a / CIU-35 — host-scoped local secrets. Explicit-only: values
        # are NEVER printed and materialization never happens implicitly inside
        # transport verbs.
        import argparse as _ap
        p = _ap.ArgumentParser(add_help=False)
        p.add_argument("host", nargs="?", default=None)
        p.add_argument("--materialize", action="store_true", default=False)
        p.add_argument("--list", action="store_true", default=False)
        p.add_argument("--path", dest="path_name", default=None)
        p.add_argument("-y", "--yes", action="store_true", default=False)
        opts, _ = p.parse_known_args(rest)
        if opts.host is None:
            print(
                "ciu host-secrets <host> [--materialize | --list | --path <name>] "
                "[-y]  (S14.3a)",
                file=sys.stderr,
            )
            raise SystemExit(2)
        modes = [opts.materialize, opts.list, opts.path_name is not None]
        if sum(1 for m in modes if m) != 1:
            print(
                "[S14.3a] choose exactly one of --materialize, --list, --path <name>.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
        from .hosts import get_host_secrets
        try:
            specs = get_host_secrets(repo_root, opts.host)
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(2)
        from .secrets.materialize import host_secret_store
        if opts.list:
            if not specs:
                print("(no host secrets declared)")
            else:
                for name in specs:
                    store = host_secret_store(repo_root, opts.host, name)
                    print(f"{name}  {'present' if store.exists() else 'absent'}")
            raise SystemExit(0)
        if opts.path_name is not None:
            if opts.path_name not in specs:
                print(
                    f"[ERROR] host '{opts.host}' declares no secret '{opts.path_name}'. "
                    f"Declared: {', '.join(sorted(specs)) or '(none)'}.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            print(host_secret_store(repo_root, opts.host, opts.path_name))
            raise SystemExit(0)
        # --materialize
        from .secrets.materialize import materialize_host_secrets
        try:
            results = materialize_host_secrets(
                repo_root,
                opts.host,
                specs,
                assume_yes=opts.yes,
                env=os.environ,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(2)
        if not results:
            print(f"host '{opts.host}': no secrets declared")
        else:
            for name, res in results.items():
                print(f"{name} -> {res.file}")
        raise SystemExit(0)

    elif verb == "check":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--check"] + rest))

    elif verb == "graph":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--graph"] + rest))

    elif verb == "ssh":
        # Parse: ciu ssh <host> [--admin] [-- cmd...]
        import argparse as _ap
        from .hosts import get_host
        from .transport_ssh import ssh_exec
        p = _ap.ArgumentParser(prog="ciu ssh", add_help=False)
        p.add_argument("host", nargs="?", default=None)
        p.add_argument("--admin", action="store_true", default=False)
        # Split on '--' to separate host/flags from remote command
        if "--" in rest:
            sep = rest.index("--")
            ssh_rest = rest[:sep]
            cmd_argv = rest[sep + 1:]
        else:
            ssh_rest = rest
            cmd_argv = []
        opts = p.parse_args(ssh_rest)
        if not opts.host:
            print("ciu ssh: missing <host>. Run 'ciu ssh --help'.", file=sys.stderr)
            raise SystemExit(2)
        # Resolve repo root from env
        repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
        config = _load_remote_config(repo_root)
        host_cfg = get_host(repo_root, opts.host, admin=opts.admin)
        interactive = len(cmd_argv) == 0
        raise SystemExit(ssh_exec(
            host_cfg, cmd_argv,
            config=config,
            repo_root=repo_root,
            interactive=interactive,
            admin=opts.admin,
        ))

    else:
        if verb == "-d" and rest:
            print(
                f"ciu: '-d' is not a verb. Did you mean: ciu up --dir {rest[0]!r}?",
                file=sys.stderr,
            )
        else:
            print(f"ciu: unknown verb '{verb}'. Run 'ciu' for usage.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
