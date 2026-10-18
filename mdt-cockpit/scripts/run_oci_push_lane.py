#!/usr/bin/env python3
"""Driver of the `oci-push` lane (MDTC-02 T6), stdlib only.

The OCI-layout push test needs docker, buildx, crane and regctl, which tester-unified does
not ship and the mdt-cockpit image does. This driver runs on the bare host, starts the EXACT
image named by MDT_OCI_PUSH_IMAGE in its own container and runs the test inside it:

* placed in the gate tier (`--cgroup-parent $CGROUP_PARENT_DEV_GATES`), `--init`, uniquely
  named, removed BY EXACT NAME;
* the host docker socket is mounted (the test starts a registry:2 container and a buildx
  builder); the container needs the default bridge network for that;
* the sources travel as a tar stream of the tracked `mdt-cockpit/` files on stdin, so no
  devcontainer-local path is ever bind-mounted;
* MDT_OCI_PUSH_REQUIRED=1: a missing tool or unreachable registry FAILS the test.

There is no default image: an unset variable is a configuration error.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_IMAGE = "MDT_OCI_PUSH_IMAGE"
ENV_GATES = "CGROUP_PARENT_DEV_GATES"
SOCKET = "/var/run/docker.sock"
INNER = (
    "set -euo pipefail; mkdir -p /work && cd /work && tar -x && cd mdt-cockpit && "
    "exec python3 -m unittest -v scripts.test_oci_layout_push"
)


def require_environment(environ: dict[str, str]) -> tuple[str, str]:
    values = []
    for name in (ENV_IMAGE, ENV_GATES):
        value = environ.get(name, "").strip()
        if not value:
            raise SystemExit(
                f"{name} is not set. The oci-push lane tests inside the exact mdt-cockpit image it is "
                f"given and has no default: `{ENV_IMAGE}=<ref> ./run-gate.py oci-push`."
            )
        values.append(value)
    return values[0], values[1]


def docker_run_argv(image: str, gates: str, name: str) -> list[str]:
    return [
        "docker", "run", "--rm", "-i", "--name", name, "--init", "--user", "0",
        "--cgroup-parent", gates, "-v", f"{SOCKET}:{SOCKET}",
        "-e", "MDT_OCI_PUSH_REQUIRED=1", "--entrypoint", "bash", image, "-c", INNER,
    ]


def tracked_tar_argv() -> list[str]:
    """Tar of the tracked files of this project, relative to the repo root."""
    return ["bash", "-c", "git ls-files -z -- mdt-cockpit | tar --null -T - -c"]


def main() -> int:
    image, gates = require_environment(dict(os.environ))
    name = f"mdt-cockpit-oci-push-{os.getpid()}"
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    try:
        tar = subprocess.Popen(tracked_tar_argv(), cwd=repo_root, stdout=subprocess.PIPE)
        run = subprocess.run(docker_run_argv(image, gates, name), stdin=tar.stdout, check=False)
        tar.stdout.close()
        if tar.wait() != 0:
            print("oci-push: could not stream the tracked sources", file=sys.stderr)
            return 1
        return run.returncode
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
