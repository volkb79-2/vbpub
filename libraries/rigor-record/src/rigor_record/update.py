"""``update``: the pure record transitions of the post-release workflow (memo 3.4, 3.5, 5.3-5.5).

Every function takes already-loaded records (keyed by COMMIT) and returns a :class:`Plan` (or ``None`` when there is
nothing to do); none of them touches the clock or the network, and git only through :class:`GitFacts`. The git
procedure that commits a plan is :mod:`rigor_record.store`.

R2 evidence enters here: one lane result per R2 fragment, stored in the record of the commit it judged (or, with
``attribute_to``, of a published commit whose targets are identical). A record may be created for an unlabelled
commit. The allowed stored R2 transitions are :data:`TRANSITIONS`; the other verbs move what they name:
``reconcile`` (``survivors`` to ``pass-with-waivers``) and ``withdraw`` (a label's state). R2 waivers live only in
``rigor/waivers.toml`` and are derived on read; no per-result ``waived`` status is stored.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .assemble import (
    Refusal,
    _group_fragments,
    _Group,
    _load_fragments,
    _merge_machines,
    _result,
    _targets,
    _toolset_of,
)
from .gitfacts import GitError, GitFacts
from .validate import _semver_key, load_waivers, validate_record

#: Stored R2 transitions (memo 3.4). ``error`` re-runs like ``incomplete`` resumes (memo 5.6).
TRANSITIONS: dict[str, frozenset[str]] = {
    "incomplete": frozenset({"pass", "survivors", "incomplete", "error"}),
    "error": frozenset({"pass", "survivors", "incomplete", "error"}),
    "survivors": frozenset({"pass-with-waivers", "survivors", "pass"}),
}
#: Entries kept in a result's ``history`` (memo 3.4).
HISTORY_LIMIT = 5
#: Above this many survivors the record keeps per-file counts only (SPEC 4).
LISTED_LIMIT = 200
FRAGMENT_LIMIT = 500
_OID = re.compile(r"[0-9a-f]{40}")
_DISPOSITIONS = ("open", "equivalent-reviewed", "accepted", "resolved")
_ATTEMPT_KEYS = ("toolset", "scope", "campaign", "candidates", "buckets", "score", "worker_counts")


@dataclass(frozen=True)
class Plan:
    """What one ``update`` changes: new versions of some records (keyed by commit), and the commit that will carry them."""

    commit: str
    changed: dict[str, dict]
    subject: str
    trailers: tuple[str, ...]
    key: str


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def fragment_key(digests: Iterable[str], extra: str = "") -> str:
    """Idempotency key of a fragment set: sha256 of the sorted fragment digests (memo 3.2), first 12 hex.

    ``extra`` folds in what else decides the outcome (the attribution target).
    """
    return sha12("\n".join(sorted(digests)) + extra)


def qualification_key(name: str, commit: str, digests: Iterable[str]) -> str:
    """Like :func:`fragment_key`, but the same fragments recorded under another name or commit are another update."""
    return sha12(name + "\n" + commit + "\n" + "\n".join(sorted(digests)))


def withdraw_key(tag: str, reason: str, replacement: str) -> str:
    return sha12(json.dumps(["withdraw", tag, replacement, reason]))


def label_commits(records: dict[str, dict]) -> dict[str, str]:
    """``{tag: commit}`` over every label of every record."""
    return {label["tag"]: commit for commit, record in records.items() for label in record["labels"]}


def name_of(record: dict) -> str:
    """The newest label's tag, else the short commit: how a record is named in subjects and messages."""
    labels = record["labels"]
    return max(labels, key=lambda label: _semver_key(label["version"]))["tag"] if labels else record["commit"][:12]


def _plan(primary: str, changed: dict[str, dict], level: str, status: str, key: str) -> Plan:
    order = [primary] + sorted(c for c in changed if c != primary)
    trailers = tuple(f"Rigor-Record: {changed[c]['project']}@{c} rev={changed[c]['revision']} fragments={key}" for c in order)
    return Plan(primary, changed, f"chore(rigor): {name_of(changed[primary])} {level} {status}", trailers, key)


