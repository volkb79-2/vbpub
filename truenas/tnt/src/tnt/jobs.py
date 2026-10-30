"""Host-scoped records for TrueNAS v27 jobs and generation-bound client handles."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


TERMINAL_STATES = frozenset({"SUCCESS", "FAILED", "ABORTED"})
KNOWN_STATES = TERMINAL_STATES | {"WAITING", "RUNNING", "UNKNOWN"}
_SENSITIVE_KEYS = frozenset({"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key",
                             "credentials", "credential", "token", "secret", "secret_access_key", "access_key",
                             "client_secret", "encryption_password", "encryption_salt", "key", "client_key", "salt",
                             "stored_key", "server_key"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any, scrub: Callable[[str], str], key: Any = None) -> Any:
    if isinstance(key, str):
        normalized = key.casefold().replace("-", "_")
        if (normalized in _SENSITIVE_KEYS or any(marker in normalized for marker in
                                                 ("password", "secret", "credential", "token"))
                or normalized.endswith("_key")):
            return "***"
    if isinstance(value, Mapping):
        return {str(name): _safe(child, scrub, name) for name, child in value.items()
                if isinstance(name, (str, int, float, bool))}
    if isinstance(value, (list, tuple)):
        return [_safe(child, scrub) for child in value]
    if isinstance(value, str):
        return scrub(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub(str(value))


@dataclass
class _JobRecord:
    local_id: str
    host: str
    job_id: str
    method: str
    generation: int
    state: str = "WAITING"
    progress: dict[str, Any] = field(default_factory=dict)
    logs_excerpt: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    reconciliation: str = "live"
    events: list[dict[str, str]] = field(default_factory=list)


class JobTicket:
    """Buffer callback updates until the synchronous client returns its remote job ID."""

    def __init__(self, registry: "JobRegistry", method: str, generation: int,
                 callback: Callable[[Mapping[str, Any]], None] | None):
        self.registry = registry
        self.method = method
        self.generation = generation
        self.callback = callback
        self._lock = threading.RLock()
        self._local_id: str | None = None
        self._pending: list[Mapping[str, Any]] = []

    def on_update(self, fields: Mapping[str, Any]) -> None:
        if not isinstance(fields, Mapping):
            return
        safe_fields = self.registry._callback_fields(fields)
        with self._lock:
            local_id = self._local_id
            if local_id is None:
                self._pending.append(safe_fields)
                self._pending = self._pending[-100:]
            else:
                self.registry._apply_update(local_id, safe_fields)
        if self.callback is not None:
            try:
                self.callback(safe_fields)
            except Exception:
                # UI progress callbacks are advisory; they must not break the client callback thread.
                pass

    def bind(self, remote: Any) -> "JobHandle":
        job_id = getattr(remote, "job_id", None)
        if isinstance(job_id, bool) or not isinstance(job_id, (int, str)) or not str(job_id):
            raise TypeError("the v27 client did not return a job handle with an ID")
        local_id = self.registry._register(str(job_id), self.method, self.generation)
        with self._lock:
            self._local_id = local_id
            pending = tuple(self._pending)
            self._pending.clear()
        for fields in pending:
            self.registry._apply_update(local_id, fields)
        return JobHandle(self.registry, local_id, remote)

    def submission_failed(self) -> None:
        with self._lock:
            self._pending.clear()


class JobHandle:
    """Generation-bound local handle; waiting can be cancelled without aborting the remote job."""

    def __init__(self, registry: "JobRegistry", local_id: str, remote: Any):
        self.registry = registry
        self.local_id = local_id
        self.remote = remote
        self.job_id = str(remote.job_id)

    @property
    def state(self) -> str:
        item = self.registry.get(self.local_id)
        return item["state"] if item else "UNKNOWN"

    def wait(self, *, cancelled: Callable[[], bool] | None = None) -> Any:
        event = getattr(self.remote, "event", None)
        result = getattr(self.remote, "result", None)
        if not hasattr(event, "wait") or not callable(result):
            self.registry._finish(self.local_id, state="UNKNOWN", error="the v27 job handle is incomplete")
            raise RuntimeError("the v27 client returned an incomplete job handle")
        while not event.wait(0.1):
            if cancelled is not None and cancelled():
                self.registry._detached(self.local_id)
                raise JobWaitCancelled(f"local wait stopped for job {self.job_id}; the remote job was not aborted")
        try:
            value = result()
        except Exception as exc:
            safe_error = self.registry.sanitize_error(exc)
            self.registry._finish(self.local_id, state=self.state if self.state in TERMINAL_STATES else "UNKNOWN",
                                  error=safe_error)
            raise RuntimeError(safe_error) from None
        self.registry._finish(self.local_id, state="SUCCESS", error=None)
        return value


class JobWaitCancelled(Exception):
    """The caller stopped waiting. The remote job may still be running."""


class JobRegistry:
    """Keep sanitized per-host job state across session replacement and reconcile by remote job ID."""

    def __init__(self, host: str, scrub: Callable[[str], str] | None = None):
        self.host = host
        self._scrub = scrub or (lambda text: text)
        self._lock = threading.RLock()
        self._records: dict[str, _JobRecord] = {}
        self._remote: dict[tuple[str, str], str] = {}
        self._generation = 0

    def set_scrubber(self, scrub: Callable[[str], str] | None) -> None:
        with self._lock:
            self._scrub = scrub or (lambda text: text)

    def sanitize_error(self, error: BaseException | str) -> str:
        return self._scrub(str(error))[:4096]

    def sanitize_value(self, value: Any) -> Any:
        """Return display-safe JSON-shaped job/alert data using the host credential scrubber."""
        return _safe(value, self._scrub)

    def ticket(self, method: str, generation: int,
               callback: Callable[[Mapping[str, Any]], None] | None = None) -> JobTicket:
        return JobTicket(self, method, generation, callback)

    def _register(self, job_id: str, method: str, generation: int) -> str:
        local_id = uuid.uuid4().hex
        item = _JobRecord(local_id, self.host, job_id, method, generation)
        with self._lock:
            self._generation = generation
            self._records[local_id] = item
            self._remote[(job_id, method)] = local_id
            self._event(item, "state", "Job submitted")
        return local_id

    def record_remote(self, *, job_id: str | int, method: str, state: str,
                      generation: int = 0) -> None:
        """Add a remote job row observed during reconciliation (also used by focused service tests)."""
        if isinstance(job_id, bool) or not isinstance(job_id, (int, str)) or not isinstance(method, str) or not method:
            raise ValueError("a job record requires a valid ID and method")
        job_key = str(job_id)
        with self._lock:
            if (job_key, method) in self._remote:
                local_id = self._remote[(job_key, method)]
            else:
                local_id = uuid.uuid4().hex
                self._records[local_id] = _JobRecord(local_id, self.host, job_key, method, generation)
                self._remote[(job_key, method)] = local_id
        self._finish(local_id, state=state)

    def _callback_fields(self, fields: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = {"id", "method", "state", "progress", "logs_excerpt", "error", "description",
                   "time_started", "time_finished"}
        return {key: _safe(fields.get(key), self._scrub, key) for key in allowed if key in fields}

    def _event(self, record: _JobRecord, kind: str, text: str) -> None:
        record.events.append({"at": _now(), "kind": kind, "text": self._scrub(text)[:4096]})
        record.events = record.events[-200:]
        record.updated_at = _now()

    def _apply_update(self, local_id: str, fields: Mapping[str, Any]) -> None:
        with self._lock:
            record = self._records.get(local_id)
            if record is None:
                return
            raw_state = fields.get("state")
            if isinstance(raw_state, str):
                state = raw_state.upper()
                if state not in KNOWN_STATES:
                    state = "UNKNOWN"
                if state != record.state:
                    record.state = state
                    self._event(record, "state", f"Job state: {state}")
            raw_progress = fields.get("progress")
            if isinstance(raw_progress, Mapping):
                percent = raw_progress.get("percent")
                if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
                    percent = None
                description = raw_progress.get("description")
                if not isinstance(description, str):
                    description = None
                progress = {"percent": percent, "description": self._scrub(description)[:1000] if description else None}
                if progress != record.progress:
                    record.progress = progress
                    label = "progress unavailable" if percent is None else f"Progress {percent:g}%"
                    if description:
                        label += f": {description}"
                    self._event(record, "progress", label)
            log = fields.get("logs_excerpt")
            if isinstance(log, str):
                log = self._scrub(log)[:4096]
                if log != record.logs_excerpt:
                    record.logs_excerpt = log
                    self._event(record, "log", log)
            error = fields.get("error")
            if isinstance(error, str) and error:
                error = self._scrub(error)[:4096]
                if error != record.error:
                    record.error = error
                    self._event(record, "error", error)
            record.reconciliation = "live"

    def _finish(self, local_id: str, *, state: str, error: str | None = None) -> None:
        fields: dict[str, Any] = {"state": state}
        if error is not None:
            fields["error"] = error
        self._apply_update(local_id, fields)

    def _detached(self, local_id: str) -> None:
        with self._lock:
            record = self._records.get(local_id)
            if record is None:
                return
            if record.state not in TERMINAL_STATES:
                record.reconciliation = "pending"
                self._event(record, "state", "Local wait stopped; remote job was left running")

    def add_event(self, local_id: str | None, kind: str, text: str) -> None:
        if local_id is None:
            return
        with self._lock:
            record = self._records.get(local_id)
            if record is not None:
                self._event(record, kind, text)

    def get(self, local_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(local_id)
            return None if record is None else self._public(record)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(record) for record in self._records.values()]

    @staticmethod
    def _public(record: _JobRecord) -> dict[str, Any]:
        return {"local_id": record.local_id, "host": record.host, "job_id": record.job_id,
                "method": record.method, "generation": record.generation, "state": record.state,
                "progress": dict(record.progress), "logs_excerpt": record.logs_excerpt, "error": record.error,
                "created_at": record.created_at, "updated_at": record.updated_at,
                "reconciliation": record.reconciliation, "events": [dict(event) for event in record.events]}

    def mark_disconnected(self) -> None:
        with self._lock:
            for record in self._records.values():
                if record.state not in TERMINAL_STATES:
                    record.state = "UNKNOWN"
                    record.reconciliation = "pending"
                    record.error = "Connection ended before the remote job outcome was observed."
                    self._event(record, "state", "Connection ended; reconnection check is pending")

    def reconcile(self, rpc: Any) -> None:
        with self._lock:
            pending = [record for record in self._records.values()
                       if record.reconciliation == "pending" or record.state == "UNKNOWN"]
        if not pending:
            return
        options = {"limit": 10000, "offset": 0, "order_by": ["-id"],
                   "select": ["id", "method", "state", "progress", "logs_excerpt", "error", "description",
                              "time_started", "time_finished"]}
        try:
            rows = rpc.call("core.get_jobs", [], options)
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise ValueError("core.get_jobs returned an invalid job list")
            available: dict[tuple[str, str], Mapping[str, Any]] = {}
            for row in rows:
                ident, method, state = row.get("id"), row.get("method"), row.get("state")
                if (isinstance(ident, bool) or not isinstance(ident, int) or not isinstance(method, str)
                        or not method or not isinstance(state, str)):
                    raise ValueError("core.get_jobs returned a row without a valid ID, method, and state")
                available[(str(ident), method)] = row
        except Exception as exc:
            safe_error = self._scrub(str(exc))[:1000]
            with self._lock:
                for record in pending:
                    record.state = "UNKNOWN"
                    record.reconciliation = "unavailable"
                    record.error = f"Job reconciliation unavailable: {safe_error}"
                    self._event(record, "error", record.error)
            return
        with self._lock:
            for record in pending:
                row = available.get((record.job_id, record.method))
                if row is None:
                    record.state = "UNKNOWN"
                    record.reconciliation = "not-visible"
                    record.error = "The reconnected session cannot establish this job's outcome; its ID is not visible in the current job query."
                    self._event(record, "error", record.error)
                else:
                    self._apply_update(record.local_id, self._callback_fields(row))
                    record.reconciliation = "matched"

