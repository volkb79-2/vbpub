"""``assemble``: fragments + review + build inventory + waivers -> the release-time record of a commit (memo 5.1).

The record is keyed by the tagged commit. It holds one result per lane of the release sequence (R0, and R1/R3 where
assay verified the claim), the sequence result, the review and one ``release`` label. Everything that could make the
record untrue is a refusal that names its cause: a fragment judged on another project tree, a review that does not
bind the candidate commit, an expired waiver, a failed lane without a waiver, a member without a fragment or a
fragment that is no member, R2 evidence (it enters through ``update``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .gitfacts import GitError, GitFacts
from .render import RenderError, render_body
from .validate import (
    canonical_json,
    check_size,
    is_expired,
    validate_fragment,
    validate_record,
    validate_waivers,
)

_TAG = re.compile(r"(.+?)-v([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)")
_REVIEW_MARK = "rigor-review:"
_REVIEW_LINE = re.compile(
    r"<!-- rigor-review: verdict=(?P<verdict>[A-Z]+) from=(?P<from>[A-Za-z0-9.-]+) "
    r"to=(?P<to>[0-9a-f]{40}) reviewer=(?P<reviewer>[a-z][a-z0-9-]{0,63}) -->"
)
_PROJECT_DIR = re.compile(r"\.|[A-Za-z0-9_][A-Za-z0-9._+-]*(?:/[A-Za-z0-9_][A-Za-z0-9._+-]*)*")
_LEVEL_STATUS = {"PASS": "pass", "FAIL": "fail", "ERROR": "fail"}


class Refusal(Exception):
    """``assemble`` refused. ``problems`` names every cause found, one per line."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Assembled:
    record: dict
    record_text: str
    body_text: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- inputs ----------------------------------------------------------------------------------------------------
def parse_review(data: bytes, commit: str) -> tuple[dict | None, list[str]]:
    """The review document's machine line, bound to ``commit`` (memo 5.1). Prose is hashed, never read further."""
    text = data.decode("utf-8", errors="replace")
    marked = [line for line in text.splitlines() if _REVIEW_MARK in line]
    if len(marked) != 1:
        return None, [f"review: expected exactly one rigor-review line, found {len(marked)}"]
    match = _REVIEW_LINE.fullmatch(marked[0].strip())
    if match is None:
        return None, ["review: the rigor-review line is malformed"]
    problems = []
    if match["verdict"] != "ACCEPT":
        problems.append(f"review: verdict is {match['verdict']}, not ACCEPT")
    if match["to"] != commit:
        problems.append(f"review: unbound, it reviewed {match['to']} but the candidate commit is {commit}")
    if problems:
        return None, problems
    start = None if match["from"] == "none" else match["from"]
    return {
        "status": "accept",
        "range": {"from": start, "to": match["to"]},
        "reviewer_role": match["reviewer"],
        "document_sha256": _sha256(data),
    }, []


def _artifacts(manifest: Any, project: str, commit: str, tag: str) -> tuple[list[dict], list[str]]:
    """The release's artifact digests from a cmru inventory.

    Two producers, one consumer: a local ``build.json`` (``kind: cmru-local-build``, which must say
    ``publication: eligible``) and the inventory cmru's record step hands over as ``CMRU_BUILD_INVENTORY``
    (``kind: cmru-release-inventory``, which names the ``tag`` it is being released under and has no
    ``publication`` key: it exists only for a release in progress).
    """
    kind = manifest.get("kind") if isinstance(manifest, dict) else None
    if kind not in ("cmru-local-build", "cmru-release-inventory"):
        return [], ["artifacts: not a cmru build inventory"]
    problems = []
    if kind == "cmru-local-build" and manifest.get("publication") != "eligible":
        problems.append("artifacts: the build is not eligible for publication")
    if kind == "cmru-release-inventory" and manifest.get("tag") != tag:
        problems.append(f"artifacts: the inventory is for tag {manifest.get('tag')}, not {tag}")
    if manifest.get("source_commit") != commit:
        problems.append("artifacts: the build was made from a different commit")
    if manifest.get("project") != project:
        problems.append("artifacts: the build belongs to a different project")
    found: dict[str, str] = {}
    for directory in manifest.get("artifacts", []):
        for entry in directory.get("files", []):
            name = entry["path"].rsplit("/", 1)[-1]
            if name in found:
                problems.append(f"artifacts: duplicate artifact name {name}")
            found[name] = entry["sha256"]
    return [{"name": n, "sha256": found[n]} for n in sorted(found)], problems


