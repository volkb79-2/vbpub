"""Tests for the ground-truth re-baseline probe. PACKAGE RP01.

Two disjoint concerns, tested separately per the handoff:

  * `resync_plan` (B.2's decision table) is PURE -- tested with plain
    in-memory facts, no filesystem/subprocess/git at all.
  * `gather_handoff_presence` / `gather_git_facts` (the I/O boundary) are
    tested against a REAL temporary git repo (reusing the `sample_project`
    fixture's already-initialized repo) plus one mocked-subprocess edge
    case for the git-invocation-failure path.

Everything here is dry-run only: no test asserts a statefile write or an
event append (RP01 never performs either).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from nyxloom import cli, storage
from nyxloom.resync import (
    ACTION_ADVANCE,
    ACTION_NEEDS_OPERATOR,
    ACTION_NONE,
    MERGE_SOURCE_CONTENT,
    MERGE_SOURCE_REFS,
    GitFacts,
    ProposedTransition,
    gather_git_facts,
    gather_handoff_presence,
    resync_plan,
)
from nyxloom.resync import _git as resync_git
from nyxloom.types import Attempt, AttemptState, Role, Route, TaskState, TaskStateFile, utc_now


def _tsf(task_id: str, state: TaskState, handoff_path: str | None = None,
         attempts: list[Attempt] | None = None) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project="demo",
        state=state,
        since=utc_now(),
        handoff_path=handoff_path,
        attempts=attempts or [],
    )


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# resync_plan -- pure, B.2's decision table

def test_resync_plan_merge_ready_merged_proposes_advance():
    """Oracle 1: the dstdns/ui-P10 case -- a task believed MERGE_READY whose
    branch is merged (and its handoff archived) -> propose MERGED/COMPLETED."""
    states = {
        "dstdns-P30": _tsf("dstdns-P30", TaskState.MERGE_READY,
                            handoff_path="handoffs/dstdns-P30.md"),
    }
    frontmatters = {"dstdns-P30": False}  # archived -- gone from the trove
    git_facts = GitFacts(merged_refs=frozenset({"feat/dstdns-P30", "dstdns-P30"}))

    plan = resync_plan(states, frontmatters, git_facts)

    assert len(plan) == 1
    row = plan[0]
    assert row.task_id == "dstdns-P30"
    assert row.believed_state is TaskState.MERGE_READY
    assert row.ground_truth == "merged"
    assert row.proposed_action == ACTION_ADVANCE
    assert "merged" in row.evidence


def test_resync_plan_genuinely_open_queued_no_action():
    """Oracle 2: a QUEUED task with no merge and handoff present -> no action."""
    states = {
        "demo-open": _tsf("demo-open", TaskState.QUEUED, handoff_path="handoff/demo-open.md"),
    }
    frontmatters = {"demo-open": True}
    git_facts = GitFacts()

    plan = resync_plan(states, frontmatters, git_facts)

    assert plan == [ProposedTransition(
        task_id="demo-open",
        believed_state=TaskState.QUEUED,
        ground_truth="open",
        proposed_action=ACTION_NONE,
        evidence="handoff present in trove; no merge detected — genuinely open",
    )]


def test_resync_plan_orphan_flagged_needs_operator_never_dropped():
    """Oracle 3: a statefile with no handoff and no merge -> NEEDS_OPERATOR,
    it must appear in the plan (never silently dropped)."""
    states = {"demo-orphan": _tsf("demo-orphan", TaskState.ACTIVE, handoff_path=None)}
    frontmatters = {"demo-orphan": False}
    git_facts = GitFacts()

    plan = resync_plan(states, frontmatters, git_facts)

    assert len(plan) == 1
    assert plan[0].ground_truth == "orphan"
    assert plan[0].proposed_action == ACTION_NEEDS_OPERATOR


def test_resync_plan_terminal_state_wins_precedence_over_merge_signal():
    """A COMPLETED/SUPERSEDED/CANCELLED task is already settled -- resync
    takes no action even if a (stale) merge signal is also present."""
    states = {"demo-done": _tsf("demo-done", TaskState.COMPLETED)}
    frontmatters = {"demo-done": False}
    git_facts = GitFacts(merged_refs=frozenset({"demo-done", "feat/demo-done"}))

    plan = resync_plan(states, frontmatters, git_facts)

    assert plan[0].ground_truth == "terminal"
    assert plan[0].proposed_action == ACTION_NONE


def test_resync_plan_non_terminal_belief_family_all_advance_when_merged():
    """B.2 rows 1+2 collapse: QUEUED/CARVED/ACTIVE/AWAITING_REVIEW/MERGE_READY
    all propose the same advance once merged is confirmed."""
    believed_states = [
        TaskState.CARVED, TaskState.QUEUED, TaskState.ACTIVE,
        TaskState.AWAITING_REVIEW, TaskState.MERGE_READY,
    ]
    states = {f"t-{s.value}": _tsf(f"t-{s.value}", s) for s in believed_states}
    frontmatters = {tid: False for tid in states}
    git_facts = GitFacts(merged_refs=frozenset(
        tid for tid in states
    ) | frozenset(f"feat/{tid}" for tid in states))

    plan = resync_plan(states, frontmatters, git_facts)

    assert len(plan) == len(believed_states)
    assert all(row.proposed_action == ACTION_ADVANCE for row in plan)
    assert all(row.ground_truth == "merged" for row in plan)


def test_resync_plan_content_merge_evidence_used_when_not_in_merged_refs():
    """The content-check channel (squash/CAS) also drives the advance
    proposal, not just the `--merged` channel."""
    states = {"demo-squash": _tsf("demo-squash", TaskState.CARVED)}
    frontmatters = {"demo-squash": False}
    git_facts = GitFacts(content_merged={"demo-squash": "archived path: docs/archive/x.md"})

    plan = resync_plan(states, frontmatters, git_facts)

    assert plan[0].ground_truth == "merged"
    assert plan[0].proposed_action == ACTION_ADVANCE
    assert plan[0].evidence == "archived path: docs/archive/x.md"
    assert plan[0].merge_source == MERGE_SOURCE_CONTENT


def test_resync_plan_present_handoff_outranks_content_merge_evidence():
    """The dstdns-P31/P32 fix: a still-present handoff is authoritatively
    OPEN even when the LOW-confidence content channel has a (false-positive)
    entry for it — e.g. a carve commit whose message names the task id when
    creating its handoff. Content evidence must NOT retire a handoff that is
    physically still in the trove (a bare `--apply` would otherwise be one
    `--apply-content-merges` away from dropping live forward work)."""
    states = {
        "dstdns-P31": _tsf("dstdns-P31", TaskState.QUEUED,
                           handoff_path="nyxloom-trove/handoffs/dstdns-P31.md"),
    }
    frontmatters = {"dstdns-P31": True}  # still present in handoffs/
    git_facts = GitFacts(content_merged={
        "dstdns-P31": "commit-log match for 'dstdns-P31' on main: f4776ef2 carve(...): P30->P31->P32",
    })

    plan = resync_plan(states, frontmatters, git_facts)

    assert len(plan) == 1
    assert plan[0].ground_truth == "open"
    assert plan[0].proposed_action == ACTION_NONE
    assert plan[0].merge_source is None


def test_resync_plan_merged_ref_still_wins_over_present_handoff():
    """The precedence guard's other side: a genuine `git branch --merged` ref
    DOES outrank physical presence (a merged branch whose handoff file was
    not yet archived) -> still proposes advance, tagged MERGE_SOURCE_REFS."""
    states = {"demo-linger": _tsf("demo-linger", TaskState.MERGE_READY)}
    frontmatters = {"demo-linger": True}  # file lingers, but branch is merged
    git_facts = GitFacts(merged_refs=frozenset({"demo-linger", "feat/demo-linger"}))

    plan = resync_plan(states, frontmatters, git_facts)

    assert plan[0].ground_truth == "merged"
    assert plan[0].proposed_action == ACTION_ADVANCE
    assert plan[0].merge_source == MERGE_SOURCE_REFS


def test_resync_plan_missing_frontmatter_entry_defaults_to_not_present():
    """A task_id entirely absent from the frontmatters mapping (not merely
    False) still fails safe to the orphan branch."""
    states = {"demo-gap": _tsf("demo-gap", TaskState.QUEUED)}
    plan = resync_plan(states, {}, GitFacts())
    assert plan[0].ground_truth == "orphan"
    assert plan[0].proposed_action == ACTION_NEEDS_OPERATOR


def test_resync_plan_is_pure_and_deterministic():
    """Oracle 5: identical (in-memory, no I/O) inputs called twice yield an
    identical plan, in a stable (sorted task_id) order."""
    states = {
        "a": _tsf("a", TaskState.MERGE_READY),
        "b": _tsf("b", TaskState.QUEUED, handoff_path="handoff/b.md"),
        "c": _tsf("c", TaskState.ACTIVE),
    }
    frontmatters = {"a": False, "b": True, "c": False}
    git_facts = GitFacts(merged_refs=frozenset({"a", "feat/a"}))

    plan1 = resync_plan(states, frontmatters, git_facts)
    plan2 = resync_plan(states, frontmatters, git_facts)

    assert plan1 == plan2
    assert [p.task_id for p in plan1] == ["a", "b", "c"]
    assert plan1[0].ground_truth == "merged"
    assert plan1[1].ground_truth == "open"
    assert plan1[2].ground_truth == "orphan"


# ---------------------------------------------------------------------------
# gather_handoff_presence -- filesystem + frontmatter parse

def test_gather_handoff_presence_present_missing_none_and_malformed(sample_project, tmp_state):
    root = sample_project.root
    (root / "handoff" / "broken.md").write_text("not frontmatter at all\n")

    states = {
        "t-present": _tsf("t-present", TaskState.QUEUED, handoff_path="handoff/demo-P01-sample.md"),
        "t-missing": _tsf("t-missing", TaskState.QUEUED, handoff_path="handoff/does-not-exist.md"),
        "t-none": _tsf("t-none", TaskState.QUEUED, handoff_path=None),
        "t-malformed": _tsf("t-malformed", TaskState.QUEUED, handoff_path="handoff/broken.md"),
    }

    out = gather_handoff_presence(sample_project, states)

    assert out == {
        "t-present": True,
        "t-missing": False,
        "t-none": False,
        "t-malformed": False,
    }


def test_gather_handoff_presence_id_scan_survives_stale_handoff_path(sample_project, tmp_state):
    """The topos fix: a statefile whose `handoff_path` is STALE (a
    pre-standardization location that no longer exists — topos carried
    `handoff/<id>.md` while the file lives at `nyxloom-trove/handoffs/`) is
    STILL 'present' when a handoff carrying its id is discoverable under the
    project's handoff_globs. Presence is a fact about the trove, not the path
    string. (sample_project already ships handoff/demo-P01-sample.md with
    frontmatter id 'demo-P01-sample'.)"""
    states = {
        "demo-P01-sample": _tsf(
            "demo-P01-sample", TaskState.QUEUED,
            handoff_path="legacy/old-location/demo-P01-sample.md",  # stale, gone
        ),
    }

    out = gather_handoff_presence(sample_project, states)

    assert out == {"demo-P01-sample": True}


# ---------------------------------------------------------------------------
# gather_git_facts / the hardened merge-check helper

def test_gather_git_facts_branch_merged_detected(sample_project, tmp_state):
    root = sample_project.root
    _run_git(root, "checkout", "-b", "feat/demo-P02-merged")
    (root / "marker-P02.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "P02 work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge P02", "feat/demo-P02-merged")

    states = {"demo-P02-merged": _tsf("demo-P02-merged", TaskState.MERGE_READY)}
    facts = gather_git_facts(str(root), "main", states)

    assert "feat/demo-P02-merged" in facts.merged_refs
    assert "demo-P02-merged" in facts.merged_refs
    # Already covered by --merged -- no need for (and no) content-check entry.
    assert "demo-P02-merged" not in facts.content_merged


def test_gather_git_facts_content_check_archived_path_catches_squash_merge(sample_project, tmp_state):
    """Oracle 4: a branch NOT in `git branch --merged` (here: no branch ref
    exists at all -- the deleted-branch case) but whose archived handoff
    path IS present on `main` -> the content check still classifies it
    merged. The commit message deliberately does NOT mention the task id,
    so this exercises the archive-path scan, not the commit-log grep."""
    root = sample_project.root
    archive_dir = root / "docs" / "archive" / "handoff"
    archive_dir.mkdir(parents=True)
    (archive_dir / "demo-P05-squash-REPORT.md").write_text("archived report\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "chore: housekeeping")

    states = {"demo-P05-squash": _tsf("demo-P05-squash", TaskState.QUEUED)}
    facts = gather_git_facts(str(root), "main", states)

    assert "demo-P05-squash" not in facts.merged_refs
    assert "feat/demo-P05-squash" not in facts.merged_refs
    assert "demo-P05-squash" in facts.content_merged
    assert "docs/archive/handoff/demo-P05-squash-REPORT.md" in facts.content_merged["demo-P05-squash"]


def test_gather_git_facts_content_check_commit_log_grep_catches_squash_reference(sample_project, tmp_state):
    """The other content-check channel: a squash commit's own message
    conventionally names the source branch, even with no archived file and
    no surviving branch ref."""
    root = sample_project.root
    _run_git(root, "commit", "--allow-empty", "-qm", "Squash merge feat/demo-P06-log: shipped")

    states = {"demo-P06-log": _tsf("demo-P06-log", TaskState.ACTIVE)}
    facts = gather_git_facts(str(root), "main", states)

    assert "demo-P06-log" in facts.content_merged
    assert "commit-log match" in facts.content_merged["demo-P06-log"]


def test_gather_git_facts_uses_attempt_branch_as_merge_candidate(sample_project, tmp_state):
    """A recorded Attempt.branch (not just the `feat/<id>` convention) is
    also checked against `--merged`."""
    root = sample_project.root
    _run_git(root, "checkout", "-b", "custom/oddball-branch-name")
    (root / "marker-odd.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "oddball work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge oddball", "custom/oddball-branch-name")

    att = Attempt(
        attempt_id="att-1", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="r", cli="fake", model="m"), started=utc_now(),
        branch="custom/oddball-branch-name",
    )
    states = {"demo-P10-oddball": _tsf("demo-P10-oddball", TaskState.AWAITING_REVIEW, attempts=[att])}
    facts = gather_git_facts(str(root), "main", states)

    assert "custom/oddball-branch-name" in facts.merged_refs
    assert "demo-P10-oddball" not in facts.content_merged


def test_gather_git_facts_no_evidence_leaves_task_unmerged(sample_project, tmp_state):
    """A task with genuinely no merge signal anywhere -> absent from both
    channels (resync_plan will then fall through to open/orphan)."""
    root = sample_project.root
    states = {"demo-P09-untouched": _tsf("demo-P09-untouched", TaskState.QUEUED)}
    facts = gather_git_facts(str(root), "main", states)

    assert "demo-P09-untouched" not in facts.merged_refs
    assert "feat/demo-P09-untouched" not in facts.merged_refs
    assert "demo-P09-untouched" not in facts.content_merged


def test_git_helper_returns_empty_on_nonzero_git_exit(tmp_path, tmp_state):
    """`tmp_path` is not a git repo at all -- every git invocation fails
    (returncode != 0) and the fact-gatherer fails safe (empty facts, no
    crash) rather than propagating the error."""
    states = {"demo-nope": _tsf("demo-nope", TaskState.QUEUED)}
    facts = gather_git_facts(str(tmp_path), "main", states)

    assert facts.merged_refs == frozenset()
    assert facts.content_merged == {}


def test_git_helper_returns_empty_on_oserror(monkeypatch):
    """A missing git executable (or any OSError) is swallowed, not raised."""
    from nyxloom import resync as resync_mod

    def _raise(*_a, **_k):
        raise OSError("git executable not found")

    monkeypatch.setattr(resync_mod.subprocess, "run", _raise)

    assert resync_git("/some/repo", ["status"]) == ""


# ---------------------------------------------------------------------------
# CLI verb: `nyxloom resync <project>` -- prints, never writes

def test_cli_resync_no_tasks_prints_message(sample_project, tmp_state, capsys):
    exit_code = cli.main(["resync", "demo"])
    assert exit_code == 0
    assert "no tasks" in capsys.readouterr().out


def test_cli_resync_prints_table_for_merged_task(sample_project, tmp_state, capsys):
    root = sample_project.root
    _run_git(root, "checkout", "-b", "feat/demo-P20-cli")
    (root / "marker-cli.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "P20 work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge P20", "feat/demo-P20-cli")

    storage.save_state(_tsf("demo-P20-cli", TaskState.MERGE_READY))

    exit_code = cli.main(["resync", "demo"])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "demo-P20-cli" in out
    assert "MERGE_READY" in out
    assert "MERGED/COMPLETED" in out
    assert "merged" in out

    # RP01 is dry-run only: the statefile itself must be untouched.
    reloaded = storage.load_state("demo", "demo-P20-cli")
    assert reloaded is not None
    assert reloaded.state is TaskState.MERGE_READY
