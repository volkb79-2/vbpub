"""Stdlib-only host-side probe and runner helpers shipped as source.

This module is imported by the controller and also read from the installed
package and executed by ``python3 -c`` on a host. Keep it free of imports from
the rest of fleetd: a host only has this source plus the runner checkout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sys
import tomllib
import zlib
from pathlib import Path
from typing import Mapping, Sequence

OUTCOME_BUCKETS = ("killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent")
_SAFE_ASSAY_LANE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def decode_payload(encoded: str) -> list[dict[str, object]]:
    """Decode the compressed probe table passed to the shipped command."""
    value = json.loads(zlib.decompress(base64.b64decode(encoded, validate=True)))
    if not isinstance(value, list) or any(not isinstance(group, dict) for group in value):
        raise ValueError("probe payload is not a group table")
    return value


def resolve_assay_lane(checkout: Path, project: str, lane: str) -> str:
    """Resolve the lane's Assay artifact name from its Run-Gate declaration."""
    project_dir = checkout if project == "." else checkout / project
    assay_lane = lane
    for config_path in (project_dir / "run-gate.toml", checkout / "run-gate.root.toml"):
        try:
            declaration = tomllib.loads(config_path.read_text(encoding="utf-8")).get("lanes", {}).get(lane)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        if declaration is not None:
            assay_lane = declaration.get("assay_lane", lane)
            break
    if not isinstance(assay_lane, str) or _SAFE_ASSAY_LANE.fullmatch(assay_lane) is None:
        raise ValueError("resolved Assay lane is not a safe artifact name")
    return assay_lane


