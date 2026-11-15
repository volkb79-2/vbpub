"""Per-candidate measurement recorded in campaign evidence, and its summary.

Sizing an R2 campaign is a guess until somebody measures it (the merged
``assay-r2`` lane shipped with no ``candidate_ram`` and an 11h budget derived
from a 595 s whole-suite baseline divided by a worker count). This module is
the recorder that ends that guess:

* every finished candidate contributes its own wall seconds (Assay's
  ``elapsed_seconds``) and its own observed memory peak (Assay's
  ``resource-profile/1`` ``memory_peak_bytes``, null when the cgprofile
  observer could not measure it);
* the document keeps the raw rows -- so a later re-derivation can exclude, say,
  ``budget_exceeded`` candidates -- and a summary of medians over KILLED and
  SURVIVED candidates only, which are the two buckets whose runtime is the
  cost of judging;
* a candidate with no measurement is recorded as ``null``, never as zero: a
  zero would silently drag every median down and size the next campaign's
  budgets too small.

The summary is what ``fleet sizing --measurement`` consumes, so the numbers
that size a campaign are the numbers a previous campaign actually observed.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ContractError, is_candidate_id

MEASUREMENT_SCHEMA = "fleet-r2-measurement/1"

#: Only these buckets describe "the cost of judging a candidate"; a
#: ``budget_exceeded`` or ``hung`` candidate's elapsed time is the bound that
#: stopped it, not its runtime, and would understate the next campaign.
MEASURED_BUCKETS = ("killed", "survived")
BUCKETS = ("killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent")


def _median(values: Sequence[float]) -> float | None:
    return round(float(statistics.median(sorted(values))), 6) if values else None


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _memory(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def measurement_document(
    *,
    campaign: str,
    lane: str,
    commit: str,
    image_digest: str | None,
    planned_ids: Iterable[str],
    records: Iterable[Mapping[str, Any]],
    baseline_seconds: float | None = None,
) -> dict[str, Any]:
    """Build the ``fleet-r2-measurement/1`` document from campaign records.

    *records* are the owner-attributed records the collector already selected
    (one per candidate at most). A candidate with no record, or a record
    without a usable number, keeps ``null`` for that number.
    """
    planned = sorted(set(planned_ids))
    if any(not is_candidate_id(candidate_id) for candidate_id in planned):
        raise ContractError("planned_ids: expected 64-character lowercase candidate ids")
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        candidate_id = record.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in planned:
            continue
        by_id.setdefault(candidate_id, []).append(record)
    rows: list[dict[str, Any]] = []
    counts = {bucket: 0 for bucket in BUCKETS}
    measured_seconds: list[float] = []
    measured_memory: list[int] = []
    for candidate_id in planned:
        entries = by_id.get(candidate_id, [])
        if not entries:
            rows.append({
                "candidate_id": candidate_id, "outcome_bucket": None,
                "elapsed_seconds": None, "memory_peak_bytes": None,
            })
            continue
        owner = entries[0]
        bucket = owner.get("outcome_bucket")
        if not isinstance(bucket, str) or bucket not in BUCKETS:
            bucket = None
        else:
            counts[bucket] += 1
        seconds = _seconds(owner.get("elapsed_seconds"))
        memory = _memory(owner.get("memory_peak_bytes"))
        if bucket in MEASURED_BUCKETS and seconds is not None:
            measured_seconds.append(seconds)
        if bucket in MEASURED_BUCKETS and memory is not None:
            measured_memory.append(memory)
        rows.append({
            "candidate_id": candidate_id,
            "outcome_bucket": bucket,
            "elapsed_seconds": seconds,
            "memory_peak_bytes": memory,
        })
    return {
        "schema": MEASUREMENT_SCHEMA,
        "campaign": campaign,
        "lane": lane,
        "commit": commit,
        "image_digest": image_digest,
        "baseline_seconds": (
            float(baseline_seconds)
            if isinstance(baseline_seconds, (int, float)) and not isinstance(baseline_seconds, bool)
            and math.isfinite(float(baseline_seconds)) and baseline_seconds >= 0
            else None
        ),
        "candidates": rows,
        "summary": {
            "measured_candidates": len(measured_seconds),
            "elapsed_seconds_median": _median(measured_seconds),
            "elapsed_seconds_min": round(min(measured_seconds), 6) if measured_seconds else None,
            "elapsed_seconds_max": round(max(measured_seconds), 6) if measured_seconds else None,
            "memory_peak_bytes_median": int(_median(measured_memory)) if measured_memory else None,
            "memory_peak_bytes_max": max(measured_memory) if measured_memory else None,
            "memory_measured_candidates": len(measured_memory),
            "buckets": counts,
        },
    }


def validate_measurement(value: object) -> dict[str, Any]:
    """Validate a ``fleet-r2-measurement/1`` document strictly."""
    if not isinstance(value, Mapping):
        raise ContractError("measurement: expected a JSON object")
    if value.get("schema") != MEASUREMENT_SCHEMA:
        raise ContractError(f"measurement.schema: expected {MEASUREMENT_SCHEMA}")
    for key in ("campaign", "lane", "commit"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ContractError(f"measurement.{key}: expected non-empty text")
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise ContractError("measurement.summary: expected an object")
    for key in ("elapsed_seconds_median", "memory_peak_bytes_median"):
        if key not in summary:
            raise ContractError(f"measurement.summary: missing field {key}")
        if summary[key] is not None and (
            isinstance(summary[key], bool)
            or not isinstance(summary[key], (int, float))
            or not math.isfinite(float(summary[key]))
            or summary[key] <= 0
        ):
            raise ContractError(f"measurement.summary.{key}: expected a positive number or null")
    return dict(value)


def load_measurement(path: Path) -> dict[str, Any]:
    """Read and validate a measurement document from disk."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"measurement {source}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"measurement {source}: is not valid JSON: {exc}") from exc
    return validate_measurement(document)
