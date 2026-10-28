"""Make ``cmru`` importable from the source tree without installing the package.

Also enables the cli-extended pytest plugin (W2-PKG5, CLI-T3): at collection it loads
``docs/cli-review.toml`` (found through ``[tool.cli-extended]`` in ``pyproject.toml``) and fails
the session unless every active reviewed case links a collected test through
``@pytest.mark.cli_case``. A focused run of a few test files must pass
``--cli-case-partial`` (the full gate and the full suite need no flag).
"""
import contextlib
import ctypes
import os
import signal
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest_plugins = ["cli_extended.pytest_plugin"]

#: The process environment when the session started (before any test ran).
SESSION_START_ENVIRON = dict(os.environ)


_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC_PRCTL = _LIBC.prctl
_LIBC_PRCTL.restype = ctypes.c_int
_ORIGINAL_DUP = os.dup
_ORIGINAL_DUP2 = os.dup2
_ORIGINAL_CLOSE = os.close
_ORIGINAL_KILL = os.kill
_ORIGINAL_WAITPID = os.waitpid
_ORIGINAL_SLEEP = time.sleep
_ORIGINAL_MONOTONIC = time.monotonic


def _descendant_records(root_pid: int) -> dict[int, tuple[int, str, str]]:
    result: dict[int, tuple[int, str, str]] = {}
    pending = [root_pid]
    while pending:
        parent_pid = pending.pop()
        try:
            children = Path(
                f"/proc/{parent_pid}/task/{parent_pid}/children"
            ).read_text(encoding="ascii").split()
        except OSError:
            # A process can exit between its parent's children list and this read.
            continue
        for child_text in children:
            try:
                pid = int(child_text)
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
                close = stat.rfind(")")
                fields = stat[close + 2 :].split()
                result[pid] = (int(fields[1]), fields[0], stat[stat.find("(") + 1 : close])
                pending.append(pid)
            except (OSError, ValueError, IndexError):
                # Processes can exit between listing /proc and reading stat.
                continue
    return result


def _enable_child_subreaper() -> int:
    """Make pytest adopt orphaned test grandchildren and return the prior setting."""
    previous = ctypes.c_int()
    if _LIBC_PRCTL(
        _PR_GET_CHILD_SUBREAPER, ctypes.byref(previous), 0, 0, 0,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_GET_CHILD_SUBREAPER)")
    if _LIBC_PRCTL(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_CHILD_SUBREAPER)")
    return previous.value


def _restore_child_subreaper(previous: int) -> None:
    if _LIBC_PRCTL(_PR_SET_CHILD_SUBREAPER, previous, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_CHILD_SUBREAPER)")


@pytest.fixture(scope="session", autouse=True)
def _pytest_adopts_orphaned_test_children():
    if not Path("/proc/self/stat").exists():
        yield
        return
    previous = _enable_child_subreaper()
    try:
        yield
    finally:
        _restore_child_subreaper(previous)


