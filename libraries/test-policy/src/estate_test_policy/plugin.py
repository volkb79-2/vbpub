"""pytest plugin ``estate_test_policy``: tests never depend on host load or speed.

Static rules (fixed sleeps, sub-120 s timeouts, wall-time assertions) are checked per test at setup; leaked
threads, file descriptors and direct child processes are checked at teardown. A deviation is allowed only through
``@pytest.mark.estate_allow("<rule>", reason="...", backlog="XX-NN")``.

``--estate-policy`` overrides ini ``estate_policy``. Both default to ``off`` so installing the wheel is inert until
a project opts in. In ``off`` the plugin only registers its markers.
"""

from __future__ import annotations

import inspect
import linecache
import os
import threading
import time
import tokenize
import warnings

import pytest

from . import children, containment, policy

_MODES = ("off", "warn", "fail")


class EstatePolicyWarning(UserWarning):
    """Emitted instead of a failure in ``--estate-policy=warn`` mode."""


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("estate-test-policy")
    group.addoption(
        "--estate-policy",
        choices=_MODES,
        default=None,
        help="enforcement mode; overrides ini estate_policy (default: off)",
    )
    parser.addini("estate_policy", "enforcement mode: off, warn or fail", default="off")
    parser.addini("estate_policy_sleep_threshold", "fixed sleeps at or above this many seconds are flagged", default="0.2")
    parser.addini("estate_policy_settle_seconds", "grace for a just-stopped thread or child to vanish", default="5")


def policy_mode(config: pytest.Config) -> str:
    """Resolve CLI over ini; refuse an absent or unknown resolved value."""
    resolved = getattr(config, "_estate_policy_mode", None)
    if resolved is not None:
        return resolved
    option = config.getoption("--estate-policy")
    if option is not None:
        resolved = option
    else:
        configured = config.getini("estate_policy")
        if configured not in _MODES:
            raise pytest.UsageError(f"estate_policy must be one of {', '.join(_MODES)} (got {configured!r})")
        resolved = configured
    config._estate_policy_mode = resolved  # type: ignore[attr-defined]
    return resolved


def pytest_configure(config: pytest.Config) -> None:
    policy_mode(config)
    config.addinivalue_line(
        "markers",
        "estate_allow(rule, reason, backlog): allow one estate-test-policy rule for this test; "
        "reason and a backlog id are mandatory",
    )
    config.addinivalue_line(
        "markers",
        "requires_contained_cgroup: run only inside finite cgroup-v2 memory and pids caps and a private "
        "PID namespace",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    if policy_mode(config) == "off" or getattr(config, "_estate_policy_subreaper_ready", False):
        return
    if not children.enable_subreaper():
        warnings.warn(
            "estate-test-policy: leftover-children guard is inactive on non-Linux; "
            "PR_SET_CHILD_SUBREAPER is unavailable",
            EstatePolicyWarning,
            stacklevel=2,
        )
    config._estate_policy_subreaper_ready = True  # type: ignore[attr-defined]


def take_snapshot() -> policy.Snapshot:
    """Threads and open fds of this process."""
    threads = frozenset(f"{t.name}#{t.ident}" for t in threading.enumerate())
    fd_dir = "/proc/self/fd"
    fds = set()
    for name in os.listdir(fd_dir):
        try:
            fds.add(f"{name}->{os.readlink(os.path.join(fd_dir, name))}")
        except OSError:
            continue  # the fd of the listing itself, or one closed meanwhile
    return policy.Snapshot(threads, frozenset(fds))


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


def _report(config: pytest.Config, lines: list[str], mode: str | None = None) -> None:
    if not lines:
        return
    text = "estate-test-policy:\n  " + "\n  ".join(lines)
    if (mode or policy_mode(config)) == "warn":
        warnings.warn(text, EstatePolicyWarning, stacklevel=2)
    else:
        pytest.fail(text, pytrace=False)


def _static_lines(item: pytest.Item, allowed: set[str], threshold: float) -> list[str]:
    function = getattr(item, "function", None)
    if function is None:
        return []
    try:
        function = inspect.unwrap(function)
        filename = inspect.getsourcefile(function)
        code = function.__code__
        if filename is None:
            return []
        linecache.checkcache(filename)
        source = "".join(linecache.getlines(filename))
    except (AttributeError, OSError, TypeError, ValueError):  # dynamically created tests have no source
        return []
    if not source:
        return []
    function_line = code.co_firstlineno
    try:
        module = policy._module_tree(source)
        if policy._function_at_line(module, function_line) is None:
            return [f"[source-analysis] cannot locate test function in {filename} at line {function_line}"]
        violations = policy.analyze_source(source, sleep_threshold=threshold, function_line=function_line)
    except (SyntaxError, tokenize.TokenError) as exc:
        return [f"[source-analysis] could not analyze {filename}: {exc}"]
    return [violation.render() for violation in violations if violation.rule not in allowed]


def _settled_leaks(before: policy.Snapshot, settle: float) -> list[str]:
    deadline = time.monotonic() + settle
    while True:
        leaks = policy.diff_snapshots(before, take_snapshot())
        if not leaks or time.monotonic() >= deadline:
            return leaks
        time.sleep(0.05)


def _containment_refusal(config: pytest.Config, settle: float) -> containment.ContainmentResult | None:
    cached = getattr(config, "_estate_policy_containment_refusal", None)
    if cached is not None:
        return cached
    result = containment.check(settle_seconds=settle)
    if not result.contained:
        config._estate_policy_containment_refusal = result  # type: ignore[attr-defined]
        return result
    return None


@pytest.fixture(autouse=True)
def estate_policy_guard(request: pytest.FixtureRequest):
    config = request.config
    mode = policy_mode(config)
    if mode == "off":
        yield
        return
    allowed, errors = _allowed_rules(request.node)
    threshold = float(config.getini("estate_policy_sleep_threshold"))
    _report(config, errors + _static_lines(request.node, allowed, threshold), mode)

    if request.node.get_closest_marker("requires_contained_cgroup") is not None:
        refusal = _containment_refusal(config, float(config.getini("estate_policy_settle_seconds")))
        if refusal is not None:
            if mode == "warn":
                warnings.warn(refusal.message, EstatePolicyWarning, stacklevel=2)
                pytest.skip(refusal.message)
            pytest.fail(refusal.message, pytrace=False)

    before = take_snapshot()
    child_guard = None
    allowed_child_before = None
    if children.is_linux():
        settle = float(config.getini("estate_policy_settle_seconds"))
        if policy.RULE_CHILDREN in allowed:
            allowed_child_before = children.direct_children()
        else:
            child_guard = children.ChildGuard(settle_seconds=settle)
    yield
    lines = []
    if policy.RULE_LEAK not in allowed:
        lines.extend(_settled_leaks(before, float(config.getini("estate_policy_settle_seconds"))))
    if child_guard is not None:
        child_leaks = child_guard.check()
        if child_leaks:
            lines.append(
                "[children] test left direct child processes behind "
                "(killed and reaped by the guard):"
            )
            lines.extend(f"  {leak}" for leak in child_leaks)
    elif allowed_child_before is not None:
        children.reap_new_zombies(allowed_child_before)
    _report(config, lines, mode)
