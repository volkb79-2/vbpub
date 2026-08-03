"""F007 2026-07-27 (gap-engine wave 2): GAP2, the verdict-audit extension --
plan-gap-engine-and-reviewer-repair.md §GAP2. Periodically re-judges a
bounded sample of recently-COMPLETED tasks BLIND (only the handoff's oracles
and the final merged diff -- never the recorded verdict/rationale/
REJECT_CLASS), then the DAEMON (never the agent) compares that blind
judgment against what it already recorded. Disagreements become carve
candidates through the same CARVE_OUTCOME envelope GAP1's gap-audit already
uses; agreement produces nothing.

Extends GAP1's `kind == "gap-audit"` carve packet (not a sibling `kind`) --
see `daemon._verdict_audit_section_lines`'s own docstring for why: the two
prompts share the identical cadence trigger, carve authority, and REQUIRED
OUTPUT CONTRACT envelope, so a second dispatch mechanism would only
duplicate plumbing GAP1 already built. Local helpers mirror
tests/test_gap_audit.py and tests/test_daemon.py's own conventions (never
imported across test files -- STANDING.md).

Every oracle is tested with observable evidence and a deliberate negative.
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from nyxloom import daemon, lint, paths, reconcile, storage
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt, ReceiptResult,
    Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers (never added to conftest.py -- STANDING.md)
# --------------------------------------------------------------------------

def _record_review(project, task_id, result):
    """CR-03 (DR-13): the RECORDED verdict is now the daemon's own
    REVIEW_RECORDED event, not a fresh markdown scan.

    That is the same fact the merge decision was made on. Re-deriving it from
    the branch could disagree with what the daemon actually did, and an audit
    that compares against a re-derivation audits the derivation instead of
    the decision -- which is the one thing this audit exists to catch."""
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.REVIEW_RECORDED, payload={"result": result}, task_id=task_id)


def _make_feature_branch(root: Path, task_id: str, filename: str, content: str) -> None:
    subprocess.run(["git", "-C", str(root), "branch", f"feat/{task_id}"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", f"feat/{task_id}"],
                    check=True, capture_output=True)
    (root / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                    "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                    "commit", "-qm", f"feat {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)


def _commit_review_report(root: Path, task_id: str, reports_dir: str, content: str) -> None:
    """Commit `<reports_dir>/<task_id>-REVIEW.md` onto feat/<task_id> --
    that branch must already exist (see _make_feature_branch)."""
    branch = f"feat/{task_id}"
    subprocess.run(["git", "-C", str(root), "checkout", branch], check=True, capture_output=True)
    report_dir = root / reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{task_id}-REVIEW.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                    "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                    "commit", "-qm", f"review {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)


def _merge_feature_branch(root: Path, task_id: str, allow_empty: bool = False) -> str:
    """Real `git merge --no-ff feat/<task_id>` into main; returns the merge
    commit sha. `allow_empty=True` commits nothing new onto the feature
    branch first (an empty commit) so the resulting merge is a real 2-parent
    commit whose diff against its own first parent is genuinely empty."""
    if allow_empty:
        subprocess.run(["git", "-C", str(root), "checkout", f"feat/{task_id}"],
                        check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                        "commit", "--allow-empty", "-qm", "empty"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
         "merge", "--no-ff", f"feat/{task_id}", "-m", f"Merge {task_id}"],
        check=True, capture_output=True,
    )
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()


def _write_completed_handoff(root: Path, task_id: str, component: str | None = None,
                              oracle_ids: tuple[str, ...] = ("O1",)) -> str:
    """A minimal schema-valid handoff for `task_id`, optionally carrying
    `component`. Written directly to the working tree (not committed --
    _frontmatter_for reads the live filesystem, not git history)."""
    comp_line = f"component: {component}\n" if component else ""
    oracle_lines = "".join(
        f"  - id: {oid}\n"
        f'    observable: "pytest tests/test_{task_id}.py::test_{oid.lower()} passes"\n'
        f'    negative: "a bad value raises ValueError (test_{oid.lower()}_violation)"\n'
        "    gate: pytest-q\n"
        for oid in oracle_ids
    )
    text = (
        "---\n"
        "schema_version: 1\n"
        f"id: {task_id}\n"
        "project: demo\n"
        f"{comp_line}"
        "title: Completed sample\n"
        "tier: flash-high\n"
        'input_revision: "0000000"\n'
        "source: {kind: roadmap, ref: docs/ROADMAP.md}\n"
        "scope:\n"
        f"  touch: [\"src/demo/{task_id}.py\"]\n"
        "oracles:\n"
        f"{oracle_lines}"
        "gates: [pytest-q]\n"
        'escalate_if: ["a named contract cannot be met as specified"]\n'
        "---\n\n# Completed sample\nBody.\n"
    )
    rel = f"handoff/{task_id}.md"
    (root / rel).write_text(text, encoding="utf-8")
    # Committed immediately (on whatever branch is currently checked out --
    # every call site calls this right after a merge, so that's `main`) so
    # it can never be picked up as a stray untracked file by a LATER
    # `git add -A` on some other feature branch (which would otherwise
    # falsely leak an unrelated task's handoff into that branch's diff).
    subprocess.run(["git", "-C", str(root), "add", rel], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root),
                    "commit", "-qm", f"handoff {task_id}"], check=True, capture_output=True)
    return rel


def _seed_completed_task(project: str, task_id: str, handoff_path: str | None,
                          merge_commit: str | None, since=None) -> None:
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.COMPLETED, since=since or utc_now(), handoff_path=handoff_path,
        merge_commit=merge_commit,
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )


class _CapturingLog:
    """Local twin of test_daemon.py's own capturing-log seam (immune to the
    global-logger/file race -- proving the log module persists a record to
    file end-to-end is test_log.py's job; this only proves the emission)."""

    def __init__(self, real):
        self._real = real
        self.calls: list[tuple[str, dict]] = []

    def info(self, msg, **kw):
        self.calls.append((msg, dict(kw)))
        return self._real.info(msg, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ==========================================================================
# Oracle 5: bounded sampling, dropped tasks visible (never silently
# truncated) -- _sample_verdict_audit_tasks
# ==========================================================================

def test_oracle_5_bounded_sampling_most_recent_first(monkeypatch):
    """More COMPLETED tasks than the bound -> at most the bound is sampled,
    most-recent-first, and the dropped ids are LOGGED (never silently
    truncated)."""
    d = daemon.Daemon({})
    now = utc_now()
    states = {}
    for i in range(5):
        tid = f"demo-P{i}"
        states[tid] = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=tid, project="demo",
            state=TaskState.COMPLETED, since=now - timedelta(days=i), handoff_path=None,
        )
    capturing = _CapturingLog(daemon.log)
    monkeypatch.setattr(daemon, "log", capturing)

    sampled = d._sample_verdict_audit_tasks(states, sample_size=2)

    assert [t.task_id for t in sampled] == ["demo-P0", "demo-P1"]  # most recent first
    dropped_calls = [kw for msg, kw in capturing.calls if msg == "verdict-audit-sample-truncated"]
    assert len(dropped_calls) == 1
    assert sorted(dropped_calls[0]["dropped_ids"]) == ["demo-P2", "demo-P3", "demo-P4"]
    assert dropped_calls[0]["dropped_count"] == 3


