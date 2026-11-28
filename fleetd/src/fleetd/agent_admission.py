"""Admission and executor boundary for the fleetd host agent."""

from __future__ import annotations

import fcntl
import json
import os
import queue
import stat
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from .admission import (
    AdmissionBook,
    AdmissionError,
    AdmissionHandle,
    AdmissionRequest,
    AdmissionResources,
    AdmissionResult,
    ExitCode,
    HostCapacity,
    InspectorAnswer,
)
from .agent_capacity import host_capacity_from_facts
from .agent_policy import AgentIdentity, AgentPolicy
from .contracts import ContractError, HostFacts, validate_host_facts


class AgentExecutor(Protocol):
    """The C3b boundary; start takes ownership of the live admission handle."""

    def start(
        self, request: Mapping[str, object], admission: AdmissionHandle,
    ) -> Mapping[str, object]:
        """Start the admitted job and keep or hand off its flock until completion."""
        ...

    def request_stop(self, job_id: str, reason: str) -> Mapping[str, object] | None: ...

    def read_state(self, job_id: str) -> Mapping[str, object] | None: ...


class BoundedCall:
    """Bound a synchronous query without letting it keep the caller's lock.

    At most one callback may remain outstanding. A timed-out daemon worker may
    finish later, but it never owns the admission queue lock and cannot create
    an unbounded worker backlog.
    """

    def __init__(
        self,
        timeout_s: float,
        *,
        name: str,
        completion_waiter: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.name = name
        self.completion_waiter = completion_waiter or (lambda finished, timeout: finished.wait(timeout))
        self._slot = threading.BoundedSemaphore(1)
        self._last_worker: threading.Thread | None = None

    def __call__(self, callback: Callable[[], Any]) -> Any:
        if not self._slot.acquire(blocking=False):
            raise TimeoutError(f"{self.name} query is still running after its deadline")
        finished = threading.Event()
        outcomes: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                outcomes.put((True, callback()))
            except BaseException as exc:
                outcomes.put((False, exc))
            finally:
                self._slot.release()
                finished.set()

        worker = threading.Thread(target=invoke, name=f"fleetd-{self.name}-query", daemon=True)
        self._last_worker = worker
        try:
            worker.start()
        except RuntimeError:
            self._slot.release()
            raise
        if not self.completion_waiter(finished, self.timeout_s):
            raise TimeoutError(f"{self.name} query timed out after {self.timeout_s:g}s")
        succeeded, outcome = outcomes.get_nowait()
        if succeeded:
            return outcome
        if isinstance(outcome, BaseException):
            raise outcome
        raise RuntimeError(f"{self.name} query returned an invalid worker result")


class AgentAdmission:
    """Adapt validated agent requests to the durable C2 admission book."""

    def __init__(
        self,
        state_dir: Path,
        policy: AgentPolicy,
        facts_provider: Callable[[], Mapping[str, object] | HostFacts],
        *,
        legacy_jobs_provider: Callable[[], Iterable[str]] | None = None,
        inspector: Callable[[str], InspectorAnswer] | None = None,
        container_inspector: Callable[[str], Mapping[str, object] | None] | None = None,
        clock: Callable[[], float] = time.time,
        waiter: Callable[[float], None] = time.sleep,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.policy = policy
        self.facts_provider = facts_provider
        self.legacy_jobs_provider = legacy_jobs_provider
        self.inspector = inspector
        self.container_inspector = container_inspector
        self.book = AdmissionBook(self.state_dir, clock=clock, waiter=waiter)
        self._capacity_call = BoundedCall(policy.query_timeout_s, name="capacity")
        self._legacy_call = BoundedCall(policy.query_timeout_s, name="legacy-list")
        self._inspector_call = BoundedCall(policy.query_timeout_s, name="object-inspector")
        self._uncertain_start_handles: dict[str, AdmissionHandle] = {}
        self._uncertain_start_lock = threading.Lock()

    def _capacity(self, identity: AgentIdentity) -> HostCapacity:
        try:
            raw = self.facts_provider()
            document = raw.to_dict() if isinstance(raw, HostFacts) else raw
            capacity = host_capacity_from_facts(validate_host_facts(document))
        except (ContractError, OSError, ValueError, TypeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"host capacity unavailable: {exc}") from exc

        priorities = capacity.policy.priority_classes_by_principal
        if priorities.value is None:
            return capacity
        principal_classes = priorities.value.get(identity.principal)
        if principal_classes is None:
            raise AdmissionError(
                ExitCode.REFUSAL,
                f"priority policy is missing for authenticated principal {identity.principal!r}",
            )
        return replace(
            capacity,
            policy=replace(
                capacity.policy,
                priority_classes_by_principal=type(priorities).known({identity.group: principal_classes}),
            ),
        )

    def _capacity_provider(self, identity: AgentIdentity) -> Callable[[], HostCapacity]:
        def provide() -> HostCapacity:
            try:
                return self._capacity_call(lambda: self._capacity(identity))
            except TimeoutError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"host capacity query timed out: {exc}") from exc
        return provide

    def _legacy_provider(self) -> Callable[[], Iterable[str]]:
        def provide() -> Iterable[str]:
            if self.legacy_jobs_provider is None:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "legacy job list provider is not configured")
            try:
                return self._legacy_call(lambda: tuple(self.legacy_jobs_provider()))
            except TimeoutError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"legacy job list query timed out: {exc}") from exc
        return provide

    def _inspector_provider(self) -> Callable[[str], InspectorAnswer] | None:
        if self.inspector is None:
            return None

        def inspect(target: str) -> InspectorAnswer:
            try:
                return self._inspector_call(lambda: self.inspector(target))
            except TimeoutError:
                return InspectorAnswer.ERROR
        return inspect

    @staticmethod
    def request_model(request: Mapping[str, Any]) -> AdmissionRequest:
        resources = request["resources"]
        requested_by = request["requested_by"]
        return AdmissionRequest(
            job_id=request["job_id"],
            repo=request["repo"],
            sha=request["sha"],
            base=request["base"],
            project=request["project"],
            lane=request["lane"],
            shard=request["shard"],
            resources=AdmissionResources(
                planned_cores=resources["planned_cores"],
                footprint=resources["footprint"],
                declared_peak_bytes=resources["declared_peak_bytes"],
                cpu_set=resources["cpu_set"],
            ),
            priority=request["priority"],
            requested_by_client=requested_by["client"],
            requested_by_version=requested_by["version"],
            requested_by_run=requested_by["run"],
            stores=request["stores"],
            extra_argv=tuple(request["extra_argv"]),
            env=request["env"],
            dev=request["dev"],
            kind=request["kind"],
        )

    def _job_dir(self, job_id: str) -> Path:
        jobs_dir = self.state_dir / "jobs"
        try:
            jobs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot create agent jobs directory: {exc}") from exc
        if jobs_dir.is_symlink() or not jobs_dir.is_dir() or jobs_dir.stat().st_mode & 0o077:
            raise AdmissionError(ExitCode.REFUSAL, "agent jobs path is not a private real directory")
        path = jobs_dir / job_id
        if path.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, f"job directory {job_id!r} must not be a symlink")
        try:
            path.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot create durable job directory: {exc}") from exc
        if path.is_symlink() or not path.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, f"job path {job_id!r} is not a real directory")
        if path.stat().st_mode & 0o077:
            raise AdmissionError(ExitCode.REFUSAL, f"job directory {job_id!r} is accessible by other users")
        return path

    def _submit_lock(self, job_id: str) -> int:
        """Serialize same-id submit write, admission and cleanup transactions."""

        job_dir = self._job_dir(job_id)
        directory_fd = -1
        lock_fd = -1
        try:
            directory_fd = os.open(
                job_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            lock_fd = os.open(
                "submit.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_mode & 0o077:
                raise AdmissionError(ExitCode.REFUSAL, "job submit lock is not a private regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return lock_fd
        except AdmissionError:
            if lock_fd >= 0:
                os.close(lock_fd)
            raise
        except OSError as exc:
            if lock_fd >= 0:
                os.close(lock_fd)
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot lock durable job state: {exc}") from exc
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)

    @staticmethod
    def _existing_request(path: Path, *, group: str, digest: str) -> bool:
        if path.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "durable request record is a symlink")
        if not path.exists():
            return False
        try:
            raw = path.read_bytes()
            if len(raw) > 64 * 1024:
                raise ValueError("record exceeds 64 KiB")
            record = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"durable request record is unreadable: {exc}") from exc
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "group", "digest", "request"}
            or record.get("schema") != "fleet-host-job-request/1"
        ):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable request record has an invalid schema")
        if record["group"] != group:
            raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
        if record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "job id was already used with a different request digest")
        if not isinstance(record["request"], dict):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable request payload is invalid")
        return True

    def _write_request(self, request: Mapping[str, Any], identity: AgentIdentity, digest: str) -> tuple[Path, bool]:
        job_dir = self._job_dir(str(request["job_id"]))
        request_path = job_dir / "request.json"
        if self._existing_request(request_path, group=identity.group, digest=digest):
            return request_path, False
        document = {
            "schema": "fleet-host-job-request/1",
            "group": identity.group,
            "digest": digest,
            "request": request,
        }
        try:
            payload = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"request is not durable JSON: {exc}") from exc
        if len(payload) > 64 * 1024:
            raise AdmissionError(ExitCode.REFUSAL, "durable request record exceeds the 64 KiB limit")
        temporary = ""
        try:
            fd, temporary = tempfile.mkstemp(prefix="request-", dir=job_dir)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, request_path, follow_symlinks=False)
            except FileExistsError:
                self._existing_request(request_path, group=identity.group, digest=digest)
                os.unlink(temporary)
                temporary = ""
                return request_path, False
            os.unlink(temporary)
            temporary = ""
            directory_fd = os.open(job_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot persist durable request record: {exc}") from exc
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return request_path, True

    @staticmethod
    def _remove_unadmitted_request(path: Path, *, created: bool) -> None:
        if not created:
            return
        try:
            path.unlink(missing_ok=True)
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Keeping an unclaimed durable request is safer than hiding an uncertain record.
            pass

    def submit(self, request: Mapping[str, Any], identity: AgentIdentity, executor: AgentExecutor) -> Mapping[str, object]:
        model = self.request_model(request)
        digest = model.digest()
        lock_fd = self._submit_lock(model.job_id)
        try:
            return self._submit_locked(request, identity, executor, model, digest)
        finally:
            os.close(lock_fd)

    def _submit_locked(
        self,
        request: Mapping[str, Any],
        identity: AgentIdentity,
        executor: AgentExecutor,
        model: AdmissionRequest,
        digest: str,
    ) -> Mapping[str, object]:
        request_path, created_request = self._write_request(request, identity, digest)
        try:
            result = self.book.submit(
                model,
                authenticated_group=identity.group,
                capacity_provider=self._capacity_provider(identity),
                legacy_jobs_provider=self._legacy_provider(),
                inspector=self._inspector_provider(),
            )
        except AdmissionError:
            try:
                current = self.book.status(model.job_id, identity.group)
            except AdmissionError:
                current = {"state": "uncertain"}
            self._remove_unadmitted_request(request_path, created=created_request and current is None)
            raise

        if result.handle is None:
            if result.record.get("schema") == "fleet-admission-waiting/1":
                return {"job_id": model.job_id, "state": "queued", "reason": None}
            if result.record.get("schema") == "fleet-admission-terminal/1":
                return self._terminal_state(model.job_id, result.record)
            state = executor.read_state(model.job_id)
            if state is None:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor has no state for the admitted idempotent job")
            return dict(state)
        if not isinstance(result.handle, AdmissionHandle):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "job admission returned a non-job handle")
        try:
            state = executor.start(request, result.handle)
        except BaseException as exc:
            with self._uncertain_start_lock:
                self._uncertain_start_handles[model.job_id] = result.handle
            if not isinstance(exc, Exception):
                raise
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"executor start failed; durable request and admission charge are retained: {exc}",
            ) from exc
        if not isinstance(state, Mapping):
            with self._uncertain_start_lock:
                self._uncertain_start_handles[model.job_id] = result.handle
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor start returned no job state")
        return dict(state)

    def status(self, job_id: str, identity: AgentIdentity, executor: AgentExecutor | None) -> Mapping[str, object]:
        record = self.book.status(job_id, identity.group)
        if record is None:
            raise AdmissionError(ExitCode.REFUSAL, f"unknown job {job_id!r}")
        if record.get("schema") == "fleet-admission-waiting/1":
            return {"job_id": job_id, "state": "queued", "reason": None}
        if record.get("schema") == "fleet-admission-terminal/1":
            return self._terminal_state(job_id, record)
        if record.get("schema") != "fleet-admission-job/1":
            raise AdmissionError(ExitCode.REFUSAL, f"record for {job_id!r} is not an admitted job")
        if executor is None:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor unavailable: cannot read admitted job state")
        state = executor.read_state(job_id)
        if state is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"executor has no state for job {job_id!r}")
        return dict(state)

    @staticmethod
    def _terminal_state(job_id: str, record: Mapping[str, object]) -> Mapping[str, object]:
        result: dict[str, object] = {
            "job_id": job_id,
            "state": record["state"],
            "completed_at": record["completed_at"],
        }
        for key in ("verdict", "exit_code", "lane", "api", "collected_at", "result_sha256", "result_size"):
            if key in record:
                result[key] = record[key]
        return result

    def cancel(
        self, job_id: str, reason: str, identity: AgentIdentity, executor: AgentExecutor | None,
    ) -> Mapping[str, object]:
        record = self.book.cancel(job_id, identity.group)
        if record is None:
            raise AdmissionError(ExitCode.REFUSAL, f"unknown job {job_id!r}")
        if record.get("schema") == "fleet-admission-waiting/1":
            return {"job_id": job_id, "state": "cancelled", "reason": reason}
        if record.get("schema") == "fleet-admission-terminal/1":
            raise AdmissionError(ExitCode.REFUSAL, f"finished job {job_id!r} cannot be cancelled")
        if record.get("schema") != "fleet-admission-job/1":
            raise AdmissionError(ExitCode.REFUSAL, f"record for {job_id!r} is not an admitted job")
        if executor is None:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor unavailable: cannot stop admitted job")
        state = executor.request_stop(job_id, reason)
        if state is None:
            state = executor.read_state(job_id)
        if state is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"executor has no state for job {job_id!r}")
        return dict(state)

    def reserve_lease(
        self,
        lease_id: str,
        identity: AgentIdentity,
        *,
        container_name: str | None = None,
        footprint: str | None = None,
        planned_cores: float | None = None,
        priority: str | None = None,
        container_id: str | None = None,
        waiter: Callable[[float], None] | None = None,
    ) -> AdmissionResult:
        if container_id is not None:
            existing = self.book.status(lease_id, identity.group)
            if existing is None or existing.get("schema") != "fleet-admission-lease/1":
                raise AdmissionError(ExitCode.REFUSAL, "unknown lease id")
            if existing.get("state") == "tombstone" or existing.get("released") is True:
                raise AdmissionError(ExitCode.REFUSAL, "a revoked lease cannot be reconnected")
            prior_id = existing.get("container_id")
            if prior_id is not None and prior_id != container_id:
                raise AdmissionError(ExitCode.REFUSAL, "reconnect container id does not match the immutable binding")
            self._verify_container_binding(lease_id, str(existing["container_name"]), container_id)
            result = self.book.resume_lease(lease_id, identity.group, container_id)
            if result.handle is None:
                raise AdmissionError(
                    ExitCode.QUEUED_WITHDRAWN,
                    "lease already has a live connection; no second bind is allowed",
                )
            return result

        if container_name is None or footprint is None or planned_cores is None or priority is None:
            raise AdmissionError(ExitCode.REFUSAL, "new lease requires container_name, footprint, planned_cores and priority")

        from .admission import LeaseRequest

        request = LeaseRequest(
            lease_id=lease_id,
            container_name=container_name,
            resources=AdmissionResources(planned_cores=planned_cores, footprint=footprint),
            priority=priority,
        )
        inspector = self._inspector_provider()
        if inspector is None:
            raise AdmissionError(ExitCode.REFUSAL, "lease requires an injected object inspector")
        result = self.book.reserve_lease(
            request,
            authenticated_group=identity.group,
            capacity_provider=self._capacity_provider(identity),
            legacy_jobs_provider=self._legacy_provider(),
            inspector=inspector,
            waiter=waiter,
        )
        if result.handle is None:
            if result.record.get("state") == "tombstone" or result.record.get("released") is True:
                raise AdmissionError(ExitCode.REFUSAL, "a revoked lease cannot be reconnected")
            raise AdmissionError(
                ExitCode.QUEUED_WITHDRAWN,
                "lease already exists; reconnect with its immutable container_id",
            )
        return result

    def _verify_container_binding(self, lease_id: str, container_name: str, container_id: str) -> None:
        if self.container_inspector is None:
            raise AdmissionError(ExitCode.REFUSAL, "lease bind requires a container identity inspector")
        try:
            observed = self._inspector_call(lambda: self.container_inspector(container_id))
        except TimeoutError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"container identity inspector timed out: {exc}") from exc
        except Exception as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"container identity inspector failed: {exc}") from exc
        if not isinstance(observed, Mapping):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "container identity inspector returned ERROR")
        observed_id = observed.get("id")
        observed_name = observed.get("name")
        labels = observed.get("labels")
        if observed_id != container_id:
            raise AdmissionError(ExitCode.REFUSAL, "lease bind container id does not match the inspected immutable id")
        if isinstance(observed_name, str) and observed_name.startswith("/"):
            observed_name = observed_name[1:]
        if observed_name != container_name:
            raise AdmissionError(ExitCode.REFUSAL, "lease bind container name does not match its grant")
        if not isinstance(labels, Mapping) or labels.get("fleetd.lease") != lease_id:
            raise AdmissionError(ExitCode.REFUSAL, "lease bind container is missing its fleetd.lease label")

    def bind_lease(
        self, lease_id: str, container_id: str, identity: AgentIdentity, handle: object,
    ) -> Mapping[str, object]:
        record = self.book.status(lease_id, identity.group)
        if record is None or record.get("schema") != "fleet-admission-lease/1":
            raise AdmissionError(ExitCode.REFUSAL, "unknown lease id")
        self._verify_container_binding(lease_id, str(record["container_name"]), container_id)
        return self.book.bind_lease(lease_id, identity.group, container_id, handle)  # type: ignore[arg-type]

    def reconcile_lease(self, lease_id: str, identity: AgentIdentity) -> Mapping[str, object] | None:
        inspector = self._inspector_provider()
        if inspector is None:
            raise AdmissionError(ExitCode.REFUSAL, "lease reconciliation requires an injected object inspector")
        return self.book.reconcile_lease(lease_id, identity.group, inspector)

    def revoke_lease(self, lease_id: str, identity: AgentIdentity) -> Mapping[str, object] | None:
        """Persist the no-more-bindings tombstone before a pending lease is inspected."""

        return self.book.revoke_lease(lease_id, identity.group)

    def reconcile_leases(self) -> tuple[Mapping[str, object], ...]:
        """Reconcile exact durable lease targets; an unconfigured inspector is a no-op."""

        inspector = self._inspector_provider()
        if inspector is None:
            return ()
        results: list[Mapping[str, object]] = []
        for record in self.book.lease_records():
            lease_id = str(record["lease_id"])
            group = str(record["group"])
            try:
                reconciled = self.book.reconcile_lease(lease_id, group, inspector)
            except Exception:
                # Uncertainty never releases the durable accounting charge.
                continue
            if reconciled is not None:
                results.append(reconciled)
        return tuple(results)
