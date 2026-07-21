#!/usr/bin/env python3
"""CIU — Container Infrastructure Utility (compose · init · up)"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .cli_utils import get_cli_version
from .config_constants import WORKSPACE_ENV

_USAGE = """\
CIU {ver} — Container Infrastructure Utility (compose · init · up)
Uses: ciu.global.toml + ciu.env

  ENVIRONMENT
    env                         show ciu.env key=value pairs (read-only)
    env generate [--define-root PATH]
                                generate or refresh ciu.env from system state
    iops-baseline [--path P] [--runtime N] [--force]
                                measure disk randread IOPS (fio) → io-baseline.env (S15.9)

  AUTHORING
    render                      render ciu.global.toml from Jinja2 template
    profiles                    list available host profiles

  STACK ORCHESTRATION
    up   [--profile NAME | --dir PATH]   start Docker Compose stack
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

  SECRETS
    secrets list   [-d PATH]             list materialised secret names
    secrets reset  [--name N] [-y]       delete secret store files

  REMOTE (requires hosts file — see .ciu.hosts.toml)
    ssh <host> [--admin] [-- cmd...]            remote shell or command (access plane)
    up  --host <name> [selection flags]         push-deploy: bundle-sync + render-on-target
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
ciu render [--profile NAME] [--define-root PATH]
  Render ciu.global.toml + per-stack ciu.toml from their Jinja2 templates.

  --profile NAME       host profile to render for (default: active profile)
  --define-root PATH   override repo root (no parent walking)
""",
    "profiles": """\
ciu profiles
  List available host profiles. Takes no options.
""",
    "up": """\
ciu up [--profile NAME | --dir PATH] [--phases N,M] [--dry-run] [-y] [--ignore-errors]
ciu up --host NAME [selection...]            # push-deploy, render-on-target (needs docker on target)
ciu up --host NAME --thin [--bootstrap | --rollback] [selection...]  # docker-optional push→activate
  Render + materialise secrets + start the Docker Compose stack(s).

  --profile NAME     deploy the named host profile (default: active profile)
  --dir PATH         deploy a single stack directory (engine path)
  --phases N,M       restrict to the given phase numbers
  --dry-run          render everything but do not call docker
  -y, --yes          assume yes to prompts
  --ignore-errors    continue past a failing stack

  Remote (SPEC S14):
  --host NAME        push-deploy to a host from the inventory (.ciu.hosts.toml)
  --thin             docker-optional: push an artifact to bundle_dir, then run the
                     host's shell activation contract (bootstrap|apply|health|
                     rollback) — no docker/python needed on the target (S14.6)
  --bootstrap        (with --thin) run the 'bootstrap' verb before 'apply'
  --rollback         (with --thin) run the 'rollback' verb only (no fresh push)
""",
    "down": """\
ciu down [--profile NAME]
  Stop project containers; volumes are preserved (use `ciu clean` to remove them).

  --profile NAME   restrict to the named host profile (default: active profile)
""",
    "clean": """\
ciu clean [--profile NAME] [-y] [--ignore-errors]
  Tear down completely: remove ALL project containers (running AND exited, incl.
  init/sidecars), `docker compose down -v --remove-orphans`, remove project
  volumes and `vol-*` hostdirs, and remove rendered artifacts. The post-clean
  invariant (S6.4) is enforced: zero project containers AND zero project volumes
  remain, else clean fails (exit 1).

  --profile NAME     restrict to the named host profile (default: active profile)
  -y, --yes          assume yes to prompts
  --ignore-errors    continue past a failing stack (best-effort per stack)
""",
    "health": """\
ciu health [--profile NAME]
ciu health --preflight [--strict]
ciu health --host NAME [--thin]
  Run the health gate (S7.7) over the selection, or probe images for missing
  healthcheck tools (--preflight).

  --profile NAME   restrict to the named host profile (default: active profile)
  --preflight      probe rendered compose images for missing healthcheck tools
  --strict         (with --preflight) treat missing tools as an error
  --host NAME      run the health gate on a remote host (SPEC S14)
  --thin           (with --host) run the docker-optional 'health' activation
                   verb instead of remote `ciu health` (S14.6)
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
ciu secrets list [-d PATH]
ciu secrets reset [--name N] [-y]
  Inspect or delete materialised secret store files (S4.25).

  -d PATH        stack directory (default: cwd)
  --name N       restrict reset to one secret name
  -y, --yes      assume yes to prompts
