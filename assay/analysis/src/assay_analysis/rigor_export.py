"""``assay analyze export``: one verified verdict becomes a ``urn:vbpub:rigor-fragment:1`` judgment-fact fragment.

The fragment carries judgment facts only (outcome, coverage, canary, mutation counts, survivors) and no resource
number. Assay does not import the library that owns the fragment schema: the schema is a published JSON contract,
and this module restates the few string patterns it needs so that it can refuse to emit a value the schema would
reject. A tiny helper in the tests validates the output against the vendored schema and goldens.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from assay import git
from assay.mutation import JUDGE_IDENTITY_LABEL
from assay.verdict import VERDICT_SCHEMA_VERSION

from assay_analysis import evidence

FRAGMENT_SCHEMA = "urn:vbpub:rigor-fragment:1"
MAX_LISTED_SURVIVORS = 500
MAX_FRAGMENT_BYTES = 256 * 1024
MAX_CANARY_TARGETS = 50
MAX_LISTED_PATHS = 64
LEVELS = ("R0", "R1", "R2", "R3")

_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_LANE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPERATOR = re.compile(r"^[a-z]+:[a-z0-9-]+$")
_TEST_NODE = re.compile(r"^[A-Za-z0-9_./:\[\]-]{1,256}$")
_RELPATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*(?:/[A-Za-z0-9_][A-Za-z0-9._+-]*)*$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_OID = re.compile(r"^[0-9a-f]{40}$")
_BUCKETS = ("killed", "survived", "equivalent", "hung", "budget_exceeded", "crashed")


def _canonical(document) -> str:
    """The serialisation committed fixtures use: sorted keys, 2-space indent, ASCII, final newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _checked(pattern: re.Pattern, value, field: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} {value!r} cannot be represented in a rigor fragment")
    return value


def _outcome(status: str) -> str:
    """Assay's six statuses onto the fragment's closed PASS | FAIL | ERROR set (RRK-03)."""
    return status if status in ("PASS", "FAIL") else "ERROR"


def _reason(status: str, reason_code) -> str | None:
    # A verified verdict always carries a reason_code unless it passed.
    return None if status == "PASS" else _checked(_REASON, reason_code, "reason_code")


def _timestamp(text: str) -> str:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"progress timestamp {text!r} is not ISO 8601") from exc
    if moment.tzinfo is None:
        raise ValueError(f"progress timestamp {text!r} has no UTC offset")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lane_config_sha256(lane_file: Path, lane: str) -> str:
    """Digest of the lane's declaration: canonical JSON of ``schema_version`` and the ``[lanes.<lane>]`` table."""
    document = tomllib.loads(lane_file.read_text(encoding="utf-8"))
    lanes = document.get("lanes")
    if not isinstance(lanes, dict) or lane not in lanes:
        raise ValueError(f"lane {lane!r} is not declared in {lane_file.name}")
    return _sha256(_canonical({"schema_version": document.get("schema_version"), "lane": lanes[lane]}))


def project_identity(lane_file: Path, commit: str, project: str | None) -> tuple[str, str]:
    """``(project name, project tree oid at commit)`` for the project directory that holds the lane file."""
    directory = lane_file.resolve().parent
    top = Path(git.run(directory, "rev-parse", "--show-toplevel").strip()).resolve()
    relative = directory.relative_to(top).as_posix()
    prefix = "" if relative == "." else relative
    spec = f"{commit}:{prefix}" if prefix else f"{commit}^{{tree}}"
    tree = git.run(directory, "rev-parse", "--verify", spec).strip()
    name = project if project is not None else directory.name.lower()
    return _checked(_PROJECT, name, "project"), _checked(_OID, tree, "project tree")


def _claims(document: dict) -> dict:
    return {claim["rigor"]: claim for claim in document["claims"]}


def claim_set(document: dict) -> list[dict]:
    """A-1a: ``[{rigor, outcome}]`` for every verified computed claim at R0..R3, ordered by level.

    Only a claim assay itself verified is listed (an unverified one is absent, never listed as failing); R4 is outside
    the fragment's level vocabulary and is not carried. This list, and nothing else, is what a consumer may read as
    "this lane's verdict proves level X".
    """
    return [{"rigor": claim["rigor"], "outcome": _outcome(claim["status"])}
            for claim in sorted(document["claims"], key=lambda c: c["rigor"])
            if claim["rigor"] in LEVELS and claim.get("verified_by_assay") is True]


