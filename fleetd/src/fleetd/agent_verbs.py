"""Host filesystem verbs for the fleetd agent protocol."""

from __future__ import annotations

import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import re
import select
import socket
import stat
import tempfile
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .admission import AdmissionError, ExitCode, InspectorAnswer
from .agent_admission import AgentAdmission, AgentExecutor
from .agent_policy import AgentIdentity, AgentPolicy
from .agent_protocol import (
    MAX_FETCH_BYTES,
    FrameError,
    _write_all,
    read_frame,
    validate_relative_path,
    validate_request,
    write_frame,
)
from .contracts import ContractError, HostFacts, validate_host_facts
from .hostside import open_beneath, resolve_assay_lane

AGENT_SCHEMA = "fleet-host-agent/1"
MAX_ASSAY_PROGRESS_COUNT_BYTES = 8 * 1024 * 1024
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_UPLOAD_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_UPLOAD_GROUP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SEED_UPLOAD_NAME_RE = re.compile(r"assay-seed-[0-9]+\.tgz\Z")
_SEED_CHECKSUM_NAME_RE = re.compile(r"assay-seed-[0-9]+\.tgz\.sha256\Z")
_UPLOAD_OBJECT_NAMES = frozenset({"in.bundle", "candidates", "dev.tracked.tar", "dev.untracked.tar"})
_MAX_UPLOAD_RECORDS = 65536
_MAX_UPLOAD_RECORD_BYTES = 64 * 1024
_SEMVER_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_CAPABILITIES: dict[str, list[str]] = {
    "probe": ["verb"],
    "facts": ["verb"],
    "have": ["verb", "repo"],
    "put": ["verb", "name", "sha256", "bytes"],
    "submit": [
        "verb", "job_id", "repo", "sha", "base", "project", "lane", "shard",
        "resources", "priority", "requested_by", "stores", "extra_argv", "env", "dev", "kind",
    ],
    "status": ["verb", "job_id"],
    "progress": ["verb", "job_id", "offset"],
    "cancel": ["verb", "job_id", "reason"],
    "fetch": ["verb", "job_id", "artifact", "offset", "length"],
    "lease": ["verb", "lease_id", "container_name", "footprint", "planned_cores", "priority", "container_id"],
}


@contextmanager
def _upload_directory(parent_fd: int, name: str, what: str) -> Iterator[int]:
    """Open one real child directory below a trusted descriptor, failing closed."""

    try:
        fd = open_beneath(parent_fd, name, directory=True)
    except (OSError, ValueError) as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} {name!r} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} {name!r} is not a real directory")
        yield fd
    finally:
        os.close(fd)


def _open_optional_upload_directory(parent_fd: int, name: str, what: str) -> int | None:
    """Open a real child directory, treating only an absent directory as empty."""

    try:
        fd = open_beneath(parent_fd, name, directory=True)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} {name!r} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} {name!r} is not a real directory")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _upload_directory_entries(directory_fd: int, what: str) -> list[str]:
    """List a trusted directory without a symlink or an unbounded entry count."""

    try:
        with os.scandir(directory_fd) as entries:
            names = []
            for entry in entries:
                if len(names) >= _MAX_UPLOAD_RECORDS:
                    raise AdmissionError(
                        ExitCode.RUNNER_FAILURE,
                        f"cannot collect upload objects: {what} exceed the {_MAX_UPLOAD_RECORDS}-entry limit",
                    )
                names.append(entry.name)
    except OSError as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} cannot be listed: {exc}") from exc
    return names


def _read_upload_record(directory_fd: int, name: str, what: str) -> bytes:
    """Read one bounded regular record without following a symlink or blocking."""

    try:
        fd = open_beneath(directory_fd, name)
    except (OSError, ValueError) as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} is not a regular file")
        raw = os.read(fd, _MAX_UPLOAD_RECORD_BYTES + 1)
    except OSError as exc:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot collect upload objects: {what} is unreadable: {exc}") from exc
    finally:
        os.close(fd)
    if len(raw) > _MAX_UPLOAD_RECORD_BYTES:
        raise AdmissionError(
            ExitCode.RUNNER_FAILURE,
            f"cannot collect upload objects: {what} exceeds {_MAX_UPLOAD_RECORD_BYTES} bytes",
        )
    return raw


def _known_upload_name(name: str) -> bool:
    """The closed upload-name set ``put`` may have committed."""

    return (
        name in _UPLOAD_OBJECT_NAMES
        or _SEED_UPLOAD_NAME_RE.fullmatch(name) is not None
        or _SEED_CHECKSUM_NAME_RE.fullmatch(name) is not None
    )


def _record_descriptor_digest(value: object, where: str, name: str) -> str:
    """Read the content address out of a validated file-descriptor record."""

    if not isinstance(value, Mapping) or not isinstance(value.get("sha256"), str) or _UPLOAD_DIGEST_RE.fullmatch(str(value["sha256"])) is None:
        raise AdmissionError(
            ExitCode.RUNNER_FAILURE,
            f"cannot collect upload objects: job request record {name!r} has an invalid {where} digest",
        )
    return str(value["sha256"])


@dataclass(frozen=True)
class _UploadObjectRef:
    """One committed store object, as the collector must see it."""

    group: str
    name: str
    digest: str
    size: int
    modified: float

    @property
    def seed_stem(self) -> str | None:
        """The seed-archive stem this object belongs to, or ``None``."""

        stem = self.name.removesuffix(".sha256")
        return stem if _SEED_UPLOAD_NAME_RE.fullmatch(stem) is not None else None

    @property
    def key(self) -> tuple[str, str]:
        """The store location ``(group, name)`` this object was committed under."""

        return (self.group, self.name)


def _referenced_seed_sidecars(
    references: Mapping[str, frozenset[str]],
    objects: tuple[_UploadObjectRef, ...],
) -> frozenset[tuple[str, str]]:
    """Sidecars whose seed archive a durable record names.

    A job record names only the seed archive's digest, while the store keeps the
    archive at ``uploads/<group>/assay-seed-<n>.tgz/<digest>`` and its checksum
    at ``uploads/<group>/assay-seed-<n>.tgz.sha256/<checksum digest>``.  When the
    archive is referenced, its sidecar is the same input and is kept with it; a
    sidecar with no referenced archive is not covered by any record.
    """

    referenced_archives = {
        (item.group, item.name) for item in objects
        if item.seed_stem == item.name and item.digest in references.get(item.group, frozenset())
    }
    return frozenset((group, stem + ".sha256") for group, stem in referenced_archives)


def _is_referenced_upload_object(
    item: _UploadObjectRef,
    references: Mapping[str, frozenset[str]],
    seed_sidecars: frozenset[tuple[str, str]],
) -> bool:
    """A store object survives only while a durable record names it."""

    return item.digest in references.get(item.group, frozenset()) or item.key in seed_sidecars


