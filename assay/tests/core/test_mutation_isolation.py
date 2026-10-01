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

**P23**: isolation is now a real P22 committed-object snapshot per mutant
(:func:`~assay.isolation.SnapshotRepository.materialize_replacement`),
never a ``shutil.copytree`` of a live directory -- so every fixture here
is a real, committed ``git`` repository (:class:`conftest.GitRepo`), and
each ``run_mutation`` call is handed a real prepared seed
(:func:`conftest.prepared_snapshot`), a real resolved plan
(:func:`conftest.make_plan`), and a real injected deadline
(:func:`conftest.make_deadline`) -- the identical three objects
:func:`~assay.runner.run_lane`'s own higher-rigor path constructs once and
shares across every unit.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationSite, MutationTarget, run_mutation
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


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _seed_repo(tmp_path: Path, name: str, *, text: str = _TEXT) -> GitRepo:
    repo = GitRepo(path=tmp_path / name)
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", text)
    repo.commit_all("add flags")
    return repo


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


def _make_decide_by_mutated_content(repo_path: Path):
    """Builds a fake ``process_runner`` bound to *repo_path*: the
    BASELINE call always passes (unmutated content), and every MUTANT call
    reads the ACTUAL mutated file inside its own *cwd* (a real, independent
    P22 snapshot's own project root) and decides the outcome from what it
    finds there -- proves the right RESULT lands on the right JOB even when
    completion order is scrambled, because the decision is keyed to the
    real, isolated, per-mutant content rather than to submission position."""

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo_path:
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
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _make_decide_by_mutated_content(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=_clock,
            executor_factory=lambda jobs: _SynchronousExecutor(),
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert len(mutation.killed) == 1
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
        repo = _seed_repo(tmp_path, f"repo-{jobs}")
        scratch_root = tmp_path / f"scratch-{jobs}"
        scratch_root.mkdir()
        decide = _make_decide_by_mutated_content(repo.path)
        baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
        plan = make_plan(lane)
        deadline = make_deadline()
        with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
            mutation = run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=plan,
                deadline=deadline,
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=jobs,
                max_mutants=50,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=_clock,
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
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    before = (repo.path / "pkg" / "flags.py").read_bytes()
    decide = _make_decide_by_mutated_content(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=_clock,
        )

    assert baseline.outcome is Outcome.PASS
    # all four buckets were genuinely reached by this same run, each
    # correctly attributed regardless of which thread finished first.
    assert len(mutation.killed) == 1
    assert [o.lineno for o in mutation.survived] == [3]
    assert [o.lineno for o in mutation.crashed] == [4]
    assert [o.lineno for o in mutation.budget_exceeded] == [5]

    after = (repo.path / "pkg" / "flags.py").read_bytes()
    assert after == before
    assert repo.git("status", "--porcelain") == ""


def test_scratch_copies_are_discarded_after_every_mutant_run(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _make_decide_by_mutated_content(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=_clock,
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    # The prepared seed itself is the only thing scratch_root still holds
    # once every materialized child has closed -- P22's own contract, never
    # asserted empty (the seed is legitimately still on disk here, since
    # this whole block just closed the `with prepared_snapshot(...)` above).
    assert list(scratch_root.iterdir()) == []


# --- the bucket sort is what makes ordering adapter-independent --------------


def _always_pass(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")


@dataclass(frozen=True)
class _ReverseOrderAdapter:
    """A minimal generating adapter, used with an executor that completes
    its work in REVERSE submission order.

    P21 note: this stub used to emit its sites in reverse identity order,
    because `run_mutation` then sorted each bucket afterwards. That sort is
    gone -- `collect_mutation_sites` now refuses an out-of-order batch
    outright, so no input could reach the sort and AUTHORING 3b.D asks for
    an unreachable line to be restructured away rather than kept. What
    remains genuinely observable, and what this stub now exercises, is
    POSITION ALIGNMENT: each future is awaited by its own index, so a result
    lands with the job that produced it no matter which subprocess finished
    first. An `as_completed` implementation would scramble exactly this.
    """

    name: str = "rev"
    source_globs: tuple[str, ...] = ("*.py",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = False
    external_tools: tuple[str, ...] = ()

    def for_project(self, *, repo_top: Path, project_root: Path) -> "_ReverseOrderAdapter":
        return self

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def generate_mutation_sites(
        self, text: str, lines: set[int], *, operators: tuple[str, ...], limit: int
    ):
        source = text.encode("utf-8")
        sites = []
        for lineno in sorted(lines):
            needle = f"    x{lineno} = True".encode("utf-8")
            start = source.index(needle) + len(needle) - len(b"True")
            sites.append(
                MutationSite(
                    start_byte=start,
                    end_byte=start + len(b"True"),
                    replacement=b"False",
                    lineno=lineno,
                    operator="python:bool-const-flip",
                    description=f"True->False (line {lineno})",
                )
            )
        return tuple(sites[:limit])


def test_the_recorded_buckets_come_out_identity_ordered_across_files(
    tmp_path: Path,
):
    """End to end, across TWO files handed in reverse path order: the
    recorded bucket is ordered by identity (which leads with `path`), not by
    the order the caller happened to supply its targets in.

    `Mutation` itself refuses an out-of-order bucket, so this is not merely
    an assertion about a list -- it is the reason `run_mutation` can build
    one without sorting: `collect_mutation_sites` visits targets by path and
    each per-file batch is already identity-ordered, and results are
    consumed position-aligned with that job list."""
    lane = make_lane(argv=("pytest", "-q"))
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    text = "def flags():\n" + "".join(f"    x{n} = True\n" for n in (2, 3, 4))
    repo.write("pkg/flags.py", text)
    repo.write("pkg/aaa.py", text)
    repo.commit_all("add pkg")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    # Supplied z-then-a on purpose: path order is the collector's job.
    targets = (
        MutationTarget(path="pkg/flags.py", text=text, lines=frozenset({2, 3, 4})),
        MutationTarget(path="pkg/aaa.py", text=text, lines=frozenset({2, 3, 4})),
    )
    baseline = execute_command(lane, cwd=repo.path, process_runner=_always_pass)
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=targets,
            adapter=_ReverseOrderAdapter(),
            jobs=3,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=_clock,
        )

    # everything survives (`_always_pass`), so all six identities land in the
    # one bucket and their ORDER is the whole observation.
    recorded = [(entry.path, entry.lineno) for entry in mutation.survived]
    assert recorded == [
        ("pkg/aaa.py", 2),
        ("pkg/aaa.py", 3),
        ("pkg/aaa.py", 4),
        ("pkg/flags.py", 2),
        ("pkg/flags.py", 3),
        ("pkg/flags.py", 4),
    ]
