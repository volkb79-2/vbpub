"""Restartable named-job campaign scheduling and deterministic collection."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .contracts import ContractError, is_candidate_id
from .plan import replan_pending
from .runner import JobSpec, RunnerJobs, parse_progress, validate_job_spec
from .transport import HostUnavailable, Transport, TransportError


STATE_SCHEMA = "fleet-campaign-state/2"
RESULT_SCHEMA = "fleet-campaign-result/1"
STALL_FLOOR_SECONDS = 15 * 60
LOST_AFTER_SECONDS = 30 * 60
_CAMPAIGN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
_EXEC_COMMIT = re.compile(r"[0-9a-f]{40,64}\Z")
_PROJECT_PART = re.compile(r"[A-Za-z0-9._-]+\Z")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(plan)).hexdigest()


def _safe_campaign(value: object) -> str:
    if not isinstance(value, str) or _CAMPAIGN_ID.fullmatch(value) is None or value in {".", ".."}:
        raise ContractError("campaign id: expected a safe name of at most 120 letters, digits, '.', '_' or '-' characters")
    return value


def _system_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ContractError("controller boot id is unavailable; persistent campaign clocks cannot be made restart-safe") from exc
    if re.fullmatch(r"[0-9a-f-]{36}", value) is None:
        raise ContractError("controller boot id has an invalid format")
    return value


def _execution(plan: Mapping[str, Any]) -> dict[str, str]:
    value = plan.get("execution")
    if not isinstance(value, dict) or value.get("schema") != "fleet-plan-execution/1":
        raise ContractError("plan execution context is missing; generate the plan from a clean Git project")
    commit = value.get("commit")
    project = value.get("project")
    if not isinstance(commit, str) or _EXEC_COMMIT.fullmatch(commit) is None:
        raise ContractError("plan execution.commit: expected a full lowercase Git commit id")
    if (
        not isinstance(project, str)
        or project.startswith("/")
        or ".." in project
        or (project != "." and any(
            not part or part in {".", ".."} or _PROJECT_PART.fullmatch(part) is None
            for part in project.split("/")
        ))
    ):
        raise ContractError("plan execution.project: expected a relative project path")
    return {"commit": commit, "project": project or "."}


def _job_spec(job: Mapping[str, Any]) -> JobSpec:
    spec = JobSpec(
        campaign=str(job["campaign"]),
        host=str(job["host"]),
        slot=job["slot"],
        batch=job["batch"],
        candidate_ids=tuple(job["candidate_ids"]),
        commit=str(job["commit"]),
        project=str(job["project"]),
        lane=str(job["lane"]),
        jobs=job["jobs"],
    )
    validate_job_spec(spec)
    return spec


def _event_key(event: Mapping[str, object], host: str, job: str) -> tuple[float, str, str]:
    emitted = event.get("emitted_at")
    try:
        when = datetime.fromisoformat(str(emitted).replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        when = math.inf
    return when, host, job


class CampaignManager:
    """Own one campaign state file and drive runner jobs through a transport."""

    def __init__(
        self,
        state_dir: Path,
        transport: Transport,
        *,
        clock: Callable[[], float] | None = None,
        utc_clock: Callable[[], float] | None = None,
        boot_id: Callable[[], str] | None = None,
        transport_timeout: float = 30.0,
    ):
        self.state_dir = Path(state_dir)
        self.clock = clock or time.monotonic
        self.utc_clock = utc_clock or time.time
        self.boot_id = boot_id or _system_boot_id
        self.runner = RunnerJobs(transport, timeout=transport_timeout)

    @contextlib.contextmanager
    def _locked(self, campaign: str) -> Iterator[None]:
        """Hold the campaign's local lock for one whole read/modify/write command."""
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.state_dir / f"{campaign}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ContractError(f"campaign {campaign} is busy: another fleet command holds its state lock") from exc
            yield
        finally:
            os.close(descriptor)

    def run(self, campaign: str, plan: Mapping[str, Any]) -> dict[str, Any]:
        campaign = _safe_campaign(campaign)
        with self._locked(campaign):
            path = self._state_path(campaign)
            if path.exists():
                state = self._load_state(campaign)
                self._reconcile(state, plan)
            else:
                state = self._new_state(campaign, plan)
                self._write_state(state)
            self._refresh(state)
            self._requeue_exited(state)
            self._handle_lost_hosts(state)
            self._retry_deferred(state)
            self._start_available(state)
            self._write_state(state)
            return self.status_document(state)

    def status(self, campaign: str) -> dict[str, Any]:
        campaign = _safe_campaign(campaign)
        with self._locked(campaign):
            state = self._load_state(campaign)
            self._refresh(state)
            self._write_state(state)
            return self.status_document(state)

    def tick(self, campaign: str) -> dict[str, Any]:
        campaign = _safe_campaign(campaign)
        with self._locked(campaign):
            state = self._load_state(campaign)
            self._refresh(state)
            self._handle_lost_hosts(state)
            self._retry_deferred(state)
            self._detect_stalls(state)
            self._requeue_exited(state)
            self._stop_superseded(state)
            self._start_available(state)
            self._write_state(state)
            return self.status_document(state)

    def collect(self, campaign: str) -> tuple[dict[str, Any], str, Path, Path]:
        campaign = _safe_campaign(campaign)
        with self._locked(campaign):
            state = self._load_state(campaign)
            self._refresh(state)
            result = self._collect_result(state)
            json_path = self.state_dir / f"{campaign}.result.json"
            report_path = self.state_dir / f"{campaign}.report.md"
            _atomic_write(json_path, _canonical(result))
            _atomic_write(report_path, self._render_report(result).encode("utf-8"))
            self._write_state(state)
            return result, report_path.read_text(encoding="utf-8"), json_path, report_path

    def _state_path(self, campaign: str) -> Path:
        return self.state_dir / f"{campaign}.json"

    def _new_state(self, campaign: str, plan: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_plan(plan)
        execution = _execution(plan)
        now = self.clock()
        candidate_metadata: dict[str, dict[str, Any]] = {}
        unplaced_reasons: dict[str, str] = {}
        for host in plan["hosts"]:
            for candidate in host["assigned"]:
                candidate_metadata[candidate["id"]] = dict(candidate)
        for candidate in plan.get("unplaced", []):
            unplaced_reasons[candidate["id"]] = candidate["reason"]
            candidate_metadata.setdefault(candidate["id"], {"id": candidate["id"], "reason": candidate["reason"]})
        for candidate in plan.get("terminal", []):
            unplaced_reasons[candidate["id"]] = candidate["reason"]
            candidate_metadata.setdefault(candidate["id"], {"id": candidate["id"], "reason": candidate["reason"]})
        hosts: dict[str, dict[str, Any]] = {}
        jobs: dict[str, dict[str, Any]] = {}
        for host in plan["hosts"]:
            host_id = host["id"]
            hosts[host_id] = {
                "status": "UNKNOWN",
                "error": None,
                "capacity_alone_bytes": host.get("cap_alone_bytes", 0),
                "inflight_limit": max(1, host.get("campaign_slots", 1)),
                "last_contact_mono": None,
                "unreachable_since_mono": None,
                "loss_requeued": False,
                "paused_seconds": 0.0,
                "last_progress_mono": now,
            }
            for batch in host.get("batches", []):
                ids = list(batch.get("candidate_ids", []))
                if not ids:
                    continue
                workers = 1 if batch.get("kind") == "retry" else min(host["worker_slots"], len(ids))
                spec = JobSpec(
                    campaign=campaign,
                    host=host_id,
                    slot=0,
                    batch=batch["batch"],
                    candidate_ids=tuple(ids),
                    commit=execution["commit"],
                    project=execution["project"],
                    lane=plan["lane"],
                    jobs=max(1, workers),
                )
                jobs[spec.name] = self._job_record(spec, now, batch.get("kind", "regular"))
        expected_ids = sorted(candidate_metadata)
        state = {
            "schema": STATE_SCHEMA,
            "clock_boot_id": self.boot_id(),
            "campaign": campaign,
            "plan": dict(plan),
            "plan_sha256": _plan_fingerprint(plan),
            "execution": execution,
            "lane": plan["lane"],
            "started_mono": now,
            "updated_mono": now,
            "planned_ids": expected_ids,
            "candidate_metadata": candidate_metadata,
            "unplaced_reasons": unplaced_reasons,
            "hosts": hosts,
            "jobs": jobs,
            "deferred_requeues": [],
            "warnings": [],
        }
        return state

    @staticmethod
    def _job_record(spec: JobSpec, now: float, kind: str, *, reason: str | None = None) -> dict[str, Any]:
        return {
            "name": spec.name,
            "campaign": spec.campaign,
            "host": spec.host,
            "slot": spec.slot,
            "batch": spec.batch,
            "candidate_ids": list(spec.sorted_candidate_ids),
            "list_digest": spec.list_digest,
            "list8": spec.list8,
            "commit": spec.commit,
            "project": spec.project,
            "lane": spec.lane,
            "jobs": spec.jobs,
            "kind": kind,
            "status": "QUEUED",
            "created_mono": now,
            "started_mono": None,
            "last_progress_mono": None,
            "paused_seconds": 0.0,
            "progress_mtime": None,
            "progress_path": None,
            "progress_offset": None,
            "progress_end_offset": None,
            "verdict_path": f"campaigns/{spec.runner_campaign}/verdict.json",
            "progress_count": 0,
            "records": [],
            "not_started_ids": [],
            "baseline_seconds": None,
            "exit_code": None,
            "failure_reason": None,
            "requeued": False,
            "stop_pending": False,
            "requeue_reason": reason,
        }

    def _validate_plan(self, plan: Mapping[str, Any]) -> None:
        if not isinstance(plan, Mapping) or plan.get("schema") != "fleet-plan/1":
            raise ContractError("plan: expected fleet-plan/1")
        _execution(plan)
        if not isinstance(plan.get("lane"), str) or not plan["lane"]:
            raise ContractError("plan.lane: expected a non-empty string")
        hosts = plan.get("hosts")
        if not isinstance(hosts, list):
            raise ContractError("plan.hosts: expected an array")
        seen_hosts: set[str] = set()
        seen_ids: set[str] = set()
        all_assigned: set[str] = set()
        for host_index, host in enumerate(hosts):
            where = f"plan.hosts[{host_index}]"
            if not isinstance(host, dict) or not isinstance(host.get("id"), str):
                raise ContractError(f"{where}: expected an object with an id")
            host_id = host["id"]
            if not _CAMPAIGN_ID.fullmatch(host_id) or host_id in {".", ".."}:
                raise ContractError(f"{where}.id: unsafe host id")
            if host_id in seen_hosts:
                raise ContractError("plan.hosts: duplicate host id")
            seen_hosts.add(host_id)
            assigned = host.get("assigned", [])
            if not isinstance(assigned, list):
                raise ContractError(f"{where}.assigned: expected an array")
            assigned_ids: set[str] = set()
            for candidate_index, candidate in enumerate(assigned):
                if not isinstance(candidate, dict) or not is_candidate_id(candidate.get("id")):
                    raise ContractError(f"{where}.assigned[{candidate_index}]: invalid candidate id")
                candidate_id = candidate["id"]
                if candidate_id in assigned_ids or candidate_id in all_assigned:
                    raise ContractError(f"plan: candidate {candidate_id!r} is assigned more than once")
                assigned_ids.add(candidate_id)
                all_assigned.add(candidate_id)
            batches = host.get("batches", [])
            if not isinstance(batches, list):
                raise ContractError(f"{where}.batches: expected an array")
            for batch_index, batch in enumerate(batches):
                if not isinstance(batch, dict) or not isinstance(batch.get("candidate_ids"), list):
                    raise ContractError(f"{where}.batches[{batch_index}]: expected candidate_ids array")
                if isinstance(batch.get("batch"), bool) or not isinstance(batch.get("batch"), int) or batch["batch"] < 0:
                    raise ContractError(f"{where}.batches[{batch_index}].batch: expected a non-negative integer")
                for candidate_id in batch["candidate_ids"]:
                    if not is_candidate_id(candidate_id):
                        raise ContractError(f"{where}.batches[{batch_index}]: invalid candidate id")
                    if candidate_id not in assigned_ids:
                        raise ContractError(f"{where}.batches[{batch_index}]: candidate is absent from assigned")
                    if candidate_id in seen_ids:
                        raise ContractError(f"plan: candidate {candidate_id!r} is assigned more than once")
                    seen_ids.add(candidate_id)
            if not assigned_ids <= seen_ids:
                raise ContractError(f"{where}.batches: assigned candidates are missing from batches")
            if host.get("status") == "available":
                workers = host.get("worker_slots")
                if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
                    raise ContractError(f"{where}.worker_slots: expected a positive integer")
        unplaced = plan.get("unplaced", [])
        terminal = plan.get("terminal", [])
        if not isinstance(unplaced, list) or not isinstance(terminal, list):
            raise ContractError("plan unplaced/terminal: expected arrays")
        for row in unplaced + terminal:
            if not isinstance(row, dict) or not is_candidate_id(row.get("id")):
                raise ContractError("plan unplaced/terminal: invalid candidate id")
            if row["id"] in seen_ids:
                raise ContractError(f"plan: candidate {row['id']!r} is both assigned and unplaced")
            seen_ids.add(row["id"])

    def _load_state(self, campaign: str) -> dict[str, Any]:
        path = self._state_path(campaign)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ContractError(f"campaign state does not exist: {campaign}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ContractError(f"campaign state is not valid UTF-8 JSON: {campaign}") from exc
        if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA:
            raise ContractError(f"campaign state has an unsupported schema: {campaign}")
        if value.get("campaign") != campaign or not isinstance(value.get("jobs"), dict) or not isinstance(value.get("hosts"), dict):
            raise ContractError(f"campaign state identity or tables are invalid: {campaign}")
        self._rebase_after_restart(value)
        return value

    def _write_state(self, state: dict[str, Any]) -> None:
        now_mono = self.clock()
        now_utc = self.utc_clock()
        state["updated_mono"] = now_mono
        state["updated_utc"] = now_utc
        state["clock_boot_id"] = self.boot_id()
        self._sync_utc_timestamps(state, now_mono, now_utc)
        _atomic_write(self._state_path(state["campaign"]), _canonical(state))

    def _rebase_after_restart(self, state: dict[str, Any]) -> None:
        current_boot = self.boot_id()
        if state.get("clock_boot_id") == current_boot:
            return
        now_mono = self.clock()
        now_utc = self.utc_clock()

        def rebase(value: object) -> None:
            if isinstance(value, dict):
                for key, item in list(value.items()):
                    if key.endswith("_mono") and isinstance(item, (int, float)) and not isinstance(item, bool):
                        utc_value = value.get(key[:-5] + "_utc")
                        if isinstance(utc_value, (int, float)) and not isinstance(utc_value, bool):
                            # A backward UTC step pauses fallback age; it can delay a
                            # detector but cannot make a deadline fire early.
                            elapsed = max(0.0, now_utc - float(utc_value))
                            value[key] = now_mono - elapsed
                    else:
                        rebase(item)

        rebase(state)
        state["clock_boot_id"] = current_boot

    @staticmethod
    def _sync_utc_timestamps(value: object, now_mono: float, now_utc: float) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key.endswith("_mono") and isinstance(item, (int, float)) and not isinstance(item, bool):
                    elapsed = max(0.0, now_mono - float(item))
                    value[key[:-5] + "_utc"] = now_utc - elapsed
                else:
                    CampaignManager._sync_utc_timestamps(item, now_mono, now_utc)

    def _reconcile(self, state: dict[str, Any], plan: Mapping[str, Any]) -> None:
        self._validate_plan(plan)
        execution = _execution(plan)
        if execution != state["execution"] or plan.get("lane") != state["lane"]:
            raise ContractError("campaign id is bound to its original commit, project, and lane; choose a new campaign id")
        fingerprint = _plan_fingerprint(plan)
        if fingerprint == state["plan_sha256"]:
            return
        now = self.clock()
        desired = self._new_state(state["campaign"], plan)
        desired_names = set(desired["jobs"])
        old_names = set(state["jobs"])
        for name in sorted(old_names - desired_names):
            old = state["jobs"][name]
            if old["status"] in {"RUNNING", "STARTING", "UNKNOWN", "QUEUED"}:
                prior_status = old["status"]
                old["status"] = "SUPERSEDED_PENDING" if prior_status != "QUEUED" else "SUPERSEDED"
                old["stop_pending"] = prior_status != "QUEUED"
                old["superseded_mono"] = now
        for name, row in desired["jobs"].items():
            if name not in state["jobs"]:
                state["jobs"][name] = row
        current_host_ids = set(desired["hosts"])
        for host_id, host in desired["hosts"].items():
            if host_id not in state["hosts"]:
                state["hosts"][host_id] = host
            else:
                state["hosts"][host_id]["capacity_alone_bytes"] = host["capacity_alone_bytes"]
                state["hosts"][host_id]["inflight_limit"] = host["inflight_limit"]
        for host_id in set(state["hosts"]) - current_host_ids:
            state["hosts"][host_id]["status"] = "REMOVED_FROM_PLAN"
        state["plan"] = dict(plan)
        state["plan_sha256"] = fingerprint
        state["planned_ids"] = desired["planned_ids"]
        state["candidate_metadata"] = desired["candidate_metadata"]
        state["unplaced_reasons"] = desired["unplaced_reasons"]
        state["deferred_requeues"] = []
        state["warnings"].append("plan changed; obsolete named jobs will be stopped before replacement batches start")
        state["reconciled_mono"] = now

    def _refresh(self, state: dict[str, Any]) -> None:
        now = self.clock()
        available = {row["id"] for row in state["plan"]["hosts"] if row.get("status") == "available"}
        for host_id in sorted(state["hosts"]):
            host_state = state["hosts"][host_id]
            job_rows = {
                name: job for name, job in state["jobs"].items()
                if job["host"] == host_id and job["status"] != "SUPERSEDED"
            }
            if host_state["status"] == "REMOVED_FROM_PLAN":
                continue
            if not job_rows and host_id not in available:
                # A host the planner refused (shared-prod, unknown role, PSI) and
                # that owns no job of this campaign is never contacted.
                continue
            try:
                self._refresh_host(state, host_id, host_state, job_rows, now)
            except HostUnavailable:
                if host_state["unreachable_since_mono"] is None:
                    host_state["unreachable_since_mono"] = host_state["last_contact_mono"]
                    if host_state["unreachable_since_mono"] is None:
                        host_state["unreachable_since_mono"] = now
                host_state["status"] = "UNREACHABLE"
                if now - host_state["unreachable_since_mono"] > LOST_AFTER_SECONDS:
                    host_state["status"] = "LOST"
                continue
            except (ContractError, TransportError) as exc:
                self._record_host_error(host_state, str(exc))

    def _refresh_host(
        self,
        state: dict[str, Any],
        host_id: str,
        host_state: dict[str, Any],
        job_rows: Mapping[str, dict[str, Any]],
        now: float,
    ) -> None:
        wanted = {name: _job_spec(job) for name, job in job_rows.items()}
        rows = self.runner.probe(host_id, wanted)
        prior_status = host_state["status"]
        returned_lost = (
            host_state["unreachable_since_mono"] is not None
            and now - host_state["unreachable_since_mono"] > LOST_AFTER_SECONDS
        ) or prior_status == "LOST"
        if host_state["unreachable_since_mono"] is not None:
            pause = max(0.0, now - host_state["unreachable_since_mono"])
            host_state["paused_seconds"] += pause
            for job in job_rows.values():
                if job["status"] in {"RUNNING", "STARTING", "UNKNOWN", "LOST"}:
                    job["paused_seconds"] += pause
            host_state["unreachable_since_mono"] = None
        host_state["last_contact_mono"] = now
        host_state["status"] = "ONLINE"
        host_state.pop("error", None)
        if returned_lost:
            host_state["loss_requeued"] = False
            host_state["returned_after_loss"] = True
        for name, job in job_rows.items():
            row = rows[name]
            state_name = row.get("state")
            superseded_pending = job["status"] == "SUPERSEDED_PENDING"
            was_unstarted = job["status"] in {"QUEUED", "STARTING"}
            digest = row.get("list_digest")
            if state_name != "MISSING" and (
                not isinstance(digest, str) or digest not in {job["list_digest"], job["list8"]}
            ):
                raise ContractError(f"named job {name} has a different candidate-list digest")
            if state_name == "MISSING":
                if superseded_pending:
                    job["status"] = "SUPERSEDED"
                    job["stop_pending"] = False
                    continue
                if job["status"] in {"STARTING", "RUNNING", "UNKNOWN", "LOST"}:
                    job["status"] = "EXIT"
                    job["missing_job"] = True
                    job["ended_mono"] = now
                    if not job.get("finished_ids"):
                        job["status"] = "FAILED"
                        job["failure_reason"] = "missing_job (exit code unavailable)"
                continue
            old_finished = set(job.get("finished_ids", []))
            records = row.get("records", [])
            verdict_records = row.get("verdict_records", []) if state_name == "EXIT" else []
            if not isinstance(records, list):
                raise TransportError(f"probe row for {name} has an invalid records field")
            if not isinstance(verdict_records, list):
                raise TransportError(f"probe row for {name} has an invalid verdict records field")
            for field in ("progress_offset", "progress_end_offset"):
                value = row.get(field, job.get(field))
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise TransportError(f"probe row for {name} has an invalid {field}")
                job[field] = value
            baseline_seconds = row.get("baseline_seconds")
            if baseline_seconds is not None:
                if (
                    isinstance(baseline_seconds, bool)
                    or not isinstance(baseline_seconds, (int, float))
                    or not math.isfinite(float(baseline_seconds))
                    or baseline_seconds < 0
                ):
                    raise TransportError(f"probe row for {name} has an invalid baseline time")
                job["baseline_seconds"] = float(baseline_seconds)
            allowed = set(job["candidate_ids"])
            all_records = job.setdefault("records", [])
            record_keys = {
                (item.get("candidate_id"), item.get("outcome_bucket"), item.get("emitted_at"), item.get("elapsed_seconds"))
                for item in all_records if isinstance(item, dict)
            }
            not_started_ids = set(job.get("not_started_ids", []))
            for event in [*records, *verdict_records]:
                if not isinstance(event, dict):
                    continue
                candidate_id = event.get("candidate_id")
                if not isinstance(candidate_id, str) or candidate_id not in allowed:
                    continue
                if not is_candidate_id(candidate_id):
                    raise TransportError(f"probe row for {name} has an invalid candidate id")
                if event.get("not_started") is True:
                    not_started_ids.add(candidate_id)
                    continue
                outcome = event.get("outcome_bucket")
                if not isinstance(outcome, str) or outcome not in {
                    "killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent"
                }:
                    continue
                elapsed = event.get("elapsed_seconds")
                if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(float(elapsed)) or elapsed < 0:
                    elapsed = None
                emitted = event.get("emitted_at") if isinstance(event.get("emitted_at"), str) else None
                record = {
                    "candidate_id": candidate_id,
                    "outcome_bucket": outcome,
                    "elapsed_seconds": elapsed,
                    "emitted_at": emitted,
                }
                key = (candidate_id, outcome, emitted, elapsed)
                if key not in record_keys:
                    record_keys.add(key)
                    all_records.append(record)
            job["not_started_ids"] = sorted(not_started_ids)
            finished = {record["candidate_id"] for record in all_records if isinstance(record, dict)}
            job["finished_ids"] = sorted(finished)
            job["progress_count"] = len(finished)
            job["progress_mtime"] = row.get("progress_mtime")
            if row.get("progress_path") is not None:
                job["progress_path"] = row["progress_path"]
            if row.get("verdict_path") is not None:
                job["verdict_path"] = row["verdict_path"]
            new_finished = finished - old_finished
            if new_finished:
                job["last_progress_mono"] = now
                job["paused_seconds"] = 0.0
                host_state["last_progress_mono"] = now
            if state_name == "RUNNING":
                if was_unstarted:
                    job["adopted"] = True
                if job["started_mono"] is None:
                    job["started_mono"] = now
                if job["last_progress_mono"] is None:
                    job["last_progress_mono"] = now
                job["status"] = "RUNNING"
            elif state_name == "EXIT":
                job["exit_code"] = row.get("exit_code")
                job["ended_mono"] = job.get("ended_mono", now)
                if job["status"] not in {"STALLED", "FAILED"}:
                    if set(job["candidate_ids"]) <= finished:
                        job["status"] = "COMPLETE"
                    elif not finished:
                        job["status"] = "FAILED"
                        job["failure_reason"] = f"exit code {job['exit_code']}"
                    else:
                        job["status"] = "EXIT"
            elif state_name == "UNKNOWN":
                job["status"] = "UNKNOWN"
                if job["started_mono"] is None:
                    job["started_mono"] = now
                if job["last_progress_mono"] is None:
                    job["last_progress_mono"] = now
            else:
                raise TransportError(f"probe row for {name} has unsupported job state {state_name!r}")
            if superseded_pending:
                if state_name in {"EXIT", "MISSING"}:
                    job["status"] = "SUPERSEDED"
                    job["stop_pending"] = False
                else:
                    job["status"] = "SUPERSEDED_PENDING"
        if prior_status == "LOST":
            self._stop_returned_duplicates(state, host_id)
        self._stop_superseded_host(state, host_id)

    @staticmethod
    def _record_host_error(host_state: dict[str, Any], message: str) -> None:
        host_state["status"] = "ERROR"
        host_state["error"] = message

    def _stop_superseded_host(self, state: dict[str, Any], host_id: str) -> None:
        for job in state["jobs"].values():
            if job["host"] != host_id or job["status"] != "SUPERSEDED_PENDING":
                continue
            if not job["stop_pending"]:
                job["status"] = "SUPERSEDED"
                continue
            try:
                self.runner.stop(host_id, job["name"])
            except HostUnavailable:
                state["hosts"][host_id]["status"] = "UNREACHABLE"
                if state["hosts"][host_id]["unreachable_since_mono"] is None:
                    state["hosts"][host_id]["unreachable_since_mono"] = state["hosts"][host_id]["last_contact_mono"]
                continue
            except (TransportError, ContractError) as exc:
                self._record_host_error(state["hosts"][host_id], str(exc))
                continue
            job["stop_pending"] = False
            job["status"] = "SUPERSEDED"

    def _stop_superseded(self, state: dict[str, Any]) -> None:
        for host_id in sorted(state["hosts"]):
            self._stop_superseded_host(state, host_id)

    def _stop_returned_duplicates(self, state: dict[str, Any], host_id: str) -> None:
        done_elsewhere = self._done_by_other_hosts(state, host_id)
        for job in state["jobs"].values():
            if job["host"] != host_id or job["status"] != "RUNNING":
                continue
            if set(job["candidate_ids"]) <= done_elsewhere:
                try:
                    self.runner.stop(host_id, job["name"])
                except HostUnavailable:
                    state["hosts"][host_id]["status"] = "UNREACHABLE"
                    continue
                except (TransportError, ContractError) as exc:
                    self._record_host_error(state["hosts"][host_id], str(exc))
                    continue
                job["status"] = "SUPERSEDED"
                job["stop_pending"] = False

    @staticmethod
    def _done_ids(state: Mapping[str, Any]) -> set[str]:
        return {
            record["candidate_id"]
            for job in state["jobs"].values()
            for record in job.get("records", [])
            if isinstance(record, dict) and isinstance(record.get("candidate_id"), str)
        }

    def _done_by_other_hosts(self, state: Mapping[str, Any], host_id: str) -> set[str]:
        return {
            record["candidate_id"]
            for job in state["jobs"].values()
            if job["host"] != host_id
            for record in job.get("records", [])
            if isinstance(record, dict) and isinstance(record.get("candidate_id"), str)
        }

    def _inflight_ids(self, state: Mapping[str, Any], *, exclude_host: str | None = None) -> set[str]:
        return {
            candidate_id
            for job in state["jobs"].values()
            if job["host"] != exclude_host and job["status"] in {"QUEUED", "STARTING", "RUNNING", "UNKNOWN"}
            for candidate_id in job["candidate_ids"]
        }

    def _handle_lost_hosts(self, state: dict[str, Any]) -> None:
        for host_id in sorted(state["hosts"]):
            host_state = state["hosts"][host_id]
            if host_state["status"] != "LOST" or host_state["loss_requeued"]:
                continue
            host_state["loss_requeued"] = True
            done = self._done_ids(state)
            inflight = self._inflight_ids(state, exclude_host=host_id)
            pending_by_job: list[tuple[dict[str, Any], list[str]]] = []
            for job in state["jobs"].values():
                if job["host"] != host_id or job["status"] not in {"QUEUED", "RUNNING", "STARTING", "UNKNOWN"}:
                    continue
                pending = [candidate_id for candidate_id in job["candidate_ids"] if candidate_id not in done and candidate_id not in inflight]
                job["status"] = "LOST"
                job["lost_mono"] = self.clock()
                pending_by_job.append((job, pending))
            for old_job, candidate_ids in pending_by_job:
                if candidate_ids:
                    target = self._largest_available_host(state, excluding={host_id})
                    if target is None:
                        state["deferred_requeues"].append({
                            "source_job": old_job["name"],
                            "candidate_ids": candidate_ids,
                            "reason": "lost host has no reachable planner target yet",
                        })
                    else:
                        self._enqueue_replanned(state, old_job, candidate_ids, target, "host-lost")
            if host_state["unreachable_since_mono"] is None:
                host_state["status"] = "ONLINE"

    def _largest_available_host(self, state: Mapping[str, Any], *, excluding: set[str]) -> str | None:
        candidates = [
            (host["capacity_alone_bytes"], host_id)
            for host_id, host in state["hosts"].items()
            if host_id not in excluding and host["status"] == "ONLINE"
            and host_id in {row["id"] for row in state["plan"]["hosts"] if row.get("status") == "available"}
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: (row[0], row[1]))[1]

    def _retry_deferred(self, state: dict[str, Any]) -> None:
        still_deferred = []
        for item in state["deferred_requeues"]:
            source_job = state["jobs"].get(item["source_job"])
            if source_job is None:
                continue
            target = self._largest_available_host(state, excluding={source_job["host"]})
            if target is None:
                still_deferred.append(item)
                continue
            done = self._done_ids(state)
            inflight = self._inflight_ids(state, exclude_host=source_job["host"])
            pending = [candidate_id for candidate_id in item["candidate_ids"] if candidate_id not in done and candidate_id not in inflight]
            if pending:
                self._enqueue_replanned(state, source_job, pending, target, "host-lost")
        state["deferred_requeues"] = still_deferred

    def _detect_stalls(self, state: dict[str, Any]) -> None:
        now = self.clock()
        for job in list(state["jobs"].values()):
            if job["status"] != "RUNNING":
                continue
            if set(job["candidate_ids"]) <= self._done_ids(state):
                continue
            host_state = state["hosts"][job["host"]]
            if host_state["status"] != "ONLINE" or job["last_progress_mono"] is None:
                continue
            threshold = self._stall_threshold(state, job)
            age = max(0.0, now - job["last_progress_mono"] - job["paused_seconds"])
            if age < threshold:
                continue
            try:
                self.runner.stop(job["host"], job["name"])
            except HostUnavailable:
                host_state["status"] = "UNREACHABLE"
                host_state["unreachable_since_mono"] = host_state["last_contact_mono"] or now
                continue
            except (TransportError, ContractError) as exc:
                self._record_host_error(host_state, str(exc))
                continue
            job["status"] = "STALLED"
            job["stalled_mono"] = now
            job["stall_after_seconds"] = threshold
            done = self._done_ids(state)
            pending = [candidate_id for candidate_id in job["candidate_ids"] if candidate_id not in done]
            if not job.get("finished_ids"):
                job["status"] = "FAILED"
                job["exit_code"] = job.get("exit_code") if job.get("exit_code") is not None else 143
                job["failure_reason"] = f"exit code {job['exit_code']}"
                job["ended_mono"] = now
                job["requeued"] = True
            elif pending:
                self._enqueue_replanned(state, job, pending, job["host"], "stalled")

    @staticmethod
    def _p95(values: Sequence[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index]

    @staticmethod
    def _predicted_candidate_seconds(state: Mapping[str, Any], job: Mapping[str, Any]) -> list[float]:
        plan = state["plan"]
        time_reference = plan.get("time_reference", {})
        reference_baseline = time_reference.get("baseline_score") if isinstance(time_reference, dict) else None
        reference_major = time_reference.get("geekbench_major") if isinstance(time_reference, dict) else None
        reference_version = time_reference.get("geekbench_version") if isinstance(time_reference, dict) else None
        host_plan = next((row for row in plan["hosts"] if row["id"] == job["host"]), {})
        speed = host_plan.get("speed")
        comparable = (
            isinstance(speed, dict)
            and isinstance(reference_baseline, (int, float))
            and not isinstance(reference_baseline, bool)
            and speed.get("geekbench_major") == reference_major
            and speed.get("geekbench_version") == reference_version
            and speed.get("baseline_score") == reference_baseline
            and isinstance(speed.get("single_core"), (int, float))
            and not isinstance(speed.get("single_core"), bool)
            and speed["single_core"] > 0
        )
        factor = host_plan.get("f_h", 1.0)
        result = []
        for candidate_id in job["candidate_ids"]:
            candidate = state["candidate_metadata"].get(candidate_id, {})
            reference_seconds = candidate.get("time_ref_seconds")
            if (
                isinstance(reference_seconds, bool)
                or not isinstance(reference_seconds, (int, float))
                or not math.isfinite(float(reference_seconds))
                or reference_seconds < 0
            ):
                continue
            predicted = (
                float(reference_seconds) * float(reference_baseline) / float(speed["single_core"]) * float(factor)
                if comparable else float(reference_seconds)
            )
            if math.isfinite(predicted) and predicted >= 0:
                result.append(predicted)
        return result

    @classmethod
    def _stall_threshold(cls, state: Mapping[str, Any], job: Mapping[str, Any]) -> float:
        host_id = job["host"]
        durations = sorted(
            float(record["elapsed_seconds"])
            for job in state["jobs"].values() if job["host"] == host_id
            for record in job.get("records", [])
            if isinstance(record, dict)
            and isinstance(record.get("elapsed_seconds"), (int, float))
            and not isinstance(record.get("elapsed_seconds"), bool)
            and math.isfinite(float(record["elapsed_seconds"]))
            and record["elapsed_seconds"] >= 0
        )
        predicted_p95 = cls._p95(cls._predicted_candidate_seconds(state, job))
        observed_p95 = cls._p95(durations)
        threshold = max(
            float(STALL_FLOOR_SECONDS),
            3.0 * predicted_p95 if predicted_p95 is not None else 0.0,
            3.0 * observed_p95 if observed_p95 is not None else 0.0,
        )
        if not job.get("finished_ids"):
            demand = state["plan"].get("demand", {})
            declared_baseline = demand.get("baseline_seconds") if isinstance(demand, dict) else None
            if (
                isinstance(declared_baseline, (int, float))
                and not isinstance(declared_baseline, bool)
                and math.isfinite(float(declared_baseline))
                and declared_baseline >= 0
            ):
                baseline_seconds = float(declared_baseline)
            else:
                host_baselines = [
                    float(row["baseline_seconds"])
                    for row in state["jobs"].values()
                    if row["host"] == host_id
                    and isinstance(row.get("baseline_seconds"), (int, float))
                    and not isinstance(row.get("baseline_seconds"), bool)
                    and math.isfinite(float(row["baseline_seconds"]))
                    and row["baseline_seconds"] >= 0
                ]
                baseline_seconds = cls._p95(host_baselines) or 0.0
            threshold += baseline_seconds
        return threshold

    def _requeue_exited(self, state: dict[str, Any]) -> None:
        done = self._done_ids(state)
        for job in list(state["jobs"].values()):
            if job["status"] != "EXIT" or job["requeued"]:
                continue
            job["requeued"] = True
            if not job.get("finished_ids"):
                job["status"] = "FAILED"
                if job.get("missing_job"):
                    job["failure_reason"] = "missing_job (exit code unavailable)"
                else:
                    job["failure_reason"] = f"exit code {job.get('exit_code')}"
                job["ended_mono"] = job.get("ended_mono", self.clock())
                continue
            pending = [candidate_id for candidate_id in job["candidate_ids"] if candidate_id not in done]
            inflight = self._inflight_ids(state, exclude_host=job["host"])
            pending = [candidate_id for candidate_id in pending if candidate_id not in inflight]
            if not pending:
                continue
            if state["hosts"][job["host"]]["status"] == "ONLINE":
                self._enqueue_replanned(state, job, pending, job["host"], "job-exit")
            else:
                state["deferred_requeues"].append({
                    "source_job": job["name"], "candidate_ids": pending,
                    "reason": "job exited while its host is unreachable",
                })

    def _enqueue_replanned(
        self,
        state: dict[str, Any],
        old_job: Mapping[str, Any],
        candidate_ids: Sequence[str],
        target_host: str,
        reason: str,
    ) -> None:
        try:
            batches = replan_pending(state["plan"], list(candidate_ids), target_host)
        except ContractError as exc:
            state["warnings"].append(f"could not re-plan unfinished candidates from {old_job['name']}: {exc}")
            return
        next_batch = max(
            (job["batch"] for job in state["jobs"].values() if job["host"] == target_host),
            default=-1,
        ) + 1
        host_row = next(row for row in state["plan"]["hosts"] if row["id"] == target_host)
        for batch in batches:
            ids = tuple(batch["candidate_ids"])
            workers = 1 if batch.get("kind") == "retry" else min(host_row["worker_slots"], len(ids))
            spec = JobSpec(
                campaign=state["campaign"],
                host=target_host,
                slot=0,
                batch=next_batch,
                candidate_ids=ids,
                commit=state["execution"]["commit"],
                project=state["execution"]["project"],
                lane=state["lane"],
                jobs=max(1, workers),
            )
            next_batch += 1
            state["jobs"][spec.name] = self._job_record(spec, self.clock(), batch.get("kind", "regular"), reason=reason)

    def _start_available(self, state: dict[str, Any]) -> None:
        for host_id in sorted(host["id"] for host in state["plan"]["hosts"] if host.get("status") == "available"):
            host_state = state["hosts"][host_id]
            if host_state["status"] not in {"ONLINE", "UNKNOWN"}:
                continue
            limit = host_state["inflight_limit"]
            active = [
                job for job in state["jobs"].values()
                if job["host"] == host_id and job["status"] in {"STARTING", "RUNNING", "UNKNOWN"}
            ]
            free = max(0, limit - len({job["slot"] for job in active}))
            queued = sorted(
                (job for job in state["jobs"].values() if job["host"] == host_id and job["status"] == "QUEUED"),
                key=lambda job: (job["slot"], job["batch"], job["name"]),
            )
            for job in queued:
                if free <= 0 or job["slot"] in {active_job["slot"] for active_job in active}:
                    continue
                job["status"] = "STARTING"
                now = self.clock()
                # A refused start must not leave a stall deadline from the failed
                # attempt behind: restore both clocks so a later start begins fresh.
                previous_clocks = (job["started_mono"], job["last_progress_mono"])
                job["started_mono"] = job["started_mono"] or now
                job["last_progress_mono"] = job["last_progress_mono"] or now
                self._write_state(state)
                try:
                    started = self.runner.start(_job_spec(job))
                except HostUnavailable as exc:
                    job["status"] = "QUEUED"
                    job["started_mono"], job["last_progress_mono"] = previous_clocks
                    host_state["status"] = "UNREACHABLE"
                    host_state["unreachable_since_mono"] = host_state["last_contact_mono"] or now
                    job["start_error"] = str(exc)
                    break
                except (TransportError, ContractError) as exc:
                    job["status"] = "QUEUED"
                    job["started_mono"], job["last_progress_mono"] = previous_clocks
                    job["start_error"] = str(exc)
                    if str(exc) not in state["warnings"]:
                        state["warnings"].append(str(exc))
                    self._record_host_error(host_state, str(exc))
                    break
                job["status"] = "RUNNING"
                job["adopted"] = not started
                host_state["status"] = "ONLINE"
                host_state["last_contact_mono"] = self.clock()
                active.append(job)
                free -= 1
                self._write_state(state)

    def status_document(self, state: Mapping[str, Any]) -> dict[str, Any]:
        now = self.clock()
        result_hosts = []
        for host_plan in state["plan"]["hosts"]:
            host_id = host_plan["id"]
            host_state = state["hosts"][host_id]
            host_jobs = [job for job in state["jobs"].values() if job["host"] == host_id and job["status"] not in {"SUPERSEDED", "SUPERSEDED_PENDING"}]
            rows = []
            for job in sorted(host_jobs, key=lambda item: (item["batch"], item["name"])):
                done = len(set(job.get("finished_ids", [])))
                total = len(job["candidate_ids"])
                elapsed = max(0.0, now - (job["started_mono"] if job["started_mono"] is not None else job["created_mono"]) - job["paused_seconds"])
                rate = done / elapsed if elapsed > 0 else None
                host_total = len(host_plan.get("assigned", []))
                geek_eta = None
                if host_plan.get("eta_seconds") is not None and host_total:
                    geek_eta = max(0.0, float(host_plan["eta_seconds"]) * max(0, total - done) / host_total)
                observed_eta = max(0, total - done) / rate if rate and rate > 0 else None
                eta = self._blend_eta(geek_eta, observed_eta)
                rows.append({
                    "name": job["name"],
                    "slot": job["slot"],
                    "batch": job["batch"],
                    "state": job["status"],
                    "done": done,
                    "total": total,
                    "rate_candidates_per_second": self._round(rate),
                    "eta_seconds": self._round(eta),
                    "stall_after_seconds": job.get("stall_after_seconds"),
                    "progress_mtime": job.get("progress_mtime"),
                    "exit_code": job.get("exit_code"),
                    "reason": job.get("failure_reason"),
                })
            result_hosts.append({
                "host": host_id,
                "state": host_state["status"],
                "error": host_state.get("error"),
                "done": len({candidate_id for job in host_jobs for candidate_id in job.get("finished_ids", [])}),
                "total": len(host_plan.get("assigned", [])),
                "batches": rows,
                "markers": [
                    marker for marker, enabled in (
                        ("LOST", host_state["status"] == "LOST"),
                        ("STALLED", any(job["status"] == "STALLED" for job in host_jobs)),
                    ) if enabled
                ],
            })
        return {
            "schema": "fleet-campaign-status/1",
            "campaign": state["campaign"],
            "lane": state["lane"],
            "hosts": result_hosts,
        }

    @staticmethod
    def _round(value: float | None) -> float | None:
        return None if value is None or not math.isfinite(value) else round(value, 3)

    @staticmethod
    def _blend_eta(geekbench_eta: float | None, observed_eta: float | None) -> float | None:
        if geekbench_eta is not None and observed_eta is not None:
            return (geekbench_eta + observed_eta) / 2
        return geekbench_eta if observed_eta is None else observed_eta

    def _collect_result(self, state: dict[str, Any]) -> dict[str, Any]:
        expected_ids = set(state["planned_ids"])
        events: list[dict[str, Any]] = []
        seen_progress: set[tuple[str, str, str, str | None, float | None]] = set()
        missing_artifacts: list[dict[str, str]] = []
        not_started_reasons: dict[str, str] = {}
        progress_paths: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for job in state["jobs"].values():
            allowed = set(job["candidate_ids"])
            for record in job.get("records", []):
                if not isinstance(record, dict):
                    continue
                candidate_id = record.get("candidate_id")
                outcome = record.get("outcome_bucket")
                if (
                    not isinstance(candidate_id, str)
                    or candidate_id not in expected_ids
                    or candidate_id not in allowed
                    or not isinstance(outcome, str)
                    or outcome not in {
                    "killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent"
                    }
                ):
                    continue
                emitted_at = record.get("emitted_at") if isinstance(record.get("emitted_at"), str) else None
                elapsed = record.get("elapsed_seconds")
                elapsed_seconds = (
                    float(elapsed)
                    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(float(elapsed))
                    else None
                )
                key = (job["host"], candidate_id, outcome, emitted_at, elapsed_seconds)
                if key not in seen_progress:
                    events.append({**record, "host": job["host"], "job": job["name"]})
                    seen_progress.add(key)
            if job["status"] in {"QUEUED", "STARTING"}:
                continue
            path = job.get("progress_path")
            if isinstance(path, str) and path:
                progress_paths.setdefault((job["host"], path), []).append(job)
        for (host, path), jobs in sorted(progress_paths.items()):
            try:
                content = self.runner.get(host, path)
            except (TransportError, HostUnavailable):
                missing_artifacts.append({"host": host, "artifact": "progress", "path": path})
                continue
            for job in sorted(jobs, key=lambda row: row["name"]):
                offset = job.get("progress_offset")
                end_offset = job.get("progress_end_offset")
                if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                    continue
                if end_offset is not None and (
                    isinstance(end_offset, bool) or not isinstance(end_offset, int) or end_offset < offset
                ):
                    continue
                if job["status"] in {"COMPLETE", "EXIT", "FAILED", "STALLED"} and end_offset is None:
                    continue
                for record in parse_progress(
                    content,
                    expected_ids & set(job["candidate_ids"]),
                    start_offset=offset,
                    end_offset=end_offset,
                ):
                    emitted_at = record.get("emitted_at") if isinstance(record.get("emitted_at"), str) else None
                    elapsed = record.get("elapsed_seconds")
                    elapsed_seconds = (
                        float(elapsed)
                        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(float(elapsed))
                        else None
                    )
                    key = (host, record["candidate_id"], record["outcome_bucket"], emitted_at, elapsed_seconds)
                    if key not in seen_progress:
                        events.append({**record, "host": host, "job": job["name"]})
                        seen_progress.add(key)
        for job in sorted(state["jobs"].values(), key=lambda row: row["name"]):
            if job["status"] in {"QUEUED", "STARTING"}:
                continue
            host = job["host"]
            try:
                verdict = self.runner.get(host, job["verdict_path"])
            except (TransportError, HostUnavailable):
                missing_artifacts.append({"host": host, "job": job["name"], "artifact": "verdict", "path": job["verdict_path"]})
                continue
            try:
                verdict_events = self._verdict_records(verdict, set(job["candidate_ids"]))
            except ContractError as exc:
                missing_artifacts.append({"host": host, "job": job["name"], "artifact": "invalid-verdict", "detail": str(exc)})
                continue
            existing_host_ids = {
                event["candidate_id"] for event in events if event["host"] == host
            }
            for record in verdict_events:
                if record.get("not_started") is True:
                    not_started_reasons.setdefault(
                        record["candidate_id"], "never started (Assay process tree status: not-started)"
                    )
                    continue
                if record["candidate_id"] not in existing_host_ids:
                    events.append({**record, "host": host, "job": job["name"]})
                    existing_host_ids.add(record["candidate_id"])
        for job in state["jobs"].values():
            if job["status"] not in {"QUEUED", "STARTING"} and not job.get("progress_path"):
                missing_artifacts.append({"host": job["host"], "job": job["name"], "artifact": "progress", "path": ""})
            for candidate_id in job.get("not_started_ids", []):
                not_started_reasons.setdefault(
                    candidate_id, "never started (Assay process tree status: not-started)"
                )
        failed_jobs = [
            {
                "host": job["host"],
                "job": job["name"],
                "exit_code": job.get("exit_code"),
                "reason": job.get("failure_reason") or f"exit code {job.get('exit_code')}",
                "candidate_ids": list(job["candidate_ids"]),
            }
            for job in sorted(state["jobs"].values(), key=lambda row: row["name"])
            if job["status"] == "FAILED"
        ]
        failed_candidate_reasons = {
            candidate_id: failed["reason"]
            for failed in failed_jobs
            for candidate_id in failed["candidate_ids"]
        }
        by_candidate: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            candidate_id = event.get("candidate_id")
            by_candidate.setdefault(candidate_id, []).append(event)
        candidate_rows = []
        totals = {name: 0 for name in ("killed", "survived", "timeout", "crashed", "equivalent")}
        duplicates = 0
        for candidate_id in sorted(expected_ids):
            records = by_candidate.get(candidate_id, [])
            records.sort(key=lambda item: _event_key(item, item["host"], item["job"]))
            metadata = state["candidate_metadata"].get(candidate_id, {})
            if not records:
                candidate_rows.append({
                    "candidate_id": candidate_id,
                    "outcome": "missing",
                    "owner_host": None,
                    "owner_job": None,
                    "emitted_at": None,
                    "duplicates_ignored": 0,
                    "reason": (
                        failed_candidate_reasons.get(candidate_id)
                        or not_started_reasons.get(candidate_id)
                        or state["unplaced_reasons"].get(candidate_id)
                    ),
                })
                continue
            owner = records[0]
            outcome = self._outcome(owner["outcome_bucket"])
            totals[outcome] += 1
            ignored = max(0, len(records) - 1)
            duplicates += ignored
            candidate_rows.append({
                "candidate_id": candidate_id,
                "outcome": outcome,
                "owner_host": owner["host"],
                "owner_job": owner["job"],
                "emitted_at": owner.get("emitted_at"),
                "duplicates_ignored": ignored,
                "file": metadata.get("file"),
            })
        per_host = self._host_totals(state, candidate_rows)
        return {
            "schema": RESULT_SCHEMA,
            "campaign": state["campaign"],
            "lane": state["lane"],
            "plan_sha256": state["plan_sha256"],
            "candidates": candidate_rows,
            "totals": {**totals, "missing": sum(row["outcome"] == "missing" for row in candidate_rows), "duplicates_ignored": duplicates},
            "per_host": per_host,
            "failed_jobs": failed_jobs,
            "missing_artifacts": sorted(missing_artifacts, key=lambda item: (item["host"], item.get("job", ""), item["artifact"], item.get("path", ""))),
        }

    @staticmethod
    def _outcome(bucket: str) -> str:
        if bucket in {"budget_exceeded", "hung"}:
            return "timeout"
        if bucket in {"killed", "survived", "crashed", "equivalent"}:
            return bucket
        raise ContractError(f"unsupported Assay outcome bucket: {bucket}")

    @staticmethod
    def _job_for_record(state: Mapping[str, Any], host: str, candidate_id: str) -> str:
        names = sorted(
            job["name"] for job in state["jobs"].values()
            if job["host"] == host and candidate_id in job["candidate_ids"]
        )
        return names[0] if names else "unknown"

    @classmethod
    def _verdict_records(cls, content: bytes, expected_ids: set[str]) -> list[dict[str, Any]]:
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("verdict artifact is not UTF-8 JSON") from exc
        mutations: list[Mapping[str, Any]] = []
        claims = document.get("claims", []) if isinstance(document, dict) else []
        if isinstance(claims, list):
            for claim in claims:
                if isinstance(claim, dict) and claim.get("rigor") == "R2" and isinstance(claim.get("mutation"), dict):
                    mutations.append(claim["mutation"])
        judgment = document.get("judgment") if isinstance(document, dict) else None
        r2 = judgment.get("r2") if isinstance(judgment, dict) else None
        if isinstance(r2, dict) and isinstance(r2.get("mutation"), dict):
            mutations.append(r2["mutation"])
        records = []
        for mutation in mutations:
            for bucket in ("killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent"):
                outcomes = mutation.get(bucket, [])
                if not isinstance(outcomes, list):
                    continue
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    candidate_id = outcome.get("candidate_id")
                    if not isinstance(candidate_id, str) or candidate_id not in expected_ids:
                        continue
                    records.append({
                        "candidate_id": candidate_id,
                        "outcome_bucket": bucket,
                        "elapsed_seconds": None,
                        "emitted_at": None,
                        "not_started": (
                            bucket == "budget_exceeded"
                            and isinstance(outcome.get("process_tree_evidence"), dict)
                            and outcome["process_tree_evidence"].get("leader_status") == "not-started"
                        ),
                    })
        return records

    def _host_totals(self, state: Mapping[str, Any], candidate_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        now = self.clock()
        result = []
        for host_plan in state["plan"]["hosts"]:
            host_id = host_plan["id"]
            jobs = [job for job in state["jobs"].values() if job["host"] == host_id and job["status"] not in {"SUPERSEDED", "SUPERSEDED_PENDING"}]
            starts = [job["started_mono"] for job in jobs if isinstance(job.get("started_mono"), (int, float))]
            ends = [job.get("ended_mono", now if job["status"] in {"RUNNING", "UNKNOWN", "STARTING"} else job.get("created_mono")) for job in jobs if job.get("started_mono") is not None]
            wall = max(0.0, max(ends, default=now) - min(starts, default=now))
            owned = sum(row["outcome"] != "missing" and row["owner_host"] == host_id for row in candidate_rows)
            result.append({
                "host": host_id,
                "state": state["hosts"][host_id]["status"],
                "wall_seconds": round(wall, 3),
                "candidate_rate_per_second": round(owned / wall, 6) if wall > 0 else None,
                "owned_candidates": owned,
                "planned_candidates": len(host_plan.get("assigned", [])),
                "batches": len(jobs),
            })
        return result

    @staticmethod
    def _render_report(result: Mapping[str, Any]) -> str:
        totals = result["totals"]
        lines = [
            f"# Campaign {result['campaign']}",
            "",
            f"Lane: `{result['lane']}`",
            f"Plan SHA-256: `{result['plan_sha256']}`",
            "",
            "## Totals",
            "",
            "| Killed | Survived | Timeout | Crashed | Equivalent | Missing | Duplicates ignored |",
            "|---:|---:|---:|---:|---:|---:|---:|",
            f"| {totals['killed']} | {totals['survived']} | {totals['timeout']} | {totals['crashed']} | {totals['equivalent']} | {totals['missing']} | {totals['duplicates_ignored']} |",
            "",
            "## Candidates",
            "",
            "| Candidate | Outcome | Owner host | Owner job | Finished at | Duplicates ignored | Reason |",
            "|---|---|---|---|---|---:|---|",
        ]
        for row in result["candidates"]:
            reason = str(row.get("reason") or "—").replace("|", "\\|")
            lines.append(
                f"| `{row['candidate_id']}` | {row['outcome']} | {row['owner_host'] or '—'} | {row['owner_job'] or '—'} | {row['emitted_at'] or '—'} | {row['duplicates_ignored']} | {reason} |"
            )
        lines.extend(["", "## Failed batches", ""])
        if result.get("failed_jobs"):
            lines.extend(["| Host | Job | Exit code | Reason |", "|---|---|---:|---|"])
            for job in result["failed_jobs"]:
                reason = str(job["reason"]).replace("|", "\\|")
                lines.append(f"| {job['host']} | `{job['job']}` | {job['exit_code']} | {reason} |")
        else:
            lines.append("None.")
        lines.extend(["", "## Hosts", "", "| Host | State | Wall seconds | Candidates / second | Owned | Planned | Batches |", "|---|---|---:|---:|---:|---:|---:|"])
        for host in result["per_host"]:
            rate = "—" if host["candidate_rate_per_second"] is None else str(host["candidate_rate_per_second"])
            lines.append(f"| {host['host']} | {host['state']} | {host['wall_seconds']} | {rate} | {host['owned_candidates']} | {host['planned_candidates']} | {host['batches']} |")
        lines.extend(["", "## Missing artifacts", ""])
        if result["missing_artifacts"]:
            for item in result["missing_artifacts"]:
                detail = item.get("path") or item.get("detail") or ""
                lines.append(f"- {item['host']} / {item.get('job', 'campaign')}: {item['artifact']} {detail}".rstrip())
        else:
            lines.append("None.")
        return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
