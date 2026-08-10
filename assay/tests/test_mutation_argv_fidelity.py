"""O5 -- the baseline and EVERY mutant invocation receive the lane's
declared argv byte-for-byte; the only thing that varies between calls is
``cwd``, never derived from which file was mutated.

The negative this defends (O5, verbatim): *deriving `tests/test_<module>`
from a mutated source path changes the fake runner's recorded argv and
fails the paired two-source fixture.* Proven with a PAIRED two-source
fixture (two files, at two DIFFERENT paths) -- if argv were ever derived
from a source path (the exact anti-pattern A-012 already deleted from
nyxloom's own reference), the two files' own mutant calls would record
DIFFERENT argv from each other and from the baseline; here every recorded
argv is identical.

**P23**: both the baseline and every mutant now run through ONE shared,
already-resolved :class:`~assay.runner.CommandPlan` (:func:`conftest.
make_plan`) against a real P22 committed snapshot (:func:`conftest.
prepared_snapshot`) -- exactly what makes "byte-identical argv" a fact
about the SHARED plan object rather than something each call could
independently re-derive.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationTarget, run_mutation
from assay.runner import execute_plan

_DECLARED_ARGV = ("pytest", "tests", "-q", "--maxfail=1")

_TEXT_A = "def is_a(x):\n    if x > 0:\n        return True\n    return False\n"
_TEXT_B = "def is_b(y):\n    if y == 0:\n        return True\n    return False\n"

#: A PAIRED two-source fixture: two files at two DIFFERENT paths, each
#: contributing one mutant -- the exact shape O5's own negative names.
_TARGETS = (
    MutationTarget(path="pkg/a.py", text=_TEXT_A, lines=frozenset({2})),
    MutationTarget(path="lib/b.py", text=_TEXT_B, lines=frozenset({2})),
)


@dataclass(frozen=True)
class _Call:
    argv: tuple[str, ...]
    cwd: Path


class _RecordingProcessRunner:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append(_Call(argv=tuple(argv), cwd=Path(cwd)))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")


def _seed_repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/a.py", _TEXT_A)
    repo.write("lib/b.py", _TEXT_B)
    repo.commit_all("add pkg and lib")
    return repo


def test_baseline_and_every_mutant_receive_byte_identical_argv(tmp_path: Path):
    lane = make_lane(argv=_DECLARED_ARGV)
    repo = _seed_repo(tmp_path)
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    recorder = _RecordingProcessRunner()
    plan = make_plan(lane)
    deadline = make_deadline()
    baseline = execute_plan(plan, cwd=repo.path, timeout=60.0, process_runner=recorder)

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
            operators=("compare-swap",),
            process_runner=recorder,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 2
    # one baseline call plus one call per mutant, from TWO different files.
    assert len(recorder.calls) == 3

    argvs = {call.argv for call in recorder.calls}
    assert argvs == {_DECLARED_ARGV}, (
        f"every call must declare the identical argv regardless of which "
        f"file was mutated, got {argvs}"
    )
    assert baseline.plan.argv_declared == _DECLARED_ARGV
    assert baseline.plan.argv_effective == _DECLARED_ARGV
    assert baseline.plan is plan


def test_cwd_is_the_only_thing_that_varies_between_calls(tmp_path: Path):
    lane = make_lane(argv=_DECLARED_ARGV)
    repo = _seed_repo(tmp_path)
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    recorder = _RecordingProcessRunner()
    plan = make_plan(lane)
    deadline = make_deadline()
    baseline = execute_plan(plan, cwd=repo.path, timeout=60.0, process_runner=recorder)

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("compare-swap",),
            process_runner=recorder,
            clock=lambda: datetime.now(timezone.utc),
        )

    cwds = [call.cwd for call in recorder.calls]
    assert cwds[0] == repo.path, "the baseline runs against the real committed repo"
    mutant_cwds = cwds[1:]
    assert len(set(mutant_cwds)) == len(mutant_cwds) == 2, (
        "every mutant gets its own distinct scratch cwd"
    )
    assert repo.path not in mutant_cwds
    for cwd in mutant_cwds:
        assert scratch_root.resolve() in cwd.resolve().parents
