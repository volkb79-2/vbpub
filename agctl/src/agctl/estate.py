"""`agctl estate`: one row per package, every field naming its source (DESIGN section 4a).

The row is the join of four inputs on ONE key, the package id (DESIGN 4a: "the package id. Branch
names carry it already"):

* **git** (`gitstate`): the worktrees, their branches and heads, and whether each branch is
  merged into `origin/main`;
* **runs** (`runs`): the legacy flat record sets, their outcome, their live context and their
  `<run>-y<N>` yield chain;
* **gates** (`sources`): the remote-gate result directories and the gate lock files, whose
  held/free state comes from `/proc/locks` and never from a probe `flock`;
* **state** (`sources`): each peer's `STATE.md` lines that name a package id, marked
  `unverified` because a sentence is not a state, plus the handover files' ages.

The package id is DERIVED from the branch name (`runs.topic_key`): the stamp is dropped and the
numeric id token kept (`plat-22c-resumebusy-20261125_031456` -> `plat-22c`). That derivation is
a convention, not a fact in the input, so every cell built on it says `derived via topic_key`.

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
from typing import Any

from cli_extended import CliRuntime

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
FIELDS = ("owner", "plan", "worktree", "branch", "head", "merged", "runs", "gates", "handover")

# Age buckets of the header: a bucket edge is a policy choice, and a fixed bucket means two
# calls a second apart print the same text. The edges are wide enough that a live estate moves
# between calls rarely, and narrow enough that a stale handover is visible as stale.
AGE_BUCKETS = ((3600, "<1h"), (6 * 3600, "1-6h"), (24 * 3600, "6-24h"))
AGE_OLD = ">24h"
AGE_UNKNOWN = "unknown"
HEADER_LIMIT = 4096
HEADER_MARKER = "[truncated]"


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


def _branch_worktrees(view: gitstate.GitView) -> dict[str, list[gitstate.Worktree]]:
    """Every identified branch grouped by its package key (one key may hold several branches)."""

    groups: dict[str, list[gitstate.Worktree]] = {}
    for entry in view.worktrees:
        # A detached or bare entry carries no package identity; it is reported in the header
        # count, never folded into a row that would invent one. `main` is the integration
        # branch, not a package.
        if entry.bare or entry.detached or not entry.branch or entry.branch == "main":
            continue
        groups.setdefault(ag_runs.topic_key(entry.branch), []).append(entry)
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


def _runs_field(records: tuple[ag_runs.RunRecord, ...], set_: ag_runs.RunSet) -> Field:
    if not set_.readable:
        return Field(
            value=UNKNOWN,
            source=RUNS_SOURCE,
            state=MISSING,
            detail="no runs directory is readable: " + ", ".join(set_.dirs),
        )
    if not records:
        return Field(
            value="0 runs",
            source=RUNS_SOURCE,
            state=PRESENT,
            detail="no record set carries this package key (derived via topic_key)",
        )
    chains = ag_runs.yield_chains(set_.records)
    parts: list[str] = []
    newest = max((record.live_mtime or _mtime_of(str(record.path("done") or "")) or 0.0) for record in records)
    active = sum(1 for record in records if record.state() == ag_runs.ACTIVE)
    for record in records:
        state = record.state()
        if state == ag_runs.ENDED:
            status = f"{record.outcome or 'outcome-unknown'} exit {record.exit_code}"
        else:
            status = "active" + (f" {record.tokens // 1000}k" if record.tokens is not None else "")
        chain = next((members for members in chains.values() if record.id in members and len(members) > 1), ())
        suffix = f" chain {'>'.join(chain)}" if chain else ""
        parts.append(f"{record.id} {status}{suffix}")
    return Field(
        value=f"{len(records)} run(s), {active} active",
        source=f"{RUNS_SOURCE} (package key derived via topic_key)",
        state=PRESENT,
        mtime=newest or None,
        detail="; ".join(parts),
    )


def _gates_field(package: str, results: tuple[ag_sources.GateResult, ...], slots: tuple[ag_sources.Slot, ...]) -> Field:
    mine = tuple(result for result in results if result.project == package)
    held = tuple(slot for slot in slots if slot.state == ag_sources.HELD)
    if not mine:
        detail = "no remote-gate record names this project directory (the records carry project_dir, not a package id)"
        if held:
            detail += f"; {len(held)} gate slot(s) held estate-wide"
        return Field(value=UNKNOWN, source=GATES_SOURCE, state=UNKNOWN, detail=detail)
    newest = mine[0]
    detail = f"{len(mine)} result(s); newest {newest.name}"
    detail += f"; {len(held)} slot(s) held estate-wide ({', '.join(slot.name for slot in held)})" if held else "; no gate slot held"
    return Field(value=newest.text(), source=GATES_SOURCE, state=PRESENT, mtime=newest.mtime, detail=detail)


def _state_fields(
    package: str,
    peers: tuple[ag_sources.PeerState, ...],
    own_handover: tuple[str, float | None, str],
    now: float,
) -> tuple[Field, Field, Field]:
    """`(owner, plan, handover)` from the peer prose that names this package."""

    naming = [(peer, line) for peer in peers for line in peer.lines if line.package == package]
    if not naming:
        absent = [peer.peer for peer in peers if peer.state_state != PRESENT]
        detail = "no STATE.md line names this package id"
        if absent:
            detail += f"; unreadable peer state: {', '.join(absent)}"
        return (
            Field(value=UNKNOWN, source=STATE_SOURCE, state=UNKNOWN, detail=detail),
            Field(value=UNKNOWN, source=STATE_SOURCE, state=UNKNOWN, detail=detail),
            _own_handover_field(own_handover, now),
        )
    peer, line = naming[0]
    quote = line.text[:160]
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
        detail=f"no machine plan state yet (DESIGN 4a: STATE.md until plan.yaml lands): {quote}",
    )
    return owner, plan, _peer_handover_field(peer, now)


def _own_handover_field(own: tuple[str, float | None, str], now: float) -> Field:
    path, mtime, detail = own
    if mtime is None:
        return Field(value=UNKNOWN, source=f"{HANDOVER_SOURCE} {path}" if path else HANDOVER_SOURCE, state=MISSING, detail=detail)
    return Field(
        value=age_bucket(now - mtime),
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


def rows_from_inputs(view: View) -> list[Row]:
    """One row per package key: git identity, runs, gates and peer state joined on that key."""

    groups = _branch_worktrees(view.git)
    rows: list[Row] = []
    for package in sorted(groups):
        entries = groups[package]
        branches = tuple(entry.branch for entry in entries if entry.branch)
        heads = tuple((entry.head or UNKNOWN) for entry in entries)
        unmerged = tuple(entry.branch for entry in entries if entry.branch and view.git.merged_here(entry.branch) == "no")
        merged_states = {view.git.merged_here(entry.branch) for entry in entries}
        if "no" in merged_states:
            merged_value = "no: " + ", ".join(unmerged)
            merged_state = PRESENT
            merged_detail = "not in origin/main (merge candidate)"
        elif merged_states == {"yes"}:
            merged_value, merged_state, merged_detail = "yes", PRESENT, ""
        else:
            merged_value, merged_state = UNKNOWN, UNKNOWN
            merged_detail = view.git.detail or "the merge list does not decide this branch"
        records = view.run_set.for_key(package)
        owner, plan, handover = _state_fields(package, view.peers, (view.handover_path, view.handover_mtime, view.handover_detail), view.now)
        fields = {
            "owner": owner,
            "plan": plan,
            "worktree": _git_field("; ".join(entry.path for entry in entries), entries[0].path),
            "branch": _git_field("; ".join(branches), entries[0].path, detail="package key derived via topic_key"),
            "head": _git_field(
                "; ".join(heads),
                entries[0].path,
                state=PRESENT if all(head != UNKNOWN for head in heads) else UNKNOWN,
                detail="" if all(head != UNKNOWN for head in heads) else "the porcelain block carries no HEAD line",
            ),
            "merged": Field(value=merged_value, source=MERGED_SOURCE, state=merged_state, detail=merged_detail),
            "runs": _runs_field(records, view.run_set),
            "gates": _gates_field(package, view.results, view.slots),
            "handover": handover,
        }
        rows.append(Row(package=package, branches=branches, unmerged=unmerged, fields=fields))
    return rows


def render_text(rows: list[Row], view: View) -> str:
    git = view.git
    lines = [f"estate: {len(rows)} package row(s) of {len(git.worktrees)} worktree entries (source: {GIT_SOURCE})"]
    if git.merged_state != PRESENT:
        lines.append(f"estate: merge state UNKNOWN ({git.detail})")
    if not view.run_set.readable:
        lines.append(f"estate: run records UNKNOWN (no readable runs directory: {', '.join(view.run_set.dirs)})")
    held = [slot for slot in view.slots if slot.state == ag_sources.HELD]
    unknown_slots = [slot for slot in view.slots if slot.state == ag_sources.UNKNOWN]
    lines.append(
        f"estate: gate slots {len(view.slots)} ({len(held)} held, {len(unknown_slots)} unknown; source: {SLOTS_SOURCE})"
    )
    lines.append(f"estate: gate results {len(view.results)} (source: {GATES_SOURCE})")
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


def render_header(rows: list[Row], view: View) -> str:
    """The compact, hook-injectable block of DESIGN section 8 item 1d.

    Deterministic by construction: fixed order, fixed sections, ages as buckets and no
    timestamp that moves between two calls. An unchanged estate therefore prints
    byte-identical text, which is what lets the hook emit it only when the digest changes.
    """

    lines: list[str] = []
    merge = [(row.package, branch, row.fields["head"].value) for row in rows for branch in row.unmerged]
    lines.append(f"MERGE {len(merge)}: " + ("; ".join(f"{branch} {head}" for _, branch, head in merge) or "none"))
    held = [slot for slot in view.slots if slot.state == ag_sources.HELD]
    lines.append(
        f"LEASES {len(held)}: " + (", ".join(slot.text() for slot in held) if held else "none")
    )
    live = [record for record in view.run_set.records if record.state() == ag_runs.ACTIVE]
    if not view.run_set.readable:
        lines.append("LIVE: unknown (no readable runs directory)")
    else:
        rendered = "; ".join(
            f"{record.id} {record.route or 'route-unknown'}"
            + (f" {record.tokens // 1000}k" if record.tokens is not None else " ctx-unknown")
            for record in live
        )
        lines.append(f"LIVE {len(live)}: " + (rendered or "none"))
    peers = "; ".join(
        f"{peer.peer} {age_bucket(view.now - peer.handover_mtime) if peer.handover_mtime is not None else AGE_UNKNOWN}"
        for peer in view.peers
    )
    lines.append("PEERS: " + (peers or "none"))
    own = age_bucket(view.now - view.handover_mtime) if view.handover_mtime is not None else AGE_UNKNOWN
    lines.append(f"HANDOVER own {own}")
    # `_truncate` is total: it returns the block unchanged when it already fits, so the cap is
    # guaranteed in one place instead of an `if` here and another cut inside.
    return _truncate(lines) + "\n"


def _truncate(lines: list[str]) -> str:
    """The longest run of whole lines that fits the cap, marker included.

    Whole lines only, because half a line can be read as a different fact. A block that already
    fits is returned unchanged (no marker), so the function is total: the caller never has to
    decide whether truncation was needed. If not even the first line fits, that first line is
    cut to the cap and marked — an oversized single line must not produce an empty block.
    """

    room = HEADER_LIMIT - len(HEADER_MARKER) - 2
    if len("\n".join(lines)) <= room:
        return "\n".join(lines)
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if len(candidate) > room:
            break
        kept.append(line)
    if not kept:
        return lines[0][:room] + "\n" + HEADER_MARKER
    return "\n".join([*kept, HEADER_MARKER])


def read_view(config: ag_config.Config, *, now: float | None = None) -> tuple[View | None, str]:
    """Read every input once; a repository that cannot be resolved is a stated unknown."""

    root, detail = gitstate.discover_root(config.home)
    if root is None:
        return None, detail
    git = gitstate.read_view(root)
    locks = ag_sources.read_proc_locks()
    handover_path, handover_mtime, handover_detail = ag_sources.read_own_handover(config)
    view = View(
        git=git,
        run_set=ag_runs.read(config),
        slots=ag_sources.gate_slots(config, locks),
        results=ag_sources.read_results(config),
        peers=ag_sources.read_peers(config),
        handover_path=handover_path,
        handover_mtime=handover_mtime,
        handover_detail=handover_detail,
        now=time.time() if now is None else now,
    )
    return view, ""


def _rows(config: ag_config.Config, *, now: float | None = None) -> tuple[View | None, list[Row], str]:
    view, detail = read_view(config, now=now)
    if view is None:
        return None, [], detail
    return view, rows_from_inputs(view), ""


def run(args: argparse.Namespace, runtime: CliRuntime, *, config: ag_config.Config | None = None) -> int:
    """The ``estate`` handler: read every input, print rows (or the header). Read-only."""

    resolved = config if config is not None else ag_config.load()
    view, rows, detail = _rows(resolved)
    header = bool(getattr(args, "header", False))
    if view is None:
        # No repository is neither a crash nor an empty estate: it is a stated unknown.
        payload = {"schema": "agctl-estate/1", "root": None, "rows": [], "unknown": {"root": {"source": "git rev-parse --show-toplevel", "detail": detail}}}
        if runtime.json_mode:
            runtime.output.primary(payload)
        else:
            runtime.output.primary(f"estate: UNKNOWN - no git work tree could be resolved ({detail})\n")
        return 0
    if header:
        # The header is text by contract (a hook injects it); `--json --header` would be two
        # answers to one question, so it is refused rather than silently preferring one.
        if runtime.json_mode:
            raise ValueError("--header prints the injected text block; it cannot be combined with --json")
        runtime.output.primary(render_header(rows, view))
        return 0
    if runtime.json_mode:
        runtime.output.primary(
            {
                "schema": "agctl-estate/1",
                "root": view.git.root,
                "git": {"worktrees": len(view.git.worktrees), "merged": view.git.merged_state, "detail": view.git.detail},
                "sources": {
                    "runs": {"dirs": list(view.run_set.dirs), "readable": list(view.run_set.readable), "records": len(view.run_set.records)},
                    "gate_slots": [slot.as_dict() for slot in view.slots],
                    "gate_results": len(view.results),
                    "peers": [{"peer": peer.peer, "state": peer.state_state, "lines": len(peer.lines), "handover_mtime": peer.handover_mtime} for peer in view.peers],
                    "handover": {"path": view.handover_path, "mtime": view.handover_mtime},
                },
                "rows": [row.as_dict() for row in rows],
            }
        )
        return 0
    runtime.output.primary(render_text(rows, view))
    return 0
