"""Phase-2 reviewer coverage of P23's cleanup-masking handlers and the
mutation wave loop's budget/fatal discrimination.

These are the branches A-195 and P22's own cleanup repair are *about*, and
neither the project suite nor the byte-locked packet reached them:

* `_run_higher_rigor_lane`'s `except AssayError` arm when a normal result
  already exists — the locked `ExitFails` raises `OSError`, taking the
  sibling arm, so an `AssayError` escaping a context's `__exit__` (which is
  what P22's own cleanup actually raises) was never driven;
* the same function's `except RuntimeError` arm, in BOTH directions: laundering
  a cleanup-time leak into a claim, and refusing to launder a genuine
  programmer error into one;
* `run_mutation`'s "every LATER identity is budget-stopped too" rule, which
  needs more than one unsubmitted identity to be observable at all; and
* the same loop's discrimination between the deadline's own
  `BUDGET_EXCEEDED`/`LANE_TIMEOUT` and P22's `BUDGET_EXCEEDED`/
  `SNAPSHOT_LIMIT_EXCEEDED` policy refusal.

`scratch_root_factory` is the seam the packet froze precisely so a cleanup
failure can be injected "without permissions, races, or monkeypatching
`tempfile` internals". The handler treats a failure from either owned context
identically, so the outer one stands in for both.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest
from conftest import GitRepo, make_lane, make_r2_judge

from assay import mutation, runner
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode

MOMENT = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return MOMENT


class CountingMonotonic:
    def __init__(self, *, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        observed = self.value
        self.value += self.step
        return observed


def _seed(repo: GitRepo, *, head_source: str) -> tuple[str, str]:
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("base")
    repo.write("pkg/mod.py", head_source)
    head_rev = repo.commit_all("introduce sites")
    return base_rev, head_rev


def _r2_lane(
    repo: GitRepo,
    base_rev: str,
    *,
    jobs: int = 1,
    max_mutants: int = 20,
    budget_seconds: float = 300.0,
):
    return make_lane(
        rigor=("R0", "R2"),
        judge=make_r2_judge(
            language="python",
            source_root_paths=(repo.path / "pkg",),
            base=base_rev,
            mutation=MutationConfig(
                jobs=jobs, max_mutants=max_mutants, operators=("compare-swap",)
            ),
        ),
        argv=("check",),
        budget=f"{budget_seconds}s",
        budget_seconds=budget_seconds,
    )


def _pass_everything(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), 0, "", "")


# --- the outer-cleanup handlers ---------------------------------------------


class _ExitRaises:
    """A scratch-root context that yields normally and fails on the way out
    with a caller-chosen exception -- the deterministic stand-in the packet's
    own seam exists for."""

    def __init__(self, root: Path, exc: BaseException) -> None:
        self.root = root
        self.exc = exc

    def __enter__(self) -> Path:
        return self.root

    def __exit__(self, exc_type, exc, traceback):
        raise self.exc


class _EnterRaises:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def __enter__(self):
        raise self.exc

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_a_cleanup_assay_error_replaces_only_the_highest_higher_rigor_claim(
    git_repo: GitRepo, tmp_path: Path
):
    """A-193/A-194's "outer scratch cleanup alone fails" rule, driven by an
    ``AssayError`` rather than the locked case's ``OSError``.

    P22's own cleanup raises ``AssayError``, so this is the arm a real
    cleanup failure takes. The completed R0 claim must survive verbatim; only
    the highest declared higher-rigor claim becomes the payload-free
    ``ERROR``/``GIT_FAILED`` pair, and its judgment tier is removed."""
    base_rev, head_rev = _seed(git_repo, head_source="def f(x):\n    return x > 0\n")
    scratch = (tmp_path / "owned-scratch").resolve()
    scratch.mkdir()
    calls = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        _r2_lane(git_repo, base_rev),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
        scratch_root_factory=lambda: _ExitRaises(
            scratch,
            AssayError(
                "injected cleanup failure",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.GIT_FAILED,
            ),
        ),
    )

    assert calls == 2, "baseline and one mutant really ran before cleanup failed"
    assert verdict.claims[0].status is Outcome.PASS, "the completed R0 claim survives"
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (Outcome.ERROR, ReasonCode.GIT_FAILED)
    assert r2.mutation is None
    assert verdict.judgment is None


def test_a_cleanup_runtime_error_after_a_result_is_not_masked_away(
    git_repo: GitRepo, tmp_path: Path
):
    """``prepare_snapshot``'s own live-child leak detection raises
    ``RuntimeError`` on a NORMAL exit. Reaching it after a complete result
    means assay itself leaked a context -- the run's own evidence is no longer
    trustworthy, so the highest higher-rigor claim is replaced exactly as any
    other cleanup failure would be, rather than the leak being swallowed and
    a clean-looking PASS emitted."""
    base_rev, head_rev = _seed(git_repo, head_source="def f(x):\n    return x > 0\n")
    scratch = (tmp_path / "owned-scratch").resolve()
    scratch.mkdir()

    verdict = runner.run_lane(
        _r2_lane(git_repo, base_rev),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_pass_everything,
        clock=_clock,
        monotonic=CountingMonotonic(),
        scratch_root_factory=lambda: _ExitRaises(
            scratch, RuntimeError("injected live-child leak")
        ),
    )

    assert verdict.claims[0].status is Outcome.PASS
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (Outcome.ERROR, ReasonCode.GIT_FAILED)
    assert r2.mutation is None
    assert verdict.judgment is None


def test_a_runtime_error_before_any_result_propagates_and_is_never_laundered(
    git_repo: GitRepo,
):
    """The other direction of the same handler, and the one that keeps it
    honest: a ``RuntimeError`` with no completed result behind it is a genuine
    programmer error, not a lane terminal. Turning it into an
    ``ERROR``/``GIT_FAILED`` claim would bury a real bug inside a
    well-formed artifact that a consumer would read as an ordinary refusal."""
    base_rev, head_rev = _seed(git_repo, head_source="def f(x):\n    return x > 0\n")

    def process(*args, **kwargs):
        raise AssertionError("nothing may run once the scratch root is unusable")

    with pytest.raises(RuntimeError, match="injected programmer error"):
        runner.run_lane(
            _r2_lane(git_repo, base_rev),
            commit=head_rev,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=PythonAdapter(),
            assay_version="0.1.0",
            process_runner=process,
            clock=_clock,
            monotonic=CountingMonotonic(),
            scratch_root_factory=lambda: _EnterRaises(
                RuntimeError("injected programmer error")
            ),
        )


# --- the mutation wave loop -------------------------------------------------


def test_every_identity_after_an_expiry_is_budget_stopped_not_only_the_next(
    git_repo: GitRepo,
):
    """The locked expiry case has exactly two sites, so the identity that
    observes expiry IS the last one and the "mark every remaining identity"
    rule is invisible. With three sites and ``jobs=1``, expiry after the first
    mutant must budget-stop BOTH remaining identities, launch no further
    process, and keep the completed one as evidence."""
    source = "def f(x):\n    return x > 0 and x > 1 and x > 2\n"
    base_rev, head_rev = _seed(git_repo, head_source=source)
    expired = False
    units: list[str] = []

    def monotonic() -> float:
        return 101.0 if expired else 0.0

    def process(argv, *, env, cwd, timeout):
        nonlocal expired
        text = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        unit = "baseline" if text == source else "mutant"
        units.append(unit)
        if unit == "mutant":
            expired = True
        return subprocess.CompletedProcess(
            list(argv), 0 if unit == "baseline" else 1, "", ""
        )

    verdict = runner.run_lane(
        _r2_lane(git_repo, base_rev, budget_seconds=100.0),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=monotonic,
    )

    assert units == ["baseline", "mutant"], "no third or fourth process may start"
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert r2.mutation.total == 3
    assert len(r2.mutation.killed) == 1, "the completed identity remains evidence"
    assert len(r2.mutation.budget_exceeded) == 2, "BOTH later identities are stopped"
    # buckets stay identity-ordered independent of completion order
    stopped = [outcome.start_byte for outcome in r2.mutation.budget_exceeded]
    assert stopped == sorted(stopped)


class _SnapshotLimitPrepared:
    """A `prepared` stand-in whose replacement materialization refuses with
    P22's own policy-limit pair -- a REFUSAL, not a lane that ran out of
    time."""

    def __init__(self) -> None:
        self.calls = 0

    def materialize_replacement(self, **kwargs):
        self.calls += 1
        raise AssayError(
            "the transferred pack exceeded max_pack_bytes",
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.SNAPSHOT_LIMIT_EXCEEDED,
        )


def test_a_p22_policy_refusal_in_a_worker_keeps_its_own_pair(git_repo: GitRepo):
    """The handoff says the wave loop catches "ONLY that exact
    ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` from ``deadline.remaining()``".

    Matching on the outcome alone also swallowed P22's own
    ``BUDGET_EXCEEDED``/``SNAPSHOT_LIMIT_EXCEEDED`` policy refusal, which
    would then have been relabelled ``LANE_TIMEOUT`` and reported as a
    per-identity budget stop with the surviving identities still presented as
    evidence. It must instead propagate unchanged, as the payload-free R2
    terminal the table reserves for a P22 worker failure."""
    plan = runner.resolve_command_plan(
        make_lane(argv=("check",)),
        passthrough_source={},
        project_prefix=PurePosixPath("."),
    )
    baseline = runner.CommandResult(
        plan=plan,
        outcome=Outcome.PASS,
        reason_code=None,
        returncode=0,
        started=MOMENT.isoformat(),
        ended=MOMENT.isoformat(),
    )
    prepared = _SnapshotLimitPrepared()
    target = mutation.MutationTarget(
        path="pkg/mod.py",
        text="def f(x):\n    return x > 0\n",
        lines=frozenset({2}),
    )

    def explode_process(*args, **kwargs):
        raise AssertionError("a refused materialization must launch no process")

    with pytest.raises(AssayError) as caught:
        mutation.run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=runner.LaneDeadline.start(
                budget_seconds=100.0, monotonic=CountingMonotonic()
            ),
            targets=(target,),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=20,
            operators=("compare-swap",),
            process_runner=explode_process,
            clock=_clock,
        )

    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.SNAPSHOT_LIMIT_EXCEEDED
    assert prepared.calls == 1


# --- committed bytes that are not valid UTF-8 -------------------------------


def _commit_non_utf8(repo: GitRepo, rel: str, *, head_first_line: str) -> tuple[str, str]:
    """Commit *rel* holding a real non-UTF-8 byte sequence on a line the diff
    never touches, so ``git diff``'s own output stays decodable while the
    blob itself does not.

    That separation is the point: `assay.git`'s decoder would refuse a diff
    containing the bad bytes long before any source read, so a fixture that
    put them on the CHANGED line would prove a different guard entirely."""
    tail = b'BAD = "\xff\xfe"\n'
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    (repo.path / "pkg/mod.py").write_bytes(b"x = 1\n" + tail)
    base_rev = repo.commit_all("base")
    (repo.path / "pkg/mod.py").write_bytes(head_first_line.encode("ascii") + tail)
    head_rev = repo.commit_all("change only the ascii line")
    return base_rev, head_rev


def test_a_non_utf8_source_file_renders_a_complete_r2_claim(git_repo: GitRepo):
    """P23 reads every mutation-target source through the prepared seed, and
    the strict UTF-8 decode there must land inside a COMPLETE R2 claim -- the
    same ``ERROR``/``UNREADABLE_ARTIFACT`` pair the direct path's own bounded
    read renders -- never propagate out of ``run_lane`` uncaught."""
    base_rev, head_rev = _commit_non_utf8(
        git_repo, "pkg/mod.py", head_first_line="x = 2\n"
    )

    verdict = runner.run_lane(
        _r2_lane(git_repo, base_rev),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_pass_everything,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert verdict.claims[0].status is Outcome.PASS, "R0 still ran and passed"
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )
    assert r2.mutation is None


def test_mutation_integrity_check_is_lane_budgeted_yet_keeps_completed_evidence(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """P26/A-212 and A-195 hold together at `_snapshot_left_dirt`'s call site.

    The frozen P26 handoff §5 requires the mutation snapshot dirt/HEAD checks
    to "pass ``deadline.remaining`` into the private helper" AND to "change no
    bucket semantics". Two implementations each satisfy only one half, and
    this oracle rejects both:

    * omitting the callable leaves those Git children genuinely UNBOUNDED
      (``git._run_bounded`` waits in ``selector.select(None)``/``proc.wait()``
      with no timeout), so a hung ``status``/``rev-parse`` outlives the whole
      lane budget inside a worker — caught by the forwarding assertions;
    * forwarding it naively lets an expiry observed at this AFTER-the-fact
      bookkeeping step reclassify an already-decisive mutant, so the completed
      identity's real result is discarded — caught by the bucket assertions.

    The deadline is driven by the same fake `monotonic` the sibling budget test
    uses (the mutant's own process flips it), so nothing here depends on wall
    clock, machine speed, worker, or test order.
    """
    source = "def f(x):\n    return x > 0 and x > 1 and x > 2\n"
    base_rev, head_rev = _seed(git_repo, head_source=source)
    expired = False
    units: list[str] = []
    integrity_remaining: list[object] = []

    real_dirty_paths = mutation.git.dirty_paths
    real_head_rev = mutation.git.head_rev

    def monotonic() -> float:
        return 101.0 if expired else 0.0

    def spy_dirty_paths(repo, *, remaining=None):
        if Path(repo) != git_repo.path:  # a mutant snapshot, not the lane repo
            integrity_remaining.append(remaining)
        return real_dirty_paths(repo, remaining=remaining)

    def spy_head_rev(repo, *, remaining=None):
        if Path(repo) != git_repo.path:
            integrity_remaining.append(remaining)
        return real_head_rev(repo, remaining=remaining)

    monkeypatch.setattr(mutation.git, "dirty_paths", spy_dirty_paths)
    monkeypatch.setattr(mutation.git, "head_rev", spy_head_rev)

    def process(argv, *, env, cwd, timeout):
        nonlocal expired
        text = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        unit = "baseline" if text == source else "mutant"
        units.append(unit)
        if unit == "mutant":
            expired = True
        return subprocess.CompletedProcess(
            list(argv), 0 if unit == "baseline" else 1, "", ""
        )

    verdict = runner.run_lane(
        _r2_lane(git_repo, base_rev, budget_seconds=100.0),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=monotonic,
    )

    # (1) the integrity check really is deadline-forwarded, never unbounded
    assert integrity_remaining, "the snapshot dirt/HEAD check never ran"
    assert all(callable(item) for item in integrity_remaining), (
        "the mutation snapshot dirt/HEAD check ran an UNBOUNDED Git child; "
        "handoff §5 requires deadline.remaining reach the private helper"
    )
    # (2) it is the ONE lane deadline, not a fresh or detached budget: it
    #     reports the very expiry this lane observed.
    for item in integrity_remaining:
        with pytest.raises(AssayError) as caught:
            item()
        assert (caught.value.outcome, caught.value.reason_code) == (
            Outcome.BUDGET_EXCEEDED,
            ReasonCode.LANE_TIMEOUT,
        )

    # (3) ...and the bucket semantics are byte-identical to before P26.
    assert units == ["baseline", "mutant"], "no third or fourth process may start"
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert r2.mutation.total == 3
    assert len(r2.mutation.killed) == 1, (
        "an expiry seen at this AFTER-the-fact bookkeeping check discarded an "
        "already-decisive mutant; completed identities remain evidence"
    )
    assert len(r2.mutation.budget_exceeded) == 2
