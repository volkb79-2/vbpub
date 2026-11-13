"""Small command builders for the existing named-job runner scripts."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shlex
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import ContractError, is_candidate_id
from .transport import HostUnavailable, Transport, TransportError, command_metadata, json_output


_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
_SAFE_JOB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}\Z")
_SAFE_COMMIT = re.compile(r"[0-9a-f]{40,64}\Z")
_SAFE_PROJECT_PART = re.compile(r"[A-Za-z0-9._-]+\Z")


@dataclass(frozen=True)
class JobSpec:
    """A single planned host batch ready for the runner."""

    campaign: str
    host: str
    slot: int
    batch: int
    candidate_ids: tuple[str, ...]
    commit: str
    project: str
    lane: str
    jobs: int

    @property
    def sorted_candidate_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.candidate_ids))

    @property
    def list_digest(self) -> str:
        content = "".join(f"{candidate_id}\n" for candidate_id in self.sorted_candidate_ids)
        return hashlib.sha256(content.encode("ascii")).hexdigest()

    @property
    def list8(self) -> str:
        return self.list_digest[:8]

    @property
    def name(self) -> str:
        return f"{self.campaign}.{self.host}.s{self.slot}.b{self.batch}.{self.list8}"

    @property
    def runner_campaign(self) -> str:
        return f"{self.name}-s1"

    @property
    def candidate_file(self) -> str:
        return f".assay/fleetd-plan/{self.campaign}/{self.host}/{self.name}.ids"

    @property
    def candidate_file_home_path(self) -> str:
        project = "" if self.project == "." else f"{self.project}/"
        return f"checkouts/{self.commit}/{project}.assay/fleetd-plan/{self.campaign}/{self.host}/{self.name}.ids"


def _safe_identifier(value: object, where: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None or value in {".", ".."}:
        raise ContractError(f"{where}: expected a safe name of at most 120 letters, digits, '.', '_' or '-' characters")
    return value


def validate_job_spec(spec: JobSpec) -> None:
    """Refuse values that could change a job name or escape its checkout."""
    _safe_identifier(spec.campaign, "campaign")
    _safe_identifier(spec.host, "host id")
    if isinstance(spec.slot, bool) or not isinstance(spec.slot, int) or spec.slot < 0:
        raise ContractError("job slot: expected a non-negative integer")
    if isinstance(spec.batch, bool) or not isinstance(spec.batch, int) or spec.batch < 0:
        raise ContractError("job batch: expected a non-negative integer")
    if not spec.candidate_ids or any(not is_candidate_id(value) for value in spec.candidate_ids):
        raise ContractError("job candidate list: expected one or more Assay SHA-256 ids")
    if len(set(spec.candidate_ids)) != len(spec.candidate_ids):
        raise ContractError("job candidate list: duplicate candidate id")
    if _SAFE_COMMIT.fullmatch(spec.commit) is None:
        raise ContractError("execution commit: expected a full lowercase Git commit id")
    if spec.project != ".":
        parts = spec.project.split("/")
        if not parts or any(part in {"", ".", ".."} or _SAFE_PROJECT_PART.fullmatch(part) is None for part in parts):
            raise ContractError("execution project: expected a safe relative project path")
    _safe_identifier(spec.lane, "lane")
    if isinstance(spec.jobs, bool) or not isinstance(spec.jobs, int) or spec.jobs < 1:
        raise ContractError("job worker count: expected a positive integer")
    if _SAFE_JOB_NAME.fullmatch(spec.name) is None:
        raise ContractError("generated job name is too long or unsafe")


def _b64_json(value: object) -> str:
    return base64.b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).decode("ascii")


def probe_script(jobs: Mapping[str, Sequence[str]]) -> str:
    names = sorted(jobs)
    expected = {name: sorted(jobs[name]) for name in names}
    metadata = command_metadata("probe", {"jobs": names, "candidate_ids": expected})
    payload = _b64_json(expected)
    remote = r'''set -euo pipefail
source "$HOME/runner-scripts/lib.sh"
python3 - "$RUNNER_HOME" PAYLOAD <<'PY'
import base64
import json
import os
from pathlib import Path
import sys

runner_home = Path(sys.argv[1])
expected = json.loads(base64.b64decode(sys.argv[2], validate=True))
home = Path.home()
rows = []
for name, planned in sorted(expected.items()):
    job_dir = home / "jobs" / name
    if not job_dir.is_dir():
        rows.append({"name": name, "state": "MISSING"})
        continue
    campaign_dir = runner_home / "campaigns" / f"{name}-s1"
    exit_file = job_dir / "EXIT"
    exit_code = None
    if exit_file.is_file():
        try:
            exit_code = int(exit_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            exit_code = None
        state = "EXIT"
    else:
        state = "UNKNOWN"
        try:
            pid = int((job_dir / "pid").read_text(encoding="ascii").strip())
            os.kill(pid, 0)
            state = "RUNNING"
        except (OSError, ValueError):
            pass
    digest_file = job_dir / "list.sha256"
    try:
        list_digest = digest_file.read_text(encoding="ascii").strip()
    except OSError:
        list_digest = None
    progress_path = None
    records = []
    mtime = None
    pointer = campaign_dir / "progress-file"
    try:
        raw_path = Path(pointer.read_text(encoding="utf-8").strip())
        if raw_path.is_absolute():
            progress_path = raw_path.resolve().relative_to(home.resolve()).as_posix()
        else:
            progress_path = raw_path.as_posix()
        source = home / progress_path
        mtime = source.stat().st_mtime
        with source.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event") == "candidate" and event.get("candidate_id") in planned:
                    records.append({key: event[key] for key in (
                        "candidate_id", "outcome_bucket", "elapsed_seconds", "emitted_at"
                    ) if key in event})
    except (OSError, UnicodeError, ValueError):
        progress_path = None
        mtime = None
    verdict_path = f"campaigns/{name}-s1/verdict.json"
    done = {event.get("candidate_id") for event in records if isinstance(event.get("candidate_id"), str)}
    rows.append({
        "name": name, "state": state, "exit_code": exit_code,
        "list_digest": list_digest, "progress_count": len(done),
        "progress_mtime": mtime, "progress_path": progress_path,
        "verdict_path": verdict_path, "records": records,
    })
print(json.dumps({"jobs": rows}, sort_keys=True, separators=(",", ":")))
PY'''.replace("PAYLOAD", shlex.quote(payload))
    return f"{metadata}\n{remote}"


def start_script(spec: JobSpec) -> str:
    validate_job_spec(spec)
    metadata = command_metadata("start", {
        "name": spec.name,
        "list_digest": spec.list_digest,
        "candidate_ids": list(spec.sorted_candidate_ids),
        "ids_path": spec.candidate_file_home_path,
        "commit": spec.commit,
        "project": spec.project,
        "lane": spec.lane,
        "jobs": spec.jobs,
    })
    job_dir = f'"$HOME/jobs/{spec.name}"'
    candidate_file = f'"$RUNNER_HOME/checkouts/{spec.commit}/{spec.candidate_file}"'
    run_job = '"$HOME/runner-scripts/run-job"'
    run_args = [
        shlex.quote(spec.runner_campaign),
        shlex.quote(spec.commit),
        shlex.quote(spec.project),
        shlex.quote(spec.lane),
        "--jobs",
        shlex.quote(str(spec.jobs)),
        "--only-candidates",
        shlex.quote(spec.candidate_file),
    ]
    expected_digest = shlex.quote(spec.list_digest)
    remote = "\n".join(
        [
            "set -euo pipefail",
            'source "$HOME/runner-scripts/lib.sh"',
            f"job_dir={job_dir}",
            f"candidate_file={candidate_file}",
            f"expected_digest={expected_digest}",
            'file_digest=$(sha256sum -- "$candidate_file" | cut -d " " -f 1)',
            '[[ $file_digest == "$expected_digest" ]] || { echo "candidate file digest mismatch" >&2; exit 73; }',
            'if [[ -e $job_dir ]]; then',
            '  stored_digest=$(cat -- "$job_dir/list.sha256" 2>/dev/null || true)',
            '  if [[ $stored_digest == "$expected_digest" ]]; then echo EXISTING; exit 0; fi',
            '  echo "named job exists with another or unverifiable candidate-list digest" >&2; exit 73',
            "fi",
            f"if {run_job} {shlex.quote(spec.name)} -- env R2_MAX_ATTEMPTS=2147483647 bash \"$HOME/runner-scripts/run-r2-campaign.sh\" "
            + " ".join(run_args) + " >/dev/null; then",
            "  start_status=0",
            "else",
            "  start_status=$?",
            '  if [[ $start_status == 3 && -d $job_dir ]]; then',
            '    stored_digest=$(cat -- "$job_dir/list.sha256" 2>/dev/null || true)',
            '    if [[ $stored_digest == "$expected_digest" ]]; then echo EXISTING; exit 0; fi',
            '    echo "duplicate run-job name has an unverifiable list digest" >&2; exit 73',
            "  fi",
            '  exit "$start_status"',
            "fi",
            'printf "%s\\n" "$expected_digest" > "$job_dir/list.sha256.tmp"',
            'mv -f -- "$job_dir/list.sha256.tmp" "$job_dir/list.sha256"',
            "echo STARTED",
        ]
    )
    return f"{metadata}\n{remote}"


def stop_script(name: str) -> str:
    _safe_identifier(name, "job name")
    metadata = command_metadata("stop", {"name": name})
    remote = "\n".join(
        [
            "set -euo pipefail",
            'source "$HOME/runner-scripts/lib.sh"',
            "if bash \"$HOME/runner-scripts/run-r2-campaign.sh\" stop " + shlex.quote(f"{name}-s1") + " >/dev/null; then",
            "  echo STOPPED",
            "else",
            "  stop_status=$?",
            f'  if [[ -e "$RUNNER_HOME/campaigns/{name}-s1/final-exit" || -e "$HOME/jobs/{name}/EXIT" ]]; then echo NOT_RUNNING; exit 0; fi',
            '  exit "$stop_status"',
            "fi",
        ]
    )
    return f"{metadata}\n{remote}"


class RunnerJobs:
    """Probe and control runner jobs using hx transport and runner-owned scripts."""

    def __init__(self, transport: Transport, *, timeout: float = 30.0):
        self.transport = transport
        self.timeout = timeout

    def probe(self, host: str, jobs: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
        if not jobs:
            return {}
        rc, out, err = self.transport.run(host, probe_script(jobs), self.timeout)
        if rc in {124, 255}:
            raise HostUnavailable(f"probe could not reach {host}: {err.strip()}")
        if rc != 0:
            raise TransportError(f"probe failed on {host} with exit {rc}: {err.strip()}")
        try:
            document = json.loads(out)
        except json.JSONDecodeError as exc:
            raise TransportError(f"probe on {host} returned invalid JSON") from exc
        rows = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise TransportError(f"probe on {host} did not return a job table")
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise TransportError(f"probe on {host} returned an invalid job row")
            result[row["name"]] = row
        if set(result) != set(jobs):
            raise TransportError(f"probe on {host} omitted a requested job")
        return result

    def start(self, spec: JobSpec) -> bool:
        validate_job_spec(spec)
        current = self.probe(spec.host, {spec.name: spec.sorted_candidate_ids})[spec.name]
        if current.get("state") != "MISSING":
            if not self._digest_matches(current.get("list_digest"), spec):
                raise TransportError(f"named job {spec.name} exists with a different candidate-list digest")
            return False
        contents = "".join(f"{candidate_id}\n" for candidate_id in spec.sorted_candidate_ids).encode("ascii")
        self.transport.put(spec.host, spec.candidate_file_home_path, contents, self.timeout)
        rc, out, err = self.transport.run(spec.host, start_script(spec), self.timeout)
        if rc != 0:
            current = self.probe(spec.host, {spec.name: spec.sorted_candidate_ids})[spec.name]
            if current.get("state") != "MISSING" and self._digest_matches(current.get("list_digest"), spec):
                return False
            raise TransportError(f"start-job failed for {spec.name} with exit {rc}: {err.strip() or out.strip()}")
        if out.strip() not in {"STARTED", "EXISTING"}:
            raise TransportError(f"start-job for {spec.name} returned an unexpected response")
        return out.strip() == "STARTED"

    def stop(self, host: str, name: str) -> bool:
        rc, out, err = self.transport.run(host, stop_script(name), self.timeout)
        if rc in {124, 255}:
            raise HostUnavailable(f"stop-job could not reach {host}: {err.strip()}")
        if rc != 0:
            raise TransportError(f"stop-job failed for {name} with exit {rc}: {err.strip()}")
        return out.strip() == "STOPPED"

    def get(self, host: str, path: str) -> bytes:
        return self.transport.get(host, path, self.timeout)

    @staticmethod
    def _digest_matches(actual: object, spec: JobSpec) -> bool:
        return isinstance(actual, str) and actual == spec.list_digest


def parse_progress(content: bytes, expected_ids: set[str]) -> list[dict[str, object]]:
    """Parse the candidate events needed by the collector from one progress stream."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("progress artifact is not UTF-8") from exc
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "candidate":
            continue
        candidate_id = event.get("candidate_id")
        if not isinstance(candidate_id, str) or not is_candidate_id(candidate_id):
            raise ContractError(f"progress line {line_number}: invalid candidate id")
        if candidate_id not in expected_ids:
            continue
        outcome = event.get("outcome_bucket")
        if not isinstance(outcome, str):
            raise ContractError(f"progress line {line_number}: unsupported candidate outcome")
        if outcome not in {"killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent"}:
            raise ContractError(f"progress line {line_number}: unsupported candidate outcome")
        elapsed = event.get("elapsed_seconds")
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed))
            or elapsed < 0
        ):
            elapsed = None
        emitted = event.get("emitted_at")
        records.append({
            "candidate_id": candidate_id,
            "outcome_bucket": outcome,
            "elapsed_seconds": elapsed,
            "emitted_at": emitted if isinstance(emitted, str) else None,
        })
    return records


def canonical_probe_json(value: object) -> str:
    """Expose a stable serializer for deterministic fake and contract tests."""
    return json_output(value)
