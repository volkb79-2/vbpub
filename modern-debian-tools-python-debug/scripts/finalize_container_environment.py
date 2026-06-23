#!/usr/bin/env python3
"""mdt devcontainer in-container finalize ("post" script).

Symmetric to the host-side `initialize_container_environment.py`: that one runs
ON THE HOST (devcontainer.json `initializeCommand`) to pre-create bind-mount
sources; THIS one runs INSIDE the container (devcontainer.json
`postCreateCommand`) to finish wiring the dev environment.

It is shipped **baked into the mdt base image** at `/usr/local/bin/` so every
mdt-based devcontainer gets it for free — a consuming repo's `postCreateCommand`
is just:

    "postCreateCommand": "finalize_container_environment.py"

Design tenets
-------------
- **stdlib-only, idempotent, best-effort.** The generic mdt steps below never
  hard-fail finalize (they warn and continue) — a fresh container always comes up.
- **ciu-AGNOSTIC — no hard ciu dependency.** mdt *ships and encourages* ciu, but
  this script NEVER imports, calls, or assumes ciu (or any ciu-rendered stack).
  A repo that does not use ciu gets a fully working devcontainer from mdt alone.
- **Consumer hooks are where repo-specific (and ciu) setup lives.** finalize runs
  the consumer's own scripts from its `.devcontainer/` so they don't have to fork
  this file. The bracketing is:

      .devcontainer/finalize.pre.d/*.sh   (sorted)   ─┐ before generic steps
      <generic mdt steps>                              │
      .devcontainer/finalize.post.d/*.sh  (sorted)   ─┘ after  generic steps

  A single-file form is also honoured: `.devcontainer/finalize.pre.sh` and
  `.devcontainer/finalize.post.sh` (run after the matching `.d` dir).

Hook contract
-------------
- Hooks are run in lexical order (use a numeric prefix: `10-…`, `20-…`).
- A hook that is executable runs directly; otherwise it is run with `bash`.
- Each hook inherits the environment plus these exported context vars:
    MDT_FINALIZE=1            MDT_ENV_TYPE=devcontainer|github_actions|local
    MDT_WORKSPACE_DIR=…       MDT_DEVCONTAINER_DIR=…
    MDT_USER=… MDT_UID=… MDT_GID=… MDT_DOCKER_GID=…
- **Enforcement boundary:** generic mdt steps only WARN on failure (exit stays 0
  for their account). A *consumer* hook that exits non-zero is reported and makes
  finalize's final exit code non-zero (so the build surfaces it) — because that
  hook is where the consumer puts ITS critical setup (e.g. `ciu env generate`).
  By default other hooks still run; set `MDT_FINALIZE_STRICT=1` to abort on the
  first failing hook instead.

Flags
-----
  --no-hooks        run only the generic mdt steps (skip consumer hooks)
  --hooks-only      run only the consumer hooks (skip generic steps)
  --devcontainer-dir PATH   override hook-discovery dir (else $MDT_DEVCONTAINER_DIR
                            or <workspace>/.devcontainer)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# ── tiny logging (no color when not a tty) ──────────────────────────────────
_TTY = sys.stdout.isatty()


def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m" if _TTY else msg


def info(m: str) -> None:
    print(_c("0;34", "[finalize] ") + m, flush=True)


def ok(m: str) -> None:
    print(_c("0;32", "[finalize] ") + m, flush=True)


def warn(m: str) -> None:
    print(_c("1;33", "[finalize] WARN ") + m, file=sys.stderr, flush=True)


def err(m: str) -> None:
    print(_c("0;31", "[finalize] ERROR ") + m, file=sys.stderr, flush=True)


# ── environment detection (generic) ─────────────────────────────────────────
def detect_environment() -> dict:
    if os.environ.get("GITHUB_ACTIONS"):
        env_type = "github_actions"
    elif Path("/.dockerenv").exists() and os.environ.get("REMOTE_CONTAINERS"):
        env_type = "devcontainer"
    else:
        env_type = "local"

    docker_gid = ""
    for sock in ("/var/run/docker-host.sock", "/var/run/docker.sock"):
        try:
            docker_gid = str(os.stat(sock).st_gid)
            break
        except OSError:
            continue

    return {
        "env_type": env_type,
        "user": os.environ.get("USER") or (os.getlogin() if hasattr(os, "getlogin") else "unknown"),
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
        "docker_gid": docker_gid,
        "workspace": str(Path.cwd()),
        "home": os.path.expanduser("~"),
    }


# ── generic mdt steps (best-effort) ─────────────────────────────────────────
_BASHRC = Path(os.path.expanduser("~/.bashrc"))


def _bashrc_block(marker: str, body: str) -> None:
    """Idempotently (re)write a marked block in ~/.bashrc."""
    start, end = f"# >>> mdt {marker}", f"# <<< mdt {marker}"
    try:
        text = _BASHRC.read_text(encoding="utf-8") if _BASHRC.exists() else ""
    except OSError:
        text = ""
    lines, out, skip = text.splitlines(), [], False
    for ln in lines:
        if ln == start:
            skip = True
            continue
        if ln == end:
            skip = False
            continue
        if not skip:
            out.append(ln)
    out += [start, *body.strip("\n").splitlines(), end]
    try:
        _BASHRC.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as e:
        warn(f"could not update ~/.bashrc ({marker}): {e}")


def setup_path() -> None:
    user_bin = Path(os.path.expanduser("~/.local/bin"))
    try:
        user_bin.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _bashrc_block("path", 'export PATH="$HOME/.local/bin:$PATH"')
    ok("~/.local/bin on PATH")


def setup_aliases() -> None:
    # Generic, non-invasive convenience aliases. Deliberately ciu-free.
    _bashrc_block(
        "aliases",
        """
