"""B091/D-23/RW-33/RW-36 -- :mod:`assay.liveness`: the materialized pytest
plugin, the pytest-argv detection rule, the plan-injection function, and
:class:`~assay.liveness.LivenessRunner`'s LAUNCH shape (env stamping, events
path determinism, argv/cwd/timeout forwarding into `Popen`).

Every test here is a UNIT test against `liveness.py` directly -- no real
subprocess, no real pytest run (that lives in session 2's spike transcript,
recorded in the P7 LOG/REPORT, and in a real end-to-end CLI test added
separately). The negative this file defends: a plan whose argv does not
literally invoke pytest must come back byte-identical and unWARNed-when-
`diagnostics=None`; a plan that does must gain EXACTLY the `-p`/`PYTHONPATH`
pair -- appended, per RW-36, never declared -- and nothing else;
`LivenessRunner` must stamp both liveness env vars into the launched child's
env, with an events path that is a deterministic function of `cwd` alone.

**A3's own monitoring-loop CLASSIFICATION logic** (hung vs. budget_exceeded
vs. normal completion, the CPU-tree sampling, the `/proc` failure-is-growth
rule) is a SEPARATE module, `test_liveness_runner_monitor.py` -- this file
stays about what `__call__` launches WITH, that one about what the loop
DECIDES once it is running.
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath

import pytest

from assay import liveness
from assay.errors import Outcome
from assay.runner import CommandPlan, execute_plan


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
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=diagnostics
    )
    assert injection.active is False
    assert injection.plan is plan
    assert injection.reason == "argv-does-not-invoke-pytest"
    assert injection.plugin is None
    assert "WARN" in diagnostics.getvalue()
    assert "pytest" in diagnostics.getvalue()
    assert not (tmp_path / "liveness").exists()


def test_inject_with_diagnostics_none_does_not_raise_and_stays_silent(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("go", "test", "./..."))
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injection.active is False
    assert injection.plan is plan


def test_inject_adds_plugin_flag_to_argv_appended_never_argv_declared(
    tmp_path: Path,
) -> None:
    """(RW-36) The lane's own declared argv is the lane's own words: liveness
    must land in `argv_appended`, and `argv_declared` must stay byte-for-byte
    what the lane wrote -- this is the exact regression session 2's original
    (pre-RW-36) `argv_declared`-mutating cut would fail.
    """
    plan = _plan(argv=("pytest", "-q"), env_effective={})
    liveness_dir = tmp_path / "liveness"
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=liveness_dir, diagnostics=None
    )
    assert injection.active is True
    assert injection.reason == "auto-pytest-argv"
    assert injection.plugin == str(liveness_dir / liveness.LIVENESS_PLUGIN_FILENAME)
    new_plan = injection.plan
    assert new_plan.argv_declared == ("pytest", "-q")
    assert new_plan.argv_appended == ("-p", "assay_liveness_plugin")
    assert new_plan.argv_effective == (
        "pytest",
        "-q",
        "-p",
        "assay_liveness_plugin",
    )
    assert new_plan.env_effective["PYTHONPATH"] == str(liveness_dir)
    assert (liveness_dir / liveness.LIVENESS_PLUGIN_FILENAME).exists()


def test_inject_sets_cli_argv_appended_to_the_pre_injection_appended_tuple(
    tmp_path: Path,
) -> None:
    """(RW-36) `cli_argv_appended` must freeze whatever `argv_appended` held
    BEFORE liveness added its own tokens -- an empty tuple here, since this
    plan declared no CLI passthrough -- so `execute_plan`'s
    `allow_argv_append` refusal keeps testing only the CLI's own tokens.
    """
    plan = _plan(argv=("pytest",))
    assert plan.cli_argv_appended is None
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injection.plan.cli_argv_appended == ()


def test_inject_prepends_to_an_existing_pythonpath(tmp_path: Path) -> None:
    plan = _plan(argv=("pytest",), env_effective={"PYTHONPATH": "/already/here"})
    liveness_dir = tmp_path / "liveness"
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=liveness_dir, diagnostics=None
    )
    assert injection.active is True
    assert injection.plan.env_effective["PYTHONPATH"] == f"{liveness_dir}:/already/here"


def test_inject_preserves_the_lanes_own_appended_tokens_and_recomputes_effective(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("pytest",), argv_appended=("--maxfail=1",))
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injection.active is True
    new_plan = injection.plan
    assert new_plan.argv_declared == ("pytest",)
    assert new_plan.argv_appended == ("--maxfail=1", "-p", "assay_liveness_plugin")
    assert new_plan.argv_effective == (
        "pytest",
        "--maxfail=1",
        "-p",
        "assay_liveness_plugin",
    )
    # (RW-36) `cli_argv_appended` names exactly the lane's own pre-existing
    # CLI-consented tokens -- liveness's own `-p ...` pair is NOT part of it,
    # which is what lets `execute_plan` run this plan even when
    # `allow_argv_append` is false (liveness never requires that consent).
    assert new_plan.cli_argv_appended == ("--maxfail=1",)
    # (A-036 transparency invariant, preserved by construction) allow_argv_append
    # travels through byte-for-byte -- liveness injection never touches the
    # CLI passthrough gate.
    assert new_plan.allow_argv_append == plan.allow_argv_append


def test_inject_other_env_keys_survive_untouched(tmp_path: Path) -> None:
    plan = _plan(argv=("pytest",), env_effective={"FOO": "bar"})
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injection.plan.env_effective["FOO"] == "bar"
    # the ORIGINAL plan's env_effective must not have been mutated in place
    assert "PYTHONPATH" not in plan.env_effective


# --------------------------------------------------------------------------
# inject_liveness_plugin -- judge.mutation.liveness policy (RW-36)
# --------------------------------------------------------------------------


def test_inject_liveness_false_is_off_unconditionally_no_warn(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("pytest", "-q"))
    diagnostics = io.StringIO()
    injection = liveness.inject_liveness_plugin(
        plan,
        liveness_dir=tmp_path / "liveness",
        diagnostics=diagnostics,
        liveness_policy=liveness.LIVENESS_FALSE,
    )
    assert injection.active is False
    assert injection.plan is plan
    assert injection.reason == "declared-false"
    assert injection.plugin is None
    # an explicit, written-down opt-out is not a surprise -- no WARN.
    assert diagnostics.getvalue() == ""
    assert not (tmp_path / "liveness").exists()


def test_inject_liveness_true_forces_injection_when_argv_invokes_pytest(
    tmp_path: Path,
) -> None:
    plan = _plan(argv=("pytest", "-q"))
    injection = liveness.inject_liveness_plugin(
        plan,
        liveness_dir=tmp_path / "liveness",
        diagnostics=None,
        liveness_policy=liveness.LIVENESS_TRUE,
    )
    assert injection.active is True
    assert injection.reason == "declared-true"
    assert injection.plan.argv_appended == ("-p", "assay_liveness_plugin")


def test_inject_liveness_true_on_a_non_pytest_argv_is_off_with_a_warn(
    tmp_path: Path,
) -> None:
    """Defensive only -- `config._load_mutation` already refuses this
    combination at load time -- but this function's own contract must not
    depend on the loader never being bypassed by a hand-built `Lane`.
    """
    plan = _plan(argv=("tox",))
    diagnostics = io.StringIO()
    injection = liveness.inject_liveness_plugin(
        plan,
        liveness_dir=tmp_path / "liveness",
        diagnostics=diagnostics,
        liveness_policy=liveness.LIVENESS_TRUE,
    )
    assert injection.active is False
    assert injection.reason == "argv-does-not-invoke-pytest"
    assert "WARN" in diagnostics.getvalue()


def test_inject_liveness_true_on_a_non_pytest_argv_with_diagnostics_none(
    tmp_path: Path,
) -> None:
    """Same defensive case, `diagnostics=None` -- must not raise, and must
    stay silent (nothing to print to)."""
    plan = _plan(argv=("tox",))
    injection = liveness.inject_liveness_plugin(
        plan,
        liveness_dir=tmp_path / "liveness",
        diagnostics=None,
        liveness_policy=liveness.LIVENESS_TRUE,
    )
    assert injection.active is False
    assert injection.reason == "argv-does-not-invoke-pytest"


@pytest.mark.parametrize("policy", [None, liveness.LIVENESS_AUTO])
def test_inject_liveness_auto_and_omitted_policy_behave_identically(
    tmp_path: Path, policy: str | None
) -> None:
    plan = _plan(argv=("pytest",))
    injection = liveness.inject_liveness_plugin(
        plan,
        liveness_dir=tmp_path / "liveness",
        diagnostics=None,
        liveness_policy=policy,
    )
    assert injection.active is True
    assert injection.reason == "auto-pytest-argv"


def test_liveness_injection_to_wire_shape(tmp_path: Path) -> None:
    plan = _plan(argv=("pytest",))
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    wire = injection.to_wire()
    assert wire == {
        "active": True,
        "reason": "auto-pytest-argv",
        "plugin": str(tmp_path / "liveness" / liveness.LIVENESS_PLUGIN_FILENAME),
    }
    assert set(wire) == {"active", "reason", "plugin"}


# --------------------------------------------------------------------------
# LivenessRunner -- env stamping + launch (A3: active Popen loop; the
# CLASSIFICATION logic itself -- hung/budget_exceeded/normal-completion --
# is covered in `test_liveness_runner_monitor.py`, a separate module so this
# one stays focused on "what does __call__ launch with").
# --------------------------------------------------------------------------


class _FakeProc:
    """A fake ``subprocess.Popen`` handle: exits with *returncode* after
    *poll_after_ticks* `poll()` calls have returned `None` -- `0` means "the
    very first poll already reports done", which is all these launch-shape
    tests need (the monitor loop checks `poll()` BEFORE it ever reads the
    events file, samples CPU, or sleeps, so a zero-tick fake never touches
    any of that machinery).
    """

    def __init__(self, pid: int, *, returncode: int = 0, poll_after_ticks: int = 0) -> None:
        self.pid = pid
        self.returncode = returncode
        self._remaining_ticks = poll_after_ticks

    def poll(self):
        if self._remaining_ticks > 0:
            self._remaining_ticks -= 1
            return None
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _FakePopen:
    def __init__(self, *, returncode: int = 0, poll_after_ticks: int = 0) -> None:
        self.calls: list[dict[str, object]] = []
        self._returncode = returncode
        self._poll_after_ticks = poll_after_ticks
        self.procs: list[_FakeProc] = []

    def __call__(self, argv, *, env, cwd, stdout, stderr, start_new_session):
        self.calls.append(
            {
                "argv": tuple(argv),
                "env": dict(env),
                "cwd": cwd,
                "start_new_session": start_new_session,
            }
        )
        proc = _FakeProc(
            pid=1000 + len(self.procs),
            returncode=self._returncode,
            poll_after_ticks=self._poll_after_ticks,
        )
        self.procs.append(proc)
        return proc


def test_liveness_runner_creates_events_dir_on_init(tmp_path: Path) -> None:
    events_dir = tmp_path / "candidates"
    assert not events_dir.exists()
    liveness.LivenessRunner(events_dir=events_dir, expect_next_event_within_s=60.0)
    assert events_dir.is_dir()


def test_liveness_runner_stamps_env_and_forwards_everything_else(
    tmp_path: Path,
) -> None:
    popen = _FakePopen()
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates",
        expect_next_event_within_s=60.0,
        popen=popen,
    )
    cwd = tmp_path / "candidate-a"
    cwd.mkdir()
    result = runner(("pytest", "-q"), env={"AMBIENT": "1"}, cwd=cwd, timeout=42.0)
    assert result.returncode == 0
    assert len(popen.calls) == 1
    call = popen.calls[0]
    assert call["argv"] == ("pytest", "-q")
    assert call["cwd"] == cwd
    assert call["start_new_session"] is True
    assert call["env"]["AMBIENT"] == "1"
    assert call["env"][liveness.ASSAY_LIVENESS_EXIT_ENV] == "1"
    events_path = Path(call["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV])
    assert events_path.parent == tmp_path / "candidates"


def test_liveness_runner_events_path_is_deterministic_per_cwd(
    tmp_path: Path,
) -> None:
    popen = _FakePopen()
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates",
        expect_next_event_within_s=60.0,
        popen=popen,
    )
    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    cwd_a.mkdir()
    cwd_b.mkdir()
    runner(("pytest",), env={}, cwd=cwd_a, timeout=None)
    runner(("pytest",), env={}, cwd=cwd_a, timeout=None)
    runner(("pytest",), env={}, cwd=cwd_b, timeout=None)
    path_a1 = popen.calls[0]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    path_a2 = popen.calls[1]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    path_b = popen.calls[2]["env"][liveness.ASSAY_LIVENESS_EVENTS_ENV]
    assert path_a1 == path_a2
    assert path_a1 != path_b


def test_liveness_runner_none_timeout_passes_through(tmp_path: Path) -> None:
    """`timeout=None` (an unbounded lane, B067) must never make the loop
    treat the budget as expired -- the elapsed-budget check is skipped
    entirely when `timeout is None`, so a zero-tick fake process still
    completes normally rather than being killed on the very first poll.
    """
    popen = _FakePopen()
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates",
        expect_next_event_within_s=60.0,
        popen=popen,
    )
    cwd = tmp_path / "candidate-a"
    cwd.mkdir()
    result = runner(("pytest",), env={}, cwd=cwd, timeout=None)
    assert result.returncode == 0


# --------------------------------------------------------------------------
# plugin_source_hash
# --------------------------------------------------------------------------


def test_plugin_source_hash_is_stable_and_nonempty() -> None:
    first = liveness.plugin_source_hash()
    second = liveness.plugin_source_hash()
    assert first == second
    assert len(first) == 64  # sha256 hex digest length


class _FakeReport:
    def __init__(self, *, when: str, nodeid: str, outcome: str, duration) -> None:
        self.when = when
        self.nodeid = nodeid
        self.outcome = outcome
        self.duration = duration


def test_materialized_plugin_writes_valid_json_events(tmp_path: Path, monkeypatch) -> None:
    """(P7 session 5, real bug found by the end-to-end fixture test, never
    by a unit test) The materialized plugin used to build each NDJSON line
    with `%r` (`repr()`) on `nodeid`/`outcome`/`duration_s` -- Python's
    `repr()` of a string is SINGLE-quoted, not JSON's double-quoted string
    syntax, and `repr(None)` is the bare token `None`, not JSON's `null`.
    Every line the plugin ever wrote was therefore invalid JSON, silently
    swallowed by every downstream `json.loads` as a "tolerated torn line" --
    `compute_expect_next_event_within_s` never found a real `slowest_test_s`
    (always the coarse fallback) and `_read_events_progress` never saw a
    real `session_finish`. No existing test caught this because every OTHER
    test in this package hand-constructs its own valid-JSON event fixtures
    rather than running the plugin's own code. This test imports the
    MATERIALIZED plugin module (the exact file a real pytest process would
    load via `-p assay_liveness_plugin`) and calls its hooks directly --
    real plugin code, a fake `report`/`exitstatus` only, no real pytest
    subprocess (this file's own stated scope, see the module docstring)."""
    import importlib.util

    liveness_dir = tmp_path / "liveness"
    plugin_path = liveness.materialize_liveness_plugin(liveness_dir)
    spec = importlib.util.spec_from_file_location("assay_liveness_plugin_under_test", plugin_path)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    events_path = tmp_path / "events.ndjson"
    monkeypatch.setenv(liveness.ASSAY_LIVENESS_EVENTS_ENV, str(events_path))

    # `when="setup"` must be a no-op (only `"call"` is recorded) -- and a
    # `None` duration (a real, if rare, `TestReport.duration` value) must
    # round-trip through `json.dumps` as JSON `null`, never the bare Python
    # token `None` the old `%r` formatting produced.
    plugin.pytest_runtest_logreport(_FakeReport(when="setup", nodeid="x", outcome="passed", duration=0.1))
    plugin.pytest_runtest_logreport(
        _FakeReport(when="call", nodeid="pkg/test_mod.py::test_it", outcome="passed", duration=0.0125)
    )
    plugin.pytest_runtest_logreport(_FakeReport(when="call", nodeid="y", outcome="failed", duration=None))
    plugin.pytest_sessionfinish(session=None, exitstatus=0)

    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # the "setup" report never wrote a line.
    import json as _json

    first = _json.loads(lines[0])  # Raises if this is not valid JSON.
    assert first == {
        "event": "test",
        "nodeid": "pkg/test_mod.py::test_it",
        "outcome": "passed",
        "duration_s": 0.0125,
        "t": first["t"],
    }
    assert isinstance(first["t"], float)
    second = _json.loads(lines[1])
    assert second["nodeid"] == "y"
    assert second["duration_s"] is None  # JSON `null`, not the string "None".
    third = _json.loads(lines[2])
    assert third["event"] == "session_finish"
    assert third["exitstatus"] == 0
    # Every character in every line is ASCII/UTF-8 double-quoted JSON --
    # `json.loads` above already proves this, but the explicit `"` check
    # pins the regression symptom directly: the old format's `nodeid` field
    # read `'pkg/test_mod.py::test_it'` (single-quoted), which this asserts
    # can never reappear.
    assert "'pkg/test_mod.py::test_it'" not in lines[0]
    assert '"pkg/test_mod.py::test_it"' in lines[0]


# --------------------------------------------------------------------------
# integration: execute_plan's allow_argv_append refusal (RW-36)
# --------------------------------------------------------------------------


def test_liveness_injected_plan_runs_through_execute_plan_despite_no_consent(
    tmp_path: Path,
) -> None:
    """The whole point of `cli_argv_appended` (RW-36): a plan whose ONLY
    appended tokens are liveness's own `-p ...` pair must NOT be refused by
    `execute_plan`'s `allow_argv_append` check, even though `allow_argv_append`
    is `False` and `argv_appended` is genuinely non-empty. Before this
    session's `cli_argv_appended` field existed, routing liveness through
    `argv_appended` (as RW-36 requires) would have made EVERY liveness-active
    candidate refuse with ERROR/EXEC_FAILED -- this is the regression test
    for that.
    """
    plan = _plan(argv=("pytest", "-q"))
    assert plan.allow_argv_append is False
    injection = liveness.inject_liveness_plugin(
        plan, liveness_dir=tmp_path / "liveness", diagnostics=None
    )
    assert injection.active is True

    def _fake_runner(argv, *, env, cwd, timeout):
        import subprocess

        assert "-p" in argv and "assay_liveness_plugin" in argv
        return subprocess.CompletedProcess(args=list(argv), returncode=0, stdout="", stderr="")

    result = execute_plan(
        injection.plan,
        cwd=tmp_path,
        timeout=30.0,
        process_runner=_fake_runner,
    )
    assert result.outcome is Outcome.PASS


def test_a_plan_with_real_unconsented_cli_appended_argv_is_still_refused(
    tmp_path: Path,
) -> None:
    """The other half of the same property: liveness's own bypass must NOT
    also swallow a genuine unconsented CLI `--` append -- `allow_argv_append`
    keeps meaning exactly what it always meant for tokens that are not
    liveness's own.
    """
    plan = CommandPlan(
        argv_declared=("pytest", "-q"),
        argv_appended=("--maxfail=1",),
        argv_effective=("pytest", "-q", "--maxfail=1"),
        env_declared={},
        env_effective={},
        env_passthrough=(),
        allow_argv_append=False,
        budget_seconds=60.0,
        project_prefix=PurePosixPath("."),
    )
    result = execute_plan(
        plan,
        cwd=tmp_path,
        timeout=30.0,
        process_runner=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("process_runner must not be called")
        ),
    )
    assert result.outcome is Outcome.ERROR

