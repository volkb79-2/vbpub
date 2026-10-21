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
from mdt_cli import MdtArgumentParser
import hashlib
import collections
import json
import re
import selectors
import shlex
import subprocess
import sys
import time
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
RESCAN_INTERVAL_SECONDS = 5.0


class GuardError(RuntimeError):
    """Configuration or Docker inspection could not be safely completed."""


class MalformedEvent(GuardError):
    """One `docker events` line that cannot be turned into a container id (MDT-HS-03)."""


# A single malformed line must not kill the guard: every restart (RestartSec=5) is a window in
# which created containers are not seen. It is logged loudly and answered with an immediate full
# reconcile. Only a PERSISTENT run of them means the daemon's event shape is not understood, and
# then the guard exits so systemd shows the failure instead of looping quietly.
MAX_CONSECUTIVE_MALFORMED_EVENTS = 5
# ...and a trickle of bad lines interleaved with good ones must not hide a broken parser forever
# (H-26): this many malformed events inside the window exit as well.
MAX_MALFORMED_IN_WINDOW = 20
MALFORMED_WINDOW_SECONDS = 60.0
# Docker events always carry the full 64-hex id (H-27); a shorter prefix can be ambiguous.
_CONTAINER_ID = re.compile(r"[0-9a-fA-F]{64}")


class ContainerGone(GuardError):
    """`docker inspect` says the container no longer exists (an ephemeral --rm container, H-25)."""


def _inspect_says_gone(stderr: str) -> bool:
    """True only for Docker's explicit not-found answer, so a daemon outage still fails loudly."""
    text = stderr.lower()
    return "no such container" in text or "no such object" in text


def container_id_from_event(event: object) -> str | None:
    """The container id of one `docker events --format '{{json .}}'` object, None to ignore it.

    Docker 29 (API 1.56) emits the id only as ``Actor.ID`` and no longer sends the deprecated
    top-level ``id``/``status``/``from``. The legacy ``id`` (or ``ID``) is accepted only when
    ``Actor`` is absent (older daemons) or agrees with it; a disagreement is refused, never
    guessed. The command filters on ``type=container``, so a non-container event cannot occur on
    a healthy daemon; it carries no container to police and is ignored (the periodic rescan still
    covers the host)."""
    if not isinstance(event, dict):
        raise MalformedEvent(f"event is not a JSON object: {event!r}")
    event_type = event.get("Type")
    if event_type is not None and event_type != "container":
        return None
    actor = event.get("Actor")
    raw_actor = actor.get("ID") if isinstance(actor, dict) else None
    raw_legacy = event.get("id") or event.get("ID")
    for raw in (raw_actor, raw_legacy):
        if raw is not None and not isinstance(raw, str):
            raise MalformedEvent(f"container id is not a string: {raw!r}")
    actor_id = (raw_actor or "").strip()
    legacy_id = (raw_legacy or "").strip()
    if actor_id and legacy_id and actor_id != legacy_id:
        raise MalformedEvent(f"Actor.ID ({actor_id[:12]}) and the legacy id ({legacy_id[:12]}) disagree")
    container_id = actor_id or legacy_id
    if not container_id:
        raise MalformedEvent("docker events event has no container id (neither Actor.ID nor a legacy id)")
    if not _CONTAINER_ID.fullmatch(container_id):
        raise MalformedEvent(f"container id {container_id[:80]!r} is not a full 64-hex id")
    return container_id


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


DEFAULT_CONFIRMATION_RECORD = Path("/var/lib/mdt/guard-terminate.confirmed")
DEFAULT_MACHINE_ID_FILE = Path("/etc/machine-id")


