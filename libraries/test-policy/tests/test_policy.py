import pytest

from estate_test_policy import policy


def rules(src, **kw):
    return [v.rule for v in policy.analyze_source(src, **kw)]


def test_fixed_sleep_flagged_outside_a_loop():
    assert rules("import time\ndef t():\n    time.sleep(1)\n    assert True\n") == ["fixed-sleep"]
    assert rules("async def t():\n    await asyncio.sleep(0.5)\n") == ["fixed-sleep"]
    assert rules("def t():\n    sleep(2)\n") == ["fixed-sleep"]


def test_sleep_below_threshold_or_non_constant_or_in_loop_is_fine():
    assert rules("def t():\n    time.sleep(0.05)\n") == []
    assert rules("def t(n):\n    time.sleep(n)\n") == []
    assert rules("def t():\n    time.sleep()\n") == []
    assert rules("def t():\n    while not ready():\n        time.sleep(1)\n") == []
    assert rules("def t():\n    for _ in range(3):\n        time.sleep(1)\n") == []
    assert rules("def t():\n    time.sleep(0.5)\n", sleep_threshold=1.0) == []
    assert rules("def t():\n    other.sleep(5)\n") == []


def test_short_timeouts_flagged():
    assert rules("def t():\n    subprocess.run(x, timeout=5)\n") == ["short-timeout"]
    assert rules("def t():\n    p.wait(30)\n    t.join(1.5)\n    s.settimeout(10)\n") == ["short-timeout"] * 3
    assert rules("def t():\n    q.get(timeout=60)\n") == ["short-timeout"]


def test_timeouts_at_or_above_floor_or_zero_or_dynamic_are_fine():
    assert rules("def t():\n    subprocess.run(x, timeout=120)\n") == []
    assert rules("def t():\n    p.wait(0)\n    q.get(timeout=0)\n") == []
    assert rules("def t(n):\n    p.wait(n)\n    q.get(timeout=n)\n    p.wait()\n") == []
    assert rules("def t():\n    foo(1)\n    foo(retries=3)\n") == []


def test_timeouts_in_strings():
    assert rules("def t():\n    cmd = 'tool --x timeout 30s'\n") == ["short-timeout"]
    assert rules("def t():\n    cmd = 'timeout=500ms run'\n") == ["short-timeout"]
    assert rules("def t():\n    cmd = 'timeout 2m run'\n") == []
    assert rules("def t():\n    cmd = 'timeout 1h'\n    x = 'timeout 0'\n") == []
    assert rules("def t():\n    cmd = 'timeouts 30'\n    y = 5\n") == []
    assert rules("def t():\n    cmd = 'timeout 90 '\n") == ["short-timeout"]


def test_walltime_assertions():
    assert rules("def t():\n    a = time.monotonic()\n    run()\n    assert time.monotonic() - a < 1\n") == ["wall-time-assert"]
    assert rules("def t():\n    a = time.time()\n    run()\n    elapsed = time.time() - a\n    assert elapsed < 2\n") == ["wall-time-assert"]
    assert rules("def t():\n    a: float = perf_counter()\n    e = a\n    assert e > 0\n") == ["wall-time-assert"]


def test_non_clock_assertions_are_fine():
    assert rules("def t():\n    a = 1\n    assert a < 2\n    assert x\n    x: int\n    y: int = 3\n") == []
    assert rules("def t():\n    assert os.getpid() > 0\n    z = foo.time()\n    assert z\n") == []
    assert rules("def t():\n    a = time.monotonic()\n    assert not flag\n") == []


def test_violations_sorted_and_rendered():
    out = policy.analyze_source("def t():\n    time.sleep(1)\n    p.wait(3)\n")
    assert [v.lineno for v in out] == [2, 3]
    assert out[0].render().startswith("[fixed-sleep] line 2:")


@pytest.mark.parametrize(
    "args,kwargs,fragment",
    [
        ((), {}, "needs a rule"),
        (("nope",), {"reason": "long enough reason", "backlog": "TP-1"}, "needs a rule"),
        (("fixed-sleep",), {"backlog": "TP-1"}, "requires reason"),
        (("fixed-sleep",), {"reason": "short", "backlog": "TP-1"}, "requires reason"),
        (("fixed-sleep",), {"reason": "long enough reason"}, "requires backlog"),
        (("fixed-sleep",), {"reason": "long enough reason", "backlog": "tp-1"}, "requires backlog"),
    ],
)
def test_allow_marker_validation_errors(args, kwargs, fragment):
    rule, error = policy.validate_allow(args, kwargs)
    assert fragment in error


def test_allow_marker_valid():
    assert policy.validate_allow(("fixed-sleep",), {"reason": "hardware property", "backlog": "RG-162"}) == ("fixed-sleep", None)
    assert policy.validate_allow((), {"rule": "resource-leak", "reason": "kernel behaviour", "backlog": "A-12-3"}) == ("resource-leak", None)


def test_diff_snapshots_reports_everything():
    before = policy.Snapshot(frozenset({"a#1"}), frozenset({"0->x"}), frozenset({"5"}))
    after = policy.Snapshot(frozenset({"a#1", "b#2"}), frozenset({"0->x", "7->y"}), frozenset({"5", "9"}))
    assert policy.diff_snapshots(before, after) == [
        "thread still running: b#2",
        "file descriptor still open: 7->y",
        "child process still present: pid 9",
    ]
    assert policy.diff_snapshots(after, before) == []


def test_call_of_a_call_has_no_name():
    assert rules("def t():\n    f()(timeout=5)\n    g()(1)\n") == ["short-timeout"]
