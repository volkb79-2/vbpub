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

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    completed_process,
    make_deadline,
    make_lane,
    make_plan,
    make_r3_judge,
    prepared_snapshot,
)

from assay import canary, contention, runner
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
    return completed_process(list(argv), 0, "", "")


def _r3_lane(
    repo: GitRepo,
    *,
    mechanism: str = "import-break",
    env_passthrough: tuple[str, ...] = (),
):
    return make_lane(
        rigor=("R0", "R3"),
        judge=make_r3_judge(
            language="python",
            source_root_paths=(repo.path / "pkg",),
            canary=CanaryConfig(mechanism=mechanism, target="pkg/mod.py"),
        ),
        argv=("check",),
        env_passthrough=env_passthrough,
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
        return completed_process(list(argv), 0, "", "")

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
    assert r3.canary.attempts[0].control_outcome is Outcome.PASS
    assert r3.canary.attempts[0].transformed_outcome is None
    assert r3.canary.attempts[0].expected_reason_code is ReasonCode.COMMAND_FAILED
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
        return completed_process(list(argv), 0, "", "")

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
        return completed_process(list(argv), 0, "", "")

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
    assert result.attempts[0].control_outcome is Outcome.PASS
    assert result.attempts[0].transformed_outcome is None
    assert result.attempts[0].expected_reason_code is None
    assert "not a known canary mechanism" in result.attempts[0].description
    assert "import-break" in result.attempts[0].description and "uncovered-line" in result.attempts[0].description


@pytest.mark.parametrize(
    (
        "limited_phase",
        "resource_limit_hit",
        "transform_diagnostic",
        "diagnostic_stream",
    ),
    [
        ("control", True, "cause", "stderr"),
        ("transform", True, "cause", "stderr"),
        ("transform", False, "cause", "stderr"),
        ("transform", False, "cause", "stdout"),
        ("transform", False, "echoed-marker", "stderr"),
        ("transform", False, "nonce-less-copy", "stderr"),
        ("transform", False, "unrelated", "stderr"),
        ("none", False, "echoed-marker", "stderr"),
        ("none", False, "nonce-unrelated", "stderr"),
        ("none", False, "wrong-path", "stderr"),
    ],
)
@pytest.mark.parametrize("observer_declared", [False, True])
def test_canary_contention_distinguishes_limits_from_expected_transform_failure(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch,
    limited_phase: str,
    resource_limit_hit: bool,
    transform_diagnostic: str,
    diagnostic_stream: str,
    observer_declared: bool,
):
    monkeypatch.setenv("ASSAY_GATE_REQUIRE_RESOURCE_OBSERVATION", "1")
    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", "/observer.sock")
    head_rev = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _r3_lane(
        git_repo,
        env_passthrough=("ASSAY_RESOURCE_OBSERVER_SOCKET",) if observer_declared else (),
    )

    class Window:
        def __init__(self, observation):
            self.observation = observation

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    affected = contention.ContentionObservation(
        "affected",
        resource_limit_hit=resource_limit_hit,
        reasons=(
            "pids-or-memory-oom-event"
            if resource_limit_hit
            else "cgroup_psi.cpu.some_us=1",
        ),
    )
    clear = contention.ContentionObservation("clear")
    window_observations = iter(
        (affected, clear)
        if limited_phase == "control"
        else (clear, affected)
        if limited_phase == "transform"
        else (clear, clear)
    )
    observer_paths: list[str | None] = []

    def window(**kwargs):
        observer_paths.append(kwargs["observer_socket_path"])
        return Window(next(window_observations))

    monkeypatch.setattr(
        canary.contention,
        "ContentionWindow",
        window,
    )

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text()
        match = re.search(
            r'raise AssertionError\("(assay-canary-import-break:[0-9a-f]+)"\)',
            source,
        )
        transformed = match is not None
        diagnostic = ""
        if transformed:
            token = match.group(1)
            injected_line = next(
                index
                for index, text in enumerate(source.splitlines(), start=1)
                if token in text
            )
            if transform_diagnostic == "cause":
                diagnostic = (
                    f"pkg/mod.py:{injected_line}: in <module>\n"
                    f'    raise AssertionError("{token}")\n'
                    f"E   AssertionError: {token}\n"
                )
            elif transform_diagnostic == "wrong-path":
                diagnostic = (
                    f"other/pkg/mod.py:{injected_line}: in <module>\n"
                    f'    raise AssertionError("{token}")\n'
                    f"E   AssertionError: {token}\n"
                )
            elif transform_diagnostic == "echoed-marker":
                diagnostic = (
                    "assay-canary-import-break\n"
                    "E   AssertionError: unrelated test assertion\n"
                )
            elif transform_diagnostic == "nonce-less-copy":
                diagnostic = (
                    f"pkg/mod.py:{injected_line}: in <module>\n"
                    '    raise AssertionError("assay-canary-import-break")\n'
                    "E   AssertionError: assay-canary-import-break\n"
                )
            elif transform_diagnostic == "nonce-unrelated":
                diagnostic = (
                    f"{token}\n"
                    "tests/test_other.py:77: in test_unrelated\n"
                    "E   AssertionError: unrelated failure\n"
                )
            else:
                diagnostic = "unrelated pytest assertion failure\n"
        diagnostic_stdout = diagnostic if diagnostic_stream == "stdout" else ""
        diagnostic_stderr = diagnostic if diagnostic_stream == "stderr" else ""
        return completed_process(
            list(argv),
            1 if transformed else 0,
            diagnostic_stdout,
            diagnostic_stderr,
        )

    with prepared_snapshot(git_repo, commit=head_rev, scratch_root=scratch) as prepared:
        result = run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(
                lane,
                passthrough_source={
                    "ASSAY_RESOURCE_OBSERVER_SOCKET": "/observer.sock"
                },
            ),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process,
            clock=_clock,
        )

    assert observer_paths == [
        "/observer.sock" if observer_declared else None,
        "/observer.sock" if observer_declared else None,
    ]
    attempt = result.attempts[0]
    assert attempt.control_outcome is (
        Outcome.INCONCLUSIVE if limited_phase == "control" else Outcome.PASS
    )
    if limited_phase == "control":
        assert attempt.transformed_outcome is Outcome.FAIL
        assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
        assert (
            "control result withheld by cgroup resource window affected"
            in attempt.description
        )
        assert canary.judge_attempt(attempt) == (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        )
    elif resource_limit_hit:
        assert attempt.transformed_outcome is Outcome.INCONCLUSIVE
        assert attempt.observed_reason_code is ReasonCode.CANARY_INCONCLUSIVE
        assert (
            "transform result withheld by cgroup resource window affected"
            in attempt.description
        )
        assert canary.judge_attempt(attempt) == (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        )
    else:
        if transform_diagnostic == "cause":
            # The random token, exact injected source line, and native
            # traceback frame establish the intended import-break cause. PSI
            # alone must not overturn that completed canary signal.
            assert attempt.transformed_outcome is Outcome.FAIL
            assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
            if limited_phase == "transform":
                assert "resource window" not in attempt.description
            assert canary.judge_attempt(attempt) == (Outcome.PASS, None)
        elif limited_phase == "none":
            assert attempt.transformed_outcome is Outcome.FAIL
            assert attempt.observed_reason_code is ReasonCode.CANARY_SURVIVED
            assert canary.judge_attempt(attempt) == (
                Outcome.FAIL,
                ReasonCode.CANARY_SURVIVED,
            )
        else:
            # An echoed public marker, nonce-less copied source, a nonce
            # echoed beside another stack, and an unrelated assertion do not
            # prove that the transformed source caused COMMAND_FAILED.
            assert attempt.transformed_outcome is Outcome.INCONCLUSIVE
            assert attempt.observed_reason_code is ReasonCode.CANARY_INCONCLUSIVE
            assert (
                "transform result withheld by cgroup resource window affected"
                in attempt.description
            )
            assert canary.judge_attempt(attempt) == (
                Outcome.INCONCLUSIVE,
                ReasonCode.CANARY_INCONCLUSIVE,
            )


