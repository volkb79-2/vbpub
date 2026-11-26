"""`agctl estate`: one row per package, every field naming its source (DESIGN section 4a).

The row is the join of four inputs, and the join keys differ because the inputs' keys differ:

* **runs** (`runs`): the legacy flat record sets, their outcome, their live context and their
  `<run>-y<N>` yield chain. A run record carries its worktree in `.wt` — a PATH, and therefore
  a fact — so a run joins the row of the branch that worktree holds. A run with no readable
  worktree path (an archived record whose worktree is gone) falls back to the name convention
  `runs.package_key`, which drops only the generated stamp and the `<run>-y<N>` suffix. Either
  way a run with no worktree at all still gets its own row: the README's promise that a run is
  reported whether or not its branch exists is what this key set implements.
* **git** (`gitstate`): the worktrees, their branches and heads, and whether each branch is
  merged into `origin/main`;
* **gates** (`sources`): the remote-gate results and the gate lock files, whose held/free
  state comes from `/proc/locks` and never from a probe `flock`. A result joins a row only
  when its commit id is that row's head, exactly or as a short form: `meta.json` names a
  project DIRECTORY, not a package, so a project-directory match would print another branch's
  verdict as this row's;
* **state** (`sources`): each peer's `STATE.md` lines that name a package id, marked
  `unverified` because a sentence is not a state, plus the handover files' ages.

**No source can take the view down.** A runs directory, a gate directory, a lock directory or
a peer state directory that exists but cannot be listed is reported as an unreadable source
with its reason; the other sources still render. A repository that cannot be resolved leaves
the git cells `unknown` and the run/gate/peer cells intact, because git is one of four inputs.

Contract:

* A field carries `value`, `source`, `state` and `mtime`. A value that could not be established
  is `unknown` (or `missing` when the source is absent), with the reason in `detail` — never
  `""`, `0` or `-`, which read as facts. A value read from prose is `unverified`.
* Nothing here writes, locks, or contacts a host or the network; git is the only subprocess.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_extended import CliFailure, CliRuntime

from . import bounded
from . import config as ag_config
from . import gitstate
from . import runs as ag_runs
from . import sources as ag_sources

PRESENT = "present"
UNKNOWN = "unknown"
MISSING = "missing"
UNVERIFIED = "unverified"

GIT_SOURCE = "git worktree list --porcelain"
MERGED_SOURCE = "git branch --merged origin/main"
RUNS_SOURCE = "legacy flat run records"
GATES_SOURCE = "remote-gate results (meta.json/lane.log/result.json)"
SLOTS_SOURCE = "/proc/locks matched by device and inode (never a probe flock)"
STATE_SOURCE = "STATE.md package-id line (prose)"
HANDOVER_SOURCE = "HANDOFF.md mtime"
MERGE_QUEUE_SOURCE = "the current handover's MERGE QUEUE line (prose)"
FIELDS = ("owner", "plan", "plan_id", "worktree", "branch", "head", "merged", "runs", "gates", "handover")

# A run whose lock file is free and which never wrote `.done` (DESIGN section 4).
DIED = "DIED"
ACTIVE = "active"
UNDECIDED = "undecided"

# Age buckets of the header: a bucket edge is a policy choice, and a fixed bucket means two
# calls a second apart print the same text. The edges are wide enough that a live estate moves
# between calls rarely, and narrow enough that a stale handover is visible as stale.
AGE_BUCKETS = ((3600, "<1h"), (6 * 3600, "1-6h"), (24 * 3600, "6-24h"))
AGE_OLD = ">24h"
AGE_UNKNOWN = "unknown"
# The live-context figure is BUCKETED, not the token count: `.live` is rewritten on every
# writer update, and a header that printed it verbatim would change on every call, defeating
# the on-change digest the hook relies on. The edges are wide enough that a run moves between
# them rarely and narrow enough that a nearly-full run is visible as one.
CTX_BUCKETS = ((50_000, "<50k"), (150_000, "50-150k"), (250_000, "150-250k"))
CTX_OLD = ">250k"
CTX_UNKNOWN = "ctx-unknown"
# Each header line is bounded WHERE IT IS COMPOSED (first `HEADER_ITEMS` items, each cut to
# `HEADER_ITEM_LIMIT` bytes, plus `+N more`), so the block always fits and `[truncated]` is a
# belt-and-braces that a real estate never reaches: a prefix truncation would otherwise drop
# the LAST sections, and the leases and live runs are exactly the facts a reader must not
# silently lose.
HEADER_ITEMS = 8
HEADER_ITEM_LIMIT = 96
# A merge candidate's head is printed SHORT (the 9-character form run-gate itself prints): the
# full 40-hex id is in the row's `head` cell, and a hook block does not need it twice.
SHORT_HEAD = 9
HEADER_NOTE_LIMIT = 120
HEADER_LIMIT = 4096
HEADER_MARKER = "[truncated]"
MERGE_QUEUE_PREFIX = "MERGE QUEUE"
MERGE_QUEUE_LIMIT = 240
# A gate result's commit id must be this long to name a head: a shorter prefix would match
# unrelated commits and turn "no result for this head" into somebody else's verdict.
SHORT_SHA = 7


@dataclass(frozen=True)
class Field:
    """One cell of a row: what it says, who said it, and how old that source is."""

    value: str
    source: str
    state: str = PRESENT
    mtime: float | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"value": self.value, "source": self.source, "state": self.state}
        if self.mtime is not None:
            payload["mtime"] = self.mtime
        if self.detail:
            payload["detail"] = self.detail
        return payload

    def text(self) -> str:
        parts = [f"{self.value:14s}", f"{self.state:10s}", self.source]
        if self.mtime is not None:
            parts.append(time.strftime("mtime %Y-%m-%dT%H:%M:%SZ", time.gmtime(self.mtime)))
        if self.detail:
            parts.append(f"({self.detail})")
        return " ".join(parts)


def _mtime_of(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _git_field(value: str, path: str, state: str = PRESENT, detail: str = "") -> Field:
    """A git-derived cell; its age is the worktree's own `.git` entry (or the path itself)."""

    return Field(
        value=value,
        source=GIT_SOURCE,
        state=state,
        mtime=_mtime_of(os.path.join(path, ".git")) or _mtime_of(path),
        detail=detail,
    )


