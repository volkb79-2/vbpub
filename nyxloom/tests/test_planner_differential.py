"""Differential verification for the planner (amendment §5.1).

The amendment makes this a REQUIRED acceptance mechanism for CR-06, not an
idea: "run the old and new planner over identical snapshots -- the CR-00
fixtures *and* the historical event log of the self-host project -- and diff
the planned action sequences. Any difference is either explained in the
package report or is a defect."

The two halves it names are both here:

* `test_historical_corpus_plans_are_identical` -- 800-odd projections replayed
  from three projects' real event logs, each planned under every declared
  environment profile.
* `test_cr00_corpus_plans_are_identical` -- the CR-00 characterization
  scenarios, which are the cases someone did think to write down.

HOW THE BASELINE CANNOT DRIFT. `tests/legacy_planner.py` is a verbatim copy of
`reconcile.py` at the branch point, and imports no production planning code --
so a change to the new planner cannot reach it. The first test below fails if
that copy stops matching the commit it claims to be.

WHAT "IDENTICAL" MEANS, precisely, and why the two halves differ:

* ACTIONS: identical, in order. The legacy module's action classes are
  distinct Python classes from production's, so plans are compared as
  normalized (class name, field mapping) tuples -- which is the observable the
  amendment names, and is insensitive to a class moving modules.
* TRACE: every legacy breadcrumb still appears, in order, with the same
  `(kind, task_id, detail)` -- a SUBSEQUENCE check rather than equality.
  Equality would forbid CR-06's own deliverable: the parent's work item 3 is
  "return actions plus structured rule-match explanations", so the breadcrumb
  stream is expected to GROW. Subsequence keeps every existing reader intact
  (the CR-00 corpus classifies waits from these strings verbatim) while
  letting the explanation model deepen.

Under the program's stop-loss (§5.4) an unexplained difference stops the
package. It is not written down as expected.
"""

from __future__ import annotations

import subprocess
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

import corpus_profiles
import legacy_planner
import planner_corpus
from nyxloom import reconcile

REPO = Path(__file__).resolve().parents[1]
LEGACY_PATH = Path(__file__).with_name("legacy_planner.py")

#: The commit `tests/legacy_planner.py` was copied from -- CR-06's branch
#: point on `main`.
BRANCH_POINT = "052857ae"
SOURCE_IN_TREE = "nyxloom/src/nyxloom/reconcile.py"

#: The two mechanical edits the frozen copy declares in its own header, as
#: (frozen text, original text). Applying them in reverse must reproduce the
#: committed blob byte for byte.
_FROZEN_EDITS = (
    ("from nyxloom.carver_session import", "from .carver_session import"),
    ("from nyxloom import snapshot", "from . import snapshot"),
    ("from nyxloom.config import", "from .config import"),
    ("from nyxloom.stages import", "from .stages import"),
    ("from nyxloom.types import", "from .types import"),
)
_HEADER_END = "Pure reconcile planner (SPEC §5, §8, §9). PACKAGE P02."


def _normalize_action(action) -> tuple:
    """(class name, field mapping) -- comparable across the two modules."""
    assert is_dataclass(action), action
    return (
        type(action).__name__,
        tuple(sorted((f.name, repr(getattr(action, f.name))) for f in fields(action))),
    )


def _normalize_plan(plan) -> tuple:
    return tuple(_normalize_action(a) for a in plan)


def _breadcrumbs(plan) -> tuple:
    trace = getattr(plan, "trace", None)
    if trace is None:
        return ()
    return tuple((n.kind, n.task_id, n.detail) for n in trace.breadcrumbs)


def _is_subsequence(needle: tuple, haystack: tuple) -> bool:
    it = iter(haystack)
    return all(item in it for item in needle)


def compare(inp) -> list[str]:
    """Plan `inp` with both planners; return a list of human-readable deltas.

    Empty list means zero delta. Returning the deltas rather than asserting
    lets the mutation test below prove this function can actually SEE a
    difference -- a comparator nobody has watched fail is not a comparator.
    """
    old = legacy_planner.plan_project(inp)
    new = reconcile.plan_project(inp)
    deltas: list[str] = []
    old_actions, new_actions = _normalize_plan(old), _normalize_plan(new)
    if old_actions != new_actions:
        only_old = [a for a in old_actions if a not in new_actions]
        only_new = [a for a in new_actions if a not in old_actions]
        deltas.append(
            f"actions differ: only-legacy={only_old!r} only-new={only_new!r} "
            f"(order: legacy={[a[0] for a in old_actions]} new={[a[0] for a in new_actions]})"
        )
    old_trace, new_trace = _breadcrumbs(old), _breadcrumbs(new)
    if not _is_subsequence(old_trace, new_trace):
        missing = [n for n in old_trace if n not in new_trace]
        deltas.append(f"trace lost breadcrumbs: {missing!r} (new={new_trace!r})")
    return deltas


# ---------------------------------------------------------------------------
# the baseline and the corpus are what they claim to be


