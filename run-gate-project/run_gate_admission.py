"""Daemon-wide gate tickets compatible with ciu v8's count admission.

This module deliberately depends on no run-gate state. It is kept small and
standalone so ciu8 can copy the same object-name, label and release rules into
``gate/admission.py``. Callers supply policy and presentation callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import socket
import subprocess
import time
from typing import Callable


TICKET_PREFIX = "ciu-res-gates-"
ADMISSION_PREFIX = "ciu-admission-"
RUN_PREFIX = "ciu-run-"
TICKET_LABEL = "ciu.reservation.tier=gates"
ADMISSION_LABEL = "ciu.admission.generation"
TICKET_GRACE_SECONDS = 300
MARKER_GRACE_SECONDS = 3600
DEFAULT_LANE_DEADLINE_SECONDS = 24 * 3600
CONFLICT_WAIT_SECONDS = 30
LABEL_VALUE_GRAMMAR = {
    "ciu.admission.generation": "decimal-integer",
    "ciu.admission.owner": "json-compact-sorted{ciu_version,host,time,user}",
    "ciu.admission.tiers.gates.max_concurrent": "decimal-integer",
    "ciu.admission.unreadable_policy": "refuse|unbudgeted",
    "ciu.reservation.deadline": "epoch-seconds-utc-decimal-integer",
    "ciu.reservation.group": "ticket-name",
    "ciu.reservation.kind": "lane|marker",
    "ciu.reservation.override": "true",
    "ciu.reservation.owner": "json-compact-sorted{boot_id=stripped-proc-boot_id,host=gethostname-unqualified,lane,pid,pid_ns=decimal-inode-string,run_id,start_ticks}",
    "ciu.reservation.scheme": "settle|ticket",
    "ciu.reservation.tier": "gates",
}


class AdmissionError(RuntimeError):
    """Docker or published-policy state cannot support a safe decision."""


class AdmissionDockerUnavailable(AdmissionError):
    """The Docker CLI or daemon could not answer an admission operation."""


class AdmissionRefused(AdmissionError):
    """The count policy was readable and this gate did not fit."""

    reason = "no-headroom"


@dataclass(frozen=True)
class AdmissionTicket:
    name: str
    number: int
    marker: str
    waited_s: float
    override: bool
    admitted_at: float
    admitted_monotonic: float
    run_deadline: int

    def result(self) -> dict:
        return {"tier": "gates", "ticket": self.name,
                "waited_s": round(self.waited_s, 3),
                "override": self.override}


def compact_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def process_start_ticks(pid: int, proc_root: str | os.PathLike[str] = "/proc") -> int:
    """Read field 22 of procfs stat, accounting for spaces in comm."""
    raw = open(os.path.join(os.fspath(proc_root), str(pid), "stat"),
               encoding="ascii").read()
    close = raw.rfind(")")
    if close < 0:
        raise AdmissionError(f"cannot parse /proc/{pid}/stat")
    fields = raw[close + 1:].split()  # starts at field 3 (state)
    try:
        return int(fields[19])       # field 22
    except (IndexError, ValueError) as exc:
        raise AdmissionError(f"cannot read start_ticks for pid {pid}") from exc


def owner_tuple(lane: str, run_id: str, *,
                proc_root: str | os.PathLike[str] = "/proc",
                hostname: str | None = None) -> dict:
    """The exact cross-tool owner encoding from SPEC-V8 S21.4.8."""
    pid = os.getpid()
    root = os.fspath(proc_root)
    try:
        boot_id = open(os.path.join(root, "sys/kernel/random/boot_id"),
                       encoding="ascii").read().strip()
        # S21.4.8 pins the value to this process's own namespace inode. The
        # owner's pid is separately paired with start_ticks below.
        pid_ns = str(os.stat(os.path.join(root, "self/ns/pid")).st_ino)
        start_ticks = process_start_ticks(pid, root)
    except OSError as exc:
        raise AdmissionError(f"cannot read gate owner identity: {exc}") from exc
    host = (hostname or socket.gethostname()).split(".", 1)[0]
    if not boot_id or not host or not run_id or not lane:
        raise AdmissionError("gate owner identity contains an empty field")
    return {"boot_id": boot_id, "host": host, "lane": lane,
            "pid": pid, "pid_ns": pid_ns, "run_id": run_id,
            "start_ticks": start_ticks}


def validate_owner(value: object) -> dict | None:
    """Parse only the pinned owner JSON shape; malformed labels stay opaque."""
    if not isinstance(value, str):
        return None
    try:
        owner = json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(owner, dict) or set(owner) != {
            "boot_id", "host", "lane", "pid", "pid_ns", "run_id",
            "start_ticks"}:
        return None
    if not all(isinstance(owner[key], str) and owner[key]
               for key in ("boot_id", "host", "lane", "pid_ns", "run_id")):
        return None
    if (isinstance(owner["pid"], bool)
            or not isinstance(owner["pid"], int) or owner["pid"] < 1
            or isinstance(owner["start_ticks"], bool)
            or not isinstance(owner["start_ticks"], int)
            or owner["start_ticks"] < 0):
        return None
    if str(owner["pid_ns"]).isdecimal() is False:
        return None
    # Enforce the wire spelling as well as its JSON meaning.
    if compact_json(owner) != value:
        return None
    return owner


def parse_decimal(value: object, label: str) -> int | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        return None
    try:
        return int(value)
    except ValueError:
        # Python limits decimal conversion length. Docker labels are external
        # input: an oversized decimal is unreadable state, not an exception
        # that can bypass the caller's closed-result handling.
        return None


def parse_reservation_labels(labels: object) -> dict | None:
    """Return a validated shared label record, or None when it is malformed.

    Readers count malformed ticket labels as live and report them. They never
    reclaim an object from a label they cannot prove belongs to this grammar.
    """
    if not isinstance(labels, dict):
        return None
    owner = validate_owner(labels.get("ciu.reservation.owner"))
    deadline = parse_decimal(labels.get("ciu.reservation.deadline"),
                             "ciu.reservation.deadline")
    tier = labels.get("ciu.reservation.tier")
    kind = labels.get("ciu.reservation.kind")
    scheme = labels.get("ciu.reservation.scheme")
    group = labels.get("ciu.reservation.group")
    override = labels.get("ciu.reservation.override")
    if (owner is None or deadline is None or tier != "gates"
            or kind not in ("lane", "marker")
            or scheme not in ("ticket", "settle")
            or not isinstance(group, str) or not group):
        return None
    if override is not None and override != "true":
        return None
    return {"owner": owner, "deadline": deadline, "tier": tier,
            "kind": kind, "scheme": scheme, "group": group,
            "override": override == "true"}


class DockerAdmission:
    """Docker-backed admission. ``run`` and clocks are injectable for tests."""

    def __init__(self, docker: str = "docker", *,
                 run: Callable = subprocess.run,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 notice: Callable[[str], None] | None = None,
                 owner_provider: Callable[[str, str], dict] = owner_tuple,
                 cgroup_parent: str | None = None,
                 proc_root: str | os.PathLike[str] = "/proc",
                 conflict_wait: float = CONFLICT_WAIT_SECONDS,
                 poll_seconds: float = 2.0):
        self.docker = docker
        self.run = run
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.notice = notice or (lambda _message: None)
        self.owner_provider = owner_provider
        self.cgroup_parent = cgroup_parent
        self.proc_root = os.fspath(proc_root)
        self.conflict_wait = conflict_wait
        self.poll_seconds = poll_seconds

    def _call(self, *argv: str, check: bool = True,
              timeout: float | None = None) -> subprocess.CompletedProcess:
        try:
            proc = self.run([self.docker, *argv], capture_output=True,
                            text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdmissionDockerUnavailable(
                f"docker {' '.join(argv[:2])} failed: {exc}") from exc
        if check and proc.returncode:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            message = (detail[-1] if detail else "no detail")
            if self._looks_unavailable(message):
                raise AdmissionDockerUnavailable(
                    f"docker {' '.join(argv[:2])} failed "
                    f"({proc.returncode}): {message}")
            raise AdmissionError(f"docker {' '.join(argv[:2])} failed "
                                 f"({proc.returncode}): {message}")
        return proc

    @staticmethod
    def _looks_unavailable(detail: str) -> bool:
        lowered = detail.lower()
        return any(token in lowered for token in (
            "cannot connect to the docker daemon", "is the docker daemon "
            "running", "error during connect", "permission denied while "
            "trying to connect", "docker: command not found"))

    def verify_image(self, image: str, *, timeout: float = 20.0) -> None:
        """Prove the explicitly configured image is local and can tombstone.

        The admission path must never let Docker pull a guessed image. The
        bounded probe also catches images without the configured /bin/true
        entrypoint before a live ticket can become impossible to release.
        """
        if not self.image_present(image, timeout=timeout):
            raise AdmissionError(f"admission ticket image {image!r} is not "
                                 "present locally; run-gate never pulls an "
                                 "implicit admission image")
        argv = ["run", "--rm", "--pull=never", "--network=none"]
        if self.cgroup_parent:
            argv += ["--cgroup-parent", self.cgroup_parent]
        argv += ["--entrypoint", "/bin/true", image]
        proc = self._call(*argv, check=False, timeout=timeout)
        if proc.returncode:
            raw_detail = (proc.stderr or proc.stdout or "").strip()
            if self._looks_unavailable(raw_detail):
                raise AdmissionDockerUnavailable(
                    f"docker ticket image probe failed: {raw_detail}")
            detail = raw_detail.splitlines()
            raise AdmissionError(f"admission ticket image {image!r} cannot "
                                 "run /bin/true: "
                                 f"{detail[-1] if detail else 'no detail'}")

    def image_present(self, image: str, *, timeout: float = 20.0) -> bool:
        """Read-only check for the explicitly named local ticket image."""
        if not isinstance(image, str) or not image.strip():
            raise AdmissionError("admission ticket_image must be explicit")
        inspected = self._call("image", "inspect", image, check=False,
                               timeout=timeout)
        if inspected.returncode:
            detail = (inspected.stderr or inspected.stdout or "").strip()
            if self._looks_unavailable(detail):
                raise AdmissionDockerUnavailable(
                    f"docker image inspect failed: {detail}")
            if "no such image" in detail.lower():
                return False
            raise AdmissionError(
                f"docker image inspect failed ({inspected.returncode}): "
                f"{detail or 'no detail'}")
        return True

    def _containers(self, *, label: str | None = None,
                    name: str | None = None) -> list[dict]:
        command = ["ps", "--all", "--quiet", "--no-trunc"]
        if label:
            command += ["--filter", f"label={label}"]
        if name:
            command += ["--filter", f"name={name}"]
        listed = self._call(*command).stdout.split()
        if not listed:
            return []
        raw = self._call("inspect", *listed).stdout
        try:
            objects = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdmissionError(f"docker inspect returned invalid JSON: {exc}") from exc
        if not isinstance(objects, list):
            raise AdmissionError("docker inspect did not return a list")
        return [obj for obj in objects if isinstance(obj, dict)]

    @staticmethod
    def _name(obj: dict) -> str:
        return str(obj.get("Name", "")).lstrip("/")

    @staticmethod
    def _labels(obj: dict) -> dict:
        return obj.get("Config", {}).get("Labels") or {}

    @staticmethod
    def _status(obj: dict) -> str:
        state = obj.get("State") or {}
        return str(state.get("Status", "unknown"))

    def _admission_objects(self) -> list[dict]:
        return [obj for obj in self._containers(name=ADMISSION_PREFIX)
                if self._name(obj).startswith(ADMISSION_PREFIX)]

    def _ticket_objects(self) -> list[dict]:
        return [obj for obj in self._containers(name=TICKET_PREFIX)
                if re.fullmatch(r"ciu-res-gates-[1-9][0-9]*", self._name(obj))]

    def _group_objects(self, group: str) -> list[dict]:
        return self._containers(label=f"ciu.reservation.group={group}")

    def _highest_admission(self) -> tuple[int, dict | None]:
        objects = self._admission_objects()
        decoded = []
        for obj in objects:
            labels = self._labels(obj)
            generation = parse_decimal(labels.get("ciu.admission.generation"),
                                       "ciu.admission.generation")
            owner = labels.get("ciu.admission.owner")
            try:
                owner_doc = json.loads(owner) if isinstance(owner, str) else None
            except (ValueError, TypeError, RecursionError):
                owner_doc = None
            if (generation is None or generation < 1
                    or self._name(obj) != f"{ADMISSION_PREFIX}{generation}"
                    or not self._valid_admission_owner(owner_doc, owner)):
                self.notice(f"run-gate: admission WARNING: unreadable published "
                            f"object {self._name(obj)!r}; it is not used")
                continue
            decoded.append((generation, obj))
        if not decoded:
            return 0, None
        decoded.sort(key=lambda item: item[0])
        if len(decoded) > 1:
            self.notice("run-gate: admission WARNING: multiple generations "
                        "are visible; using the highest")
        return decoded[-1]

    @staticmethod
    def _valid_admission_owner(owner: object, raw: object = None) -> bool:
        return (isinstance(owner, dict)
                and set(owner) == {"ciu_version", "host", "time", "user"}
                and all(isinstance(owner[key], str) and owner[key]
                        for key in ("ciu_version", "host", "user"))
                and isinstance(owner["time"], int)
                and not isinstance(owner["time"], bool)
                and owner["time"] >= 0
                and isinstance(raw, str) and compact_json(owner) == raw)

    def read_limit(self) -> tuple[int | None, str, int | None, dict | None]:
        """Read the published count fresh. The local policy is a caller fact."""
        generation, obj = self._highest_admission()
        if obj is None:
            return None, "unreadable", None, None
        labels = self._labels(obj)
        limit = parse_decimal(labels.get(
            "ciu.admission.tiers.gates.max_concurrent"),
            "ciu.admission.tiers.gates.max_concurrent")
        if limit is not None and limit < 1:
            limit = None
        policy = labels.get("ciu.admission.unreadable_policy")
        if policy not in ("refuse", "unbudgeted"):
            policy = "unreadable"
        owner = labels.get("ciu.admission.owner")
        if not isinstance(owner, str):
            owner = None
        return limit, policy, generation, {"name": self._name(obj),
                                          "owner": owner}

    def _create_named(self, name: str, image: str, labels: dict,
                      *, timeout: float | None = None) -> bool:
        argv = ["create", "--name", name, "--entrypoint", "/bin/true"]
        if self.cgroup_parent:
            argv += ["--cgroup-parent", self.cgroup_parent]
        for key, value in sorted(labels.items()):
            argv += ["--label", f"{key}={value}"]
        argv.append(image)
        proc = self._call(*argv, check=False, timeout=timeout)
        if proc.returncode == 0:
            return True
        detail = (proc.stderr or proc.stdout or "").lower()
        if "already in use" in detail or "conflict" in detail \
                or "name" in detail and "exists" in detail:
            return False
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = lines[-1] if lines else "no detail"
        if self._looks_unavailable(message):
            raise AdmissionDockerUnavailable(
                f"docker create {name!r} failed ({proc.returncode}): {message}")
        raise AdmissionError(f"docker create {name!r} failed "
                             f"({proc.returncode}): "
                             f"{message}")

    def _create_next(self, prefix: str, image: str, labels_for,
                     number_for) -> tuple[str, int]:
        conflict_started = self.clock()
        while True:
            number = number_for()
            name = f"{prefix}{number}"
            labels = labels_for(name)
            if self._create_named(name, image, labels):
                return name, number
            # docker create --name is the host-wide compare-and-swap. The
            # competing object may not be visible on this first list yet.
            while self.clock() - conflict_started < self.conflict_wait:
                visible = any(self._name(obj) == name
                              for obj in self._containers(name=name))
                next_number = number_for()
                if visible:
                    if next_number <= number:
                        raise AdmissionError(
                            f"docker name conflict for {name!r}: the visible "
                            "object does not advance the allocator")
                    break
                if next_number == number:
                    self.sleep(min(self.poll_seconds,
                                   self.conflict_wait -
                                   (self.clock() - conflict_started)))
                    break
                break
            else:
                raise AdmissionError(f"docker name conflict for {name!r} "
                                     "did not become visible or free within "
                                     f"{self.conflict_wait:g}s")

    def publish(self, image: str, max_concurrent: int, *,
                owner_version: str, unreadable_policy: str = "unbudgeted",
                replace: bool = False) -> dict:
        if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) \
                or max_concurrent < 1:
            raise AdmissionError("--max-concurrent must be an integer >= 1")
        if unreadable_policy not in ("refuse", "unbudgeted"):
            raise AdmissionError("unreadable_policy must be 'refuse' or 'unbudgeted'")
        old = self._admission_objects()
        # Never remove an object merely because its name resembles ours. It
        # must carry the exact immutable generation and owner grammar first.
        for obj in old:
            labels = self._labels(obj)
            generation = parse_decimal(labels.get("ciu.admission.generation"),
                                       "ciu.admission.generation")
            try:
                owner_doc = json.loads(labels.get("ciu.admission.owner", ""))
            except (ValueError, TypeError, RecursionError):
                owner_doc = None
            if (generation is None or generation < 1
                    or self._name(obj) != f"{ADMISSION_PREFIX}{generation}"
                    or not self._valid_admission_owner(
                        owner_doc, labels.get("ciu.admission.owner"))):
                raise AdmissionError(f"refusing to replace unreadable admission "
                                     f"object {self._name(obj)!r}")
        if old and not replace:
            raise AdmissionError("a published admission object already exists; "
                                 "pass --replace to publish a new generation")
        now = int(self.clock())
        owner = compact_json({"ciu_version": owner_version,
                              "host": socket.gethostname().split(".", 1)[0],
                              "time": now,
                              "user": os.environ.get("USER") or str(os.getuid())})
        def highest() -> int:
            current, _obj = self._highest_admission()
            return current
        name, generation = self._create_next(
            ADMISSION_PREFIX, image,
            lambda name: {
                # The Docker name is the CAS. Derive the generation label
                # from the number that won that CAS, never from a second
                # independently observed listing.
                "ciu.admission.generation": str(
                    int(name.removeprefix(ADMISSION_PREFIX))),
                "ciu.admission.owner": owner,
                "ciu.admission.unreadable_policy": unreadable_policy,
                "ciu.admission.tiers.gates.max_concurrent":
                    str(max_concurrent),
            },
            lambda: highest() + 1)
        new_visible = [obj for obj in self._containers(name=name)
                       if self._name(obj) == name]
        if not new_visible:
            raise AdmissionError(f"published object {name!r} was created but "
                                 "is not visible to Docker inspect")
        fresh = self._admission_objects()
        valid = []
        for obj in fresh:
            labels = self._labels(obj)
            seen_generation = parse_decimal(
                labels.get("ciu.admission.generation"),
                "ciu.admission.generation")
            try:
                seen_owner = json.loads(labels.get("ciu.admission.owner", ""))
            except (json.JSONDecodeError, TypeError, RecursionError):
                seen_owner = None
            if (seen_generation is None or seen_generation < 1
                    or self._name(obj) !=
                    f"{ADMISSION_PREFIX}{seen_generation}"
                    or not self._valid_admission_owner(
                        seen_owner, labels.get("ciu.admission.owner"))):
                raise AdmissionError(f"refusing to replace unreadable admission "
                                     f"object {self._name(obj)!r}")
            valid.append((seen_generation, obj))
        active_generation = max((item[0] for item in valid), default=0)
        failures = []
        for seen_generation, obj in valid:
            if seen_generation >= active_generation:
                continue
            old_name = self._name(obj)
            try:
                self._call("rm", old_name)
            except AdmissionError as exc:
                failures.append(f"{old_name}: {exc}")
        if failures:
            raise AdmissionError("new admission generation is active, but "
                                 "older objects could not all be removed: "
                                 + "; ".join(failures))
        if active_generation > generation:
            raise AdmissionError(
                f"published generation {generation} was superseded by "
                f"generation {active_generation} during publication")
        return {"name": name, "generation": generation,
                "max_concurrent": max_concurrent,
                "unreadable_policy": unreadable_policy,
                "owner": owner}

    def show(self) -> dict | None:
        _generation, obj = self._highest_admission()
        if obj is None:
            return None
        labels = self._labels(obj)
        return {"name": self._name(obj),
                "generation": parse_decimal(labels.get(
                    "ciu.admission.generation"), "generation"),
                "max_concurrent": parse_decimal(labels.get(
                    "ciu.admission.tiers.gates.max_concurrent"), "limit"),
                "unreadable_policy": labels.get(
                    "ciu.admission.unreadable_policy"),
                "owner": labels.get("ciu.admission.owner"),
                "state": self._status(obj)}

    def _owner_liveness(self, owner: dict) -> str:
        """Return alive/dead only when this reader can prove it locally.

        A PID from another host boot or PID namespace is opaque here. In
        particular, a missing ``/proc/<pid>`` entry is not proof of death
        unless this reader shares the owner's boot and PID namespace.
        """
        try:
            root = self.proc_root
            boot = open(os.path.join(root, "sys/kernel/random/boot_id"),
                        encoding="ascii").read().strip()
            host = socket.gethostname().split(".", 1)[0]
            pid_ns = str(os.stat(os.path.join(root, "self/ns/pid")).st_ino)
        except (OSError, UnicodeError):
            return "unknown"
        if (owner.get("host"), owner.get("boot_id"), owner.get("pid_ns")) != \
                (host, boot, pid_ns):
            return "unknown"
        pid = owner.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            return "unknown"
        try:
            live_ns = str(os.stat(os.path.join(root, str(pid), "ns/pid")).st_ino)
            live_ticks = process_start_ticks(pid, root)
        except FileNotFoundError:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return "dead"
            except (PermissionError, OSError):
                return "unknown"
            return "unknown"
        except (PermissionError, OSError, UnicodeError, AdmissionError):
            return "unknown"
        return ("alive" if live_ns == owner["pid_ns"]
                and live_ticks == owner["start_ticks"] else "dead")

    def admission_snapshot(self) -> dict:
        """Read visible publication objects, preserving malformed entries."""
        admission_rows = []
        valid_admissions = []
        for obj in self._admission_objects():
            name = self._name(obj)
            labels = self._labels(obj)
            generation = parse_decimal(
                labels.get("ciu.admission.generation"),
                "ciu.admission.generation")
            raw_owner = labels.get("ciu.admission.owner")
            try:
                owner = json.loads(raw_owner) if isinstance(raw_owner, str) else None
            except (ValueError, TypeError, RecursionError):
                owner = None
            identity_valid = (generation is not None and generation >= 1
                              and name == f"{ADMISSION_PREFIX}{generation}"
                              and self._valid_admission_owner(owner, raw_owner))
            limit = parse_decimal(labels.get(
                "ciu.admission.tiers.gates.max_concurrent"), "limit")
            if limit is not None and limit < 1:
                limit = None
            policy = labels.get("ciu.admission.unreadable_policy")
            row = {"name": name, "generation": generation,
                   "docker_status": self._status(obj),
                   "max_concurrent": limit,
                   "max_concurrent_readable": limit is not None,
                   "unreadable_policy": policy if policy in
                   ("refuse", "unbudgeted") else None,
                   "owner": owner if identity_valid else None,
                   "identity_readable": bool(identity_valid)}
            if identity_valid:
                valid_admissions.append((generation, row))
            admission_rows.append(row)
        valid_admissions.sort(key=lambda item: item[0])
        published = valid_admissions[-1][1] if valid_admissions else None
        return {"published": published,
                "visible_admission_objects": admission_rows}

    def status_snapshot(self) -> dict:
        """Read the published cap and ticket queue without reaping or writing.

        Unlike ``acquire`` and ``reap``, this method is observational only:
        it calls Docker ``ps``/``inspect`` and reads labels and procfs, but
        never creates, starts, stops, or removes a container. Malformed
        objects remain visible and are not treated as free capacity.
        """
        admission = self.admission_snapshot()
        published = admission["published"]
        limit = published.get("max_concurrent") if published else None

        objects = [obj for obj in self._containers(name=TICKET_PREFIX)
                   if self._name(obj).startswith(TICKET_PREFIX)]
        raw_tickets = []
        for obj in objects:
            name = self._name(obj)
            labels = self._labels(obj)
            parsed = self._valid_ticket_labels(obj)
            try:
                number = self._ticket_number(name)
            except AdmissionError:
                number = None
            docker_status = self._status(obj)
            tombstone = docker_status in ("exited", "dead", "removing")
            marker = None
            if parsed is not None:
                marker_name = f"{RUN_PREFIX}{name}"
                markers = [member for member in self._group_objects(name)
                           if self._name(member) == marker_name]
                if len(markers) == 1:
                    marker_obj = markers[0]
                    marker_labels = self._valid_marker_labels(marker_obj,
                                                              parsed)
                    marker = {
                        "name": marker_name,
                        "docker_status": self._status(marker_obj),
                        "deadline": (marker_labels["deadline"]
                                     if marker_labels else None),
                        "readable": marker_labels is not None,
                    }
                elif len(markers) > 1:
                    marker = {"name": marker_name, "readable": False,
                              "error": "duplicate run markers"}
            liveness = (self._owner_liveness(parsed["owner"])
                        if parsed is not None else "unknown")
            raw_owner = labels.get("ciu.reservation.owner")
            try:
                decoded_owner = (json.loads(raw_owner)
                                 if isinstance(raw_owner, str) else None)
            except (ValueError, TypeError, RecursionError):
                decoded_owner = None
            raw_tickets.append({
                "name": name, "number": number,
                "docker_status": docker_status,
                "labels_readable": parsed is not None,
                "owner": parsed["owner"] if parsed is not None else decoded_owner,
                "owner_liveness": liveness,
                "deadline": parsed["deadline"] if parsed is not None else
                parse_decimal(labels.get("ciu.reservation.deadline"),
                              "deadline"),
                "marker": marker,
                "tombstone": tombstone,
                "counted_live": not tombstone,
                "_valid": parsed is not None,
            })

        live = sorted((ticket for ticket in raw_tickets
                       if ticket["counted_live"] and
                       ticket["number"] is not None),
                      key=lambda item: item["number"])
        for ticket in raw_tickets:
            marker = ticket["marker"]
            if ticket["tombstone"]:
                ticket["state"] = "tombstone" if ticket["_valid"] else \
                    "unreadable-tombstone"
            elif ticket["owner_liveness"] == "dead":
                ticket["state"] = "dead-owner"
            elif not ticket["_valid"] or (marker is not None
                                           and not marker.get("readable")):
                ticket["state"] = "unreadable-live"
            elif marker is not None:
                # A marker is written only after the ticket fits the current
                # published cap. A later cap reduction does not retroactively
                # turn an admitted run into a waiter.
                ticket["state"] = ("running" if
                                    ticket["owner_liveness"] == "alive"
                                    else "owner-unknown")
            elif limit is None:
                ticket["state"] = "queue-unknown"
            else:
                position = sum(1 for earlier in live
                               if earlier["number"] <= ticket["number"])
                ticket["state"] = ("admitting" if position <= limit
                                    else "queued")
            ticket.pop("_valid", None)

        return {**admission,
                "tickets": sorted(raw_tickets,
                                  key=lambda item: (item["number"] is None,
                                                    item["number"] or 0,
                                                    item["name"]))}

    @staticmethod
    def _ticket_number(name: str) -> int:
        match = re.fullmatch(r"ciu-res-gates-([1-9][0-9]*)", name)
        if not match:
            raise AdmissionError(f"malformed gate ticket name {name!r}")
        return int(match.group(1))

    def resume(self, prior_result: object) -> AdmissionTicket | None:
        """Re-use a persisted lane ticket when its inflight run is re-attached."""
        if not isinstance(prior_result, dict):
            return None
        name = prior_result.get("ticket")
        if not isinstance(name, str) or not re.fullmatch(
                r"ciu-res-gates-[1-9][0-9]*", name):
            return None
        tickets = [obj for obj in self._ticket_objects()
                   if self._name(obj) == name]
        if len(tickets) != 1:
            return None
        parsed = self._valid_ticket_labels(tickets[0])
        if parsed is None:
            self.notice(f"run-gate: admission WARNING: cannot resume "
                        f"malformed ticket {name!r}")
            return None
        marker_name = f"{RUN_PREFIX}{name}"
        markers = [obj for obj in self._group_objects(name)
                   if self._name(obj) == marker_name]
        if len(markers) != 1:
            return None
        marker = self._valid_marker_labels(markers[0], parsed)
        if marker is None:
            self.notice(f"run-gate: admission WARNING: cannot resume "
                        f"malformed marker {marker_name!r}")
            return None
        return AdmissionTicket(
            name=name, number=self._ticket_number(name), marker=marker_name,
            waited_s=(prior_result.get("waited_s", 0.0)
                      if isinstance(prior_result.get("waited_s", 0.0), (int, float))
                      else 0.0),
            override=parsed["override"], admitted_at=self.clock(),
            admitted_monotonic=self.monotonic(),
            run_deadline=marker["deadline"])

    def _owner_is_provably_dead(self, owner: dict) -> bool:
        """Only judge liveness inside the exact same host/boot/PID namespace."""
        try:
            root = self.proc_root
            boot = open(os.path.join(root, "sys/kernel/random/boot_id"),
                        encoding="ascii").read().strip()
            host = socket.gethostname().split(".", 1)[0]
            pid_ns = str(os.stat(os.path.join(root, "self/ns/pid")).st_ino)
        except OSError:
            return False
        if (owner["host"], owner["boot_id"], owner["pid_ns"]) != \
                (host, boot, pid_ns):
            return False
        pid = owner["pid"]
        try:
            live_ns = str(os.stat(os.path.join(root, str(pid), "ns/pid")).st_ino)
            live_ticks = process_start_ticks(pid, root)
        except FileNotFoundError:
            # procfs with hidepid=2 can report an inaccessible live pid as
            # ENOENT. Prove absence through the kernel process table; EPERM or
            # any other uncertainty remains live until the deadline.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except (PermissionError, OSError):
                return False
            return False
        except (PermissionError, OSError, AdmissionError):
            # An unreadable /proc entry is not evidence of death.
            return False
        return live_ns != owner["pid_ns"] or live_ticks != owner["start_ticks"]

    @staticmethod
    def _valid_ticket_labels(obj: dict) -> dict | None:
        name = DockerAdmission._name(obj)
        parsed = parse_reservation_labels(DockerAdmission._labels(obj))
        if (parsed is None or parsed["tier"] != "gates"
                or parsed["kind"] != "lane"
                or parsed["group"] != name
                or parsed["scheme"] != "ticket"):
            return None
        return parsed

    def _refuse_mixed_scheme(self) -> None:
        for obj in self._ticket_objects():
            parsed = parse_reservation_labels(self._labels(obj))
            if parsed is not None and parsed["scheme"] != "ticket":
                raise AdmissionError("reservations of scheme "
                                     f"{parsed['scheme']} found; run-gate uses "
                                     "ticket")

    @staticmethod
    def _valid_marker_labels(obj: dict, ticket: dict) -> dict | None:
        parsed = parse_reservation_labels(DockerAdmission._labels(obj))
        if (parsed is None or parsed["tier"] != "gates"
                or parsed["kind"] != "marker"
                or parsed["group"] != ticket["group"]
                or parsed["scheme"] != "ticket"
                or parsed["owner"] != ticket["owner"]):
            return None
        return parsed

    def _reap_old_tombstones(self) -> None:
        tickets = self._ticket_objects()
        highest = max((self._ticket_number(self._name(obj))
                       for obj in tickets), default=0)
        for obj in tickets:
            name = self._name(obj)
            if (self._ticket_number(name) < highest
                    and self._status(obj) in ("exited", "dead")
                    and self._valid_ticket_labels(obj) is not None):
                self._call("rm", name)

    def reap(self) -> None:
        """Release abandoned groups and obsolete tombstones, never collect runs."""
        self._refuse_mixed_scheme()
        tickets = sorted(self._ticket_objects(),
                         key=lambda obj: self._ticket_number(self._name(obj)))
        for obj in tickets:
            name = self._name(obj)
            parsed = self._valid_ticket_labels(obj)
            if parsed is None:
                self.notice(f"run-gate: admission WARNING: malformed ticket "
                            f"labels on {name!r}; counting it as live")
                continue
            marker_name = f"{RUN_PREFIX}{name}"
            group = self._group_objects(name)
            markers = [member for member in group
                       if self._name(member) == marker_name]
            if len(markers) > 1:
                self.notice(f"run-gate: admission WARNING: duplicate run "
                            f"markers for {name!r}; not reaping it")
                continue
            marker = markers[0] if markers else None
            marker_info = None
            if marker is not None:
                marker_info = self._valid_marker_labels(marker, parsed)
                if marker_info is None:
                    self.notice(f"run-gate: admission WARNING: malformed run "
                                f"marker {marker_name!r}; not reaping {name!r}")
                    continue
            now = int(self.clock())
            marker_expired = (marker_info is not None
                              and now >= marker_info["deadline"])
            wait_expired = (marker is None and now >= parsed["deadline"])
            owner_dead = self._owner_is_provably_dead(parsed["owner"])
            if not (marker_expired or wait_expired or owner_dead):
                continue

            members = [member for member in group
                       if self._name(member) not in (name, marker_name)]
            if marker_expired:
                stop_failed = False
                for member in members:
                    member_status = self._status(member)
                    if member_status in ("exited", "dead"):
                        continue
                    member_name = self._name(member)
                    if member_status not in ("running", "restarting", "paused"):
                        self.notice(f"run-gate: admission WARNING: expired group "
                                    f"member {member_name!r} is {member_status}; "
                                    f"keeping {name!r}")
                        stop_failed = True
                        continue
                    try:
                        self._call("stop", "--time", "5", member_name,
                                   timeout=15)
                    except AdmissionError as exc:
                        self.notice(f"run-gate: admission WARNING: cannot stop "
                                    f"expired group member {member_name!r}: {exc}")
                        stop_failed = True
                        continue
                    current = [entry for entry in self._containers(name=member_name)
                               if self._name(entry) == member_name]
                    if current and self._status(current[0]) not in ("exited", "dead"):
                        self.notice(f"run-gate: admission WARNING: expired group "
                                    f"member {member_name!r} is still "
                                    f"{self._status(current[0])}; keeping {name!r}")
                        stop_failed = True
                if stop_failed:
                    continue
            else:
                # Created, running, restarting and paused members all keep a
                # ticket unless its true run deadline has expired (handled
                # above by stopping the group). Never remove a lane member.
                holding = [member for member in members
                           if self._status(member) not in ("exited", "dead")]
                if holding:
                    continue
            if marker is not None:
                try:
                    self._call("rm", marker_name)
                except AdmissionError as exc:
                    self.notice(f"run-gate: admission WARNING: cannot remove "
                                f"expired marker {marker_name!r}: {exc}")
                    continue
            self.notice(f"run-gate: admission reaped abandoned ticket {name} "
                        f"(owner {parsed['owner']['pid']})")
            try:
                self.release_name(name, self._ticket_number(name))
            except AdmissionError as exc:
                self.notice(f"run-gate: admission WARNING: cannot release "
                            f"abandoned ticket {name!r}: {exc}")
        self._reap_old_tombstones()

    def acquire(self, *, lane: str, run_id: str, image: str,
                wait_seconds: int, unreadable_policy: str = "unbudgeted",
                budget_seconds: int | None = None, override: bool = False,
                owner: dict | None = None) -> AdmissionTicket | None:
        if wait_seconds < 0:
            raise AdmissionError("admission wait must be non-negative")
        limit, published_policy, _generation, published = self.read_limit()
        policy = (unreadable_policy if published_policy == "unreadable"
                  else published_policy)
        if limit is None:
            self.notice("run-gate: admission limit is unreadable because no "
                        "valid gates limit is published; run `run-gate "
                        "admission set`")
            if policy == "refuse" and not override:
                raise AdmissionRefused("no gates limit is published and "
                                       "unreadable_policy=refuse")
            if override:
                self.notice("run-gate: --override-admission has no published "
                            "limit to override; continuing without a ticket")
            return None
        self.reap()
        owner = owner or self.owner_provider(lane, run_id)
        if validate_owner(compact_json(owner)) is None:
            raise AdmissionError("gate owner tuple does not match the shared label grammar")
        created = int(self.clock())
        short_deadline = created + wait_seconds + TICKET_GRACE_SECONDS
        def ticket_labels(name: str) -> dict:
            labels = {
                "ciu.reservation.deadline": str(short_deadline),
                "ciu.reservation.group": name,
                "ciu.reservation.kind": "lane",
                "ciu.reservation.owner": compact_json(owner),
                "ciu.reservation.scheme": "ticket",
                "ciu.reservation.tier": "gates",
            }
            if override:
                labels["ciu.reservation.override"] = "true"
            return labels
        ticket_name, number = self._create_next(
            TICKET_PREFIX, image, ticket_labels,
            lambda: max((self._ticket_number(self._name(obj))
                         for obj in self._ticket_objects()), default=0) + 1)
        started = self.clock()
        waited = 0.0
        try:
            while True:
                self.reap()
                limit, published_policy, _generation, published = self.read_limit()
                if limit is None:
                    policy = (unreadable_policy if published_policy == "unreadable"
                              else published_policy)
                    self.notice("run-gate: admission limit became unreadable; "
                                "releasing its ticket and applying " + policy)
                    self.release_name(ticket_name, number)
                    if policy == "refuse" and not override:
                        raise AdmissionRefused("no gates limit is published and "
                                               "unreadable_policy=refuse")
                    return None
                live = 0
                for obj in self._ticket_objects():
                    other_name = self._name(obj)
                    other_number = self._ticket_number(other_name)
                    if other_number > number:
                        continue
                    state = self._status(obj)
                    labels = self._labels(obj)
                    parsed = parse_reservation_labels(labels)
                    if parsed is None:
                        self.notice(f"run-gate: admission WARNING: malformed "
                                    f"ticket labels on {other_name!r}; counting "
                                    "it as live")
                        live += 1
                    elif state not in ("exited", "dead", "removing"):
                        live += 1
                if live <= limit or override:
                    break
                waited = max(0.0, self.clock() - started)
                self.notice(f"run-gate: waiting for a gate slot: "
                            f"{live - 1} of {limit} in use; ticket {ticket_name}")
                if waited >= wait_seconds:
                    raise AdmissionRefused(
                        f"gate ticket {ticket_name} waited {waited:.1f}s; "
                        f"{live - 1} of {limit} slots remain in use")
                self.sleep(min(self.poll_seconds, wait_seconds - waited))
            waited = max(0.0, self.clock() - started)
            admitted_at = self.clock()
            admitted_monotonic = self.monotonic()
            true_deadline = int(admitted_at) + (
                budget_seconds if budget_seconds is not None
                else DEFAULT_LANE_DEADLINE_SECONDS) + MARKER_GRACE_SECONDS
            marker_name = f"{RUN_PREFIX}{ticket_name}"
            marker_labels = {
                "ciu.reservation.deadline": str(true_deadline),
                "ciu.reservation.group": ticket_name,
                "ciu.reservation.kind": "marker",
                "ciu.reservation.owner": compact_json(owner),
                "ciu.reservation.scheme": "ticket",
                "ciu.reservation.tier": "gates",
            }
            if not self._create_named(marker_name, image, marker_labels):
                raise AdmissionError(f"run marker {marker_name!r} already exists")
            return AdmissionTicket(ticket_name, number, marker_name, waited,
                                   override, admitted_at, admitted_monotonic,
                                   true_deadline)
        except BaseException:
            try:
                self.release_name(ticket_name, number)
            except AdmissionError as exc:
                self.notice(f"run-gate: admission WARNING: ticket "
                            f"{ticket_name!r} could not be released while "
                            f"unwinding acquisition: {exc}")
            raise

    def release_name(self, name: str, number: int | None = None) -> None:
        number = self._ticket_number(name) if number is None else number
        visible = self._ticket_objects()
        matches = [obj for obj in visible if self._name(obj) == name]
        if not matches:
            # A janitor may have released this ticket while its old owner was
            # waking up. Release is intentionally idempotent.
            return
        obj = matches[0]
        if self._valid_ticket_labels(obj) is None:
            raise AdmissionError(f"refusing to release ticket {name!r}: its "
                                 "labels do not match the shared grammar")
        members = [member for member in self._group_objects(name)
                   if self._name(member) not in
                   (name, f"{RUN_PREFIX}{name}")]
        holding = [member for member in members
                   if self._status(member) not in ("exited", "dead")]
        if holding:
            names = ", ".join(sorted(self._name(member) for member in holding))
            raise AdmissionError(f"cannot release {name!r} while its group "
                                 f"still has live members: {names}")
        if self._status(obj) in ("exited", "dead"):
            highest_existing = max((self._ticket_number(self._name(item))
                                    for item in visible), default=number)
            if number < highest_existing:
                self._call("rm", name)
            return
        highest = max((self._ticket_number(self._name(obj))
                       for obj in visible), default=number)
        if number < highest:
            self._call("rm", name)
            return
        state = self._status(obj)
        if state != "created":
            raise AdmissionError(f"refusing to start ticket {name!r} in "
                                 f"unexpected state {state!r}")
        # Keeping the highest visible number as an exited object is the
        # monotone allocator's tombstone; it cannot be reused by a future gate.
        self._call("start", name)
        self._call("wait", name, timeout=max(5.0, self.poll_seconds * 3))
        inspected = [obj for obj in self._containers(name=name)
                     if self._name(obj) == name]
        if not inspected or self._status(inspected[0]) not in ("exited", "dead"):
            raise AdmissionError(f"ticket {name!r} did not become an exited tombstone")

    def release(self, ticket: AdmissionTicket) -> None:
        members = [obj for obj in self._group_objects(ticket.name)
                   if self._name(obj) not in (ticket.name, ticket.marker)]
        holding = [obj for obj in members
                   if self._status(obj) in ("created", "running",
                                            "restarting", "paused")]
        if holding:
            names = ", ".join(sorted(self._name(obj) for obj in holding))
            raise AdmissionError(f"cannot release {ticket.name!r} while its "
                                 f"group still has live members: {names}")
        marker = [obj for obj in self._containers(name=ticket.marker)
                  if self._name(obj) == ticket.marker]
        if marker:
            if self._valid_marker_labels(marker[0], {
                    "owner": validate_owner(self._labels(marker[0]).get(
                        "ciu.reservation.owner")),
                    "group": ticket.name}) is None:
                raise AdmissionError(f"refusing to remove malformed run marker "
                                     f"{ticket.marker!r}")
            self._call("rm", ticket.marker)
        self.release_name(ticket.name, ticket.number)


def local_docker_endpoint(run: Callable = subprocess.run,
                          environ: dict | None = None) -> tuple[bool, str]:
    """Whether the selected Docker endpoint is provably local for publication."""
    environ = os.environ if environ is None else environ
    docker_host = environ.get("DOCKER_HOST", "")
    if docker_host and not docker_host.startswith("unix://"):
        return False, f"DOCKER_HOST={docker_host!r} is not a local Unix socket"
    context = environ.get("DOCKER_CONTEXT")
    if not context:
        try:
            current = run(["docker", "context", "show"], capture_output=True,
                          text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot determine active Docker context: {exc}"
        if current.returncode:
            return False, "cannot determine active Docker context"
        context = current.stdout.strip()
    try:
        proc = run(["docker", "context", "inspect", context,
                    "--format", "{{(index .Endpoints \"docker\").Host}}"],
                   capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot inspect Docker context {context!r}: {exc}"
    endpoint = proc.stdout.strip()
    if proc.returncode or not endpoint.startswith("unix://"):
        return False, f"Docker context {context!r} does not prove a local Unix endpoint"
    return True, "local Docker Unix endpoint"
