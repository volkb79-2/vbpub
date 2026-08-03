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

import json
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
    Actor, ActorKind, AttemptState, BlockerType, EventType, Role, TaskState,
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


@pytest.fixture()
def sync_dispatch(monkeypatch):
    """Deterministic stand-in for wrapper.launch_detached's real double
    os.fork() (wrapper.py's launch_detached), adapted from the identical
    seam `test_transient_throttle_resumes_same_attempt_end_to_end` (this
    file) built to de-flake B25: os.fork() under load on Python 3.14 in the
    tester-unified image is a known fragility (see that test's docstring
    and tester-unified/Dockerfile's note) -- the only real-fork primitive
    in this codebase. A loaded xdist worker makes the forked wrapper's
    wall-clock completion (fork + Popen + log classification + receipt
    write) arbitrarily slow, which no timeout value fixes -- it only trades
    flaky-fast for flaky-slow (AUTHORING.md Sec 3b: "if shrinking the number
    could flip the result, it is an oracle"). Removing the wait entirely
    (the doctrine's preferred fix) requires removing the fork it waits on.

    `_sync_launch` reproduces every on-disk artifact the real
    launch_detached publishes before it returns -- spec.json (written
    pre-fork) and wrapper.pid (written by the intermediate child, and the
    file daemon.py cross-checks when a recorded attempt pid looks dead,
    daemon.py's "freshest wrapper.pid file actually on disk") -- and
    records the spec instead of forking, so the daemon's own dispatch-time
    event (ATTEMPT_PREFLIGHTED/ATTEMPT_CREATED) commits first, matching
    production ordering. The returned `_tick_sync` helper then runs
    `wrapper_main` in-process, synchronously, right after `run_pass`
    returns for that same pass -- the wrapper body still does everything
    for real (real leases, a real subprocess spawn of the fake CLI, real
    log classification, real receipt/event writes; see test_wrapper.py's
    identical in-process `wrapper_main(spec_path)` pattern) -- so by the
    time `_tick_sync` returns, the receipt is already on disk. No polling,
    no timeout, and no larger iteration budget is involved anywhere in
    this synchronization.

    What it deliberately does NOT cover: the wrapper no longer runs
    CONCURRENTLY with the rest of the daemon pass, so a test driven
    through this seam cannot observe a daemon-pass/wrapper interleaving
    (the storage clobber window `test_transient_throttle_resumes_same_
    attempt_end_to_end` documents). That path stays covered by every test
    in this file still driven through `_tick`, which forks for real --
    this seam is opt-in per test, never the file's default.

    `_tick_sync.launched` records each spec the seam actually intercepted.
    A test using this fixture asserts it is non-empty: if daemon.py ever
    stopped resolving `wrapper.launch_detached` through the module (e.g.
    a `from .wrapper import launch_detached`), the patch would silently
    stop applying and the test would quietly go back to forking rather
    than reporting that its seam is dead."""
    from nyxloom import wrapper as wrapper_mod

    monkeypatch.setattr(wrapper_mod, "SESSION_CAPTURE_DELAY", 0)

    pending_specs: list[Path] = []
    launched: list[str] = []

    def _sync_launch(spec) -> int:
        attempt_dir = Path(spec.attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        pending_specs.append(spec_path)
        launched.append(spec.attempt_id)
        wrapper_pid = 4242 + len(launched)
        (attempt_dir / "wrapper.pid").write_text(str(wrapper_pid), encoding="utf-8")
        return wrapper_pid

    monkeypatch.setattr(wrapper_mod, "launch_detached", _sync_launch)

    def _tick_sync(d: daemon.Daemon, project: str) -> None:
        d.run_pass(project)
        while pending_specs:
            wrapper_mod.wrapper_main(str(pending_specs.pop(0)))

    _tick_sync.launched = launched
    return _tick_sync


# ---------------------------------------------------------------------------
# driving helpers

def _wait_for_sync_point(predicate, timeout: float = 60.0, step: float = 0.05) -> None:
    """Wait for an external test synchronization point, never a result.

    The detached wrapper communicates completion by atomically publishing its
    receipt.  A following reconcile pass is not valid until that receipt is
    present: proceeding after a timed-out poll races the next pass against the
    previous attempt and makes the fixed pass-count tests machine-speed
    dependent.  The timeout is only a generous hung-process failsafe; the
    lifecycle assertions below decide the test's result.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(step)
    raise RuntimeError("detached wrapper did not publish its receipt")


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
    _wait_for_sync_point(lambda: not _any_receipt_pending(project))


def _worktree_for(cfg: ProjectConfig, task_id: str) -> Path:
    """Mirrors daemon.py's DispatchImplementer/_ensure_worktree convention
    (cfg.root / cfg.worktree_root / f'feat/{task_id}') -- a REVIEW_INDEPENDENT
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


def test_fake_misnamed_review_file_still_approves(behavioral_project, tmp_state, fake_cli,
                                                    sync_dispatch):
    """P33/self-correct anchor: VERDICT: APPROVED in a MISNAMED file
    (P42-REVIEW.md, not <task_id>-REVIEW.md) must still be found -- the old
    rigid single-path lookup found nothing and fail-safed a genuinely-
    approved task to REVIEW_REJECTED.

    Drives dispatch through `sync_dispatch` (this file's `_tick_sync`), not
    `_tick`'s real wrapper.launch_detached double-fork: this test flaked
    under loaded xdist workers because a real os.fork()'d wrapper's
    wall-clock completion is load-dependent (see `sync_dispatch`'s
    docstring for the full root cause and the established B25 precedent).
    `_tick_sync` removes the fork this test's poll used to wait on, so its
    20-iteration budget only bounds genuine reconcile-pass progress, never
    a real subprocess's completion time.

    The verdict below is unchanged by the seam: both agent legs really run
    (implementer commit, reviewer commit of the misnamed report), the
    daemon really parses the verdict, and the assertions are the same
    lifecycle state and the same committed filenames as before."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_approve_step(cfg, TASK_ID, filename="P42-REVIEW.md"))

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(20):
        sync_dispatch(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.MERGE_READY

    # The seam must have actually intercepted both dispatches (implementer +
    # reviewer). A silently-unpatched launch_detached would fork for real and
    # re-introduce exactly the load-dependence this test removed.
    assert len(sync_dispatch.launched) >= 2, sync_dispatch.launched

    ls = _git(cfg, "ls-tree", "-r", "--name-only", f"feat/{TASK_ID}")
    assert "P42-REVIEW.md" in ls
    assert f"{TASK_ID}-REVIEW.md" not in ls


def test_fake_null_commit_receipt_still_reaches_self_review(behavioral_project, tmp_state, fake_cli):
    """The wrapper always writes head_commit=null itself (it never reads
    back anything the CLI wrote); the daemon's _crosscheck_head_commit only
    upgrades it to a real sha when the branch is ACTUALLY ahead of
    default_branch. Script an implementer step that commits NOTHING: the
    branch stays even with default_branch (a genuine null-head_commit
    case), yet a DONE receipt still PROGRESSES past ACTIVE. Under the B5
    default pipeline (self_review included) that first stop is SELF_REVIEWING
    -- the implementer's own warm self-check -- not straight to
    AWAITING_REVIEW. Documented as observed behavior (see the REPORT)."""
    cfg = behavioral_project
    fake_cli.queue(TASK_ID, "implementer", FakeStep())  # no commits, exit 0

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(15):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state not in (TaskState.CARVED, TaskState.QUEUED, TaskState.ACTIVE):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.SELF_REVIEWING

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
# B21 2026-07-23 (D-R16 §3, scope-amendment escalation). O1: an IMPLEMENTER
# whose log tail emits SCOPE_AMENDMENT_REQUEST: {...} gets a bounded,
# never-a-dead-end fresh re-dispatch (the fix for the P26/P31 forbidden-
# needed-file failure mode). O3: bounded -- past
# daemon.MAX_SCOPE_AMENDMENTS_PER_TASK approvals, a further request falls
# through to the SAME hard-BLOCK path CONTRACT-blocked receipts use above.
# Modeled on test_fake_blocked_exit_sets_typed_contract_blocker (the BLOCKED
# analog, just above) and test_reject_loop_requeues_never_strands (the >= 2
# fresh-dispatch shape, just below).
# ===========================================================================

def test_scope_amendment_request_triggers_bounded_re_dispatch_not_blocked(
    behavioral_project, tmp_state, fake_cli
):
    """O1: a SCOPE_AMENDMENT_REQUEST results in a FRESH implementer
    re-dispatch (>= 2 implementer attempts created), exactly one
    SCOPE_AMENDMENT_APPROVED event recorded for the requested file, and the
    task NEVER lands in BLOCKED -- never the P26/P31 dead end (fake a
    workaround, or hard-BLOCK) this package replaces."""
    cfg = behavioral_project
    fake_cli.queue(
        TASK_ID, "implementer",
        FakeStep(extra_stdout=(
            'SCOPE_AMENDMENT_REQUEST: {"file": "src/demo/shared_helper.py", '
            '"reason": "needs a shared helper outside scope.touch"}'
        )),
    )
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(30):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        impl_attempts = [a for a in tsf.attempts if a.role is Role.IMPLEMENTER]
        if len(impl_attempts) >= 2:
            break

    tsf = storage.load_state("demo", TASK_ID)
    events = list(storage.iter_events("demo"))
    impl_created = [e for e in events if e.type is EventType.ATTEMPT_CREATED
                     and e.payload["attempt"]["role"] == "implementer"]
    approved = [e for e in events if e.type is EventType.SCOPE_AMENDMENT_APPROVED
                and e.task_id == TASK_ID]
    tick_errors = [e for e in events if e.type is EventType.TICK_ERROR]

    assert not tick_errors, f"reconcile pass raised: {[e.payload for e in tick_errors]}"
    assert len(impl_created) >= 2, (
        "the scope-amendment escalation must re-dispatch a FRESH implementer "
        "attempt -- never a dead end"
    )
    assert len(approved) == 1
    assert approved[0].payload.get("file") == "src/demo/shared_helper.py"
    assert tsf.state != TaskState.BLOCKED, (
        f"task must never be BLOCKED by a bounded scope-amendment request, got {tsf.state}"
    )


def test_scope_amendment_bounded_falls_through_to_blocked_at_cap(
    behavioral_project, tmp_state, fake_cli
):
    """O3: after daemon.MAX_SCOPE_AMENDMENTS_PER_TASK approved amendments, a
    FURTHER SCOPE_AMENDMENT_REQUEST for the SAME task lands in TASK_BLOCKED
    with a typed CONTRACT blocker -- NOT another re-dispatch. Bounded, so the
    escalation can never loop forever (mirrors D-R8's bounded reviewer-fix
    cap)."""
    cfg = behavioral_project
    for n in range(daemon.MAX_SCOPE_AMENDMENTS_PER_TASK + 1):
        fake_cli.queue(
            TASK_ID, "implementer",
            FakeStep(extra_stdout=(
                f'SCOPE_AMENDMENT_REQUEST: {{"file": "src/demo/extra_{n}.py", '
                f'"reason": "amendment number {n}"}}'
            )),
        )

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(40):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state == TaskState.BLOCKED:
            break

    tsf = storage.load_state("demo", TASK_ID)
    events = list(storage.iter_events("demo"))
    approved = [e for e in events if e.type is EventType.SCOPE_AMENDMENT_APPROVED
                and e.task_id == TASK_ID]
    tick_errors = [e for e in events if e.type is EventType.TICK_ERROR]

    assert not tick_errors, f"reconcile pass raised: {[e.payload for e in tick_errors]}"
    assert tsf.state == TaskState.BLOCKED
    assert tsf.blocker is not None
    assert tsf.blocker.type is BlockerType.CONTRACT
    assert "scope-amendment cap" in (tsf.blocker.detail or "")
    assert len(approved) == daemon.MAX_SCOPE_AMENDMENTS_PER_TASK, (
        "the cap must stop approving at exactly MAX_SCOPE_AMENDMENTS_PER_TASK"
    )


# ===========================================================================
# B24 2026-07-23 (D-R17, transient-failure backoff-resume). O4: an
# IMPLEMENTER whose log tail carries a provider-throttle signature (idle
# timeout) gets the SAME attempt RESUMED -- never a fresh implementer
# dispatch, never BLOCKED. Modeled on
# test_scope_amendment_request_triggers_bounded_re_dispatch_not_blocked just
# above (tick loop + queued FakeStep + event assertions).
# ===========================================================================

def _diagnose_stuck_attempt(project: str, task_id: str) -> str:
    """B24 (post-review hardening, 2026-07-23): DURABLE forensic dump for
    when the interrupted-wait loop below times out -- read straight from
    disk/the event log, nothing sampled/in-memory, so a recurrence under
    the gate gives a definitive next clue instead of a bare 'never went
    INTERRUPTED'. Never raises itself (a diagnostic that crashes is worse
    than no diagnostic)."""
    lines: list[str] = []
    try:
        tsf = storage.load_state(project, task_id)
    except Exception as exc:
        return f"(diagnose failed: load_state raised {exc!r})"
    if not tsf:
        return "(no statefile at all)"
    lines.append(f"task state={tsf.state!r} attempts={len(tsf.attempts)}")
    for att in tsf.attempts:
        attempt_dir = paths.attempt_dir(project, att.attempt_id)
        receipt_path = attempt_dir / "receipt.json"
        child_pid_path = attempt_dir / "child.pid"
        wrapper_log = attempt_dir / "wrapper.log"
        attempt_log = attempt_dir / "attempt.log"
        lines.append(
            f"  attempt {att.attempt_id}: state={att.state!r} pid={att.pid!r} "
            f"receipt_on_disk={receipt_path.exists()} "
            f"child_pid_on_disk={child_pid_path.read_text().strip() if child_pid_path.exists() else None!r}"
        )
        for label, p in (("wrapper.log", wrapper_log), ("attempt.log", attempt_log)):
            if p.exists():
                try:
                    tail = p.read_text(encoding="utf-8", errors="replace")[-1000:]
                except Exception as exc:
                    tail = f"(read failed: {exc!r})"
                lines.append(f"  {label} tail: {tail!r}")
            else:
                lines.append(f"  {label}: MISSING")
    try:
        events = list(storage.iter_events(project))
    except Exception as exc:
        events = []
        lines.append(f"  (iter_events raised {exc!r})")
    lines.append(f"  total events in log: {len(events)}; last 5 types: "
                 f"{[e.type.value for e in events[-5:]]}")
    tick_errors = [e for e in events if e.type is EventType.TICK_ERROR]
    if tick_errors:
        lines.append(f"  TICK_ERROR payloads: {[e.payload for e in tick_errors]}")
    return "\n".join(lines)


def test_transient_throttle_resumes_same_attempt_end_to_end(
    behavioral_project, tmp_state, fake_cli, monkeypatch
):
    """O4: a scripted idle-timeout on the FIRST implementer leg is resumed
    as the SAME attempt (ATTEMPT_RESUMED for the same attempt_id,
    attempt.resume-1.log on disk) -- exactly one ATTEMPT_CREATED for
    role=implementer (no fresh dispatch), and the task never lands BLOCKED.

    BEHAVIORAL_ROUTES_TOML's fake-impl route has no `resume` template (no
    existing behavioral test exercises resume), so this test adds one via
    an in-memory Routes object + a config.Routes.load monkeypatch (NOT a
    second on-disk routes.toml rewrite -- post-review hardening,
    2026-07-23: a mid-test file rewrite was a needless extra moving part
    with no offsetting benefit, since every route lookup in this codebase
    goes through config.Routes.load(), never a direct file read).
    build_resume's own mapping only supports {session}/{worktree}/{prompt}
    (no {task_id}), so the literal TASK_ID is baked into the template, same
    as any fixed-task test route. The fake CLI also has no real session-
    capture wiring (no session_capture/session_discover on this test route,
    see adapters.capture_session), so the session_handle is seeded directly
    once the attempt is observed INTERRUPTED -- mirroring
    test_mark_interrupted_and_resume's (test_daemon.py) identical manual
    seeding -- so it is the DAEMON's own backoff/resume gate under test,
    not this harness's session-capture plumbing.

    SESSION_CAPTURE_DELAY (wrapper.py, real 5s block per dispatch before the
    wrapper even starts waiting on the child) is pinned to 0 -- this is a
    REAL, unconditional wall-clock cost every dispatch in this test pays for
    nothing (capture_session always returns None for this bare "fake" cli
    route), and shrinking it shrinks the real-time window for whatever
    timing-sensitive issue a slower/busier gate container might be hitting.

    B25 2026-07-25 (de-flaking the former xfail, backlog B25): this test
    used to drive the transient leg through a REAL wrapper.launch_detached
    double os.fork() (wrapper.py:146,150) -- the only real-fork primitive
    in this codebase, and os.fork() under load on Python 3.14 in the
    tester-unified image is a known fragility (see the retired xfail's own
    diagnosis and tester-unified/Dockerfile's note). Replaced with
    `_sync_launch`, a synchronous stand-in adapted from test_daemon.py's
    `fake_launch_detached`: write spec.json + return a fake pid -- EXACTLY
    what the real launch_detached does before it forks -- so the daemon's
    own dispatch-time bookkeeping (the ATTEMPT_PREFLIGHTED / ATTEMPT_RESUMED
    append right after `wrapper.launch_detached(spec)` returns in daemon.py)
    still commits FIRST, unchanged from production ordering.

    This ordering is load-bearing, not cosmetic: storage.apply_event's
    monotonic-regression guard (storage.py's "Monotonic guard" comment,
    _attempt_regression) only protects a CALLER's stale in-memory `states`
    object from regressing an attempt that CALLER already observed
    further along -- it does nothing for a *different* writer's fresher
    disk state the caller never loaded. CREATED and PREFLIGHTING are both
    in storage._EARLY_ATTEMPT_STATES, so a naive "run wrapper_main inline,
    then let the daemon's own subsequent ATTEMPT_PREFLIGHTED append save
    its stale in-memory copy" would NOT be flagged as a regression -- it
    would silently clobber the wrapper's fresher on-disk INTERRUPTED+receipt
    with the daemon's stale PREFLIGHTED snapshot. `_tick_sync` below avoids
    this by running wrapper_main only AFTER `d.run_pass` has already
    returned (i.e., after the daemon's own bookkeeping for that pass is on
    disk) -- fully sequential, so there is nothing left to race.

    The wrapper body itself still runs for REAL (real leases, a real
    subprocess spawn of the fake CLI, real log classification, real
    receipt/event writes) -- see test_wrapper.py's identical in-process
    `wrapper_main(spec_path)` pattern at
    test_transient_classification_is_interrupted_not_exited -- just driven
    synchronously instead of via a detached grandchild process. By the time
    `_tick_sync` returns, the receipt is already on disk; no `_wait`/polling
    is needed at all for this test's dispatch/resume legs."""
    from nyxloom import config as config_mod
    from nyxloom import wrapper as wrapper_mod

    monkeypatch.setattr(wrapper_mod, "SESSION_CAPTURE_DELAY", 0)

    # Real-fork replacement seam (see docstring above): pending_specs
    # records each attempt spec.json launch_detached would have forked for,
    # in dispatch order; _tick_sync drains it (running wrapper_main inline)
    # only once the SAME reconcile pass's own daemon-side event has landed.
    pending_specs: list[Path] = []

    def _sync_launch(spec: "wrapper_mod.WrapperSpec") -> int:
        attempt_dir = Path(spec.attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        pending_specs.append(spec_path)
        return 4242 + len(pending_specs)

    monkeypatch.setattr(wrapper_mod, "launch_detached", _sync_launch)

    def _tick_sync(d: daemon.Daemon, project: str) -> None:
        d.run_pass(project)
        while pending_specs:
            wrapper_mod.wrapper_main(str(pending_specs.pop(0)))

    cfg = behavioral_project
    resume_template = ["fake", "--role", "implementer", "--task", TASK_ID, "{prompt}"]
    custom_routes = config_mod.Routes(
        revision="test-behavioral-resume",
        tiers={"flash-high": ["fake-impl"], "review-independent": ["fake-review"]},
        routes={
            "fake-impl": config_mod.RouteDef(
                route_id="fake-impl", cli="fake", model="fake-model",
                probe=["true"], usage_source="none",
                dispatch_extra=["--role", "implementer", "--task", "{task_id}"],
                resume=resume_template,
            ),
            "fake-review": config_mod.RouteDef(
                route_id="fake-review", cli="fake", model="fake-model",
                probe=["true"], usage_source="none",
                dispatch_extra=["--role", "review", "--task", "{task_id}"],
            ),
        },
    )
    monkeypatch.setattr(config_mod.Routes, "load",
                        classmethod(lambda cls, path=None: custom_routes))

    fake_cli.queue(
        TASK_ID, "implementer",
        FakeStep(extra_stdout="Upstream idle timeout after 90s, connection reset",
                 exit_code=1),
    )
    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())

    d = daemon.Daemon({"demo": cfg.root})

    # A fresh handoff needs several ticks just to go CARVED->QUEUED->ACTIVE
    # (dispatch) before the fake CLI ever runs at all (see e.g.
    # test_scope_amendment_request_triggers_bounded_re_dispatch_not_blocked's
    # identical `for _ in range(30)` warm-up above) -- once dispatch actually
    # fires, `_tick_sync` drives the WHOLE transient leg (receipt.json +
    # ATTEMPT_INTERRUPTED) to completion synchronously within that SAME
    # call, so this loop typically breaks on the very tick dispatch happens.
    def _went_interrupted() -> bool:
        tsf = storage.load_state("demo", TASK_ID)
        return bool(tsf and tsf.attempts
                    and tsf.attempts[-1].state == AttemptState.INTERRUPTED)

    for _ in range(30):
        _tick_sync(d, "demo")
        if _went_interrupted():
            break

    assert _went_interrupted(), (
        "attempt never went INTERRUPTED\n" + _diagnose_stuck_attempt("demo", TASK_ID)
    )

    tsf = storage.load_state("demo", TASK_ID)
    attempt_id = tsf.attempts[-1].attempt_id
    # THE oracle, checked before doing anything else: TRANSIENT, INTERRUPTED
    # -- never EXITED (an EXITED attempt could never be revived).
    att = tsf.attempt_by_id(attempt_id)
    assert att.receipt is not None and att.receipt.result.value == "transient", (
        f"receipt={att.receipt!r}\n" + _diagnose_stuck_attempt("demo", TASK_ID)
    )
    assert att.state is AttemptState.INTERRUPTED

    att.session_handle = "sess-fake-1"
    storage.save_state(tsf)

    for _ in range(20):
        _tick_sync(d, "demo")
        events = list(storage.iter_events("demo"))
        if any(e.type is EventType.ATTEMPT_RESUMED and e.attempt_id == attempt_id
               for e in events):
            break

    tsf = storage.load_state("demo", TASK_ID)
    events = list(storage.iter_events("demo"))
    resumed = [e for e in events
               if e.type is EventType.ATTEMPT_RESUMED and e.attempt_id == attempt_id]
    impl_created = [e for e in events if e.type is EventType.ATTEMPT_CREATED
                     and e.payload["attempt"]["role"] == "implementer"]
    tick_errors = [e for e in events if e.type is EventType.TICK_ERROR]

    diag = lambda: _diagnose_stuck_attempt("demo", TASK_ID)  # noqa: E731
    assert not tick_errors, f"reconcile pass raised: {[e.payload for e in tick_errors]}"
    assert len(resumed) == 1, f"the SAME attempt must be resumed exactly once\n{diag()}"
    attempt_dir = paths.attempt_dir("demo", attempt_id)
    assert (attempt_dir / "attempt.resume-1.log").exists(), diag()
    assert len(impl_created) == 1, (
        "no FRESH implementer dispatch -- the throttled attempt is resumed, not restarted\n"
        + diag()
    )
    assert tsf.state != TaskState.BLOCKED, (
        f"task must never be BLOCKED by a transient provider throttle, got {tsf.state}"
    )


def test_scope_amendment_helpers_degrade_gracefully_on_storage_error(
    behavioral_project, tmp_state, monkeypatch
):
    """Defensive branch: Daemon._scope_amendments_approved/
    _scope_amendment_files degrade to [] rather than raising if
    storage.iter_events blows up (mirrors _ratchet_already_open's identical
    try/except shape, just above the O2 section below) -- a transient
    storage hiccup must never crash reconcile or misreport the amendment
    count."""
    cfg = behavioral_project
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    assert d._scope_amendments_approved("demo", TASK_ID) == []
    assert d._scope_amendment_files("demo", TASK_ID) == []


def test_scope_amendment_files_reach_review_independent_dispatch(
    behavioral_project, tmp_state, fake_cli
):
    """O6 wiring (daemon-level, complements the O6 adapter-unit test in
    test_adapters.py): LaunchReview's aggregation loop actually COMPUTES a
    wave member's approved amendment file(s) (Daemon._scope_amendment_files)
    and threads them into the real build_dispatch call, for a task that
    genuinely reaches a review wave carrying an approved amendment -- not
    just a synthetic direct call.

    Seeds the SCOPE_AMENDMENT_APPROVED event directly (bypassing the actual
    request/approve cycle, which O1/O3 above already cover) rather than
    scripting a real SCOPE_AMENDMENT_REQUEST + a second implementer attempt.
    Reason (found while writing this test, documented in LOG.md): daemon.py's
    `_attempt_scan` (module contract item 4) rescans EVERY historical EXITED
    IMPLEMENTER attempt on a task -- not just the latest -- whenever the task
    is currently ACTIVE. Once a second real implementer attempt is dispatched
    (task ACTIVE again), the FIRST (already-consumed) scope-amendment
    attempt's stale receipt matches that same "EXITED while ACTIVE" catch-all
    and gets a SECOND EmitAttemptExit planned in the same pass as the fresh
    attempt's own exit -- a pre-existing daemon quirk (unrelated to and out
    of scope for this package) that a two-attempt scope-amendment scenario
    can trigger over enough ticks. A single clean implementer attempt (no
    scope-amendment marker at all) with the approval event seeded ahead of
    time reaches the same amendment-carrying-review state without it."""
    cfg = behavioral_project
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    ptoml.write_text(ptoml.read_text().replace(
        "[project]\n",
        '[project]\npipeline = ["carve", "implement", "review_independent", '
        '"triage", "auto_merge", "post_merge_gate"]\n', 1))

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")  # discover handoff -> CARVED (creates the statefile)

    states = storage.list_states("demo")
    storage.append_and_apply(
        "demo", states,
        actor=Actor(ActorKind.OPERATOR, "test-seed"),
        type=EventType.SCOPE_AMENDMENT_APPROVED,
        payload={"file": "src/demo/shared_helper.py", "reason": "test seed"},
        task_id=TASK_ID,
    )

    fake_cli.queue(TASK_ID, "implementer", _impl_commit_step())
    fake_cli.queue(TASK_ID, "review", _review_approve_step(cfg, TASK_ID))

    for _ in range(30):
        _tick(d, "demo")
        tsf = storage.load_state("demo", TASK_ID)
        if tsf and tsf.state in (TaskState.MERGE_READY, TaskState.REVIEW_REJECTED,
                                   TaskState.BLOCKED):
            break

    tsf = storage.load_state("demo", TASK_ID)
    assert tsf.state == TaskState.MERGE_READY, (
        "the review dispatch must have actually run (and approved) for this "
        f"assertion to mean anything about LaunchReview's amendment "
        f"aggregation; got {tsf.state}"
    )
    review_attempts = [a for a in tsf.attempts if a.role is Role.REVIEW_INDEPENDENT]
    assert review_attempts, "expected a real REVIEW_INDEPENDENT dispatch to have run"

    approved = [e for e in storage.iter_events("demo")
                if e.type is EventType.SCOPE_AMENDMENT_APPROVED and e.task_id == TASK_ID]
    assert len(approved) == 1, (
        "sanity: the task must still carry its approved amendment when the "
        "review wave dispatched -- otherwise this test proves nothing about "
        "the aggregation loop actually running with a non-empty list"
    )


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
# tsf.attempts for ANY REVIEW_INDEPENDENT attempt in state EXITED, with no
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
        "REVIEW_INDEPENDENT attempts -- a second, genuinely fresh review"
    )
    results = [e.payload.get("result") for e in review_recorded]
    assert "rejected" in results and "approved" in results, (
        f"expected a rejected verdict followed by an approved one; got {results}"
    )
    assert tsf.state == TaskState.MERGE_READY
