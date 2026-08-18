"""``runner.run_lane``'s R2 orchestration (P18): mutation testing after R0
(and after R1, when both are declared), reusing R0's own already-obtained
``CommandResult`` as the mandatory baseline (never re-executed), and R2's
target-scoping diff -- reused from R1 when it already resolved one, or
independently resolved through the identical two measurability guards
when it did not.

Real git state materialised under ``tmp_path`` (the established P02/P17
pattern) and the REAL :class:`~assay.adapters.python.PythonAdapter`
(``FakeAdapter`` has no ``generate_mutants`` -- R2 needs a real mutation
catalogue, so this module trades the R1 suite's synthetic ``.zzz``
language for real, minimal Python source).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, fixed_clock, make_lane, make_r1_judge, make_r2_judge

from assay import git as git_module
from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import Outcome, ReasonCode
from assay.verdict import Judgment

MOMENT_A = datetime(2026, 8, 8, 10, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 8, 8, 10, 0, 1, tzinfo=timezone.utc)
MOMENT_C = datetime(2026, 8, 8, 10, 0, 2, tzinfo=timezone.utc)
MOMENT_D = datetime(2026, 8, 8, 10, 0, 3, tzinfo=timezone.utc)
MOMENT_E = datetime(2026, 8, 8, 10, 0, 4, tzinfo=timezone.utc)

_MUTATION = MutationConfig(jobs=1, max_mutants=50, operators=("python:compare-swap",))


def _seed_compare_swap_site(repo: GitRepo) -> tuple[str, str]:
    """A real two-commit diff introducing ONE ``compare-swap`` site:
    ``x > 0`` has exactly one swap target (``>=``) in the adapter's own
    closed catalogue, so this fixture always generates exactly one
    mutant.

    ``.gitignore`` for ``cov.json`` rides along in the base commit (A-140,
    matching ``test_runner_run_lane.py``'s own ``_seed_two_commits``):
    unrelated to ``src/mod.py``'s own diff, so it cannot change what R1/R2
    measure, but P20's post-command dirty check (A-175) now refuses the
    whole run if a real lane command writes an un-ignored ``cov.json``.
    """
    repo.write(".gitignore", "cov.json\n")
    repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("add mod.py")
    repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    head_rev = repo.commit_all("introduce a compare-swap site")
    return base_rev, head_rev


def _kill_on_mutation():
    """A REAL-shaped, injected ``process_runner``: PASS against the
    baseline's own unmodified content, and PASS iff the literal substring
    ``x > 0`` survived the splice -- so the single generated mutant
    (``x >= 0``) is genuinely, mechanically KILLED, never asserted by fiat.

    Content-keyed rather than ``cwd``-keyed: P23 runs the baseline inside its
    own independent P22 snapshot too, never at the consumer's own
    ``project_root`` directly, so a decision keyed to path identity would
    never match baseline's own call. The baseline's real, unmodified content
    already contains ``x > 0``, so this single content check is correct for
    BOTH baseline and mutant without a separate cwd-comparison branch.
    """

    def decide(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "src" / "mod.py").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            list(argv), returncode=0 if "x > 0" in text else 1, stdout="", stderr=""
        )

    return decide


# --- R2 declared without R1: independent target-scoping diff -----------------


def test_r2_without_r1_kills_the_one_generated_mutant(git_repo: GitRepo):
    base_rev, head_rev = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(),
    )

    assert verdict.outcome is Outcome.PASS
    assert [c.rigor for c in verdict.claims] == ["R0", "R2"]
    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.PASS
    assert r2_claim.mutation.total == 1
    assert len(r2_claim.mutation.killed) == 1
    assert verdict.judgment.r2.jobs == 1
    assert verdict.judgment.r2.operators == ("python:compare-swap",)
    assert verdict.judgment.r1 is None


def test_r2_without_r1_refuses_on_base_is_head(git_repo: GitRepo):
    """R2's own independent guard sequence -- the IDENTICAL
    ``check_base_is_head`` R1 uses -- fires when no R1 claim resolved a
    diff to reuse."""
    head = git_repo.head()
    judge = make_r2_judge(
        source_root_paths=(git_repo.path,), base=head, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    r2_claim = verdict.claims[1]
    assert r2_claim.rigor == "R2"
    assert r2_claim.status is Outcome.NO_MEASUREMENT
    assert r2_claim.reason_code is ReasonCode.BASE_IS_HEAD
    assert r2_claim.mutation is None
    assert verdict.judgment is None


def test_r2_without_r1_refuses_on_dirty_tree_the_command_itself_created(
    git_repo: GitRepo,
):
    """R2's own guard sequence runs its ``check_dirty_tree`` AFTER R0's
    command has already executed -- the identical POST-execution,
    source-root-scoped check ``evaluate_r1`` already applies (distinct
    from ``run_lane``'s own PRE-execution whole-tree guard, which cannot
    see pollution the command itself introduces after starting clean):
    a real ``touch`` under the declared source root, left behind by R0's
    own argv, is what trips this -- not anything present before the run
    started."""
    base_rev, head_rev = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("/bin/sh", "-c", "touch src/leftover.pyc"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.NO_MEASUREMENT
    assert r2_claim.reason_code is ReasonCode.DIRTY_TREE
    assert r2_claim.mutation is None


def test_judgment_r2_records_the_lanes_own_declared_policy_verbatim(
    git_repo: GitRepo,
):
    """A-143's shape applied to ``judgment.r2``. Every other R2 fixture in
    the suite declares ``jobs = 1`` and the single operator its own fixture
    happens to use -- so a producer that hardcoded ``jobs=1``, or that
    recorded the operators it actually SUBMITTED rather than the ones the
    lane declared, or that canonicalised the list, would pass all of them.

    This lane declares a ``jobs`` no default could coincide with, and TWO
    operators in an order that is neither alphabetical nor
    ``MUTATION_OPERATORS``'s own -- of which the fixture exercises exactly
    one. Both must come back untouched: the record is the DECLARED policy,
    not a summary of what happened.
    """
    base_rev, head_rev = _seed_compare_swap_site(git_repo)
    declared = MutationConfig(
        jobs=3, max_mutants=50, operators=("python:falsy-swap", "python:compare-swap")
    )
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=declared
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(),
    )

    assert verdict.claims[1].mutation.total == 1, "only the compare-swap site exists"
    assert verdict.judgment.r2.jobs == 3
    assert verdict.judgment.r2.operators == ("python:falsy-swap", "python:compare-swap")


def test_the_lanes_command_runs_exactly_once_against_the_unmodified_tree(
    git_repo: GitRepo,
):
    """O3's own negative, at the level it is actually about: "calling
    run_mutation's old baseline path increments the command ledger twice".
    ``run_mutation`` no longer HAS that path (its own suite proves the
    function itself never re-runs a baseline), but the property O3 states
    is a property of the PIPELINE -- so it is counted here, where R0 and R2
    meet, against the real ``cwd`` each invocation receives.

    P23: baseline and mutant each run inside their OWN independent P22
    snapshot -- neither ever runs at the consumer's own ``project_root``
    directly (a STRONGER isolation than the old "R0's own project_root,
    reused verbatim" shape this test used to check by path identity), so
    the "ran against the unmodified tree exactly once" claim is now proven
    by CONTENT rather than by path equality.
    """
    base_rev, head_rev = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))
    decide = _kill_on_mutation()
    cwds: list[Path] = []
    contents: list[str] = []

    def counting(argv, *, env, cwd, timeout):
        cwds.append(Path(cwd))
        # read WHILE the snapshot is still live -- it is discarded the
        # moment this unit's context closes, before `run_lane` returns.
        contents.append((Path(cwd) / "src" / "mod.py").read_text(encoding="utf-8"))
        return decide(argv, env=env, cwd=cwd, timeout=timeout)

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=counting,
    )

    assert verdict.claims[1].mutation.total == 1
    assert len(cwds) == 2, "one baseline plus one mutant, and nothing else"
    assert len(set(cwds)) == 2, (
        "the baseline and the one mutant each run inside their own "
        "independent P22 snapshot, never the same directory"
    )
    assert git_repo.path not in cwds, (
        "the lane's own command never runs against the consumer's real "
        "repository at all under the higher-rigor path"
    )
    assert contents.count("def f(x):\n    return x > 0\n") == 1, (
        "the lane's own command must run exactly ONCE against the "
        "unmodified baseline content -- R0's result IS R2's baseline"
    )


# --- the project is a SUBDIRECTORY of its repository (A-145) -----------------


def test_r2_mutates_a_project_that_lives_in_a_subdirectory_of_its_repo(
    git_repo: GitRepo,
):
    """A-145. Every target path is REPO-relative (``sub/src/mod.py``) while
    each mutant's scratch tree is a copy of the PROJECT root (``sub/``), so
    the two spellings differ by exactly the project's own prefix. Before
    the fix this raised ``FileNotFoundError`` out of a worker thread, out
    of ``run_lane``, and out of ``assay run`` -- AFTER the lane's command
    had already run, with no artifact emitted at all (the A-139 shape).
    Not a hypothetical layout: it is assay's own, inside ``vbpub``.

    The lane's own ``grep`` is what decides kill vs survival, and it names
    ``src/mod.py`` -- the PROJECT-relative spelling, because that is what
    the command's own ``cwd`` sees. That the same file is
    ``sub/src/mod.py`` in the artifact is the whole point.
    """
    project_root = git_repo.path / "sub"
    git_repo.write("sub/src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("sub/src/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.commit_all("introduce a compare-swap site")
    judge = make_r2_judge(
        source_root_paths=(project_root / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("/bin/sh", "-c", "grep -q 'x > 0' src/mod.py"),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=project_root,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    r2_claim = verdict.claims[1]
    assert r2_claim.mutation.total == 1
    assert len(r2_claim.mutation.killed) == 1, (
        "the mutant must reach src/mod.py INSIDE the copy -- a kill proves "
        "the splice landed on the file the command actually greps"
    )


def test_a_surviving_mutant_in_a_subdirectory_project_keeps_its_repo_relative_path(
    git_repo: GitRepo,
):
    """The other half of A-145: the path the ARTIFACT records is the
    repo-relative one (``sub/src/mod.py``), the same spelling
    ``Coverage.missing_lines``'s own keys and ``git diff`` already use --
    never the project-relative one the write step needs internally. A
    survivor is what makes that observable, because only the non-killed
    buckets carry per-mutant identities at all."""
    project_root = git_repo.path / "sub"
    git_repo.write("sub/src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("sub/src/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.commit_all("introduce a compare-swap site")
    judge = make_r2_judge(
        source_root_paths=(project_root / "src",), base=base_rev, mutation=_MUTATION
    )
    # exits 0 regardless of what the file says: nothing asserts the change,
    # so the mutant genuinely survives.
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=project_root,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.FAIL
    assert r2_claim.reason_code is ReasonCode.MUTANTS_SURVIVED
    assert [entry.path for entry in r2_claim.mutation.survived] == ["sub/src/mod.py"]


# --- a non-PASS R0 baseline is R2's own necessary prerequisite ---------------


def test_a_failing_r0_baseline_is_reused_verbatim_and_r2s_own_guards_never_run(
    git_repo: GitRepo,
):
    """*base* == HEAD, which WOULD trip R2's own ``BASE_IS_HEAD`` guard if
    it were ever consulted -- proving R0's own FAIL takes precedence
    unconditionally, never even reaching that guard (module docstring:
    "R2's baseline gate is strictly stricter than R1's")."""
    head = git_repo.head()
    judge = make_r2_judge(
        source_root_paths=(git_repo.path,), base=head, mutation=_MUTATION
    )
    lane = make_lane(
        rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 7")
    )

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.FAIL
    r2_claim = verdict.claims[1]
    assert r2_claim.rigor == "R2"
    assert r2_claim.status is Outcome.FAIL
    assert r2_claim.reason_code is ReasonCode.COMMAND_FAILED
    assert r2_claim.mutation is None
    assert verdict.judgment is None


# --- R1 and R2 declared together: the diff resolves exactly once ------------


def test_r1_and_r2_together_reuse_the_same_resolved_diff_not_a_second_one(
    git_repo: GitRepo, monkeypatch
):
    """A single, REAL ``/bin/sh`` command (no injected process_runner, so
    both R0/R1's coverage write and R2's kill/survive decision come from
    ACTUALLY running the same script): unconditionally writes ``cov.json``
    (R1's own artifact), then greps the live file for the literal
    substring ``x > 0`` -- PASS/writes-coverage against the real baseline,
    and genuinely KILLED for the one generated mutant (whose own splice
    reads ``x >= 0``)."""
    base_rev, _ = _seed_compare_swap_site(git_repo)
    r1_judge = make_r1_judge(
        # P33/V5-2: a combined R1+R2 judge declares python operators, so the
        # resolved language must be `python` -- the default `zzz` would make
        # the recorded policy name a catalogue that language does not have.
        language="python",
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        fail_under=0.0,
        mutation=_MUTATION,
    )
    write_cov_then_check = (
        "cat > cov.json <<'EOF'\n"
        '{"files": {"src/mod.py": {"executed_lines": [1, 2], '
        '"missing_lines": [], "excluded_lines": []}}}\n'
        "EOF\n"
        "grep -q 'x > 0' src/mod.py"
    )
    lane = make_lane(
        rigor=("R0", "R1", "R2"),
        judge=r1_judge,
        argv=("/bin/sh", "-c", write_cov_then_check),
    )

    diff_calls: list[tuple] = []
    real_run = git_module.run

    def counting_run(repo, *args, **kwargs):
        if args and args[0] == "diff":
            diff_calls.append(args)
        return real_run(repo, *args, **kwargs)

    monkeypatch.setattr(git_module, "run", counting_run)

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert len(diff_calls) == 1, "base..head must be diffed exactly once per run"
    assert [c.rigor for c in verdict.claims] == ["R0", "R1", "R2"]
    r2_claim = verdict.claims[2]
    assert r2_claim.status is Outcome.PASS
    assert r2_claim.mutation.total == 1
    assert isinstance(verdict.judgment, Judgment)
    assert verdict.judgment.r1 is not None
    assert verdict.judgment.r2 is not None
    assert verdict.judgment.r2.jobs == 1


def test_r1_declared_but_its_own_coverage_guard_trips_still_lets_r2_resolve(
    git_repo: GitRepo,
):
    """R1's own artifact consume/parse renders the P20/A-174 truthful
    absence (a declared coverage artifact that was never written at all)
    before ``evaluate_r1`` is even called -- proving R2 does not silently
    inherit R1's own failure to resolve ``added``, and instead runs its own
    independent, SEPARATE resolution (the ``added_holder`` empty path) to a
    genuine PASS."""
    base_rev, _ = _seed_compare_swap_site(git_repo)
    r1_judge = make_r1_judge(
        # P33/V5-2: see the sibling above -- python operators need a python
        # resolution.
        language="python",
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        fail_under=0.0,
        coverage_artifact="never-written-cov.json",
        mutation=_MUTATION,
    )
    lane = make_lane(
        rigor=("R0", "R1", "R2"), judge=r1_judge, argv=("/bin/sh", "-c", "exit 0")
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(),
    )

    r1_claim, r2_claim = verdict.claims[1], verdict.claims[2]
    assert r1_claim.status is Outcome.NO_MEASUREMENT
    assert r1_claim.reason_code is ReasonCode.EMPTY_COVERAGE
    assert r1_claim.coverage is None
    assert r2_claim.status is Outcome.PASS
    assert r2_claim.mutation.total == 1
    assert verdict.judgment.r1 is None
    assert verdict.judgment.r2 is not None


# --- ended covers R2's own completion ----------------------------------------


def test_ended_covers_r2s_own_completion_not_only_r0s(git_repo: GitRepo):
    base_rev, head_rev = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(),
        # R0 (started/ended) + the ONE generated mutant (started/ended) +
        # run_lane's own final `ended` read -- five clock() calls total.
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C, MOMENT_D, MOMENT_E),
    )

    # jobs=1 and exactly one generated mutant makes the clock() sequence
    # deterministic: R0 (A/B), the one mutant (C/D), then this function's
    # own final `ended` read (E) -- an EXACT expected value, not merely
    # "differs from R0's own ended", the same discipline
    # test_run_lane_full_pass_builds_judgment_and_covers_ended_after_r1
    # already applies one rigor level over.
    assert verdict.started == "2026-08-08T10:00:00+00:00", "R0's own start, unchanged"
    assert verdict.ended == "2026-08-08T10:00:04+00:00", "covers the mutant's own completion"


# --- P20 work item 5: a mutation-target source-read failure is a complete claim


def test_run_lane_r2_renders_a_complete_claim_when_a_target_source_read_fails(
    monkeypatch: pytest.MonkeyPatch, git_repo: GitRepo
):
    """``resolve_mutation_targets`` calls the injected ``read_source_text``
    once per considered file; an expected filesystem/Unicode failure there
    must land inside a complete R2 claim, never propagate past `run_lane`
    uncaught (the same class of gap P20 closes for R1's own source reads).
    Driven by a REAL committed file exceeding the REAL fixed
    :data:`assay.runner.MAX_SOURCE_FILE_BYTES` ceiling (only the ceiling's
    magnitude is injected) -- the identical reviewer repair
    ``test_evaluate_r1_renders_unreadable_artifact_for_a_source_read_failure``
    documents, so R2's source-read boundary is proven by the bound itself
    rather than by the mechanism that implements it."""
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("src/broken.py", "def g(y):\n    return y\n")
    git_repo.commit_all("add a file that will fail to read")

    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    # 8 bytes: smaller than either committed source file, so the first real
    # target read really does exceed the real ceiling.
    monkeypatch.setattr(runner, "MAX_SOURCE_FILE_BYTES", 8)

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.PASS  # R0 itself is unaffected
    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.ERROR
    assert r2_claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r2_claim.mutation is None


# --- P34/W5: the baseline's own equivalence_artifact reservation -------------
#
# `MutationConfig` itself carries `equivalence_artifact`/`kill_signal_artifact`
# unconditionally (config.py's own language-scoped REFUSAL is a loader-time
# rule, not a dataclass one) -- so a `judge.language = "python"` R2 lane built
# directly here, bypassing the loader exactly like every other fixture in
# this module, is the correct way to exercise `runner.py`'s OWN mechanism in
# isolation from SQL entirely (the table itself carries "zero SQL knowledge",
# carve §3.6).

_MUTATION_WITH_EQUIVALENCE = MutationConfig(
    jobs=1,
    max_mutants=50,
    operators=("python:compare-swap",),
    equivalence_artifact="dump.txt",
)


def _seed_compare_swap_site_ignoring_dump(repo: GitRepo) -> tuple[str, str]:
    """The identical two-commit compare-swap fixture as
    ``_seed_compare_swap_site``, but ignoring ``dump.txt`` (this section's
    own declared ``equivalence_artifact``) and ``dump-link.txt`` (a second
    hard-linked name one test below deliberately creates) instead of
    ``cov.json`` -- A-140/A-175 again: an un-ignored file the lane's own
    command leaves behind trips the post-command dirty check before any
    claim is even reached."""
    repo.write(".gitignore", "dump.txt\ndump-link.txt\n")
    repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("add mod.py")
    repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    head_rev = repo.commit_all("introduce a compare-swap site")
    return base_rev, head_rev


def test_a_baseline_that_declares_equivalence_artifact_and_does_not_write_it_stops_before_any_mutant(
    git_repo: GitRepo,
):
    """(P34/A-279, carve §3.7) The baseline declared
    ``judge.mutation.equivalence_artifact`` and its own command never wrote
    it -- ``ERROR``/``EXEC_FAILED``, before any mutant, NEVER a comparison
    against ``None`` (which would make every mutant unequal and hide the
    fault this row exists to catch).

    Proven not merely by the claim's own shape but by the process-boundary
    ledger, mirroring ``test_mutation_baseline_gate.py``'s own O1 proof one
    level up: the mutant's own compare-swap site would be a genuine,
    submittable candidate if this guard were deleted, so ``len(calls) == 1``
    is the "no mutant attempted" fact, not an assumption.
    """
    base_rev, head_rev = _seed_compare_swap_site_ignoring_dump(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))
    calls: list[Path] = []

    def counting(argv, *, env, cwd, timeout):
        calls.append(Path(cwd))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=counting,
    )

    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.ERROR
    assert r2_claim.reason_code is ReasonCode.EXEC_FAILED
    assert r2_claim.mutation is None
    assert verdict.judgment is None
    assert len(calls) == 1, "the process boundary must be crossed only ONCE -- the baseline"


def test_a_baseline_that_writes_its_equivalence_artifact_proceeds_to_a_real_mutant_run(
    git_repo: GitRepo,
):
    """The must-succeed control for the refusal above: the IDENTICAL
    declaration, with a command that actually writes the artifact, proceeds
    all the way to a real, judged R2 claim rather than refusing everything
    regardless of what the baseline did."""
    base_rev, head_rev = _seed_compare_swap_site_ignoring_dump(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("/bin/sh", "-c", "echo dump > dump.txt"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    r2_claim = verdict.claims[1]
    assert r2_claim.mutation is not None
    assert r2_claim.mutation.total == 1
    # The single generated mutant WROTE THE SAME constant "dump\n" the
    # baseline did (this command's own content does not depend on the
    # mutated file at all) -- so it is proven inert, never `survived`: the
    # equivalent bucket is reachable through `runner.run_lane` itself, not
    # only through `mutation.run_mutation` directly.
    assert r2_claim.mutation.survived == ()
    assert r2_claim.mutation.killed == ()
    assert len(r2_claim.mutation.equivalent) == 1
    assert verdict.judgment.r2.equivalence_artifact == "dump.txt"


def test_run_lane_never_reads_a_stale_pre_existing_equivalence_artifact(
    git_repo: GitRepo,
):
    """The mirror of ``test_runner_run_lane.py``'s own
    ``test_run_lane_removes_a_stale_artifact_before_running_never_reuses_it``,
    one artifact over: a stale, UNCOMMITTED copy of the declared
    ``equivalence_artifact`` sits on the CONSUMER's own real disk before the
    run starts. P23's snapshot-from-commit-alone construction means it is
    never even copied into the baseline's own snapshot in the first place,
    so the baseline's own reservation reads its own command's fresh bytes,
    never the stale ones -- proven here by the stale copy surviving
    UNTOUCHED and by the mutant landing in `equivalent` (which the stale
    bytes, chosen to differ, would NOT have produced).
    """
    base_rev, head_rev = _seed_compare_swap_site_ignoring_dump(git_repo)
    stale = "STALE -- must never be compared against\n"
    git_repo.write("dump.txt", stale)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("/bin/sh", "-c", "echo dump > dump.txt"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert (git_repo.path / "dump.txt").read_text(encoding="utf-8") == stale
    r2_claim = verdict.claims[1]
    assert r2_claim.mutation is not None
    assert len(r2_claim.mutation.equivalent) == 1
    # A-223d: every attempted mutant proven inert, none killed/survived --
    # INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT, not the ERROR a stale-bytes leak
    # would have hidden underneath (a false PASS/FAIL comparison against
    # content this run never produced).
    assert r2_claim.status is Outcome.INCONCLUSIVE
    assert r2_claim.reason_code is ReasonCode.ALL_MUTANTS_EQUIVALENT
    assert verdict.outcome is Outcome.INCONCLUSIVE


# --- P34/W5: §3.7's "either artifact tracked by git" refusal -----------------
#
# The tracked-artifact check is a baseline-time-only, PRE-EXECUTION refusal
# (§3.7: `runner._execute_snapshot_unit`) -- it fires before the lane's
# command ever runs, so none of these fixtures need a real compare-swap
# diff at all; a minimal one-file repo is enough to reach it.


def _seed_minimal_r2_repo(repo: GitRepo) -> str:
    repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    return repo.commit_all("add mod.py")


def test_run_lane_refuses_a_tracked_equivalence_artifact_r2_only(git_repo: GitRepo):
    """(§3.7) `equivalence_artifact` tracked by git inside the prepared
    snapshot is `ERROR`/`BAD_LANE_CONFIG`, mirroring
    `test_runner_run_lane.py::test_run_lane_refuses_a_tracked_artifact_path`
    one artifact over. R2-only (no R1 declared): the coverage reservation
    never exists, so this is also the "nothing else to close" arm."""
    base_rev = _seed_minimal_r2_repo(git_repo)
    git_repo.write("dump.txt", "accidentally committed\n")
    head_rev = git_repo.commit_all("accidentally commit the equivalence artifact")
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_run_lane_refuses_a_tracked_equivalence_artifact_with_r1_also_declared(
    git_repo: GitRepo,
):
    """The must-close-the-OTHER-reservation-too arm: R1 is ALSO declared,
    so the coverage reservation is already armed by the time the
    equivalence artifact's own tracked check fires -- proving that
    reservation is closed on this path too, never leaked."""
    git_repo.write(".gitignore", "cov.json\n")
    base_rev = _seed_minimal_r2_repo(git_repo)
    git_repo.write("dump.txt", "accidentally committed\n")
    head_rev = git_repo.commit_all("accidentally commit the equivalence artifact")
    judge = make_r1_judge(
        language="python",
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        fail_under=0.0,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(rigor=("R0", "R1", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG


_MUTATION_WITH_KILL_SIGNAL_ONLY = MutationConfig(
    jobs=1,
    max_mutants=50,
    operators=("python:compare-swap",),
    kill_signal_artifact="signal.txt",
)

_MUTATION_WITH_BOTH_ARTIFACTS = MutationConfig(
    jobs=1,
    max_mutants=50,
    operators=("python:compare-swap",),
    equivalence_artifact="dump.txt",
    kill_signal_artifact="signal.txt",
)


def test_run_lane_refuses_a_tracked_kill_signal_artifact_r2_only(git_repo: GitRepo):
    """(§3.7) The `kill_signal_artifact` mirror of the equivalence-artifact
    refusal above: R2-only and no `equivalence_artifact` declared, so
    NEITHER reservation exists yet when this check fires -- the "nothing
    to close" arm for both inner guards at once."""
    base_rev = _seed_minimal_r2_repo(git_repo)
    git_repo.write("signal.txt", "accidentally committed\n")
    head_rev = git_repo.commit_all("accidentally commit the kill-signal artifact")
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_KILL_SIGNAL_ONLY,
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_run_lane_refuses_a_tracked_kill_signal_artifact_with_both_other_reservations_live(
    git_repo: GitRepo,
):
    """The must-close-BOTH-other-reservations arm: R1 declared (coverage
    reservation live) AND `equivalence_artifact` ALSO declared (that
    reservation live too) by the time the kill-signal artifact's own
    tracked check fires -- proving both are closed on this path, never
    leaked."""
    git_repo.write(".gitignore", "cov.json\ndump.txt\n")
    base_rev = _seed_minimal_r2_repo(git_repo)
    git_repo.write("signal.txt", "accidentally committed\n")
    head_rev = git_repo.commit_all("accidentally commit the kill-signal artifact")
    judge = make_r1_judge(
        language="python",
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        fail_under=0.0,
        mutation=_MUTATION_WITH_BOTH_ARTIFACTS,
    )
    lane = make_lane(rigor=("R0", "R1", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_run_lane_refuses_a_non_regular_equivalence_artifact(git_repo: GitRepo):
    """(P20's vocabulary, applied here) A committed DIRECTORY at the
    declared `equivalence_artifact` path is not git-``ls-files``-tracked at
    that EXACT literal path (only the placeholder file inside it is), so
    this reaches `safeio.reserve_output`'s own pre-existing-object check --
    the reservation is refused before it is ever armed, let alone consumed
    -- mirroring
    `test_runner_run_lane.py::test_run_lane_refuses_a_directory_at_the_artifact_path`
    exactly, one artifact over. That refusal is never caught inside
    `_execute_snapshot_unit` (it is a structural precondition failure, the
    same disposition the coverage artifact's own tracked-path guard gets),
    so it propagates out and taints the WHOLE run uniformly -- R0 itself
    included, not merely R2 -- which is why this asserts only the
    verdict-level shape rather than a specific claim's."""
    base_rev = _seed_minimal_r2_repo(git_repo)
    git_repo.write("dump.txt/placeholder.txt", "not an equivalence artifact\n")
    git_repo.git("add", "-f", "dump.txt/placeholder.txt")
    head_rev = git_repo.commit_all("commit a directory at the equivalence artifact path")
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_run_lane_refuses_a_baseline_that_wrote_a_multiply_linked_equivalence_artifact(
    git_repo: GitRepo,
):
    """The SOFT half of a malformed artifact, distinct from the structural
    refusal above: the baseline's own command writes a REGULAR file at the
    declared path (so `reserve`/`arm` both succeed) but then hard-links a
    second name to it -- `safeio`'s own single-owner-output contract
    (`consume()`'s `require_single_link=True`) refuses it as reachable from
    somewhere this reservation never produced. Unlike the directory case,
    THIS failure happens strictly after `arm()`, so `_execute_snapshot_unit`
    catches it itself and turns it into an R2-scoped early claim -- R0
    stays a genuine, unaffected PASS, proving `equivalence_reservation.
    consume()`'s own `except AssayError` branch, not the uncaught
    propagation the directory test exercises."""
    base_rev, head_rev = _seed_compare_swap_site_ignoring_dump(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base_rev,
        mutation=_MUTATION_WITH_EQUIVALENCE,
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("/bin/sh", "-c", "echo dump > dump.txt && ln dump.txt dump-link.txt"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.claims[0].status is Outcome.PASS  # R0 itself is unaffected
    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.ERROR
    assert r2_claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r2_claim.mutation is None
    assert verdict.judgment is None