def lane_paths(lane_file: Path, lane: str) -> tuple[list[str], list[str]]:
    """A-1a: ``(inputs, judged_paths)`` as the lane file declares them.

    ``inputs`` is ``isolation.link_paths`` (repository-top-relative, out-of-project by construction); ``judged_paths``
    is ``judge.source_roots`` (project-relative). Both are sorted and checked against the schema's path grammar.
    """
    declared = tomllib.loads(lane_file.read_text(encoding="utf-8"))["lanes"][lane]
    inputs = declared.get("isolation", {}).get("link_paths", [])
    roots = declared.get("judge", {}).get("source_roots", [])
    return (sorted(_checked(_RELPATH, path, "link_paths entry") for path in inputs),
            sorted(_checked(_RELPATH, path, "source_roots entry") for path in roots))


def _coverage(document: dict, claim: dict | None):
    if claim is None or "coverage" not in claim:
        return None
    data = claim["coverage"]
    r1 = document["judgment"]["r1"]
    floor = float(r1["fail_under"])
    reported = data.get("branch_capability") == "reported"
    total = data.get("branches_total", 0)
    if total:
        branch = 100.0 * data["branches_covered"] / total
    else:
        # No branch arcs exist to miss, unless the format could not report them at all.
        branch = 100.0 if reported else 0.0
    return {
        "line_pct": float(data["pct"]),
        "branch_pct": round(branch, 4),
        # Branches are judged against the same floor whenever the format reports them (or the lane requires them).
        "floor": {"line": floor, "branch": floor if reported or r1.get("require_branch") else 0},
        "excluded_lines": sum(len(lines) for lines in data.get("excluded_lines", {}).values()),
    }


def _canary(claim: dict | None):
    if claim is None or "canary" not in claim:
        return None
    canary = claim["canary"]
    targets = [_checked(_RELPATH, attempt["target"], "canary target") for attempt in canary["attempts"]]
    if not 1 <= len(targets) <= MAX_CANARY_TARGETS:
        raise ValueError("canary target count cannot be represented in a rigor fragment")
    return {
        "mechanism": _checked(_IDENTIFIER, canary["mechanism"], "canary mechanism"),
        "targets": targets,
        "outcome": _outcome(claim["status"]),
    }


def _progress(path: Path, commit: str) -> dict:
    """Campaign times and resumed count of the newest progress run at ``commit`` (first ``run`` to last event)."""
    text, _ = evidence._read(path)
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        event = evidence._json(line)
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"malformed progress event at line {number}")
        if event["event"] == "run":
            current = ({"started": event.get("started"), "ended": event.get("emitted_at"), "resumed": 0}
                       if event.get("commit") == commit else None)
            continue
        if current is None:
            continue
        if event["event"] == "resume":
            current["resumed"] += int(event.get("resumed_total", 0))
        if event.get("emitted_at") is not None:
            current["ended"] = event["emitted_at"]
    if current is None:
        raise ValueError(f"progress has no run at commit {commit}")
    return current


def _nearest_failing_test(survivor: dict, killed: list[dict]):
    """The ``first_failing_test`` of the closest killed mutant in the same file, or ``None`` when unknown."""
    same = [k for k in killed if k["path"] == survivor["path"] and k.get("first_failing_test")]
    if not same:
        return None
    best = min(same, key=lambda k: (abs(k["lineno"] - survivor["lineno"]), k["lineno"], k["candidate_id"]))
    return best["first_failing_test"] if _TEST_NODE.fullmatch(best["first_failing_test"]) else None


def _survivors(mutation: dict) -> list[dict]:
    killed = [k for k in mutation["killed"] if "candidate_id" in k]
    out = []
    for item in mutation["survived"]:
        if "candidate_id" not in item:
            raise ValueError("a survivor lacks candidate_id; the fragment cannot name it")
        out.append({
            "candidate_id": _checked(_IDENTIFIER, item["candidate_id"], "survivor candidate_id"),
            "path": _checked(_RELPATH, item["path"], "survivor path"),
            "line": int(item["lineno"]),
            "operator": _checked(_OPERATOR, item["operator"], "survivor operator"),
            "first_failing_test": _nearest_failing_test(item, killed),
        })
    return sorted(out, key=lambda s: (s["path"], s["line"], s["candidate_id"]))


def _r2(document: dict, claim: dict | None, progress: dict | None):
    if claim is None or "mutation" not in claim:
        return None, None
    mutation = claim["mutation"]
    judgment = document["judgment"]["r2"]
    buckets = {name: len(mutation[name]) for name in _BUCKETS}
    executed = sum(buckets.values())
    total = mutation["candidate_count"]
    if executed > total:
        raise ValueError("mutation buckets exceed candidate_count; the verdict cannot be summarised")
    resumed = min(progress["resumed"], executed) if progress else 0
    decided = buckets["killed"] + buckets["survived"]
    survivors = _survivors(mutation)
    sidecar = None
    sidecar_sha = None
    if len(survivors) > MAX_LISTED_SURVIVORS:
        sidecar = _canonical(survivors)
        sidecar_sha = _sha256(sidecar)
    profile = judgment["profile"]["status_counts"]
    return {
        "candidates": {"total": total, "executed": executed, "resumed": resumed},
        "buckets": buckets,
        "score": round(buckets["killed"] / decided, 4) if decided else None,
        "worker_counts": dict(judgment["worker_counts"]),
        "profile_status_counts": {key: profile[key] for key in ("MEASURED", "PARTIAL", "NO_MEASUREMENT")},
        "campaign": ({"started_at": _timestamp(progress["started"]), "completed_at": _timestamp(progress["ended"])}
                     if progress and progress["started"] and progress["ended"] else None),
        "survivors": {"total": len(survivors), "listed": survivors[:MAX_LISTED_SURVIVORS],
                      "sidecar_sha256": sidecar_sha},
    }, sidecar


