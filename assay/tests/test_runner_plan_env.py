"""O2 — the child receives exactly lane env plus declared passthrough values
and no ambient sentinel; argv_declared/appended/effective are exact; and an
append attempted without ``allow_argv_append`` is rejected before execution.

The negative this defends (verbatim): *merging os.environ leaks the sentinel;
omitting append bookkeeping changes the expected artifact; ignoring the
permission runs the sentinel command.*

Two levels of proof on purpose:

* :func:`~assay.runner.resolve_command_plan` is exercised directly with an
  INJECTED ``passthrough_source`` dict, never real ``os.environ`` mutation
  (AUTHORING.md §3b.B — a test must not mutate process-global state).
* one test drives the REAL default process runner (an actual child) so "no
  ambient leak" is proven against the genuine ``subprocess`` boundary, not
  only against assay's own bookkeeping agreeing with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import make_lane

from assay import runner
from assay.errors import Outcome, ReasonCode


# --- env: declared-only, plus exactly the named passthrough ------------------


def test_env_effective_is_env_declared_when_no_passthrough_is_named():
    lane = make_lane(env={"MOCK_MODE": "true"}, env_passthrough=())

    plan = runner.resolve_command_plan(lane, passthrough_source={"SENTINEL": "leak"})

    assert dict(plan.env_declared) == {"MOCK_MODE": "true"}
    assert dict(plan.env_effective) == {"MOCK_MODE": "true"}
    assert "SENTINEL" not in plan.env_effective


def test_env_effective_adds_only_the_declared_passthrough_names():
    lane = make_lane(env={"MOCK_MODE": "true"}, env_passthrough=("PATH", "HOME"))
    source = {"PATH": "/usr/bin", "HOME": "/root", "SENTINEL": "leak"}

    plan = runner.resolve_command_plan(lane, passthrough_source=source)

    assert dict(plan.env_effective) == {
        "MOCK_MODE": "true",
        "PATH": "/usr/bin",
        "HOME": "/root",
    }
    assert "SENTINEL" not in plan.env_effective, "ambient name not declared, must not leak"


def test_a_declared_passthrough_name_absent_from_the_source_is_simply_omitted():
    lane = make_lane(env={}, env_passthrough=("NEVER_SET",))

    plan = runner.resolve_command_plan(lane, passthrough_source={})

    assert dict(plan.env_effective) == {}


def test_passthrough_source_defaults_to_os_environ(monkeypatch):
    # The one place this module touches real os.environ, via monkeypatch,
    # which restores it automatically -- proving the PRODUCTION default
    # without leaving process-global state mutated after the test.
    monkeypatch.setenv("ASSAY_TEST_PASSTHROUGH_PROBE", "seen")
    lane = make_lane(env={}, env_passthrough=("ASSAY_TEST_PASSTHROUGH_PROBE",))

    plan = runner.resolve_command_plan(lane)

    assert dict(plan.env_effective) == {"ASSAY_TEST_PASSTHROUGH_PROBE": "seen"}


# --- the REAL child receives exactly env_effective, nothing merged in -------


def test_the_real_child_receives_exactly_env_effective_and_no_ambient_leak(
    tmp_path: Path,
):
    # `/usr/bin/env` (absolute path -- no PATH lookup needed) prints the
    # process environment verbatim, with none of CPython's own PEP 538 locale
    # coercion, which would otherwise inject an LC_CTYPE the child interpreter
    # adds to ITS OWN os.environ and that has nothing to do with what assay
    # passed it -- a false positive this test must not chase.
    lane = make_lane(env={"MOCK_MODE": "true"}, env_passthrough=("SENTINEL_PASS",))
    plan = runner.resolve_command_plan(
        lane, passthrough_source={"SENTINEL_PASS": "yes", "SENTINEL_LEAK": "no"}
    )

    proc = runner.default_process_runner(
        ["/usr/bin/env"],
        env=plan.env_effective,
        cwd=tmp_path,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    child_env = dict(
        line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
    )
    assert child_env == dict(plan.env_effective)
    assert "SENTINEL_LEAK" not in child_env, "ambient os.environ leaked into the child"


# --- argv: declared/appended/effective are exact ------------------------------


def test_argv_effective_is_declared_when_nothing_is_appended():
    lane = make_lane(argv=("pytest", "tests", "-q"))

    plan = runner.resolve_command_plan(lane)

    assert plan.argv_declared == ("pytest", "tests", "-q")
    assert plan.argv_appended == ()
    assert plan.argv_effective == ("pytest", "tests", "-q")


def test_argv_effective_is_declared_plus_appended_in_order():
    lane = make_lane(argv=("pytest", "tests", "-q"))

    plan = runner.resolve_command_plan(lane, argv_append=("-k", "not slow"))

    assert plan.argv_appended == ("-k", "not slow")
    assert plan.argv_effective == ("pytest", "tests", "-q", "-k", "not slow")


# --- append permission: rejected before execution, permitted when allowed ---


def test_append_without_permission_is_rejected_before_execution(tmp_path: Path):
    lane = make_lane(argv=("/bin/sh", "-c", "exit 0"), allow_argv_append=False)
    called = []

    def spy_runner(argv, *, env, cwd, timeout):
        called.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = runner.execute_command(
        lane, argv_append=("--extra",), cwd=tmp_path, process_runner=spy_runner
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert called == [], "the process must never have been started"
    # The attempted effective argv is still recorded -- transparency about
    # what was ASKED for, even though it never ran (A-036).
    assert result.plan.argv_appended == ("--extra",)
    assert result.plan.argv_effective == ("/bin/sh", "-c", "exit 0", "--extra")


def test_append_with_permission_is_actually_executed(tmp_path: Path):
    # `sh -c '<cmd>' <arg0>` binds the first argument AFTER the command string
    # to $0 -- so the appended marker path becomes $0, and the command only
    # touches it if the appended argv genuinely reached the child.
    marker = tmp_path / "APPEND_RAN"
    lane = make_lane(argv=("/bin/sh", "-c", 'touch "$0"'), allow_argv_append=True)

    result = runner.execute_command(lane, argv_append=(str(marker),), cwd=tmp_path)

    assert result.outcome is Outcome.PASS
    assert marker.exists(), "a permitted append must actually reach the child"


def test_no_append_and_permission_disabled_still_runs_normally(tmp_path: Path):
    # allow_argv_append=false must not block an UNAPPENDED run -- it only
    # gates an actual append attempt.
    lane = make_lane(argv=("/bin/sh", "-c", "exit 0"), allow_argv_append=False)

    result = runner.execute_command(lane, cwd=tmp_path)

    assert result.outcome is Outcome.PASS
