#!/usr/bin/env python3
"""Extract the CR-06 planner differential corpus from real project event logs.

The amendment's §5.1 names two differential inputs for CR-06: the CR-00
fixtures, and "the historical event log of the self-host project". This tool
produces the second one as a committed, hermetic fixture so the differential
does not read the operator's live state directory at test time.

WHAT IS REAL AND WHAT IS NOT. The corpus is the projection-affecting slice of
three projects' real event logs -- `nyxloom` (the self-host project), `dstdns`
and `topos`. Replaying it reproduces the exact task/attempt projections those
projects actually passed through: real state combinations, real attempt
ladders, real interrupt/resume/failure sequences. Everything OUTSIDE the
projection (routes, provider health, budgets, pause mode, carver session) is
NOT taken from the logs -- the differential supplies those as declared
profiles, because their historical values were never persisted per pass.

WHAT IS PRUNED, AND THE CHECK THAT PRUNING IS FAITHFUL. Payloads are reduced
to the fields the projector consumes and the planner can observe: prose
(`notes`, blocker detail), filesystem paths, session handles' contents and
usage/cost records are dropped, because committing an operator's working
history into a test fixture is neither necessary nor appropriate. Pruning is
not assumed safe -- this tool REFUSES to write a corpus unless, for every
snapshot, the frozen legacy planner produces an identical plan from the pruned
projection and from the raw one. A pruning that changed a plan would be a
corpus that silently tests something other than what happened.

Events the current projector REFUSES are recorded in the manifest with their
count rather than dropped silently: CR-04a made validation read committed
state, so a historical log can contain transitions the store would no longer
accept. That count is a fact about the corpus and belongs in it.

Usage (from the nyxloom package root, with the daemon's state root):

    NYXLOOM_STATE=~/.local/state/nyxloom PYTHONPATH=src:tests \\
        python tools/extract_planner_corpus.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from nyxloom import storage                       # noqa: E402
from nyxloom.projection import apply_event        # noqa: E402
from nyxloom.types import TaskStateFile           # noqa: E402

PROJECTS = ("nyxloom", "dstdns", "topos")
OUT = REPO / "tests" / "fixtures" / "planner_corpus_v1.json"
CORPUS_VERSION = 1

#: Attempt fields the planner can observe. `route` is kept because the
#: projector requires it to rebuild an Attempt; its value is replaced.
_ATTEMPT_KEEP = (
    "attempt_id", "role", "state", "started", "ended", "session_handle", "wave_id",
)
#: Statefile fields the planner can observe.
_STATE_KEEP = (
    "schema_version", "task_id", "project", "state", "since", "wave_id",
    "paused", "attempts",
)


#: real session handle -> stable pseudonym, in first-seen order.
_HANDLES: dict[str, str] = {}


def _pseudonym(handle: str) -> str:
    """A stable stand-in for one provider session id.

    DISTINCTNESS IS LOAD-BEARING, so this is a mapping and not a constant. A
    real handle is an opaque provider session id that does not belong in a
    committed fixture -- but the planner does not merely test it for
    truthiness: the reviewer session-reuse rule picks the newest handle and
    puts its VALUE in `LaunchReview.resume_session`. Collapsing every handle
    to one placeholder would make that selection unobservable, so distinct
    handles stay distinct and only their content is replaced.
    """
    return _HANDLES.setdefault(handle, f"corpus-session-{len(_HANDLES) + 1}")


def _prune_attempt(raw: dict) -> dict:
    """Reduce an attempt record to what the planner reads.

    `receipt` is reduced to its `result` and exit code, which is all
    `attempts_used` and the receipt ladder consult; worktree/branch/log paths,
    pids and usage records are dropped.
    """
    out = {k: raw[k] for k in _ATTEMPT_KEEP if k in raw}
    out["route"] = {"route_id": "corpus-route", "cli": "fake", "model": "fake-a"}
    if raw.get("session_handle"):
        out["session_handle"] = _pseudonym(raw["session_handle"])
    receipt = raw.get("receipt")
    if receipt is not None:
        out["receipt"] = {
            "result": receipt.get("result"),
            "exit_code": receipt.get("exit_code", 0),
        }
    return out


def _pseudonymized_raw(states: dict[str, TaskStateFile]) -> dict[str, TaskStateFile]:
    """The raw projection with ONLY session handles pseudonymized.

    The fidelity check below compares this against the fully pruned
    projection. Handles are replaced on both sides because their content is
    deliberately not committed; every other field is left exactly as the log
    recorded it, so a pruning that dropped something the planner reads still
    shows up as a plan difference.
    """
    out: dict[str, TaskStateFile] = {}
    for tid, tsf in states.items():
        raw = tsf.to_dict()
        for att in raw.get("attempts", []):
            if att.get("session_handle"):
                att["session_handle"] = _pseudonym(att["session_handle"])
        out[tid] = TaskStateFile.from_dict(raw)
    return out


def _prune_state(raw: dict) -> dict:
    out = {k: raw[k] for k in _STATE_KEEP if k in raw}
    out["attempts"] = [_prune_attempt(a) for a in raw.get("attempts", [])]
    return out


def _snapshot_key(states: dict[str, dict]) -> str:
    blob = json.dumps(states, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


#: Payload keys carrying operator prose or absolute paths. Dropped wholesale;
#: the projector treats each as optional and the planner reads none of them.
_DROP_KEYS = ("notes", "reason", "output_tail", "detail")


def _prune_payload(kind: str, payload: dict) -> dict:
    """Reduce one event payload to the projector's and planner's surface.

    Deliberately a SUBTRACTION rather than a whitelist per event type: every
    key not named here survives verbatim, so an event type this tool has never
    seen still replays correctly instead of silently losing a field. The
    fidelity check in `main` is what proves the subtraction was safe.
    """
    out = {k: v for k, v in payload.items() if k not in _DROP_KEYS}
    if "attempt" in out and isinstance(out["attempt"], dict):
        out["attempt"] = _prune_attempt(out["attempt"])
    if "statefile" in out and isinstance(out["statefile"], dict):
        out["statefile"] = _prune_state(out["statefile"])
    if "merge_commit" in out:
        out["merge_commit"] = "0" * 40
    if isinstance(out.get("blocker"), dict):
        out["blocker"] = {k: v for k, v in out["blocker"].items() if k not in _DROP_KEYS}
    if isinstance(out.get("gate_result"), dict):
        out["gate_result"] = {
            k: v for k, v in out["gate_result"].items()
            if k not in _DROP_KEYS and k != "artifacts"
        }
        out["gate_result"]["commit"] = "0" * 40
    return out


def _event_row(ev) -> dict:
    return {
        "sequence": ev.sequence,
        # Kept, not normalized: `apply_event` stamps `TaskStateFile.since`
        # from it, and the wave age trigger reads that against `inp.now`.
        # A corpus with synthetic timestamps could not exercise it.
        "timestamp": ev.timestamp.isoformat(),
        "type": ev.type.value,
        "task_id": ev.task_id,
        "attempt_id": ev.attempt_id,
        "wave_id": ev.wave_id,
        "decision_id": ev.decision_id,
        "payload": _prune_payload(ev.type.value, ev.payload),
    }


def _plan_repr(states: dict[str, TaskStateFile]) -> str:
    """The frozen legacy planner's plan for a projection, as text.

    Imported lazily and locally so this tool has no import-time dependency on
    the test tree when someone is only reading it.
    """
    import legacy_planner
    from corpus_profiles import baseline_input   # tests/corpus_profiles.py

    plan = legacy_planner.plan_project(baseline_input(dict(states)))
    return "\n".join(sorted(repr(a) for a in plan))


def _rebuild(project: str, row: dict):
    from planner_corpus import rebuild_event      # tests/planner_corpus.py
    return rebuild_event(project, row)


def main() -> int:
    """Emit the pruned event LOG, not the projections it produces.

    The projections are what the differential plans against, but storing them
    costs ~11 MiB: 800 snapshots of a 30-task projection share almost all
    their content. The log that generates them is two orders of magnitude
    smaller, and replaying it in the test is a few milliseconds -- so the
    fixture is the log and `replay()` below is its only consumer.
    """
    logs: dict[str, list[dict]] = {}
    manifest: list[dict] = []
    for project in PROJECTS:
        raw_states: dict[str, TaskStateFile] = {}
        pruned_states: dict[str, TaskStateFile] = {}
        seen: set[str] = set()
        rows: list[dict] = []
        refused = 0
        total = 0
        for ev in storage.iter_events(project):
            total += 1
            try:
                affected = apply_event(raw_states, ev)
            except Exception:
                # CR-04a made validation read committed state, so a historical
                # log can carry transitions the store would no longer accept.
                # Counted in the manifest rather than dropped silently.
                refused += 1
                continue
            row = _event_row(ev)
            # The pruned log must replay to the same projection the raw one
            # does, judged by the plan the frozen planner makes from it --
            # otherwise the corpus tests something other than what happened.
            apply_event(pruned_states, _rebuild(project, row))
            if _plan_repr(pruned_states) != _plan_repr(_pseudonymized_raw(raw_states)):
                print(f"REFUSING: pruning changed the plan at {project}-{ev.sequence}",
                      file=sys.stderr)
                return 2
            rows.append(row)
            if affected and pruned_states:
                # Same dedup key the test-side reader uses, so the manifest
                # describes the corpus a reader actually gets rather than a
                # second count that can disagree with it.
                seen.add(json.dumps(
                    {t: v.to_dict() for t, v in sorted(pruned_states.items())},
                    sort_keys=True, default=str))
        logs[project] = rows
        manifest.append({
            "project": project, "events": total, "refused_by_projector": refused,
            "replayed_events": len(rows), "distinct_projections": len(seen),
        })
        print(f"{project}: {total} events, {refused} refused, "
              f"{len(rows)} replayed, {len(seen)} distinct projections")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "version": CORPUS_VERSION,
        "generated_by": "tools/extract_planner_corpus.py",
        "manifest": manifest,
        "logs": logs,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
