"""Schema plus semantic validation of fragments, records and waivers, size limits and waiver expiry (SPEC 4, 7, 9).

Each ``validate_*`` function returns a list of :class:`~rigor_record.schema.Violation`; an empty list means valid.
Semantic checks run only when the document already satisfies its schema, so they may index freely. The privacy
lint runs on every document (it is part of validity, not an extra), and its hits come back as violations.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any, Callable, Iterable

from .privacy import lint
from .schema import Validator, Violation
from .stats import BASELINES, MIN_N

#: Size limits in bytes (SPEC 10).
LIMITS = {"record": 64 * 1024, "fragment": 256 * 1024, "release-body": 8 * 1024, "view": 128 * 1024}

#: Level-entry statuses that carry no judged data.
_EMPTY_LEVEL_STATUSES = ("not-applicable", "unknown")

_SEMVER = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z.-]+))?$")

_UNIT_RULES: tuple[tuple[Callable[[str], bool], str], ...] = (
    (lambda name: name.endswith("_bytes"), "bytes"),
    (lambda name: name.endswith("_ops"), "ops"),
    (lambda name: name.endswith("_ref"), "s_ref"),
    (lambda name: name in ("cpu_per_wall", "steal_fraction"), "ratio"),
    (lambda name: name.endswith("seconds") or name.startswith("phase_seconds."), "s"),
)


def canonical_json(document: Any) -> str:
    """The one serialisation committed fixtures and records use: sorted keys, 2-space indent, ASCII, final newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def check_size(kind: str, data: str | bytes) -> list[Violation]:
    """Check serialized ``data`` against the SPEC 10 limit of ``kind``."""
    size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
    limit = LIMITS[kind]
    if size > limit:
        return [Violation("$", f"{kind} is {size} bytes, over the {limit} byte limit")]
    return []


# -- semver and waiver expiry ---------------------------------------------------------------------------------
def _semver_key(version: str) -> tuple:
    match = _SEMVER.match(version)
    if not match:
        raise ValueError(f"not a semantic version: {version!r}")
    major, minor, patch, pre = match.groups()
    # A pre-release sorts before its release.
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


def is_expired(expires_before: str, version: str) -> bool:
    """A waiver with ``expires_before = X`` refuses at version X and at every later version (SPEC 7)."""
    return _semver_key(version) >= _semver_key(expires_before)


# -- generic helpers ------------------------------------------------------------------------------------------
def _privacy(document: Any, hosts: Iterable[str], users: Iterable[str]) -> list[Violation]:
    return [Violation(f.path, f"privacy rule {f.rule}") for f in lint(document, hosts=hosts, users=users)]


def _bad(path: str, message: str) -> list[Violation]:
    return [Violation(path, message)]


# -- performance entries (shared by fragments and records) ----------------------------------------------------
def _expected_unit(metric: str) -> str:
    for matches, unit in _UNIT_RULES:
        if matches(metric):
            return unit
    return "s"