@pytest.fixture(autouse=True)
def _each_test_reaps_its_process_descendants(request):
    """Clean subprocess descendants at each test boundary.

    Release and shell integration tests can start helpers below pytest that
    outlive their immediate parent. Adopt and reap those helpers before the
    next test so Assay sees only descendants that escaped the test scope.
    Captured stdout/stderr are restored too: release logging redirects those
    process-wide descriptors in in-process CLI tests.
    """
    root_pid = os.getpid()
    before = set(_descendant_records(root_pid))
    saved_stdout = _ORIGINAL_DUP(1)
    saved_stderr = _ORIGINAL_DUP(2)
    leaks: dict[int, tuple[int, str, str]] = {}
    cleanup_survivors: dict[int, tuple[int, str, str]] = {}
    try:
        yield
    finally:
        try:
            leaks = {
                pid: record for pid, record in _descendant_records(root_pid).items()
                if pid not in before
            }
            deadline = _ORIGINAL_MONOTONIC() + 2.0
            while leaks and _ORIGINAL_MONOTONIC() < deadline:
                current = _descendant_records(root_pid)
                pids = set(current) - before
                for pid in pids:
                    try:
                        _ORIGINAL_KILL(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for pid in pids:
                    try:
                        _ORIGINAL_WAITPID(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                _ORIGINAL_SLEEP(0.01)
                leaks = {
                    pid: record for pid, record in _descendant_records(root_pid).items()
                    if pid not in before
                }
            cleanup_survivors = leaks
        finally:
            _ORIGINAL_DUP2(saved_stdout, 1)
            _ORIGINAL_DUP2(saved_stderr, 2)
            _ORIGINAL_CLOSE(saved_stdout)
            _ORIGINAL_CLOSE(saved_stderr)
        if cleanup_survivors:
            details = "; ".join(
                f"pid={pid} ppid={ppid} state={state} command={command!r}"
                for pid, (ppid, state, command) in sorted(cleanup_survivors.items())
            )
            pytest.fail(
                f"{request.node.nodeid} could not clean up process descendants: {details}",
                pytrace=False,
            )


@contextlib.contextmanager
def preserved_environ():
    """Restore ``os.environ`` exactly as found on exit (W3-PREP).

    The code under test writes ``CMRU_INTERNAL_*`` and ``PYTHONUNBUFFERED`` straight
    into the process environment (``cli.main`` / the runner), which no per-test
    monkeypatch can anticipate; without this, those values leaked into every later
    test in the session.
    """
    before = dict(os.environ)
    try:
        yield
    finally:
        if dict(os.environ) != before:
            os.environ.clear()
            os.environ.update(before)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_release_notes_preflight: run the real KI-75 release-notes preflight "
        "instead of the neutral stub unrelated prepare-phase tests get",
    )
    config.addinivalue_line(
        "markers",
        "release_controller_provenance: exercise the installed-wheel release-controller boundary",
    )
    config.addinivalue_line(
        "markers",
        "real_release_version_plan: run the real D-001 release version-plan step "
        "instead of the neutral stub unrelated release-child tests get",
    )
    config.addinivalue_line(
        "markers",
        "real_release_gates_plan: run the real CMRU-SKIP-01 gates record "
        "instead of the pass-through stub unrelated release-child tests get",
    )


#: The release-controller identity unit tests see. The launcher records it and
#: hands it to the transaction child through ``CMRU_RELEASE_CONTROLLER_*``.
NEUTRAL_RELEASE_CONTROLLER_IDENTITY = {
    "version": "6.2.0",
    "wheel_sha256": "a" * 64,
    "distribution_sha256": "b" * 64,
    "module_path": "/site-packages/cmru/cli.py",
}


@pytest.fixture(autouse=True)
def _neutral_release_controller_provenance(request):
    """Keep release-transaction unit tests independent of the test image's CMRU install.

    A plain attribute swap, not ``monkeypatch``: a ``monkeypatch`` requested by
    an autouse fixture that runs before ``_restore_process_environment`` is torn
    down AFTER that restore and re-introduces values the code under test wrote
    (the leak the zz probe catches).
    """
    if request.node.get_closest_marker("release_controller_provenance"):
        yield
        return
    from cmru import cli

    saved = cli._record_release_controller_identity
    cli._record_release_controller_identity = (
        lambda _phase: dict(NEUTRAL_RELEASE_CONTROLLER_IDENTITY)
    )
    try:
        yield
    finally:
        cli._record_release_controller_identity = saved


@pytest.fixture(autouse=True)
def _neutral_release_notes_preflight(request):
    """Tests of other release behaviour have no real changelog to read.

    ``_prepare_release_projects`` refuses (before any gate or tag) when a tagged
    project's pending section is unusable, and ``release --dry-run`` previews the
    release body (KI-75). Tests of OTHER behaviour (mocked changelog generation,
    fake non-repository projects) stub those two seams; the KI-75 tests carry the
    ``real_release_notes_preflight`` marker and exercise the real ones.

    Plain attribute swaps, not ``monkeypatch``: a second monkeypatch fixture is torn
    down AFTER the environment restore below and re-introduces leaked values (the zz
    probe catches that).
    """
    if request.node.get_closest_marker("real_release_notes_preflight"):
        yield
        return
    from types import SimpleNamespace

    from cmru import cli

    saved = (
        cli._pending_release_notes_or_refuse,
        cli._preview_release_bodies,
    )
    cli._pending_release_notes_or_refuse = (
        lambda *_args, **_kwargs: SimpleNamespace(version="0.0.0")
    )
    cli._preview_release_bodies = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        (
            cli._pending_release_notes_or_refuse,
            cli._preview_release_bodies,
        ) = saved


@pytest.fixture(autouse=True)
def _neutral_release_version_plan(request):
    """Release-child tests of OTHER behaviour fake their plan and repository.

    The D-001 version-plan step reads transaction sidecars and the candidate's
    Git history, which those mocked, non-repository fixtures do not have. They
    get a neutral step that leaves the invocation's flags in force; the D-001
    tests carry the ``real_release_version_plan`` marker and exercise the real
    step. The release end-to-end tests run CMRU in a subprocess and always use
    the real step. Plain attribute swap, for the reason given above.
    """
    if request.node.get_closest_marker("real_release_version_plan"):
        yield
        return
    from cmru import cli

    saved = cli._release_version_plan
    cli._release_version_plan = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        cli._release_version_plan = saved


@pytest.fixture(autouse=True)
def _neutral_release_gates_plan(request):
    """Release-child tests of OTHER behaviour fake their workspace.

    The CMRU-SKIP-01 gates record lives in the transaction sidecars those mocked
    workspaces lack, so they get a neutral step that passes the invocation's
    ``--skip-gates`` through unrecorded; tests carrying ``real_release_gates_plan``
    exercise the real record.
    """
    if request.node.get_closest_marker("real_release_gates_plan"):
        yield
        return
    from cmru import cli

    saved = cli._release_gates_plan
    cli._release_gates_plan = lambda _root, _workspace, requested, **_kw: requested
    try:
        yield
    finally:
        cli._release_gates_plan = saved


@pytest.fixture(autouse=True)
def _restore_process_environment(request):
    # Hermeticity: the real estate config DECLARES ``${CGROUP_PARENT_DEV_GATES}`` with no
    # default. The assay lane runs pytest with ``env_passthrough = ["PATH"]``, so the host's
    # variable is absent there and every test that loads ``cmru.orchestration.toml`` would
    # exit 2. Every test gets a fake value (never the host's); the test proving the
    # declared-config refusal (``test_the_real_estate_config_refuses_without_the_gates_cgroup_parent``)
    # ``delenv``s it. It is set HERE, inside the snapshot, and not through a second
    # ``monkeypatch`` fixture: that would be torn down AFTER this restore and re-introduce
    # values a test's own ``monkeypatch.delenv`` recorded (a leak the zz probe catches).
    with preserved_environ():
        os.environ["CGROUP_PARENT_DEV_GATES"] = "test-gates.slice"
        # KI-75: a publisher refuses to run without a declared release-notes
        # source. Tests of unrelated publisher behaviour declare "none"; the
        # KI-75 tests set or remove it explicitly.
        os.environ["CMRU_RELEASE_NOTES_SOURCE"] = "none"
        # A transaction child refuses to run without the launcher's controller
        # identity (``CMRU_RELEASE_CONTROLLER_*``). Tests that stand in for a
        # child see the neutral identity that ``_neutral_release_controller_
        # provenance`` makes the launcher record; the provenance tests set or
        # remove these themselves.
        if not request.node.get_closest_marker("release_controller_provenance"):
            os.environ["CMRU_RELEASE_CONTROLLER_VERSION"] = (
                NEUTRAL_RELEASE_CONTROLLER_IDENTITY["version"]
            )
            os.environ["CMRU_RELEASE_CONTROLLER_WHEEL_SHA256"] = (
                NEUTRAL_RELEASE_CONTROLLER_IDENTITY["wheel_sha256"]
            )
            os.environ["CMRU_RELEASE_CONTROLLER_FILES_SHA256"] = (
                NEUTRAL_RELEASE_CONTROLLER_IDENTITY["distribution_sha256"]
            )
        yield
