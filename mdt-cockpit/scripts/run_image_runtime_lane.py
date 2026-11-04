#!/usr/bin/env python3
"""Driver of the `image-runtime-built` lane (MDT-012): the runtime acceptance of the images THIS release built.

`run-gate image-runtime` takes explicit image refs and stays the manual lane (local-dev builds, published
tags). The release cannot name refs, so this driver derives them the way `oci-push` does
(`run_oci_push_lane.find_layout`): in the shipped `RELEASE_IMAGE_FLOW=load` the `prepare` step leaves one OCI
layout per bake target plus `transaction.json`; the driver accepts the native and the VS Code layout only
when that binding proves they belong to THIS build, imports each into the local docker store under a private
unique tag, runs `tests/runtime` with `MDT_RUNTIME_IMAGE_NATIVE` / `MDT_RUNTIME_IMAGE_VSC` set to those tags,
and removes the tags by exact name even when an import fails partway. No built image, a stale or ambiguous
layout, or a failing scenario makes the lane FAIL; there is no fallback to another image.

The targets are explicit arguments (no default): `--native` and `--vsc`.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_oci_push_lane as oci  # noqa: E402

ROOT = oci.ROOT
ENV_NATIVE = "MDT_RUNTIME_IMAGE_NATIVE"
ENV_VSC = "MDT_RUNTIME_IMAGE_VSC"


def runtime_argv() -> list[str]:
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests/runtime", "-p", "test_*.py", "-v"]


def runtime_environ(base: dict[str, str], native_tag: str, vsc_tag: str) -> dict[str, str]:
    env = dict(base)
    env[ENV_NATIVE] = native_tag
    env[ENV_VSC] = vsc_tag
    return env


def tags_for(pid: int) -> tuple[str, str]:
    return f"mdt-cockpit-runtime-native:{pid}", f"mdt-cockpit-runtime-vsc:{pid}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the image-runtime scenarios against the images this release built.")
    parser.add_argument("--native", required=True, help="the bake target of the native image (explicit, no default)")
    parser.add_argument("--vsc", required=True, help="the bake target of the VS Code devcontainer image (explicit, no default)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    oci.require_gates(dict(os.environ))
    root = oci.layout_root()
    native_layout = oci.find_layout(root, args.native)
    vsc_layout = oci.find_layout(root, args.vsc)
    native_tag, vsc_tag = tags_for(os.getpid())
    try:
        oci.import_image(native_layout, native_tag)
        oci.import_image(vsc_layout, vsc_tag)
        return subprocess.run(runtime_argv(), cwd=ROOT, env=runtime_environ(dict(os.environ), native_tag, vsc_tag), check=False).returncode
    finally:
        # always, even when an import failed partway: remove the private tags by exact name
        for tag in (native_tag, vsc_tag):
            subprocess.run(["docker", "rmi", tag], capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
