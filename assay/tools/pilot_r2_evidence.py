"""Shared source-bound R2 evidence checks for non-qualifying pilot reports."""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from assay.errors import EXIT_CODES, Outcome, REASON_CODES, ReasonCode
from assay.r2_command import R2_APPENDED, R2_TRANSFORM_ID, collection_digest, transform_argv
from assay.verdict import MUTATION_BUCKETS, ROLLUP_PRECEDENCE
import pilot_r2_snapshot

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


def validate_pilot_terminal_event(
    event: dict[str, Any],
    *,
    context: str,
    end_buckets: dict[str, int] | None = None,
) -> None:
    """Validate the pilot wrapper's terminal outcome and exit-code contract.

    Exit 6 is the pilot-only completion sentinel. Otherwise the event carries
    the verdict's ordinary exit code, except that a non-completed PASS is
    converted to exit 2 so a partial pilot cannot look successful.
    """
    raw_outcome = event.get("outcome")
    try:
        outcome = Outcome(raw_outcome)
    except (TypeError, ValueError):
        raise ValueError(f"{context} has an unknown outcome") from None

    raw_reason = event.get("reason_code")
    if raw_reason is None:
        reason = None
    else:
        try:
            reason = ReasonCode(raw_reason)
        except (TypeError, ValueError):
            raise ValueError(f"{context} has an unknown reason code") from None
    if (reason is None and outcome is not Outcome.PASS) or (
        reason is not None and reason not in REASON_CODES[outcome]
    ):
        raise ValueError(f"{context} has a reason code that does not belong to its outcome")

    exit_code = event.get("exit_code")
    if type(exit_code) is not int or exit_code not in {1, 2, 3, 4, 5, 6}:
        raise ValueError(f"{context} has an impossible exit code")
    if event.get("destination") is not None:
        raise ValueError(f"{context} unexpectedly names an artifact destination")
    if end_buckets is not None:
        if end_buckets["crashed"]:
            r2_outcome = Outcome.ERROR
        elif end_buckets["budget_exceeded"]:
            r2_outcome = Outcome.BUDGET_EXCEEDED
        elif end_buckets["hung"]:
            r2_outcome = Outcome.BUDGET_EXCEEDED
        elif end_buckets["survived"]:
            # Both pilot lanes judge at the native 100% mutation floor.
            r2_outcome = Outcome.FAIL
        elif end_buckets["killed"] == 0 and end_buckets["equivalent"] > 0:
            r2_outcome = Outcome.INCONCLUSIVE
        elif sum(end_buckets.values()) == 0:
            r2_outcome = Outcome.INCONCLUSIVE
        else:
            r2_outcome = Outcome.PASS

        # The pilot terminal event is the R0-R2 rollup. R0/R1 may contribute
        # an outcome that outranks R2, but the rollup cannot be weaker than R2.
        precedence = (*ROLLUP_PRECEDENCE, Outcome.PASS)
        if precedence.index(outcome) > precedence.index(r2_outcome):
            if outcome is Outcome.PASS:
                raise ValueError(f"{context} PASS outcome contradicts its mutation sweep")
            raise ValueError(
                f"{context} terminal outcome is weaker than its mutation sweep"
            )
    if exit_code != 6:
        expected = 2 if outcome is Outcome.PASS else EXIT_CODES[outcome]
        if exit_code != expected:
            raise ValueError(f"{context} exit code disagrees with its outcome")


