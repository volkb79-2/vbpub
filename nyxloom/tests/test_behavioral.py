"""F0 behavioral harness: drives the REAL daemon reconcile loop (Daemon.
run_pass) against a scriptable fake CLI (nyxloom.testing) over MANY cycles,
asserting CONTRACT INVARIANTS a single-pass unit test with a trivial fake
cannot catch. Regression anchor for the class of bugs that shipped despite
a green ~610-test unit suite:
  (a) REVIEW_REJECTED stranded (no reconcile handler at all);
  (b) a notification stormed every reconcile cycle for a persistent
      condition (review_rejections_by_area never deduped);
  (c) a reviewer's MISNAMED review file (P42-REVIEW.md, not
      <task_id>-REVIEW.md) falsely rejected a genuinely-APPROVED task;
  (d) an illegal transition left a spurious event in the log.

See src/nyxloom/testing.py for the fake's scripting surface (FakeStep/
FakeScript/fake_cli_main) and its module docstring for WHY scripting
happens by file (not argv) and what the fake CLI can actually control.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from nyxloom import daemon, paths, storage
from nyxloom.config import ProjectConfig
from nyxloom.testing import FakeScript, FakeStep
from nyxloom.types import (
    AttemptState, BlockerType, EventType, Role, TaskState,
)

TASK_ID = "demo-P01-sample"
TASK_ID_2 = "demo-P02-second"

BEHAVIORAL_PROJECT_TOML = """\
[project]
id = "demo"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]
infra_globs = ["infra/**"]

[gates.pytest-q]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60
environment = "local"

[mutexes.stack]
scope = "project"
capacity = 1

[policy]
max_active_tasks = 2
ready_queue_target = 3
max_attempts_per_task = 20
wave_max_diffs = 1
carve_ahead_target = 0

[notify]
"""

# Two fake ROUTES (not one shared "fake-cli" like other tests use) so the
# fake CLI's argv literally carries which ROLE dispatched it -- see
# nyxloom.testing's module docstring. `{task_id}` is a real build_dispatch
# placeholder (adapters.render_argv); `--role` is a fixed literal per route,
# a pure test convention with no meaning to production code.
BEHAVIORAL_ROUTES_TOML = """\
revision = "test-behavioral"

[tiers.flash-high]
routes = ["fake-impl"]

[tiers.frontier-review]
routes = ["fake-review"]

[routes.fake-impl]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
dispatch_extra = ["--role", "implementer", "--task", "{task_id}"]

[routes.fake-review]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
dispatch_extra = ["--role", "review", "--task", "{task_id}"]
"""


# lint's L11/L12 rules (src/nyxloom/lint.py) require the BODY (not just
# frontmatter) to mention a worktree path, a branch name, an out-of-scope/
# forbid reference, a context section, AND a `BLOCKED:` marker -- conftest.
# py's own SAMPLE_HANDOFF body ("Contract body. If a named contract cannot
# be met...") does NOT satisfy L11 (no worktree/branch/out-of-scope
# mentions), so it never reaches lint_clean=True on its own; tests that
# need CARVED->QUEUED (like test_integration.py's CLEAN_HANDOFF) always
# overwrite the handoff with a body like this one. Mirrored here for both
# task ids.
def _clean_handoff(task_id: str, touch: list[str]) -> str:
    touch_yaml = ", ".join(f'"{t}"' for t in touch)
    return f"""\