def age_bucket(age_seconds: float | None) -> str:
    """A stable bucket name for an age in seconds; `unknown` when there is no age."""

    if age_seconds is None:
        return AGE_UNKNOWN
    for limit, name in AGE_BUCKETS:
        if age_seconds < limit:
            return name
    return AGE_OLD


def ctx_bucket(tokens: int | None) -> str:
    """A stable bucket name for a run's live context; `ctx-unknown` when there is no figure."""

    if tokens is None:
        return CTX_UNKNOWN
    for limit, name in CTX_BUCKETS:
        if tokens < limit:
            return name
    return CTX_OLD


def _branch_worktrees(view: gitstate.GitView) -> dict[str, list[gitstate.Worktree]]:
    """Every identified branch grouped by its package key (one key may hold several branches)."""

    groups: dict[str, list[gitstate.Worktree]] = {}
    for entry in view.worktrees:
        # A detached or bare entry carries no package identity; it is reported in the header
        # count, never folded into a row that would invent one. `main` is the integration
        # branch, not a package.
        if entry.bare or entry.detached or not entry.branch or entry.branch == "main":
            continue
        groups.setdefault(ag_runs.package_key(entry.branch), []).append(entry)
    return groups


@dataclass
class Row:
    """One package: the joined inputs, each cell keeping its own provenance."""

    package: str
    branches: tuple[str, ...]
    unmerged: tuple[str, ...]
    fields: dict[str, Field] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "package": self.package,
            "branches": list(self.branches),
            "unmerged": list(self.unmerged),
        }
        payload.update({name: self.fields[name].as_dict() for name in FIELDS})
        return payload


@dataclass(frozen=True)
class View:
    """Every input of one estate run, so rendering never reads a file again."""

    git: gitstate.GitView
    run_set: ag_runs.RunSet
    slots: tuple[ag_sources.Slot, ...]
    results: tuple[ag_sources.GateResult, ...]
    peers: tuple[ag_sources.PeerState, ...]
    handover_path: str
    handover_mtime: float | None
    handover_detail: str
    now: float
    # The reasons a source could not be listed, kept so a render can state them: one
    # unreadable directory costs that source's cells, never the other sources.
    locks: ag_sources.LockTable = field(default_factory=lambda: ag_sources.LockTable((), True, ""))
    slots_detail: str = ""
    results_detail: str = ""
    peers_detail: tuple[str, ...] = ()
    merge_queue: str = ""
    merge_queue_detail: str = ""