def test_contention_note_retains_measured_pressure_and_bounds_unknown_details():
    affected = canary._contention_note(
        "control",
        contention.ContentionObservation(
            "affected", reasons=("cgroup_psi.cpu.some_us=27",)
        ),
    )
    unknown = canary._contention_note(
        "transform",
        contention.ContentionObservation(
            "unknown", reasons=("after-sample-unavailable:/sensitive/path",)
        ),
    )

    assert affected == (
        "control result withheld by cgroup resource window affected "
        "(cgroup_psi.cpu.some_us=27)"
    )
    assert unknown == (
        "transform result withheld by cgroup resource window unknown "
        "(after-sample-unavailable:/sensitive/path)"
    )


def test_pressure_affected_failing_control_is_inconclusive(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ASSAY_GATE_REQUIRE_RESOURCE_OBSERVATION", "1")
    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", "/observer.sock")
    head_rev = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _r3_lane(
        git_repo,
        env_passthrough=("ASSAY_RESOURCE_OBSERVER_SOCKET",),
    )

    class Window:
        def __init__(self, observation):
            self.observation = observation

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    observations = iter(
        (
            contention.ContentionObservation(
                "affected", reasons=("cgroup_psi.cpu.some_us=200000",)
            ),
            contention.ContentionObservation("clear"),
        )
    )
    monkeypatch.setattr(
        canary.contention,
        "ContentionWindow",
        lambda **kwargs: Window(next(observations)),
    )

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text()
        match = re.search(
            r'raise AssertionError\("(assay-canary-import-break:[0-9a-f]+)"\)',
            source,
        )
        if match is None:
            return completed_process(list(argv), 1, "unrelated control failure", "")
        token = match.group(1)
        injected_line = next(
            index
            for index, text in enumerate(source.splitlines(), start=1)
            if token in text
        )
        diagnostic = (
            f"pkg/mod.py:{injected_line}: in <module>\n"
            f'    raise AssertionError("{token}")\n'
            f"E   AssertionError: {token}\n"
        )
        return completed_process(list(argv), 1, diagnostic, "")

    with prepared_snapshot(git_repo, commit=head_rev, scratch_root=scratch) as prepared:
        result = run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(
                lane,
                passthrough_source={
                    "ASSAY_RESOURCE_OBSERVER_SOCKET": "/observer.sock"
                },
            ),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process,
            clock=_clock,
        )

    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.INCONCLUSIVE
    assert attempt.transformed_outcome is Outcome.FAIL
    assert canary.judge_attempt(attempt) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )


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