def validate_pilot_end_accounting(
    *,
    selected_order: list[str],
    candidate_events: list[dict[str, Any]],
    prior_buckets: dict[str, str],
    pending_total: int,
    resumed_total: int,
    end_buckets: dict[str, int],
    context: str,
    require_prefix_indexes: bool = False,
) -> None:
    """Prove an end bucket vector can arise from this pending/resume history.

    Progress records name completed pending candidates, but an interrupted
    attempt can omit timed-out candidates. Candidate indexes still reveal how
    many selected identities were resumed before each emitted candidate. A
    small flow check then proves that the exact resumed outcomes, emitted
    outcomes, and omitted pending candidates (budget-exceeded) can produce
    the end buckets. A lower-bound check alone can mislabel a reused prior
    kill as another timeout.
    """
    if (
        not selected_order
        or len(selected_order) != len(set(selected_order))
        or set(end_buckets) != set(MUTATION_BUCKETS)
        or any(type(end_buckets[name]) is not int or end_buckets[name] < 0 for name in MUTATION_BUCKETS)
        or sum(end_buckets.values()) != len(selected_order)
        or type(pending_total) is not int
        or type(resumed_total) is not int
        or pending_total < 0
        or resumed_total < 0
        or pending_total + resumed_total != len(selected_order)
        or len(candidate_events) > pending_total
    ):
        raise ValueError(f"{context} totals do not partition the selected candidates")

    selected_position = {identity: index for index, identity in enumerate(selected_order)}
    current_buckets = {name: 0 for name in MUTATION_BUCKETS}
    event_by_position: list[tuple[int, int]] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    last_position = -1
    last_index = -1
    for event in candidate_events:
        identity = event.get("candidate_id")
        candidate_index = event.get("candidate_index")
        bucket = event.get("outcome_bucket")
        if (
            not isinstance(identity, str)
            or identity not in selected_position
            or identity in seen_ids
            or type(candidate_index) is not int
            or not 0 <= candidate_index < pending_total
            or candidate_index in seen_indexes
            or bucket not in MUTATION_BUCKETS
        ):
            raise ValueError(f"{context} contains an invalid candidate disposition")
        position = selected_position[identity]
        if position <= last_position or candidate_index <= last_index:
            raise ValueError(f"{context} candidate dispositions are out of selected order")
        if require_prefix_indexes and candidate_index != len(event_by_position):
            raise ValueError(f"{context} single-worker candidate indexes are not a prefix")
        seen_ids.add(identity)
        seen_indexes.add(candidate_index)
        current_buckets[bucket] += 1
        event_by_position.append((position, candidate_index))
        last_position = position
        last_index = candidate_index

    missing_pending = pending_total - len(candidate_events)
    required_resumed_buckets = {
        name: end_buckets[name] - current_buckets[name]
        for name in MUTATION_BUCKETS
    }
    required_resumed_buckets["budget_exceeded"] -= missing_pending
    if (
        any(value < 0 for value in required_resumed_buckets.values())
        or sum(required_resumed_buckets.values()) != resumed_total
    ):
        raise ValueError(f"{context} buckets disagree with pending and resumed totals")

    # Each gap between emitted events has an exact resumed count: for an
    # event at selected position p and pending index i, exactly p-i selected
    # identities before it were resumed. Reused outcomes in each gap are
    # assigned to bucket nodes below.
    groups: list[tuple[int, dict[str, int]]] = []
    previous_position = -1
    previous_resumed_before = 0
    for position, candidate_index in event_by_position:
        resumed_before = position - candidate_index
        gap_ids = selected_order[previous_position + 1 : position]
        required_in_gap = resumed_before - previous_resumed_before
        capacities = {name: 0 for name in MUTATION_BUCKETS}
        for identity in gap_ids:
            bucket = prior_buckets.get(identity)
            if bucket in capacities:
                capacities[bucket] += 1
        if required_in_gap < 0 or required_in_gap > sum(capacities.values()):
            raise ValueError(f"{context} candidate indexes contradict prior resume evidence")
        groups.append((required_in_gap, capacities))
        previous_position = position
        previous_resumed_before = resumed_before

    trailing_ids = selected_order[previous_position + 1 :]
    trailing_required = resumed_total - previous_resumed_before
    trailing_capacities = {name: 0 for name in MUTATION_BUCKETS}
    for identity in trailing_ids:
        bucket = prior_buckets.get(identity)
        if bucket in trailing_capacities:
            trailing_capacities[bucket] += 1
    if trailing_required < 0 or trailing_required > sum(trailing_capacities.values()):
        raise ValueError(f"{context} resumed total contradicts prior resume evidence")
    groups.append((trailing_required, trailing_capacities))

    # Bipartite capacitated matching: every gap supplies its known number of
    # resumed candidates, and each bucket must receive exactly the number the
    # end record attributes to reused state.
    group_count = len(groups)
    source = 0
    group_base = 1
    bucket_base = group_base + group_count
    sink = bucket_base + len(MUTATION_BUCKETS)
    graph: list[list[list[int]]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, stop: int, capacity: int) -> None:
        forward = [stop, len(graph[stop]), capacity]
        reverse = [start, len(graph[start]), 0]
        graph[start].append(forward)
        graph[stop].append(reverse)

    bucket_indexes = {name: index for index, name in enumerate(MUTATION_BUCKETS)}
    for group_index, (required, capacities) in enumerate(groups):
        node = group_base + group_index
        add_edge(source, node, required)
        for name, capacity in capacities.items():
            if capacity:
                add_edge(node, bucket_base + bucket_indexes[name], capacity)
    for name, required in required_resumed_buckets.items():
        add_edge(bucket_base + bucket_indexes[name], sink, required)

    flow = 0
    while True:
        parent: list[tuple[int, int] | None] = [None] * len(graph)
        parent[source] = (source, -1)
        queue = [source]
        for node in queue:
            for edge_index, edge in enumerate(graph[node]):
                if edge[2] > 0 and parent[edge[0]] is None:
                    parent[edge[0]] = (node, edge_index)
                    queue.append(edge[0])
                    if edge[0] == sink:
                        break
            if parent[sink] is not None:
                break
        if parent[sink] is None:
            break
        amount = resumed_total - flow
        node = sink
        while node != source:
            previous, edge_index = parent[node]  # type: ignore[misc]
            amount = min(amount, graph[previous][edge_index][2])
            node = previous
        node = sink
        while node != source:
            previous, edge_index = parent[node]  # type: ignore[misc]
            edge = graph[previous][edge_index]
            edge[2] -= amount
            graph[node][edge[1]][2] += amount
            node = previous
        flow += amount
        if flow == resumed_total:
            break
    if flow != resumed_total:
        raise ValueError(f"{context} end buckets cannot arise from its resume history")


