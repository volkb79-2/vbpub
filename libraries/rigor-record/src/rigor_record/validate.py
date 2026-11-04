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

#: Size limits in bytes (SPEC 10).
LIMITS = {"record": 64 * 1024, "fragment": 256 * 1024, "release-body": 8 * 1024, "view": 128 * 1024}

#: Minimum sample count at which a percentile is reported (run-gate owns the computation; this is the structural rule).
MIN_N = {50: 1, 90: 10, 98: 50, 99: 100}

#: Fields of a lane result that exist exactly when the result holds R2.
R2_KEYS = ("scope", "campaign", "candidates", "buckets", "score", "worker_counts", "survivors", "deviations",
           "attributed_from", "history")
#: Worst-first ordering of stored statuses (higher is worse).
STATUS_RANK = {"pass": 0, "pass-with-waivers": 1, "survivors": 2, "fail": 3, "incomplete": 4, "error": 5}
_GATE_STATUSES = ("pass", "fail")
_R2_STATUSES = ("pass", "pass-with-waivers", "survivors", "incomplete", "error")

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
    # the schema already fixes which fields a measured and a published-table speed carry
    if speed["basis"].partition("-")[0] != f"geekbench{speed['geekbench_major']}":
        out += _bad(f"{path}.basis", "must name the speed's own Geekbench major")
    version = speed["geekbench_version"]
    if version is not None and int(version.split(".")[0]) != speed["geekbench_major"]:
        out += _bad(f"{path}.geekbench_major", "must be the major part of geekbench_version")
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
        norm = entry["normalisation"]
        for cid in entry["machine_classes"]:
            if cid not in classes:
                out += _bad(f"{base}.machine_classes.{cid}", "machine class is not declared")
                continue
            speed = classes[cid]["speed"]
            if "basis" not in speed:
                continue
            if norm["geekbench_major"] not in (None, speed["geekbench_major"]):
                out += _bad(f"{base}.machine_classes.{cid}", "Geekbench majors must not be mixed within one entry")
            elif norm["reference_score"] not in (None, speed["baseline_score"]):
                out += _bad(f"{base}.normalisation.reference_score", f"must equal the baseline_score of machine class {cid}")
        has_basis = norm["basis"] != "none"
        major = norm["geekbench_major"]
        consistent = (
            (norm["divisor"] == "single_core") == has_basis
            and (major is not None) == has_basis
            and (norm["reference_score"] is not None) == has_basis
            and (not has_basis or norm["basis"].startswith(f"geekbench{major}-"))
            and (norm["reason"] is None) == has_basis
        )
        if not consistent:
            out += _bad(f"{base}.normalisation",
                        "basis none needs no major, divisor none, no reference score and a reason; "
                        "otherwise a basis naming the major, single_core, the machine classes' baseline score and no reason")
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
    if doc["kind"] == "run-gate-composite":
        if len(set(doc["members"])) != len(doc["members"]) or doc["lane"] in doc["members"]:
            out += _bad("$.members", "members are distinct lanes other than the sequence itself")
        return out
    if doc["kind"] == "assay-lane":
        if (doc["outcome"] == "PASS") != (doc["reason"] is None):
            out += _bad("$.reason", "reason is null exactly when the outcome is PASS")
        levels = [claim["rigor"] for claim in doc.get("claims", [])]
        if levels != sorted(set(levels)):
            out += _bad("$.claims", "claims are ordered R0..R3 with one entry per level")
        for key in ("inputs", "judged_paths"):
            if doc.get(key, []) != sorted(set(doc.get(key, []))):
                out += _bad(f"$.{key}", f"{key} are sorted and distinct")
        if doc["r2"] is not None:
            out += _check_assay_r2(doc["r2"], "$.r2")
        return out
    if doc["started_at"] > doc["completed_at"]:
        out += _bad("$.completed_at", "completed_at precedes started_at")
    class_out, ids = _check_machine_classes(doc["machine_classes"], "$.machine_classes")
    return out + class_out + _check_perf(doc["perf"], ids, "$.perf")


