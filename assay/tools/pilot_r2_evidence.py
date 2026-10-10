"""Shared source-bound R2 evidence checks for non-qualifying pilot reports."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from assay.r2_command import R2_APPENDED, R2_TRANSFORM_ID, collection_digest, transform_argv

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_MAX_BYTES = 64 * 1024 * 1024
_MANIFEST_MAX_NODE_BYTES = 4096
_BASELINE_COMMON_FIELDS = {
    "collection_count",
    "collection_sha256",
    "duplicates",
    "hook_fingerprint_sha256",
    "hook_count",
    "runtime_fingerprint_sha256",
}
_COMMAND_FIELDS = {
    "transform",
    "argv_declared",
    "argv_transformed",
    "appended",
    "cwd",
    "config_sha256",
    "coverage_baseline",
    "r2_baseline",
}
_RESOURCE_FIELDS = {
    "cpu_seconds",
    "peak_rss_bytes",
    "phase_seconds",
    "startup_seconds",
}
_PHASE_FIELDS = {"materialize", "command", "integrity", "teardown"}
_STARTUP_FIELDS = {"to_session_start", "to_first_test"}


def _read_manifest(path: Path) -> tuple[list[str], str]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("R2 manifest is not a single-link regular file")
        if info.st_size > _MANIFEST_MAX_BYTES:
            raise ValueError("R2 manifest exceeds the 64 MiB limit")
        raw = bytearray()
        while len(raw) <= _MANIFEST_MAX_BYTES:
            block = os.read(
                descriptor,
                min(1024 * 1024, _MANIFEST_MAX_BYTES + 1 - len(raw)),
            )
            if not block:
                break
            raw.extend(block)
        if len(raw) > _MANIFEST_MAX_BYTES:
            raise ValueError("R2 manifest exceeds the 64 MiB limit")
        after = os.fstat(descriptor)
        if (
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or len(raw) != after.st_size
        ):
            raise ValueError("R2 manifest changed while it was being read")
    finally:
        os.close(descriptor)

    content = bytes(raw)
    if content and not content.endswith(b"\n"):
        raise ValueError("R2 manifest is missing its final newline")
    if not content:
        return [], hashlib.sha256(content).hexdigest()
    lines = content[:-1].split(b"\n")
    if any(
        not line
        or b"\r" in line
        or len(line) > _MANIFEST_MAX_NODE_BYTES
        for line in lines
    ):
        raise ValueError("R2 manifest contains an invalid node ID record")
    try:
        nodes = [line.decode("utf-8", errors="strict") for line in lines]
    except UnicodeDecodeError as exc:
        raise ValueError("R2 manifest contains a non-UTF-8 node ID") from exc
    if len(nodes) != len(set(nodes)):
        raise ValueError("R2 manifest contains duplicate node IDs")
    return nodes, hashlib.sha256(content).hexdigest()


def _git_file(repo_root: Path, commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-c", "maintenance.auto=false",
            "-c", "maintenance.autoDetach=false",
            "-c", "gc.autoDetach=false",
            "-C", str(repo_root),
            "show", f"{commit}:{relative_path}",
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot read committed {relative_path}: {detail}")
    return result.stdout


def _baseline(raw: Any, *, name: str, require_wall: bool) -> dict[str, Any]:
    expected = _BASELINE_COMMON_FIELDS | ({"wall_s"} if require_wall else set())
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError(f"R2 command {name} has missing or unknown fields")
    count = raw.get("collection_count")
    duplicates = raw.get("duplicates")
    hook_count = raw.get("hook_count")
    if type(count) is not int or count < 0:
        raise ValueError(f"R2 command {name} collection_count must be >= 0")
    if duplicates != 0 or type(duplicates) is not int:
        raise ValueError(f"R2 command {name} duplicates must equal 0")
    if type(hook_count) is not int or hook_count < 0:
        raise ValueError(f"R2 command {name} hook_count must be >= 0")
    for field in (
        "collection_sha256",
        "hook_fingerprint_sha256",
        "runtime_fingerprint_sha256",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            raise ValueError(f"R2 command {name} {field} must be a SHA-256 digest")
    if require_wall:
        wall = raw.get("wall_s")
        if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0:
            raise ValueError(f"R2 command {name} wall_s must be finite and >= 0")
    return raw


def validate_r2_command(
    raw: Any,
    *,
    repo_root: Path,
    expected_commit: str,
    expected_lane: str,
    manifest_path: Path,
) -> dict[str, Any]:
    """Bind the summary's command and both collection baselines to source."""
    if not isinstance(raw, dict) or set(raw) != _COMMAND_FIELDS:
        raise ValueError("pilot summary r2_command has missing or unknown fields")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("expected source commit is not a full lowercase Git ID")
    try:
        repo_root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve pilot source repository: {exc}") from exc

    try:
        config_raw = _git_file(repo_root, expected_commit, "assay/assay.toml")
        config = tomllib.loads(config_raw.decode("utf-8"))
        lane = config["lanes"][expected_lane]
        declared = lane["argv"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read committed lane {expected_lane!r}: {exc}") from exc
    if not isinstance(declared, list) or not all(isinstance(token, str) for token in declared):
        raise ValueError(f"committed lane {expected_lane!r} has malformed argv")
    if raw.get("argv_declared") != declared:
        raise ValueError("pilot R2 declared argv differs from the committed lane")
    if raw.get("transform") != R2_TRANSFORM_ID:
        raise ValueError("pilot R2 command has an unexpected transform")
    try:
        transformed = list(transform_argv(declared))
    except (TypeError, ValueError) as exc:
        raise ValueError("committed lane argv cannot be transformed for R2") from exc
    if raw.get("argv_transformed") != transformed:
        raise ValueError("pilot R2 transformed argv differs from the committed lane")
    if raw.get("appended") != list(R2_APPENDED):
        raise ValueError("pilot R2 command has unexpected appended argv")
    if raw.get("cwd") != "assay":
        raise ValueError("pilot R2 command cwd differs from the committed project root")

    pyproject = _git_file(repo_root, expected_commit, "assay/pyproject.toml")
    expected_config_sha = hashlib.sha256(pyproject).hexdigest()
    if raw.get("config_sha256") != expected_config_sha:
        raise ValueError("pilot R2 config digest differs from committed assay/pyproject.toml")

    coverage = _baseline(
        raw.get("coverage_baseline"), name="coverage_baseline", require_wall=False
    )
    r2 = _baseline(raw.get("r2_baseline"), name="r2_baseline", require_wall=True)
    if any(
        coverage[field] != r2[field]
        for field in ("collection_count", "collection_sha256")
    ):
        raise ValueError("pilot coverage and R2 baseline collections differ")

    nodes, manifest_sha256 = _read_manifest(manifest_path)
    if (
        len(nodes) != r2["collection_count"]
        or collection_digest(nodes) != r2["collection_sha256"]
    ):
        raise ValueError("pilot R2 manifest does not match the retained R2 baseline")
    return {
        "nodes": nodes,
        "manifest_sha256": manifest_sha256,
        "coverage_baseline": coverage,
        "r2_baseline": r2,
    }


def validate_candidate_evidence(
    raw: Any,
    *,
    baselines: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    expected_fields = {
        "command",
        "collection_count",
        "collection_sha256",
        "hook_fingerprint_sha256",
        "started_count",
        "failed_call_index",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError(f"pilot candidate {candidate_id} has malformed collection evidence")
    command = raw.get("command")
    baseline_name = {"declared": "coverage_baseline", "r2": "r2_baseline"}.get(command)
    if baseline_name is None:
        raise ValueError(f"pilot candidate {candidate_id} has an unknown evidence command")
    baseline = baselines[baseline_name]
    for field in ("collection_count", "collection_sha256", "hook_fingerprint_sha256"):
        if raw.get(field) != baseline[field]:
            raise ValueError(
                f"pilot candidate {candidate_id} {field} differs from {baseline_name}"
            )
    return raw


def validate_kill_witness(
    execution: Any,
    evidence: dict[str, Any],
    *,
    nodes: list[str],
    candidate_id: str,
) -> None:
    if not isinstance(execution, dict):
        raise ValueError(f"pilot killed candidate {candidate_id} has no execution object")
    mode = execution.get("mode")
    witness = execution.get("witness")
    if not isinstance(witness, dict) or set(witness) != {
        "node_id",
        "when",
        "outcome",
        "session_exit_status",
        "process_exit_status",
    }:
        raise ValueError(f"pilot killed candidate {candidate_id} has a malformed failed-call witness")
    node_id = witness.get("node_id")
    if (
        not isinstance(node_id, str)
        or witness.get("when") != "call"
        or witness.get("outcome") != "failed"
        or type(witness.get("session_exit_status")) is not int
        or witness.get("session_exit_status") != 1
        or type(witness.get("process_exit_status")) is not int
        or witness.get("process_exit_status") != 1
    ):
        raise ValueError(f"pilot killed candidate {candidate_id} has an invalid failed-call witness")

    if mode == "full":
        if evidence.get("command") != "declared":
            raise ValueError(f"pilot full kill {candidate_id} requires declared-command evidence")
        if evidence.get("started_count") is not None or evidence.get("failed_call_index") is not None:
            raise ValueError(f"pilot full kill {candidate_id} carries failed-prefix evidence")
        if node_id not in nodes:
            raise ValueError(f"pilot full kill {candidate_id} witness node is not in the R2 manifest")
        return

    if mode == "witness-cold":
        index = evidence.get("failed_call_index")
        started = evidence.get("started_count")
        if (
            evidence.get("command") != "r2"
            or type(index) is not int
            or index < 0
            or index >= len(nodes)
            or type(started) is not int
            or started != index + 1
            or nodes[index] != node_id
        ):
            raise ValueError(
                f"pilot cold-witness kill {candidate_id} is not the manifest node at its failed index"
            )
        return

    raise ValueError(f"pilot kill {candidate_id} has unsupported execution mode {mode!r}")


def _finite_nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_resources(raw: Any, *, candidate_id: str, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _RESOURCE_FIELDS:
        raise ValueError(f"{context} measurements for {candidate_id} have missing or unknown fields")
    cpu = raw.get("cpu_seconds")
    rss = raw.get("peak_rss_bytes")
    phases = raw.get("phase_seconds")
    startup = raw.get("startup_seconds")
    if cpu is not None and not _finite_nonnegative(cpu):
        raise ValueError(f"{context} CPU measurement for {candidate_id} is invalid")
    if rss is not None and (type(rss) is not int or rss < 0):
        raise ValueError(f"{context} RSS measurement for {candidate_id} is invalid")
    if not isinstance(phases, dict) or set(phases) != _PHASE_FIELDS or any(
        not _finite_nonnegative(value) for value in phases.values()
    ):
        raise ValueError(f"{context} phase measurements for {candidate_id} are invalid")
    if startup is not None and (
        not isinstance(startup, dict)
        or set(startup) != _STARTUP_FIELDS
        or any(value is not None and not _finite_nonnegative(value) for value in startup.values())
    ):
        raise ValueError(f"{context} startup measurements for {candidate_id} are invalid")
    return raw


def validate_progress_measurements(event: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    elapsed = event.get("elapsed_seconds")
    tests_completed = event.get("tests_completed")
    if not _finite_nonnegative(elapsed):
        raise ValueError(f"pilot candidate {candidate_id} elapsed_seconds is invalid")
    if tests_completed is not None and (
        type(tests_completed) is not int or tests_completed < 0
    ):
        raise ValueError(f"pilot candidate {candidate_id} tests_completed is invalid")
    if not _RESOURCE_FIELDS <= set(event):
        raise ValueError(f"pilot candidate {candidate_id} is missing resource measurements")
    resources = {field: event[field] for field in _RESOURCE_FIELDS}
    validate_resources(resources, candidate_id=candidate_id, context="progress")
    return {"elapsed_seconds": elapsed, "tests_completed": tests_completed, **resources}


def validate_state_measurements(record: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    return validate_resources(record.get("resources"), candidate_id=candidate_id, context="state")
