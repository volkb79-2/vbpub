#!/usr/bin/env python3
"""CIU — Container Infrastructure Utility (compose · init · up)"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .cli_utils import get_cli_version

_USAGE = """\
CIU {ver} — Container Infrastructure Utility (compose · init · up)
Uses: ciu.global.toml + .env.ciu

  ENVIRONMENT
    env                         show .env.ciu key=value pairs (read-only)
    env generate [--define-root PATH]
                                generate or refresh .env.ciu from system state

  AUTHORING
    render                      render ciu.global.toml from Jinja2 template
    profiles                    list available host profiles

  STACK ORCHESTRATION
    up   [--profile NAME | --dir PATH]   start Docker Compose stack
    down [--profile NAME]                stop stack (preserve volumes)
    clean                                remove containers and volumes
    health [--profile NAME]              health gate check
    health --preflight [--strict]        probe images for missing healthcheck tools

  DEV-LOOP BUILDS
    bake [targets ...] [--no-cache]      docker buildx bake --load

  SECRETS
    secrets list   [-d PATH]             list materialised secret names
    secrets reset  [--name N] [-y]       delete secret store files
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
ciu env — show .env.ciu key=value pairs (read-only)
ciu env generate [--define-root PATH] — (re)generate .env.ciu from system state

  --define-root PATH   override repo root (no parent walking); for `generate`
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
  Render + materialise secrets + start the Docker Compose stack(s).

  --profile NAME     deploy the named host profile (default: active profile)
  --dir PATH         deploy a single stack directory (engine path)
  --phases N,M       restrict to the given phase numbers
  --dry-run          render everything but do not call docker
  -y, --yes          assume yes to prompts
  --ignore-errors    continue past a failing stack
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
  Run the health gate (S7.7) over the selection, or probe images for missing
  healthcheck tools (--preflight).

  --profile NAME   restrict to the named host profile (default: active profile)
  --preflight      probe rendered compose images for missing healthcheck tools
  --strict         (with --preflight) treat missing tools as an error
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
    """Walk up from cwd to find and print .env.ciu key=value pairs."""
    current = Path.cwd()
    while True:
        candidate = current / ".env.ciu"
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
    print("[INFO] No .env.ciu found. Run: ciu env generate", file=sys.stderr)
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

    elif verb == "render":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--render-toml"] + rest))

    elif verb == "profiles":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--list-profiles"] + rest))

    elif verb == "up":
        if "--dir" in rest:
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
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--stop"] + rest))

    elif verb == "clean":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--clean"] + rest))

    elif verb == "health":
        from .deploy import main as deploy_main
        if "--preflight" in rest:
            extra = [r for r in rest if r != "--preflight"]
            raise SystemExit(deploy_main(["--preflight"] + extra))
        raise SystemExit(deploy_main(["--healthcheck"] + rest))

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