if command -v batcat >/dev/null 2>&1; then alias bat='batcat --paging=never'; fi
alias ll='ls -l'
alias la='ls -la'
alias gs='git status -sb'
alias gl='git log --oneline --decorate -n 20'
alias gd='git diff'
alias dps='docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"'
""",
    )
    ok("shell aliases configured")


def setup_vscode_settings(env_type: str) -> None:
    if env_type != "devcontainer":
        return
    vsc = Path.cwd() / ".vscode"
    settings = vsc / "settings.json"
    try:
        vsc.mkdir(parents=True, exist_ok=True)
        if not settings.exists():
            settings.write_text(
                json.dumps(
                    {
                        "python.defaultInterpreterPath": "python3",
                        "python.terminal.activateEnvironment": False,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            ok("created .vscode/settings.json")
    except OSError as e:
        warn(f"vscode settings: {e}")


def verify_base_tools() -> None:
    # Sanity-check a few tools the mdt base image is expected to provide.
    expected = ["bat", "fd", "rg", "fzf", "yq", "jq", "git", "docker", "python3"]
    missing = [t for t in expected if shutil.which(t) is None]
    if missing:
        warn(f"base-image tools not found on PATH: {', '.join(missing)}")
    else:
        ok(f"base-image tools verified ({len(expected)})")


def run_generic_steps(envd: dict) -> None:
    info("generic mdt steps…")
    setup_path()
    setup_aliases()
    setup_vscode_settings(envd["env_type"])
    verify_base_tools()


# ── consumer hook discovery + execution ─────────────────────────────────────
def _resolve_devcontainer_dir(override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env_override = os.environ.get("MDT_DEVCONTAINER_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return (Path.cwd() / ".devcontainer").resolve()


def _hook_scripts(dc_dir: Path, phase: str) -> list:
    """Ordered hook list for a phase: the `.d` dir entries then the single file."""
    scripts: list = []
    d = dc_dir / f"finalize.{phase}.d"
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.is_file() and (p.suffix == ".sh" or os.access(p, os.X_OK)):
                scripts.append(p)
    single = dc_dir / f"finalize.{phase}.sh"
    if single.is_file():
        scripts.append(single)
    return scripts


def _run_hook(p: Path, hook_env: dict) -> int:
    cmd = [str(p)] if os.access(p, os.X_OK) else ["bash", str(p)]
    info(f"hook → {p.name}")
    try:
        return subprocess.run(cmd, env={**os.environ, **hook_env}, cwd=Path.cwd()).returncode
    except OSError as e:
        err(f"could not run hook {p}: {e}")
        return 127


def run_hooks(phase: str, dc_dir: Path, hook_env: dict, strict: bool) -> bool:
    """Run a phase's hooks. Returns True if all succeeded (or none present)."""
    scripts = _hook_scripts(dc_dir, phase)
    if not scripts:
        return True
    all_ok = True
    for p in scripts:
        rc = _run_hook(p, hook_env)
        if rc != 0:
            err(f"consumer hook {p.name} exited {rc}")
            all_ok = False
            if strict:
                break
    return all_ok


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="mdt in-container finalize (post script).")
    ap.add_argument("--no-hooks", action="store_true", help="generic steps only")
    ap.add_argument("--hooks-only", action="store_true", help="consumer hooks only")
    ap.add_argument("--devcontainer-dir", default=None, help="override hook-discovery dir")
    args = ap.parse_args()

    envd = detect_environment()
    dc_dir = _resolve_devcontainer_dir(args.devcontainer_dir)
    strict = os.environ.get("MDT_FINALIZE_STRICT") == "1"

    info(f"env={envd['env_type']} user={envd['user']}({envd['uid']}:{envd['gid']}) "
         f"docker_gid={envd['docker_gid'] or '?'} workspace={envd['workspace']}")
    info(f"hook dir: {dc_dir}{'' if dc_dir.is_dir() else ' (absent — no consumer hooks)'}")

    hook_env = {
        "MDT_FINALIZE": "1",
        "MDT_ENV_TYPE": envd["env_type"],
        "MDT_WORKSPACE_DIR": envd["workspace"],
        "MDT_DEVCONTAINER_DIR": str(dc_dir),
        "MDT_USER": envd["user"],
        "MDT_UID": envd["uid"],
        "MDT_GID": envd["gid"],
        "MDT_DOCKER_GID": envd["docker_gid"],
    }

    consumer_ok = True
    if not args.no_hooks:
        consumer_ok &= run_hooks("pre", dc_dir, hook_env, strict)
    if not args.hooks_only and (consumer_ok or not strict):
        run_generic_steps(envd)
    if not args.no_hooks and (consumer_ok or not strict):
        consumer_ok &= run_hooks("post", dc_dir, hook_env, strict)

    if consumer_ok:
        ok("finalize complete")
        return 0
    err("finalize completed with consumer-hook failures (see above)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
