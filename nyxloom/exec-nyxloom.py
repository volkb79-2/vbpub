#!/usr/bin/env python3
"""Admin entrypoint for nyxloom — one command, host now / container later.

Usage:
    ./exec-nyxloom.py                 # defaults to `status`
    ./exec-nyxloom.py status --project topos
    ./exec-nyxloom.py doctor
    ./exec-nyxloom.py <any nyxloom.cli subcommand> [args...]

Routing (transition-safe across the P19 containerization):
  1. If a running daemon container is found (name ending "-nyxloomd" — the
     ciu.toml `container_prefix` produces `<prefix>-nyxloomd` — or
     $NYXLOOM_CONTAINER), the args are forwarded via `docker exec <container>
     /opt/nyxloom-venv/bin/python -m nyxloom.cli ...` — the containerized
     daemon owns the authoritative state volume. (The image has no `nyxloom`
     entrypoint on PATH; the venv interpreter is what the daemon's own CMD
     uses.)
  2. Otherwise it runs host-side against THIS checkout's src/ and the live
     XDG state dir (works today, before P19 lands).

Exit code is the wrapped command's exit code (os.exec* replaces this process).
Read-only for `status`/`doctor`; it changes nothing itself.
"""
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "src")

# P27: the resolver lives in nyxloom.adapters (importable + unit-tested) --
# this hyphenated script cannot itself be imported, so put SRC on sys.path
# ahead of importing it. This runs before the host-fallback PYTHONPATH env
# below is even relevant (that env only affects the exec'd subprocess).
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from nyxloom.adapters import find_controller_container  # noqa: E402

CONTROLLER_PYTHON = "/opt/nyxloom-venv/bin/python"


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

    container = find_controller_container()
    if container is not None:
        # P19+: the daemon runs in a ciu-managed container; exec into it via
        # the venv interpreter (no `nyxloom` entrypoint on the image's PATH).
        os.execvp("docker", ["docker", "exec", container, CONTROLLER_PYTHON,
                             "-m", "nyxloom.cli", *args])

    # Host fallback (pre-P19): run the CLI directly against src/ + live state.
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    py = _host_python()
    os.execvpe(py, [py, "-m", "nyxloom.cli", *args], env)


if __name__ == "__main__":
    main(sys.argv)
