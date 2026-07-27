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
SHARED_PROFILE = Path("/usr/local/share/modern-debian-tools-python-debug/profile.sh")
SHARED_CUSTOMIZATION_ROOT = SHARED_PROFILE.parent
CUSTOMIZATION_ROOT = Path(os.path.expanduser("~/.config/modern-debian-tools-python-debug"))
CUSTOMIZATION_README = CUSTOMIZATION_ROOT / "README.md"
CUSTOMIZATION_ENV = CUSTOMIZATION_ROOT / "ai.env"
CUSTOMIZATION_ENV_EXAMPLE = CUSTOMIZATION_ROOT / "ai.env.example"
CUSTOMIZATION_ALIASES = CUSTOMIZATION_ROOT / "aliases.sh"
CUSTOMIZATION_ALIASES_EXAMPLE = CUSTOMIZATION_ROOT / "aliases.sh.example"
CUSTOMIZATION_SHELL_ENV = CUSTOMIZATION_ROOT / "shell.env"
CUSTOMIZATION_HTOPRC = CUSTOMIZATION_ROOT / "htoprc"
CUSTOMIZATION_MC_INI = CUSTOMIZATION_ROOT / "mc.ini"
CUSTOMIZATION_NANORC = CUSTOMIZATION_ROOT / "nanorc"
CUSTOMIZATION_LESSPIPE = CUSTOMIZATION_ROOT / "lesspipe.sh"
CUSTOMIZATION_ZSHRC = CUSTOMIZATION_ROOT / "zshrc"

# Tool-local env files that should resolve back to the central ai.env.
TOOL_ENV_LINKS = {
    Path(os.path.expanduser("~/.codex/.env")),
    Path(os.path.expanduser("~/.openclaw/.env")),
    Path(os.path.expanduser("~/.reasonix/.env")),
}


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


def _ensure_text_file(destination: Path, content: str) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return
        destination.write_text(content, encoding="utf-8")
    except OSError as e:
        warn(f"could not seed {destination}: {e}")


def _ensure_copy(source: Path, destination: Path) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return
        shutil.copy2(source, destination)
    except OSError as e:
        warn(f"could not copy {source} -> {destination}: {e}")


def _ensure_symlink(source: Path, target: Path) -> None:
    try:
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink() or source.exists():
            try:
                if source.resolve() == target.resolve():
                    return
            except OSError:
                pass
            if source.is_dir() and not source.is_symlink():
                return
            warn(f"leaving existing path in place instead of linking {source} -> {target}")
            return
        source.symlink_to(target)
    except OSError as e:
        warn(f"could not link {source} -> {target}: {e}")


def setup_shell_bootstrap() -> None:
    _bashrc_block(
        "mdt-profile",
        f"""
if [ -r {SHARED_PROFILE} ]; then
    # shellcheck source=/dev/null
    source {SHARED_PROFILE}
fi
""",
    )
    ok("shell bootstrap configured")


def setup_customization_root() -> None:
    for name, destination in {
        "README.md": CUSTOMIZATION_README,
        "ai.env.example": CUSTOMIZATION_ENV_EXAMPLE,
        "aliases.sh": CUSTOMIZATION_ALIASES,
        "aliases.sh.example": CUSTOMIZATION_ALIASES_EXAMPLE,
        "shell.env": CUSTOMIZATION_SHELL_ENV,
        "htoprc": CUSTOMIZATION_HTOPRC,
        "mc.ini": CUSTOMIZATION_MC_INI,
        "nanorc": CUSTOMIZATION_NANORC,
        "lesspipe.sh": CUSTOMIZATION_LESSPIPE,
        "zshrc": CUSTOMIZATION_ZSHRC,
    }.items():
        _ensure_copy(SHARED_CUSTOMIZATION_ROOT / name, destination)


def setup_tool_env_links() -> None:
    for link in TOOL_ENV_LINKS:
        _ensure_symlink(link, CUSTOMIZATION_ENV)


