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

_MUTATION = MutationConfig(jobs=1, operators=("compare-swap",))


def _seed_compare_swap_site(repo: GitRepo) -> tuple[str, str]:
    """A real two-commit diff introducing ONE ``compare-swap`` site:
    ``x > 0`` has exactly one swap target (``>=``) in the adapter's own
    closed catalogue, so this fixture always generates exactly one
    mutant."""
    repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("add mod.py")
    repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    head_rev = repo.commit_all("introduce a compare-swap site")
    return base_rev, head_rev


def _kill_on_mutation(project_root: Path):
    """A REAL-shaped, injected ``process_runner``: PASS against the
    unmodified *project_root* (the baseline), and against a mutant copy,
    PASS iff the literal substring ``x > 0`` survived the splice -- so the
    single generated mutant (``x >= 0``) is genuinely, mechanically
    KILLED, never asserted by fiat."""

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == project_root:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        text = (Path(cwd) / "src" / "mod.py").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            list(argv), returncode=0 if "x > 0" in text else 1, stdout="", stderr=""
        )

    return decide


# --- R2 declared without R1: independent target-scoping diff -----------------


def test_r2_without_r1_kills_the_one_generated_mutant(git_repo: GitRepo):
    base_rev, _ = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))

    verdict = runner.run_lane(
        lane,
        commit="a" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(git_repo.path),
    )

    assert verdict.outcome is Outcome.PASS
    assert [c.rigor for c in verdict.claims] == ["R0", "R2"]
    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.PASS
    assert r2_claim.mutation.total == 1
    assert r2_claim.mutation.killed == 1
    assert verdict.judgment.r2.jobs == 1
    assert verdict.judgment.r2.operators == ("compare-swap",)
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
        commit="b" * 40,
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
    base_rev, _ = _seed_compare_swap_site(git_repo)
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
        commit="c" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    r2_claim = verdict.claims[1]
    assert r2_claim.status is Outcome.NO_MEASUREMENT
    assert r2_claim.reason_code is ReasonCode.DIRTY_TREE
    assert r2_claim.mutation is None


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
        commit="d" * 40,
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

    def counting_run(repo, *args):
        if args and args[0] == "diff":
            diff_calls.append(args)
        return real_run(repo, *args)

    monkeypatch.setattr(git_module, "run", counting_run)

    verdict = runner.run_lane(
        lane,
        commit="e" * 40,
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
    """R1's own artifact-reading guard fires (a declared coverage artifact
    that was never written at all) before ``evaluate_r1`` ever reaches the
    diff -- proving R2 does not silently inherit R1's own failure to
    resolve ``added``, and instead runs its own independent, SEPARATE
    resolution (the ``added_holder`` empty path) to a genuine PASS."""
    base_rev, _ = _seed_compare_swap_site(git_repo)
    r1_judge = make_r1_judge(
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
        commit="f" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(git_repo.path),
    )

    r1_claim, r2_claim = verdict.claims[1], verdict.claims[2]
    assert r1_claim.status is Outcome.ERROR
    assert r1_claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r1_claim.coverage is None
    assert r2_claim.status is Outcome.PASS
    assert r2_claim.mutation.total == 1
    assert verdict.judgment.r1 is None
    assert verdict.judgment.r2 is not None


# --- ended covers R2's own completion ----------------------------------------


def test_ended_covers_r2s_own_completion_not_only_r0s(git_repo: GitRepo):
    base_rev, _ = _seed_compare_swap_site(git_repo)
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=_MUTATION
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))

    verdict = runner.run_lane(
        lane,
        commit="0" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_kill_on_mutation(git_repo.path),
        # R0 (started/ended) + the ONE generated mutant (started/ended) +
        # run_lane's own final `ended` read -- five clock() calls total.
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C, MOMENT_D, MOMENT_E),
    )

    assert verdict.started == "2026-08-08T10:00:00+00:00", "R0's own start, unchanged"
    assert verdict.ended != "2026-08-08T10:00:01+00:00", "not merely R0's own ended"
