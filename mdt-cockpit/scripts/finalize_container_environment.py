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
- **stdlib-only, idempotent, best-effort.** (Agent skills of the first-party CLIs are
  installed by `install_agent_skills`; see docs/DESIGN-GUIDE.md.) The generic mdt steps below never
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
from mdt_cli import MdtArgumentParser
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/local/lib")
try:
    from mdt_buildkit_builder import (
        BuilderError,
        MANAGED_BUILDER,
        MANAGED_ENDPOINT,
        ensure_managed_builder,
    )
except ImportError:
    # Source-tree tests import this module before the Dockerfile has installed
    # the shared helper.
    from scripts.mdt_buildkit_builder import (  # type: ignore
        BuilderError,
        MANAGED_BUILDER,
        MANAGED_ENDPOINT,
        ensure_managed_builder,
    )

# ── tiny logging (no color when not a tty) ──────────────────────────────────
_TTY = sys.stdout.isatty()
# The user's startup files whose absence makes zsh run zsh-newuser-install.
ZSH_STARTUP_FILES = (".zshenv", ".zprofile", ".zshrc", ".zlogin")
ZSHRC_STUB = (
    "# Created by mdt-cockpit's finalize step so that zsh-newuser-install does not\n"
    "# take over your first terminal. It is a deliberate no-op: the image's zsh\n"
    "# defaults are loaded system-wide from /etc/zsh/zshrc before this file runs.\n"
    "# Edit or replace it freely; finalize never touches an existing ~/.zshrc.\n"
)


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
def _current_user_name() -> str:
    """The running user's name, derived from the uid (the passwd database).

    `os.getlogin()` needs a controlling terminal and `$USER` is not always set (a
    container started without a tty or `-e USER`), so neither is a source of truth; the
    uid is. A uid with no passwd entry is reported by number, never guessed.
    """
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return f"uid-{os.getuid()}"


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
        "user": _current_user_name(),
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
        "docker_gid": docker_gid,
        "workspace": str(Path.cwd()),
        "home": os.path.expanduser("~"),
    }


# ── generic mdt steps (best-effort) ─────────────────────────────────────────
def setup_zshrc_stub() -> None:
    """Keep zsh-newuser-install from taking over a fresh home's first terminal.

    D7: every image default is wired system-wide, so the home stays empty. zsh is
    the one tool that cannot be told to skip its new-user wizard system-wide: it
    runs whenever the user has none of .zshenv/.zprofile/.zshrc/.zlogin. So a
    comment-only ~/.zshrc is written - only then, only if absent, never replacing
    or extending a user's file. (The image defaults reach the user through
    /etc/zsh/zshrc, so image updates need no change to this file.)
    """
    home = Path(os.path.expanduser("~"))
    if any(os.path.lexists(home / name) for name in ZSH_STARTUP_FILES):
        return
    try:
        descriptor = os.open(home / ".zshrc", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(ZSHRC_STUB)
    except FileExistsError:
        return
    except OSError as e:
        warn(f"could not write the ~/.zshrc stub: {e}")
        return
    ok("wrote the comment-only ~/.zshrc stub (zsh-newuser-install stays quiet)")


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


def setup_buildkit_builder(*, required: bool = False) -> bool:
    """Validate and reuse the one host-managed remote builder.

    A devcontainer must carry both variables explicitly. Any partial or
    different configuration is an error; generic finalization never falls
    back to Docker's embedded builder or creates a per-container worker.
    """
    builder = os.environ.get("BUILDX_BUILDER", "").strip()
    endpoint = os.environ.get("BUILDKIT_HOST", "").strip()
    if not builder and not endpoint:
        if required:
            err("devcontainer must set BUILDX_BUILDER=mdt-managed and BUILDKIT_HOST=" + MANAGED_ENDPOINT)
            return False
        return True
    if builder != MANAGED_BUILDER or endpoint != MANAGED_ENDPOINT:
        err(
            "inconsistent managed BuildKit environment: expected "
            f"BUILDX_BUILDER={MANAGED_BUILDER} and BUILDKIT_HOST={MANAGED_ENDPOINT}, "
            f"got BUILDX_BUILDER={builder or '<empty>'} BUILDKIT_HOST={endpoint or '<empty>'}"
        )
        return False
    docker = shutil.which("docker")
    if docker is None:
        err("BUILDX_BUILDER is configured but docker is not on PATH; install Docker/Buildx or fix the devcontainer")
        return False
    try:
        info(f"verifying remote builder {MANAGED_BUILDER} at {MANAGED_ENDPOINT}")
        ensure_managed_builder(docker, MANAGED_ENDPOINT, create_if_missing=True)
    except (BuilderError, OSError) as exc:
        err(str(exc))
        return False
    ok(f"using host-managed BuildKit builder {MANAGED_BUILDER}")
    return True


def verify_base_tools() -> None:
    # Sanity-check a few tools the mdt base image is expected to provide.
    expected = ["bat", "fd", "rg", "fzf", "yq", "jq", "git", "docker", "python3"]
    missing = [t for t in expected if shutil.which(t) is None]
    if missing:
        warn(f"base-image tools not found on PATH: {', '.join(missing)}")
    else:
        ok(f"base-image tools verified ({len(expected)})")


# First-party CLIs that package agent skills behind a `<cli> skills` verb
# (check / install, cli-extended's skills plugin). One constant: add a CLI here and
# nowhere else. A CLI that is absent from PATH, or whose installed version has no
# `skills` verb yet, is skipped quietly.
SKILLS_CLIS = ("cmru", "ciu", "pwmcp", "topos")
SKILLS_TIMEOUT_SECONDS = 20  # per call
SKILLS_TOTAL_BUDGET_SECONDS = 60  # whole step; a hung CLI never holds the container start longer
# Warn-once markers (one file per CLI and kind, in the container's writable layer): a
# user-modified skill is refused by `install` on every start, by design, and says so once.
SKILLS_MARKER_DIR = Path("/var/devcontainer")


def _run_skills(argv: list[str], deadline: float) -> subprocess.CompletedProcess | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        warn(f"{' '.join(argv)}: skipped, the agent-skills time budget ({SKILLS_TOTAL_BUDGET_SECONDS}s) is spent")
        return None
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=min(SKILLS_TIMEOUT_SECONDS, remaining), check=False
        )
    except (OSError, subprocess.SubprocessError) as e:
        warn(f"{' '.join(argv)}: {e}")
        return None