_FRAGMENT_POINTERS = {
    "assay-lane": "/$defs/assay_lane",
    "run-gate-lane": "/$defs/run_gate_lane",
    "run-gate-composite": "/$defs/run_gate_composite",
}


def validate_fragment(document: Any, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Violation]:
    """Validate one ``urn:vbpub:rigor-fragment:1`` document (schema, semantics, privacy)."""
    if not isinstance(document, dict) or document.get("kind") not in _FRAGMENT_POINTERS:
        return _bad("$.kind", "must be assay-lane, run-gate-lane or run-gate-composite")
    violations = Validator("rigor-fragment-1.json").validate(document, _FRAGMENT_POINTERS[document["kind"]])
    if not violations:
        violations = _fragment_semantics(document)
    return violations + _privacy(document, hosts, users)


# -- records --------------------------------------------------------------------------------------------------
def _review(review: dict, path: str) -> list[Violation]:
    filled = [review["range"], review["reviewer_role"], review["document_sha256"]]
    accept = review["status"] == "accept"
    if accept != all(v is not None for v in filled) or (not accept and any(v is not None for v in filled)):
        return _bad(path, "accept needs range, reviewer role and document digest; missing carries none")
    return []


def _r2_survivors(surv: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    names = ("open", "equivalent_reviewed", "accepted", "resolved")
    total = sum(surv[n] for n in names)
    listed = surv["listed"]
    if total <= 200:
        if len(listed) != total or surv["by_file"] or surv["sidecar_sha256"] is not None:
            out += _bad(path, "up to 200 survivors are all listed, with no per-file counts and no sidecar")
        for name in names:
            if sum(1 for s in listed if s["disposition"].replace("-", "_") == name) != surv[name]:
                out += _bad(f"{path}.{name}", "count differs from the listed survivors")
    else:
        if listed or sum(surv["by_file"].values()) != total or surv["sidecar_sha256"] is None:
            out += _bad(path, "over 200 survivors collapse to per-file counts that add up, plus the sidecar digest")
    for i, s in enumerate(listed):
        if (s["disposition"] == "resolved") != (s["resolved_by"] is not None):
            out += _bad(f"{path}.listed[{i}].resolved_by", "set exactly when the disposition is resolved")
        if s["disposition"] in ("equivalent-reviewed", "accepted") and s["reason"] is None:
            out += _bad(f"{path}.listed[{i}].reason", "an equivalent-reviewed or accepted survivor states why")
    return out


def _r2_complete(result: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    status = result["levels"]["R2"]
    cand, buckets, surv = result["candidates"], result["buckets"], result["survivors"]
    if cand["executed"] != cand["total"]:
        out += _bad(f"{path}.candidates", f"{status} means every candidate ran")
    if sum(buckets.values()) != cand["executed"]:
        out += _bad(f"{path}.buckets", "bucket counts must add up to candidates.executed")
    if result["campaign"]["closeout_exit"] not in (0, 1):
        out += _bad(f"{path}.campaign.closeout_exit", "a completed campaign closes out with exit 0 or 1")
    names = ("open", "equivalent_reviewed", "accepted", "resolved")
    if sum(surv[n] for n in names) != buckets["survived"]:
        out += _bad(f"{path}.survivors", "survivor dispositions must add up to buckets.survived")
    waived = surv["equivalent_reviewed"] + surv["accepted"]
    if status == "survivors" and surv["open"] < 1:
        out += _bad(f"{path}.levels.R2", "survivors needs at least one open survivor")
    if status == "pass-with-waivers" and (surv["open"] != 0 or waived < 1):
        out += _bad(f"{path}.levels.R2", "pass-with-waivers needs no open survivor and at least one equivalent-reviewed or accepted one")
    if status == "pass" and (surv["open"] != 0 or waived != 0):
        out += _bad(f"{path}.levels.R2", "pass needs no open, equivalent-reviewed or accepted survivor")
    return out + _r2_survivors(surv, f"{path}.survivors")


def _r2_scope(scope: dict, path: str) -> list[Violation]:
    if scope["kind"] == "whole-source":
        if scope["base_commit"] is not None or "base_label" in scope or not scope["anchored"]:
            return _bad(path, "a whole-source campaign has no base and is anchored")
    elif scope["base_commit"] is None:
        return _bad(path, "a changed-since campaign names its base commit")
    return []


def _r2_result(result: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    status = result["levels"]["R2"]
    if status in ("pass", "pass-with-waivers", "survivors"):
        if "survivors" not in result:
            return out + _bad(path, f"{status} needs the survivor block")
        out += _r2_complete(result, path)
    else:
        wanted = 3 if status == "incomplete" else 2
        if "survivors" in result:
            out += _bad(f"{path}.survivors", f"{status} carries no survivor block")
        if result["campaign"]["closeout_exit"] != wanted:
            out += _bad(f"{path}.campaign.closeout_exit", f"{status} closes out with exit {wanted}")
        if result["candidates"]["executed"] > result["candidates"]["total"]:
            out += _bad(f"{path}.candidates", "executed exceeds total")
    return out + _r2_scope(result["scope"], f"{path}.scope")


def _lane_result(result: dict, path: str) -> list[Violation]:
    out: list[Violation] = []
    levels = result["levels"]
    if not levels:
        out += _bad(f"{path}.levels", "a result holds at least one level")
    if not result["targets"]:
        out += _bad(f"{path}.targets", "a result names the targets it judged")
    if result["kind"] == "command":
        if result["judged_by"] != "run-gate" or list(levels) != ["R0"] or result.get("note") != "declared, not judged" or (
            result["verdict_sha256"] is not None or result["toolset"] is None
        ):
            out += _bad(path, "a command lane is judged by run-gate, holds R0 only, is noted 'declared, not judged' and has no verdict")
    else:
        if result["judged_by"] != "assay" or "R0" not in levels or "note" in result or result["verdict_sha256"] is None:
            out += _bad(path, "an assay lane is judged by assay, holds R0, carries a verdict digest and no note")
    for name in ("R0", "R1", "R3"):
        if name in levels and levels[name] not in _GATE_STATUSES:
            out += _bad(f"{path}.levels.{name}", "R0, R1 and R3 are stored as pass or fail")
    if "R2" in levels and levels["R2"] not in _R2_STATUSES:
        out += _bad(f"{path}.levels.R2", "R2 is stored as pass, pass-with-waivers, survivors, incomplete or error")
    failed = [n for n in ("R0", "R1", "R3") if levels.get(n) == "fail"]
    if bool(failed) != ("waiver" in result):
        out += _bad(f"{path}.waiver", "a failed level is stored only with its waiver, and a waiver needs a failed level")
    if ("coverage" in result) != ("R1" in levels):
        out += _bad(f"{path}.coverage", "coverage is stored exactly when the result holds R1")
    elif levels.get("R1") == "pass":
        cov = result["coverage"]
        if cov["line_pct"] < cov["floor"]["line"] or cov["branch_pct"] < cov["floor"]["branch"]:
            out += _bad(f"{path}.coverage", "R1 pass needs coverage at or above its floor")
    if ("canary" in result) != ("R3" in levels):
        out += _bad(f"{path}.canary", "a canary is stored exactly when the result holds R3")
    elif levels.get("R3") == "pass" and result["canary"]["outcome"] != "PASS":
        out += _bad(f"{path}.canary", "R3 pass needs a passing canary")
    present = [k for k in R2_KEYS if k in result]
    if "R2" in levels:
        missing = [k for k in R2_KEYS if k not in result and k != "survivors"]
        if missing:
            out += _bad(path, f"an R2 result carries {', '.join(missing)}")
        else:
            out += _r2_result(result, path)
    elif present:
        out += _bad(path, f"{', '.join(present)} belong to an R2 result")
    return out


def _toolset_refs(doc: dict) -> list[tuple[str, str | None]]:
    refs: list[tuple[str, str | None]] = []
    for i, r in enumerate(doc["results"]):
        refs.append((f"$.results[{i}].toolset", r["toolset"]))
        refs += [(f"$.results[{i}].history[{j}].toolset", h["toolset"]) for j, h in enumerate(r.get("history", []))]
    for name, qual in doc["qualifications"].items():
        refs += [(f"$.qualifications.{name}.results[{i}].toolset", r["toolset"]) for i, r in enumerate(qual["results"])]
    return refs


def _record_semantics(doc: dict) -> list[Violation]:
    out: list[Violation] = []
    tags = [label["tag"] for label in doc["labels"]]
    if len(set(tags)) != len(tags):
        out += _bad("$.labels", "duplicate label tag")
    for i, label in enumerate(doc["labels"]):
        if not label["tag"].endswith(f"-v{label['version']}"):
            out += _bad(f"$.labels[{i}].version", "must be the version part of the tag")
        if (label["state"] == "withdrawn") != (label["withdrawn"] is not None):
            out += _bad(f"$.labels[{i}].withdrawn", "set exactly when the state is withdrawn")
    for path, ref in _toolset_refs(doc):
        if ref is not None and ref not in doc["toolsets"]:
            out += _bad(path, "toolset is not declared in toolsets")
    lanes: set[str] = set()
    sequences = []
    for i, result in enumerate(doc["results"]):
        if result["lane"] in lanes:
            out += _bad(f"$.results[{i}].lane", "duplicate lane result")
        lanes.add(result["lane"])
        if result["kind"] == "sequence":
            sequences.append(result)
            continue
        out += _lane_result(result, f"$.results[{i}]")
        for j, attempt in enumerate(result.get("history", [])):
            out += _r2_scope(attempt["scope"], f"$.results[{i}].history[{j}].scope")
        if "R2" in result["levels"] and result["lane"] not in doc["inventory"]["r2_lanes"]:
            out += _bad(f"$.results[{i}]", "an R2 result's lane is listed in inventory.r2_lanes")
    inv = doc["inventory"]
    if len(sequences) > 1:
        out += _bad("$.results", "at most one sequence result")
    if sequences:
        seq = sequences[0]
        if inv["sequence"] != seq["lane"] or inv["members"] != seq["members"]:
            out += _bad("$.inventory", "the inventory names the sequence result's lane and members")
    elif inv["sequence"] is not None or inv["members"]:
        out += _bad("$.inventory", "an inventory with a sequence or members needs the sequence result")
    if not set(inv["r2_lanes"]).isdisjoint(inv["members"]):
        out += _bad("$.inventory.r2_lanes", "an R2 lane is not a member of the release sequence")
    out += _review(doc["review"], "$.review")
    for name, qual in doc["qualifications"].items():
        for i, result in enumerate(qual["results"]):
            out += _lane_result(result, f"$.qualifications.{name}.results[{i}]")
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


def validate_machine(document: Any) -> list[Violation]:
    """Validate a cgprofile ``machine/1`` object against the vendored copy of its owner's schema.

    The shape belongs to cgprofile (PROTOCOL section 11); the only rule added here is that a speed's ``basis``
    names the same Geekbench major as its ``geekbench_major`` (``geekbench7-measured`` with major 7). There is no
    privacy lint: this is the producer's local object (a table speed carries a source URL); the record keeps only a
    projection of it.
    """
    violations = Validator("rigor-machine-1.json").validate(document)
    speed = document.get("speed") if not violations else None
    if speed and speed["basis"].split("-")[0] != f"geekbench{speed['geekbench_major']}":
        violations += _bad("$.speed.basis", "must name the speed's own Geekbench major")
    return violations


def validate_index(document: Any, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> list[Violation]:
    """Validate a generated ``rigor/index.json`` (schema and privacy)."""
    return Validator("rigor-index-1.json").validate(document) + _privacy(document, hosts, users)
