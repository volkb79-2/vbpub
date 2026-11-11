"""(B529) Execution-resource settings are not part of the judge identity.

:func:`assay.mutation.judge_sha256` folds the content digest of the whole
judged tree (B088). That is what makes a strengthened test re-execute every
candidate -- and it also made a lane's OWN resource settings judge identity:
raising ``judge.mutation.jobs_max`` or the lane container's memory in
``assay.toml``/``run-gate.toml`` discarded every finished candidate of a
running campaign (the 2026-11-11 ciu campaign: 355 of 627).

This module projects those two configuration files before they are hashed.
Each file is parsed as TOML, the keys listed below are removed, and the
remainder is serialized canonically; the digest of that text stands in for
the file's Git object id in the tree digest
(:func:`assay.isolation._projected_manifest_sha256`). Consequences:

* changing a listed key (worker counts, the candidate ceiling, the lane's
  wall budget, container CPU/memory, scheduling hints, snapshot
  materialization knobs) keeps every record's judge identity;
* changing anything else in those files -- argv, env, ``env_passthrough``,
  ``budget_per_candidate`` (the per-candidate timeout DOES judge),
  liveness, operators, source roots, isolation selection, the run-gate
  environment/image, ``resource_observer`` -- still invalidates, exactly as
  before. Every other file of the tree is untouched, so B088 holds for tests,
  fixtures and sources;
* comment-only and formatting-only edits of the two files no longer
  invalidate either, because the projection is over the parsed document.

The removed values are not discarded: :func:`config_projection` digests them
(with the run's worker count) into ``resource_sha256``. A state record keeps
that digest, and resume re-executes a record whose bucket is
resource-sensitive (``crashed``, ``hung``, ``budget_exceeded``) when the
digest moved, because those three outcomes can depend on memory, CPU, worker
count and budgets. ``killed`` and ``survived`` records are reused.

Known, named limits:

* a TEST that reads one of the listed keys (e.g. a documentation-contract
  test asserting ``jobs == 1``) is judged by a value outside the identity.
  The baseline still runs on every attempt, so a test that turns red on the
  new value stops the lane before any candidate is judged; a test whose
  verdict on a MUTANT depends on a resource value is not detected;
* only the lane file the run loaded and the ``run-gate.toml`` beside it are
  projected; any other configuration file in the tree is ordinary content;
* ``removed`` values of every lane in the two files reach
  ``resource_sha256`` (not only the running lane's), which can only cause
  extra re-execution of resource-sensitive records, never less.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from pathlib import PurePosixPath
from typing import Any

from .records import record

#: Folded into every projected digest; bump when the key lists change.
PROJECTION_LABEL = "assay-config-projection/1"

#: ``[lanes.<name>]`` keys of an assay lane file that are resources.
ASSAY_LANE_KEYS: frozenset[str] = frozenset({"budget"})
#: ``[lanes.<name>.judge.mutation]`` keys that are resources or scheduling.
ASSAY_MUTATION_KEYS: frozenset[str] = frozenset(
    {
        "jobs",
        "jobs_max",
        "max_mutants",
        "shard_index",
        "shard_count",
        "schedule_high_factor",
        "schedule_low_factor",
        "prediction_wall_seconds",
        "prediction_rss_bytes",
    }
)
#: ``[lanes.<name>.isolation]`` keys that change how a tree is materialized,
#: never what it contains (config.py: "Verdict inputs are identical in both").
ASSAY_ISOLATION_KEYS: frozenset[str] = frozenset(
    {"verify_clone", "materialize", "tmpfs_root", "tmpfs_max_mib"}
)
#: ``[lanes.<name>.judge.canary]`` wall budget.
ASSAY_CANARY_KEYS: frozenset[str] = frozenset({"budget_per_attempt"})
#: Top-level ``[isolation]`` keys of the lane file (snapshot size limits).
ASSAY_PROJECT_ISOLATION_KEYS: frozenset[str] = frozenset({"limits"})

#: Top-level tables of a ``run-gate.toml`` that are resources/observability.
RUN_GATE_TOP_KEYS: frozenset[str] = frozenset({"profile", "footprint", "resources"})
#: ``[lanes.<name>]`` keys of a ``run-gate.toml`` that are resources.
RUN_GATE_LANE_KEYS: frozenset[str] = frozenset(
    {
        "budget",
        "stall_timeout",
        "memory",
        "resources",
        "jobs",
        "workers",
        "workers_max",
        "profile",
        "description",
    }
)

RUN_GATE_FILE_NAME = "run-gate.toml"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return {"toml-datetime": value.isoformat()}
    raise TypeError(f"unexpected TOML value type {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Deterministic JSON for a parsed TOML value (sorted keys, no spaces)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=True,
        default=_json_default,
    )


def _pop_keys(table: Any, keys: frozenset[str]) -> dict[str, Any]:
    removed: dict[str, Any] = {}
    if isinstance(table, dict):
        for key in sorted(keys & set(table)):
            removed[key] = table.pop(key)
    return removed


def _sub(table: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(table, dict):
            return None
        table = table.get(key)
    return table


def project_lane_document(document: dict[str, Any]) -> dict[str, Any]:
    """Remove the assay lane file's resource keys IN PLACE; return them."""
    removed: dict[str, Any] = {}
    top_isolation = _pop_keys(document.get("isolation"), ASSAY_PROJECT_ISOLATION_KEYS)
    if top_isolation:
        removed["isolation"] = top_isolation
    lanes = document.get("lanes")
    if isinstance(lanes, dict):
        lane_removed: dict[str, Any] = {}
        for name in sorted(lanes):
            table = lanes[name]
            entry: dict[str, Any] = {}
            for label, subtable, keys in (
                ("lane", table, ASSAY_LANE_KEYS),
                ("isolation", _sub(table, "isolation"), ASSAY_ISOLATION_KEYS),
                ("mutation", _sub(table, "judge", "mutation"), ASSAY_MUTATION_KEYS),
                ("canary", _sub(table, "judge", "canary"), ASSAY_CANARY_KEYS),
            ):
                popped = _pop_keys(subtable, keys)
                if popped:
                    entry[label] = popped
            if entry:
                lane_removed[name] = entry
        if lane_removed:
            removed["lanes"] = lane_removed
    return removed