def _warn_once(cli: str, kind: str, message: str) -> None:
    """Warn the first time per container for (cli, kind); afterwards log at INFO."""
    marker = SKILLS_MARKER_DIR / f"agent-skills-{cli}-{kind}.warned"
    if marker.exists():
        info(message)
        return
    warn(message)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass  # no marker, no harm: the warning may repeat


def _crashed(proc: subprocess.CompletedProcess) -> bool:
    return "Traceback (most recent call last)" in (proc.stderr or "")


def install_agent_skills() -> None:
    """Install the agent skills packaged with the first-party CLIs into the user home.

    A fresh home has none of them, so `cmru doctor` would report "skills not current".
    For each CLI of SKILLS_CLIS on PATH: `<cli> skills check` (exit 0 = current, exit 1
    with no traceback = not current, exit 2 = the CLI has no `skills` verb; a traceback or
    any other code = a broken CLI, skipped with a warning) and, only when not current,
    `<cli> skills install`. A failing install (a modified skill is refused) warns once per
    container per CLI, then logs at INFO. The whole step is bounded by
    SKILLS_TOTAL_BUDGET_SECONDS. `install` writes only the skill directories the tool owns
    (`~/.claude/skills/<skill>`, `~/.agents/skills/<skill>`, each stamped), so it never
    replaces or extends a user file, and a second run on a current home changes nothing.
    Every failure is a warning: the container start never depends on this step.
    """
    deadline = time.monotonic() + SKILLS_TOTAL_BUDGET_SECONDS
    for cli in SKILLS_CLIS:
        path = shutil.which(cli)
        if path is None:
            continue
        checked = _run_skills([path, "skills", "check"], deadline)
        if checked is None:
            continue
        if checked.returncode == 0:
            info(f"{cli}: agent skills current")
            continue
        if checked.returncode == 2:
            info(f"{cli}: no `skills` verb in this version; skipped")
            continue
        if checked.returncode != 1 or _crashed(checked):
            _warn_once(cli, "check", f"{cli} skills check is broken (exit {checked.returncode}); skills step skipped")
            continue
        installed = _run_skills([path, "skills", "install"], deadline)
        if installed is None:
            continue
        if installed.returncode == 0:
            ok(f"{cli}: installed agent skills")
        else:
            detail = (installed.stderr or installed.stdout or "").strip().splitlines()
            _warn_once(
                cli,
                "install",
                f"{cli} skills install failed (exit {installed.returncode}): {detail[-1] if detail else 'no output'}"
                f" (a locally modified skill is refused; `{cli} skills install --overwrite-modified` is the operator's choice)",
            )


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


