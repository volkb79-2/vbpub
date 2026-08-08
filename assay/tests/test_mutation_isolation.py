"""O3 -- each mutant runs against ISOLATED source state; the shared source
tree is provably unchanged after killed, survived, crashed, AND
budget-stopped results (A-120's "true by construction" reading -- the
shared tree is never opened for writing at all, never a write-then-restore
round-trip); deterministic input order yields deterministic output order
regardless of completion order.

The negative this defends (O3, verbatim): *in-place shared mutation
contaminates a later fake run or leaves the source hash changed;
completion-order output changes the expected list.* The second half is
proven two ways, neither timing-dependent (AUTHORING.md §3b.A): a
SYNCHRONOUS fake executor (deterministic on every run, on every machine)
proves each job's OWN result attaches to that SAME job regardless of
execution order, keyed by the real, isolated, per-mutant file content
rather than by position; the REAL default ``ThreadPoolExecutor`` (genuine,
non-deterministic thread scheduling) then proves the identical property
holds under real concurrency too -- not asserted via elapsed time, but via
the SAME per-mutant-content-keyed attribution check, which a completion-
order bug would fail regardless of which thread happened to finish first.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import make_lane

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import (
    Mutant,
    MutationTarget,
    _within_project,
    project_prefix,
    run_mutation,
)
from assay.runner import execute_command

#: Four independent bool-const sites -- one per bucket this test drives
#: into KILLED, SURVIVED, CRASHED, and BUDGET_EXCEEDED respectively, keyed
#: by their own `lineno` so the fake process runner can decide per-mutant
#: behaviour from the MUTATED FILE'S OWN CONTENT (never from an execution-
#: order-derived signal).
_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    c = True\n"
    "    d = True\n"
    "    return a, b, c, d\n"
)
_TARGETS = (
    MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3, 4, 5})),
)


def _materialize_project(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")


class _SynchronousExecutor:
    """A minimal fake executor: ``submit`` runs *fn* IMMEDIATELY and
    returns an already-resolved ``Future`` -- no thread, no timing, no
    deferral (:func:`~assay.mutation.run_mutation` itself calls
    ``future.result()`` on every submitted future BEFORE its own ``with``
    block exits, so a fake that defers execution to ``__exit__`` would
    deadlock; this one cannot).

    Still a genuine test of attribution, not merely a decorative stand-in:
    each ``Future`` is a SEPARATE object tied to its OWN ``(job, index)``
    pair, so the test using this fake proves the right result attaches to
    the right job by construction of the FAKE's own contract, then relies
    on the per-mutant-CONTENT-keyed decision function (below) -- not on
    submission position -- to prove the isolation itself (not merely the
    bookkeeping) is what produced the right answer.
    """

    def __enter__(self) -> "_SynchronousExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def submit(self, fn, *args):
        future: Future = Future()
        future.set_result(fn(*args))
        return future


def _make_decide_by_mutated_content(project_root: Path):
    """Builds a fake ``process_runner`` bound to *project_root*: the
    BASELINE call always passes (unmutated content), and every MUTANT call
    reads the ACTUAL mutated file inside its own *cwd* and decides the
    outcome from what it finds there -- proves the right RESULT lands on
    the right JOB even when completion order is scrambled, because the
    decision is keyed to the real, isolated, per-mutant content rather
    than to submission position."""

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == project_root:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")
        if "b = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        if "c = False" in text:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if "d = False" in text:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        raise AssertionError(f"unexpected mutated content in {cwd}: {text!r}")

    return decide


def test_each_jobs_own_result_attaches_to_that_same_job_not_to_its_position(
    tmp_path: Path,
):
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    _materialize_project(project_root)
    scratch_root.mkdir()
    decide = _make_decide_by_mutated_content(project_root)
    baseline = execute_command(lane, cwd=project_root, process_runner=decide)

    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("bool-const-flip",),
        process_runner=decide,
        executor_factory=lambda jobs: _SynchronousExecutor(),
    )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert mutation.killed == 1
    assert [o.lineno for o in mutation.survived] == [3]
    assert [o.lineno for o in mutation.crashed] == [4]
    assert [o.lineno for o in mutation.budget_exceeded] == [5]


def test_jobs_1_and_jobs_3_render_identical_records_under_the_real_executor(
    tmp_path: Path,
):
    """The real ``ThreadPoolExecutor`` this time (the default factory),
    proving the SAME per-mutant-content-keyed decision produces the
    IDENTICAL aggregated record at two different concurrency bounds --
    complementary to O2's own test of this, exercised here against a
    result set that spans all four buckets rather than a single one."""
    lane = make_lane(argv=("pytest", "-q"))

    def run(jobs: int):
        project_root = tmp_path / f"proj-{jobs}"
        scratch_root = tmp_path / f"scratch-{jobs}"
        _materialize_project(project_root)
        scratch_root.mkdir()
        decide = _make_decide_by_mutated_content(project_root)
        baseline = execute_command(lane, cwd=project_root, process_runner=decide)
        mutation = run_mutation(
            lane,
            baseline=baseline,
            project_root=project_root,
        repo_top=project_root,
            scratch_root=scratch_root,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=jobs,
            operators=("bool-const-flip",),
            process_runner=decide,
        )
        assert baseline.outcome is Outcome.PASS
        return mutation

    assert run(1).to_dict() == run(3).to_dict()


# --- the shared source tree is provably unchanged ---------------------------


def test_the_shared_source_tree_is_byte_identical_after_all_four_terminal_cases(
    tmp_path: Path,
):
    """Uses the REAL default executor (genuine ``ThreadPoolExecutor``,
    genuine non-deterministic thread scheduling) -- proving BOTH that
    attribution survives real concurrency (a completion-order bug would
    fail this non-deterministically, which the fixed, per-mutant-content
    keyed assertions below would still catch every time it did) AND that
    the shared source is untouched, without asserting anything about
    elapsed time."""
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    _materialize_project(project_root)
    scratch_root.mkdir()
    before = (project_root / "pkg" / "flags.py").read_bytes()
    decide = _make_decide_by_mutated_content(project_root)
    baseline = execute_command(lane, cwd=project_root, process_runner=decide)

    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("bool-const-flip",),
        process_runner=decide,
    )

    assert baseline.outcome is Outcome.PASS
    # all four buckets were genuinely reached by this same run, each
    # correctly attributed regardless of which thread finished first.
    assert mutation.killed == 1
    assert [o.lineno for o in mutation.survived] == [3]
    assert [o.lineno for o in mutation.crashed] == [4]
    assert [o.lineno for o in mutation.budget_exceeded] == [5]

    after = (project_root / "pkg" / "flags.py").read_bytes()
    assert after == before


def test_scratch_copies_are_discarded_after_every_mutant_run(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    _materialize_project(project_root)
    scratch_root.mkdir()
    decide = _make_decide_by_mutated_content(project_root)
    baseline = execute_command(lane, cwd=project_root, process_runner=decide)

    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("bool-const-flip",),
        process_runner=decide,
    )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert list(scratch_root.iterdir()) == []


# --- the bucket sort is what makes ordering adapter-independent --------------


def _always_pass(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")


@dataclass(frozen=True)
class _ReverseOrderAdapter:
    """An adapter that emits its mutants in DELIBERATELY reverse identity
    order. `PythonAdapter.generate_mutants` sorts its own output by
    ``(lineno, operator, description, start)``, and `collect_mutants` sorts
    targets by path -- so with the only real generating adapter in the
    suite, `run_mutation`'s own three bucket sorts can never change
    anything, and removing them passes every other test in this module.

    ``adapters/base.py``'s protocol promises no order at all, though, which
    is what those sorts exist for and what the next generating adapter
    (Go, P23) will exercise for real. This stub is that adapter's stand-in
    today: without the sorts, the recorded survivors come back in
    submission order, which is this adapter's own reverse order.
    """

    name: str = "rev"
    source_globs: tuple[str, ...] = ("*.py",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def generate_mutants(self, text: str, lines: set[int]):
        return tuple(
            Mutant(
                lineno=lineno,
                operator="bool-const-flip",
                description=f"True->False (line {lineno})",
                mutated_text=text.replace(
                    f"    x{lineno} = True", f"    x{lineno} = False"
                ),
            )
            for lineno in sorted(lines, reverse=True)
        )


def test_the_recorded_buckets_are_ordered_by_identity_not_by_submission(
    tmp_path: Path,
):
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    (project_root / "pkg").mkdir(parents=True)
    text = "def flags():\n" + "".join(f"    x{n} = True\n" for n in (2, 3, 4))
    (project_root / "pkg" / "flags.py").write_text(text, encoding="utf-8")
    scratch_root.mkdir()
    targets = (
        MutationTarget(path="pkg/flags.py", text=text, lines=frozenset({2, 3, 4})),
    )
    baseline = execute_command(lane, cwd=project_root, process_runner=_always_pass)

    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=targets,
        adapter=_ReverseOrderAdapter(),
        jobs=1,
        operators=("bool-const-flip",),
        process_runner=_always_pass,
    )

    # everything survives (`_always_pass`), so all three identities land in
    # the one bucket and their ORDER is the whole observation.
    assert [entry.lineno for entry in mutation.survived] == [2, 3, 4]


# --- where the mutant is WRITTEN inside the copy (A-145) ---------------------
#
# A target's `path` is REPO-relative; the scratch tree is a copy of the
# PROJECT root. Those are the same string only when the project IS the
# repository top -- which is what every fixture above happens to be, and
# exactly why the mismatch survived until a real subdirectory project ran.


def test_project_prefix_is_empty_when_the_project_is_the_repository_top(
    tmp_path: Path,
):
    assert project_prefix(repo_top=tmp_path, project_root=tmp_path) == ""


def test_project_prefix_names_the_projects_own_subdirectory(tmp_path: Path):
    (tmp_path / "assay").mkdir()

    assert project_prefix(repo_top=tmp_path, project_root=tmp_path / "assay") == "assay/"


def test_a_nested_project_prefix_uses_forward_slashes(tmp_path: Path):
    (tmp_path / "libs" / "assay").mkdir(parents=True)

    prefix = project_prefix(repo_top=tmp_path, project_root=tmp_path / "libs" / "assay")

    assert prefix == "libs/assay/"


def test_a_repo_relative_target_is_respelled_relative_to_the_project_root():
    assert _within_project("assay/src/mod.py", "assay/") == "src/mod.py"


def test_a_target_outside_the_project_root_is_refused_before_submission():
    """The check earns its place: without it this path is written into the
    copy anyway (or, more often, crashes deep in a worker thread with a
    bare ``FileNotFoundError``), because the copy simply has no counterpart
    for a file that lives outside the directory it was copied from."""
    with pytest.raises(ValueError, match="outside the project root"):
        _within_project("other/src/mod.py", "assay/")
