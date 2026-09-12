"""B091/D-23/RW-33 -- :mod:`assay.liveness`: the materialized pytest plugin,
the pytest-argv detection rule, the plan-injection function, and
:class:`~assay.liveness.LivenessRunner`'s v1 (env-stamping only) scope.

Every test here is a UNIT test against `liveness.py` directly -- no real
subprocess, no real pytest run (that lives in this session's spike transcript,
recorded in the P7 LOG/REPORT, and in a real end-to-end CLI test added
separately). The negative this file defends: a plan whose argv does not
literally invoke pytest must come back byte-identical and unWARNed-when-
`diagnostics=None`; a plan that does must gain EXACTLY the `-p`/`PYTHONPATH`
pair and nothing else; `LivenessRunner` must stamp both liveness env vars and
pass everything else through untouched, with a events path that is a
deterministic function of `cwd` alone.
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath

import pytest

from assay import liveness
from assay.runner import CommandPlan


def _plan(
    argv: tuple[str, ...] = ("pytest", "-q"),
    *,
    env_effective: dict[str, str] | None = None,
    argv_appended: tuple[str, ...] = (),
) -> CommandPlan:
    env = {} if env_effective is None else env_effective
    return CommandPlan(
        argv_declared=argv,
        argv_appended=argv_appended,
        argv_effective=argv + argv_appended,
        env_declared={},
        env_effective=env,
        env_passthrough=(),
        allow_argv_append=bool(argv_appended),
        budget_seconds=60.0,
        project_prefix=PurePosixPath("."),
    )


# --------------------------------------------------------------------------
# argv_invokes_pytest
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "-q"),
        ("pytest",),
        ("/usr/bin/pytest", "-q"),
        ("./venv/bin/pytest",),
        ("python", "-m", "pytest", "-q"),
        ("python3.11", "-m", "pytest"),
        # A bare "pytest" token matches WHEREVER it appears (the spec is "a
        # token equal to pytest", not "the first token") -- this is a
        # deliberately permissive rule, not a positional one.
        ("python", "script.py", "pytest"),
    ],
)
def test_argv_invokes_pytest_true_cases(argv: tuple[str, ...]) -> None:
    assert liveness.argv_invokes_pytest(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("python", "-m", "unittest"),
        ("echo", "pytest-ish"),
        ("python", "-m"),
        ("-m", "unittest"),
        ("tox",),
    ],
)
def test_argv_invokes_pytest_false_cases(argv: tuple[str, ...]) -> None:
    assert liveness.argv_invokes_pytest(argv) is False


# --------------------------------------------------------------------------
# materialize_liveness_plugin
# --------------------------------------------------------------------------


def test_materialize_creates_dir_and_file_when_absent(tmp_path: Path) -> None:
    target_dir = tmp_path / "nested" / "liveness"
    path = liveness.materialize_liveness_plugin(target_dir)
    assert path == target_dir / liveness.LIVENESS_PLUGIN_FILENAME
    assert path.read_text(encoding="utf-8") == liveness._PLUGIN_SOURCE


def test_materialize_overwrites_when_content_differs(tmp_path: Path) -> None:
    target_dir = tmp_path / "liveness"
    target_dir.mkdir()
    stale = target_dir / liveness.LIVENESS_PLUGIN_FILENAME
    stale.write_text("stale content, not the real plugin\n", encoding="utf-8")
    liveness.materialize_liveness_plugin(target_dir)
    assert stale.read_text(encoding="utf-8") == liveness._PLUGIN_SOURCE


def test_materialize_skips_write_when_content_already_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "liveness"
    liveness.materialize_liveness_plugin(target_dir)

    def _forbidden_write(self: Path, *_a: object, **_k: object) -> int:
        raise AssertionError("write_text must not be called when content matches")

    monkeypatch.setattr(Path, "write_text", _forbidden_write)
    # Must not raise -- the early-return (content already matches) path is
    # taken, never falling through to the forbidden write.
    liveness.materialize_liveness_plugin(target_dir)


def test_materialize_treats_an_unreadable_existing_file_as_a_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "liveness"
    target_dir.mkdir()
    stale = target_dir / liveness.LIVENESS_PLUGIN_FILENAME
    stale.write_text("irrelevant", encoding="utf-8")

    def _raise_read(self: Path, *_a: object, **_k: object) -> str:
        raise OSError("simulated unreadable file")

    # read_text raising must not propagate -- it is treated as "content
    # differs" (the honest default: an unreadable file is never assumed to
    # already match) and the write proceeds via the real write_text.
    monkeypatch.setattr(Path, "read_text", _raise_read)
    liveness.materialize_liveness_plugin(target_dir)
    monkeypatch.undo()
    assert stale.read_text(encoding="utf-8") == liveness._PLUGIN_SOURCE


# --------------------------------------------------------------------------
# inject_liveness_plugin
# --------------------------------------------------------------------------


def test_inject_returns_plan_unchanged_when_argv_is_not_pytest(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("go", "test", "./..."))
    diagnostics = io.StringIO()
    new_plan, injected = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=diagnostics
    )
    assert injected is False
    assert new_plan is plan
    assert "WARN" in diagnostics.getvalue()
    assert "pytest" in diagnostics.getvalue()
    assert not (tmp_path / "liveness").exists()


def test_inject_with_diagnostics_none_does_not_raise_and_stays_silent(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("go", "test", "./..."))
    new_plan, injected = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injected is False
    assert new_plan is plan


def test_inject_adds_plugin_flag_and_pythonpath_when_no_existing_pythonpath(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("pytest", "-q"), env_effective={})
    liveness_dir = tmp_path / "liveness"
    new_plan, injected = liveness.inject_liveness_plugin(
        plan, liveness_dir=liveness_dir, diagnostics=None
    )
    assert injected is True
    assert new_plan.argv_declared == ("pytest", "-q", "-p", "assay_liveness_plugin")
    assert new_plan.argv_effective == new_plan.argv_declared
    assert new_plan.env_effective["PYTHONPATH"] == str(liveness_dir)
    assert (liveness_dir / liveness.LIVENESS_PLUGIN_FILENAME).exists()


def test_inject_prepends_to_an_existing_pythonpath(tmp_path: Path) -> None:
    plan = _plan(argv=("pytest",), env_effective={"PYTHONPATH": "/already/here"})
    liveness_dir = tmp_path / "liveness"
    new_plan, injected = liveness.inject_liveness_plugin(
        plan, liveness_dir=liveness_dir, diagnostics=None
    )
    assert injected is True
    assert new_plan.env_effective["PYTHONPATH"] == f"{liveness_dir}:/already/here"


def test_inject_preserves_argv_appended_and_recomputes_effective(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("pytest",), argv_appended=("--maxfail=1",))
    new_plan, injected = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injected is True
    assert new_plan.argv_appended == ("--maxfail=1",)
    assert new_plan.argv_declared == ("pytest", "-p", "assay_liveness_plugin")
    assert new_plan.argv_effective == (
        "pytest",
        "-p",
        "assay_liveness_plugin",
        "--maxfail=1",
    )
    # (A-036 transparency invariant, preserved by construction) allow_argv_append
    # travels through byte-for-byte -- liveness injection never touches the
    # CLI passthrough gate.
    assert new_plan.allow_argv_append == plan.allow_argv_append


def test_inject_other_env_keys_survive_untouched(tmp_path: Path) -> None:
    plan = _plan(argv=("pytest",), env_effective={"FOO": "bar"})
    new_plan, _ = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert new_plan.env_effective["FOO"] == "bar"
    # the ORIGINAL plan's env_effective must not have been mutated in place
    assert "PYTHONPATH" not in plan.env_effective


# --------------------------------------------------------------------------
# LivenessRunner (v1: env-stamping only, delegates to `inner`)
# --------------------------------------------------------------------------


class _FakeInner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append(
            {"argv": tuple(argv), "env": dict(env), "cwd": cwd, "timeout": timeout}
        )
        import subprocess

        return subprocess.CompletedProcess(
            args=list(argv), returncode=0, stdout="", stderr=""
        )


def test_liveness_runner_creates_events_dir_on_init(tmp_path: Path) -> None:
    events_dir = tmp_path / "candidates"
    assert not events_dir.exists()
    liveness.LivenessRunner(events_dir=events_dir, inner=_FakeInner())
    assert events_dir.is_dir()


def test_liveness_runner_stamps_env_and_forwards_everything_else(
    tmp_path: Path,
) -> None:
    inner = _FakeInner()
    runner = liveness.LivenessRunner(events_dir=tmp_path / "candidates", inner=inner)
    cwd = tmp_path / "candidate-a"
    result = runner(
        ("pytest", "-q"), env={"AMBIENT": "1"}, cwd=cwd, timeout=42.0
    )
    assert result.returncode == 0
    assert len(inner.calls) == 1
    call = inner.calls[0]
    assert call["argv"] == ("pytest", "-q")
    assert call["cwd"] == cwd
    assert call["timeout"] == 42.0
    assert call["env"]["AMBIENT"] == "1"
    assert call["env"][liveness.ASSAY_LIVENESS_EXIT_ENV] == "1"
    events_path = Path(call["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV])
    assert events_path.parent == tmp_path / "candidates"


def test_liveness_runner_events_path_is_deterministic_per_cwd(
    tmp_path: Path,
) -> None:
    inner = _FakeInner()
    runner = liveness.LivenessRunner(events_dir=tmp_path / "candidates", inner=inner)
    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    runner(("pytest",), env={}, cwd=cwd_a, timeout=None)
    runner(("pytest",), env={}, cwd=cwd_a, timeout=None)
    runner(("pytest",), env={}, cwd=cwd_b, timeout=None)
    path_a1 = inner.calls[0]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    path_a2 = inner.calls[1]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    path_b = inner.calls[2]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    assert path_a1 == path_a2
    assert path_a1 != path_b


def test_liveness_runner_none_timeout_passes_through(tmp_path: Path) -> None:
    inner = _FakeInner()
    runner = liveness.LivenessRunner(events_dir=tmp_path / "candidates", inner=inner)
    runner(("pytest",), env={}, cwd=tmp_path, timeout=None)
    assert inner.calls[0]["timeout"] is None


# --------------------------------------------------------------------------
# plugin_source_hash
# --------------------------------------------------------------------------


def test_plugin_source_hash_is_stable_and_nonempty() -> None:
    first = liveness.plugin_source_hash()
    second = liveness.plugin_source_hash()
    assert first == second
    assert len(first) == 64  # sha256 hex digest length