def build_fragment(verdict: Path, expected_commit: str, lane: str, lane_file: Path,
                   progress: Path | None = None, project: str | None = None) -> tuple[dict, str | None]:
    """Return ``(fragment, survivor sidecar text or None)`` for one verified verdict."""
    text, artifact = evidence._read(verdict)
    document = evidence._json(text)
    version = document.get("schema_version") if isinstance(document, dict) else None
    if isinstance(version, int) and version < VERDICT_SCHEMA_VERSION:
        raise ValueError(f"VERDICT_SCHEMA_ARCHIVED: verdict schema {version} is archived; export reads schema "
                         f"{VERDICT_SCHEMA_VERSION} only, so re-produce the verdict with a current assay")
    result = evidence.inspect_verdict(verdict, expected_commit)  # verifies, refuses dirty overrides and wrong commit
    document = result["verdict"]
    _checked(_LANE, lane, "lane")
    if document["lane"] != lane:
        raise ValueError(f"verdict is for lane {document['lane']!r}, not {lane!r}")
    provenance = document.get("judge_provenance")
    if provenance is None:
        raise ValueError("verdict has no judge_provenance; export needs the judge's artifact digest "
                         "(run the lane with an installed assay wheel or zipapp)")
    claims = _claims(document)
    name, tree = project_identity(lane_file, document["commit"], project)
    config_sha256 = lane_config_sha256(lane_file, lane)  # refuses a lane the file does not declare
    inputs, judged_paths = lane_paths(lane_file, lane)
    if max(len(inputs), len(judged_paths)) > MAX_LISTED_PATHS:
        raise ValueError(f"a lane declaring more than {MAX_LISTED_PATHS} paths cannot be represented in a rigor fragment")
    r2, sidecar = _r2(document, claims.get("R2"), _progress(progress, document["commit"]) if progress else None)
    fragment = {
        "schema": FRAGMENT_SCHEMA,
        "kind": "assay-lane",
        "project": name,
        "lane": lane,
        "judged_commit": _checked(_OID, document["commit"], "commit"),
        "project_tree": tree,
        "outcome": _outcome(document["outcome"]),
        "reason": _reason(document["outcome"], document.get("reason_code")),
        "verdict_sha256": artifact["sha256"],
        "config_sha256": config_sha256,
        "verified": True,
        "judge": {
            "version": _checked(_SEMVER, provenance["version"], "judge version"),
            "artifact_sha256": provenance["digest"],
            "judge_identity": JUDGE_IDENTITY_LABEL,
            "verdict_schema": f"urn:assay:schema:verdict:{document['schema_version']}",
        },
        "coverage": _coverage(document, claims.get("R1")),
        "canary": _canary(claims.get("R3")),
        "r2": r2,
        "claims": claim_set(document),
        "inputs": inputs,
        "judged_paths": judged_paths,
    }
    return fragment, sidecar


def export(verdict: Path, expected_commit: str, lane: str, lane_file: Path, out: Path,
           progress: Path | None = None, project: str | None = None) -> dict:
    """Write the fragment (and the survivor sidecar beside it when needed); never overwrite an existing file."""
    fragment, sidecar = build_fragment(verdict, expected_commit, lane, lane_file, progress, project)
    body = _canonical(fragment)
    if len(body.encode("utf-8")) > MAX_FRAGMENT_BYTES:
        raise ValueError("fragment exceeds 256 KiB")
    sidecar_path = out.with_name(out.name + ".survivors.json") if sidecar is not None else None
    for target in (out, sidecar_path):
        if target is not None and target.exists():
            raise ValueError(f"refusing to overwrite {target}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if sidecar_path is not None:
        sidecar_path.write_text(sidecar, encoding="utf-8")
    out.write_text(body, encoding="utf-8")
    result = {"schema": FRAGMENT_SCHEMA, "fragment": str(out), "fragment_sha256": _sha256(body),
              "outcome": fragment["outcome"], "reason": fragment["reason"]}
    if sidecar_path is not None:
        result["survivor_sidecar"] = str(sidecar_path)
        result["survivor_sidecar_sha256"] = fragment["r2"]["survivors"]["sidecar_sha256"]
    return result
