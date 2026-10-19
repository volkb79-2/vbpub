#!/usr/bin/env python3
"""Driver of the `oci-push` lane (MDTC-02 T6, D-006), stdlib only.

The OCI-layout push test needs docker, buildx, crane and regctl, which tester-unified does not
ship and the mdt-cockpit image does. This driver runs on the bare host and runs the test inside
THE IMAGE THE RELEASE JUST BUILT: nobody types a ref. In the shipped `RELEASE_IMAGE_FLOW=load` the
`prepare` step leaves one OCI layout per bake target under `build/oci-layouts/` (the directory
`build-push.py` itself resolves) plus `transaction.json`, which binds them to that build. The driver
takes the layout of the target named by `--target` (explicit, no default), accepts it only when the
binding proves it belongs to THIS build (recorded digest, saved build state, commit lineage, no stray
layouts - see `find_layout`), imports it into the local docker store under a private unique tag
(`regctl image export | docker load`, because this daemon cannot load an OCI layout directly), runs the
lane in it and removes the container and that tag by exact name even when the load fails partway. A
missing, stale or ambiguous layout makes the lane FAIL - it never falls back to another image.

* the container is placed in the gate tier (`--cgroup-parent $CGROUP_PARENT_DEV_GATES`),
  `--init`, uniquely named and removed BY EXACT NAME;
* the host docker socket is mounted (the test starts a registry:2 container and a buildx builder);
* the sources travel as a tar stream of the tracked `mdt-cockpit/` files on stdin, so no
  devcontainer-local path is ever bind-mounted;
* MDT_OCI_PUSH_REQUIRED=1: a missing tool or unreachable registry FAILS the test.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
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
    return _build_push()._oci_layout_dir()


def _build_push():
    spec = importlib.util.spec_from_file_location("mdt_build_push", ROOT / "build-push.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def generated_prefixes(config: Path | None = None) -> list[str]:
    """Project-relative paths the release may commit between the build and the gate.

    Mirrors cmru's own rule (`_commit_prepared_generated`, cli.py): the declared `commit_generated`
    outputs PLUS the changelog - `[project.release] changelog` defaults to "CHANGES.md", a string names
    another project-relative path, and `false` opts out. Everything is read from the project config.
    """
    with (config or ROOT / "cmru.toml").open("rb") as handle:
        release = tomllib.load(handle)["project"]["release"]
    allowed = list(release.get("commit_generated", []))
    changelog = release.get("changelog", "CHANGES.md")
    if changelog is not False:
        if not isinstance(changelog, str) or not changelog.strip() or Path(changelog).is_absolute():
            raise SystemExit(f"oci-push: project.release.changelog must be a project-relative path or false, got {changelog!r}")
        allowed.append(changelog.strip())
    return allowed


def layout_digest(layout: Path) -> str:
    return top_digest(layout)


def find_layout(root: Path, target: str, git=_git, build_env: Path | None = None, prefixes: list[str] | None = None) -> Path:
    """The OCI layout of `target`, proven to belong to THIS build; anything else FAILS.

    The release's `prepare` step leaves `transaction.json` beside the layouts (build-push.py). A layout is
    accepted only when ALL of these hold: the manifest exists and names the target; the layout's top
    digest equals the recorded one; no other layout sits beside it unrecorded (ambiguous or stale);
    the saved build state (.build-env.json) carries the same BUILD_DATE and BUILD_TIMESTAMP; and the
    commit the build ran at is an ancestor of HEAD with nothing but the declared generated paths
    changed since.
    """
    manifest_path = root / "transaction.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"oci-push: no image was built in this transaction (no {manifest_path}). The lane tests inside the "
            "image the release just built and has no fallback; run it after the `prepare` step "
            "(`build-push.py --build`)."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest.get("targets") or {}
    if target not in recorded:
        raise SystemExit(f"oci-push: target {target!r} is not in {manifest_path} (built: {sorted(recorded)})")
    present = sorted(p.name for p in root.glob("*") if (p / "oci-layout").is_file())
    extra = [name for name in present if name not in recorded]
    if extra:
        raise SystemExit(f"oci-push: ambiguous layouts: {extra} sit beside the build's {sorted(recorded)} but are not part of it")
    layout = root / target
    if not (layout / "index.json").is_file():
        raise SystemExit(f"oci-push: the recorded layout {layout} is missing")
    if layout_digest(layout) != recorded[target]:
        raise SystemExit(f"oci-push: {layout} changed since the build (digest {layout_digest(layout)} != recorded {recorded[target]})")
    env_path = build_env or (ROOT / ".build-env.json")
    saved = (json.loads(env_path.read_text(encoding="utf-8")).get("vars") or {}) if env_path.is_file() else {}
    for key, field in (("BUILD_DATE", "build_date"), ("BUILD_TIMESTAMP", "build_timestamp")):
        if saved.get(key) != manifest.get(field):
            raise SystemExit(f"oci-push: the saved build state has {key}={saved.get(key)!r}, the layouts were built with {manifest.get(field)!r}: stale layouts")
    head = git(["rev-parse", "HEAD"]).stdout.strip()
    source = str(manifest.get("source_head", ""))
    if git(["merge-base", "--is-ancestor", source, head]).returncode != 0:
        raise SystemExit(f"oci-push: the layouts were built at {source}, which is not an ancestor of HEAD {head}")
    changed = [line for line in git(["diff", "--name-only", "--relative", source, head, "--", "."]).stdout.splitlines() if line]
    allowed = generated_prefixes() if prefixes is None else prefixes
    foreign = [c for c in changed if not any(c == a or c.startswith(a.rstrip("/") + "/") for a in allowed)]
    if foreign:
        raise SystemExit(f"oci-push: sources changed since the layouts were built (not generated outputs): {foreign[:5]}")
    return layout


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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OCI-layout push tests inside the image this release built.")
    parser.add_argument("--target", required=True, help="the bake target whose image the tests run in (explicit, no default)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    gates = require_gates(dict(os.environ))
    layout = find_layout(layout_root(), args.target)
    tag = f"mdt-cockpit-oci-push:{os.getpid()}"
    name = f"mdt-cockpit-oci-push-{os.getpid()}"
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    try:
        import_image(layout, tag)
        tar = subprocess.Popen(tracked_tar_argv(), cwd=repo_root, stdout=subprocess.PIPE)
        run = subprocess.run(docker_run_argv(tag, gates, name), stdin=tar.stdout, check=False)
        tar.stdout.close()
        if tar.wait() != 0:
            print("oci-push: could not stream the tracked sources", file=sys.stderr)
            return 1
        return run.returncode
    finally:
        # always, even when `docker load` failed partway: remove the container and the private tag by exact name
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["docker", "rmi", tag], capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