def _run_status(record: ag_runs.RunRecord, locks: ag_sources.LockTable) -> str:
    """`ended`/`active`/`DIED`/`undecided` for one run (DESIGN 4a: a free lock means DIED).

    The lock is decided from `/proc/locks` by device and inode, never by a probe flock. A run
    with no lock file at all is `undecided` rather than `active`: a launcher that never wrote
    one tells us nothing about whether its wrapper is alive.
    """

    if record.state() == ag_runs.ENDED:
        return ag_runs.ENDED
    lock = record.path("lock")
    if lock is None:
        return UNDECIDED
    state, _pid, _detail = locks.state_of(lock)
    if state == ag_sources.FREE:
        return DIED
    if state == ag_sources.HELD:
        return ACTIVE
    return UNDECIDED


def _runs_field(
    records: tuple[ag_runs.RunRecord, ...],
    set_: ag_runs.RunSet,
    locks: ag_sources.LockTable,
    chains: dict[str, tuple[str, ...]],
) -> Field:
    if not set_.readable:
        detail = "no runs directory is readable: " + ", ".join(set_.dirs)
        if set_.unreadable:
            detail = "; ".join(f"unreadable run directory: {reason}" for _dir, reason in set_.unreadable)
        return Field(value=UNKNOWN, source=RUNS_SOURCE, state=MISSING, detail=detail)
    if not records:
        return Field(
            value="0 runs",
            source=RUNS_SOURCE,
            state=PRESENT,
            detail="no record set carries this package key (derived via package_key)",
        )
    parts: list[str] = []
    newest = max((record.live_mtime or _mtime_of(str(record.path("done") or "")) or 0.0) for record in records)
    counts = {ag_runs.ENDED: 0, ACTIVE: 0, DIED: 0, UNDECIDED: 0}
    for record in records:
        status = _run_status(record, locks)
        counts[status] += 1
        if status == ag_runs.ENDED:
            shown = f"{record.outcome or 'outcome-unknown'} exit {record.exit_code}"
        elif status == ACTIVE:
            shown = "active"
        elif status == DIED:
            shown = "DIED (lock free, never ended)"
        else:
            shown = "undecided (no readable lock file)"
        if status != ag_runs.ENDED and record.tokens is not None:
            # The live context is a fact about the run whatever the lock says; it is reported
            # as the raw figure here (the HEADER buckets it, because the header must not move).
            shown += f" {record.tokens // 1000}k"
        chain = next((members for members in chains.values() if record.id in members and len(members) > 1), ())
        suffix = f" chain {'>'.join(chain)}" if chain else ""
        parts.append(f"{record.id} {shown}{suffix}")
    value = (
        f"{len(records)} run(s): {counts[ag_runs.ENDED]} ended, {counts[ACTIVE]} active, "
        f"{counts[DIED]} died, {counts[UNDECIDED]} undecided"
    )
    detail = "; ".join(parts)
    if set_.unreadable:
        detail += "; unreadable run directory: " + "; ".join(reason for _dir, reason in set_.unreadable)
    return Field(value=value, source=RUNS_SOURCE, state=PRESENT, mtime=newest or None, detail=detail)


def _sha_matches(result_sha: str, head: str) -> bool:
    """A gate result's commit id names this head, exactly or as a short form (at least 7 chars).

    `head` is never `unknown` here: the caller collects only known heads, because a result
    cannot be attributed to a head nobody read.
    """

    return len(result_sha) >= SHORT_SHA and (result_sha == head or head.startswith(result_sha))


def _held_detail(slots: tuple[ag_sources.Slot, ...]) -> str:
    held = tuple(slot for slot in slots if slot.state == ag_sources.HELD)
    if held:
        return f"; {len(held)} slot(s) held estate-wide ({', '.join(slot.name for slot in held)})"
    return "; no gate slot held"


