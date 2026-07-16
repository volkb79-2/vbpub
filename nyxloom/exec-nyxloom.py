#!/usr/bin/env python3
"""Admin entrypoint for nyxloom — one command, host now / container later.

Usage:
    ./exec-nyxloom.py                 # defaults to `status`
    ./exec-nyxloom.py status --project topos
    ./exec-nyxloom.py doctor
    ./exec-nyxloom.py init <project_folder>   # scaffold a nyxloom-trove/
    ./exec-nyxloom.py <any nyxloom.cli subcommand> [args...]

`init` is not special-cased below -- it rides the same generic argv
forwarding as every other subcommand (docker exec into the controller when
running, host fallback otherwise), which also proves the target instance can
reach <project_folder> (a built-in access check; see nyxloom-trove/STANDARD.md).

Routing (transition-safe across the P19 containerization):
  1. If a running controller container is found (name contains both
     "nyxloom" and "controller", or $NYXLOOM_CONTAINER), the args are
     forwarded via `docker exec <container> nyxloom ...` — the containerized
     daemon owns the authoritative state volume.
  2. Otherwise it runs host-side against THIS checkout's src/ and the live
     XDG state dir (works today, before P19 lands).

Exit code is the wrapped command's exit code (os.exec* replaces this process).
Read-only for `status`/`doctor`; it changes nothing itself.
"""
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "src")


def _find_controller_container() -> str | None:
    override = os.environ.get("NYXLOOM_CONTAINER")
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
    except (subprocess.SubprocessError, OSError):
        return None
    if override and override in out:
        return override
    for name in out:
        if "nyxloom" in name and "controller" in name:
            return name
    return None


def _host_python() -> str:
    # Prefer the known project venv (carries nyxloom's deps); fall back to
    # whatever interpreter is running this script.
    for cand in (
        os.path.join(REPO, ".venv", "bin", "python"),
        "/workspaces/vbpub/.venv/bin/python",
    ):
        if os.path.exists(cand):
            return cand
    return sys.executable


def main(argv: list[str]) -> None:
    args = argv[1:] or ["status"]

    container = _find_controller_container()
    if container is not None:
        # P19+: the daemon runs in a ciu-managed container; exec into it.
        os.execvp("docker", ["docker", "exec", container, "nyxloom", *args])

    # Host fallback (pre-P19): run the CLI directly against src/ + live state.
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    py = _host_python()
    os.execvpe(py, [py, "-m", "nyxloom.cli", *args], env)


if __name__ == "__main__":
    main(sys.argv)