def _release_version(tag: str) -> str | None:
    match = _TAG.fullmatch(tag)
    return None if match is None else match.group(2)


# -- fragments -------------------------------------------------------------------------------------------------
@dataclass
class _Group:
    """The facts of one lane: its assay fragment and/or its run-gate fragment."""

    lane: str
    assay: dict | None = None
    gate: dict | None = None

    @property
    def primary(self) -> dict:
        return self.assay if self.assay is not None else self.gate  # type: ignore[return-value]


def _load_fragments(
    named: Sequence[tuple[str, bytes]], hosts: Iterable[str], users: Iterable[str],
) -> tuple[list[tuple[str, str, dict]], list[str]]:
    loaded, problems = [], []
    for name, data in named:
        oversize = check_size("fragment", data)
        if oversize:
            problems.append(f"fragment {name}: {oversize[0].message}")
            continue
        try:
            document = json.loads(data)
        except ValueError:
            problems.append(f"fragment {name}: not valid JSON")
            continue
        violations = validate_fragment(document, hosts=hosts, users=users)
        problems += [f"fragment {name}: {v}" for v in violations]
        if not violations:
            loaded.append((name, _sha256(data), document))
    return loaded, problems


def _group_fragments(
    loaded: list[tuple[str, str, dict]], *, sequence: bool = True,
) -> tuple[dict[str, _Group], dict | None, list[str]]:
    """Group the fragments by lane. ``sequence`` demands exactly one composite (assemble); ``update`` passes False."""
    problems: list[str] = []
    assays = {digest: (name, doc) for name, digest, doc in loaded if doc["kind"] == "assay-lane"}
    claimed: dict[str, str] = {}
    groups: dict[str, _Group] = {}
    composites = [doc for _n, _d, doc in loaded if doc["kind"] == "run-gate-composite"]
    if sequence and len(composites) != 1:
        problems.append(f"sequence: expected exactly one run-gate-composite fragment, found {len(composites)}")

    def group(lane: str) -> _Group:
        return groups.setdefault(lane, _Group(lane))

    for name, _digest, doc in loaded:
        if doc["kind"] != "run-gate-lane":
            continue
        target = group(doc["lane"])
        if target.gate is not None:
            problems.append(f"lane {doc['lane']}: two run-gate fragments")
        target.gate = doc
        ref = doc["assay_fragment_sha256"]
        if ref is None:
            continue
        if ref not in assays:
            problems.append(f"fragment {name}: its assay fragment {ref[:12]} is not in the fragment set")
        else:
            claimed[ref] = doc["lane"]
    for _name, digest, doc in loaded:
        if doc["kind"] != "assay-lane":
            continue
        target = group(claimed.get(digest, doc["lane"]))
        if target.assay is not None:
            problems.append(f"lane {target.lane}: two assay fragments")
        target.assay = doc
    return groups, (composites[0] if len(composites) == 1 else None), problems