def test_legacy_baseline_is_the_committed_branch_point():
    """Undoing the two declared edits must reproduce the committed blob.

    Without this the baseline is only as trustworthy as the promise that
    nobody edited it, and the cheapest way past a failing differential would
    be to edit the thing it is measured against.
    """
    committed = subprocess.run(
        ["git", "show", f"{BRANCH_POINT}:{SOURCE_IN_TREE}"],
        cwd=REPO.parent, capture_output=True, text=True, check=True,
    ).stdout
    frozen = LEGACY_PATH.read_text(encoding="utf-8")
    head, _, rest = frozen.partition(_HEADER_END)
    assert head.startswith('"""FROZEN LEGACY PLANNER'), "frozen header missing"
    restored = _HEADER_END + rest
    for frozen_text, original_text in _FROZEN_EDITS:
        assert frozen_text in restored, f"declared edit not present: {frozen_text}"
        restored = restored.replace(frozen_text, original_text)
    assert '"""' + restored == committed, (
        "tests/legacy_planner.py is no longer a verbatim copy of "
        f"{BRANCH_POINT}:{SOURCE_IN_TREE} modulo its two declared edits"
    )


def test_corpus_is_the_recorded_historical_logs():
    """The fixture's manifest must describe the log it actually contains."""
    manifest = {row["project"]: row for row in planner_corpus.manifest()}
    assert set(manifest) == {"nyxloom", "dstdns", "topos"}
    for project, row in manifest.items():
        assert row["events"] > 0
        assert row["replayed_events"] == row["events"] - row["refused_by_projector"]
        assert row["distinct_projections"] > 0
    total = sum(r["distinct_projections"] for r in manifest.values())
    # Distinct-by-content across projects, so the whole is <= the sum.
    assert 0 < len(planner_corpus.projections()) <= total


def test_corpus_projections_cover_the_states_the_planner_branches_on():
    """A corpus that never reaches a state proves nothing about the rules that
    own it. This names the coverage rather than assuming a big log has it."""
    from nyxloom.types import TaskState
    seen = {
        tsf.state for _, states in planner_corpus.projections()
        for tsf in states.values()
    }
    required = {
        TaskState.QUEUED, TaskState.ACTIVE, TaskState.AWAITING_REVIEW,
        TaskState.REVIEW_REJECTED, TaskState.MERGE_READY, TaskState.MERGED,
        TaskState.BLOCKED, TaskState.COMPLETED, TaskState.SUPERSEDED,
        TaskState.CARVED,
    }
    assert required <= seen, f"corpus never reaches {sorted(s.value for s in required - seen)}"


# ---------------------------------------------------------------------------
# the differential itself


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_historical_corpus_plans_are_identical(profile: str):
    """Every real projection, planned under one declared environment."""
    deltas: list[str] = []
    for case_id, states in planner_corpus.projections():
        found = compare(corpus_profiles.build(states, profile))
        if found:
            deltas.append(f"{case_id}/{profile}: {found[0]}")
            if len(deltas) >= 5:
                break
    assert not deltas, "planner differential delta:\n" + "\n".join(deltas)


def test_cr00_corpus_plans_are_identical():
    """The other half of §5.1: the CR-00 characterization scenarios.

    Built through the characterization module's own input builder, so this
    plans exactly what that corpus plans -- not a re-derivation of it that
    could drift into agreeing with itself.
    """
    import test_core_characterization as cc

    checked = 0
    for scenario in cc._active():
        state = cc._project(cc._history(scenario))[cc.TASK_ID]
        found = compare(cc._plan_input(scenario, state))
        assert not found, f"{scenario['name']}: {found[0]}"
        checked += 1
    assert checked >= 10, f"only {checked} CR-00 scenarios exercised"


# ---------------------------------------------------------------------------
# the anti-decoration check


def test_the_comparator_reports_an_action_delta():
    """A differential that has never been seen to fail is decoration.

    The two planners agree by construction while CR-06 is unstarted, so this
    plants the difference instead: the same projection is planned under two
    environments that a rule genuinely discriminates on, through the same
    comparison code the tests above use. If this passes silently, every green
    above means nothing.
    """
    states = next(
        states for _, states in planner_corpus.projections()
        if any(t.state.value == "QUEUED" for t in states.values())
    )
    old = legacy_planner.plan_project(corpus_profiles.build(states, "neutral"))
    new = reconcile.plan_project(corpus_profiles.build(states, "paused"))
    assert _normalize_plan(old) != _normalize_plan(new), (
        "pausing a project with queued work changed no plan -- the corpus "
        "case chosen here cannot discriminate, so the comparator is untested")


def test_the_comparator_reports_a_lost_breadcrumb():
    """The trace half of the comparison, exercised the same way."""
    full = (("dispatch", "t1", "route:r"), ("carve", None, "headroom"))
    assert _is_subsequence(full, full)
    assert _is_subsequence((("carve", None, "headroom"),), full)
    # a breadcrumb the new plan dropped, and one whose detail changed
    assert not _is_subsequence((("dispatch", "t1", "route:other"),), full)
    # order matters: a subsequence is not a subset
    assert not _is_subsequence(tuple(reversed(full)), full)