---
schema_version: 1
id: {task_id}
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "0000000"
source: {{kind: roadmap, ref: docs/ROADMAP.md}}
scope:
  touch: [{touch_yaml}]
  forbid: ["src/demo/core.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Work in the worktree `.worktrees/feat/{task_id}` on branch
`feat/{task_id}`. Touch only the scope files; `src/demo/core.py` is out of
scope (forbid list).

## Context to read first
- docs/ROADMAP.md

## Rules
If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.
"""


# ---------------------------------------------------------------------------
# fixtures

@pytest.fixture()
def behavioral_project(sample_project, tmp_path) -> ProjectConfig:
    """sample_project (conftest.py) upgraded: lint-clean referenced paths
    (docs/ROADMAP.md, the forbidden src/demo/core.py -- both required by
    lint's L7 path-resolution rule), policy bumped for fast multi-cycle
    testing (wave_max_diffs=1 so a single AWAITING_REVIEW task opens a wave
    immediately; max_attempts_per_task=20 so a reject-loop doesn't exhaust
    its budget mid-test; carve_ahead_target=0 disables carve-dispatch noise
    entirely), and two fake routes tagged by role (see BEHAVIORAL_ROUTES_TOML
    above)."""
    cfg = sample_project
    (cfg.root / "docs" / "ROADMAP.md").write_text("# Roadmap\n- R1 sample\n")
    (cfg.root / "src" / "demo").mkdir(parents=True, exist_ok=True)
    (cfg.root / "src" / "demo" / "core.py").write_text("# frozen\n")
    (cfg.root / "handoff" / "demo-P01-sample.md").write_text(
        _clean_handoff(TASK_ID, ["src/demo/thing.py", "tests/test_thing.py"])
    )
    (cfg.root / ".nyxloom" / "project.toml").write_text(BEHAVIORAL_PROJECT_TOML)
    paths.routes_path().write_text(BEHAVIORAL_ROUTES_TOML)
    return ProjectConfig.load(cfg.root)


@pytest.fixture()
def two_task_project(behavioral_project) -> ProjectConfig:
    """behavioral_project plus a SECOND independent handoff (its own task
    id), for the bounded-notifications oracle -- which needs >= 2 REJECTED
    review verdicts. A single task cannot supply a second one today (see
    test_second_review_cycle_never_relaunches_stale_wave_id below: a real,
    separately-documented gap blocks any task from being reviewed twice),
    so this fixture sidesteps it with two independent single-cycle tasks
    instead of one task cycled twice."""
    cfg = behavioral_project
    (cfg.root / "handoff" / "demo-P02-second.md").write_text(
        _clean_handoff(TASK_ID_2, ["src/demo/thing2.py"])
    )
    return cfg


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch) -> FakeScript:
    """Puts a scriptable `fake` executable on PATH (a thin shim importing
    nyxloom.testing.fake_cli_main) and points NYXLOOM_FAKE_SCRIPT at a
    fresh JSON script file. PYTHONPATH is set to THIS checkout's absolute
    src/ dir (not the gate's relative `PYTHONPATH=src`) so the shim can
    `import nyxloom.testing` regardless of the dispatched subprocess's cwd
    (a task worktree or cfg.root, neither of which is nyxloom's own repo).

    Local to this test file (not tests/conftest.py, which is explicitly
    documented FROZEN: 'implementation agents add local fixtures in their
    own test files, never here') -- extends the shared fixture set without
    touching or risking the frozen file.
    """
    import nyxloom

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "fake"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from nyxloom.testing import fake_cli_main\n"
        "sys.exit(fake_cli_main(sys.argv[1:]))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ['PATH']}")

    src_dir = str(Path(nyxloom.__file__).resolve().parent.parent)
    existing_pp = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH", f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir
    )

    script_path = tmp_path / "fake_script.json"
    monkeypatch.setenv("NYXLOOM_FAKE_SCRIPT", str(script_path))
    return FakeScript(script_path)


# ---------------------------------------------------------------------------
# driving helpers

def _wait(predicate, timeout: float = 10.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def _any_receipt_pending(project: str) -> bool:
    """True while some non-terminal attempt's wrapper hasn't written its
    receipt.json yet -- the wrapper runs detached, so a further reconcile
    pass needs the receipt on disk before it can consume it."""
    for tsf in storage.list_states(project).values():
        for att in tsf.attempts:
            if att.state in (AttemptState.CREATED, AttemptState.PREFLIGHTING,
                              AttemptState.RUNNING):
                rp = paths.attempt_dir(project, att.attempt_id) / "receipt.json"
                if not rp.exists():
                    return True
    return False


def _tick(d: daemon.Daemon, project: str) -> None:
    """One real reconcile pass, then wait for any freshly dispatched
    attempt's receipt to land before the caller runs the next pass."""
    d.run_pass(project)
    _wait(lambda: not _any_receipt_pending(project))


def _worktree_for(cfg: ProjectConfig, task_id: str) -> Path:
    """Mirrors daemon.py's DispatchImplementer/_ensure_worktree convention
    (cfg.root / cfg.worktree_root / f'feat/{task_id}') -- a FRONTIER_REVIEW
    dispatch's own cwd is cfg.root (not this path, see daemon.py's
    LaunchReview execute branch), so a review step must target this
    directory explicitly to land its commit on the task's OWN feat/
    branch (which _parse_review_verdict reads via `git show
    feat/<task_id>:...`)."""
    return cfg.root / cfg.worktree_root / f"feat/{task_id}"


def _impl_commit_step(n: int = 1) -> FakeStep:
    files = {f"src/demo/thing_{i}.py": f"# impl work {i}\n" for i in range(n)}
    return FakeStep(commit_files=files)


def _review_reject_step(cfg: ProjectConfig, task_id: str,
                         reason: str = "needs more tests") -> FakeStep:
    return FakeStep(
        target_dir=str(_worktree_for(cfg, task_id)),
        review_file=f"{cfg.reports_dir}/{task_id}-REVIEW.md",
        review_verdict="REJECTED",
        review_reason=reason,
    )


def _review_approve_step(cfg: ProjectConfig, task_id: str,
                          filename: str | None = None) -> FakeStep:
    fname = filename or f"{task_id}-REVIEW.md"
    return FakeStep(
        target_dir=str(_worktree_for(cfg, task_id)),
        review_file=f"{cfg.reports_dir}/{fname}",
        review_verdict="APPROVED",
    )


def _git(cfg: ProjectConfig, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cfg.root), *args],
                          capture_output=True, text=True)
    return res.stdout.strip()


# ===========================================================================
# O1: unit smoke tests -- the fake CAN be scripted to produce EACH key
# behavior, and the daemon-VISIBLE artifact matches the script.
# ===========================================================================

def test_fake_approved_review_reaches_merge_ready(behavioral_project, tmp_state, fake_cli):
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_approve_step(cfg, TASK_ID))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(20):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.MERGE_READY

    approved = [e for e in storage.iter_events("demo")
                if e.type is EventType.REVIEW_RECORDED
                and e.payload.get("result") == "approved"]
    assert len(approved) == 1


def test_fake_rejected_review_reaches_review_rejected(behavioral_project, tmp_state, fake_cli):
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_reject_step(cfg, TASK_ID, reason="scope drift"))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(20):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.REVIEW_REJECTED

    rejected = [e for e in storage.iter_events("demo")
                if e.type is EventType.REVIEW_RECORDED
                and e.payload.get("result") == "rejected"]
    assert len(rejected) == 1


def test_fake_misnamed_review_file_still_approves(behavioral_project, tmp_state, fake_cli):
    """P33/self-correct anchor: VERDICT: APPROVED in a MISNAMED file
    (P42-REVIEW.md, not <task_id>-REVIEW.md) must still be found -- the old
    rigid single-path lookup found nothing and fail-safed a genuinely-
    approved task to REVIEW_REJECTED."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_approve_step(cfg, TASK_ID, filename="P42-REVIEW.md"))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(20):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.MERGE_READY

    ls = _git(cfg, "ls-tree", "-r", "--name-only", f"feat/{TASK_ID}")
    assert "P42-REVIEW.md" in ls
    assert f"{TASK_ID}-REVIEW.md" not in ls


def test_fake_null_commit_receipt_still_reaches_awaiting_review(behavioral_project, tmp_state, fake_cli):
    """The wrapper always writes head_commit=null itself (it never reads
    back anything the CLI wrote); the daemon's _crosscheck_head_commit only
    upgrades it to a real sha when the branch is ACTUALLY ahead of
    default_branch. Script an implementer step that commits NOTHING: the
    branch stays even with default_branch (a genuine null-head_commit
    case), yet a DONE receipt still reaches AWAITING_REVIEW today --
    documented here as observed behavior (not asserted as either
    correct or incorrect; see the REPORT)."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", FakeStep())  # no commits, exit 0

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(15):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state not in (TaskState.CARVED, TaskState.QUEUED, TaskState.ACTIVE):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.AWAITING_REVIEW

    branch_sha = _git(cfg, "rev-parse", "--verify", f"feat/{TASK_ID}")
    main_sha = _git(cfg, "rev-parse", "--verify", cfg.default_branch)
    assert branch_sha == main_sha, "no new commit should have landed (null-commit script)"