@dataclass(frozen=True)
class AgentPaths:
    """Private state layout owned by the agent process."""

    state_dir: Path
    jobs_dir: Path
    uploads_dir: Path
    incoming_dir: Path

    @classmethod
    def initialize(cls, state_dir: Path) -> AgentPaths:
        root = Path(state_dir)
        if root.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "agent state directory must not be a symlink")
        for path in (root, root / "jobs", root / "uploads", root / ".incoming"):
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot initialize agent state directory: {exc}") from exc
            if path.is_symlink() or not path.is_dir():
                raise AdmissionError(ExitCode.REFUSAL, f"agent state path {path} is not a real directory")
            try:
                if stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise AdmissionError(ExitCode.REFUSAL, f"agent state path {path} is accessible by other users")
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect agent state path {path}: {exc}") from exc
        return cls(root, root / "jobs", root / "uploads", root / ".incoming")

    def candidate_paths(self, group: str) -> frozenset[str]:
        if self.uploads_dir.is_symlink() or not self.uploads_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, "agent upload directory is not a real directory")
        group_dir = self.uploads_dir / group
        if group_dir.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "authenticated upload group directory is a symlink")
        if not group_dir.is_dir():
            return frozenset()
        directory = group_dir / "candidates"
        if not directory.is_dir() or directory.is_symlink():
            return frozenset()
        paths: set[str] = set()
        try:
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file() or _COMMIT_RE.fullmatch(path.name) is None:
                    continue
                paths.add(str(path.resolve(strict=True)))
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect host-created candidates: {exc}") from exc
        return frozenset(paths)

    def candidate_root(self, group: str) -> Path:
        """The absolute path a committed candidates object is reported under."""

        return self.state_dir.resolve(strict=False) / "uploads" / group / "candidates"


@dataclass
class AgentReply:
    document: dict[str, object]
    body: bytes = b""
    body_fd: int | None = None
    body_length: int = 0

    def close(self) -> None:
        if self.body_fd is not None:
            fd, self.body_fd = self.body_fd, None
            os.close(fd)


@dataclass
class _UploadReservation:
    fd: int
    owner_path: Path
    path: Path
    temporary_record_path: Path
    temporary_fd: int
    temporary_name: str
    expected: int