# ── PWMCP network join ──────────────────────────────────────────────────────
# `ciu up` in /srv/pwmcp-instance attaches the container that ran it to the stack
# network, and the stack (restart: unless-stopped) outlives this container. A
# devcontainer that is RECREATED or REBUILT starts on the default bridge only, so the
# alias `pwmcp` (PWMCP_MCP_URL=http://pwmcp:8931/mcp) does not resolve until it is
# attached again. postCreateCommand runs on every create/rebuild, which is exactly
# when a network membership is lost (a plain restart keeps it). Not ciu-aware: it
# looks only at Docker labels/aliases of the stack's own container.
PWMCP_ALIAS = "pwmcp"
PWMCP_DOCKER_TIMEOUT_SECONDS = 20


def _docker(*argv: str) -> subprocess.CompletedProcess | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    try:
        return subprocess.run(
            [docker, *argv], capture_output=True, text=True,
            timeout=PWMCP_DOCKER_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        warn(f"docker {' '.join(argv[:2])}: {e}")
        return None


def _container_networks(name: str) -> dict | None:
    """Networks of exactly the container ``name``: {network: [aliases]}, None if unknown."""
    res = _docker("inspect", "--type", "container", "--format", "{{json .NetworkSettings.Networks}}", name)
    if res is None or res.returncode != 0:
        return None
    try:
        nets = json.loads(res.stdout.strip() or "null")
    except ValueError:
        return None
    if not isinstance(nets, dict):
        return None
    return {k: list((v or {}).get("Aliases") or []) for k, v in nets.items()}


PWMCP_OVERRIDE_ENV = "PWMCP_STACK_CONTAINER"
_INSTANCE_ID_RE = re.compile(r'^\s*(?:export\s+)?INSTANCE_ID=["\']?([A-Za-z0-9_-]+)["\']?\s*$', re.M)


PWMCP_INSTANCE_DIR_ENV = "PWMCP_INSTANCE_DIR"
PWMCP_INSTANCE_DIR_DEFAULT = "/srv/pwmcp-instance"


def pwmcp_instance_dir() -> Path:
    """Directory of the shipped PWMCP instance (compose working dir of its stack)."""
    return Path(os.environ.get(PWMCP_INSTANCE_DIR_ENV, "").strip() or PWMCP_INSTANCE_DIR_DEFAULT)


def pwmcp_stack_container_name(workspace: Path | None = None) -> tuple[str | None, str]:
    """The exact name of THIS cockpit's PWMCP stack container, and where it came from.

    Every PWMCP stack is named `pwmcp-<INSTANCE_ID>-pwmcp`, and instance, worktree, gate and
    other users' stacks all match a generic filter, so the name is derived from this
    workspace's identity: PWMCP_STACK_CONTAINER (explicit override), else the INSTANCE_ID of the
    shipped instance itself (`<instance dir>/ciu.env` or `.ciu/ciu.env`; the workspace's own
    INSTANCE_ID is a different ciu instance and would name a stack that does not exist), else
    INSTANCE_ID from the environment, else the INSTANCE_ID line of <workspace>/ciu.env.
    (None, "") when unknown.
    """
    override = os.environ.get(PWMCP_OVERRIDE_ENV, "").strip()
    if override:
        return override, PWMCP_OVERRIDE_ENV
    inst = pwmcp_instance_dir()
    for rel in ("ciu.env", ".ciu/ciu.env"):
        try:
            m = _INSTANCE_ID_RE.search((inst / rel).read_text(encoding="utf-8"))
        except OSError:
            m = None
        if m:
            return f"pwmcp-{m.group(1)}-pwmcp", str(inst / rel)
    iid = os.environ.get("INSTANCE_ID", "").strip()
    if iid:
        return f"pwmcp-{iid}-pwmcp", "INSTANCE_ID"
    env_file = (workspace or Path.cwd()) / "ciu.env"
    try:
        m = _INSTANCE_ID_RE.search(env_file.read_text(encoding="utf-8"))
    except OSError:
        m = None
    if m:
        return f"pwmcp-{m.group(1)}-pwmcp", str(env_file)
    return None, ""


def join_pwmcp_network(workspace: Path | None = None) -> None:
    """Attach this container to the network of ITS OWN running PWMCP stack. Never fails finalize.

    The stack container is selected by exact name derived from this workspace's identity (see
    pwmcp_stack_container_name), never "the first one": other stacks on the host (worktree,
    gate, another user's) are ignored, and with no identity or no exact match nothing is
    joined. The candidate is found with a filtered `docker ps` and inspected (`--type
    container`); only a network on which it carries the alias `pwmcp` counts. Already
    attached: nothing to do.
    """
    if shutil.which("docker") is None:
        return
    me = os.environ.get("HOSTNAME") or os.uname().nodename
    expected, source = pwmcp_stack_container_name(workspace)
    choose = (f"set {PWMCP_OVERRIDE_ENV}=<container name> (e.g. in remoteEnv) to choose the stack, "
              "or INSTANCE_ID / ciu.env in the workspace")
    listed = _docker(
        "ps", "--filter", "name=^pwmcp-", "--filter", "label=com.docker.compose.service=pwmcp",
        "--format", "{{.Names}}",
    )
    if listed is None or listed.returncode != 0:
        detail = ((listed.stderr or listed.stdout) if listed else "docker unavailable").strip().splitlines()
        warn(f"pwmcp: docker ps failed ({detail[-1] if detail else 'no output'}); skipped")
        return
    names = sorted(listed.stdout.split())
    if source not in (PWMCP_OVERRIDE_ENV,) and not source.startswith(str(pwmcp_instance_dir())):
        # The instance's generated ciu.env is not kept on disk after `ciu up`; its running stack
        # still carries the compose working-dir label, which identifies it exactly.
        inst = pwmcp_instance_dir()
        by_dir = _docker(
            "ps", "--filter", "name=^pwmcp-", "--filter", "label=com.docker.compose.service=pwmcp",
            "--filter", f"label=com.docker.compose.project.working_dir={inst}",
            "--format", "{{.Names}}",
        )
        found = sorted(by_dir.stdout.split()) if by_dir is not None and by_dir.returncode == 0 else []
        if len(found) == 1 and found[0] in names:
            expected, source = found[0], f"compose working dir {inst}"
    if expected is None:
        info(f"pwmcp: this workspace's PWMCP identity is unknown (found: {', '.join(names) or 'none'}); "
             f"not joining any stack; {choose}")
        return
    matches = [n for n in names if n == expected]
    if len(matches) != 1:
        if names:
            info(f"pwmcp: no running stack named {expected} (from {source}); other stacks "
                 f"({', '.join(names)}) are not joined; {choose}")
        else:
            info(f"pwmcp: no running PWMCP stack found; to start it: cd /srv/pwmcp-instance && ciu up "
                 f"(the {PWMCP_ALIAS} alias resolves once this container is on its network)")
        return
    mine = _container_networks(me)
    if mine is None:
        warn(f"pwmcp network: cannot inspect this container ({me}); skipped")
        return
    candidates = [net for net, aliases in (_container_networks(matches[0]) or {}).items()
                  if PWMCP_ALIAS in aliases]
    if not candidates:
        info(f"pwmcp: {matches[0]} has no network carrying the alias {PWMCP_ALIAS}; skipped")
        return
    shared = [n for n in candidates if n in mine]
    if shared:
        info(f"pwmcp: already on network {shared[0]}")
        return
    if len(candidates) > 1:
        warn(f"pwmcp: {matches[0]} carries the alias on several networks ({', '.join(candidates)}); "
             f"joining {candidates[0]}")
    res = _docker("network", "connect", candidates[0], me)
    if res is not None and res.returncode == 0:
        ok(f"pwmcp: joined network {candidates[0]} ({matches[0]})")
    else:
        detail = ((res.stderr or res.stdout) if res else "docker unavailable").strip().splitlines()
        warn(f"pwmcp: network connect {candidates[0]} failed: {detail[-1] if detail else 'no output'}")


def run_generic_steps(envd: dict) -> bool:
    info("generic mdt steps…")
    setup_zshrc_stub()
    setup_vscode_settings(envd["env_type"])
    repair_docker_socket_relay()
    join_pwmcp_network()
    buildkit_ok = setup_buildkit_builder(required=envd["env_type"] == "devcontainer")
    verify_base_tools()
    install_agent_skills()
    return buildkit_ok


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
    ap = MdtArgumentParser(description="mdt in-container finalize (post script).")
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
    generic_ok = True
    if not args.no_hooks:
        consumer_ok &= run_hooks("pre", dc_dir, hook_env, strict)
    if not args.hooks_only and (consumer_ok or not strict):
        generic_ok = run_generic_steps(envd)
    if not args.no_hooks and (consumer_ok or not strict):
        consumer_ok &= run_hooks("post", dc_dir, hook_env, strict)

    if consumer_ok and generic_ok:
        ok("finalize complete")
        return 0
    err("finalize did not complete successfully (consumer hooks or managed BuildKit setup failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
