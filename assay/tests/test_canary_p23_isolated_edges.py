"""Phase-2 reviewer coverage of `run_isolated_canary`'s three refusal edges.

`canary.py`'s P23 rewrite left four branches unreached by both the project
suite and the byte-locked packet: a canary target whose committed bytes are
not valid UTF-8, a transform that produces no change, an unrecognised
mechanism name, and the control half leaving Git-visible state behind (that
last one is covered in `test_runner_p23_combined_axis_review.py`).

The first three are all "nothing to judge" shapes, and each has to render a
CONSTRUCTIBLE result rather than raise past the caller or invent a canary
judgement nobody made. Two are driven through `run_lane`; the unrecognised
mechanism is reachable only by calling the public
`assay.canary.run_isolated_canary` directly, because `assay.config`'s closed
`CANARY_MECHANISMS` vocabulary refuses the name at load time -- so the
function's own guard is a public-surface contract, not dead code, and is
tested as one.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    make_r3_judge,
    prepared_snapshot,
)

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.canary import run_isolated_canary
from assay.config import CanaryConfig
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


def _pass_everything(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), 0, "", "")


def _r3_lane(repo: GitRepo, *, mechanism: str = "import-break"):
    return make_lane(
        rigor=("R0", "R3"),
        judge=make_r3_judge(
            language="python",
            source_root_paths=(repo.path / "pkg",),
            canary=CanaryConfig(mechanism=mechanism, target="pkg/mod.py"),
        ),
        argv=("check",),
    )


def _seed(repo: GitRepo) -> str:
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f():\n    return 1\n")
    return repo.commit_all("seed a canary target")


class _NoOpTransformAdapter(PythonAdapter):
    """A real adapter whose import-break injection returns the text it was
    given. Not contrived: an adapter can legitimately decide a particular file
    has nothing to break (an empty module, a stub, a file whose only content
    is already an unconditional raise), and the honest answer is then "nothing
    to judge", never a transform half run against identical bytes."""

    def inject_import_break(self, text: str) -> tuple[str, str]:
        return text, "injected nothing at all"


def test_a_no_op_transform_judges_nothing_and_runs_no_transform_unit(
    git_repo: GitRepo,
):
    """O3's "malformed/no-op canary" row: the existing complete
    ``CANARY_INCONCLUSIVE`` result, with the control's own outcome recorded,
    the expected cause still named, and NO second snapshot materialised."""
    head_rev = _seed(git_repo)
    units: list[Path] = []

    def process(argv, *, env, cwd, timeout):
        units.append(Path(cwd))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        _r3_lane(git_repo),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=_NoOpTransformAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert len(units) == 2, "the lane baseline and the control only"
    r3 = verdict.claims[1]
    assert (r3.status, r3.reason_code) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert r3.canary is not None
    assert r3.canary.control_outcome is Outcome.PASS
    assert r3.canary.transformed_outcome is None
    assert r3.canary.expected_reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.judgment is not None and verdict.judgment.r3 is not None


def test_a_non_utf8_canary_target_is_a_payload_free_r3_claim(git_repo: GitRepo):
    """The canary target's bytes are read once from the prepared seed under
    the same strict UTF-8 rule R2's own target reads use. A target assay
    cannot decode has no transform, so the claim is the payload-free
    ``ERROR``/``UNREADABLE_ARTIFACT`` pair -- never a canary judgement, and
    never an exception escaping ``run_lane``."""
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    (repo.path / "pkg/mod.py").write_bytes(b'BAD = "\xff\xfe"\n')
    head_rev = repo.commit_all("seed an undecodable canary target")
    units = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal units
        units += 1
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        _r3_lane(repo),
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert units == 2, "the baseline and the control ran; no transform unit did"
    assert verdict.claims[0].status is Outcome.PASS
    r3 = verdict.claims[1]
    assert (r3.status, r3.reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )
    assert r3.canary is None
    assert verdict.judgment is None


def test_an_unrecognised_mechanism_judges_nothing_on_the_public_surface(
    git_repo: GitRepo, tmp_path: Path
):
    """``run_isolated_canary`` is public (it is in ``assay.canary.__all__``),
    and ``assay.config``'s closed vocabulary cannot reach this branch, so the
    guard is a contract for direct callers rather than dead code: an
    unrecognised mechanism name yields a complete ``CanaryResult`` naming the
    mechanisms that DO exist, with the control's real outcome preserved and no
    transform ever attempted."""
    head_rev = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _r3_lane(git_repo)
    units = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal units
        units += 1
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    with prepared_snapshot(git_repo, commit=head_rev, scratch_root=scratch) as prepared:
        result = run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="not-a-real-mechanism",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process,
            clock=_clock,
        )

    assert units == 1, "the control ran; nothing was transformed"
    assert result.control_outcome is Outcome.PASS
    assert result.transformed_outcome is None
    assert result.expected_reason_code is None
    assert "not a known canary mechanism" in result.description
    assert "import-break" in result.description and "uncovered-line" in result.description


def test_a_test_path_canary_target_is_still_refused_before_any_unit(
    git_repo: GitRepo, tmp_path: Path
):
    """The control that keeps the three "nothing to judge" cases above from
    being satisfiable by a function that never materialises anything: the one
    prerequisite refusal still fires first, as a raised ``AssayError``, before
    a single snapshot or process exists."""
    head_rev = _seed(git_repo)
    git_repo.write("pkg/test_thing.py", "def test_x():\n    assert True\n")
    head_rev = git_repo.commit_all("add a test file")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _r3_lane(git_repo)

    def process(*args, **kwargs):
        raise AssertionError("a test-path target must be refused before any unit")

    with prepared_snapshot(git_repo, commit=head_rev, scratch_root=scratch) as prepared:
        with pytest.raises(AssayError) as caught:
            run_isolated_canary(
                lane,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                project_root=git_repo.path,
                resolved_base=None,
                mechanism="import-break",
                target="pkg/test_thing.py",
                adapter=PythonAdapter(),
                process_runner=process,
                clock=_clock,
            )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