def guard_digest(policy: str, image: str, machine_id: str) -> str:
    """Digest of what the wizard confirmed; MUST equal `_mdt_guard_digest` in
    scripts/mdt-container-io-caps.lib.sh (a test compares the two)."""
    text = (
        f"BUILDX_ACCIDENTAL_CONTAINER_POLICY={policy}\n"
        f"DEV_BUILDKITD_IMAGE={image}\n"
        f"machine-id={machine_id or 'none'}\n"
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_machine_id(path: Path) -> str:
    try:
        return "".join(path.read_text(encoding="utf-8").split())
    except OSError:
        return ""


def enforce_confirmation(
    config: GuardConfig,
    record: Path = DEFAULT_CONFIRMATION_RECORD,
    machine_id_file: Path = DEFAULT_MACHINE_ID_FILE,
) -> GuardConfig:
    """terminate removes containers, so it needs a valid confirmation record: the digest of the
    current policy + image + this host. Without one the guard runs report-only and says so.
    A report-only configuration withdraws a stale record (the policy was changed away)."""
    if config.policy != "terminate":
        try:
            if record.exists():
                record.unlink()
                print(
                    f"[mdt-buildkit-guard] policy is {config.policy}: withdrew the stale terminate "
                    f"confirmation {record}; terminate needs a new wizard confirmation",
                    flush=True,
                )
        except OSError as exc:
            print(f"[mdt-buildkit-guard] WARN: could not withdraw {record}: {exc}", file=sys.stderr, flush=True)
        return config
    expected = guard_digest("terminate", config.buildkit_image, _read_machine_id(machine_id_file))
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if "policy=terminate" in lines and f"sha256={expected}" in lines:
        return config
    print(
        f"[mdt-buildkit-guard] ERROR: policy=terminate has NO valid confirmation record ({record}) for this "
        "policy, image and host; running as REPORT-ONLY. Run install.sh --wizard to confirm terminate.",
        file=sys.stderr,
        flush=True,
    )
    return GuardConfig(policy="report-only", buildkit_image=config.buildkit_image)


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


def builder_name_from_container(name: str) -> str | None:
    """The Buildx builder a `buildx_buildkit_<builder><node>` container belongs to.

    Buildx names the container after the builder plus the node index (`keen_mestorf0`), so the
    builder is the name without its trailing digits; a builder whose own name ends in digits is
    ambiguous, which is why callers print it as "builder (from the container name)".
    """
    for prefix in _CANDIDATE_PREFIXES:
        if name.startswith(prefix):
            builder = name[len(prefix):].rstrip("0123456789")
            return builder or None
    return None


def remedy_for(info: ContainerInfo, decision: Decision) -> str:
    """What the person whose build just failed with "No such container" should do.

    The guard's journal line is the only place this host explains why a worker disappeared; the
    client only sees Docker's "No such container". A builder registered by an earlier Buildx
    (selected in ~/.docker/buildx/current) is the usual cause, so say how to leave it.
    """
    if decision.status != "unapproved-buildkit-worker":
        return ""
    builder = builder_name_from_container(info.name)
    named = f"builder {builder!r} (from the container name)" if builder else "a Buildx builder"
    return (
        f" Remedy: {named} was started by a docker-container Buildx driver, which this host does not "
        "allow; builds go through the managed worker 'mdt-managed' (BuildKit service in "
        f"{MANAGED_CGROUP_PARENT}). Remove the stale builder"
        + (f" with `docker buildx rm {builder}`" if builder else "")
        + " and build with the host's docker-buildx shim or `--builder mdt-managed`; see "
        "docs/BUILD-ARCHITECTURE.md 'Making every build use mdt-managed'."
    )


def _docker(docker: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [docker, *args], capture_output=True, text=True, check=False
    )


def inspect_container(docker: str, container_id: str) -> ContainerInfo:
    result = _docker(docker, ["inspect", container_id])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "inspect failed").strip()
        if _inspect_says_gone(result.stderr or ""):
            raise ContainerGone(f"container {container_id[:12]} is gone: {detail[:200]}")
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
            f"[mdt-buildkit-guard] REPORT-ONLY {decision.status}: {prefix}; {decision.reason}."
            f"{remedy_for(info, decision)}",
            flush=True,
        )
        return decision
    removed = _docker(docker, ["rm", "-f", info.container_id])
    if removed.returncode != 0 and _inspect_says_gone(removed.stderr or ""):
        print(f"[mdt-buildkit-guard] info: {prefix} was already gone before rm", flush=True)
        return decision
    if removed.returncode != 0:
        detail = (removed.stderr or removed.stdout or "docker rm failed").strip()
        raise GuardError(
            f"policy=terminate could not remove {prefix}: {detail[:300]}"
        )
    print(
        f"[mdt-buildkit-guard] TERMINATED {decision.status}: {prefix}; {decision.reason}."
        f"{remedy_for(info, decision)}",
        flush=True,
    )
    return decision


def reconcile(docker: str, container_id: str, config: GuardConfig) -> Decision | None:
    """Reconcile one container; None when it vanished before inspect (nothing left to police)."""
    try:
        info = inspect_container(docker, container_id)
    except ContainerGone as exc:
        print(f"[mdt-buildkit-guard] info: skipped, {exc}", flush=True)
        return None
    return handle_container(docker, info, config)


