"""Derived views of stored results (memo 3.2, 3.4): validity at a commit and the per-commit level summary.

Pure functions of already-loaded records. Nothing here touches git, the clock or the network: the caller supplies
``targets_at``, a mapping function from a commit to the ``{path: git object id}`` of that commit (tests use a dict).
A stored result is never copied to another commit; whether it applies there is computed on read.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Sequence

from .gitfacts import GitError, GitFacts
from .validate import STATUS_RANK, _semver_key

TargetsAt = Callable[[str], Mapping[str, str | None]]
#: A notes provider (GN-1) answers whether a campaign for (commit, lane) is running; the default knows none.
NotesProvider = Callable[[str, str], bool]

NOT_JUDGED = "not-judged"
DECLARED = "declared, not judged"


def valid_at(result: Mapping, commit_targets: Mapping[str, str | None], *, own_commit: bool = False) -> bool:
    """A result is valid at a commit exactly when every one of its ``targets`` has the same object id there.

    A result that names ``unresolved_inputs`` (declared inputs git has no identity for) cannot be shown identical at
    any other commit: it is valid only at the commit it was judged on (``own_commit``).
    """
    if result.get("unresolved_inputs") and not own_commit:
        return False
    targets = result["targets"]
    return bool(targets) and all(commit_targets.get(path) == oid for path, oid in targets.items())


def targets_at_factory(records: Iterable[Mapping], git: "GitFacts") -> TargetsAt:
    """``targets_at`` backed by git: the object id of every path any stored result names, per commit (cached)."""
    paths = sorted({path for r in records for result in _lane_results(r) for path in result["targets"]})
    cache: dict[str, dict[str, str | None]] = {}

    def targets_at(commit: str) -> Mapping[str, str | None]:
        if commit not in cache:
            found: dict[str, str | None] = {}
            for path in paths:
                try:
                    found[path] = git.tree(commit, path)
                except GitError:
                    found[path] = None
            cache[commit] = found
        return cache[commit]

    return targets_at


def _lane_results(record: Mapping) -> list[Mapping]:
    return [r for r in record["results"] if r["kind"] != "sequence"]


def worst(statuses: Iterable[str]) -> str | None:
    """The worst stored status of ``statuses`` (``None`` when empty)."""
    return max(statuses, key=lambda s: STATUS_RANK[s], default=None)


def latest_label(records: Iterable[Mapping]) -> str | None:
    """The tag of the newest current release label of the project (by SemVer), or ``None``."""
    current = [(_semver_key(label["version"]), label["tag"]) for r in records for label in r["labels"] if label["state"] == "current"]
    return max(current)[1] if current else None


def valid_results(records: Sequence[Mapping], commit: str, targets_at: TargetsAt) -> list[tuple[Mapping, Mapping]]:
    """``(record, result)`` pairs valid at ``commit``, oldest record first (``records`` is in storage order)."""
    here = targets_at(commit)
    return [
        (record, result) for record in records for result in record["results"]
        if result["kind"] != "sequence" and valid_at(result, here, own_commit=record["commit"] == commit)
    ]


def _newest_per_lane_level(pairs: Sequence[tuple[Mapping, Mapping]], level: str) -> dict[str, Mapping]:
    found: dict[str, Mapping] = {}
    for _record, result in pairs:  # later records win
        if level in result["levels"] and (result["kind"] == "assay" or level == "R0"):
            found[result["lane"]] = result
    return found


def held_results(records: Sequence[Mapping], commit: str, targets_at: TargetsAt, level: str) -> dict[str, Mapping]:
    """The newest valid result per lane that holds ``level`` at ``commit`` (command lanes only ever hold R0)."""
    return _newest_per_lane_level(valid_results(records, commit, targets_at), level)


def level_summary(
    records: Sequence[Mapping], commit: str, targets_at: TargetsAt, notes: NotesProvider | None = None,
) -> dict[str, str]:
    """The level summary of ``commit``: ``R0``, ``R1``, ``R3``, ``review`` and ``R2`` as short strings.

    - R0 is the release sequence valid at the commit: every member lane has a passing valid R0 result.
    - R1 and R3 are the worst status of all valid assay results that hold the level; ``not-judged`` when none does.
    - R2 is ``k/n <worst>``: k of the n R2 lanes of the commit's inventory have a valid result. With k = 0 the
      summary is ``pending`` on the project's latest release label, ``superseded`` on an older label and
      ``not-judged`` elsewhere; ``running`` replaces ``pending`` when the notes provider reports a campaign.
    - command lanes hold R0 only (they are listed as declared, not judged elsewhere) and never feed R1/R3.
    """
    pairs = valid_results(records, commit, targets_at)
    own = next((r for r in records if r["commit"] == commit), None)
    out = {"review": "missing" if own is None else own["review"]["status"]}
    out["R0"] = _r0(records, commit, pairs)
    for level in ("R1", "R3"):
        statuses = [r["levels"][level] for r in _newest_per_lane_level(pairs, level).values()]
        out[level] = worst(statuses) or NOT_JUDGED
    out["R2"] = _r2(records, own, pairs, notes)
    return out


def _r0(records: Sequence[Mapping], commit: str, pairs: Sequence[tuple[Mapping, Mapping]]) -> str:
    # The commit's OWN release sequence (never another record's: a later release may add or drop member lanes).
    # A commit without a release record has no sequence of its own and is not judged at R0.
    sequences = [s for r in records if r["commit"] == commit for s in r["results"] if s["kind"] == "sequence"]
    if not sequences:
        return NOT_JUDGED
    sequence = sequences[-1]
    passed = _newest_per_lane_level(pairs, "R0")
    if not sequence["members"] or any(lane not in passed for lane in sequence["members"]):
        return NOT_JUDGED
    statuses = [passed[lane]["levels"]["R0"] for lane in sequence["members"]]
    if sequence["outcome"] != "PASS" and "fail" not in statuses:
        statuses.append("fail")
    return worst(statuses) or NOT_JUDGED


def _r2(
    records: Sequence[Mapping], own: Mapping | None, pairs: Sequence[tuple[Mapping, Mapping]], notes: NotesProvider | None,
) -> str:
    inventory = (own or (records[-1] if records else None))
    lanes = [] if inventory is None else inventory["inventory"]["r2_lanes"]
    if not lanes:
        return NOT_JUDGED
    held = _newest_per_lane_level(pairs, "R2")
    done = [held[lane]["levels"]["R2"] for lane in lanes if lane in held]
    if done:
        return f"{len(done)}/{len(lanes)} {worst(done)}"
    labels = [] if own is None else own["labels"]
    commit = "" if own is None else own["commit"]
    if not labels:
        return NOT_JUDGED
    if latest_label(records) in [label["tag"] for label in labels]:
        running = notes is not None and any(notes(commit, lane) for lane in lanes)
        return "running" if running else "pending"
    return "superseded"