def validate_pilot_resume_queue(
    *,
    selected_order: list[str],
    prior_dispositions: set[str],
    candidate_events: list[dict[str, Any]],
    pending_total: int,
    resumed_total: int,
    context: str,
) -> None:
    """Prove candidate indexes address the queue left by prior dispositions.

    A resume event records a count, not candidate IDs. The index/identity pairs
    still constrain which prior identities could have been reused: for a
    candidate at selected position ``p`` and pending index ``i``, exactly
    ``p - i`` earlier selected identities must have been resumed. Every such
    identity must have a disposition before this attempt. This also validates
    interrupted attempts that have no end event to trigger bucket accounting.
    """
    if (
        not selected_order
        or len(selected_order) != len(set(selected_order))
        or not prior_dispositions <= set(selected_order)
        or type(pending_total) is not int
        or type(resumed_total) is not int
        or pending_total < 0
        or resumed_total < 0
        or pending_total + resumed_total != len(selected_order)
        or resumed_total > len(prior_dispositions)
        or len(candidate_events) > pending_total
    ):
        raise ValueError(f"{context} resume queue totals disagree with prior dispositions")

    selected_position = {identity: index for index, identity in enumerate(selected_order)}
    previous_position = -1
    previous_resumed_before = 0
    previous_index = -1
    for event in candidate_events:
        identity = event.get("candidate_id")
        candidate_index = event.get("candidate_index")
        if (
            not isinstance(identity, str)
            or identity not in selected_position
            or type(candidate_index) is not int
            or not 0 <= candidate_index < pending_total
            or candidate_index <= previous_index
        ):
            raise ValueError(
                f"{context} candidate indexes do not address the resume queue "
                f"(identity={identity!r}, selected_position="
                f"{selected_position.get(identity) if isinstance(identity, str) else None}, "
                f"index={candidate_index!r}, previous_index={previous_index}, "
                f"pending_total={pending_total})"
            )
        position = selected_position[identity]
        if position <= previous_position:
            raise ValueError(f"{context} candidate order differs from its resume queue")
        resumed_before = position - candidate_index
        gap_ids = selected_order[previous_position + 1 : position]
        available_in_gap = sum(identity in prior_dispositions for identity in gap_ids)
        required_in_gap = resumed_before - previous_resumed_before
        if required_in_gap < 0 or required_in_gap > available_in_gap:
            raise ValueError(f"{context} candidate indexes contradict prior resume dispositions")
        previous_position = position
        previous_index = candidate_index
        previous_resumed_before = resumed_before

    trailing_ids = selected_order[previous_position + 1 :]
    resumed_after = resumed_total - previous_resumed_before
    available_after = sum(identity in prior_dispositions for identity in trailing_ids)
    if resumed_after < 0 or resumed_after > available_after:
        raise ValueError(f"{context} resume count exceeds prior candidate dispositions")


def _read_manifest(path: Path) -> tuple[list[str], str, dict[str, Any]]:
    content, identity = pilot_r2_snapshot.read_file(
        path, maximum=_MANIFEST_MAX_BYTES
    )
    if content and not content.endswith(b"\n"):
        raise ValueError("R2 manifest is missing its final newline")
    if not content:
        return [], hashlib.sha256(content).hexdigest(), identity
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
    return nodes, hashlib.sha256(content).hexdigest(), identity


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

    nodes, manifest_sha256, manifest_identity = _read_manifest(manifest_path)
    if (
        len(nodes) != r2["collection_count"]
        or collection_digest(nodes) != r2["collection_sha256"]
    ):
        raise ValueError("pilot R2 manifest does not match the retained R2 baseline")
    return {
        "nodes": nodes,
        "manifest_sha256": manifest_sha256,
        "manifest_identity": manifest_identity,
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