def reconcile_all(docker: str, config: GuardConfig) -> None:
    """Reconcile the complete container set using the inspect identity oracle."""
    for container_id in _container_ids(docker):
        reconcile(docker, container_id, config)


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
    result = _docker(docker, ["ps", "-aq", "--no-trunc"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "docker ps failed").strip()
        raise GuardError(f"cannot enumerate containers: {detail[:300]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def watch(
    docker: str,
    config: GuardConfig,
    *,
    rescan_interval: float = RESCAN_INTERVAL_SECONDS,
    popen=None,
    clock=None,
    selector_factory=None,
) -> int:
    """Watch events and periodically reconcile all containers.

    The initial ``ps`` cannot be made atomic with starting ``docker events``.
    A periodic full scan closes that gap and also repairs a dropped or delayed
    event without weakening the inspect-based identity check.  The injectable
    timing/process primitives keep the race oracle deterministic without a
    Docker daemon.
    """
    if rescan_interval <= 0:
        raise GuardError("guard rescan interval must be greater than zero")
    if popen is None:
        popen = subprocess.Popen
    if clock is None:
        clock = time.monotonic
    if selector_factory is None:
        selector_factory = selectors.DefaultSelector

    reconcile_all(docker, config)

    process = popen(
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
    selector = selector_factory()
    selector.register(process.stdout, selectors.EVENT_READ)
    next_rescan = clock() + rescan_interval
    malformed = 0
    malformed_times: collections.deque[float] = collections.deque()
    last_malformed_sweep: float | None = None
    try:
        while True:
            ready = selector.select(max(0.0, next_rescan - clock()))
            now = clock()
            if now >= next_rescan:
                reconcile_all(docker, config)
                next_rescan = now + rescan_interval

            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue
            if line.strip():
                try:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise MalformedEvent(f"docker events returned invalid JSON: {exc}") from exc
                    container_id = container_id_from_event(event)
                except MalformedEvent as exc:
                    malformed += 1
                    stamp = clock()
                    malformed_times.append(stamp)
                    while malformed_times and stamp - malformed_times[0] > MALFORMED_WINDOW_SECONDS:
                        malformed_times.popleft()
                    print(
                        f"[mdt-buildkit-guard] ERROR: malformed docker event ({malformed} in a row): {exc}; "
                        f"line={line.strip()[:300]!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if malformed >= MAX_CONSECUTIVE_MALFORMED_EVENTS:
                        raise GuardError(
                            f"{malformed} malformed docker events in a row (last: {exc}); the daemon's event "
                            "shape is not understood, exiting so the failure is visible"
                        ) from exc
                    if len(malformed_times) >= MAX_MALFORMED_IN_WINDOW:
                        raise GuardError(
                            f"{len(malformed_times)} malformed docker events within "
                            f"{MALFORMED_WINDOW_SECONDS:.0f}s (last: {exc}); the daemon's event shape is not "
                            "understood, exiting so the failure is visible"
                        ) from exc
                    # no restart, no missed-event window: look at everything now, but at most once per
                    # rescan interval (H-28); the periodic rescan covers the rest
                    if last_malformed_sweep is None or stamp - last_malformed_sweep >= rescan_interval:
                        last_malformed_sweep = stamp
                        reconcile_all(docker, config)
                    continue
                malformed = 0
                if container_id is None:
                    print(f"[mdt-buildkit-guard] ignored non-container event: {line.strip()[:200]!r}", flush=True)
                    continue
                reconcile(docker, container_id, config)
    finally:
        selector.unregister(process.stdout)
        selector.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
    detail = ""
    if process.stderr:
        detail = process.stderr.read().strip()
    raise GuardError(f"docker events ended unexpectedly{(': ' + detail[:300]) if detail else ''}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = MdtArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/mdt/host-setup.env"))
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--verify-managed-container", metavar="CONTAINER_ID")
    parser.add_argument("--confirmation-record", type=Path, default=DEFAULT_CONFIRMATION_RECORD)
    parser.add_argument("--machine-id-file", type=Path, default=DEFAULT_MACHINE_ID_FILE)
    args = parser.parse_args(argv)
    try:
        config = enforce_confirmation(load_config(args.config), args.confirmation_record, args.machine_id_file)
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