def test_oracle_5_negative_no_truncation_no_log(monkeypatch):
    """Fewer COMPLETED tasks than the bound -> ALL sampled, nothing dropped,
    no truncation log emitted (the negative for oracle 5)."""
    d = daemon.Daemon({})
    now = utc_now()
    states = {
        "demo-P0": TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="demo-P0", project="demo",
            state=TaskState.COMPLETED, since=now, handoff_path=None),
    }
    capturing = _CapturingLog(daemon.log)
    monkeypatch.setattr(daemon, "log", capturing)

    sampled = d._sample_verdict_audit_tasks(states, sample_size=5)

    assert [t.task_id for t in sampled] == ["demo-P0"]
    assert not any(msg == "verdict-audit-sample-truncated" for msg, _kw in capturing.calls)


def test_sample_excludes_non_completed_tasks():
    """Only TaskState.COMPLETED is eligible -- an ACTIVE/QUEUED/etc. task
    never enters the sample regardless of the bound."""
    d = daemon.Daemon({})
    now = utc_now()
    states = {
        "demo-P0": TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="demo-P0", project="demo",
            state=TaskState.COMPLETED, since=now, handoff_path=None),
        "demo-P1": TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="demo-P1", project="demo",
            state=TaskState.ACTIVE, since=now, handoff_path=None),
    }
    sampled = d._sample_verdict_audit_tasks(states, sample_size=5)
    assert [t.task_id for t in sampled] == ["demo-P0"]


