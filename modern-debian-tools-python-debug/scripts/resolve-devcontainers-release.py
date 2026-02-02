#!/usr/bin/env python3
"""Resolve devcontainers release labels for stable/dev base images.

Outputs KEY=VALUE lines for step_runner env_command consumption.
"""
from __future__ import annotations

import os
import subprocess
import sys


def inspect_release(image: str) -> str:
    def _inspect() -> str:
        result = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{ index .Config.Labels \"dev.containers.release\" }}",
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    release = _inspect()
    if release:
        return release

    pull = subprocess.run(
        ["docker", "pull", image],
        text=True,
        capture_output=True,
    )
    if pull.returncode != 0:
        raise RuntimeError(f"Failed to pull base image: {image}")

    release = _inspect()
    if not release:
        raise RuntimeError(f"Missing dev.containers.release label on image: {image}")
    return release


def main() -> int:
    stable_image = os.getenv(
        "DEVCONTAINERS_BASE_STABLE",
        "mcr.microsoft.com/devcontainers/python:3.14-trixie",
    )
    dev_image = os.getenv(
        "DEVCONTAINERS_BASE_DEV",
        "mcr.microsoft.com/devcontainers/python:dev-3.14-trixie",
    )

    stable_release = inspect_release(stable_image)
    dev_release = inspect_release(dev_image)

    print(f"DEVCONTAINERS_RELEASE_STABLE={stable_release}")
    print(f"DEVCONTAINERS_RELEASE_DEV={dev_release}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
