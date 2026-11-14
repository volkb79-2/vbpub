"""Small command builders for the existing named-job runner scripts."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shlex
import zlib
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ContractError, is_candidate_id
from .runner_layout import (
    ASSAY_DIR,
    PROGRESS_PREFIX,
    PROGRESS_SUFFIX,
    candidate_ids_home_path,
    candidate_ids_relative,
    progress_home_path,
)
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
        return candidate_ids_relative(self.campaign, self.host, self.name)

    @property
    def candidate_file_home_path(self) -> str:
        return candidate_ids_home_path(self.commit, self.project, self.campaign, self.host, self.name)


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
        if not parts or ".." in spec.project or any(part in {"", ".", ".."} or _SAFE_PROJECT_PART.fullmatch(part) is None for part in parts):
            raise ContractError("execution project: expected a safe relative project path")
    _safe_identifier(spec.lane, "lane")
    if isinstance(spec.jobs, bool) or not isinstance(spec.jobs, int) or spec.jobs < 1:
        raise ContractError("job worker count: expected a positive integer")
    if _SAFE_JOB_NAME.fullmatch(spec.name) is None:
        raise ContractError("generated job name is too long or unsafe")


def probe_script(jobs: Mapping[str, JobSpec]) -> str:
    targets = []
    for name in sorted(jobs):
        spec = jobs[name]
        validate_job_spec(spec)
        if spec.name != name:
            raise ContractError("probe job key does not match its generated name")
        targets.append({
            "name": name,
            "ids_path": spec.candidate_file_home_path,
            "commit": spec.commit,
            "project": spec.project,
            "lane": spec.lane,
            "assay_dir": ASSAY_DIR,
            "progress_prefix": PROGRESS_PREFIX,
            "progress_suffix": PROGRESS_SUFFIX,
        })
    metadata = command_metadata("probe", {"jobs": targets})
    payload = json.dumps(targets, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.b64encode(payload).decode("ascii")
    python_source = r'''import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib

runner_home = Path(sys.argv[1])
targets = json.loads(base64.b64decode(sys.argv[2], validate=True))
home = Path.home()
rows = []
for target in sorted(targets, key=lambda row: row["name"]):
    name = target["name"]
    job_dir = home / "jobs" / name
    checkout = runner_home / "checkouts" / target["commit"]
    project_dir = checkout if target["project"] == "." else checkout / target["project"]
    if not job_dir.is_dir():
        # Start refuses an unsynced checkout before uploading into its path.
        ready = checkout.is_dir() and not checkout.is_symlink() and (checkout / ".git").exists() and project_dir.is_dir()
        rows.append({"name": name, "state": "MISSING", "checkout_ready": ready})
        continue
    ids_file = runner_home / target["ids_path"]
    try:
        planned = [line.strip() for line in ids_file.read_text(encoding="ascii").splitlines() if line.strip()]
    except (OSError, UnicodeError):
        planned = []
    calculated_digest = hashlib.sha256("".join(f"{candidate_id}\n" for candidate_id in sorted(planned)).encode("ascii")).hexdigest()
    try:
        list_digest = (job_dir / "list.sha256").read_text(encoding="ascii").strip()
    except OSError:
        # run-job creates the job directory before its detached command starts;
        # the uploaded ids file is the durable recovery source during that gap.
        list_digest = calculated_digest if planned else None
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
    def read_offset(filename):
        try:
            value = (job_dir / filename).read_text(encoding="ascii").strip()
        except OSError:
            return None
        return int(value) if re.fullmatch(r"[0-9]+", value) else None
    progress_offset = read_offset("progress-offset")
    progress_end_offset = read_offset("progress-end-offset")
    if progress_end_offset is not None and (
        progress_offset is None or progress_end_offset < progress_offset
    ):
        progress_end_offset = None
    assay_lane = target["lane"]
    for config_path in (project_dir / "run-gate.toml", checkout / "run-gate.root.toml"):
        try:
            declaration = tomllib.loads(config_path.read_text(encoding="utf-8")).get("lanes", {}).get(target["lane"])
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        if declaration is not None:
            assay_lane = declaration.get("assay_lane", target["lane"])
            break
    if not isinstance(assay_lane, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", assay_lane) is None:
        raise ValueError("resolved Assay lane is not a safe artifact name")
    progress_source = project_dir / target["assay_dir"] / f"{target['progress_prefix']}{assay_lane}{target['progress_suffix']}"
    # Home-relative for a later get. Lexical first, so a symlinked checkouts
    # directory outside HOME still works; never fail the whole probe here.
    try:
        progress_path = progress_source.relative_to(home).as_posix()
    except ValueError:
        try:
            progress_path = progress_source.resolve().relative_to(home.resolve()).as_posix()
        except (OSError, ValueError):
            progress_path = None
    records = []
    mtime = None
    try:
        mtime = progress_source.stat().st_mtime
        if progress_offset is not None and (state != "EXIT" or progress_end_offset is not None):
            content = progress_source.read_bytes()
            end_offset = progress_end_offset if state == "EXIT" else len(content)
            for line in content[progress_offset:end_offset].decode("utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event") == "candidate" and event.get("candidate_id") in planned:
                    records.append({key: event[key] for key in (
                        "candidate_id", "outcome_bucket", "elapsed_seconds", "emitted_at"
                    ) if key in event})
    except (OSError, UnicodeError):
        mtime = None
    verdict_path = f"campaigns/{name}-s1/verdict.json"
    verdict_records = []
    baseline_seconds = None
    if state == "EXIT":
        try:
            verdict = json.loads((runner_home / verdict_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            verdict = None
        mutations = []
        claims = verdict.get("claims", []) if isinstance(verdict, dict) else []
        if isinstance(claims, list):
            for claim in claims:
                if isinstance(claim, dict) and claim.get("rigor") == "R2" and isinstance(claim.get("mutation"), dict):
                    mutations.append(claim["mutation"])
        judgment = verdict.get("judgment") if isinstance(verdict, dict) else None
        r2 = judgment.get("r2") if isinstance(judgment, dict) else None
        if isinstance(r2, dict) and isinstance(r2.get("mutation"), dict):
            mutations.append(r2["mutation"])
        for mutation in mutations:
            for bucket in ("killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent"):
                outcomes = mutation.get(bucket, [])
                if not isinstance(outcomes, list):
                    continue
                for outcome in outcomes:
                    if not isinstance(outcome, dict) or outcome.get("candidate_id") not in planned:
                        continue
                    evidence = outcome.get("process_tree_evidence")
                    verdict_records.append({
                        "candidate_id": outcome["candidate_id"],
                        "outcome_bucket": bucket,
                        "elapsed_seconds": None,
                        "emitted_at": None,
                        "not_started": (
                            bucket == "budget_exceeded"
                            and isinstance(evidence, dict)
                            and evidence.get("leader_status") == "not-started"
                        ),
                    })
        command = r2.get("r2_command") if isinstance(r2, dict) else None
        baseline = command.get("r2_baseline") if isinstance(command, dict) else None
        baseline_seconds = baseline.get("wall_s") if isinstance(baseline, dict) else None
        if (
            isinstance(baseline_seconds, bool)
            or not isinstance(baseline_seconds, (int, float))
            or not math.isfinite(float(baseline_seconds))
            or baseline_seconds < 0
        ):
            baseline_seconds = None
    done = {event.get("candidate_id") for event in records if isinstance(event.get("candidate_id"), str)}
    rows.append({
        "name": name, "state": state, "exit_code": exit_code,
        "list_digest": list_digest, "progress_count": len(done),
        "progress_mtime": mtime, "progress_path": progress_path,
        "progress_offset": progress_offset,
        "progress_end_offset": progress_end_offset,
        "verdict_path": verdict_path, "records": records,
        "verdict_records": verdict_records,
        "baseline_seconds": baseline_seconds,
    })
print(json.dumps({"jobs": rows}, sort_keys=True, separators=(",", ":")))
'''
    compressed = base64.b64encode(zlib.compress(python_source.encode("utf-8"))).decode("ascii")
    bootstrap = (
        "import base64,zlib;exec(zlib.decompress(base64.b64decode("
        + repr(compressed)
        + ")))"
    )
    remote = "\n".join([
        "set -euo pipefail",
        'source "$HOME/runner-scripts/lib.sh"',
        f"python3 -c {shlex.quote(bootstrap)} \"$RUNNER_HOME\" {shlex.quote(encoded)}",
    ])
    return f"{metadata}\n{remote}"


def start_script(spec: JobSpec) -> str:
    validate_job_spec(spec)
    metadata = command_metadata("start", {
        "name": spec.name,
        "list_digest": spec.list_digest,
        "ids_path": spec.candidate_file_home_path,
        "progress_path": progress_home_path(spec.commit, spec.project, spec.lane),
        "commit": spec.commit,
        "project": spec.project,
        "lane": spec.lane,
        "jobs": spec.jobs,
    })
    job_dir = f'"$HOME/jobs/{spec.name}"'
    candidate_file = f'"$RUNNER_HOME/{spec.candidate_file_home_path}"'
    progress_file = f'"$RUNNER_HOME/{progress_home_path(spec.commit, spec.project, spec.lane)}"'
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
    launcher = "\n".join([
        "set -euo pipefail",
        "printf '%s\\n' " + expected_digest
        + f' > "$HOME/jobs/{spec.name}/list.sha256.tmp"',
        f'mv -f -- "$HOME/jobs/{spec.name}/list.sha256.tmp" "$HOME/jobs/{spec.name}/list.sha256"',
        f'printf \'%s\\n\' "$FLEETD_PROGRESS_OFFSET" > "$HOME/jobs/{spec.name}/progress-offset.tmp"',
        f'mv -f -- "$HOME/jobs/{spec.name}/progress-offset.tmp" "$HOME/jobs/{spec.name}/progress-offset"',
        "set +e",
        '"$@"',
        "command_status=$?",
        "set -e",
        'if progress_end_offset=$(stat -c %s -- "$FLEETD_PROGRESS_PATH" 2>/dev/null); then :; else progress_end_offset=$FLEETD_PROGRESS_OFFSET; fi',
        '[[ $progress_end_offset =~ ^[0-9]+$ ]] || progress_end_offset=$FLEETD_PROGRESS_OFFSET',
        f'printf \'%s\\n\' "$progress_end_offset" > "$HOME/jobs/{spec.name}/progress-end-offset.tmp"',
        f'mv -f -- "$HOME/jobs/{spec.name}/progress-end-offset.tmp" "$HOME/jobs/{spec.name}/progress-end-offset"',
        'exit "$command_status"',
    ])
    remote = "\n".join(
        [
            "set -euo pipefail",
            'source "$HOME/runner-scripts/lib.sh"',
            f"job_dir={job_dir}",
            f"candidate_file={candidate_file}",
            f"progress_file={progress_file}",
            f"expected_digest={expected_digest}",
            'file_digest=$(sha256sum -- "$candidate_file" | cut -d " " -f 1)',
            '[[ $file_digest == "$expected_digest" ]] || { echo "candidate file digest mismatch" >&2; exit 73; }',
            'progress_offset=0',
            'if [[ -f $progress_file ]]; then progress_offset=$(stat -c %s -- "$progress_file"); fi',
            '[[ $progress_offset =~ ^[0-9]+$ ]] || { echo "invalid Assay progress size" >&2; exit 73; }',
            'if [[ -e $job_dir ]]; then',
            '  stored_digest=$(cat -- "$job_dir/list.sha256" 2>/dev/null || sha256sum -- "$candidate_file" | cut -d " " -f 1)',
            '  if [[ $stored_digest == "$expected_digest" ]]; then echo EXISTING; exit 0; fi',
            '  echo "named job exists with another or unverifiable candidate-list digest" >&2; exit 73',
            "fi",
            f"if FLEETD_PROGRESS_PATH=\"$progress_file\" FLEETD_PROGRESS_OFFSET=\"$progress_offset\" {run_job} {shlex.quote(spec.name)} -- bash -c {shlex.quote(launcher)} fleetd-launch env R2_MAX_ATTEMPTS=2147483647 bash \"$HOME/runner-scripts/run-r2-campaign.sh\" "
            + " ".join(run_args) + " >/dev/null; then",
            "  start_status=0",
            "else",
            "  start_status=$?",
            '  if [[ $start_status == 3 && -d $job_dir ]]; then',
            '    stored_digest=$(cat -- "$job_dir/list.sha256" 2>/dev/null || sha256sum -- "$candidate_file" | cut -d " " -f 1)',
            '    if [[ $stored_digest == "$expected_digest" ]]; then echo EXISTING; exit 0; fi',
            '    echo "duplicate run-job name has an unverifiable list digest" >&2; exit 73',
            "  fi",
            '  exit "$start_status"',
            "fi",
            "echo STARTED",
        ]
    )
    return f"{metadata}\n{remote}"


def stop_script(name: str) -> str:
    if not isinstance(name, str) or _SAFE_JOB_NAME.fullmatch(name) is None:
        raise ContractError("job name: expected a safe name of at most 240 letters, digits, '.', '_' or '-' characters")
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

    def probe(self, host: str, jobs: Mapping[str, JobSpec]) -> dict[str, dict[str, Any]]:
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
        current = self.probe(spec.host, {spec.name: spec})[spec.name]
        if current.get("state") != "MISSING":
            if not self._digest_matches(current.get("list_digest"), spec):
                raise TransportError(f"named job {spec.name} exists with a different candidate-list digest")
            return False
        if current.get("checkout_ready") is False:
            # Uploading would create checkouts/<commit>/ as a plain directory, which
            # the supervisor refuses and the runner's checkout sync cannot replace.
            raise TransportError(
                f"checkout {spec.commit} (project {spec.project}) is not synced on {spec.host}; "
                f"sync it before starting {spec.name}"
            )
        contents = "".join(f"{candidate_id}\n" for candidate_id in spec.sorted_candidate_ids).encode("ascii")
        self.transport.put(spec.host, spec.candidate_file_home_path, contents, self.timeout)
        rc, out, err = self.transport.run(spec.host, start_script(spec), self.timeout)
        if rc != 0:
            current = self.probe(spec.host, {spec.name: spec})[spec.name]
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


def parse_progress(
    content: bytes,
    expected_ids: set[str],
    *,
    start_offset: int = 0,
    end_offset: int | None = None,
) -> list[dict[str, object]]:
    """Parse the candidate events needed by the collector from one progress stream."""
    if isinstance(start_offset, bool) or not isinstance(start_offset, int) or start_offset < 0:
        raise ContractError("progress start offset must be a non-negative integer")
    if end_offset is not None and (
        isinstance(end_offset, bool) or not isinstance(end_offset, int) or end_offset < start_offset
    ):
        raise ContractError("progress end offset must be an integer at or after its start")
    try:
        text = content[start_offset:end_offset].decode("utf-8")
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
