"""pytest plugin ``estate_test_policy``: tests never depend on host load or speed.

Static rules (fixed sleeps, sub-120 s timeouts, wall-time assertions) are checked per test at setup; leaked
threads, file descriptors and child processes are checked at teardown. A deviation is allowed only through
``@pytest.mark.estate_allow("<rule>", reason="...", backlog="XX-NN")``.

Options: ``--estate-policy={off,warn,fail}`` (default ``fail``), ini ``estate_policy_sleep_threshold`` (seconds,
default 0.2) and ``estate_policy_settle_seconds`` (how long a just-stopped thread or child may take to disappear
before it counts as leaked, default 5).
"""

from __future__ import annotations

import inspect
import os
import threading
import time
import warnings

import pytest

from . import policy

_MODES = ("off", "warn", "fail")


class EstatePolicyWarning(UserWarning):
    """Emitted instead of a failure in ``--estate-policy=warn`` mode."""


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("estate-test-policy")
    group.addoption("--estate-policy", choices=_MODES, default="fail", help="enforcement mode (default: fail)")
    parser.addini("estate_policy_sleep_threshold", "fixed sleeps at or above this many seconds are flagged", default="0.2")
    parser.addini("estate_policy_settle_seconds", "grace for a just-stopped thread or child to vanish", default="5")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "estate_allow(rule, reason, backlog): allow one estate-test-policy rule for this test; "
        "reason and a backlog id are mandatory",
    )


def take_snapshot() -> policy.Snapshot:
    """Threads, open fds and direct children of this process (Linux /proc)."""
    threads = frozenset(f"{t.name}#{t.ident}" for t in threading.enumerate())
    fd_dir = "/proc/self/fd"
    fds = set()
    for name in os.listdir(fd_dir):
        try:
            fds.add(f"{name}->{os.readlink(os.path.join(fd_dir, name))}")
        except OSError:
            continue  # the fd of the listing itself, or one closed meanwhile
    children = set()
    for tid in os.listdir("/proc/self/task"):
        try:
            with open(f"/proc/self/task/{tid}/children", encoding="ascii") as handle:
                children.update(handle.read().split())
        except OSError:
            continue  # thread ended meanwhile
    return policy.Snapshot(threads, frozenset(fds), frozenset(children))


def _allowed_rules(node: pytest.Item) -> tuple[set[str], list[str]]:
    allowed: set[str] = set()
    errors: list[str] = []
    for marker in node.iter_markers("estate_allow"):
        rule, error = policy.validate_allow(marker.args, marker.kwargs)
        if error:
            errors.append(error)
        else:
            allowed.add(rule)
    return allowed, errors


def _report(config: pytest.Config, lines: list[str]) -> None:
    if not lines:
        return
    text = "estate-test-policy:\n  " + "\n  ".join(lines)
    if config.getoption("--estate-policy") == "warn":
        warnings.warn(text, EstatePolicyWarning, stacklevel=2)
    else:
        pytest.fail(text, pytrace=False)


def _static_lines(item: pytest.Item, allowed: set[str], threshold: float) -> list[str]:
    function = getattr(item, "function", None)
    if function is None:
        return []
    try:
        lines, first = inspect.getsourcelines(function)
    except (OSError, TypeError):  # dynamically created tests have no source
        return []
    return [v.render() for v in policy.analyze_source("".join(lines), sleep_threshold=threshold, first_line=first) if v.rule not in allowed]


def _settled_leaks(before: policy.Snapshot, settle: float) -> list[str]:
    deadline = time.monotonic() + settle
    while True:
        leaks = policy.diff_snapshots(before, take_snapshot())
        if not leaks or time.monotonic() >= deadline:
            return leaks
        time.sleep(0.05)


@pytest.fixture(autouse=True)
def estate_policy_guard(request: pytest.FixtureRequest):
    config = request.config
    if config.getoption("--estate-policy") == "off":
        yield
        return
    allowed, errors = _allowed_rules(request.node)
    threshold = float(config.getini("estate_policy_sleep_threshold"))
    _report(config, errors + _static_lines(request.node, allowed, threshold))
    before = take_snapshot()
    yield
    if policy.RULE_LEAK not in allowed:
        _report(config, _settled_leaks(before, float(config.getini("estate_policy_settle_seconds"))))