def _bump(record: dict) -> dict:
    out = copy.deepcopy(record)
    out["revision"] += 1
    return out


def _validated(record: dict, hosts: Iterable[str], users: Iterable[str]) -> dict:
    violations = validate_record(record, hosts=hosts, users=users)
    if violations:
        raise Refusal([f"record {record['commit'][:12]}: {v}" for v in violations])
    return record


def record_of(records: dict[str, dict], ref: str, git: GitFacts) -> dict:
    """The record ``ref`` names: a 40-hex commit, or a release tag (which must still resolve to the record's commit)."""
    if _OID.fullmatch(ref):
        if ref not in records:
            raise Refusal([f"{ref} has no record"])
        return records[ref]
    commit = label_commits(records).get(ref)
    if commit is None:
        raise Refusal([f"{ref} has no record; run rigor-record sync first"])
    try:
        actual = git.tag_commit(ref)
    except GitError as exc:
        raise Refusal([str(exc)]) from exc
    if actual != commit:
        raise Refusal([f"tag {ref} now resolves to {actual}, the record says {commit}: a moved tag refuses"])
    return records[commit]


def check_expected_revision(record: dict, expect: int | None) -> None:
    """Compare-and-swap (memo 3.2): ``expect`` is the revision the caller saw."""
    if expect is not None and expect != record["revision"]:
        raise Refusal([f"{name_of(record)} is at revision {record['revision']}, expected {expect}"])


def read_waivers(project_dir: Path, hosts: Iterable[str], users: Iterable[str]) -> dict | None:
    """``rigor/waivers.toml`` of the checkout, validated; ``None`` when absent."""
    path = project_dir / "rigor" / "waivers.toml"
    if not path.is_file():
        return None
    document, violations = load_waivers(path.read_text(encoding="utf-8"), hosts=hosts, users=users)
    if violations:
        raise Refusal([f"rigor/waivers.toml: {v}" for v in violations])
    return document


# -- transitions -----------------------------------------------------------------------------------------------
def check_transition(old: str, new: str) -> None:
    if new not in TRANSITIONS.get(old, frozenset()):
        raise Refusal([f"R2 {old} -> {new} is not an allowed transition"])


def _counts(listed: Sequence[dict]) -> dict[str, int]:
    return {d.replace("-", "_"): sum(1 for s in listed if s["disposition"] == d) for d in _DISPOSITIONS}


def closeout_of(assay: dict) -> int:
    """The close-out exit of an R2 campaign, read from its assay fragment (never supplied by the caller).

    2 when assay judged the lane ERROR, 3 when the campaign did not finish every candidate, else 1 with survivors
    and 0 without.
    """
    r2 = assay["r2"]
    if assay["outcome"] == "ERROR":
        return 2
    if r2["campaign"] is None or r2["candidates"]["executed"] != r2["candidates"]["total"]:
        return 3
    return 1 if r2["survivors"]["total"] else 0


def _status_from(closeout_exit: int, survivors_total: int) -> str:
    if closeout_exit == 2:
        return "error"
    if closeout_exit == 3:
        return "incomplete"
    return "survivors" if survivors_total else "pass"


def _survivors(r2: dict, fragment_digest: str) -> dict:
    s = r2["survivors"]
    if s["total"] > FRAGMENT_LIMIT:
        raise Refusal([f"{s['total']} survivors: over {FRAGMENT_LIMIT} the per-file counts need the sidecar list (RRK-09)"])
    listed = [{**x, "disposition": "open", "reason": None, "resolved_by": None} for x in s["listed"]]
    for x in listed:
        del x["first_failing_test"]
    zero = {"open": s["total"], "equivalent_reviewed": 0, "accepted": 0, "resolved": 0}
    if s["total"] <= LISTED_LIMIT:
        return {**zero, "listed": listed, "by_file": {}, "sidecar_sha256": None}
    by_file: dict[str, int] = {}
    for x in listed:
        by_file[x["path"]] = by_file.get(x["path"], 0) + 1
    # 201..500: the fragment itself holds the full list and is the sidecar.
    return {**zero, "listed": [], "by_file": by_file, "sidecar_sha256": fragment_digest}


