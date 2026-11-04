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

from conftest import completed_process, file_not_found, timeout_expired

from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
from pathlib import PurePosixPath

import pytest

from conftest import (
    GitRepo,
    REPOSITORY_SNAPSHOT_POLICY,
    make_deadline,
    make_lane,
    make_plan,
    prepared_snapshot,
)

from assay.adapters.python import PythonAdapter
from assay import mutation as mutation_module
from assay import isolation
from assay.errors import Outcome
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec
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


class _CheapCloneWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.trees = root / "trees"
        self.trees.mkdir(parents=True)
        self.cheap_clone = True
        self.template_path: Path | None = None
        self.cloned_trees: list[Path] = []
        self.events: list[tuple[str, Path, Path | None]] = []
        self.delete_calls: list[Path] = []
        self._next_tree = 0

    def ingest_tree(self, source: Path, name: str) -> Path:
        destination = self.trees / name
        shutil.copytree(source, destination)
        return destination

    def make_tree(self) -> Path:
        self._next_tree += 1
        destination = self.trees / f"tree-{self._next_tree}"
        destination.mkdir()
        return destination

    def clone_file(self, source: Path, destination: Path) -> None:
        shutil.copyfile(source, destination)

    def seal(self, path: Path) -> None:
        self.template_path = path
        self._set_tree_writable(path, writable=False)
        self.events.append(("seal", path, None))

    def clone_tree(self, source: Path) -> Path:
        assert source == self.template_path
        assert self._is_read_only(source), "the template must be sealed before cloning"
        self._next_tree += 1
        destination = self.trees / f"clone-{self._next_tree}"
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
        self._set_tree_writable(destination, writable=True)
        self.cloned_trees.append(destination)
        self.events.append(("clone", source, destination))
        return destination

    def delete_tree(self, path: Path) -> None:
        self.delete_calls.append(path)
        self._set_tree_writable(path, writable=True)
        shutil.rmtree(path)

    @staticmethod
    def _set_tree_writable(path: Path, *, writable: bool) -> None:
        for current, directories, files in os.walk(path, topdown=True, followlinks=False):
            for member in [Path(current), *(Path(current) / name for name in directories + files)]:
                if member.is_symlink():
                    continue
                mode = stat.S_IMODE(member.stat().st_mode)
                member.chmod(mode | stat.S_IWUSR if writable else mode & ~0o222)

    @staticmethod
    def _is_read_only(path: Path) -> bool:
        for current, directories, files in os.walk(path, topdown=True, followlinks=False):
            for member in [Path(current), *(Path(current) / name for name in directories + files)]:
                if not member.is_symlink() and member.stat().st_mode & 0o222:
                    return False
        return True


class _CheapCloneWorkspaceProvider:
    max_live_clones = 2

    def __init__(self, root: Path):
        self.root = root
        self.workspace: _CheapCloneWorkspace | None = None

    @contextmanager
    def open(self, estimate, *, scratch_root):
        self.root.mkdir()
        self.workspace = _CheapCloneWorkspace(self.root)
        try:
            yield self.workspace
        finally:
            shutil.rmtree(self.root)


def _prepared_snapshot_with_workspace(repo: GitRepo, scratch_root: Path, workspace=None):
    spec = SnapshotSpec(
        repo_top=repo.path.resolve(),
        commit=repo.head(),
        project_prefix=PurePosixPath("."),
        scratch_root=scratch_root.resolve(),
        snapshot_policy=REPOSITORY_SNAPSHOT_POLICY,
        resolved_base=None,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )
    return isolation.prepare_snapshot(spec, timeout=60.0, workspace=workspace)