def test_fake_blocked_exit_sets_typed_contract_blocker(behavioral_project, tmp_state, fake_cli):
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer",
                    FakeStep(blocked_reason="ambiguous scope", exit_code=1))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(15):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state == TaskState.BLOCKED:
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.BLOCKED
    assert tsf.blocker is not None
    assert tsf.blocker.type is BlockerType.CONTRACT
    assert "ambiguous scope" in (tsf.blocker.detail or "")


# ===========================================================================
# O2 seed lifecycle tests: the three REQUIRED invariants.
# ===========================================================================

def test_reject_loop_requeues_never_strands(behavioral_project, tmp_state, fake_cli):
    """Regression anchor for shipped bug (a): REVIEW_REJECTED had NO
    reconcile handler at all -- a rejected task was stranded FOREVER (zero
    further planned actions, requiring a manual operator re-queue).
    Script one reject, then prove the daemon re-dispatches a FRESH
    implementer attempt on its own (real, unprompted progress) rather than
    leaving the task frozen in REVIEW_REJECTED."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_reject_step(cfg, TASK_ID))
    # Any FURTHER implementer call (the re-dispatch after reject) just
    # commits cleanly -- we only care that it happens, not its content.
    fake_cli.set_default(TASK_ID, "implementer", _impl_commit_step())

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(30):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        impl_attempts = [a for a in tsf.attempts if a.role is Role.IMPLEMENTER]
        if len(impl_attempts) >= 2:
            break

    tsf = storage.load_state("demo", TASK_ID)
    events = list(storage.iter_events("demo"))
    rejected = [e for e in events if e.type is EventType.REVIEW_RECORDED
                and e.payload.get("result") == "rejected"]
    impl_created = [e for e in events if e.type is EventType.ATTEMPT_CREATED
                     and e.payload["attempt"]["role"] == "implementer"]
    tick_errors = [e for e in events if e.type is EventType.TICK_ERROR]

    assert len(rejected) == 1, "sanity: exactly the one scripted rejection happened"
    assert len(impl_created) >= 2, (
        "the reject-loop must re-dispatch a FRESH implementer attempt -- "
        "before the fix, REVIEW_REJECTED had no handler and this would "
        "stay at 1 forever"
    )
    assert not tick_errors, f"reconcile pass raised: {[e.payload for e in tick_errors]}"
    assert tsf.state not in (TaskState.REVIEW_REJECTED, TaskState.BLOCKED), (
        f"task must have progressed past the rejection, got {tsf.state}"
    )


def test_misnamed_review_file_reaches_merge_ready_not_falsely_rejected(
    behavioral_project, tmp_state, fake_cli
):
    """Regression anchor for shipped bug (c): a live reviewer committed
    `P42-REVIEW.md` instead of the documented `<task_id>-REVIEW.md`; the
    old rigid single-path lookup found nothing and fail-safed a
    genuinely-APPROVED task to REVIEW_REJECTED. Drive the full lifecycle
    with a misnamed, approving review file and prove it reaches
    MERGE_READY."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review",
                    _review_approve_step(cfg, TASK_ID, filename="P42-REVIEW.md"))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(25):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.MERGE_READY, (
        f"a misnamed but genuinely-APPROVED review must still reach "
        f"MERGE_READY, not fail-safe to REVIEW_REJECTED; got {tsf.state}"
    )

    # Prove this genuinely exercised the misnamed-file path (not an
    # accidental pass): the file really is misnamed on the branch.
    ls = _git(cfg, "ls-tree", "-r", "--name-only", f"feat/{TASK_ID}")
    assert "P42-REVIEW.md" in ls
    assert f"{TASK_ID}-REVIEW.md" not in ls

    approved = [e for e in storage.iter_events("demo")
                if e.type is EventType.REVIEW_RECORDED
                and e.payload.get("result") == "approved"]
    assert len(approved) == 1