# ==========================================================================
# _task_final_diff: the None/empty-string distinction (hard constraint --
# an unresolvable diff must never be confused with a genuinely empty one)
# ==========================================================================

def test_task_final_diff_none_when_no_merge_commit(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P50", project="demo",
        state=TaskState.COMPLETED, since=utc_now(), handoff_path=None, merge_commit=None,
    )
    assert d._task_final_diff(sample_project, tsf) is None


def test_task_final_diff_none_when_git_fails(tmp_state, sample_project):
    """An unresolvable merge_commit (bad sha) -> git fails -> None, DISTINCT
    from a real empty diff (see the positive-empty-diff test below)."""
    d = daemon.Daemon({"demo": sample_project.root})
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P50", project="demo",
        state=TaskState.COMPLETED, since=utc_now(), handoff_path=None,
        merge_commit="deadbeef" * 5,
    )
    assert d._task_final_diff(sample_project, tsf) is None


def test_task_final_diff_real_content(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    _make_feature_branch(sample_project.root, "demo-P51", "src_p51.py", "# p51 content\n")
    merge_sha = _merge_feature_branch(sample_project.root, "demo-P51")
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P51", project="demo",
        state=TaskState.COMPLETED, since=utc_now(), handoff_path=None, merge_commit=merge_sha,
    )
    diff = d._task_final_diff(sample_project, tsf)
    assert diff is not None
    assert "src_p51.py" in diff
    assert "p51 content" in diff


def test_task_final_diff_genuinely_empty_is_not_none(tmp_state, sample_project):
    """A real merge that introduces NO tree changes (an --allow-empty commit
    merged in) yields "" -- distinct from the None 'could not resolve' case
    above. Collapsing the two would render an honest no-op merge as if the
    diff were simply unavailable."""
    d = daemon.Daemon({"demo": sample_project.root})
    _make_feature_branch(sample_project.root, "demo-P52", "src_p52.py", "unused\n")
    # discard the file commit's content by resetting the branch to main's tree
    # before merging, so the merge introduces genuinely nothing:
    subprocess.run(["git", "-C", str(sample_project.root), "checkout", "feat/demo-P52"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sample_project.root), "reset", "--hard", "main"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sample_project.root), "checkout", "main"],
                    check=True, capture_output=True)
    merge_sha = _merge_feature_branch(sample_project.root, "demo-P52", allow_empty=True)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P52", project="demo",
        state=TaskState.COMPLETED, since=utc_now(), handoff_path=None, merge_commit=merge_sha,
    )
    diff = d._task_final_diff(sample_project, tsf)
    assert diff == ""
    assert diff is not None


def test_empty_diff_renders_its_own_message_not_the_unavailable_one(
        tmp_state, sample_project):
    """The helper's None/"" split has to survive all the way into the packet
    TEXT, which is the only thing the auditor ever sees.

    "unavailable" means we could not resolve the merged diff -- the auditor
    has no evidence and should say so. "empty" means we resolved it fine and
    the merge genuinely changed nothing -- which is itself a finding, and a
    suspicious one for a task recorded APPROVED. Rendering both the same way
    would hand the auditor "no information" when we actually know something
    specific. The helper-level split is asserted directly above; this asserts
    the RENDERER honours it, because a correct helper feeding a renderer that
    collapses the two is indistinguishable from a broken helper."""
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5

    _make_feature_branch(cfg.root, "demo-P70", "p70.py", "unused\n")
    # Reset the branch to main's tree so the merge introduces genuinely
    # nothing (same technique as the helper test above).
    for args in (["checkout", "feat/demo-P70"], ["reset", "--hard", "main"],
                 ["checkout", "main"]):
        subprocess.run(["git", "-C", str(cfg.root), *args],
                       check=True, capture_output=True)
    merge_sha = _merge_feature_branch(cfg.root, "demo-P70", allow_empty=True)
    h = _write_completed_handoff(cfg.root, "demo-P70", component="worker")
    _seed_completed_task("demo", "demo-P70", h, merge_sha, since=utc_now())

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    body = "\n".join(d._carve_packet_body_lines(
        cfg, "demo", seq=1, states=states, kind="gap-audit"))

    assert "Diff: (empty -- merge introduced no changes)" in body
    # The negative: the two degrade renderings must not be conflated.
    assert "Diff: (unavailable" not in body


# ==========================================================================
# Oracle 4: component grouping is real (positive + missing-component negative)
# ==========================================================================