def _check_binding(
    groups: dict[str, _Group], project: str, tag: str, commit: str, project_dir: str, tree: str, git: GitFacts,
) -> list[str]:
    problems: list[str] = []
    for lane, g in sorted(groups.items()):
        docs = [d for d in (g.assay, g.gate) if d is not None]
        for doc in docs:
            where = f"lane {lane}"
            if doc["project"] != project:
                problems.append(f"{where}: fragment is for project {doc['project']}, not {project}")
            if doc["project_tree"] != tree:
                problems.append(
                    f"{where}: fragment was judged on project tree {doc['project_tree']}, the tag {tag} has {tree}"
                )
            try:
                actual = git.tree(doc["judged_commit"], project_dir)
            except GitError:
                problems.append(f"{where}: judged commit {doc['judged_commit']} is not in the repository")
                continue
            if actual != doc["project_tree"]:
                problems.append(f"{where}: fragment claims tree {doc['project_tree']} but its judged commit has {actual}")
        if len({d["judged_commit"] for d in docs}) > 1:
            problems.append(f"lane {lane}: its fragments name different judged commits")
    return problems


# -- results ---------------------------------------------------------------------------------------------------
def _lane_outcome(g: _Group) -> str:
    if g.assay is not None and g.assay["outcome"] != "PASS":
        return g.assay["outcome"]
    if g.gate is not None and g.gate["exit"] != 0:
        return "FAIL"
    return "PASS"


def _claims(g: _Group) -> dict[str, str]:
    """Verified claim levels above R0 with their outcome (a pre-A-1a fragment has none: nothing above R0 is judged)."""
    if g.assay is None:
        return {}
    return {c["rigor"]: c["outcome"] for c in g.assay.get("claims", []) if c["rigor"] != "R0"}


def _lane_waiver(lane: str, waivers: dict | None) -> dict | None:
    for entry in [] if waivers is None else waivers.get("lane", []):
        if entry["lane"] == lane:
            return {
                "reason": entry["reason"],
                "plan_item": entry.get("plan_item"),
                "expires_before": entry["expires_before"],
            }
    return None


def _targets(g: _Group, project_dir: str, commit: str, git: GitFacts) -> tuple[dict[str, str], list[str]]:
    """The project tree plus every declared input git resolves at ``commit``, and the declared inputs it cannot.

    An unresolvable input (untracked link path, misspelling) has no object id. It is never dropped: it is returned
    as unresolved so the result names it, and a result with unresolved inputs is valid only at its own commit.
    """
    targets = {project_dir: g.primary["project_tree"]}
    unresolved: list[str] = []
    for path in [] if g.assay is None else g.assay.get("inputs", []):
        try:
            targets[path] = git.tree(commit, path)
        except GitError:
            unresolved.append(path)
    return targets, sorted(set(unresolved))


def _toolset_of(g: _Group) -> dict:
    toolset: dict[str, Any] = {}
    if g.assay is not None:
        toolset["assay"] = g.assay["judge"]
    if g.gate is not None:
        toolset["run_gate"] = g.gate["run_gate"]
        if g.gate["image"] is not None:
            toolset["tester_image"] = g.gate["image"]
    return toolset


def _result(g: _Group, project_dir: str, commit: str, waivers: dict | None, toolset_id: str, git: GitFacts) -> tuple[dict, list[str]]:
    problems: list[str] = []
    outcome = _lane_outcome(g)
    claims = _claims(g)
    status = _LEVEL_STATUS[outcome]
    levels = {"R0": status}
    targets, unresolved = _targets(g, project_dir, commit, git)
    result: dict[str, Any] = {
        "lane": g.lane,
        "kind": "assay" if g.assay is not None else "command",
        "judged_by": "assay" if g.assay is not None else "run-gate",
        "levels": levels,
        "toolset": toolset_id,
        "targets": targets,
        "verdict_sha256": None if g.assay is None else g.assay["verdict_sha256"],
        "config_sha256": g.primary["config_sha256"],
    }
    if unresolved:
        result["unresolved_inputs"] = unresolved
    if g.assay is None:
        result["note"] = "declared, not judged"
    for level, key in (("R1", "coverage"), ("R3", "canary")):
        if level not in claims:
            continue
        section = g.assay[key]
        if section is None:
            if claims[level] == "PASS":
                problems.append(f"lane {g.lane}: assay claims {level} but the fragment carries no {key}")
            continue  # a failed or errored claim without its section: the level is not held, the lane failure is waived as R0
        levels[level] = _LEVEL_STATUS[claims[level]]
        result[key] = section
    failed = [name for name, value in levels.items() if value == "fail"]
    if failed:
        waiver = _lane_waiver(g.lane, waivers)
        if waiver is None:
            problems.append(f"lane {g.lane}: {outcome if outcome != 'PASS' else 'claim ' + failed[0] + ' failed'} and no waiver covers it")
        else:
            result["waiver"] = waiver
    return result, problems