def _gates_field(
    heads: tuple[str, ...],
    topic: str,
    view: View,
) -> Field:
    """The row's gate cell: a result joins ONLY on this row's head, never on a project name."""

    if view.results_detail:
        return Field(
            value=UNKNOWN,
            source=GATES_SOURCE,
            state=MISSING,
            detail=f"gate result directory unreadable: {view.results_detail}",
        )
    known = tuple(head for head in heads if head != UNKNOWN)
    if not known:
        return Field(
            value=UNKNOWN,
            source=GATES_SOURCE,
            state=UNKNOWN,
            detail="no head is known for this row, so no recorded result can be attributed to it"
            + _held_detail(view.slots),
        )
    mine = tuple(result for result in view.results if any(_sha_matches(result.sha, head) for head in known))
    if not mine:
        detail = f"no recorded gate result names this row's head(s) ({', '.join(head[:9] for head in known)})"
        named = next((result for result in view.results if result.project == topic), None)
        if named is not None:
            detail += (
                f"; the newest result for the project directory '{named.project}' is "
                f"{named.lane} {named.verdict} {named.host} at {named.sha[:9] or 'sha-unknown'}, "
                "which is NOT this row's head"
            )
        else:
            detail += ": no recorded result shares even its project directory"
        return Field(
            value=UNKNOWN, source=GATES_SOURCE, state=UNKNOWN, detail=detail + _held_detail(view.slots)
        )
    newest = mine[0]
    detail = f"{len(mine)} result(s); newest {newest.name}" + _held_detail(view.slots)
    return Field(value=newest.text(), source=GATES_SOURCE, state=PRESENT, mtime=newest.mtime, detail=detail)


def _naming_lines(
    package: str, topic: str, peers: tuple[ag_sources.PeerState, ...]
) -> list[tuple[ag_sources.PeerState, ag_sources.StateLine]]:
    """Peer prose lines that name this package: designator matches first, then topic matches.

    The real estate separates the two stages of the key deliberately: branches carry a topic
    and a slug (`core-ciu-r2fix`), while `STATE.md` names a designator (`P-core-08`). Nothing
    derives one from the other, so an exact `P-<topic>-<designator>` match is preferred when
    it exists (it does for the `plat-NNx` package ids) and a topic match is used otherwise —
    reported as `unverified`, with the designator in its own cell so no reader mistakes a
    topic's prose for this particular package's state.
    """

    exact = [
        (peer, line)
        for peer in peers
        for line in peer.lines
        if line.package == package or package.startswith(line.package + "-")
    ]
    if exact:
        return exact
    return [(peer, line) for peer in peers for line in peer.lines if line.topic and line.topic == topic]


def _state_fields(
    package: str,
    view: View,
) -> tuple[Field, Field, Field, Field]:
    """`(owner, plan, plan_id, handover)` from the peer prose that names this package."""

    naming = _naming_lines(package, ag_runs.topic_of(package), view.peers)
    if not naming:
        absent = [peer.peer for peer in view.peers if peer.state_state != ag_sources.PRESENT]
        detail = "no STATE.md line names this package id or its topic"
        if absent:
            detail += f"; unreadable peer state: {', '.join(absent)}"
        if view.peers_detail:
            detail += f"; unreadable state directory: {', '.join(view.peers_detail)}"
        unknown = Field(value=UNKNOWN, source=STATE_SOURCE, state=UNKNOWN, detail=detail)
        return (
            unknown,
            Field(value=UNKNOWN, source=STATE_SOURCE, state=UNKNOWN, detail=detail),
            Field(value=UNKNOWN, source=STATE_SOURCE, state=UNKNOWN, detail=detail),
            _own_handover_field(view),
        )
    peer, line = naming[0]
    quote = line.text[:160]
    lines = naming
    extra = f"; {len(lines) - 1} more line(s) name the topic" if len(lines) > 1 else ""
    owner = Field(
        value=f"{peer.peer}",
        source=f"{STATE_SOURCE} {line.path}:{line.line}",
        state=UNVERIFIED,
        mtime=peer.state_mtime,
        detail=f"prose line names {line.token}: {quote}",
    )
    plan = Field(
        value="prose",
        source=f"{STATE_SOURCE} {line.path}:{line.line}",
        state=UNVERIFIED,
        mtime=peer.state_mtime,
        detail=f"no machine plan state yet (DESIGN 4a: STATE.md until plan.yaml lands): {quote}{extra}",
    )
    plan_id = Field(
        value=line.designator or "none",
        source=f"{STATE_SOURCE} {line.path}:{line.line}",
        state=UNVERIFIED if line.designator else UNKNOWN,
        mtime=peer.state_mtime,
        detail=f"the designator named by {line.token}; the join itself is on the topic or an exact id{extra}",
    )
    return owner, plan, plan_id, _peer_handover_field(peer, view.now)