def test_oracle_4_component_grouping(tmp_state, sample_project):
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5

    _make_feature_branch(cfg.root, "demo-P60", "p60.py", "# p60\n")
    m1 = _merge_feature_branch(cfg.root, "demo-P60")
    h1 = _write_completed_handoff(cfg.root, "demo-P60", component="worker")
    _seed_completed_task("demo", "demo-P60", h1, m1, since=utc_now())

    _make_feature_branch(cfg.root, "demo-P61", "p61.py", "# p61\n")
    m2 = _merge_feature_branch(cfg.root, "demo-P61")
    h2 = _write_completed_handoff(cfg.root, "demo-P61", component="ui")
    _seed_completed_task("demo", "demo-P61", h2, m2, since=utc_now() - timedelta(minutes=1))

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states=states, kind="gap-audit")
    body = "\n".join(lines)

    assert "### Component: worker" in body
    assert "### Component: ui" in body
    # Bound to the verdict-audit section itself first ('## Current queue'
    # is the NEXT top-level section _carve_packet_body_lines always emits,
    # pre-GAP2, and legitimately mentions every task id again). Within that
    # bounded span, sections are sorted alphabetically -- "ui" precedes
    # "worker" -- so each task's own component section is bounded by the
    # NEXT "### Component:" header (or the verdict-audit section's own
    # end), proving it appears under its OWN component and nowhere else.
    verdict_section = body[body.index("## Verdict audit"):body.index("## Current queue")]
    ui_section = verdict_section[verdict_section.index("### Component: ui"):
                                  verdict_section.index("### Component: worker")]
    worker_section = verdict_section[verdict_section.index("### Component: worker"):]
    assert "demo-P61" in ui_section and "demo-P60" not in ui_section
    assert "demo-P60" in worker_section and "demo-P61" not in worker_section


def test_oracle_4_negative_missing_component_degrades(tmp_state, sample_project):
    """A completed task whose handoff has NO `component` still audits --
    grouped under '(uncategorized)', never crashing or being dropped."""
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5

    _make_feature_branch(cfg.root, "demo-P62", "p62.py", "# p62\n")
    m1 = _merge_feature_branch(cfg.root, "demo-P62")
    h1 = _write_completed_handoff(cfg.root, "demo-P62", component=None)
    _seed_completed_task("demo", "demo-P62", h1, m1)

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states=states, kind="gap-audit")
    body = "\n".join(lines)

    assert "### Component: (uncategorized)" in body
    assert "demo-P62" in body


def test_missing_handoff_still_audits_never_crashes(tmp_state, sample_project):
    """A COMPLETED task whose handoff_path points nowhere (unparsable/
    missing) still appears in the audit -- grouped uncategorized, oracles
    rendered as unavailable -- rather than raising or being silently
    dropped."""
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5
    _seed_completed_task("demo", "demo-P63", "handoff/does-not-exist.md", None)

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states=states, kind="gap-audit")
    body = "\n".join(lines)

    assert "### Component: (uncategorized)" in body
    assert "demo-P63" in body
    assert "Oracles: (handoff unavailable" in body
    assert "Diff: (unavailable" in body


def test_no_completed_tasks_yields_explicit_note(tmp_state, sample_project):
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5
    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="gap-audit")
    body = "\n".join(lines)
    assert "No COMPLETED tasks are available to sample this pass." in body


# ==========================================================================
# Oracle 3: genuinely blind -- assert on the PROMPT STRING, mirroring the
# D-R3 escalation-note non-anchoring precedent (assert absence, not an
# instruction to ignore).
# ==========================================================================

def test_oracle_3_prompt_never_contains_recorded_verdict_or_rationale(tmp_state, sample_project):
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5

    _make_feature_branch(cfg.root, "demo-P70", "p70.py", "# p70\n")
    _commit_review_report(
        cfg.root, "demo-P70", cfg.reports_dir,
        "# Review\n\nSpecific finding: flagrant-marker-9f2a3c in the retry cap.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: architectural\n",
    )
    merge_sha = _merge_feature_branch(cfg.root, "demo-P70")
    h1 = _write_completed_handoff(cfg.root, "demo-P70", component="worker")
    _seed_completed_task("demo", "demo-P70", h1, merge_sha)

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states=states, kind="gap-audit")
    body = "\n".join(lines)

    assert "demo-P70" in body  # sanity: the task IS in the packet
    assert "flagrant-marker-9f2a3c" not in body   # the reviewer's specific finding
    assert "VERDICT:" not in body                 # the recorded verdict line
    assert "REJECT_CLASS:" not in body             # the reject class line
    assert "architectural" not in body             # the reject class VALUE