""",
    "check": """\
ciu check [--profile NAME] [--live] [--define-root PATH]
  Validate the requires/provides dependency graph across the selection (no deploy).

  --profile NAME     host profile to check (default: active profile)
  --live             also probe live state (Vault/Postgres/MinIO/Consul/Docker)
  --define-root PATH override repo root (no parent walking)
  --phases N,M       restrict to the given phase numbers
""",
    "graph": """\
ciu graph [--format mermaid|dot|json] [--profile NAME] [--phases N,M]
  Render the requires/provides dependency graph to STDOUT (pipe it into docs).
  Edges go consumer --ref--> provider; a require nobody provides is drawn dashed
  to an UNPROVIDED sentinel.

  --format FMT       mermaid (default), dot (Graphviz), or json
  --profile NAME     host profile to graph (default: active profile)
  --phases N,M       restrict to the given phase numbers
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
    opts = p.parse_args(rest)
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


def main() -> None:
    raw = sys.argv[1:]

    if not raw or raw[0] in ("-h", "--help"):
        print(_USAGE.format(ver=get_cli_version()))
        raise SystemExit(0)

    if raw[0] == "--version":
        print(f"ciu {get_cli_version()}")
        raise SystemExit(0)

    verb = raw[0]
    rest = raw[1:]

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
            try:
                from .deploy import load_global_config
                config = load_global_config(repo_root)
            except Exception:
                config = {}
            from .hosts import get_host
            from .transport_ssh import ssh_exec
            host_cfg = get_host(repo_root, opts.host)
            remote_cmd_parts = ["ciu render"]
            remote_cmd_parts.extend(remaining)
            remote_cmd = " ".join(remote_cmd_parts)
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

    elif verb == "up":
        if "--host" in rest:
            # Remote push-deploy path
            import argparse as _ap
            p = _ap.ArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            p.add_argument("--thin", action="store_true", default=False)
            p.add_argument("--bootstrap", action="store_true", default=False)
            p.add_argument("--rollback", action="store_true", default=False)
            opts, remaining = p.parse_known_args(rest)
            repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
            try:
                from .deploy import load_global_config
                config = load_global_config(repo_root)
            except Exception:
                config = {}
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

            # Advisory (S14.6): a docker-optional host has no docker; the
            # render-on-target path below needs it. Nudge, but do not block.
            if host_cfg.get("docker_optional"):
                print(
                    f"[WARN] Host '{opts.host}' is marked docker_optional but you are using "
                    "the docker render-on-target path. Did you mean 'ciu up --host "
                    f"{opts.host} --thin'? (S14.6)",
                    file=sys.stderr,
                )

            from .transport_ssh import ssh_sync, ssh_exec
            rc = ssh_sync(host_cfg, str(repo_root), bundle_dir, config=config, repo_root=repo_root)
            if rc != 0:
                raise SystemExit(rc)
            # Build remote ciu command
            remote_cmd_parts = [f"cd {bundle_dir} && ciu env generate && ciu render && ciu up"]
            remote_cmd_parts.extend(remaining)
            remote_cmd = " ".join(remote_cmd_parts)
            # Pass the whole command as ONE argv element: ssh space-joins remote
            # args into a single string for the remote login shell to re-parse, so
            # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
            # shell ssh spawns already interprets the operators natively.
            raise SystemExit(ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root))
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
            try:
                from .deploy import load_global_config
                config = load_global_config(repo_root)
            except Exception:
                config = {}
            from .hosts import get_host
            from .transport_ssh import ssh_exec
            host_cfg = get_host(repo_root, opts.host)
            remote_cmd_parts = ["ciu down"]
            remote_cmd_parts.extend(remaining)
            remote_cmd = " ".join(remote_cmd_parts)
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
            try:
                from .deploy import load_global_config
                config = load_global_config(repo_root)
            except Exception:
                config = {}
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
            remote_cmd_parts = ["ciu health"]
            remote_cmd_parts.extend(remaining)
            remote_cmd = " ".join(remote_cmd_parts)
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

    elif verb == "bake":
        no_cache = "--no-cache" in rest
        targets = [a for a in rest if a != "--no-cache"]
        cmd = ["docker", "buildx", "bake"] + (targets or ["all"]) + ["--load"]
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
        # Load config (best effort; transport will use repo_root for vault lazily)
        try:
            from .deploy import load_global_config
            config = load_global_config(repo_root)
        except Exception:
            config = {}
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
