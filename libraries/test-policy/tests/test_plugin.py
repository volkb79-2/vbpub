import builtins
import os
import subprocess
import threading
import types

import pytest

from estate_test_policy import plugin, policy

ARGS = ["-p", "estate_test_policy.plugin", "-p", "no:cacheprovider"]


@pytest.fixture
def run(pytester):
    pytester.makeini("[pytest]\nestate_policy_settle_seconds = 0\n")

    def go(body, *extra):
        pytester.makepyfile(test_sample=body)
        return pytester.runpytest_inprocess(*ARGS, *extra)

    return go


def test_clean_test_passes(run):
    run("def test_a():\n    assert 1 + 1 == 2\n").assert_outcomes(passed=1)


def test_fixed_sleep_fails_with_the_rule_named(run):
    result = run("import time\ndef test_a():\n    time.sleep(1)\n")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*estate-test-policy:*", "*[[]fixed-sleep[]] line 3*"])


def test_allow_marker_with_reason_and_backlog_permits(run):
    body = (
        "import time, pytest\n"
        "@pytest.mark.estate_allow('fixed-sleep', reason='kernel timer granularity probe', backlog='TP-01')\n"
        "def test_a():\n    time.sleep(1)\n"
    )
    run(body).assert_outcomes(passed=1)


def test_allow_marker_without_reason_is_itself_a_failure(run):
    body = (
        "import time, pytest\n"
        "@pytest.mark.estate_allow('fixed-sleep')\n"
        "def test_a():\n    time.sleep(1)\n"
    )
    result = run(body)
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*requires reason*"])


def test_allow_for_a_different_rule_does_not_cover(run):
    body = (
        "import time, pytest\n"
        "@pytest.mark.estate_allow('short-timeout', reason='a documented reason', backlog='TP-01')\n"
        "def test_a():\n    time.sleep(1)\n"
    )
    run(body).assert_outcomes(errors=1)


def test_warn_mode_warns_instead_of_failing(run):
    result = run("import time\ndef test_a():\n    time.sleep(1)\n", "--estate-policy=warn")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*[[]fixed-sleep[]] line 3*"])


def test_off_mode_checks_nothing(run):
    run("import time\ndef test_a():\n    time.sleep(1)\n", "--estate-policy=off").assert_outcomes(passed=1)


@pytest.fixture
def held(monkeypatch):
    """Resources the generated tests leak on purpose, held until THIS test releases them.

    Nothing here depends on elapsed time: the leaked thread blocks on an event and the leaked child blocks reading a
    pipe, so they are alive exactly until the finalizer below runs, however long the host stalls. The generated
    sources reach the holder through the builtin name ``ESTATE_HELD``.
    """
    holder = types.SimpleNamespace(release=threading.Event(), things=[])
    monkeypatch.setattr(builtins, "ESTATE_HELD", holder, raising=False)
    yield holder
    holder.release.set()
    for thing in holder.things:
        if isinstance(thing, subprocess.Popen):
            thing.stdin.close()
            thing.wait()
        elif isinstance(thing, int):
            os.close(thing)


def test_leaked_thread_fails_at_teardown(run, held):
    body = (
        "import threading\n"
        "def nap():\n    ESTATE_HELD.release.wait()\n"
        "def test_a():\n"
        "    threading.Thread(target=nap, name='leaky', daemon=True).start()\n"
    )
    result = run(body)
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*thread still running: leaky#*"])


def test_leaked_fd_and_child_are_both_reported(run, held):
    body = (
        "import os, subprocess\n"
        "def test_a():\n"
        "    ESTATE_HELD.things.append(os.open('/dev/null', os.O_RDONLY))\n"
        "    ESTATE_HELD.things.append(subprocess.Popen(['cat'], stdin=subprocess.PIPE))\n"
    )
    result = run(body)
    result.stdout.fnmatch_lines(["*file descriptor still open*", "*child process still present*"])


def test_thread_that_has_stopped_is_not_a_leak(run):
    body = (
        "import threading\n"
        "def test_a():\n"
        "    worker = threading.Thread(target=lambda: None)\n"
        "    worker.start()\n"
        "    worker.join()\n"
    )
    run(body).assert_outcomes(passed=1)


def test_leak_can_be_allowed(run, held):
    body = (
        "import threading, pytest\n"
        "def nap():\n    ESTATE_HELD.release.wait()\n"
        "@pytest.mark.estate_allow('resource-leak', reason='daemon outlives the test by design', backlog='TP-02')\n"
        "def test_a():\n"
        "    threading.Thread(target=nap, daemon=True).start()\n"
    )
    run(body).assert_outcomes(passed=1)


def test_take_snapshot_sees_current_thread_and_tolerates_vanishing_entries(monkeypatch):
    snap = plugin.take_snapshot()
    assert any(t.startswith("MainThread#") for t in snap.threads)
    real_readlink, real_open = os.readlink, open

    def bad_readlink(path):
        raise OSError("gone")

    monkeypatch.setattr(os, "readlink", bad_readlink)
    assert plugin.take_snapshot().fds == frozenset()
    monkeypatch.setattr(os, "readlink", real_readlink)

    def bad_open(path, *a, **k):
        if str(path).endswith("/children"):
            raise OSError("gone")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", bad_open)
    assert plugin.take_snapshot().children == frozenset()


def test_static_lines_without_function_or_source():
    assert plugin._static_lines(types.SimpleNamespace(), set(), 0.2) == []
    ns = {}
    exec("def f():\n    pass\n", ns)
    assert plugin._static_lines(types.SimpleNamespace(function=ns["f"]), set(), 0.2) == []


def test_settle_loop_waits_for_a_leak_that_goes_away(monkeypatch):
    before = policy.Snapshot(frozenset(), frozenset(), frozenset())
    leaked = policy.Snapshot(frozenset({"x#1"}), frozenset(), frozenset())
    seen = iter([leaked, leaked, before])  # the leak disappears on the third look, whatever the clock says
    monkeypatch.setattr(plugin, "take_snapshot", lambda: next(seen))
    assert plugin._settled_leaks(before, 3600) == []


def test_settle_loop_gives_up_after_the_deadline(monkeypatch):
    before = policy.Snapshot(frozenset(), frozenset(), frozenset())
    leaked = policy.Snapshot(frozenset({"x#1"}), frozenset(), frozenset())
    monkeypatch.setattr(plugin, "take_snapshot", lambda: leaked)
    assert plugin._settled_leaks(before, 0.1) == ["thread still running: x#1"]
