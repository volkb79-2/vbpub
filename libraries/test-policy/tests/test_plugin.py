import builtins
import os
import shlex
import threading
import time
import types

import pytest

from estate_test_policy import children, containment, plugin, policy

ARGS = ["-p", "estate_test_policy.plugin", "-p", "no:cacheprovider"]


@pytest.fixture
def run(pytester):
    pytester.makeini("[pytest]\nestate_policy = fail\nestate_policy_settle_seconds = 0\n")

    def go(body, *extra):
        pytester.makepyfile(test_sample=body)
        return pytester.runpytest_inprocess(*ARGS, *extra)

    return go


def test_clean_test_passes(run):
    run("def test_a():\n    assert 1 + 1 == 2\n").assert_outcomes(passed=1)


def test_plugin_default_is_off(pytester):
    pytester.makeini("[pytest]\nestate_policy_settle_seconds = 0\n")
    pytester.makepyfile("import time\ndef test_a():\n    time.sleep(1)\n")
    pytester.runpytest_inprocess(*ARGS).assert_outcomes(passed=1)


def test_project_ini_opts_in_to_warn(run, pytester):
    pytester.makeini("[pytest]\nestate_policy = warn\nestate_policy_settle_seconds = 0\n")
    result = run("import time\ndef test_a():\n    time.sleep(1)\n")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*[[]fixed-sleep[]] line 3*"])


@pytest.mark.parametrize(
    "ini,option,outcomes",
    [
        ("fail", "off", {"passed": 1}),
        ("off", "fail", {"errors": 1}),
        ("warn", "fail", {"errors": 1}),
        ("fail", "warn", {"passed": 1}),
    ],
)
def test_cli_mode_overrides_ini(run, pytester, ini, option, outcomes):
    pytester.makeini(f"[pytest]\nestate_policy = {ini}\nestate_policy_settle_seconds = 0\n")
    result = run("import time\ndef test_a():\n    time.sleep(1)\n", f"--estate-policy={option}")
    result.assert_outcomes(**outcomes)