class AgentVerbService:
    """Serve read, upload and artifact verbs without opening the admission book."""

    def __init__(
        self,
        state_dir: Path,
        policy: AgentPolicy,
        facts_provider: Callable[[], Mapping[str, object] | HostFacts],
        *,
        agent_version: str,
        admission: AgentAdmission | None = None,
        executor: AgentExecutor | None = None,
        legacy_jobs_provider: Callable[[], Iterable[str]] | None = None,
        inspector: Callable[[str], InspectorAnswer] | None = None,
        container_inspector: Callable[[str], Mapping[str, object] | None] | None = None,
        monotonic: Callable[[], float] | None = None,
        lease_frame_waiter: Callable[..., tuple[list[int], list[int], list[int]]] | None = None,
    ) -> None:
        if not isinstance(agent_version, str) or _SEMVER_RE.fullmatch(agent_version) is None:
            raise AdmissionError(ExitCode.REFUSAL, "host agent version must be SemVer")
        self.paths = AgentPaths.initialize(state_dir)
        self.policy = policy
        self.facts_provider = facts_provider
        self.agent_version = agent_version
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._lease_frame_waiter = lease_frame_waiter
        self.admission = admission or AgentAdmission(
            state_dir, policy, facts_provider,
            legacy_jobs_provider=legacy_jobs_provider, inspector=inspector,
            container_inspector=container_inspector,
        )
        self.executor = executor
        self.admission.reconcile_leases()

    def reconcile_leases(self) -> tuple[Mapping[str, object], ...]:
        """Reconcile exact lease identities when the configured inspector is available."""

        return self.admission.reconcile_leases()

    def _envelope(self, **fields: object) -> dict[str, object]:
        if "error" in fields:
            raise ValueError("success response cannot contain the reserved error field")
        return {"schema": AGENT_SCHEMA, "agent_version": self.agent_version, **fields}

    def _capabilities(self) -> dict[str, list[str]]:
        capabilities = {verb: list(fields) for verb, fields in _CAPABILITIES.items()}
        if (
            self.admission.inspector is None
            or self.admission.legacy_jobs_provider is None
            or self.admission.container_inspector is None
        ):
            capabilities.pop("lease")
        return capabilities

    def _facts(self) -> dict[str, Any]:
        try:
            raw = self.facts_provider()
            validated = raw if isinstance(raw, HostFacts) else validate_host_facts(raw)
        except (ContractError, OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"host facts unavailable: {exc}") from exc
        facts = validated.to_dict()
        if facts.get("schema") != "host-facts/2":
            raise AdmissionError(ExitCode.REFUSAL, "host agent requires host-facts/2")
        return facts

    def _max_upload_bytes(self) -> int:
        fact = self._facts()["policy"]["max_upload_bytes"]
        if fact["v"] is None:
            raise AdmissionError(ExitCode.REFUSAL, f"policy.max_upload_bytes unavailable: {fact['reason']}")
        return int(fact["v"])

    def _put_limits(self) -> tuple[int, int, int]:
        policy = self._facts()["policy"]
        upload = policy["max_upload_bytes"]
        if upload["v"] is None:
            raise AdmissionError(ExitCode.REFUSAL, f"policy.max_upload_bytes unavailable: {upload['reason']}")
        store = policy["max_upload_store_bytes"]
        if store["v"] is None:
            raise AdmissionError(
                ExitCode.REFUSAL,
                f"policy.max_upload_store_bytes unavailable: {store['reason']}",
            )
        floors = policy["disk_floors"]
        if floors["v"] is None:
            raise AdmissionError(ExitCode.REFUSAL, f"policy.disk_floors unavailable: {floors['reason']}")
        if not isinstance(floors["v"], Mapping) or not floors["v"]:
            raise AdmissionError(ExitCode.REFUSAL, "policy.disk_floors has no configured floors")
        return (
            int(upload["v"]), int(store["v"]),
            max(int(value) for value in floors["v"].values()),
        )

    @contextmanager
    def _upload_store_lock(self) -> Iterator[None]:
        lock_path = self.paths.state_dir / ".upload-store.lock"
        try:
            fd = os.open(
                lock_path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot lock upload store accounting: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise AdmissionError(ExitCode.REFUSAL, "upload store lock is not a private regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
        except AdmissionError:
            os.close(fd)
            raise
        except OSError as exc:
            os.close(fd)
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot lock upload store accounting: {exc}") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _write_upload_reservation(
        fd: int, expected: int, remaining: int, temporary_name: str,
    ) -> None:
        record = json.dumps({
            "schema": "fleet-upload-reservation/1",
            "expected": expected,
            "remaining": remaining,
            "temporary": temporary_name,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(record)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("upload reservation write made no progress")
            view = view[count:]

    def _read_upload_reservation(self, fd: int, token: str) -> tuple[int, int, str]:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload reservation is not a bounded regular file")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4097)
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"upload reservation is unreadable: {exc}") from exc
        temporary = f"upload-part-{token}"
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "expected", "remaining", "temporary"}
            or record["schema"] != "fleet-upload-reservation/1"
            or type(record["expected"]) is not int
            or record["expected"] < 0
            or type(record["remaining"]) is not int
            or not 0 <= record["remaining"] <= record["expected"]
            or record["temporary"] != temporary
        ):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload reservation has an invalid closed shape")
        return record["expected"], record["remaining"], temporary

    def _remove_upload_partial(self, temporary_name: str) -> None:
        path = self.paths.incoming_dir / temporary_name
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"upload partial {temporary_name!r} is not a regular file")
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def _remove_upload_metadata(self, name: str) -> None:
        path = self.paths.incoming_dir / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"upload reservation artifact {name!r} is not a regular file")
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def _remove_upload_owner(self, owner_path: Path, fd: int) -> None:
        try:
            current = owner_path.lstat()
        except FileNotFoundError:
            return
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"upload owner lock {owner_path.name!r} changed before cleanup; inspect the incoming directory and active upload processes",
            )
        try:
            os.unlink(owner_path)
        except FileNotFoundError:
            return
        self._fsync_directory(self.paths.incoming_dir)

    @staticmethod
    def _upload_recovery_refusal(filename: str, reason: str) -> AdmissionError:
        return AdmissionError(
            ExitCode.RUNNER_FAILURE,
            f"cannot safely recover upload artifact {filename!r}: {reason}; check the named files in the agent incoming directory and repair or remove them only after confirming no upload writer is active",
        )

    def _recover_upload_reservations_locked(self) -> tuple[int, int, set[str]]:
        expected_total = 0
        reservation_total = 0
        live_partials: set[str] = set()
        try:
            entries = list(os.scandir(self.paths.incoming_dir))
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect upload reservations: {exc}") from exc
        groups: dict[str, dict[str, str]] = {}
        for entry in entries:
            name = entry.name
            kind = ""
            token = ""
            if name.startswith(".upload-reservation-"):
                if name.endswith(".json"):
                    kind = "record"
                    token = name[len(".upload-reservation-"):-len(".json")]
                elif name.endswith(".tmp"):
                    kind = "temporary_record"
                    token = name[len(".upload-reservation-"):-len(".tmp")]
                else:
                    raise self._upload_recovery_refusal(name, "reservation filename is malformed; inspect its suffix and token")
            elif name.startswith(".upload-owner-"):
                if not name.endswith(".lock"):
                    raise self._upload_recovery_refusal(name, "owner-lock filename is malformed; inspect its suffix and token")
                kind = "owner"
                token = name[len(".upload-owner-"):-len(".lock")]
            elif name.startswith("upload-part-"):
                kind = "partial"
                token = name[len("upload-part-"):]
            else:
                raise self._upload_recovery_refusal(name, "unknown incoming upload artifact; inspect the incoming directory")
            if not token or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
                raise self._upload_recovery_refusal(name, "filename token is malformed; check the incoming directory for unexpected names")
            group = groups.setdefault(token, {})
            if kind in group:
                raise self._upload_recovery_refusal(name, "duplicate artifact kind for one token; inspect the incoming directory")
            group[kind] = name

        for token, artifacts in groups.items():
            record_name = f".upload-reservation-{token}.json"
            temporary_record_name = f".upload-reservation-{token}.tmp"
            owner_name = f".upload-owner-{token}.lock"
            partial_name = f"upload-part-{token}"
            if "owner" not in artifacts:
                if "record" in artifacts or "temporary_record" in artifacts:
                    offender = artifacts.get("record", artifacts.get("temporary_record", owner_name))
                    raise self._upload_recovery_refusal(
                        offender,
                        f"owner lock {owner_name!r} is missing, so owner absence cannot be established; check whether an upload process is active and inspect the owner-lock path",
                    )
                continue

            owner_path = self.paths.incoming_dir / owner_name
            try:
                fd = os.open(owner_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            except OSError as exc:
                raise self._upload_recovery_refusal(
                    owner_name,
                    f"cannot establish owner absence ({exc}); check owner-lock permissions and whether an upload process is active",
                ) from exc
            locked = False
            try:
                try:
                    info = os.fstat(fd)
                except OSError as exc:
                    raise self._upload_recovery_refusal(
                        owner_name,
                        f"cannot establish owner absence because the lock cannot be inspected ({exc}); check owner-lock permissions and active upload processes",
                    ) from exc
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                    raise self._upload_recovery_refusal(
                        owner_name,
                        "owner lock is not a private regular file; check its type and permissions",
                    )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    if "temporary_record" in artifacts:
                        raise self._upload_recovery_refusal(
                            temporary_record_name,
                            f"owner lock {owner_name!r} is held while an atomic record temporary remains; inspect the active uploader",
                        )
                    if "record" not in artifacts:
                        raise self._upload_recovery_refusal(
                            owner_name,
                            "owner lock is held without its durable reservation record; inspect the active uploader and incoming directory",
                        )
                    record_path = self.paths.incoming_dir / record_name
                    try:
                        # O_NONBLOCK: a FIFO at the record name must reach the type check, not wait for a writer.
                        record_fd = os.open(
                            record_path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                        )
                    except OSError as exc:
                        raise self._upload_recovery_refusal(
                            record_name,
                            f"live reservation cannot be read ({exc}); inspect the active uploader and record permissions",
                        ) from exc
                    try:
                        expected, _remaining, temporary = self._read_upload_reservation(record_fd, token)
                    except (AdmissionError, OSError) as exc:
                        raise self._upload_recovery_refusal(
                            record_name,
                            f"live reservation is invalid ({exc}); inspect the active uploader and record",
                        ) from exc
                    finally:
                        os.close(record_fd)
                    try:
                        partial_info = (self.paths.incoming_dir / temporary).lstat()
                    except OSError as exc:
                        raise self._upload_recovery_refusal(
                            temporary,
                            f"live reservation partial cannot be inspected ({exc}); inspect the active uploader and partial",
                        ) from exc
                    if not stat.S_ISREG(partial_info.st_mode):
                        raise self._upload_recovery_refusal(
                            temporary,
                            "live reservation partial is not a regular file; inspect the active uploader and partial",
                        )
                    expected_total += expected
                    reservation_total += expected
                    live_partials.add(temporary)
                    continue
                except OSError as exc:
                    raise self._upload_recovery_refusal(
                        owner_name,
                        f"cannot establish owner absence ({exc}); check owner-lock permissions and whether an upload process is active",
                    ) from exc
                for metadata_name in (record_name, temporary_record_name):
                    try:
                        self._remove_upload_metadata(metadata_name)
                    except AdmissionError as exc:
                        raise self._upload_recovery_refusal(metadata_name, str(exc)) from exc
                    except OSError as exc:
                        raise self._upload_recovery_refusal(
                            metadata_name,
                            f"reservation artifact cleanup failed ({exc}); check the incoming directory",
                        ) from exc
                try:
                    self._remove_upload_partial(partial_name)
                except AdmissionError as exc:
                    raise self._upload_recovery_refusal(partial_name, str(exc)) from exc
                except OSError as exc:
                    raise self._upload_recovery_refusal(
                        partial_name,
                        f"partial cleanup failed ({exc}); check the incoming directory",
                    ) from exc
                self._fsync_directory(self.paths.incoming_dir)
                try:
                    current = owner_path.lstat()
                except OSError as exc:
                    raise self._upload_recovery_refusal(
                        owner_name,
                        f"owner lock changed before cleanup ({exc}); inspect the incoming directory",
                    ) from exc
                if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise self._upload_recovery_refusal(
                        owner_name,
                        "owner lock changed before cleanup; inspect the incoming directory and active upload processes",
                    )
                os.unlink(owner_path)
                self._fsync_directory(self.paths.incoming_dir)
            except OSError as exc:
                raise self._upload_recovery_refusal(
                    owner_name,
                    f"cleanup or owner-state inspection failed ({exc}); check the incoming directory and active upload processes",
                ) from exc
            finally:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        try:
            entries = list(os.scandir(self.paths.incoming_dir))
            for entry in entries:
                if entry.name.startswith("upload-part-") and entry.name not in live_partials:
                    try:
                        self._remove_upload_partial(entry.name)
                    except AdmissionError as exc:
                        raise self._upload_recovery_refusal(entry.name, str(exc)) from exc
                    except OSError as exc:
                        raise self._upload_recovery_refusal(
                            entry.name,
                            f"partial cleanup failed ({exc}); check the incoming directory",
                        ) from exc
        except AdmissionError:
            raise
        except OSError as exc:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot recover orphaned upload partials: {exc}",
            ) from exc
        return expected_total, reservation_total, live_partials

    def _state_directory_fd(self) -> int:
        """Open the private state directory itself, never through a symlink."""

        try:
            fd = os.open(self.paths.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect the agent state directory: {exc}") from exc
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise AdmissionError(ExitCode.REFUSAL, "agent state directory is not a real directory")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _referenced_upload_objects_locked(self) -> dict[str, frozenset[str]]:
        """Digests named by durable host records, keyed by authenticated group.

        The only durable records that name an upload object are the job request
        records written before admission and retained after completion.  They
        carry the seed digest, the dev payload digests and the host-created
        candidates path.  Anything unreadable, malformed or of an unknown shape
        stops the caller rather than guessing a reference.
        """

        references: dict[str, set[str]] = {}
        state_fd = self._state_directory_fd()
        try:
            jobs_fd = _open_optional_upload_directory(state_fd, "jobs", "agent job records")
            if jobs_fd is None:
                return {}
            try:
                for name in _upload_directory_entries(jobs_fd, "agent job records"):
                    with _upload_directory(jobs_fd, name, "agent job record directory") as job_fd:
                        group, digests = self._job_record_references(job_fd, name)
                        references.setdefault(group, set()).update(digests)
            finally:
                os.close(jobs_fd)
        finally:
            os.close(state_fd)
        return {group: frozenset(digests) for group, digests in references.items()}

    def _job_record_references(self, job_fd: int, name: str) -> tuple[str, set[str]]:
        raw = _read_upload_record(job_fd, "request.json", f"job request record {name!r}")
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot collect upload objects: job request record {name!r} is unreadable: {exc}",
            ) from exc
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "group", "digest", "request"}
            or record.get("schema") != "fleet-host-job-request/1"
            or not isinstance(record.get("group"), str)
            or not record["group"]
            or not isinstance(record.get("request"), dict)
        ):
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot collect upload objects: job request record {name!r} has an unknown shape",
            )
        return str(record["group"]), self._request_upload_digests(record["request"], str(record["group"]), name)

    def _request_upload_digests(self, request: Mapping[str, Any], group: str, name: str) -> set[str]:
        digests: set[str] = set()
        stores = request.get("stores")
        if stores is not None and not isinstance(stores, Mapping):
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot collect upload objects: job request record {name!r} has an unknown stores shape",
            )
        seed = stores.get("seed") if isinstance(stores, Mapping) else None
        if seed is not None:
            digests.add(_record_descriptor_digest(seed, "stores.seed", name))
        dev = request.get("dev")
        if dev is not None:
            if not isinstance(dev, Mapping):
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE,
                    f"cannot collect upload objects: job request record {name!r} has an unknown dev shape",
                )
            for key in ("tracked", "untracked"):
                digests.add(_record_descriptor_digest(dev.get(key), f"dev.{key}", name))
        argv = request.get("extra_argv")
        if isinstance(argv, list):
            for index, item in enumerate(argv):
                if item != "--only-candidates":
                    continue
                digests.add(self._candidate_reference(argv[index + 1] if index + 1 < len(argv) else None, group, name))
        return digests

    def _candidate_reference(self, value: object, group: str, name: str) -> str:
        if not isinstance(value, str) or not value or _UPLOAD_DIGEST_RE.fullmatch(Path(value).name) is None:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot collect upload objects: job request record {name!r} has an invalid candidates reference",
            )
        if not Path(value).is_absolute() or Path(value).parent != self.paths.candidate_root(group):
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot collect upload objects: job request record {name!r} names candidates outside the authenticated group store",
            )
        return Path(value).name

    def _stored_upload_bytes(self) -> int:
        total = 0
        if self.paths.uploads_dir.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "agent upload directory is not a real directory")
        if not self.paths.uploads_dir.exists():
            return 0
        if not self.paths.uploads_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, "agent upload directory is not a real directory")

        def visit(directory: Path) -> None:
            nonlocal total
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect stored uploads: {exc}") from exc
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect stored upload {entry.name!r}: {exc}") from exc
                if stat.S_ISLNK(info.st_mode):
                    continue
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    try:
                        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                    except OSError as exc:
                        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot read stored upload {entry.name!r}: {exc}") from exc
                    try:
                        opened = os.fstat(fd)
                        if not stat.S_ISREG(opened.st_mode):
                            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"stored upload {entry.name!r} changed type")
                        total += opened.st_size
                    except OSError as exc:
                        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect stored upload {entry.name!r}: {exc}") from exc
                    finally:
                        os.close(fd)

        visit(self.paths.uploads_dir)
        return total

    def _upload_free_bytes(self) -> int:
        try:
            stats = os.statvfs(self.paths.uploads_dir)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect upload filesystem free space: {exc}") from exc
        if type(stats.f_bavail) is not int or type(stats.f_frsize) is not int or stats.f_bavail < 0 or stats.f_frsize < 0:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload filesystem returned invalid available-byte facts")
        return stats.f_bavail * stats.f_frsize

    def _upload_store_objects(self) -> tuple[_UploadObjectRef, ...]:
        """List every committed store object, refusing a store entry it does not understand.

        The layout is the one ``put`` writes: ``uploads/<group>/<name>/<sha256>``.
        A group directory, a name directory and a committed object must all be
        real, non-symlinked entries; anything else (a stray file, an unreadable
        entry, an odd name) stops collection instead of silently shrinking the
        total the store limit is checked against.
        """

        state_fd = self._state_directory_fd()
        objects: list[_UploadObjectRef] = []
        try:
            uploads_fd = _open_optional_upload_directory(state_fd, "uploads", "agent upload store")
            if uploads_fd is None:
                return ()
            try:
                for group in _upload_directory_entries(uploads_fd, "agent upload store groups"):
                    if _UPLOAD_GROUP_RE.fullmatch(group) is None:
                        raise AdmissionError(
                            ExitCode.RUNNER_FAILURE,
                            f"cannot collect upload objects: upload store entry {group!r} is not an upload group directory",
                        )
                    with _upload_directory(uploads_fd, group, "agent upload group") as group_fd:
                        for name in _upload_directory_entries(group_fd, "agent upload names"):
                            if not _known_upload_name(name):
                                raise AdmissionError(
                                    ExitCode.RUNNER_FAILURE,
                                    f"cannot collect upload objects: upload store entry {group}/{name} is not a known upload name",
                                )
                            with _upload_directory(group_fd, name, "agent upload name") as name_fd:
                                objects.extend(self._upload_name_objects(group, name, name_fd))
            finally:
                os.close(uploads_fd)
        finally:
            os.close(state_fd)
        return tuple(objects)

    def _upload_name_objects(self, group: str, name: str, name_fd: int) -> list[_UploadObjectRef]:
        found: list[_UploadObjectRef] = []
        for digest in _upload_directory_entries(name_fd, f"agent upload objects below {group}/{name}"):
            if _UPLOAD_DIGEST_RE.fullmatch(digest) is None:
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE,
                    f"cannot collect upload objects: upload store entry {group}/{name}/{digest} is not a content address",
                )
            try:
                fd = open_beneath(name_fd, digest)
            except (OSError, ValueError) as exc:
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE,
                    f"cannot collect upload objects: store object {group}/{name}/{digest} is unreadable: {exc}",
                ) from exc
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise AdmissionError(
                        ExitCode.RUNNER_FAILURE,
                        f"cannot collect upload objects: store object {group}/{name}/{digest} is not a regular file",
                    )
                found.append(_UploadObjectRef(group, name, digest, info.st_size, info.st_mtime))
            except OSError as exc:
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE,
                    f"cannot collect upload objects: store object {group}/{name}/{digest} is unreadable: {exc}",
                ) from exc
            finally:
                os.close(fd)
        return found

    def _collect_upload_objects_locked(self, needed: int, store_limit: int) -> int:
        """Delete oldest unreferenced objects until ``needed`` bytes are reclaimed.

        Deletion stops as soon as the pending put would fit; it never runs to a
        target it cannot reach, so an unreachable request leaves the store as it
        found it.  A reference, a listing failure or an odd store entry stops
        collection with nothing further deleted.
        """

        if needed <= 0:
            return 0
        try:
            references = self._referenced_upload_objects_locked()
            objects = self._upload_store_objects()
        except AdmissionError as exc:
            raise AdmissionError(
                exc.exit_code,
                f"put refused and nothing was collected: {exc.reason}",
            ) from exc
        seed_sidecars = _referenced_seed_sidecars(references, objects)
        ordered = sorted(objects, key=lambda item: (item.modified, item.group, item.name, item.digest))
        candidates = [
            item for item in ordered
            if not _is_referenced_upload_object(item, references, seed_sidecars)
        ]
        if sum(item.size for item in candidates) < needed:
            return 0
        reclaimed = 0
        for item in candidates:
            if reclaimed >= needed:
                break
            self._unlink_upload_object(item)
            reclaimed += item.size
        return reclaimed

    def _unlink_upload_object(self, item: _UploadObjectRef) -> None:
        """Remove one committed object through its parent descriptor, never a symlink."""

        state_fd = -1
        try:
            state_fd = self._state_directory_fd()
            with _upload_directory(state_fd, "uploads", "agent upload store") as uploads_fd:
                with _upload_directory(uploads_fd, item.group, "agent upload group") as group_fd:
                    with _upload_directory(group_fd, item.name, "agent upload name") as object_fd:
                        os.unlink(item.digest, dir_fd=object_fd)
                        os.fsync(object_fd)
        except AdmissionError as exc:
            raise AdmissionError(
                exc.exit_code,
                f"put refused after a partial collection: {exc.reason}",
            ) from exc
        except (OSError, ValueError) as exc:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"put refused after a partial collection: cannot delete store object "
                f"{item.group}/{item.name}/{item.digest}: {exc}",
            ) from exc
        finally:
            if state_fd >= 0:
                os.close(state_fd)
        self._collection_hook(item)

    def _collection_hook(self, item: _UploadObjectRef) -> None:
        """An injected seam after each deletion; the default does nothing."""

    def _reserve_upload(
        self, expected: int, store_limit: int, floor_bytes: int,
        group: str | None = None, name: str | None = None, digest: str | None = None,
    ) -> _UploadReservation:
        if self.paths.incoming_dir.is_symlink() or not self.paths.incoming_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, "agent incoming directory is not a real directory")
        with self._upload_store_lock():
            in_flight, remaining, _live = self._recover_upload_reservations_locked()
            stored = self._stored_upload_bytes()
            existing = False
            if group is not None and name is not None and digest is not None:
                existing_path = self.paths.uploads_dir / group / name / digest
                if existing_path.is_symlink():
                    raise AdmissionError(ExitCode.REFUSAL, "put destination is a symlink")
                existing = existing_path.exists()
                if existing and self._hash_file(existing_path) != (expected, digest):
                    raise AdmissionError(ExitCode.REFUSAL, "existing host-created upload does not match its content address")
            required = stored + in_flight + (0 if existing else expected) - store_limit
            available = self._upload_free_bytes()
            if available - remaining - expected < floor_bytes:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"put would breach configured disk floor {floor_bytes}",
                )
            if required > 0:
                self._collect_upload_objects_locked(required, store_limit)
                stored = self._stored_upload_bytes()
            if stored + in_flight + (0 if existing else expected) > store_limit:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"put would exceed policy.max_upload_store_bytes {store_limit}",
                )
            temporary_fd = -1
            owner_fd = -1
            record_fd = -1
            temporary_name = ""
            reservation_path: Path | None = None
            temporary_record_path: Path | None = None
            owner_path: Path | None = None
            try:
                temporary_fd, temporary_name = tempfile.mkstemp(
                    prefix="upload-part-", dir=self.paths.incoming_dir,
                )
                os.fchmod(temporary_fd, 0o600)
                token = Path(temporary_name).name.removeprefix("upload-part-")
                reservation_path = self.paths.incoming_dir / f".upload-reservation-{token}.json"
                temporary_record_path = self.paths.incoming_dir / f".upload-reservation-{token}.tmp"
                owner_path = self.paths.incoming_dir / f".upload-owner-{token}.lock"
                owner_fd = os.open(
                    owner_path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fsync(owner_fd)
                self._fsync_directory(self.paths.incoming_dir)
                record_fd = os.open(
                    temporary_record_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                self._write_upload_reservation(record_fd, expected, expected, Path(temporary_name).name)
                os.fsync(record_fd)
                closing_fd, record_fd = record_fd, -1
                os.close(closing_fd)
                os.replace(temporary_record_path, reservation_path)
                self._fsync_directory(self.paths.incoming_dir)
            except OSError:
                if record_fd >= 0:
                    os.close(record_fd)
                if temporary_fd >= 0:
                    os.close(temporary_fd)
                    temporary_fd = -1
                cleaned = True
                if temporary_name:
                    try:
                        self._remove_upload_partial(Path(temporary_name).name)
                    except (AdmissionError, OSError):
                        cleaned = False
                for path in (temporary_record_path, reservation_path):
                    if path is not None:
                        try:
                            self._remove_upload_metadata(path.name)
                        except (AdmissionError, OSError):
                            cleaned = False
                if cleaned:
                    try:
                        self._fsync_directory(self.paths.incoming_dir)
                    except OSError:
                        cleaned = False
                if owner_fd >= 0:
                    if cleaned and owner_path is not None:
                        try:
                            self._remove_upload_owner(owner_path, owner_fd)
                        except (AdmissionError, OSError):
                            pass
                    fcntl.flock(owner_fd, fcntl.LOCK_UN)
                    os.close(owner_fd)
                raise
            return _UploadReservation(
                owner_fd, owner_path, reservation_path, temporary_record_path,
                temporary_fd, temporary_name, expected,
            )

    def _discard_upload_reservation(self, reservation: _UploadReservation) -> None:
        with self._upload_store_lock():
            self._remove_upload_partial(Path(reservation.temporary_name).name)
            self._remove_upload_metadata(reservation.temporary_record_path.name)
            self._remove_upload_metadata(reservation.path.name)
            self._fsync_directory(self.paths.incoming_dir)
            self._remove_upload_owner(reservation.owner_path, reservation.fd)

    def _release_upload_reservation(self, reservation: _UploadReservation) -> None:
        if reservation.fd >= 0:
            fd, reservation.fd = reservation.fd, -1
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def handle(
        self,
        request: object,
        identity: AgentIdentity,
        *,
        body_stream: object | None = None,
    ) -> AgentReply:
        """Validate the full request before dispatching a verb or creating files."""
        candidate_paths = self.paths.candidate_paths(identity.group)
        normalized = validate_request(
            request, identity, self.policy, created_candidate_paths=candidate_paths,
        )
        verb = normalized["verb"]
        if verb == "probe":
            return AgentReply(self._envelope(capabilities=self._capabilities()))
        if verb == "facts":
            return AgentReply(self._envelope(facts=self._facts()))
        if verb == "have":
            return AgentReply(self._have(str(normalized["repo"])))
        if verb == "put":
            if body_stream is None:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put request has no streamed body")
            return self._put(normalized, identity, body_stream)
        if verb == "fetch":
            return self._fetch(normalized, identity)
        if verb == "progress":
            return self._progress(normalized, identity)
        if verb == "submit":
            if self.executor is None:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    "executor unavailable: fleetd host executor is not configured",
                )
            result = self.admission.submit(normalized, identity, self.executor)
            return AgentReply(self._envelope(**result))
        if verb == "status":
            result = self.admission.status(str(normalized["job_id"]), identity, self.executor)
            return AgentReply(self._envelope(**result))
        if verb == "cancel":
            result = self.admission.cancel(
                str(normalized["job_id"]), str(normalized["reason"]), identity, self.executor,
            )
            return AgentReply(self._envelope(**result))
        if verb == "lease":
            raise AdmissionError(
                ExitCode.IDENTITY_OR_TRANSPORT,
                "lease requires the held-connection protocol handler",
            )
        raise AdmissionError(ExitCode.REFUSAL, f"agent verb {verb!r} is not available in this package stage")

    def serve_lease(
        self,
        request: object,
        identity: AgentIdentity,
        stream: object,
        *,
        on_lease_held: Callable[[], None] | None = None,
    ) -> None:
        """Grant, bind or reconnect a local lease and hold its flock to disconnect."""
        normalized = validate_request(
            request, identity, self.policy,
            created_candidate_paths=self.paths.candidate_paths(identity.group),
        )
        lease_id = str(normalized["lease_id"])
        container_id = normalized.get("container_id")
        result = None
        pending_new_lease = False
        try:
            if isinstance(container_id, str):
                result = self.admission.reserve_lease(
                    lease_id, identity, container_id=container_id,
                )
                if on_lease_held is not None:
                    on_lease_held()
                self._write_lease_message(stream, event="bound", lease_id=lease_id, container_id=container_id)
            else:
                result = self.admission.reserve_lease(
                    lease_id, identity,
                    container_name=str(normalized["container_name"]),
                    footprint=str(normalized["footprint"]),
                    planned_cores=float(normalized["planned_cores"]),
                    priority=str(normalized["priority"]),
                    waiter=_lease_queue_waiter(stream),
                )
                pending_new_lease = True
                if on_lease_held is not None:
                    on_lease_held()
                self._write_lease_message(
                    stream, event="grant", lease_id=lease_id,
                    container_name=str(result.record["container_name"]),
                )
                bind_deadline = self._monotonic() + self.policy.lease_bind_timeout_s
                bind_request = read_frame(
                    stream, None, deadline=bind_deadline, clock=self._monotonic,
                    waiter=self._lease_frame_waiter,
                )
                if (
                    set(bind_request) != {"event", "lease_id", "container_id"}
                    or bind_request.get("event") != "bind"
                    or bind_request.get("lease_id") != lease_id
                    or not isinstance(bind_request.get("container_id"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", str(bind_request["container_id"])) is None
                ):
                    raise AdmissionError(ExitCode.REFUSAL, "lease bind frame has an invalid closed shape")
                container_id = str(bind_request["container_id"])
                self.admission.bind_lease(lease_id, container_id, identity, result.handle)
                pending_new_lease = False
                self._write_lease_message(stream, event="bound", lease_id=lease_id, container_id=container_id)
            _wait_for_lease_disconnect(stream)
        finally:
            if result is not None and result.handle is not None:
                if pending_new_lease:
                    self._revoke_unbound_lease(lease_id, identity, result.handle)
                else:
                    result.handle.close()

    def _revoke_unbound_lease(self, lease_id: str, identity: AgentIdentity, handle: Any) -> None:
        """Tombstone an unbound lease, release its flock, then reconcile; a failed step keeps the charge."""
        try:
            self.admission.revoke_lease(lease_id, identity)
        except Exception:
            # No durable tombstone: keep the charge and skip inspection.
            return
        finally:
            handle.close()
        try:
            self.admission.reconcile_lease(lease_id, identity)
        except Exception:
            # A failed inspection leaves the tombstone charged for later reconciliation.
            return

    def _write_lease_message(self, stream: object, **fields: object) -> None:
        write_frame(stream, self._envelope(**fields))

    def _have(self, repo: str) -> dict[str, object]:
        root = self.policy.repo_roots[repo]
        if root.is_symlink() or not root.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, f"repo checkout root {root} is unavailable")
        base = root.resolve(strict=True)
        refs: list[str] = []
        try:
            children = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot read repo checkout root: {exc}") from exc
        for checkout in children:
            if _COMMIT_RE.fullmatch(checkout.name) is None or checkout.is_symlink() or not checkout.is_dir():
                continue
            try:
                checkout.resolve(strict=True).relative_to(base)
            except (OSError, RuntimeError, ValueError):
                continue
            marker = checkout / ".git"
            if not marker.exists() or marker.is_symlink():
                continue
            refs.append(checkout.name)
        return self._envelope(repo=repo, refs=refs)

    def _stream_upload(
        self, stream: object, expected: int, digest: str, target: object,
    ) -> None:
        try:
            fd = stream if type(stream) is int else stream.fileno()
        except (AttributeError, OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put stream has no file descriptor") from exc
        if type(fd) is not int or fd < 0:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put stream has an invalid file descriptor")
        hasher = hashlib.sha256()
        remaining = expected
        while remaining:
            try:
                readable, _, _ = select.select([fd], [], [], self.policy.idle_connection_s)
            except (OSError, ValueError) as exc:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"cannot wait for put body: {exc}") from exc
            if not readable:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put body idle connection deadline expired")
            try:
                chunk = os.read(fd, min(65536, remaining))
            except OSError as exc:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"cannot read put body: {exc}") from exc
            if not chunk:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put body is truncated")
            target.write(chunk)  # type: ignore[attr-defined]
            hasher.update(chunk)
            remaining -= len(chunk)

        try:
            readable, _, _ = select.select([fd], [], [], self.policy.idle_connection_s)
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"cannot check put body end: {exc}") from exc
        if not readable:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "put client did not half-close after its body")
        extra = os.read(fd, 1)
        if extra:
            raise AdmissionError(ExitCode.REFUSAL, "put body contains bytes beyond its declared length")
        if hasher.hexdigest() != digest:
            raise AdmissionError(ExitCode.REFUSAL, "put body SHA-256 does not match the request")

    def _put(self, request: Mapping[str, Any], identity: AgentIdentity, body_stream: object) -> AgentReply:
        expected = int(request["bytes"])
        maximum, store_limit, floor_bytes = self._put_limits()
        if expected > maximum:
            raise AdmissionError(ExitCode.REFUSAL, f"put.bytes {expected} exceeds policy.max_upload_bytes {maximum}")
        name = str(request["name"])
        digest = str(request["sha256"])
        reservation: _UploadReservation | None = None
        completed = False
        try:
            reservation = self._reserve_upload(
                expected, store_limit, floor_bytes, identity.group, name, digest,
            )
            with os.fdopen(reservation.temporary_fd, "wb", closefd=True) as target:
                reservation.temporary_fd = -1
                self._stream_upload(body_stream, expected, digest, target)
                target.flush()
                os.fsync(target.fileno())
            uploads_dir = self.paths.uploads_dir
            if uploads_dir.is_symlink() or not uploads_dir.is_dir():
                raise AdmissionError(ExitCode.REFUSAL, "agent upload directory is not a real directory")
            group_dir = uploads_dir / identity.group
            if group_dir.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "put group directory is a symlink")
            group_dir.mkdir(mode=0o700, exist_ok=True)
            if group_dir.is_symlink() or not group_dir.is_dir():
                raise AdmissionError(ExitCode.REFUSAL, "put group path is not a real directory")
            destination_dir = group_dir / name
            if destination_dir.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "put destination directory is a symlink")
            destination_dir.mkdir(mode=0o700, exist_ok=True)
            if destination_dir.is_symlink() or not destination_dir.is_dir():
                raise AdmissionError(ExitCode.REFUSAL, "put destination is not a real directory")
            destination = destination_dir / digest
            with self._upload_store_lock():
                if destination.is_symlink():
                    raise AdmissionError(ExitCode.REFUSAL, "put destination is a symlink")
                if destination.exists():
                    existing = self._hash_file(destination)
                    if existing != (expected, digest):
                        raise AdmissionError(ExitCode.REFUSAL, "existing host-created upload does not match its content address")
                    os.unlink(reservation.temporary_name)
                    self._fsync_directory(self.paths.incoming_dir)
                else:
                    os.replace(reservation.temporary_name, destination)
                    self._fsync_directory(destination_dir)
                    self._fsync_directory(self.paths.incoming_dir)
                self._remove_upload_metadata(reservation.temporary_record_path.name)
                self._remove_upload_metadata(reservation.path.name)
                self._fsync_directory(self.paths.incoming_dir)
                self._remove_upload_owner(reservation.owner_path, reservation.fd)
            reply_path = str(destination.resolve(strict=True))
            completed = True
            return AgentReply(self._envelope(name=name, sha256=digest, bytes=expected, path=reply_path))
        except AdmissionError:
            raise
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot store put body: {exc}") from exc
        finally:
            if reservation is not None:
                if reservation.temporary_fd >= 0:
                    os.close(reservation.temporary_fd)
                    reservation.temporary_fd = -1
                try:
                    if not completed:
                        self._discard_upload_reservation(reservation)
                finally:
                    self._release_upload_reservation(reservation)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _hash_file(path: Path) -> tuple[int, str]:
        if path.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "agent file must not be a symlink")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot open agent file {path}: {exc}") from exc
        hasher = hashlib.sha256()
        size = 0
        try:
            mode = os.fstat(fd).st_mode
            if not stat.S_ISREG(mode):
                raise AdmissionError(ExitCode.REFUSAL, "agent file is not a regular file")
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
        finally:
            os.close(fd)
        return size, hasher.hexdigest()

    def _owned_job(self, job_id: str, identity: AgentIdentity) -> tuple[Path, dict[str, Any]]:
        job_dir = self.paths.jobs_dir / job_id
        if job_dir.is_symlink() or not job_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, f"unknown job {job_id!r}")
        request_file = job_dir / "request.json"
        if request_file.is_symlink():
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} request record is a symlink")
        try:
            fd = os.open(request_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source:
                raw = source.read(65537)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read job {job_id!r} request record: {exc}") from exc
        if len(raw) > 65536:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} request record exceeds 64 KiB")
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} request record is invalid JSON") from exc
        if (
            not isinstance(record, dict)
            or record.get("group") != identity.group
            or not isinstance(record.get("request"), dict)
        ):
            raise AdmissionError(ExitCode.REFUSAL, f"job {job_id!r} does not belong to the authenticated group")
        return job_dir, record["request"]

    @staticmethod
    def _safe_file(root: Path, path: Path, *, missing_ok: bool = False) -> tuple[int, int] | None:
        try:
            resolved_root = root.resolve(strict=True)
            resolved = path.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise AdmissionError(ExitCode.REFUSAL, f"requested file {path.name!r} is missing") from None
        except (OSError, RuntimeError, ValueError):
            raise AdmissionError(ExitCode.REFUSAL, f"requested file {path.name!r} escapes its job directory") from None
        try:
            fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            if missing_ok and isinstance(exc, FileNotFoundError):
                return None
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot open requested file {path.name!r}: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise AdmissionError(ExitCode.REFUSAL, f"requested file {path.name!r} is not a regular file")
            return fd, info.st_size
        except BaseException:
            os.close(fd)
            raise

    def _fetch(self, request: Mapping[str, Any], identity: AgentIdentity) -> AgentReply:
        job_id = str(request["job_id"])
        job_dir, _job_request = self._owned_job(job_id, identity)
        artifact = str(request["artifact"])
        opened = self._safe_file(job_dir, job_dir / artifact)
        assert opened is not None
        fd, size = opened
        offset = int(request["offset"])
        if offset > size:
            os.close(fd)
            raise AdmissionError(ExitCode.REFUSAL, f"fetch.offset {offset} exceeds artifact size {size}")
        length = min(int(request["length"]), max(0, size - offset))
        hasher = hashlib.sha256()
        try:
            remaining = size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, f"artifact {artifact!r} changed while hashing")
                hasher.update(chunk)
                remaining -= len(chunk)
            os.lseek(fd, offset, os.SEEK_SET)
            body_parts: list[bytes] = []
            remaining = length
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, f"artifact {artifact!r} changed while reading its range")
                body_parts.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
        next_offset = offset + length
        return AgentReply(self._envelope(
            job_id=job_id, artifact=artifact, size=size, sha256=hasher.hexdigest(),
            offset=offset, length=length, next_offset=next_offset,
        ), body=b"".join(body_parts))

    @staticmethod
    def _lane_kind(checkout_fd: int, project: str, lane: str) -> str:
        config_paths = (
            "run-gate.toml" if project == "." else f"{project}/run-gate.toml",
            "run-gate.root.toml",
        )
        for config_path in config_paths:
            try:
                config_fd = open_beneath(checkout_fd, config_path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise AdmissionError(ExitCode.REFUSAL, f"Run-Gate lane declaration path is unsafe: {exc}") from exc
            try:
                with os.fdopen(config_fd, "rb") as source:
                    document = tomllib.loads(source.read().decode("utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read Run-Gate lane declaration: {exc}") from exc
            lanes = document.get("lanes")
            declaration = lanes.get(lane) if isinstance(lanes, Mapping) else None
            if isinstance(declaration, Mapping):
                kind = declaration.get("kind")
                if kind in {"assay", "command", "sequence"}:
                    return str(kind)
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Run-Gate lane {lane!r} has an invalid kind")
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Run-Gate lane {lane!r} is not declared in the job checkout")

    @staticmethod
    def _progress_file(root_fd: int, relative: str) -> tuple[int, int] | None:
        try:
            fd = open_beneath(root_fd, relative)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"progress path {relative!r} is unsafe: {exc}") from exc
        try:
            size = os.fstat(fd).st_size
        except OSError as exc:
            os.close(fd)
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot inspect progress file {relative!r}: {exc}") from exc
        return fd, size

    @staticmethod
    def _assay_done_count(fd: int, size: int) -> int | None:
        if size > MAX_ASSAY_PROGRESS_COUNT_BYTES:
            return None
        os.lseek(fd, 0, os.SEEK_SET)
        remainder = b""
        candidate_ids: set[str] = set()
        remaining = size
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            lines = (remainder + chunk).split(b"\n")
            remainder = lines.pop()
            for line in lines:
                try:
                    event = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict) and event.get("event") == "candidate" and isinstance(event.get("candidate_id"), str):
                    candidate_ids.add(event["candidate_id"])
        return len(candidate_ids)

    def _open_job_checkout(
        self, job_fd: int, job_id: str, job_request: Mapping[str, Any],
    ) -> tuple[int, Path]:
        try:
            return open_beneath(job_fd, "checkout", directory=True), self.paths.jobs_dir / job_id / "checkout"
        except FileNotFoundError:
            pass
        pointer_fd = open_beneath(job_fd, "checkout.json")
        try:
            raw = os.read(pointer_fd, 4097)
        finally:
            os.close(pointer_fd)
        if len(raw) > 4096:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} checkout pointer is too large")
        try:
            pointer = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} checkout pointer is unreadable: {exc}") from exc
        repo = job_request.get("repo")
        relative = f".fleetd-worktrees/{job_id}"
        if (
            not isinstance(pointer, dict)
            or pointer.get("schema") != "fleet-host-checkout/1"
            or pointer.get("repo") != repo
            or pointer.get("relative") != relative
            or not isinstance(repo, str)
            or repo not in self.policy.repo_roots
        ):
            raise AdmissionError(ExitCode.REFUSAL, f"job {job_id!r} checkout pointer has an invalid identity or path")
        root = self.policy.repo_roots[repo]
        if not root.is_absolute() or root == Path("/") or ".." in root.parts:
            raise AdmissionError(ExitCode.REFUSAL, f"configured repo root for {repo!r} is unsafe")
        host_root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        repo_fd = -1
        try:
            repo_fd = open_beneath(host_root_fd, root.relative_to("/").as_posix(), directory=True)
            checkout_fd = open_beneath(repo_fd, relative, directory=True)
        finally:
            for fd in (repo_fd, host_root_fd):
                if fd >= 0:
                    os.close(fd)
        return checkout_fd, root / relative

    def _progress(self, request: Mapping[str, Any], identity: AgentIdentity) -> AgentReply:
        job_id = str(request["job_id"])
        _job_dir, job_request = self._owned_job(job_id, identity)
        project = validate_relative_path(job_request.get("project"), "project", allow_dot=True)
        lane = job_request.get("lane")
        if not isinstance(lane, str):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} lane is invalid")
        jobs_fd = -1
        job_fd = -1
        checkout_fd = -1
        project_fd = -1
        try:
            jobs_fd = os.open(self.paths.jobs_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            job_fd = open_beneath(jobs_fd, job_id, directory=True)
            checkout_fd, checkout_path = self._open_job_checkout(job_fd, job_id, job_request)
            if project != ".":
                project_fd = open_beneath(checkout_fd, project, directory=True)
            lane_kind = self._lane_kind(checkout_fd, project, lane)
            if lane_kind == "assay":
                assay_lane = resolve_assay_lane(checkout_path, project, lane, root_fd=checkout_fd)
                project_prefix = "" if project == "." else f"{project}/"
                progress_path = f"{project_prefix}.assay/progress-{assay_lane}.jsonl"
                opened = self._progress_file(checkout_fd, progress_path)
                unit = "assay_candidates"
            else:
                opened = self._progress_file(jobs_fd, f"{job_id}/lane.log")
                unit = "log_bytes"
        except FileNotFoundError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} checkout is unavailable: {exc}") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise AdmissionError(ExitCode.REFUSAL, f"job {job_id!r} progress path is unsafe: {exc}") from exc
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open job {job_id!r} progress tree: {exc}") from exc
        finally:
            for fd in (project_fd, checkout_fd, job_fd, jobs_fd):
                if fd >= 0:
                    os.close(fd)

        offset = int(request["offset"])
        if opened is None:
            return AgentReply(self._envelope(
                job_id=job_id, offset=offset, next_offset=offset, length=0,
                done=0 if lane_kind == "assay" else None, total=None, unit=unit, last_progress_utc=None,
            ))
        fd, size = opened
        last_progress = dt.datetime.fromtimestamp(os.fstat(fd).st_mtime, tz=dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        done = self._assay_done_count(fd, size) if lane_kind == "assay" else None
        actual_offset = min(offset, size)
        body_length = min(MAX_FETCH_BYTES, size - actual_offset)
        os.lseek(fd, actual_offset, os.SEEK_SET)
        progress_fields: dict[str, object] = {
            "job_id": job_id, "offset": actual_offset,
            "next_offset": actual_offset + body_length, "length": body_length,
            "done": done, "total": None, "unit": unit,
            "last_progress_utc": last_progress,
        }
        if lane_kind == "assay" and size > MAX_ASSAY_PROGRESS_COUNT_BYTES:
            progress_fields["done_reason"] = (
                f"progress file exceeds the {MAX_ASSAY_PROGRESS_COUNT_BYTES}-byte count limit"
            )
        document = self._envelope(**progress_fields)
        return AgentReply(document, body_fd=fd, body_length=body_length)


def write_agent_reply(stream: object, reply: AgentReply, idle_timeout_s: float | None = None) -> None:
    """Write a capped response frame followed by its streamed body, then close it."""
    try:
        write_frame(stream, reply.document, idle_timeout_s)
        fd = stream if type(stream) is int else stream.fileno()
        if type(fd) is not int or fd < 0:
            raise FrameError("response stream has an invalid file descriptor")
        if reply.body:
            _write_bytes(fd, reply.body, idle_timeout_s)
        if reply.body_fd is not None:
            remaining = reply.body_length
            while remaining:
                chunk = os.read(reply.body_fd, min(65536, remaining))
                if not chunk:
                    raise FrameError("streamed response body was truncated")
                _write_bytes(fd, chunk, idle_timeout_s)
                remaining -= len(chunk)
    finally:
        reply.close()


def _write_bytes(fd: int, data: bytes, idle_timeout_s: float | None) -> None:
    try:
        _write_all(fd, data, idle_timeout_s)
    except FrameError as exc:
        raise FrameError(f"cannot write streamed response: {exc}") from exc


def _lease_queue_waiter(stream: object) -> Callable[[float], None]:
    if not isinstance(stream, socket.socket):
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lease requires a local socket connection")

    def wait(seconds: float) -> None:
        try:
            readable, _, _ = select.select([stream], [], [], seconds)
            if not readable:
                return
            data = stream.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return
        except OSError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"lease connection failed while queued: {exc}") from exc
        if not data:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lease client disconnected before its grant")
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unexpected client data while lease admission is queued")

    return wait


def _wait_for_lease_disconnect(stream: object) -> None:
    try:
        fd = stream if type(stream) is int else stream.fileno()
    except (AttributeError, OSError, ValueError) as exc:
        raise FrameError("held lease stream has no file descriptor") from exc
    if type(fd) is not int or fd < 0:
        raise FrameError("held lease stream has an invalid file descriptor")
    while True:
        try:
            readable, _, _ = select.select([fd], [], [], None)
            if not readable:
                continue
            data = os.read(fd, 1)
        except OSError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"held lease connection failed: {exc}") from exc
        if not data:
            return
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unexpected data after lease bind acknowledgement")