def _toolset_id(record: dict, toolset: dict) -> tuple[str, dict]:
    toolsets = dict(record["toolsets"])
    for tid, known in toolsets.items():
        if known == toolset:
            return tid, toolsets
    number = 1
    while f"t{number}" in toolsets:
        number += 1
    toolsets[f"t{number}"] = toolset
    return f"t{number}", toolsets


def _new_record(project: str, commit: str) -> dict:
    return {
        "schema": "urn:vbpub:rigor-record:1", "project": project, "commit": commit, "revision": 1, "labels": [],
        "inventory": {"sequence": None, "members": [], "r2_lanes": []}, "toolsets": {}, "results": [],
        "review": {"status": "missing", "range": None, "reviewer_role": None, "document_sha256": None},
        "qualifications": {}, "perf": [], "machine_classes": [],
    }


def _put_result(record: dict, result: dict) -> None:
    """Replace the lane's result or add it; a sequence result stays last."""
    results = [r for r in record["results"] if r["lane"] != result["lane"]]
    sequences = [r for r in results if r["kind"] == "sequence"]
    record["results"] = [r for r in results if r["kind"] != "sequence"] + [result] + sequences


def _result_of(record: dict, lane: str) -> dict | None:
    return next((r for r in record["results"] if r["lane"] == lane and r["kind"] != "sequence"), None)


def _version_of(record: dict) -> tuple | None:
    versions = [_semver_key(label["version"]) for label in record["labels"]]
    return max(versions) if versions else None


def _scope(base: str | None, base_label: str | None, lane: str, commit: str, records: dict[str, dict]) -> dict:
    if base is None:
        return {"kind": "whole-source", "base_commit": None, "anchored": True}
    record = records.get(base)
    if record is None:
        raise Refusal([f"base {base} has no record in this project"])
    if base == commit:
        raise Refusal([f"base {base} is the judged commit itself"])
    held = _result_of(record, lane)
    status = None if held is None else held["levels"].get("R2")
    if status not in ("pass", "pass-with-waivers"):
        raise Refusal([f"base {base} is not a clean base for lane {lane}: its R2 is {status or 'absent'}"])
    scope = {"kind": "changed-since", "base_commit": base, "anchored": held["scope"]["anchored"]}
    if base_label is not None:
        if base_label not in [label["tag"] for label in record["labels"]]:
            raise Refusal([f"base label {base_label} is not a label of {base}"])
        scope["base_label"] = base_label
    return scope


def _resolve_older(
    new: dict, new_commit: str, lane: str, records: dict[str, dict], hosts: Iterable[str], users: Iterable[str],
) -> dict[str, dict]:
    """Mark older releases' open survivors ``resolved`` when this completed campaign no longer reports them (memo 3.5).

    "Older" is decided by the release labels' versions: a record without a label is never resolved this way.
    """
    survivors = new["survivors"]
    if survivors["by_file"]:  # the full list is in a sidecar: a candidate's absence cannot be shown
        return {}
    present = {s["candidate_id"] for s in survivors["listed"]}
    version = _version_of(records[new_commit]) if new_commit in records else None
    base = new["scope"]["base_commit"]
    base_version = None if base is None else _version_of(records[base])
    changed: dict[str, dict] = {}
    for commit, old in records.items():
        held = _result_of(old, lane)
        old_version = _version_of(old)
        if commit == new_commit or held is None or old_version is None or version is None or old_version >= version:
            continue
        if held["levels"].get("R2") != "survivors" or held["survivors"]["by_file"]:
            continue
        if base is not None and (base_version is None or base_version >= old_version):
            continue
        marks = [s for s in held["survivors"]["listed"] if s["disposition"] == "open" and s["candidate_id"] not in present]
        if not marks:
            continue
        updated = _bump(old)
        result = _result_of(updated, lane)
        assert result is not None
        for s in result["survivors"]["listed"]:
            if s["disposition"] == "open" and s["candidate_id"] not in present:
                s["disposition"], s["resolved_by"] = "resolved", new_commit
        counts = _counts(result["survivors"]["listed"])
        result["survivors"].update(counts)
        if not counts["open"]:
            result["levels"]["R2"] = "pass-with-waivers" if counts["equivalent_reviewed"] + counts["accepted"] else "pass"
        changed[commit] = _validated(updated, hosts, users)
    return changed


