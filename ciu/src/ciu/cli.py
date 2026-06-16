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

  DEV-LOOP BUILDS
    bake [targets ...] [--no-cache]      docker buildx bake --load

  SECRETS
    secrets list   [-d PATH]             list materialised secret names
    secrets reset  [--name N] [-y]       delete secret store files
"""


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
        raise SystemExit(deploy_main(["--healthcheck"] + rest))

    elif verb == "bake":
        no_cache = "--no-cache" in rest
        targets = [a for a in rest if a != "--no-cache"]
        cmd = ["docker", "buildx", "bake"] + (targets or ["all"]) + ["--load"]
        if no_cache:
            cmd.append("--no-cache")
        raise SystemExit(subprocess.call(cmd))

    elif verb == "secrets":
        from .engine import main as engine_main
        raise SystemExit(engine_main(["secrets"] + rest))

    else:
        print(f"ciu: unknown verb '{verb}'. Run 'ciu' for usage.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
