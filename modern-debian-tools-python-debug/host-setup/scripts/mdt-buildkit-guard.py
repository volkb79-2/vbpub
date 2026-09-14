#!/usr/bin/env python3
"""Enforce MDT's policy against accidental Buildx worker containers.

The watcher consumes Docker's event stream and uses Docker inspect as the
identity oracle.  Names identify candidates, but approval requires the
managed service's name, configured image, cgroup parent, and immutable labels
to agree.  An inspect failure is indeterminate and terminates the watcher so
systemd can restart it; it is never treated as approval.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

MANAGED_BUILDER = "mdt-managed"
MANAGED_CONTAINER = "mdt-buildkitd"
MANAGED_CGROUP_PARENT = "dev-buildkitd.slice"
MANAGED_LABELS = {
    "io.volkb79.mdt.buildkit.managed": "true",
    "io.volkb79.mdt.buildkit.builder": MANAGED_BUILDER,
    "io.volkb79.mdt.buildkit.slice": MANAGED_CGROUP_PARENT,
}
POLICY_KEY = "BUILDX_ACCIDENTAL_CONTAINER_POLICY"
POLICIES = frozenset(("terminate", "report-only"))
_ASSIGNMENT = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$")
_CANDIDATE_PREFIXES = ("buildx_buildkit_", "buildkit_buildkit_")


class GuardError(RuntimeError):
    """Configuration or Docker inspection could not be safely completed."""


@dataclass(frozen=True)
class GuardConfig:
    policy: str
    buildkit_image: str


@dataclass(frozen=True)
class ContainerInfo:
    container_id: str
    name: str
    image: str
    cgroup_parent: str
    labels: dict[str, str]


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    enforce: bool


def parse_env_file(text: str) -> dict[str, str]:
    """Parse the assignment subset used by host-setup.env without executing it."""
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            raise GuardError(f"invalid host-setup.env line {line_number}: {line!r}")
        key, raw = match.groups()
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            raise GuardError(f"invalid quoting for {key} on line {line_number}: {exc}") from exc
        if len(tokens) > 1:
            raise GuardError(f"unquoted whitespace in {key} on line {line_number}")
        value = tokens[0] if tokens else ""
        if key in values:
            raise GuardError(f"duplicate {key} in host-setup.env")
        values[key] = value
    return values


def load_config(path: Path) -> GuardConfig:
    try:
        values = parse_env_file(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GuardError(f"cannot read required config {path}: {exc}") from exc
    policy = values.get(POLICY_KEY, "")
    if policy not in POLICIES:
        allowed = " or ".join(sorted(POLICIES))
        raise GuardError(
            f"{POLICY_KEY} is missing or invalid ({policy!r}); set exactly {allowed!r}"
        )
    image = values.get("DEV_BUILDKITD_IMAGE", "").strip()
    if not image:
        raise GuardError(
            "DEV_BUILDKITD_IMAGE is missing or empty; refusing to approve any managed container"
        )
    return GuardConfig(policy=policy, buildkit_image=image)


def is_buildkit_image(image: str) -> bool:
    """Recognize BuildKit image references without approving by name alone."""
    reference = image.split("@", 1)[0]
    last = reference.rsplit("/", 1)[-1]
    repository = last.split(":", 1)[0]
    return repository == "buildkit" or reference in {"moby/buildkit", "docker.io/moby/buildkit"} or reference.endswith("/buildkit")


def from_inspect(container_id: str, payload: object) -> ContainerInfo:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise GuardError(f"docker inspect for {container_id} returned an unexpected shape")
    data = payload[0]
    config = data.get("Config")
    host_config = data.get("HostConfig")
    if not isinstance(config, dict) or not isinstance(host_config, dict):
        raise GuardError(f"docker inspect for {container_id} omitted Config or HostConfig")
    labels = config.get("Labels")
    if labels is None:
        labels = {}
    if not isinstance(labels, dict):
        raise GuardError(f"docker inspect for {container_id} returned invalid labels")
    return ContainerInfo(
        container_id=container_id,
        name=str(data.get("Name", "")).lstrip("/"),
        image=str(config.get("Image", "")),
        cgroup_parent=str(host_config.get("CgroupParent", "")),
        labels={str(key): str(value) for key, value in labels.items()},
    )


def is_approved_managed_container(info: ContainerInfo, config: GuardConfig) -> bool:
    return (
        info.name == MANAGED_CONTAINER
        and info.image == config.buildkit_image
        and info.cgroup_parent == MANAGED_CGROUP_PARENT
        and all(info.labels.get(key) == value for key, value in MANAGED_LABELS.items())
    )


def decide(info: ContainerInfo, config: GuardConfig) -> Decision:
    if is_approved_managed_container(info, config):
        return Decision("approved-managed", "name/image/cgroup/labels match host service", False)
    if info.name == MANAGED_CONTAINER:
        return Decision(
            "unapproved-managed-name",
            "reserved service name failed the managed image/cgroup/label identity check",
            True,
        )
    if not info.name.startswith(_CANDIDATE_PREFIXES):
        return Decision("ignored", "container is outside the reserved Buildx/BuildKit name space", False)
    if not is_buildkit_image(info.image):
        return Decision(
            "ignored",
            f"reserved-looking name has non-BuildKit image {info.image!r}",
            False,
        )
    return Decision(
        "unapproved-buildkit-worker",
        f"BuildKit image {info.image!r} is not the inspected managed service",
        True,
    )


def _docker(docker: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [docker, *args], capture_output=True, text=True, check=False
    )


def inspect_container(docker: str, container_id: str) -> ContainerInfo:
    result = _docker(docker, ["inspect", container_id])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "inspect failed").strip()
        raise GuardError(f"cannot inspect container {container_id}: {detail[:300]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GuardError(f"docker inspect for {container_id} returned invalid JSON: {exc}") from exc
    return from_inspect(container_id, payload)


def handle_container(docker: str, info: ContainerInfo, config: GuardConfig) -> Decision:
    decision = decide(info, config)
    prefix = f"id={info.container_id[:12]} name={info.name or '<unnamed>'}"
    if not decision.enforce:
        print(f"[mdt-buildkit-guard] {decision.status}: {prefix}; {decision.reason}", flush=True)
        return decision
    if config.policy == "report-only":
        print(
            f"[mdt-buildkit-guard] REPORT-ONLY {decision.status}: {prefix}; {decision.reason}",
            flush=True,
        )
        return decision
    removed = _docker(docker, ["rm", "-f", info.container_id])
    if removed.returncode != 0:
        detail = (removed.stderr or removed.stdout or "docker rm failed").strip()
        raise GuardError(
            f"policy=terminate could not remove {prefix}: {detail[:300]}"
        )
    print(
        f"[mdt-buildkit-guard] TERMINATED {decision.status}: {prefix}; {decision.reason}",
        flush=True,
    )
    return decision


def reconcile(docker: str, container_id: str, config: GuardConfig) -> Decision:
    return handle_container(docker, inspect_container(docker, container_id), config)


def verify_managed_container(docker: str, container_id: str, config: GuardConfig) -> ContainerInfo:
    info = inspect_container(docker, container_id)
    if not is_approved_managed_container(info, config):
        decision = decide(info, config)
        raise GuardError(
            f"{container_id} is not the approved {MANAGED_CONTAINER} service "
            f"({decision.status}: {decision.reason})"
        )
    return info


def _container_ids(docker: str) -> list[str]:
    result = _docker(docker, ["ps", "-aq"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "docker ps failed").strip()
        raise GuardError(f"cannot enumerate containers: {detail[:300]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def watch(docker: str, config: GuardConfig) -> int:
    for container_id in _container_ids(docker):
        reconcile(docker, container_id, config)

    process = subprocess.Popen(
        [
            docker,
            "events",
            "--filter",
            "type=container",
            "--filter",
            "event=create",
            "--filter",
            "event=start",
            "--format",
            "{{json .}}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GuardError(f"docker events returned invalid JSON: {exc}") from exc
            container_id = str(event.get("id") or event.get("ID") or "").strip()
            if not container_id:
                raise GuardError(f"docker events event has no container id: {line.strip()!r}")
            reconcile(docker, container_id, config)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()
    detail = ""
    if process.stderr:
        detail = process.stderr.read().strip()
    raise GuardError(f"docker events ended unexpectedly{(': ' + detail[:300]) if detail else ''}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/mdt/host-setup.env"))
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--verify-managed-container", metavar="CONTAINER_ID")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        print(
            f"[mdt-buildkit-guard] policy={config.policy} image={config.buildkit_image}",
            flush=True,
        )
        if args.verify_managed_container:
            info = verify_managed_container(args.docker, args.verify_managed_container, config)
            print(f"[mdt-buildkit-guard] approved {info.name} id={info.container_id[:12]}", flush=True)
            return 0
        watch(args.docker, config)
    except (GuardError, OSError) as exc:
        print(f"[mdt-buildkit-guard] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