# -- R2 from fragments -----------------------------------------------------------------------------------------
def _r2_binding(group: _Group, project: str, project_dir: str, git: GitFacts) -> list[str]:
    problems: list[str] = []
    where = f"lane {group.lane}"
    docs = [d for d in (group.assay, group.gate) if d is not None]
    for doc in docs:
        if doc["project"] != project:
            problems.append(f"{where}: fragment is for project {doc['project']}, not {project}")
        try:
            actual = git.tree(doc["judged_commit"], project_dir)
        except GitError:
            problems.append(f"{where}: judged commit {doc['judged_commit']} is not in the repository")
            continue
        if actual != doc["project_tree"]:
            problems.append(f"{where}: fragment claims tree {doc['project_tree']} but its judged commit has {actual}")
    if len({d["judged_commit"] for d in docs}) > 1:
        problems.append(f"{where}: its fragments name different judged commits")
    return problems


def _attribution(
    group: _Group, targets: dict[str, str], unresolved: list[str], attribute_to: str | None, git: GitFacts,
) -> str:
    """The commit that carries the result: the judged commit if published, else ``attribute_to`` with identical targets."""
    judged = group.primary["judged_commit"]
    if attribute_to is not None and unresolved:
        raise Refusal([f"lane {group.lane}: declared inputs {unresolved} have no git identity, so identical targets at "
                       f"{attribute_to} cannot be shown"])
    if attribute_to is None:
        if not git.published(judged):
            raise Refusal([f"lane {group.lane}: judged commit {judged} is not published (reachable from origin/main or a tag); "
                           "pass --attribute-to <published commit> whose targets are identical"])
        return judged
    if not git.published(attribute_to):
        raise Refusal([f"--attribute-to {attribute_to} is not published (reachable from origin/main or a tag)"])
    problems = []
    for path, oid in sorted(targets.items()):
        try:
            there = git.tree(attribute_to, path)
        except GitError:
            there = None
        if there != oid:
            problems.append(f"lane {group.lane}: target {path} differs at {attribute_to} ({there}), the campaign judged {oid}")
    if problems:
        raise Refusal(problems)
    return attribute_to


def _r2_result(
    group: _Group, *, existing: dict | None, targets: dict[str, str], unresolved: list[str], toolset_id: str, scope: dict,
    attempts: int, closeout_exit: int, attributed_from: str | None, digest: str,
) -> dict:
    assert group.assay is not None
    campaign = group.assay["r2"]
    status = _status_from(closeout_exit, campaign["survivors"]["total"])
    history: list[dict] = []
    if existing is not None:
        old = existing["levels"].get("R2")
        if old is None:
            raise Refusal([f"lane {group.lane}: the commit's lane result holds no R2 (is it a release-sequence member?)"])
        if old not in ("incomplete", "error"):
            raise Refusal([f"lane {group.lane}: R2 is {old}, which is final: a later campaign marks resolved_by instead"])
        check_transition(old, status)
        previous = {"status": old, **{k: copy.deepcopy(existing[k]) for k in _ATTEMPT_KEYS}}
        history = (existing["history"] + [previous])[-HISTORY_LIMIT:]
    if campaign["campaign"] is None:
        raise Refusal([f"lane {group.lane}: the R2 block has no campaign times"])
    result: dict = {
        "lane": group.lane, "kind": "assay", "judged_by": "assay", "levels": {"R0": "pass", "R2": status}, "toolset": toolset_id,
        "targets": targets, "verdict_sha256": group.assay["verdict_sha256"], "config_sha256": group.primary["config_sha256"],
        "scope": scope, "campaign": {**campaign["campaign"], "attempts": attempts, "closeout_exit": closeout_exit},
        "candidates": campaign["candidates"], "buckets": campaign["buckets"], "score": campaign["score"],
        "worker_counts": campaign["worker_counts"], "deviations": [], "attributed_from": attributed_from, "history": history,
    }
    if unresolved:
        result["unresolved_inputs"] = unresolved
    if status in ("pass", "survivors"):
        result["survivors"] = _survivors(campaign, digest)
    return result