# -- perf and machine classes ----------------------------------------------------------------------------------
def _free_class_id(classes: Iterable[dict]) -> str:
    used = {mc["id"] for mc in classes}
    number = 1
    while f"m{number}" in used:
        number += 1
    return f"m{number}"


def _merge_machines(
    groups: list[_Group], existing: Sequence[dict] = (),
) -> tuple[list[dict], list[dict], list[str]]:
    """Merge the groups' machine classes into ``existing`` (same cpu model, cpu count and hypervisor = same class)."""
    problems: list[str] = []
    classes: dict[tuple, dict] = {(mc["cpu_model"], mc["cpus_available"], mc["hypervisor"]): mc for mc in existing}
    perf: list[dict] = []
    for g in groups:
        if g.gate is None:
            continue
        remap: dict[str, str] = {}
        for mc in g.gate["machine_classes"]:
            key = (mc["cpu_model"], mc["cpus_available"], mc["hypervisor"])
            known = classes.get(key)
            if known is None:
                known = classes[key] = {**mc, "id": _free_class_id(classes.values())}
            elif known["speed"] != mc["speed"]:
                problems.append(f"lane {g.lane}: machine class {mc['id']} has a different speed than the same class elsewhere")
            remap[mc["id"]] = known["id"]
        for entry in g.gate["perf"]:
            counts: dict[str, int] = {}
            for old, count in entry["machine_classes"].items():
                counts[remap[old]] = counts.get(remap[old], 0) + count
            perf.append({**entry, "machine_classes": counts})
    return list(classes.values()), perf, problems


# -- the whole -------------------------------------------------------------------------------------------------
def _check_members(groups: dict[str, _Group], composite: dict, r2_lanes: Sequence[str]) -> list[str]:
    problems: list[str] = []
    members = set(composite["members"])
    problems += [f"sequence {composite['lane']}: member {lane} has no fragment" for lane in sorted(members - set(groups))]
    problems += [f"lane {lane}: a fragment exists but the sequence {composite['lane']} does not list the lane" for lane in sorted(set(groups) - members)]
    problems += [f"sequence {composite['lane']}: R2 lane {lane} is a member" for lane in sorted(members & set(r2_lanes))]
    for lane, g in sorted(groups.items()):
        if "R2" in _claims(g) or (g.assay is not None and g.assay["r2"] is not None):
            problems.append(f"lane {lane}: R2 evidence enters through update, not assemble")
    return problems