def _own_handover_field(view: View) -> Field:
    path, mtime, detail = view.handover_path, view.handover_mtime, view.handover_detail
    if mtime is None:
        source = f"{HANDOVER_SOURCE} {path}" if path else HANDOVER_SOURCE
        return Field(value=UNKNOWN, source=source, state=MISSING, detail=detail)
    return Field(
        value=age_bucket(view.now - mtime),
        source=f"{HANDOVER_SOURCE} {path}",
        state=PRESENT,
        mtime=mtime,
        detail="own handover (pre-migration input: the repository's .controller-state)",
    )


def _peer_handover_field(peer: ag_sources.PeerState, now: float) -> Field:
    if peer.handover_mtime is None:
        return Field(
            value=UNKNOWN,
            source=f"{HANDOVER_SOURCE} {peer.handover_path}",
            state=MISSING,
            detail=f"peer {peer.peer} has no HANDOFF.md",
        )
    return Field(
        value=age_bucket(now - peer.handover_mtime),
        source=f"{HANDOVER_SOURCE} {peer.handover_path}",
        state=PRESENT,
        mtime=peer.handover_mtime,
        detail=f"peer {peer.peer} handover",
    )


def _worktree_index(groups: dict[str, list[gitstate.Worktree]]) -> dict[str, str]:
    """Worktree path -> package key: a run's `.wt` is a path, so this join is a fact."""

    index: dict[str, str] = {}
    for key, entries in groups.items():
        for entry in entries:
            index[os.path.realpath(entry.path)] = key
            index.setdefault(entry.path, key)
    return index


def _runs_by_key(view: View, index: dict[str, str]) -> dict[str, list[ag_runs.RunRecord]]:
    """Every record under the row it belongs to: by its worktree path, else by its name."""

    grouped: dict[str, list[ag_runs.RunRecord]] = {}
    for record in view.run_set.records:
        key = ""
        worktree = record.worktree.strip()
        if worktree:
            key = index.get(os.path.realpath(worktree)) or index.get(worktree) or ""
        grouped.setdefault(key or ag_runs.package_key(record.id), []).append(record)
    return grouped


def rows_from_inputs(view: View) -> list[Row]:
    """One row per package: git identity, runs, gates and peer state joined per its own key."""

    groups = _branch_worktrees(view.git)
    runs = _runs_by_key(view, _worktree_index(groups))
    chains = ag_runs.yield_chains(view.run_set.records)
    rows: list[Row] = []
    for package in sorted(set(groups) | set(runs)):
        entries = groups.get(package, [])
        records = tuple(runs.get(package, ()))
        owner, plan, plan_id, handover = _state_fields(package, view)
        if entries:
            branches = tuple(entry.branch for entry in entries if entry.branch)
            heads = tuple((entry.head or UNKNOWN) for entry in entries)
            merged_states = {view.git.merged_here(entry.branch) for entry in entries}
            unmerged = tuple(
                entry.branch for entry in entries if entry.branch and view.git.merged_here(entry.branch) == "no"
            )
            if "no" in merged_states:
                merged_value = "no: " + ", ".join(unmerged)
                merged_state, merged_detail = PRESENT, "not in origin/main (merge candidate)"
            elif merged_states == {"yes"}:
                merged_value, merged_state, merged_detail = "yes", PRESENT, ""
            else:
                merged_value, merged_state = UNKNOWN, UNKNOWN
                merged_detail = view.git.detail or "the merge list does not decide this branch"
            worktree = _git_field("; ".join(entry.path for entry in entries), entries[0].path)
            branch = _git_field(
                "; ".join(branches), entries[0].path, detail="package key derived from the branch name"
            )
            head = _git_field(
                "; ".join(heads),
                entries[0].path,
                state=PRESENT if all(head != UNKNOWN for head in heads) else UNKNOWN,
                detail="" if all(head != UNKNOWN for head in heads) else "the porcelain block carries no HEAD line",
            )
        else:
            branches, heads, unmerged = (), (), ()
            reason = (
                "no worktree holds this package key: the branch may be gone; its run records keep the row"
                if view.git.root
                else f"git is unknown, so this row has no worktree identity ({view.git.detail})"
            )
            worktree = Field(value=UNKNOWN, source=GIT_SOURCE, state=MISSING, detail=reason)
            branch = Field(value=UNKNOWN, source=GIT_SOURCE, state=MISSING, detail=reason)
            head = Field(value=UNKNOWN, source=GIT_SOURCE, state=MISSING, detail=reason)
            merged_value, merged_state, merged_detail = UNKNOWN, UNKNOWN, reason
        fields = {
            "owner": owner,
            "plan": plan,
            "plan_id": plan_id,
            "worktree": worktree,
            "branch": branch,
            "head": head,
            "merged": Field(value=merged_value, source=MERGED_SOURCE, state=merged_state, detail=merged_detail),
            "runs": _runs_field(records, view.run_set, view.locks, chains),
            "gates": _gates_field(heads, ag_runs.topic_of(package), view),
            "handover": handover,
        }
        rows.append(Row(package=package, branches=branches, unmerged=unmerged, fields=fields))
    return rows