def test_bounded_rejection_notifications_do_not_storm(two_task_project, tmp_state, fake_cli):
    """Regression anchor for shipped bug (b): review_rejections_by_area
    counted over the WHOLE event log with no dedup, so a persistent
    rejection condition re-emitted SpecAttention('rejections') EVERY
    reconcile pass forever. Two independent tasks each get rejected once
    (review_rejections_by_area's 'unknown' bucket -- REVIEW_RECORDED never
    carries an 'area' field in production, so everything buckets there --
    crosses the >=2 threshold); then >= 10 MORE plain reconcile passes run
    while the condition persists. Before the fix this would append a new
    SPEC_ATTENTION on every one of those passes; after the fix, exactly
    one.

    (Two tasks, not one task rejected twice: a SEPARATE, documented gap --
    see test_second_review_cycle_never_relaunches_stale_wave_id below --
    means a single task cannot be reviewed a second time today, so it can
    never contribute a second REJECTED verdict on its own. Real work,
    genuinely committed to two independent branches, is what makes this a
    behavioral test rather than a hollow one.)"""
    cfg = two_task_project
    for tid in (TASK_ID, TASK_ID_2):
        fake_cli.queue(tid, "implementer", _impl_commit_step())
        fake_cli.queue(tid, "review", _review_reject_step(cfg, tid))
        fake_cli.set_default(tid, "implementer", _impl_commit_step())

    d = daemon.Daemon({"demo": cfg.root})

    def _both_rejected() -> bool:
        events = list(storage.iter_events("demo"))
        rejected_tasks = {e.task_id for e in events
                            if e.type is EventType.REVIEW_RECORDED
                            and e.payload.get("result") == "rejected"}
        return {TASK_ID, TASK_ID_2} <= rejected_tasks

    passes = 0
    while passes < 40 and not _both_rejected():
        _tick(d, "demo")
        passes += 1
    assert _both_rejected(), "both tasks must have been rejected at least once to set up the condition"

    # Now drive >= 10 MORE plain reconcile passes while the (persistent,
    # never-decreasing) rejection count sits >= 2 -- this is the storm
    # window: pre-fix, every single one of these would append a new
    # SPEC_ATTENTION('rejections').
    for _ in range(15):
        _tick(d, "demo")
        passes += 1
    assert passes >= 10

    events = list(storage.iter_events("demo"))
    rejected = [e for e in events if e.type is EventType.REVIEW_RECORDED
                and e.payload.get("result") == "rejected"]
    spec_attn = [e for e in events if e.type is EventType.SPEC_ATTENTION
                  and e.payload.get("reason") == "rejections"]

    assert len(rejected) >= 2, "the storm condition (>=2 rejections) must be genuinely live"
    assert len(spec_attn) == 1, (
        f"expected exactly ONE SpecAttention('rejections') despite "
        f"{len(rejected)} rejections across {passes} reconcile passes -- "
        f"got {len(spec_attn)} (storming if this grows with pass count)"
    )


