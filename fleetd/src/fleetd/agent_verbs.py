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
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
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
    path: Path
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
    ) -> None:
        if not isinstance(agent_version, str) or _SEMVER_RE.fullmatch(agent_version) is None:
            raise AdmissionError(ExitCode.REFUSAL, "host agent version must be SemVer")
        self.paths = AgentPaths.initialize(state_dir)
        self.policy = policy
        self.facts_provider = facts_provider
        self.agent_version = agent_version
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
        os.unlink(path)

    def _recover_upload_reservations_locked(self) -> tuple[int, int, set[str]]:
        expected_total = 0
        remaining_total = 0
        live_partials: set[str] = set()
        try:
            entries = list(os.scandir(self.paths.incoming_dir))
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot inspect upload reservations: {exc}") from exc
        for entry in entries:
            if not entry.name.startswith(".upload-reservation-") or not entry.name.endswith(".json"):
                continue
            token = entry.name[len(".upload-reservation-"):-len(".json")]
            if not token or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload reservation filename is malformed")
            try:
                fd = os.open(entry.path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            except FileNotFoundError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload reservation changed during recovery") from exc
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot recover upload reservation: {exc}") from exc
            locked = False
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    expected, remaining, temporary = self._read_upload_reservation(fd, token)
                    expected_total += expected
                    remaining_total += remaining
                    live_partials.add(temporary)
                    continue
                expected, _remaining, temporary = self._read_upload_reservation(fd, token)
                self._remove_upload_partial(temporary)
                os.unlink(entry.path)
            except FileNotFoundError:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "upload reservation changed during recovery")
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot recover upload reservation: {exc}") from exc
            finally:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        try:
            entries = list(os.scandir(self.paths.incoming_dir))
            for entry in entries:
                if entry.name.startswith("upload-part-") and entry.name not in live_partials:
                    self._remove_upload_partial(entry.name)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot recover orphaned upload partials: {exc}") from exc
        return expected_total, remaining_total, live_partials

    def _stored_upload_bytes(self) -> int:
        total = 0
        if self.paths.uploads_dir.is_symlink() or not self.paths.uploads_dir.is_dir():
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

    def _reserve_upload(
        self, expected: int, store_limit: int, floor_bytes: int,
    ) -> _UploadReservation:
        if self.paths.incoming_dir.is_symlink() or not self.paths.incoming_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, "agent incoming directory is not a real directory")
        with self._upload_store_lock():
            in_flight, remaining, _live = self._recover_upload_reservations_locked()
            stored = self._stored_upload_bytes()
            if stored + in_flight + expected > store_limit:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"put would exceed policy.max_upload_store_bytes {store_limit}",
                )
            available = self._upload_free_bytes()
            if available - remaining - expected < floor_bytes:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"put would breach configured disk floor {floor_bytes}",
                )
            temporary_fd = -1
            reservation_fd = -1
            temporary_name = ""
            reservation_path: Path | None = None
            try:
                temporary_fd, temporary_name = tempfile.mkstemp(
                    prefix="upload-part-", dir=self.paths.incoming_dir,
                )
                os.fchmod(temporary_fd, 0o600)
                token = Path(temporary_name).name.removeprefix("upload-part-")
                reservation_path = self.paths.incoming_dir / f".upload-reservation-{token}.json"
                reservation_fd = os.open(
                    reservation_path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                fcntl.flock(reservation_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._write_upload_reservation(reservation_fd, expected, expected, Path(temporary_name).name)
                os.fsync(reservation_fd)
                self._fsync_directory(self.paths.incoming_dir)
            except OSError:
                if temporary_fd >= 0:
                    os.close(temporary_fd)
                if temporary_name:
                    try:
                        os.unlink(temporary_name)
                    except FileNotFoundError:
                        pass
                if reservation_fd >= 0:
                    os.close(reservation_fd)
                if reservation_path is not None:
                    try:
                        os.unlink(reservation_path)
                    except FileNotFoundError:
                        pass
                raise
            return _UploadReservation(
                reservation_fd, reservation_path, temporary_fd, temporary_name, expected,
            )

    def _update_upload_remaining(self, reservation: _UploadReservation, remaining: int) -> None:
        with self._upload_store_lock():
            self._write_upload_reservation(
                reservation.fd, reservation.expected, remaining,
                Path(reservation.temporary_name).name,
            )

    def _discard_upload_reservation(self, reservation: _UploadReservation) -> None:
        with self._upload_store_lock():
            self._remove_upload_partial(Path(reservation.temporary_name).name)
            try:
                os.unlink(reservation.path)
            except FileNotFoundError:
                pass
            self._fsync_directory(self.paths.incoming_dir)

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
                bind_request = read_frame(stream, None)
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
                    tombstoned = False
                    try:
                        self.admission.revoke_lease(lease_id, identity)
                        tombstoned = True
                    except Exception:
                        pass
                    finally:
                        result.handle.close()
                    if tombstoned:
                        try:
                            self.admission.reconcile_lease(lease_id, identity)
                        except Exception:
                            # A failed inspection leaves the tombstone charged for later reconciliation.
                            pass
                    else:
                        # The tombstone write failed; keep the charge and skip inspection.
                        pass
                else:
                    result.handle.close()

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
        on_chunk: Callable[[int], None] | None = None,
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
            if on_chunk is not None:
                on_chunk(remaining)

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
            reservation = self._reserve_upload(expected, store_limit, floor_bytes)
            with os.fdopen(reservation.temporary_fd, "wb", closefd=True) as target:
                reservation.temporary_fd = -1
                self._stream_upload(
                    body_stream, expected, digest, target,
                    lambda remaining: self._update_upload_remaining(reservation, remaining),
                )
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
                else:
                    os.replace(reservation.temporary_name, destination)
                    self._fsync_directory(destination_dir)
                os.unlink(reservation.path)
                self._fsync_directory(self.paths.incoming_dir)
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
    def _assay_done_count(fd: int, size: int) -> int:
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

    def _progress(self, request: Mapping[str, Any], identity: AgentIdentity) -> AgentReply:
        job_id = str(request["job_id"])
        job_dir, job_request = self._owned_job(job_id, identity)
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
            checkout_fd = open_beneath(job_fd, "checkout", directory=True)
            if project != ".":
                project_fd = open_beneath(checkout_fd, project, directory=True)
            lane_kind = self._lane_kind(checkout_fd, project, lane)
            if lane_kind == "assay":
                checkout = job_dir / "checkout"
                assay_lane = resolve_assay_lane(checkout, project, lane, root_fd=checkout_fd)
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
        document = self._envelope(
            job_id=job_id, offset=actual_offset, next_offset=actual_offset + body_length, length=body_length,
            done=done, total=None, unit=unit, last_progress_utc=last_progress,
        )
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