def render_text(rows: list[Row], view: View) -> str:
    git = view.git
    lines = [f"estate: {len(rows)} package row(s) of {len(git.worktrees)} worktree entries (source: {GIT_SOURCE})"]
    if not git.root:
        lines.append(f"estate: git UNKNOWN ({git.detail}); the run, gate and state rows below still stand")
    elif git.merged_state != PRESENT:
        lines.append(f"estate: merge state UNKNOWN ({git.detail})")
    if not view.run_set.readable:
        lines.append(f"estate: run records UNKNOWN (no readable runs directory: {', '.join(view.run_set.dirs)})")
    for _dir, reason in view.run_set.unreadable:
        lines.append(f"estate: run records PARTIAL ({reason})")
    if view.slots_detail:
        lines.append(f"estate: gate slots UNKNOWN ({view.slots_detail})")
    else:
        held = [slot for slot in view.slots if slot.state == ag_sources.HELD]
        unknown_slots = [slot for slot in view.slots if slot.state == ag_sources.UNKNOWN]
        lines.append(
            f"estate: gate slots {len(view.slots)} ({len(held)} held, {len(unknown_slots)} unknown; source: {SLOTS_SOURCE})"
        )
    if view.results_detail:
        lines.append(f"estate: gate results UNKNOWN ({view.results_detail})")
    else:
        lines.append(f"estate: gate results {len(view.results)} (source: {GATES_SOURCE})")
    for reason in view.peers_detail:
        lines.append(f"estate: peer state PARTIAL ({reason})")
    for row in rows:
        lines.extend(("", f"PACKAGE {row.package}"))
        lines.extend(f"  {name:9s} {row.fields[name].text()}" for name in FIELDS)
    unverified = sum(1 for row in rows for name in FIELDS if row.fields[name].state == UNVERIFIED)
    unknown = sum(1 for row in rows for name in FIELDS if row.fields[name].state in (UNKNOWN, MISSING))
    lines.extend(
        (
            "",
            f"unverified: {unverified} cell(s) read from prose (STATE.md); unknown/missing: {unknown} cell(s); "
            "each one names its reason above",
        )
    )
    return "\n".join(lines) + "\n"


def _cut(text: str, limit: int) -> str:
    """`text` cut to `limit` UTF-8 bytes, on a character boundary (a ``~`` marks the cut)."""

    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[: limit - 1]
    return encoded.decode("utf-8", "ignore") + "~"


def _bounded_line(label: str, items: list[str], count: int, *, note: str = "") -> str:
    """One header line, bounded where it is composed: first `HEADER_ITEMS` items, `+N more`."""

    kept = [_cut(item, HEADER_ITEM_LIMIT) for item in items[:HEADER_ITEMS]]
    body = "; ".join(kept) if kept else "none"
    extra = f" +{count - len(kept)} more" if count > len(kept) else ""
    suffix = f" [{_cut(note, HEADER_NOTE_LIMIT)}]" if note else ""
    return f"{label} {count}: {body}{extra}{suffix}"