def plan_r2(
    *,
    records: dict[str, dict],
    fragments: Sequence[tuple[str, bytes]],
    lane: str | None = None,
    attempts: int,
    closeout_exit: int | None = None,
    base: str | None,
    base_label: str | None = None,
    attribute_to: str | None = None,
    project_dir: str,
    git: GitFacts,
    expect_revision: int | None = None,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Plan:
    """Apply a completed (or ended) R2 campaign's fragments: one lane result per lane, at the commit it judged (memo 5.3)."""
    hosts, users = tuple(hosts), tuple(users)
    loaded, problems = _load_fragments(fragments, hosts, users)
    groups, composite, group_problems = _group_fragments(loaded, sequence=False)
    problems += group_problems
    if composite is not None:
        problems.append("a run-gate-composite fragment belongs to assemble, not update")
    if not groups and not problems:
        problems.append("no fragments")
    if lane is not None and groups and sorted(groups) != [lane]:
        problems.append(f"R2 fragments cover lanes {sorted(groups)}, --lane pins {lane}")
    for name, g in sorted(groups.items()):
        if g.assay is None or g.assay["r2"] is None or "R2" not in [c["rigor"] for c in g.assay.get("claims", [])]:
            problems.append(f"lane {name}: the assay fragment carries no R2 claim and campaign data")
        elif {"rigor": "R0", "outcome": "PASS"} not in g.assay["claims"]:
            problems.append(f"lane {name}: an R2 campaign needs a passing R0 claim (the lane's baseline run)")
        elif g.gate is None:
            problems.append(
                f"lane {name}: the assay R2 fragment has no run-gate half (RRK-11): without it no tool identity, "
                "machine class or perf is recorded"
            )
        elif closeout_exit is not None and closeout_exit != closeout_of(g.assay):
            problems.append(
                f"lane {name}: --closeout-exit {closeout_exit} disagrees with the fragment, which closes out with {closeout_of(g.assay)}"
            )
    if problems:
        raise Refusal(problems)
    assays = {id(doc): digest for _n, digest, doc in loaded}
    projects = {g.primary["project"] for g in groups.values()}
    if len(projects) != 1:
        raise Refusal([f"R2 fragments are for projects {sorted(projects)}"])
    project = projects.pop()
    for g in groups.values():
        problems += _r2_binding(g, project, project_dir, git)
    if problems:
        raise Refusal(problems)
    working = {c: copy.deepcopy(r) for c, r in records.items()}
    touched: dict[str, dict] = {}
    statuses: list[str] = []
    pending_resolve: list[tuple[str, dict, str]] = []
    for name, g in sorted(groups.items()):
        assert g.assay is not None
        judged = g.assay["judged_commit"]
        targets, unresolved = _targets(g, project_dir, judged, git)
        commit = _attribution(g, targets, unresolved, attribute_to, git)
        attributed_from = None if commit == judged else judged
        if commit in working:
            record = working[commit]
            check_expected_revision(records[commit], expect_revision)
            if commit not in touched:
                record["revision"] += 1
        else:
            if expect_revision is not None:
                raise Refusal([f"{commit} has no record, expected revision {expect_revision}"])
            record = working[commit] = _new_record(project, commit)
        if name in record["inventory"]["members"]:
            raise Refusal([f"lane {name} is a member of the release sequence: R2 lanes are not members"])
        toolset_id, record["toolsets"] = _toolset_id(record, _toolset_of(g))
        scope = _scope(base, base_label, name, commit, working)
        result = _r2_result(
            g, existing=_result_of(record, name), targets=targets, unresolved=unresolved, toolset_id=toolset_id, scope=scope,
            attempts=attempts, closeout_exit=closeout_of(g.assay), attributed_from=attributed_from, digest=assays[id(g.assay)],
        )
        _put_result(record, result)
        if name not in record["inventory"]["r2_lanes"]:
            record["inventory"]["r2_lanes"] = sorted(record["inventory"]["r2_lanes"] + [name])
        record["perf"] = [p for p in record["perf"] if not (p["level"] == "R2" and p["lane"] == name)]
        classes, perf, machine_problems = _merge_machines([g], record["machine_classes"])
        if machine_problems:
            raise Refusal(machine_problems)
        record["machine_classes"] = classes
        record["perf"] += perf
        touched[commit] = record
        statuses.append(result["levels"]["R2"])
        if result["levels"]["R2"] in ("pass", "survivors"):
            pending_resolve.append((commit, result, name))
    changed = {commit: _validated(record, hosts, users) for commit, record in touched.items()}
    for commit, result, name in pending_resolve:
        for older, record in _resolve_older(result, commit, name, {**records, **changed}, hosts, users).items():
            if older not in changed:
                changed[older] = record
    primary = sorted(touched)[0]
    digests = [d for _n, d, _doc in loaded]
    suffix = "" if attribute_to is None else f"\nattribute-to {attribute_to}"
    shown = statuses[0] if len(set(statuses)) == 1 else "mixed"
    return _plan(primary, changed, "R2", shown, fragment_key(digests, suffix))


# -- qualifications --------------------------------------------------------------------------------------------
def plan_qualification(
    *,
    ref: str,
    name: str,
    records: dict[str, dict],
    fragments: Sequence[tuple[str, bytes]],
    project_dir: str,
    git: GitFacts,
    expect_revision: int | None = None,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Plan:
    """Record a post-publish qualification (for example ``promotion``) from the fragments of its lanes."""
    hosts, users = tuple(hosts), tuple(users)
    record = record_of(records, ref, git)
    check_expected_revision(record, expect_revision)
    if name in record["qualifications"]:
        raise Refusal([f"{name_of(record)} already has the qualification {name}; qualifications are written once"])
    loaded, problems = _load_fragments(fragments, hosts, users)
    groups, composite, group_problems = _group_fragments(loaded, sequence=False)
    problems += group_problems
    if composite is not None:
        problems.append("a run-gate-composite fragment belongs to assemble, not update")
    if not groups and not problems:
        problems.append("no fragments")
    for lane, g in sorted(groups.items()):
        if g.assay is not None and g.assay["r2"] is not None:
            problems.append(f"lane {lane}: R2 evidence enters through update --fragments, not as a qualification")
    if problems:
        raise Refusal(problems)
    commits = {g.primary["judged_commit"] for g in groups.values()}
    if len(commits) > 1:
        raise Refusal([f"qualification {name}: lanes were judged on different commits"])
    judged = commits.pop()
    for g in groups.values():
        problems += _r2_binding(g, record["project"], project_dir, git)
    if problems:
        raise Refusal(problems)
    updated = _bump(record)
    results = []
    for lane, g in sorted(groups.items()):
        toolset_id, updated["toolsets"] = _toolset_id(updated, _toolset_of(g))
        result, result_problems = _result(g, project_dir, judged, None, toolset_id, git)
        problems += [f"qualification {name}: {p}" for p in result_problems]
        for path, oid in sorted(result["targets"].items()):
            try:
                there = git.tree(record["commit"], path)
            except GitError:
                there = None
            if there != oid:
                problems.append(f"qualification {name}: lane {lane} judged {path} {oid}, the commit has {there}")
        results.append(result)
    if problems:
        raise Refusal(problems)
    updated["qualifications"][name] = {"judged_commit": judged, "results": results}
    classes, _perf, machine_problems = _merge_machines(list(groups.values()), updated["machine_classes"])
    if machine_problems:
        raise Refusal(machine_problems)
    updated["machine_classes"] = classes
    key = qualification_key(name, record["commit"], (d for _n, d, _doc in loaded))
    return _plan(record["commit"], {record["commit"]: _validated(updated, hosts, users)}, name, "pass", key)


# -- reconcile, withdraw ---------------------------------------------------------------------------------------
def plan_reconcile(
    *,
    ref: str,
    records: dict[str, dict],
    waivers: dict | None,
    git: GitFacts,
    expect_revision: int | None = None,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Plan | None:
    """Apply ``waivers.toml`` survivor dispositions to the open survivors of ``ref`` (memo 5.5). ``None`` = nothing to do."""
    record = record_of(records, ref, git)
    check_expected_revision(record, expect_revision)
    open_results = [r for r in record["results"] if r["kind"] != "sequence" and r["levels"].get("R2") == "survivors"]
    if not open_results:
        raise Refusal(["no lane has R2 survivors: only survivors can be reconciled"])
    if any(r["survivors"]["by_file"] for r in open_results):
        raise Refusal(["the survivors are collapsed to per-file counts (over 200): reconcile needs the full list"])
    rulings = {w["candidate_id"]: w for w in (waivers or {}).get("survivor", [])}
    updated = _bump(record)
    touched = False
    for result in [r for r in updated["results"] if r["kind"] != "sequence" and r["levels"].get("R2") == "survivors"]:
        u = result["survivors"]
        moved = False
        for s in u["listed"]:
            ruling = rulings.get(s["candidate_id"])
            if s["disposition"] == "open" and ruling is not None:
                s["disposition"], s["reason"] = ruling["disposition"], ruling["reason"]
                moved = True
        if moved:
            touched = True
            u.update(_counts(u["listed"]))
            if not u["open"]:
                check_transition("survivors", "pass-with-waivers")
                result["levels"]["R2"] = "pass-with-waivers"
    if not touched:
        return None
    key = sha12(json.dumps([record["commit"], sorted(rulings.items())], sort_keys=True))
    return _plan(record["commit"], {record["commit"]: _validated(updated, hosts, users)}, "R2", "reconciled", key)


def plan_withdraw(
    *,
    tag: str,
    records: dict[str, dict],
    reason: str,
    replacement: str,
    git: GitFacts,
    expect_revision: int | None = None,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Plan:
    """Mark the label ``tag`` withdrawn (memo 5.5). The replacement label must already exist, be newer and be current."""
    record = record_of(records, tag, git)
    check_expected_revision(record, expect_revision)
    label = next(x for x in record["labels"] if x["tag"] == tag)
    problems = []
    if label["state"] == "withdrawn":
        problems.append(f"{tag} is already withdrawn")
    target_commit = label_commits(records).get(replacement)
    if target_commit is None:
        problems.append(f"replacement {replacement} has no record: publish the fix release first")
    else:
        target = next(x for x in records[target_commit]["labels"] if x["tag"] == replacement)
        if target["state"] != "current":
            problems.append(f"replacement {replacement} is itself withdrawn")
        if _semver_key(target["version"]) <= _semver_key(label["version"]):
            problems.append(f"replacement {replacement} is not newer than {tag}")
    if problems:
        raise Refusal(problems)
    updated = _bump(record)
    mark = next(x for x in updated["labels"] if x["tag"] == tag)
    mark["state"] = "withdrawn"
    mark["withdrawn"] = {"reason": reason, "replacement": replacement}
    return _plan(record["commit"], {record["commit"]: _validated(updated, hosts, users)}, "release", "withdrawn",
                 withdraw_key(tag, reason, replacement))


__all__ = [
    "Plan", "TRANSITIONS", "check_expected_revision", "check_transition", "fragment_key", "label_commits", "name_of",
    "plan_qualification", "plan_r2", "plan_reconcile", "plan_withdraw", "qualification_key", "read_waivers", "record_of",
    "sha12", "withdraw_key",
]