# ===========================================================================
# Bonus: a real bug this harness discovered while building the above.
# Originally pinned as a strict xfail (regression pin for a documented,
# not-yet-fixed gap in reconcile.py). FIXED 2026-07-17 (stale-wave_id
# strand package): reconcile.py's "Check for LaunchReview for already-open
# waves" has_review_in_flight check previously scanned ALL of
# tsf.attempts for ANY FRONTIER_REVIEW attempt in state EXITED, with no
# scoping to "is this attempt still current" -- so once a task's FIRST
# review attempt exited (approved or rejected), it stayed in
# tsf.attempts forever and has_review_in_flight was permanently True.
# Combined with the reject-loop (REVIEW_REJECTED -> QUEUED -> a fresh
# implementer -> AWAITING_REVIEW a second time), that meant a second
# review could never be launched -- the task silently stranded
# AWAITING_REVIEW forever. Fixed by scoping has_review_in_flight to only
# the task's LATEST attempt (tsf.attempts[-1]): a stale EXITED review
# superseded by a fresh implementer attempt is provably no longer in
# flight. See reconcile.py's 2026-07-17 comment on that check for the
# full reasoning (including why scoping by wave_id alone does not work,
# since tsf.wave_id is never reset and REVIEW_RECORDED is a true no-op
# in storage.apply_event). No longer xfail: this now asserts the FIX,
# not just the presence of the gap.
# ===========================================================================

def test_second_review_cycle_never_relaunches_stale_wave_id(behavioral_project, tmp_state, fake_cli):
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_reject_step(cfg, TASK_ID))
    # After the reject-loop requeues, the SECOND implementer attempt fixes
    # things up and a genuinely-approving second review is queued -- this
    # should reach MERGE_READY on a correctly-functioning daemon.
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_approve_step(cfg, TASK_ID))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(40):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state == TaskState.MERGE_READY:
            break

    tsf = storage.load_state("demo", TASK_ID)
    review_recorded = [e for e in storage.iter_events("demo")
                        if e.type is EventType.REVIEW_RECORDED]
    assert len(review_recorded) >= 2, (
        "expected a SECOND review verdict (the approving one) once the "
        "reject-loop's fresh implementation lands -- a stale wave_id/"
        "has_review_in_flight check would silently strand the task "
        "AWAITING_REVIEW forever instead (never launching a second review)"
    )
    # The two REVIEW_RECORDED events must come from two genuinely DISTINCT
    # review attempts (not the same attempt re-recorded), and must show the
    # actual reject-then-approve sequence -- a positive, specific assertion
    # of the fix rather than just "two events happened".
    attempt_ids = {e.attempt_id for e in review_recorded}
    assert len(attempt_ids) >= 2, (
        "the two REVIEW_RECORDED events must belong to two distinct "
        "FRONTIER_REVIEW attempts -- a second, genuinely fresh review"
    )
    results = [e.payload.get("result") for e in review_recorded]
    assert "rejected" in results and "approved" in results, (
        f"expected a rejected verdict followed by an approved one; got {results}"
    )
    assert tsf.state == TaskState.MERGE_READY
