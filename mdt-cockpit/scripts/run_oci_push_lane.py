#!/usr/bin/env python3
"""Driver of the `oci-push` lane (MDTC-02 T6, D-006), stdlib only.

The OCI-layout push test needs docker, buildx, crane and regctl, which tester-unified does not
ship and the mdt-cockpit image does. This driver runs on the bare host and runs the test inside
THE IMAGE THE RELEASE JUST BUILT: nobody types a ref. In the shipped `RELEASE_IMAGE_FLOW=load` the
`prepare` step leaves one OCI layout per bake target under `build/oci-layouts/` (the directory
`build-push.py` itself resolves); this driver imports the first one into the local docker store
under a private unique tag (`regctl image export | docker load`, because this daemon cannot load an
OCI layout directly), runs the lane in it and removes that tag by exact name. If the release built
no image there is nothing to test and the lane FAILS - it never falls back to another image.

* the container is placed in the gate tier (`--cgroup-parent $CGROUP_PARENT_DEV_GATES`),
  `--init`, uniquely named and removed BY EXACT NAME;
* the host docker socket is mounted (the test starts a registry:2 container and a buildx builder);
* the sources travel as a tar stream of the tracked `mdt-cockpit/` files on stdin, so no
  devcontainer-local path is ever bind-mounted;
* MDT_OCI_PUSH_REQUIRED=1: a missing tool or unreachable registry FAILS the test.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_GATES = "CGROUP_PARENT_DEV_GATES"
SOCKET = "/var/run/docker.sock"
INNER = (
    "set -euo pipefail; mkdir -p /work && cd /work && tar -x && cd mdt-cockpit && "
    "python3 -m unittest -v scripts.test_oci_layout_push && "
    "exec python3 -m pytest -q -rs -p no:cacheprovider "
    "scripts/test_local_dev.py::test_the_bake_file_gives_a_local_dev_build_only_dev_tags_and_leaves_release_tags_alone"
)


def require_gates(environ: dict[str, str]) -> str:
    value = environ.get(ENV_GATES, "").strip()
    if not value:
        raise SystemExit(f"{ENV_GATES} is not set; the lane never guesses a cgroup tier.")
    return value


def layout_root() -> Path:
    """The directory build-push.py writes the per-target OCI layouts to (its own resolution)."""
    spec = importlib.util.spec_from_file_location("mdt_build_push", ROOT / "build-push.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._oci_layout_dir()


def find_layout(root: Path) -> Path:
    """The OCI layout of the image the release built; FAIL when it built none."""
    layouts = sorted(p for p in root.glob("*") if (p / "oci-layout").is_file() and (p / "index.json").is_file()) if root.is_dir() else []
    if not layouts:
        raise SystemExit(
            f"oci-push: no image was built in this transaction (no OCI layout under {root}). "
            "The lane tests inside the image the release just built and has no fallback; run it "
            "after the `prepare` step (`build-push.py --build`)."
        )
    return layouts[0]


def top_digest(layout: Path) -> str:
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    manifests = index.get("manifests") or []
    if len(manifests) < 1 or not str(manifests[0].get("digest", "")).startswith("sha256:"):
        raise SystemExit(f"oci-push: {layout}/index.json lists no manifest digest")
    return manifests[0]["digest"]


def export_argv(layout: Path, digest: str, tag: str) -> list[str]:
    return ["regctl", "image", "export", "--platform", "linux/amd64", "--name", tag, f"ocidir://{layout}@{digest}"]


def import_image(layout: Path, tag: str) -> None:
    """regctl image export | docker load, streamed (the image is gigabytes; no temp file)."""
    exporter = subprocess.Popen(export_argv(layout, top_digest(layout), tag), stdout=subprocess.PIPE)
    loaded = subprocess.run(["docker", "load"], stdin=exporter.stdout, capture_output=True, text=True, check=False)
    exporter.stdout.close()
    if exporter.wait() != 0 or loaded.returncode != 0:
        raise SystemExit(f"oci-push: could not import {layout} into docker: {loaded.stderr.strip()}")


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
    gates = require_gates(dict(os.environ))
    layout = find_layout(layout_root())
    tag = f"mdt-cockpit-oci-push:{os.getpid()}"
    name = f"mdt-cockpit-oci-push-{os.getpid()}"
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    imported = False
    try:
        import_image(layout, tag)
        imported = True
        tar = subprocess.Popen(tracked_tar_argv(), cwd=repo_root, stdout=subprocess.PIPE)
        run = subprocess.run(docker_run_argv(tag, gates, name), stdin=tar.stdout, check=False)
        tar.stdout.close()
        if tar.wait() != 0:
            print("oci-push: could not stream the tracked sources", file=sys.stderr)
            return 1
        return run.returncode
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        if imported:
            subprocess.run(["docker", "rmi", tag], capture_output=True, check=False)  # exact private tag


if __name__ == "__main__":
    raise SystemExit(main())
