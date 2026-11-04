"""``sync``: bring a published release's immutable record asset onto ``main`` (memo 5.2, 5.6).

The release-time record is the asset ``<tag>-rigor.json`` plus ``<tag>-rigor.json.sha256`` of the GitHub Release. The
download sits behind :class:`ReleaseSource` so tests use a fake; :class:`GhReleaseSource` is the real ``gh`` call. The
digest file is checked against the asset bytes and the record against the repository (the tag still resolves to its
commit, the project tree matches) before anything is committed. Re-running ``sync`` for a tag that already has a record
does nothing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Protocol

from .assemble import Refusal, _free_class_id
from .gitfacts import GitError, GitFacts
from .update import Plan, _plan, _put_result, _toolset_id, sha12
from .validate import check_size, validate_record

_DIGEST = re.compile(r"[0-9a-f]{64}")


class SyncError(Exception):
    """The release asset could not be downloaded."""


class ReleaseSource(Protocol):
    def fetch(self, tag: str, name: str) -> bytes:
        """The bytes of release asset ``name`` of ``tag``; raises :class:`SyncError` when it cannot be had."""


class GhReleaseSource:
    """:class:`ReleaseSource` backed by ``gh release download`` (the cockpit's own ``gh`` identity)."""

    def __init__(self, repo: str | None = None, gh: str = "gh") -> None:
        self.repo, self.gh = repo, gh

    def fetch(self, tag: str, name: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="rigor-sync-") as scratch:
            argv = [self.gh, "release", "download", tag, "--pattern", name, "--dir", scratch, "--clobber"]
            if self.repo is not None:
                argv += ["--repo", self.repo]
            done = subprocess.run(argv, capture_output=True, text=True, check=False)
            if done.returncode != 0:
                first = (done.stderr.strip().splitlines() or ["no message"])[0][:300]
                raise SyncError(f"gh release download {tag} {name} failed: {first}")
            path = Path(scratch) / name
            if not path.is_file():
                raise SyncError(f"release {tag} has no asset {name}")
            return path.read_bytes()


def asset_names(tag: str) -> tuple[str, str]:
    return f"{tag}-rigor.json", f"{tag}-rigor.json.sha256"


def fetch_record(
    *,
    tag: str,
    project: str,
    project_dir: str,
    source: ReleaseSource,
    git: GitFacts,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> tuple[dict, str]:
    """Download, digest-check and validate the release-time record of ``tag``. Returns ``(record, asset sha256)``."""
    record_name, digest_name = asset_names(tag)
    data = source.fetch(tag, record_name)
    tokens = source.fetch(tag, digest_name).decode("utf-8", errors="replace").split()
    actual = hashlib.sha256(data).hexdigest()
    if not tokens or _DIGEST.fullmatch(tokens[0]) is None:
        raise Refusal([f"{digest_name} does not start with a sha256 digest"])
    if tokens[0] != actual:
        raise Refusal([f"{record_name} has sha256 {actual}, {digest_name} says {tokens[0]}"])
    oversize = check_size("record", data)
    if oversize:
        raise Refusal([f"{record_name}: {oversize[0].message}"])
    try:
        record = json.loads(data)
    except ValueError as exc:
        raise Refusal([f"{record_name} is not valid JSON"]) from exc
    violations = validate_record(record, hosts=hosts, users=users)
    if violations:
        raise Refusal([f"{record_name}: {v}" for v in violations])
    problems = []
    labels = [label["tag"] for label in record["labels"]]
    if labels != [tag]:
        problems.append(f"{record_name} is the record of {labels}, not exactly {tag}")
    if record["project"] != project:
        problems.append(f"{record_name} is the record of project {record['project']}, not {project}")
    if record["revision"] != 1:
        problems.append(f"{record_name} is revision {record['revision']}: the release asset is revision 1")
    if problems:
        raise Refusal(problems)
    try:
        resolved = git.tag_commit(tag)
        mismatched = [
            f"{record_name}: target {path} of lane {r['lane']} is {oid}, the commit has {git.tree(record['commit'], path)}"
            for r in record["results"] if r["kind"] != "sequence"
            for path, oid in sorted(r["targets"].items()) if git.tree(record["commit"], path) != oid
        ]
    except GitError as exc:
        raise Refusal([str(exc)]) from exc
    if resolved != record["commit"]:
        raise Refusal([f"tag {tag} now resolves to {resolved}, the record says {record['commit']}: a moved tag refuses"])
    if mismatched:
        raise Refusal(mismatched)
    return record, actual


def _fold(released: dict, existing: dict) -> dict:
    """The released record with the commit's earlier R2 evidence (results, qualifications, perf) folded in."""
    out = copy.deepcopy(released)
    out["revision"] = existing["revision"] + 1
    # Labels the commit already carries (another tag on the same commit) are kept, never dropped by the fold.
    out["labels"] += [label for label in copy.deepcopy(existing["labels"]) if label["tag"] not in [x["tag"] for x in out["labels"]]]
    toolsets = {}
    for old, toolset in existing["toolsets"].items():
        toolsets[old], out["toolsets"] = _toolset_id(out, toolset)
    classes = {}
    for mc in existing["machine_classes"]:
        key = (mc["cpu_model"], mc["cpus_available"], mc["hypervisor"])
        known = next((c for c in out["machine_classes"] if (c["cpu_model"], c["cpus_available"], c["hypervisor"]) == key), None)
        if known is None:
            known = {**mc, "id": _free_class_id(out["machine_classes"])}
            out["machine_classes"].append(known)
        classes[mc["id"]] = known["id"]
    for result in copy.deepcopy(existing["results"]):
        if result["kind"] == "sequence":
            continue
        if any(r["lane"] == result["lane"] for r in out["results"]):
            raise Refusal([f"lane {result['lane']} has a result in the release and in the commit's earlier record"])
        result["toolset"] = toolsets[result["toolset"]]
        for attempt in result.get("history", []):
            attempt["toolset"] = toolsets[attempt["toolset"]]
        _put_result(out, result)
    for name, qualification in copy.deepcopy(existing["qualifications"]).items():
        for result in qualification["results"]:
            result["toolset"] = toolsets[result["toolset"]]
        out["qualifications"][name] = qualification
    for entry in copy.deepcopy(existing["perf"]):
        entry["machine_classes"] = {classes[c]: n for c, n in entry["machine_classes"].items()}
        out["perf"].append(entry)
    out["inventory"]["r2_lanes"] = sorted(set(out["inventory"]["r2_lanes"]) | set(existing["inventory"]["r2_lanes"]))
    return out


def plan_sync(
    *, record: dict, asset_sha256: str, records: dict[str, dict], hosts: Iterable[str] = (), users: Iterable[str] = (),
) -> Plan | None:
    """Add the release record, or its label when the commit already has a record (``None``: nothing to do)."""
    commit = record["commit"]
    existing = records.get(commit)
    if existing is None:
        return _plan(commit, {commit: record}, "release", "synced", sha12(asset_sha256))
    if record["labels"][0]["tag"] in [label["tag"] for label in existing["labels"]]:
        return None
    folded = _fold(record, existing)
    violations = validate_record(folded, hosts=tuple(hosts), users=tuple(users))
    if violations:
        raise Refusal([f"folded record: {v}" for v in violations])
    return _plan(commit, {commit: folded}, "release", "synced", sha12(asset_sha256))