def assemble(
    *,
    project: str,
    tag: str,
    commit: str,
    project_dir: str,
    fragments: Sequence[tuple[str, bytes]],
    review: bytes,
    artifacts: Any,
    waivers: Any,
    published_at: str,
    git: GitFacts,
    r2_lanes: Sequence[str] = (),
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Assembled:
    """Build the release-time record and its body. Raises :class:`Refusal` naming every cause found."""
    hosts, users = tuple(hosts), tuple(users)
    problems: list[str] = []
    version = _release_version(tag)
    if version is None:
        raise Refusal([f"tag {tag} is not <prefix>-v<semver>"])
    if _PROJECT_DIR.fullmatch(project_dir) is None:
        raise Refusal([f"project directory {project_dir} is not a project-relative POSIX path"])
    if waivers is not None:
        problems += [f"waivers: {v}" for v in validate_waivers(waivers, hosts=hosts, users=users)]
    if problems:
        raise Refusal(problems)
    # The tag must resolve to the commit, and the record binds that commit's project tree.
    try:
        tagged = git.tag_commit(tag)
        tree = git.tree(commit, project_dir)
    except GitError as exc:
        raise Refusal([str(exc)]) from exc
    if tagged != commit:
        raise Refusal([f"tag {tag} resolves to {tagged}, not {commit}"])
    if waivers is not None:
        problems += [
            f"waiver for lane {w['lane']} ({w['level']}) expired: expires_before {w['expires_before']} is not after {version}"
            for w in waivers.get("lane", []) if is_expired(w["expires_before"], version)
        ]
    review_entry, review_problems = parse_review(review, commit)
    artifact_list, artifact_problems = _artifacts(artifacts, project, commit, tag)
    loaded, fragment_problems = _load_fragments(fragments, hosts, users)
    groups, composite, group_problems = _group_fragments(loaded)
    problems += review_problems + artifact_problems + fragment_problems + group_problems
    if composite is not None:
        problems += _check_members(groups, composite, r2_lanes)
        if composite["project"] != project or composite["judged_commit"] != commit:
            problems.append(f"sequence {composite['lane']}: judged another project or commit than {project} {commit}")
    if problems:
        raise Refusal(problems)
    assert composite is not None  # a missing composite was a problem above
    problems += _check_binding(groups, project, tag, commit, project_dir, tree, git)
    if problems:
        raise Refusal(problems)

    toolsets: dict[str, dict] = {}
    results: list[dict] = []
    for lane in composite["members"]:
        toolset = _toolset_of(groups[lane])
        known = next((tid for tid, t in toolsets.items() if t == toolset), None)
        if known is None:
            known = f"t{len(toolsets) + 1}"
            toolsets[known] = toolset
        result, result_problems = _result(groups[lane], project_dir, commit, waivers, known, git)
        problems += result_problems
        results.append(result)
    if composite["outcome"] != "PASS" and not any(r["levels"]["R0"] == "fail" for r in results):
        problems.append(f"sequence {composite['lane']}: {composite['outcome']} without a failed member lane")
    machine_classes, perf, machine_problems = _merge_machines([groups[lane] for lane in composite["members"]])
    problems += machine_problems
    if problems:
        raise Refusal(problems)
    sequence_tool = {"run_gate": composite["run_gate"]}
    sequence_toolset = next((tid for tid, t in toolsets.items() if t == sequence_tool), None)
    if sequence_toolset is None:
        sequence_toolset = f"t{len(toolsets) + 1}"
        toolsets[sequence_toolset] = sequence_tool
    results.append({
        "lane": composite["lane"], "kind": "sequence", "members": list(composite["members"]),
        "outcome": composite["outcome"], "toolset": sequence_toolset,
    })
    record = {
        "schema": "urn:vbpub:rigor-record:1",
        "project": project,
        "commit": commit,
        "revision": 1,
        "labels": [{
            "kind": "release", "tag": tag, "version": version, "published_at": published_at,
            "artifacts": artifact_list, "state": "current", "withdrawn": None,
        }],
        "inventory": {"sequence": composite["lane"], "members": list(composite["members"]), "r2_lanes": sorted(r2_lanes)},
        "toolsets": toolsets,
        "results": results,
        "review": review_entry,
        "qualifications": {},
        "perf": perf,
        "machine_classes": machine_classes,
    }
    violations = validate_record(record, hosts=hosts, users=users)
    if violations:
        raise Refusal([f"record: {v}" for v in violations])
    try:
        body = render_body(record, project_dir, hosts=hosts, users=users)
    except RenderError as exc:
        raise Refusal(exc.problems) from exc
    return Assembled(record, canonical_json(record), body)
