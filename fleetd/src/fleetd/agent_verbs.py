"""Host filesystem verbs for the fleetd agent protocol."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import select
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .admission import AdmissionError, ExitCode
from .agent_policy import AgentIdentity, AgentPolicy
from .agent_protocol import FrameError, validate_request, write_frame
from .contracts import ContractError, HostFacts, validate_host_facts
from .hostside import resolve_assay_lane

AGENT_SCHEMA = "fleet-host-agent/1"
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
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
    "lease": ["verb", "lease_id", "container_name", "footprint", "planned_cores", "priority"],
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


class AgentVerbService:
    """Serve read, upload and artifact verbs without opening the admission book."""

    def __init__(
        self,
        state_dir: Path,
        policy: AgentPolicy,
        facts_provider: Callable[[], Mapping[str, object] | HostFacts],
        *,
        agent_version: str,
    ) -> None:
        self.paths = AgentPaths.initialize(state_dir)
        self.policy = policy
        self.facts_provider = facts_provider
        self.agent_version = agent_version

    def _envelope(self, **fields: object) -> dict[str, object]:
        return {"schema": AGENT_SCHEMA, "agent_version": self.agent_version, **fields}

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
            return AgentReply(self._envelope(capabilities=_CAPABILITIES))
        if verb == "facts":
            return AgentReply(self._facts())
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
        raise AdmissionError(ExitCode.REFUSAL, f"agent verb {verb!r} is not available in this package stage")

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

    def _stream_upload(self, stream: object, expected: int, digest: str, target: object) -> None:
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
        maximum = self._max_upload_bytes()
        if expected > maximum:
            raise AdmissionError(ExitCode.REFUSAL, f"put.bytes {expected} exceeds policy.max_upload_bytes {maximum}")
        name = str(request["name"])
        digest = str(request["sha256"])
        temporary_fd = -1
        temporary_name = ""
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(prefix="upload-", dir=self.paths.incoming_dir)
            os.fchmod(temporary_fd, 0o600)
            with os.fdopen(temporary_fd, "wb", closefd=True) as target:
                temporary_fd = -1
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
            if destination.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "put destination is a symlink")
            if destination.exists():
                existing = self._hash_file(destination)
                if existing != (expected, digest):
                    raise AdmissionError(ExitCode.REFUSAL, "existing host-created upload does not match its content address")
                os.unlink(temporary_name)
            else:
                os.replace(temporary_name, destination)
                temporary_name = ""
                self._fsync_directory(destination_dir)
            reply_path = str(destination.resolve(strict=True))
            return AgentReply(self._envelope(name=name, sha256=digest, bytes=expected, path=reply_path))
        except AdmissionError:
            raise
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot store put body: {exc}") from exc
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

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
    def _lane_kind(checkout: Path, project: str, lane: str) -> str:
        project_dir = checkout if project == "." else checkout / project
        checkout_root = checkout.resolve(strict=True)
        for config_path in (project_dir / "run-gate.toml", checkout / "run-gate.root.toml"):
            try:
                resolved_config = config_path.resolve(strict=True)
                resolved_config.relative_to(checkout_root)
                document = tomllib.loads(resolved_config.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError, ValueError, UnicodeError, tomllib.TOMLDecodeError) as exc:
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
        checkout = job_dir / "checkout"
        if checkout.is_symlink() or not checkout.is_dir():
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"job {job_id!r} checkout is unavailable")
        checkout = checkout.resolve(strict=True)
        project = str(job_request["project"])
        project_dir = checkout if project == "." else (checkout / project).resolve(strict=True)
        try:
            project_dir.relative_to(checkout)
        except ValueError:
            raise AdmissionError(ExitCode.REFUSAL, "job project path escapes its checkout") from None
        lane = str(job_request["lane"])
        lane_kind = self._lane_kind(checkout, project, lane)
        if lane_kind == "assay":
            assay_lane = resolve_assay_lane(checkout, project, lane)
            path = project_dir / ".assay" / f"progress-{assay_lane}.jsonl"
            unit = "assay_candidates"
        else:
            path = job_dir / "lane.log"
            unit = "log_bytes"
        offset = int(request["offset"])
        opened = self._safe_file(job_dir if lane_kind != "assay" else checkout, path, missing_ok=True)
        if opened is None:
            return AgentReply(self._envelope(
                job_id=job_id, offset=offset, next_offset=offset, length=0,
                done=0 if lane_kind == "assay" else None, total=None, unit=unit, last_progress_utc=None,
            ))
        fd, size = opened
        last_progress = dt.datetime.fromtimestamp(os.fstat(fd).st_mtime, tz=dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        done = self._assay_done_count(fd, size) if lane_kind == "assay" else None
        actual_offset = min(offset, size)
        body_length = size - actual_offset
        os.lseek(fd, actual_offset, os.SEEK_SET)
        document = self._envelope(
            job_id=job_id, offset=actual_offset, next_offset=size, length=body_length,
            done=done, total=None, unit=unit, last_progress_utc=last_progress,
        )
        return AgentReply(document, body_fd=fd, body_length=body_length)


def write_agent_reply(stream: object, reply: AgentReply) -> None:
    """Write a capped response frame followed by its streamed body, then close it."""
    try:
        write_frame(stream, reply.document)
        fd = stream if type(stream) is int else stream.fileno()
        if type(fd) is not int or fd < 0:
            raise FrameError("response stream has an invalid file descriptor")
        if reply.body:
            _write_bytes(fd, reply.body)
        if reply.body_fd is not None:
            remaining = reply.body_length
            while remaining:
                chunk = os.read(reply.body_fd, min(65536, remaining))
                if not chunk:
                    raise FrameError("streamed response body was truncated")
                _write_bytes(fd, chunk)
                remaining -= len(chunk)
    finally:
        reply.close()


def _write_bytes(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except OSError as exc:
            raise FrameError(f"cannot write streamed response: {exc}") from exc
        if written <= 0:
            raise FrameError("streamed response write made no progress")
        view = view[written:]