def setup_editor_links() -> None:
    _ensure_symlink(Path(os.path.expanduser("~/.nanorc")), CUSTOMIZATION_NANORC)
    _ensure_symlink(
        Path(os.path.expanduser("~/.config/nano/nanorc")),
        CUSTOMIZATION_NANORC,
    )
    _ensure_symlink(
        Path(os.path.expanduser("~/.config/htop/htoprc")),
        CUSTOMIZATION_HTOPRC,
    )
    _ensure_symlink(Path(os.path.expanduser("~/.config/mc/ini")), CUSTOMIZATION_MC_INI)


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


# ── docker socket relay repair ──────────────────────────────────────────────
# WHY THIS EXISTS (diagnosed 2026-07-27)
#
# The `docker-outside-of-docker` devcontainer feature bind-mounts the host socket
# to /var/run/docker-host.sock and then — whenever the host socket's GID already
# exists as a group inside the container (the common case, so: nearly always) —
# fronts it with a socat relay:
#
#   socat UNIX-LISTEN:/var/run/docker.sock,fork,... UNIX-CONNECT:/var/run/docker-host.sock
#
# started with NO `-t`. socat's default half-close timeout is 0.5 SECONDS.
#
# A non-interactive `docker run` hijacks the HTTP connection and shuts down its
# write side immediately. socat sees EOF client→server and, half a second later,
# tears down server→client as well. The container still runs to completion on the
# daemon — which neither knows nor cares that the client left — but the CLI
# receives only the first ~0.5s of output, and may report EXIT 0 for a container
# that exited non-zero.
#
# The consequence is not "flaky output". It is that a FAILING containerised
# command can become indistinguishable from a passing one. Any CI/gate/test
# harness that runs `docker run` from inside an mdt devcontainer and reads its
# exit code is liable to read a forged PASS. Measured: two red test-suite runs
# both came back exit 0 with 25 bytes of output.
#
# Note the asymmetry, because it decides how you test for this: the TRUNCATION
# is deterministic, the EXIT-CODE corruption is not. A hand sentinel against the
# same broken relay had its output truncated while still returning the right
# code. An always-wrong exit code would be caught by the first spot-check; an
# intermittently-wrong one survives every spot-check and lies exactly when a
# gate turns red. So judge on OUTPUT, never on the exit code alone.
#
# `docker version` and `docker logs` stay healthy throughout (plain
# request/response, never hijacked), so neither is a valid check. A valid check
# spans a real delay and looks at what came back:
#
#   docker run --rm <img> sh -c 'echo A; sleep 5; echo B; exit 7'
#   # healthy → prints A AND B     poisoned → prints only A
#
# postCreate is the right (and sufficient) place to repair it:
#   * first start  — the feature entrypoint runs before any lifecycle hook, so
#     the live relay is always the unpatched one → we restart it here;
#   * later `docker restart`s — the entrypoint re-reads the script we patched
#     here, so it starts a correct socat and postCreate need not run again;
#   * rebuild — the feature reinstalls its unpatched script, and postCreate runs
#     again, repatching it.
_DOCKER_INIT_SCRIPT = Path("/usr/local/share/docker-init.sh")
_RELAY_HALF_CLOSE_TIMEOUT = 86400  # seconds; must exceed the longest gate run
_RELAY_LISTEN = "UNIX-LISTEN:/var/run/docker.sock"


def _sudo(*argv: str) -> int:
    """Best-effort privileged run; returns the exit code (127 if unavailable)."""
    try:
        if os.geteuid() == 0:
            return subprocess.run(argv, check=False).returncode
        return subprocess.run(("sudo", "-n", *argv), check=False).returncode
    except OSError:
        return 127


def _relay_pattern(with_timeout: bool) -> str:
    """pgrep/pkill -f regex for the relay process.

    The leading `[s]` is not decoration. `pkill -f` matches against the FULL
    command line of every process — including the `sudo pkill -f <pattern>`
    process running the kill, whose own argv contains the pattern verbatim. A
    plain `socat …` pattern therefore makes pkill kill its own parent and die
    with it, leaving the relay untouched and the caller none the wiser. Writing
    the regex as `[s]ocat …` still matches the literal `socat …` in the target's
    argv, but the killer's argv contains the characters `[s]ocat`, which that
    regex does not match. (Learned the hard way: the first cut of this function
    killed the shell that invoked it.)

    The two variants are mutually exclusive: `socat UNIX-LISTEN` requires the
    listen address immediately after the binary, so it cannot match
    `socat -t 86400 UNIX-LISTEN…`.
    """
    return (f"[s]ocat -t {_RELAY_HALF_CLOSE_TIMEOUT} {_RELAY_LISTEN}"
            if with_timeout else f"[s]ocat {_RELAY_LISTEN}")