def test_oracle_3_output_contract_never_mentions_recorded_fields(tmp_state, sample_project):
    """Companion to the prompt-string oracle above, at the full packet
    (contract tail included) level -- _build_carve_packet, not just the
    body lines."""
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5
    packet = daemon.Daemon({"demo": cfg.root})._build_carve_packet(
        cfg, "demo", seq=1, states={}, kind="gap-audit")
    assert '"verdict_audit"' in packet
    assert "REJECT_CLASS" not in packet
    assert "VERDICT:" not in packet


# ==========================================================================
# Oracle 6: disabled is byte-identical to GAP1's current output
# ==========================================================================

def test_oracle_6_disabled_no_verdict_audit_section(tmp_state, sample_project):
    """verdict_audit_sample_size == 0 (the default) -> the gap-audit packet
    carries NO 'Verdict audit' section and no `verdict_audit` contract
    field -- byte-identical to GAP1's pre-GAP2 output. The unmodified
    test_gap_audit.py suite passing unchanged after this package is the
    other half of this proof (a real regression snapshot)."""
    cfg = sample_project
    assert cfg.policy.verdict_audit_sample_size == 0  # ships disabled

    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="gap-audit")
    body = "\n".join(lines)
    assert "Verdict audit" not in body

    packet = d._build_carve_packet(cfg, "demo", seq=1, states={}, kind="gap-audit")
    assert "verdict_audit" not in packet


def test_oracle_6_disabled_even_with_completed_tasks_present(tmp_state, sample_project):
    """The knob, not the mere presence of COMPLETED tasks, gates the
    section -- a project with real completed work but the knob at 0 still
    emits the byte-identical (no verdict-audit) packet."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P71", "p71.py", "# p71\n")
    merge_sha = _merge_feature_branch(cfg.root, "demo-P71")
    h1 = _write_completed_handoff(cfg.root, "demo-P71", component="worker")
    _seed_completed_task("demo", "demo-P71", h1, merge_sha)

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states=states, kind="gap-audit")
    body = "\n".join(lines)
    assert "Verdict audit" not in body
    # The pre-existing (pre-GAP2) '## Current queue' section legitimately
    # lists every task's id+state regardless of kind/knob -- that is NOT
    # verdict-audit content. What must be absent is verdict-audit's OWN
    # per-task representation (its distinctive '#### Task <id>' heading).
    assert "- demo-P71: COMPLETED" in body   # sanity: the pre-existing queue line IS there
    assert "#### Task demo-P71" not in body  # the verdict-audit section is NOT


def test_verdict_audit_is_gap_audit_only_negative(tmp_state, sample_project):
    """The extension is scoped to kind == 'gap-audit' only -- a test-health
    packet with the knob ON still carries no verdict-audit section (the
    two carve kinds read entirely different sources)."""
    cfg = sample_project
    cfg.policy.verdict_audit_sample_size = 5
    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="test-health")
    body = "\n".join(lines)
    assert "Verdict audit" not in body


# ==========================================================================
# Oracles 1 & 2: the comparison -- daemon-side, disagreement vs agreement
# ==========================================================================

def test_oracle_1_disagreement_yields_disputed_candidate(tmp_state, sample_project):
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P80", "p80.py", "# p80\n")
    _record_review("demo", "demo-P80", "approved")

    d = daemon.Daemon({"demo": cfg.root})
    disputes = d._verdict_audit_disputes(
        "demo", [{"task_id": "demo-P80", "judgment": "REJECTED", "rationale": "looks unsafe"}])

    assert len(disputes) == 1
    assert disputes[0]["task_id"] == "demo-P80"
    assert disputes[0]["blind_judgment"] == "REJECTED"
    assert disputes[0]["recorded_verdict"] == "APPROVED"


def test_oracle_2_agreement_produces_nothing(tmp_state, sample_project):
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P81", "p81.py", "# p81\n")
    _record_review("demo", "demo-P81", "approved")

    d = daemon.Daemon({"demo": cfg.root})
    disputes = d._verdict_audit_disputes(
        "demo", [{"task_id": "demo-P81", "judgment": "APPROVED", "rationale": "looks fine"}])

    assert disputes == []


def test_oracle_2_agreement_on_rejected_also_produces_nothing(tmp_state, sample_project):
    """Agreement is symmetric -- a blind REJECTED matching a recorded
    REJECTED is also a non-event, not just the APPROVED case."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P82", "p82.py", "# p82\n")
    _record_review("demo", "demo-P82", "rejected")

    d = daemon.Daemon({"demo": cfg.root})
    disputes = d._verdict_audit_disputes(
        "demo", [{"task_id": "demo-P82", "judgment": "REJECTED", "rationale": "confirmed unsafe"}])

    assert disputes == []


