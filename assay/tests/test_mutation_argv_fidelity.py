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
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from conftest import make_lane

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationTarget, run_mutation
from assay.runner import execute_command

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


def test_baseline_and_every_mutant_receive_byte_identical_argv(tmp_path: Path):
    lane = make_lane(argv=_DECLARED_ARGV)
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "lib").mkdir()
    (project_root / "pkg" / "a.py").write_text(_TEXT_A, encoding="utf-8")
    (project_root / "lib" / "b.py").write_text(_TEXT_B, encoding="utf-8")
    scratch_root.mkdir()
    recorder = _RecordingProcessRunner()
    baseline = execute_command(lane, cwd=project_root, process_runner=recorder)

    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("compare-swap",),
        process_runner=recorder,
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


def test_cwd_is_the_only_thing_that_varies_between_calls(tmp_path: Path):
    lane = make_lane(argv=_DECLARED_ARGV)
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "lib").mkdir()
    (project_root / "pkg" / "a.py").write_text(_TEXT_A, encoding="utf-8")
    (project_root / "lib" / "b.py").write_text(_TEXT_B, encoding="utf-8")
    scratch_root.mkdir()
    recorder = _RecordingProcessRunner()
    baseline = execute_command(lane, cwd=project_root, process_runner=recorder)

    run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("compare-swap",),
        process_runner=recorder,
    )

    cwds = [call.cwd for call in recorder.calls]
    assert cwds[0] == project_root, "the baseline runs against the real project root"
    mutant_cwds = cwds[1:]
    assert len(set(mutant_cwds)) == len(mutant_cwds) == 2, (
        "every mutant gets its own distinct scratch cwd"
    )
    assert project_root not in mutant_cwds
    for cwd in mutant_cwds:
        assert scratch_root in cwd.parents