def progress_records(
    content: bytes,
    expected_ids: set[str],
    *,
    start_offset: int = 0,
    end_offset: int | None = None,
) -> list[dict[str, object]]:
    """Read candidate progress records from one byte window.

    Malformed JSON lines are ignored, matching the host probe's existing
    tolerance. A UTF-8 or range error is raised; callers choose whether it is a
    probe-wide unknown or a collector contract error.
    """
    if isinstance(start_offset, bool) or not isinstance(start_offset, int) or start_offset < 0:
        raise ValueError("progress start offset must be a non-negative integer")
    if end_offset is not None and (
        isinstance(end_offset, bool) or not isinstance(end_offset, int) or end_offset < start_offset
    ):
        raise ValueError("progress end offset must be an integer at or after its start")
    try:
        text = content[start_offset:end_offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("progress artifact is not UTF-8") from exc
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "candidate":
            continue
        if event.get("candidate_id") not in expected_ids:
            continue
        records.append({key: event[key] for key in (
            "candidate_id", "outcome_bucket", "elapsed_seconds", "emitted_at"
        ) if key in event})
    return records


def verdict_reading(
    content: bytes,
    expected_ids: set[str],
) -> tuple[list[dict[str, object]], float | None]:
    """Read one Assay verdict's candidate outcomes and optional baseline."""
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("verdict artifact is not UTF-8 JSON") from exc
    mutations: list[Mapping[str, object]] = []
    claims = document.get("claims", []) if isinstance(document, dict) else []
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict) and claim.get("rigor") == "R2" and isinstance(claim.get("mutation"), dict):
                mutations.append(claim["mutation"])
    judgment = document.get("judgment") if isinstance(document, dict) else None
    r2 = judgment.get("r2") if isinstance(judgment, dict) else None
    if isinstance(r2, dict) and isinstance(r2.get("mutation"), dict):
        mutations.append(r2["mutation"])
    records: list[dict[str, object]] = []
    for mutation in mutations:
        for bucket in OUTCOME_BUCKETS:
            outcomes = mutation.get(bucket, [])
            if not isinstance(outcomes, list):
                continue
            for outcome in outcomes:
                if not isinstance(outcome, dict) or outcome.get("candidate_id") not in expected_ids:
                    continue
                evidence = outcome.get("process_tree_evidence")
                records.append({
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
    else:
        baseline_seconds = float(baseline_seconds)
    return records, baseline_seconds


def read_pressure(proc_root: Path, resource: str) -> float | None:
    """Read ``some avg10`` from one kernel PSI file, or ``None``."""
    try:
        for line in (proc_root / "pressure" / resource).read_text(encoding="ascii").splitlines():
            fields = line.split()
            if fields[:1] == ["some"]:
                for field in fields[1:]:
                    if field.startswith("avg10="):
                        value = float(field[6:])
                        return value if 0 <= value <= 100 else None
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def probe_document(
    runner_home: Path,
    groups: Sequence[Mapping[str, object]],
    proc_root: Path,
    *,
    home: Path | None = None,
) -> dict[str, object]:
    """Build the read-only probe answer for one runner home."""
    runner_home = Path(runner_home)
    home = Path.home() if home is None else Path(home)
    targets = [
        {
            **{
                key: value for key, value in group.items()
                if key not in {"names", "ids_template"}
            },
            "name": name,
            "ids_path": str(group["ids_template"]).replace("@NAME@", name),
        }
        for group in groups
        for name in group["names"]
    ]
    rows = []
    for target in sorted(targets, key=lambda row: str(row["name"])):
        name = str(target["name"])
        checkout = runner_home / "checkouts" / str(target["commit"])
        project_dir = checkout if target["project"] == "." else checkout / str(target["project"])
        job_dir = home / "jobs" / name
        if not job_dir.is_dir():
            ready = (
                checkout.is_dir()
                and not checkout.is_symlink()
                and (checkout / ".git").exists()
                and project_dir.is_dir()
            )
            rows.append({"name": name, "state": "MISSING", "checkout_ready": ready})
            continue
        ids_file = runner_home / str(target["ids_path"])
        try:
            planned = [line.strip() for line in ids_file.read_text(encoding="ascii").splitlines() if line.strip()]
        except (OSError, UnicodeError):
            planned = []
        calculated_digest = hashlib.sha256(
            "".join(f"{candidate_id}\n" for candidate_id in sorted(planned)).encode("ascii")
        ).hexdigest()
        try:
            list_digest = (job_dir / "list.sha256").read_text(encoding="ascii").strip()
        except OSError:
            list_digest = calculated_digest if planned else None
        exit_code = None
        if (job_dir / "EXIT").is_file():
            try:
                exit_code = int((job_dir / "EXIT").read_text(encoding="ascii").strip())
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

        def offset(filename: str) -> int | None:
            try:
                value = (job_dir / filename).read_text(encoding="ascii").strip()
            except OSError:
                return None
            return int(value) if re.fullmatch(r"[0-9]+", value) else None

        progress_offset = offset("progress-offset")
        progress_end_offset = offset("progress-end-offset")
        if progress_end_offset is not None and (
            progress_offset is None or progress_end_offset < progress_offset
        ):
            progress_end_offset = None
        assay_lane = resolve_assay_lane(checkout, str(target["project"]), str(target["lane"]))
        progress_source = (
            project_dir
            / str(target["assay_dir"])
            / f"{target['progress_prefix']}{assay_lane}{target['progress_suffix']}"
        )
        try:
            progress_path = progress_source.relative_to(home).as_posix()
        except ValueError:
            try:
                progress_path = progress_source.resolve().relative_to(home.resolve()).as_posix()
            except (OSError, ValueError):
                progress_path = None
        records: list[dict[str, object]] = []
        mtime = None
        try:
            mtime = progress_source.stat().st_mtime
            if progress_offset is not None and (state != "EXIT" or progress_end_offset is not None):
                content = progress_source.read_bytes()
                end_offset = progress_end_offset if state == "EXIT" else len(content)
                records = progress_records(
                    content,
                    set(planned),
                    start_offset=progress_offset,
                    end_offset=end_offset,
                )
        except (OSError, ValueError):
            mtime = None
            records = []
        verdict_path = f"campaigns/{name}-s1/verdict.json"
        verdict_records: list[dict[str, object]] = []
        baseline_seconds = None
        if state == "EXIT":
            try:
                verdict_content = (runner_home / verdict_path).read_bytes()
                verdict_records, baseline_seconds = verdict_reading(verdict_content, set(planned))
            except (OSError, ValueError):
                verdict_records = []
                baseline_seconds = None
        done = {
            event.get("candidate_id")
            for event in records
            if isinstance(event.get("candidate_id"), str)
        }
        rows.append({
            "name": name,
            "state": state,
            "exit_code": exit_code,
            "list_digest": list_digest,
            "progress_count": len(done),
            "progress_mtime": mtime,
            "progress_path": progress_path,
            "progress_offset": progress_offset,
            "progress_end_offset": progress_end_offset,
            "verdict_path": verdict_path,
            "records": records,
            "verdict_records": verdict_records,
            "baseline_seconds": baseline_seconds,
        })
    return {
        "jobs": rows,
        "pressure": {
            "memory_some_avg10": read_pressure(proc_root, "memory"),
            "io_some_avg10": read_pressure(proc_root, "io"),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the two command shapes embedded by :mod:`fleetd.runner`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 4 and args[0] == "probe":
        document = probe_document(
            Path(args[1]),
            decode_payload(args[2]),
            Path(args[3]),
        )
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        return 0
    if len(args) == 4 and args[0] == "resolve-assay":
        print(resolve_assay_lane(Path(args[1]), args[2], args[3]))
        return 0
    raise SystemExit("usage: hostside.py probe RUNNER_HOME PAYLOAD PROC_ROOT | resolve-assay CHECKOUT PROJECT LANE")


if __name__ == "__main__":
    main()