def _merge_line(rows: list[Row], view: View) -> str:
    """The handover's MERGE QUEUE line when it exists; unmerged worktree branches otherwise."""

    if view.merge_queue:
        return _bounded_line(
            "MERGE QUEUE", [view.merge_queue], 1, note=f"{MERGE_QUEUE_SOURCE}, unverified"
        )
    candidates = [
        f"{branch} {row.fields['head'].value[:SHORT_HEAD]}" for row in rows for branch in row.unmerged
    ]
    # No note on the fallback: DESIGN 8 item 1d defines it ("absent: unmerged worktree branches
    # and heads"), and the label itself says which of the two sources this line is. The reason
    # a queue line was absent travels in the JSON (`sources.merge_queue.detail`).
    return _bounded_line("MERGE", candidates, len(candidates))


def _live_line(view: View) -> str:
    """The runs believed alive, and how many were excluded as DIED or undecided."""

    if not view.run_set.readable:
        return "LIVE: unknown (no readable runs directory)"
    live: list[str] = []
    died = undecided = 0
    for record in view.run_set.records:
        status = _run_status(record, view.locks)
        if status == ACTIVE:
            live.append(f"{record.id} {record.route or 'route-unknown'} {ctx_bucket(record.tokens)}")
        elif status == DIED:
            died += 1
        elif status == UNDECIDED:
            undecided += 1
    line = _bounded_line("LIVE", live, len(live))
    excluded = []
    if died:
        excluded.append(f"{died} died")
    if undecided:
        excluded.append(f"{undecided} undecided")
    if excluded:
        line += " (excluded: " + ", ".join(excluded) + ")"
    return line


def _peers_line(view: View) -> str:
    items = [
        f"{peer.peer} {age_bucket(view.now - peer.handover_mtime) if peer.handover_mtime is not None else AGE_UNKNOWN}"
        for peer in view.peers
    ]
    note = f"unreadable: {', '.join(view.peers_detail)}" if view.peers_detail else ""
    return _bounded_line("PEERS", items, len(items), note=note)


def render_header(rows: list[Row], view: View) -> str:
    """The compact, hook-injectable block of DESIGN section 8 item 1d.

    Deterministic by construction: fixed order, fixed sections, ages and context as buckets,
    and no timestamp that moves between two calls. An unchanged estate therefore prints
    byte-identical text, which is what lets the hook emit it only when the digest changes.

    Every line is bounded where it is composed (`_bounded_line`), so the block fits and a
    prefix truncation cannot drop the last sections; `_truncate` stays as a backstop.
    """

    lines = [
        _merge_line(rows, view),
        _bounded_line(
            "LEASES",
            [slot.text() for slot in view.slots if slot.state == ag_sources.HELD],
            sum(1 for slot in view.slots if slot.state == ag_sources.HELD),
            note=f"unreadable lock directory: {_cut(view.slots_detail, HEADER_NOTE_LIMIT)}" if view.slots_detail else "",
        ),
        _live_line(view),
        _peers_line(view),
        f"HANDOVER own {age_bucket(view.now - view.handover_mtime) if view.handover_mtime is not None else AGE_UNKNOWN}",
    ]
    # `_truncate` is total: it returns the block unchanged when it already fits, so the cap is
    # guaranteed in one place instead of an `if` here and another cut inside.
    return _truncate(lines) + "\n"


def _truncate(lines: list[str]) -> str:
    """The longest run of whole lines that fits the cap IN BYTES, marker included.

    Whole lines only, because half a line can be read as a different fact. A block that already
    fits is returned unchanged (no marker), so the function is total and the cap has exactly
    one owner. If not even the first line fits, that first line is cut to the cap and marked.

    The lengths are accumulated over the WHOLE list before anything is chosen: a loop that
    breaks would leave an unreachable "ran to the end" branch.
    """

    room = HEADER_LIMIT - len(HEADER_MARKER) - 2
    cumulative = 0
    fit = 0
    for index, line in enumerate(lines):
        cumulative += len(line.encode("utf-8")) + (1 if index else 0)
        if cumulative <= room:
            fit = index + 1
    if fit == len(lines):
        return "\n".join(lines)
    if fit == 0:
        return _cut(lines[0], room) + "\n" + HEADER_MARKER
    return "\n".join([*lines[:fit], HEADER_MARKER])