def _relay_running(with_timeout: bool) -> bool:
    try:
        return subprocess.run(("pgrep", "-f", _relay_pattern(with_timeout)),
                              check=False, capture_output=True).returncode == 0
    except OSError:
        return False


def repair_docker_socket_relay() -> None:
    """Give the docker socket relay a sane half-close timeout. See the block
    comment above — without it every `docker run` from this container reports a
    forged exit 0. Best-effort and idempotent; never fails the finalize run,
    because a broken transport only misleads when a verdict is being READ, and
    that fail-closed assertion belongs in the harness that reads it."""
    if shutil.which("socat") is None or not Path("/var/run/docker-host.sock").is_socket():
        return  # relay not in use on this setup — nothing to repair

    # 1. Patch the feature's init script so future container starts are correct.
    try:
        text = _DOCKER_INIT_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text:
        if f"socat -t {_RELAY_HALF_CLOSE_TIMEOUT} {_RELAY_LISTEN}" in text:
            pass  # already patched
        elif f"socat {_RELAY_LISTEN}" in text:
            _sudo("cp", "-n", str(_DOCKER_INIT_SCRIPT),
                  f"{_DOCKER_INIT_SCRIPT}.bak-pre-t-fix")
            rc = _sudo("sed", "-i",
                       f"s|socat {_RELAY_LISTEN}|socat -t {_RELAY_HALF_CLOSE_TIMEOUT} {_RELAY_LISTEN}|",
                       str(_DOCKER_INIT_SCRIPT))
            if rc == 0:
                info(f"patched docker-init.sh: socat -t {_RELAY_HALF_CLOSE_TIMEOUT}")
            else:
                warn("could not patch docker-init.sh (no passwordless sudo?) — "
                     "`docker run` exit codes from this container are UNRELIABLE")
        else:
            warn("docker-init.sh has an unrecognised socat line; not patching")

    # 2. Restart the live relay, which the entrypoint already started without -t.
    if _relay_running(with_timeout=True):
        ok("docker socket relay already has a sane half-close timeout")
        return
    if not _relay_running(with_timeout=False):
        return  # no relay to restart

    user = os.environ.get("USER") or "vscode"
    _sudo("pkill", "-f", _relay_pattern(with_timeout=False))
    _sudo("rm", "-f", "/var/run/docker.sock")
    _sudo("sh", "-c",
          f"nohup setsid socat -t {_RELAY_HALF_CLOSE_TIMEOUT} "
          f"{_RELAY_LISTEN},fork,mode=660,user={user},backlog=128 "
          f"UNIX-CONNECT:/var/run/docker-host.sock "
          f">>/tmp/vscr-docker-from-docker.log 2>&1 &")

    for _ in range(10):
        if Path("/var/run/docker.sock").is_socket() and _relay_running(with_timeout=True):
            ok(f"docker socket relay restarted with -t {_RELAY_HALF_CLOSE_TIMEOUT} "
               "(was 0.5s — truncating output and forging exit 0)")
            return
        try:
            subprocess.run(("sleep", "0.5"), check=False)
        except OSError:
            break
    warn("docker socket relay restart FAILED — /var/run/docker.sock may be "
         "unusable; fall back to DOCKER_HOST=unix:///var/run/docker-host.sock")


def run_generic_steps(envd: dict) -> None:
    info("generic mdt steps…")
    setup_shell_bootstrap()
    setup_customization_root()
    setup_tool_env_links()
    setup_editor_links()
    setup_vscode_settings(envd["env_type"])
    repair_docker_socket_relay()
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