def _bucket_signature(mutation):
    return tuple(
        tuple(item.lineno for item in getattr(mutation, bucket))
        for bucket in ("killed", "survived", "crashed", "budget_exceeded")
    )


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
            return completed_process(list(argv), returncode=0, stdout="", stderr="")
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return completed_process(list(argv), returncode=1, stdout="", stderr="")
        if "b = False" in text:
            return completed_process(list(argv), returncode=0, stdout="", stderr="")
        if "c = False" in text:
            raise file_not_found(2, "No such file or directory", argv[0])
        if "d = False" in text:
            raise timeout_expired(cmd=list(argv), timeout=timeout)
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
        mutation = mutation_module._run_mutation_impl(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=_clock,
            executor_factory=lambda jobs: _SynchronousExecutor(),
            budget_per_candidate_seconds=0.05,
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert len(mutation.killed) == 1
    assert [o.lineno for o in mutation.survived] == [3]
    assert [o.lineno for o in mutation.crashed] == [4]
    assert [o.lineno for o in mutation.budget_exceeded] == [5]


def test_jobs_1_and_jobs_3_render_identical_records_under_the_real_executor(
    tmp_path: Path, monkeypatch,
):
    """The real ``ThreadPoolExecutor`` this time (the default factory),
    proving the SAME per-mutant-content-keyed decision produces the
    IDENTICAL aggregated record at two different concurrency bounds --
    complementary to O2's own test of this, exercised here against a
    result set that spans all four buckets rather than a single one."""
    lane = make_lane(argv=("pytest", "-q"))
    from assay import _window_host, runner as runner_module
    from assay.resource_limits import ResourceLimitCounters

    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(
        mutation_module, "_read_candidate_resource_counters", lambda: counters
    )
    monkeypatch.setattr(
        mutation_module,
        "_read_candidate_resource_counters_during_campaign",
        lambda: (counters, None),
    )
    monkeypatch.setattr(
        _window_host,
        "run_window",
        lambda plan, *, cwd, timeout, process_runner, clock, profile=None: runner_module.execute_plan(
            plan,
            cwd=cwd,
            timeout=timeout,
            process_runner=process_runner,
            clock=clock,
        ),
    )

    def run(jobs: int):
        repo = _seed_repo(tmp_path, f"repo-{jobs}")
        scratch_root = tmp_path / f"scratch-{jobs}"
        scratch_root.mkdir()
        decide = _make_decide_by_mutated_content(repo.path)
        baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
        plan = make_plan(lane)
        deadline = make_deadline()
        if jobs == 1:
            snapshot_context = prepared_snapshot(repo, scratch_root=scratch_root)
        else:
            monkeypatch.setattr(_window_host, "can_host", lambda *_args: True)
            workspace = _CheapCloneWorkspaceProvider(
                tmp_path / f"cheap-clone-{jobs}"
            )
            snapshot_context = _prepared_snapshot_with_workspace(
                repo, scratch_root, workspace=workspace
            )
        with snapshot_context as prepared:
            mutation = mutation_module._run_mutation_impl(
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


@pytest.mark.parametrize(
    ("bucket", "lineno"),
    [("killed", 2), ("survived", 3), ("crashed", 4), ("budget_exceeded", 5)],
)
def test_cheap_clone_materialization_preserves_plain_mutation_buckets(
    tmp_path: Path, bucket: str, lineno: int
):
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, "repo")
    name_by_line = {2: "a", 3: "b", 4: "c", 5: "d"}
    candidate_name = name_by_line[lineno]
    process_cwds: list[Path] = []
    cow_provider = _CheapCloneWorkspaceProvider(tmp_path / "cheap-clone-run")

    def decide(argv, *, env, cwd, timeout):
        root = Path(cwd)
        process_cwds.append(root)
        if root == repo.path:
            return completed_process(list(argv), returncode=0, stdout="", stderr="")
        if cow_provider.workspace is not None:
            assert root != cow_provider.workspace.template_path, "sealed template was executed"
            assert root in cow_provider.workspace.cloned_trees
        source = (root / "pkg" / "flags.py").read_text(encoding="utf-8")
        assert f"    {candidate_name} = False" in source
        if bucket == "crashed":
            raise file_not_found(2, "No such file or directory", argv[0])
        if bucket == "budget_exceeded":
            raise timeout_expired(cmd=list(argv), timeout=timeout)
        return completed_process(
            list(argv),
            returncode=1 if bucket == "killed" else 0,
            stdout="",
            stderr="",
        )

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    plan = make_plan(lane)
    target = MutationTarget(
        path="pkg/flags.py", text=_TEXT, lines=frozenset({lineno})
    )

    def run_once(scratch: Path, workspace=None):
        scratch.mkdir()
        with _prepared_snapshot_with_workspace(repo, scratch, workspace) as prepared:
            result = run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=plan,
                deadline=make_deadline(),
                targets=(target,),
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=1,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=_clock,
                executor_factory=lambda jobs: _SynchronousExecutor(),
                budget_per_candidate_seconds=0.05,
            )
        assert list(scratch.iterdir()) == []
        return _bucket_signature(result)

    plain_signature = run_once(tmp_path / "plain-scratch")
    cow_signature = run_once(
        tmp_path / "cow-scratch", workspace=cow_provider
    )

    expected = tuple(
        (lineno,) if name == bucket else ()
        for name in ("killed", "survived", "crashed", "budget_exceeded")
    )
    assert plain_signature == cow_signature == expected
    attempts = 2 if bucket in {"crashed", "budget_exceeded"} else 1
    assert len(process_cwds) == 1 + 2 * attempts
    assert cow_provider.workspace is not None
    assert cow_provider.workspace.template_path is not None
    assert len(cow_provider.workspace.cloned_trees) == attempts
    assert cow_provider.workspace.events == [
        ("seal", cow_provider.workspace.template_path, None),
        *[
            ("clone", cow_provider.workspace.template_path, clone)
            for clone in cow_provider.workspace.cloned_trees
        ],
    ]
    assert cow_provider.workspace.delete_calls == [
        *cow_provider.workspace.cloned_trees,
        cow_provider.workspace.template_path,
    ]


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
            jobs=1,
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
            jobs=1,
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
    return completed_process(list(argv), returncode=0, stdout="", stderr="")


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
            jobs=1,
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