def _merge_queue(path: str) -> tuple[str, str]:
    """`(value, detail)` of the current handover's last MERGE QUEUE line."""

    if not path:
        return "", "no handover path is configured, so no MERGE QUEUE line can be read"
    line = bounded.last_line_with_prefix(Path(path), MERGE_QUEUE_PREFIX)
    if line is None:
        return "", f"no MERGE QUEUE line in {path}; the unmerged worktree branches stand in"
    return _cut(line.strip(), MERGE_QUEUE_LIMIT), ""


def read_view(config: ag_config.Config, *, now: float | None = None, cwd: Path | None = None) -> View:
    """Read every input once. A repository that cannot be resolved costs the git cells only."""

    root, detail = gitstate.discover_root(config.repo, Path.cwd() if cwd is None else Path(cwd))
    git = (
        gitstate.read_view(root)
        if root is not None
        else gitstate.GitView("", (), frozenset(), "unknown", detail)
    )
    locks = ag_sources.read_proc_locks()
    slot_scan = ag_sources.scan_gate_slots(config, locks)
    gate_scan = ag_sources.scan_results(config)
    peer_scan = ag_sources.scan_peers(config)
    handover_path, handover_mtime, handover_detail = ag_sources.read_own_handover(config)
    merge_queue, merge_queue_detail = _merge_queue(handover_path if handover_mtime is not None else "")
    return View(
        git=git,
        run_set=ag_runs.read(config),
        slots=slot_scan.slots,
        results=gate_scan.results,
        peers=peer_scan.peers,
        handover_path=handover_path,
        handover_mtime=handover_mtime,
        handover_detail=handover_detail,
        now=time.time() if now is None else now,
        locks=locks,
        slots_detail=slot_scan.detail,
        results_detail=gate_scan.detail,
        peers_detail=peer_scan.unreadable,
        merge_queue=merge_queue,
        merge_queue_detail=merge_queue_detail,
    )


def run(args: argparse.Namespace, runtime: CliRuntime, *, config: ag_config.Config | None = None) -> int:
    """The ``estate`` handler: read every input, print rows (or the header). Read-only."""

    header = bool(getattr(args, "header", False))
    if header and runtime.json_mode:
        # The header is text by contract (a hook injects it); `--json --header` would be two
        # answers to one question. It is a usage error (exit 2), not a runtime failure (1),
        # and it is refused BEFORE any source is read.
        raise CliFailure(
            "--header prints the injected text block; it cannot be combined with --json",
            exit_code=2,
            hint="drop one of --header / --json",
        )
    resolved = config if config is not None else ag_config.load()
    view = read_view(resolved)
    rows = rows_from_inputs(view)
    if header:
        runtime.output.primary(render_header(rows, view))
        return 0
    if runtime.json_mode:
        payload: dict[str, Any] = {
            "schema": "agctl-estate/1",
            "root": view.git.root or None,
            "git": {
                "worktrees": len(view.git.worktrees),
                "merged": view.git.merged_state,
                "detail": view.git.detail,
            },
            "sources": {
                "runs": {
                    "dirs": list(view.run_set.dirs),
                    "readable": list(view.run_set.readable),
                    "unreadable": [list(entry) for entry in view.run_set.unreadable],
                    "records": len(view.run_set.records),
                },
                "gate_slots": [slot.as_dict() for slot in view.slots],
                "gate_slots_detail": view.slots_detail,
                "gate_results": len(view.results),
                "gate_results_detail": view.results_detail,
                "peers": [
                    {
                        "peer": peer.peer,
                        "state": peer.state_state,
                        "lines": len(peer.lines),
                        "handover_mtime": peer.handover_mtime,
                    }
                    for peer in view.peers
                ],
                "peers_detail": list(view.peers_detail),
                "handover": {"path": view.handover_path, "mtime": view.handover_mtime},
                "merge_queue": {"value": view.merge_queue, "detail": view.merge_queue_detail},
            },
            "rows": [row.as_dict() for row in rows],
        }
        if not view.git.root:
            payload["unknown"] = {
                "root": {"source": "git rev-parse --show-toplevel", "detail": view.git.detail}
            }
        runtime.output.primary(payload)
        return 0
    runtime.output.primary(render_text(rows, view))
    return 0