def project_run_gate_document(document: dict[str, Any]) -> dict[str, Any]:
    """Remove a ``run-gate.toml``'s resource keys IN PLACE; return them."""
    removed: dict[str, Any] = {}
    top = _pop_keys(document, RUN_GATE_TOP_KEYS)
    if top:
        removed["top"] = top
    lanes = document.get("lanes")
    if isinstance(lanes, dict):
        lane_removed = {
            name: popped
            for name in sorted(lanes)
            if (popped := _pop_keys(lanes[name], RUN_GATE_LANE_KEYS))
        }
        if lane_removed:
            removed["lanes"] = lane_removed
    return removed


@record
class ConfigProjection:
    """One file's projection: its stand-in digest and the removed values."""

    digest: str
    removed: Any


def project_config(kind: str, raw: bytes) -> ConfigProjection:
    """Project one configuration file's bytes.

    *kind* is ``"assay"`` or ``"run-gate"``. Bytes that are not UTF-8 TOML
    are not projected: the stand-in digest is then the plain content digest
    under a ``raw`` tag, so such a file still judges by its exact bytes.
    """
    if kind not in ("assay", "run-gate"):
        raise ValueError(f"unknown configuration kind {kind!r}")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        content = hashlib.sha256(raw).hexdigest()
        return ConfigProjection(digest=f"{PROJECTION_LABEL}:{kind}:raw:{content}", removed=None)
    if kind == "assay":
        removed = project_lane_document(document)
    else:
        removed = project_run_gate_document(document)
    kept = hashlib.sha256(canonical_json(document).encode("ascii")).hexdigest()
    return ConfigProjection(digest=f"{PROJECTION_LABEL}:{kind}:{kept}", removed=removed)


@record
class JudgeProjection:
    """What :func:`config_projection` hands the judge identity."""

    #: repo-relative POSIX path -> projected digest, for the tree digest.
    content_overrides: Mapping[str, str]
    #: digest of every removed value plus the run's own worker settings.
    resource_sha256: str


def config_projection(
    read_file: Callable[[PurePosixPath], bytes | None],
    *,
    project_prefix: PurePosixPath | None,
    lane_file_name: str,
    jobs: int,
    memory_budget_bytes: int | None,
) -> JudgeProjection:
    """Project the loaded lane file and its sibling ``run-gate.toml``.

    *read_file* returns a tracked regular file's committed bytes, or ``None``
    when the path is not a regular tracked leaf (then it is not overridden,
    and nothing is removed from it).
    """
    prefix = project_prefix if project_prefix is not None else PurePosixPath(".")
    overrides: dict[str, str] = {}
    removed: dict[str, Any] = {}
    for kind, name in (("assay", lane_file_name), ("run-gate", RUN_GATE_FILE_NAME)):
        path = PurePosixPath(prefix, name) if str(prefix) != "." else PurePosixPath(name)
        raw = read_file(path)
        if raw is None:
            continue
        projection = project_config(kind, raw)
        overrides[path.as_posix()] = projection.digest
        removed[kind] = projection.removed
    resource_document = {
        "label": PROJECTION_LABEL,
        "removed": removed,
        "runtime": {"jobs": jobs, "memory_budget_bytes": memory_budget_bytes},
    }
    resource_sha256 = hashlib.sha256(
        canonical_json(resource_document).encode("ascii")
    ).hexdigest()
    return JudgeProjection(content_overrides=overrides, resource_sha256=resource_sha256)