def test_invalid_ini_mode_is_a_usage_error(run, pytester):
    pytester.makeini("[pytest]\nestate_policy = bogus\n")
    result = run("def test_a():\n    pass\n")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*estate_policy must be one of off, warn, fail*"])


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

    Nothing here depends on elapsed time: the leaked thread blocks on an event and the leaked fd stays open until
    the finalizer below releases them, however long the host stalls. The generated sources reach the holder through
    the builtin name ``ESTATE_HELD``.
    """
    holder = types.SimpleNamespace(release=threading.Event(), things=[])
    monkeypatch.setattr(builtins, "ESTATE_HELD", holder, raising=False)
    yield holder
    holder.release.set()
    for thing in holder.things:
        if isinstance(thing, int):
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


def test_leaked_fd_is_reported(run, held):
    body = (
        "import os\n"
        "def test_a():\n"
        "    ESTATE_HELD.things.append(os.open('/dev/null', os.O_RDONLY))\n"
    )
    result = run(body)
    result.stdout.fnmatch_lines(["*file descriptor still open*"])


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
    real_readlink = os.readlink

    def bad_readlink(path):
        raise OSError("gone")

    monkeypatch.setattr(os, "readlink", bad_readlink)
    assert plugin.take_snapshot().fds == frozenset()


def test_static_lines_without_function_or_source():
    assert plugin._static_lines(types.SimpleNamespace(), set(), 0.2) == []
    ns = {}
    exec("def f():\n    pass\n", ns)
    assert plugin._static_lines(types.SimpleNamespace(function=ns["f"]), set(), 0.2) == []


def test_settle_loop_waits_for_a_leak_that_goes_away(monkeypatch):
    before = policy.Snapshot(frozenset(), frozenset())
    leaked = policy.Snapshot(frozenset({"x#1"}), frozenset())
    seen = iter([leaked, leaked, before])  # the leak disappears on the third look, whatever the clock says
    monkeypatch.setattr(plugin, "take_snapshot", lambda: next(seen))
    assert plugin._settled_leaks(before, 3600) == []


def test_settle_loop_gives_up_after_the_deadline(monkeypatch):
    before = policy.Snapshot(frozenset(), frozenset())
    leaked = policy.Snapshot(frozenset({"x#1"}), frozenset())
    monkeypatch.setattr(plugin, "take_snapshot", lambda: leaked)
    assert plugin._settled_leaks(before, 0.1) == ["thread still running: x#1"]


def _set_fake_containment(monkeypatch, fake_estate):
    monkeypatch.setattr(containment, "DEFAULT_PROC_ROOT", fake_estate.proc)
    monkeypatch.setattr(containment, "DEFAULT_CGROUP_ROOT", fake_estate.cgroup)


def _marked_body(sentinel):
    return (
        "import pathlib, pytest\n"
        "@pytest.mark.requires_contained_cgroup\n"
        "def test_marked():\n"
        f"    pathlib.Path({str(sentinel)!r}).write_text('body ran')\n"
    )


def test_containment_marker_passes_in_a_capped_private_tree(run, pytester, fake_estate, monkeypatch):
    fake_estate.private()
    _set_fake_containment(monkeypatch, fake_estate)
    sentinel = pytester.path / "body-ran"
    run(_marked_body(sentinel)).assert_outcomes(passed=1)
    assert sentinel.read_text() == "body ran"


def test_containment_marker_fails_at_setup_and_does_not_run_the_body(run, pytester, fake_estate, monkeypatch):
    fake_estate.private(memory="max", pids="max")
    _set_fake_containment(monkeypatch, fake_estate)
    sentinel = pytester.path / "body-ran"
    result = run(_marked_body(sentinel))
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        [
            "*requires_contained_cgroup refused:*",
            "*memory.max: no finite effective limit*",
            "*pids.max: no finite effective limit*",
        ]
    )
    assert not sentinel.exists()


def test_containment_refusal_is_cached_for_the_session(run, pytester, fake_estate, monkeypatch):
    fake_estate.private(memory="max", pids="max")
    _set_fake_containment(monkeypatch, fake_estate)
    real_reader = containment._read_text
    cgroup_reads = []

    def counting_reader(path):
        if path == fake_estate.proc / "self" / "cgroup":
            cgroup_reads.append(path)
        return real_reader(path)

    monkeypatch.setattr(containment, "DEFAULT_READER", counting_reader)
    sentinel = pytester.path / "body-ran"
    first = _marked_body(sentinel)
    second = first.replace("test_marked", "test_marked_again")
    result = run(first + second)
    result.assert_outcomes(errors=2)
    assert not sentinel.exists()
    assert len(cgroup_reads) == 1


def test_containment_marker_warn_skips_with_distinct_reason(run, pytester, fake_estate, monkeypatch):
    pytester.makeini("[pytest]\nestate_policy = warn\nestate_policy_settle_seconds = 0\n")
    fake_estate.private()
    fake_estate.process(9, 1, command="foreign")
    _set_fake_containment(monkeypatch, fake_estate)
    sentinel = pytester.path / "body-ran"
    result = run(_marked_body(sentinel), "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(
        [
            "*estate-test-policy: requires_contained_cgroup refused:*",
            "*pid-namespace-shared*",
            "*SKIPPED*estate-test-policy: requires_contained_cgroup refused*",
        ]
    )
    assert not sentinel.exists()


def test_containment_marker_is_inert_under_off(run, pytester, fake_estate, monkeypatch):
    pytester.makeini("[pytest]\nestate_policy = off\nestate_policy_settle_seconds = 0\n")
    fake_estate.proc = pytester.path / "missing-proc"
    fake_estate.cgroup = pytester.path / "missing-cgroup"
    _set_fake_containment(monkeypatch, fake_estate)
    sentinel = pytester.path / "body-ran"
    run(_marked_body(sentinel)).assert_outcomes(passed=1)
    assert sentinel.read_text() == "body ran"


def test_leaked_real_child_fails_and_is_gone(run, pytester):
    pid_path = pytester.path / "child.pid"
    body = (
        "import pathlib, subprocess\n"
        "def test_leaks_child():\n"
        "    child = subprocess.Popen(['/bin/sleep', '300'])\n"
        f"    pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))\n"
    )
    result = run(body)
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*[[]children[]] test left direct child processes behind*", "*alive; SIGKILL sent and reaped*"])
    pid = int(pid_path.read_text())
    assert children.read_process(pid) is None


def test_children_warn_mode_warns_and_still_cleans_up(run, pytester):
    pid_path = pytester.path / "child.pid"
    body = (
        "import pathlib, subprocess\n"
        "def test_leaks_child():\n"
        "    child = subprocess.Popen(['/bin/sleep', '300'])\n"
        f"    pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))\n"
    )
    result = run(body, "--estate-policy=warn")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*[[]children[]] test left direct child processes behind*"])
    pid = int(pid_path.read_text())
    assert children.read_process(pid) is None


def test_grandchild_reparented_to_subreaper_is_rescanned_killed_and_reported(run, pytester):
    sh_pid_path = pytester.path / "sh.pid"
    sleep_pid_path = pytester.path / "grandchild.pid"
    shell_command = (
        f"sleep 300 & echo $! > {shlex.quote(str(sleep_pid_path))}; wait"
    )
    body = (
        "import pathlib, subprocess, time\n"
        "PROCS = []\n"
        "def test_leaks_process_tree():\n"
        f"    child = subprocess.Popen(['/bin/sh', '-c', {shell_command!r}])\n"
        "    PROCS.append(child)\n"
        f"    pathlib.Path({str(sh_pid_path)!r}).write_text(str(child.pid))\n"
        "    deadline = time.monotonic() + 120\n"
        "    while True:\n"
        "        try:\n"
        f"            if pathlib.Path({str(sleep_pid_path)!r}).read_text():\n"
        "                break\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise AssertionError('grandchild pid was not recorded')\n"
        "        time.sleep(0.001)\n"
    )
    result = run(body)
    result.assert_outcomes(passed=1, errors=1)
    sh_pid = int(sh_pid_path.read_text())
    sleep_pid = int(sleep_pid_path.read_text())
    result.stdout.fnmatch_lines(
        [
            f"*pid {sh_pid} comm*alive; SIGKILL sent and reaped*",
            f"*pid {sleep_pid} comm*alive; SIGKILL sent and reaped*",
        ]
    )
    assert children.read_process(sh_pid) is None
    assert children.read_process(sleep_pid) is None


def test_reaped_zombie_is_reported(run, pytester):
    pid_path = pytester.path / "zombie.pid"
    body = (
        "import pathlib, subprocess, time\n"
        "from estate_test_policy import children\n"
        "PROCS = []\n"
        "def test_leaks_zombie():\n"
        "    child = subprocess.Popen(['/bin/true'])\n"
        "    PROCS.append(child)\n"
        f"    pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))\n"
        "    deadline = time.monotonic() + 120\n"
        "    while True:\n"
        "        record = children.read_process(child.pid)\n"
        "        if record is not None and record.state == 'Z':\n"
        "            break\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise AssertionError('child did not become a zombie')\n"
        "        time.sleep(0.001)\n"
    )
    result = run(body)
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*[[]children[]] test left direct child processes behind*", "*zombie; reaped*"])
    pid = int(pid_path.read_text())
    assert children.read_process(pid) is None


def test_estate_allow_children_exempts_and_cleanup_remains_test_owned(run, pytester):
    pid_path = pytester.path / "allowed.pid"
    body = (
        "import pathlib, pytest, subprocess\n"
        "@pytest.mark.estate_allow('children', reason='session helper exercised by the oracle', backlog='TP-12')\n"
        "def test_allowed_child():\n"
        "    child = subprocess.Popen(['/bin/sleep', '300'])\n"
        f"    pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))\n"
    )
    run(body).assert_outcomes(passed=1)
    pid = int(pid_path.read_text())
    record = children.read_process(pid)
    assert record is not None
    assert children.remove_if_still_ours(record) is not None
    assert children.read_process(pid) is None


def test_estate_allow_children_reaps_new_zombies_only(run, pytester):
    pid_path = pytester.path / "allowed-zombie.pid"
    body = (
        "import pathlib, pytest, subprocess, time\n"
        "from estate_test_policy import children\n"
        "PROCS = []\n"
        "@pytest.mark.estate_allow('children', reason='allowed zombie must still be reaped', backlog='TP-12')\n"
        "def test_allowed_zombie():\n"
        "    child = subprocess.Popen(['/bin/true'])\n"
        "    PROCS.append(child)\n"
        f"    pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))\n"
        "    deadline = time.monotonic() + 120\n"
        "    while True:\n"
        "        record = children.read_process(child.pid)\n"
        "        if record is not None and record.state == 'Z':\n"
        "            break\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise AssertionError('child did not become a zombie')\n"
        "        time.sleep(0.001)\n"
    )
    run(body).assert_outcomes(passed=1)
    pid = int(pid_path.read_text())
    assert children.read_process(pid) is None


def test_contained_cgroup_is_not_an_estate_allow_rule(run):
    body = (
        "import pytest\n"
        "@pytest.mark.estate_allow('contained-cgroup', reason='a documented exception', backlog='TP-13')\n"
        "def test_bad_allow():\n"
        "    pass\n"
    )
    result = run(body, "--estate-policy=warn")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*estate_allow needs a rule from*"])