def _check_distribution(dist: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    n = dist["n"]
    for p in (50, 90, 98, 99):
        value = dist[f"p{p}"]
        if (value is None) != (n < MIN_N[p]):
            rule = "null below" if n < MIN_N[p] else "present from"
            out += _bad(f"{path}.p{p}", f"must be {'null' if n < MIN_N[p] else 'a number'}: p{p} is {rule} n={MIN_N[p]}")
    if (dist["max"] is None) != (n == 0):
        out += _bad(f"{path}.max", "max is null exactly when n is 0")
    present = [dist[k] for k in ("p50", "p90", "p98", "p99", "max") if dist[k] is not None]
    if present != sorted(present):
        out += _bad(path, "percentiles must be non-decreasing up to max")
    return out


def _check_speed(speed: dict, path: str) -> list[Violation]:
    if "basis" not in speed:  # the `unavailable` form
        return []
    out: list[Violation] = []
    if int(speed["geekbench_version"].split(".")[0]) != speed["geekbench_major"]:
        out += _bad(f"{path}.geekbench_major", "must be the major part of geekbench_version")
    if speed["basis"] == "geekbench-table":
        if speed["multi_core"] is not None:
            out += _bad(f"{path}.multi_core", "a published-table speed has no multi_core (it describes the whole chip)")
    else:
        for key in ("multi_core", "cpus_at_measurement", "measured_on"):
            if speed[key] is None:
                out += _bad(f"{path}.{key}", "a measured speed records this value")
    return out


def _check_machine_classes(classes: list[dict], path: str) -> tuple[list[Violation], dict[str, dict]]:
    out: list[Violation] = []
    by_id: dict[str, dict] = {}
    for i, mc in enumerate(classes):
        if mc["id"] in by_id:
            out += _bad(f"{path}[{i}].id", "duplicate machine class id")
        by_id[mc["id"]] = mc
        out += _check_speed(mc["speed"], f"{path}[{i}].speed")
    return out, by_id


def _check_perf(entries: list[dict], classes: dict[str, dict], path: str) -> list[Violation]:
    out: list[Violation] = []
    for i, entry in enumerate(entries):
        base = f"{path}[{i}]"
        for cid in entry["machine_classes"]:
            if cid not in classes:
                out += _bad(f"{base}.machine_classes.{cid}", "machine class is not declared")
            elif "geekbench_major" in classes[cid]["speed"] and entry["normalisation"]["geekbench_major"] not in (
                None, classes[cid]["speed"]["geekbench_major"]
            ):
                out += _bad(f"{base}.machine_classes.{cid}", "Geekbench majors must not be mixed within one entry")
        norm = entry["normalisation"]
        has_basis = norm["basis"] != "none"
        major = norm["geekbench_major"]
        consistent = (
            (norm["divisor"] == "single_core") == has_basis
            and (major is not None) == has_basis
            and norm["reference_score"] == (BASELINES[major] if has_basis else None)
            and (norm["reason"] is None) == has_basis
        )
        if not consistent:
            out += _bad(f"{base}.normalisation",
                        "basis none needs no major, divisor none, no reference score and a reason; "
                        "otherwise a major, single_core, that major's baseline score and no reason")
        for name, metric in entry["metrics"].items():
            mpath = f"{base}.metrics.{name}"
            if "unit" not in metric:  # the `unavailable` form
                continue
            if metric["unit"] != _expected_unit(name):
                out += _bad(f"{mpath}.unit", f"metric {name} is reported in {_expected_unit(name)}")
            out += _check_distribution(metric, mpath)
            if name.endswith("_ref") and not has_basis:
                out += _bad(mpath, "a normalised metric needs a normalisation basis; use the unavailable form")
    return out


# -- fragments ------------------------------------------------------------------------------------------------
def _check_assay_r2(r2: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    cand, buckets, surv = r2["candidates"], r2["buckets"], r2["survivors"]
    if cand["executed"] > cand["total"]:
        out += _bad(f"{path}.candidates.executed", "executed exceeds total")
    if cand["resumed"] > cand["executed"]:
        out += _bad(f"{path}.candidates.resumed", "resumed exceeds executed")
    if sum(buckets.values()) != cand["executed"]:
        out += _bad(f"{path}.buckets", "bucket counts must add up to candidates.executed")
    if sum(r2["worker_counts"].values()) > cand["executed"]:
        out += _bad(f"{path}.worker_counts", "worker counts exceed candidates.executed")
    if sum(r2["profile_status_counts"].values()) > cand["executed"]:
        out += _bad(f"{path}.profile_status_counts", "profile status counts exceed candidates.executed")
    if surv["total"] != buckets["survived"]:
        out += _bad(f"{path}.survivors.total", "must equal buckets.survived")
    if len(surv["listed"]) != min(surv["total"], 500):
        out += _bad(f"{path}.survivors.listed", "lists min(total, 500) survivors")
    if (surv["sidecar_sha256"] is not None) != (surv["total"] > 500):
        out += _bad(f"{path}.survivors.sidecar_sha256", "present exactly when total exceeds 500")
    ids = [s["candidate_id"] for s in surv["listed"]]
    if len(set(ids)) != len(ids):
        out += _bad(f"{path}.survivors.listed", "duplicate candidate_id")
    campaign = r2["campaign"]
    if campaign is not None and campaign["started_at"] > campaign["completed_at"]:
        out += _bad(f"{path}.campaign", "completed_at precedes started_at")
    return out


def _fragment_semantics(doc: dict) -> list[Violation]:
    out: list[Violation] = []
    if doc["kind"] == "assay-lane":
        if (doc["outcome"] == "PASS") != (doc["reason"] is None):
            out += _bad("$.reason", "reason is null exactly when the outcome is PASS")
        if doc["r2"] is not None:
            out += _check_assay_r2(doc["r2"], "$.r2")
        return out
    if doc["started_at"] > doc["completed_at"]:
        out += _bad("$.completed_at", "completed_at precedes started_at")
    class_out, ids = _check_machine_classes(doc["machine_classes"], "$.machine_classes")
    return out + class_out + _check_perf(doc["perf"], ids, "$.perf")


def validate_fragment(document: Any, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Violation]:
    """Validate one ``urn:vbpub:rigor-fragment:1`` document (schema, semantics, privacy)."""
    if not isinstance(document, dict) or document.get("kind") not in ("assay-lane", "run-gate-lane"):
        return _bad("$.kind", "must be assay-lane or run-gate-lane")
    pointer = "/$defs/assay_lane" if document["kind"] == "assay-lane" else "/$defs/run_gate_lane"
    violations = Validator("rigor-fragment-1.json").validate(document, pointer)
    if not violations:
        violations = _fragment_semantics(document)
    return violations + _privacy(document, hosts, users)


# -- records --------------------------------------------------------------------------------------------------
def _gate_level(level: dict, path: str, name: str | None) -> list[Violation]:
    out: list[Violation] = []
    status, lanes = level["status"], level["lanes"]
    waived_lanes = [lane for lane in lanes if "waiver" in lane]
    bad_lanes = [lane for lane in lanes if lane["outcome"] != "PASS"]
    if status in _EMPTY_LEVEL_STATUSES:
        if lanes or level["toolset"] is not None or level["judged_commit"] is not None or level["waiver"] is not None:
            out += _bad(path, f"{status} carries no lanes, toolset, judged commit or waiver")
    elif status == "waived":
        if level["waiver"] is None:
            out += _bad(f"{path}.waiver", "a waived level names its waiver")
    else:
        if level["toolset"] is None or level["judged_commit"] is None or not lanes:
            out += _bad(path, f"{status} needs a toolset, a judged commit and at least one lane")
        if level["waiver"] is not None:
            out += _bad(f"{path}.waiver", "only a waived level carries a level waiver")
        if status == "pass" and (bad_lanes or waived_lanes):
            out += _bad(f"{path}.lanes", "pass means every lane passed without a waiver")
        if status == "pass-with-waivers" and (
            not waived_lanes or any(lane["outcome"] == "PASS" or "waiver" not in lane for lane in bad_lanes)
            or any(lane["outcome"] == "PASS" for lane in waived_lanes)
        ):
            out += _bad(f"{path}.lanes", "pass-with-waivers needs at least one failed lane, and exactly the failed lanes carry a waiver")
    if name == "R1" and status == "pass":
        cov = level.get("coverage")
        if cov is None or cov["line_pct"] < cov["floor"]["line"] or cov["branch_pct"] < cov["floor"]["branch"]:
            out += _bad(f"{path}.coverage", "R1 pass needs coverage at or above its floor")
    if name == "R3" and status == "pass":
        canaries = level.get("canaries") or []
        if not canaries or any(c["outcome"] != "PASS" for c in canaries):
            out += _bad(f"{path}.canaries", "R3 pass needs at least one canary and all of them passing")
    if name != "R1" and "coverage" in level:
        out += _bad(f"{path}.coverage", "only R1 carries coverage")
    if name != "R3" and "canaries" in level:
        out += _bad(f"{path}.canaries", "only R3 carries canaries")
    return out


def _review(review: dict, path: str) -> list[Violation]:
    filled = [review["range"], review["reviewer_role"], review["document_sha256"]]
    accept = review["status"] == "accept"
    if accept != all(v is not None for v in filled) or (not accept and any(v is not None for v in filled)):
        return _bad(path, "accept needs range, reviewer role and document digest; missing carries none")
    return []


def _r2_survivors(surv: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    total = surv["open"] + surv["equivalent"] + surv["accepted"] + surv["resolved"]
    listed = surv["listed"]
    if total <= 200:
        if len(listed) != total or surv["by_file"] or surv["sidecar_sha256"] is not None:
            out += _bad(path, "up to 200 survivors are all listed, with no per-file counts and no sidecar")
        for disposition in ("open", "equivalent", "accepted", "resolved"):
            if sum(1 for s in listed if s["disposition"] == disposition) != surv[disposition]:
                out += _bad(f"{path}.{disposition}", "count differs from the listed survivors")
    else:
        if listed or sum(surv["by_file"].values()) != total or surv["sidecar_sha256"] is None:
            out += _bad(path, "over 200 survivors collapse to per-file counts that add up, plus the sidecar digest")
    for i, s in enumerate(listed):
        if (s["disposition"] == "resolved") != (s["resolved_by"] is not None):
            out += _bad(f"{path}.listed[{i}].resolved_by", "set exactly when the disposition is resolved")
        if s["disposition"] in ("equivalent", "accepted") and s["reason"] is None:
            out += _bad(f"{path}.listed[{i}].reason", "an equivalent or accepted survivor states why")
    return out


def _r2_complete(r2: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    needed = ("toolset", "judged_commit", "scope", "campaign", "candidates", "buckets", "survivors")
    missing = [k for k in needed if r2[k] is None]
    if missing:
        return _bad(path, f"{r2['status']} needs {', '.join(missing)}")
    cand, buckets, surv = r2["candidates"], r2["buckets"], r2["survivors"]
    if cand["executed"] != cand["total"]:
        out += _bad(f"{path}.candidates", f"{r2['status']} means every candidate ran")
    if sum(buckets.values()) != cand["executed"]:
        out += _bad(f"{path}.buckets", "bucket counts must add up to candidates.executed")
    if r2["campaign"]["closeout_exit"] not in (0, 1):
        out += _bad(f"{path}.campaign.closeout_exit", "a completed campaign closes out with exit 0 or 1")
    if surv["open"] + surv["equivalent"] + surv["accepted"] + surv["resolved"] != buckets["survived"]:
        out += _bad(f"{path}.survivors", "survivor dispositions must add up to buckets.survived")
    waived = surv["equivalent"] + surv["accepted"]
    status = r2["status"]
    if status == "survivors" and surv["open"] < 1:
        out += _bad(f"{path}.status", "survivors needs at least one open survivor")
    if status == "pass-with-waivers" and (surv["open"] != 0 or waived < 1):
        out += _bad(f"{path}.status", "pass-with-waivers needs no open survivor and at least one equivalent or accepted one")
    if status == "pass" and (surv["open"] != 0 or waived != 0):
        out += _bad(f"{path}.status", "pass needs no open, equivalent or accepted survivor")
    return out + _r2_survivors(surv, f"{path}.survivors")


def _r2_scope(scope: dict | None, path: str) -> list[Violation]:
    if scope is None:
        return []
    if scope["kind"] == "whole-source" and (scope["base_tag"] is not None or not scope["anchored"]):
        return _bad(path, "a whole-source campaign has no base tag and is anchored")
    if scope["kind"] == "changed-since" and scope["base_tag"] is None:
        return _bad(path, "a changed-since campaign names its base tag")
    return []


def _r2(r2: dict, path: str, nested: bool) -> list[Violation]:
    out: list[Violation] = []
    status = r2["status"]
    if nested and r2["history"]:
        out += _bad(f"{path}.history", "a history entry carries no history of its own")
    if status in ("pass", "pass-with-waivers", "survivors"):
        out += _r2_complete(r2, path)
    elif status in ("incomplete", "error"):
        wanted = 3 if status == "incomplete" else 2
        if r2["campaign"] is None or r2["candidates"] is None:
            out += _bad(path, f"{status} needs the campaign and candidate counts")
        else:
            if r2["campaign"]["closeout_exit"] != wanted:
                out += _bad(f"{path}.campaign.closeout_exit", f"{status} closes out with exit {wanted}")
            if r2["candidates"]["executed"] > r2["candidates"]["total"]:
                out += _bad(f"{path}.candidates", "executed exceeds total")
    elif status == "partial":
        states = {lane["state"] for lane in r2["lanes"]}
        if states != {"complete", "not-runnable"}:
            out += _bad(f"{path}.lanes", "partial needs at least one complete and one not-runnable lane")
    elif status == "waived":
        if r2["waiver"] is None:
            out += _bad(f"{path}.waiver", "a waived level names its waiver")
    else:  # pending, not-applicable, unknown carry no campaign data
        empty = (
            r2["campaign"] is None and r2["candidates"] is None and r2["buckets"] is None
            and r2["survivors"] is None and r2["score"] is None and not r2["worker_counts"]
            and not r2["lanes"] and r2["waiver"] is None
        )
        if not empty:
            out += _bad(path, f"{status} carries no campaign data")
    if status != "waived" and r2["waiver"] is not None:
        out += _bad(f"{path}.waiver", "only a waived level carries a level waiver")
    return out + _r2_scope(r2["scope"], f"{path}.scope")


def _toolset_refs(doc: dict) -> list[tuple[str, str]]:
    refs = [(f"$.levels.{n}.toolset", doc["levels"][n]["toolset"]) for n in ("R0", "R1", "R3", "R2")]
    refs += [(f"$.levels.R2.history[{i}].toolset", h["toolset"]) for i, h in enumerate(doc["levels"]["R2"]["history"])]
    refs += [(f"$.qualifications.{k}.toolset", v["toolset"]) for k, v in doc["qualifications"].items()]
    return refs


def _record_semantics(doc: dict) -> list[Violation]:
    out: list[Violation] = []
    rel = doc["release"]
    if not rel["tag"].endswith(f"-v{rel['version']}"):
        out += _bad("$.release.version", "must be the version part of the tag")
    if (rel["state"] == "withdrawn") != (rel["withdrawn"] is not None):
        out += _bad("$.release.withdrawn", "set exactly when the state is withdrawn")
    for path, ref in _toolset_refs(doc):
        if ref is not None and ref not in doc["toolsets"]:
            out += _bad(path, "toolset is not declared in toolsets")
    levels = doc["levels"]
    for name in ("R0", "R1", "R3"):
        out += _gate_level(levels[name], f"$.levels.{name}", name)
    for key, qual in doc["qualifications"].items():
        out += _gate_level(qual, f"$.qualifications.{key}", None)
    out += _review(levels["review"], "$.levels.review")
    out += _r2(levels["R2"], "$.levels.R2", nested=False)
    for i, entry in enumerate(levels["R2"]["history"]):
        out += _r2(entry, f"$.levels.R2.history[{i}]", nested=True)
    class_out, ids = _check_machine_classes(doc["machine_classes"], "$.machine_classes")
    return out + class_out + _check_perf(doc["perf"], ids, "$.perf")


def validate_record(document: Any, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Violation]:
    """Validate one ``urn:vbpub:rigor-record:1`` document (schema, semantics, privacy, 64 KiB limit)."""
    violations = Validator("rigor-record-1.json").validate(document)
    if not violations:
        violations = _record_semantics(document) + check_size("record", canonical_json(document))
    return violations + _privacy(document, hosts, users)


# -- waivers --------------------------------------------------------------------------------------------------
def _waiver_semantics(doc: dict) -> list[Violation]:
    out: list[Violation] = []
    seen_lanes: set[tuple[str, str]] = set()
    for i, w in enumerate(doc.get("lane", [])):
        key = (w["lane"], w["level"])
        if key in seen_lanes:
            out += _bad(f"$.lane[{i}]", "duplicate (lane, level) waiver")
        seen_lanes.add(key)
    seen_ids: set[str] = set()
    for i, s in enumerate(doc.get("survivor", [])):
        if s["candidate_id"] in seen_ids:
            out += _bad(f"$.survivor[{i}].candidate_id", "duplicate survivor disposition")
        seen_ids.add(s["candidate_id"])
    return out


def validate_waivers(document: Any, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Violation]:
    """Validate the parsed form of a ``waivers.toml`` (schema, semantics, privacy)."""
    violations = Validator("rigor-waivers-1.json").validate(document)
    if not violations:
        violations = _waiver_semantics(document)
    return violations + _privacy(document, hosts, users)


def load_waivers(text: str, **kwargs: Any) -> tuple[dict | None, list[Violation]]:
    """Parse and validate ``waivers.toml`` text. Returns ``(document, violations)``; the document is ``None`` on a TOML error."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return None, _bad("$", f"not valid TOML: {exc}")
    return document, validate_waivers(document, **kwargs)
