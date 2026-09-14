#!/usr/bin/env python3
"""Create and verify MDT's named BuildKit remote builder.

The host installer and the in-container finalizer deliberately share this
small client-side contract.  The host service owns the BuildKit daemon; this
module only registers a Buildx ``remote`` node for its Unix socket.  It must
never create a ``docker-container`` worker.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Sequence

MANAGED_BUILDER = "mdt-managed"
MANAGED_ENDPOINT = "unix:///run/mdt-buildkitd/buildkitd.sock"


class BuilderError(RuntimeError):
    """The named builder is absent, inconsistent, or unreachable."""


@dataclass(frozen=True)
class BuilderInfo:
    driver: str
    endpoints: tuple[str, ...]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def parse_builder_inspect(text: str) -> BuilderInfo:
    """Parse the stable ``Driver:``/``Endpoint:`` fields from Buildx output.

    A builder with more than one endpoint is not the single managed remote
    node this project promises, so callers compare the complete endpoint tuple
    rather than merely checking that the expected endpoint appears somewhere.
    """
    driver = ""
    endpoints: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if key.strip() == "Driver":
            driver = value
        elif key.strip() == "Endpoint" and value:
            endpoints.append(value)
    if not driver:
        raise BuilderError("docker buildx inspect did not report a Driver field")
    return BuilderInfo(driver=driver, endpoints=tuple(endpoints))


def _run_docker(
    docker: str,
    args: Sequence[str],
    buildx_config: str | None = None,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    environment = None
    if buildx_config is not None:
        buildx_config = buildx_config.strip()
        if not buildx_config or not buildx_config.startswith("/"):
            raise BuilderError(
                "BUILDX_CONFIG must be an absolute path when explicitly supplied"
            )
        environment = os.environ.copy()
        environment["BUILDX_CONFIG"] = buildx_config
    return runner(
        [docker, *args],
        capture_output=True,
        text=True,
        check=False,
        **({"env": environment} if environment is not None else {}),
    )


def inspect_builder(
    docker: str,
    name: str = MANAGED_BUILDER,
    *,
    buildx_config: str | None = None,
    runner: Runner = subprocess.run,
) -> BuilderInfo | None:
    result = _run_docker(
        docker, ["buildx", "inspect", name], buildx_config, runner
    )
    if result.returncode != 0:
        return None
    try:
        return parse_builder_inspect(result.stdout)
    except BuilderError as exc:
        raise BuilderError(f"builder {name!r} has unreadable inspect output: {exc}") from exc


def _require_managed(info: BuilderInfo, endpoint: str) -> None:
    if info.driver != "remote" or info.endpoints != (endpoint,):
        actual_endpoint = ", ".join(info.endpoints) or "<missing>"
        raise BuilderError(
            f"builder {MANAGED_BUILDER!r} is not the managed remote builder: "
            f"driver={info.driver!r}, endpoint(s)={actual_endpoint!r}; "
            f"expected driver='remote', endpoint={endpoint!r}. "
            f"Refusing to replace an existing builder named {MANAGED_BUILDER!r}; "
            "inspect it and remove/rename it explicitly if it is stale."
        )


def ensure_managed_builder(
    docker: str,
    endpoint: str = MANAGED_ENDPOINT,
    *,
    buildx_config: str | None = None,
    create_if_missing: bool = True,
    select: bool = True,
    runner: Runner = subprocess.run,
) -> BuilderInfo:
    """Ensure ``mdt-managed`` is exactly one reachable remote node.

    Missing is safe to create because the requested driver and endpoint are
    explicit.  Existing drift is refused rather than silently deleting a
    builder/cache that another operator may own.  The final bootstrap is
    intentional: a successful local registration alone does not prove that
    the service socket is reachable.
    """
    if not endpoint or not endpoint.startswith("unix:///"):
        raise BuilderError(
            f"managed BuildKit endpoint must be an absolute Unix endpoint, got {endpoint!r}"
        )

    info = inspect_builder(docker, buildx_config=buildx_config, runner=runner)
    if info is None:
        if not create_if_missing:
            raise BuilderError(
                f"builder {MANAGED_BUILDER!r} is not registered; run host setup after "
                "mdt-buildkitd.service is available"
            )
        result = _run_docker(
            docker,
            [
                "buildx",
                "create",
                "--name",
                MANAGED_BUILDER,
                "--driver",
                "remote",
                "--use",
                endpoint,
            ],
            buildx_config,
            runner,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise BuilderError(
                f"could not create remote builder {MANAGED_BUILDER!r} at {endpoint!r}: "
                f"{detail[:400]}"
            )
        info = inspect_builder(docker, buildx_config=buildx_config, runner=runner)
        if info is None:
            raise BuilderError(
                f"Buildx created {MANAGED_BUILDER!r} but it cannot be inspected afterwards"
            )

    _require_managed(info, endpoint)

    if select:
        used = _run_docker(
            docker, ["buildx", "use", MANAGED_BUILDER], buildx_config, runner
        )
        if used.returncode != 0:
            detail = (used.stderr or used.stdout or "command failed").strip()
            raise BuilderError(f"could not select {MANAGED_BUILDER!r}: {detail[:400]}")

    bootstrapped = _run_docker(
        docker,
        ["buildx", "inspect", MANAGED_BUILDER, "--bootstrap"],
        buildx_config,
        runner,
    )
    if bootstrapped.returncode != 0:
        detail = (bootstrapped.stderr or bootstrapped.stdout or "command failed").strip()
        raise BuilderError(
            f"managed BuildKit builder {MANAGED_BUILDER!r} is not reachable at "
            f"{endpoint!r}: {detail[:400]}"
        )
    return info


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "verify"))
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--endpoint", default=MANAGED_ENDPOINT)
    parser.add_argument(
        "--buildx-config",
        default=None,
        help="explicit Buildx state directory (the host installer uses /etc/mdt/buildx)",
    )
    args = parser.parse_args()
    try:
        info = ensure_managed_builder(
            args.docker,
            args.endpoint,
            buildx_config=args.buildx_config,
            create_if_missing=args.command == "configure",
            select=args.command == "configure",
        )
    except (BuilderError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] {MANAGED_BUILDER}: driver={info.driver} "
        f"endpoint={info.endpoints[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
