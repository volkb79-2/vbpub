"""Small command builders for the existing named-job runner scripts."""

from __future__ import annotations

import base64
import hashlib
import importlib.resources
import json
import re
import shlex
import zlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from . import hostside
from .contracts import ContractError, is_candidate_id
from .runner_layout import (
    ASSAY_DIR,
    NAME_PLACEHOLDER,
    PROGRESS_PREFIX,
    PROGRESS_SUFFIX,
    candidate_ids_home_path,
    candidate_ids_relative,
    progress_home_path,
)
from .transport import Transport, TransportError, command_metadata, host_unavailable, json_output


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


def probe_payload(jobs: Mapping[str, JobSpec]) -> str:
    """Encode the jobs to probe as one compressed table of job names per checkout.

    The remote command is ONE shell argument capped at 128 KiB, so the table
    carries only what differs per job (its name) and one path template per
    (campaign, host, commit, project, lane) group; the host derives each ids
    path from the template and the name. The size therefore grows by a few
    bytes per job, not by a full target record.
    """
    groups: dict[tuple[str, str, str, str, str], list[str]] = {}
    for name in sorted(jobs):
        spec = jobs[name]
        validate_job_spec(spec)
        if spec.name != name:
            raise ContractError("probe job key does not match its generated name")
        groups.setdefault((spec.campaign, spec.host, spec.commit, spec.project, spec.lane), []).append(name)
    table = [
        {
            "names": names,
            "ids_template": candidate_ids_home_path(commit, project, campaign, host, NAME_PLACEHOLDER),
            "commit": commit,
            "project": project,
            "lane": lane,
            "assay_dir": ASSAY_DIR,
            "progress_prefix": PROGRESS_PREFIX,
            "progress_suffix": PROGRESS_SUFFIX,
        }
        for (campaign, host, commit, project, lane), names in sorted(groups.items())
    ]
    payload = json.dumps(table, sort_keys=True, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(payload)).decode("ascii")


def probe_script(jobs: Mapping[str, JobSpec], *, proc_root: str = "/proc") -> str:
    """Build the read-only probe command; ``proc_root`` lets a test point at a fixture tree."""
    encoded = probe_payload(jobs)
    metadata = command_metadata("probe", {})
    remote = "\n".join([
        "set -euo pipefail",
        'source "$HOME/runner-scripts/lib.sh"',
        _hostside_command("probe", '"$RUNNER_HOME"', shlex.quote(encoded), shlex.quote(proc_root)),
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
    checkout_dir = f'"$RUNNER_HOME/checkouts/{spec.commit}"'
    project_dir = checkout_dir if spec.project == "." else f'"$RUNNER_HOME/checkouts/{spec.commit}/{spec.project}"'
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
            f"checkout_dir={checkout_dir}",
            f"project_dir={project_dir}",
            f"candidate_file={candidate_file}",
            "assay_lane=$(" + _hostside_command(
                "resolve-assay", '"$checkout_dir"', shlex.quote(spec.project), shlex.quote(spec.lane)
            ) + ")",
            'progress_file="$project_dir/.assay/progress-$assay_lane.jsonl"',
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


@lru_cache(maxsize=1)
def _hostside_bootstrap() -> str:
    """Compress the packaged host-side source for one ``python3 -c`` argument."""
    source = importlib.resources.files("fleetd").joinpath("hostside.py").read_text(encoding="utf-8")
    compressed = base64.b64encode(zlib.compress(source.encode("utf-8"))).decode("ascii")
    return (
        "import base64,zlib;exec(zlib.decompress(base64.b64decode("
        + repr(compressed)
        + ")))"
    )


def _hostside_command(*args: str) -> str:
    """Build one shipped-module invocation from already shell-quoted arguments."""
    return f"python3 -c {shlex.quote(_hostside_bootstrap())} " + " ".join(args)


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
        self._pressure: dict[str, dict[str, float | None] | None] = {}

    def pressure(self, host: str) -> dict[str, float | None] | None:
        """The ``some avg10`` PSI of the host's last successful probe, or ``None`` when unknown."""
        return self._pressure.get(host)

    def probe(self, host: str, jobs: Mapping[str, JobSpec]) -> dict[str, dict[str, Any]]:
        self._pressure.pop(host, None)
        rc, out, err = self.transport.run(host, probe_script(jobs), self.timeout)
        if rc in {124, 255}:
            raise host_unavailable(rc, f"probe could not reach {host}: {err.strip()}")
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
        self._pressure[host] = _parse_pressure(document.get("pressure"))
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
            raise host_unavailable(rc, f"stop-job could not reach {host}: {err.strip()}")
        if rc != 0:
            raise TransportError(f"stop-job failed for {name} with exit {rc}: {err.strip()}")
        return out.strip() == "STOPPED"

    def get(self, host: str, path: str) -> bytes:
        return self.transport.get(host, path, self.timeout)

    @staticmethod
    def _digest_matches(actual: object, spec: JobSpec) -> bool:
        return isinstance(actual, str) and actual == spec.list_digest


def _parse_pressure(value: object) -> dict[str, float | None] | None:
    """Read the probe's ``pressure`` object; a missing or malformed one is unknown, never zero."""
    if not isinstance(value, Mapping):
        return None
    result: dict[str, float | None] = {}
    for key in ("memory_some_avg10", "io_some_avg10"):
        reading = value.get(key)
        valid = (
            not isinstance(reading, bool)
            and isinstance(reading, (int, float))
            and 0 <= reading <= 100  # NaN and infinity fail this range test
        )
        result[key] = float(reading) if valid else None
    return result


def parse_progress(
    content: bytes,
    expected_ids: set[str],
    *,
    start_offset: int = 0,
    end_offset: int | None = None,
) -> list[dict[str, object]]:
    """Apply the collector's closed contract to the host-side reader."""
    try:
        records = hostside.progress_records(
            content,
            expected_ids,
            start_offset=start_offset,
            end_offset=end_offset,
            strict=True,
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    return [{**record, "memory_peak_bytes": record.get("memory_peak_bytes")} for record in records]


def canonical_probe_json(value: object) -> str:
    """Expose a stable serializer for deterministic fake and contract tests."""
    return json_output(value)