def test_verdict_audit_disputes_skips_malformed_entries(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _record_review("demo", "demo-P83", "approved")
    disputes = d._verdict_audit_disputes("demo", [
        {"task_id": "", "judgment": "REJECTED"},          # no task_id
        {"task_id": "demo-P83", "judgment": "MAYBE"},      # not a valid judgment
        # CR-03: a task with NO recorded review is skipped too -- there is
        # nothing to dispute, and inventing a verdict for it would
        # manufacture a carve candidate out of an absence.
        {"task_id": "demo-P84", "judgment": "REJECTED"},
    ])
    assert disputes == []


# ==========================================================================
# End-to-end: the CARVE_OUTCOME envelope actually carries the disputes,
# and stays byte-identical to pre-GAP2 shape when verdict_audit is empty.
# ==========================================================================

def _scripted(monkeypatch, sequence):
    seq = list(sequence)

    def fake(inp):
        return seq.pop(0) if seq else []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _seed_carve_task(project: str, seq: int, worktree: Path) -> tuple[str, str]:
    task_id = f"carve-{project}-{seq}"
    attempt_id = f"att-carve-{seq}"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), worktree=str(worktree))
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return task_id, attempt_id


def _write_carve_report(root: Path, reports_dir: str, seq: int, summary: dict) -> None:
    d = root / reports_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"CARVE-{seq}.md").write_text(json.dumps(summary), encoding="utf-8")


def _write_receipt(project: str, attempt_id: str) -> None:
    d = paths.attempt_dir(project, attempt_id)
    d.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=ReceiptResult.DONE, exit_code=0)
    (d / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")


def test_end_to_end_dispute_reaches_carve_outcome_payload(tmp_state, sample_project, monkeypatch):
    """The full loop: a carve report's `verdict_audit` field is consumed by
    _consume_carve_exit, the daemon compares it against the REAL recorded
    verdict, and a DISPUTED entry lands on the CARVE_OUTCOME event -- the
    SAME envelope GAP1's carve-consumption already emits, never a new event
    type or a second dispatch."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project

    _make_feature_branch(cfg.root, "demo-P90", "p90.py", "# p90\n")
    _record_review("demo", "demo-P90", "approved")

    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, {
        "carved": [], "review_reflection": "", "headroom_estimate": 8,
        "headroom_rationale": "", "outcome": "CANDIDATES_READY",
        "verdict_audit": [
            {"task_id": "demo-P90", "judgment": "REJECTED", "rationale": "unsafe"},
        ],
    })
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    outcome_ev = next(e for e in events if e.type is EventType.CARVE_OUTCOME)
    disputes = outcome_ev.payload.get("verdict_audit_disputes")
    assert disputes == [{
        "task_id": "demo-P90", "blind_judgment": "REJECTED", "recorded_verdict": "APPROVED",
    }]


def test_end_to_end_empty_verdict_audit_is_byte_identical_payload(
        tmp_state, sample_project, monkeypatch):
    """The negative half of oracle 6, at the CARVE_OUTCOME envelope level:
    a report with no `verdict_audit` (every pre-GAP2 report, and every
    GAP2 report with the sampling knob off) produces the EXACT pre-GAP2
    payload shape -- no `verdict_audit_disputes` key at all."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, {
        "carved": [{"id": "demo-P91-new", "why": "x", "source_kind": "review"}],
        "review_reflection": "", "headroom_estimate": 8,
        "headroom_rationale": "", "outcome": "CANDIDATES_READY",
    })
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    outcome_ev = next(e for e in events if e.type is EventType.CARVE_OUTCOME)
    assert outcome_ev.payload == {
        "seq": 1, "carved_ids": ["demo-P91-new"], "outcome": "CANDIDATES_READY",
        "headroom_estimate": 8,
    }
