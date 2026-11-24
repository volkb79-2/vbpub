"""Make ``cmru`` importable from the source tree without installing the package.

Also enables the cli-extended pytest plugin (W2-PKG5, CLI-T3): at collection it loads
``docs/cli-review.toml`` (found through ``[tool.cli-extended]`` in ``pyproject.toml``) and fails
the session unless every active reviewed case links a collected test through
``@pytest.mark.cli_case``. A focused run of a few test files must pass
``--cli-case-partial`` (the full gate and the full suite need no flag).
"""
import contextlib
import os
import sys
from pathlib import Path

import pytest


def _policy_source() -> Path:
    """Find the repository's test-policy source tree, including copied suites."""
    project = Path(__file__).resolve().parents[1]
    cwd = Path.cwd().resolve()
    for root in (*project.parents, cwd, *cwd.parents):
        candidate = root / "libraries" / "test-policy" / "src"
        if (candidate / "estate_test_policy" / "plugin.py").is_file():
            return candidate.resolve()
    raise RuntimeError("cannot locate libraries/test-policy/src from cmru tests")


_POLICY_SRC = _policy_source()
sys.path.insert(0, str(_POLICY_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from estate_test_policy import policy as _estate_policy  # noqa: E402

from cmru.process_guard import (
    DescendantGuard,
    DescendantLeakError,
    enable_child_subreaper,
    restore_child_subreaper,
)

_ORIGINAL_ESTATE_ANALYZE = _estate_policy.analyze_source


def _analyze_test_source(source: str, **kwargs):
    """Strip the test function's own indentation without moving string literals.

    libraries/test-policy TP-18 (controller files it): analyze_source fails on
    a method with column-0 string-literal lines; remove the shim when it lands.
    """
    indent = source[: len(source) - len(source.lstrip())]
    normalized = "".join(
        line[len(indent):] if indent and line.startswith(indent) else line
        for line in source.splitlines(keepends=True)
    )
    return _ORIGINAL_ESTATE_ANALYZE(normalized, **kwargs)


_estate_policy.analyze_source = _analyze_test_source

pytest_plugins = ["cli_extended.pytest_plugin", "estate_test_policy.plugin"]

#: The process environment when the session started (before any test ran).
SESSION_START_ENVIRON = dict(os.environ)


@pytest.fixture
def process_run_seam(monkeypatch):
    """Install an old subprocess fake at today's shared process-run seam.

    Captured calls now enter ``cli_extended.procio.run_capture`` through
    ``cmru.process_run.run_process``. Tests that deliberately fake Git still
    describe the same completed-process responses; this fixture translates
    those fakes to the production seam instead of making production inspect a
    test's monkeypatch. Direct, not-yet-migrated ``subprocess.run`` calls in
    the same module are patched too because existing tests exercise both.
    """
    def install(module, replacement):
        from cmru import git_auth

        def adapted_run_process(
            argv, *, cwd=None, env=None, timeout=None, check=False,
            capture=True, text=True, input=None, stdin=None,
        ):
            kwargs = {"check": check}
            if capture:
                kwargs["capture_output"] = True
            if text is not None:
                kwargs["text"] = text
            if cwd is not None:
                kwargs["cwd"] = cwd
            if env is not None:
                kwargs["env"] = env
            if timeout is not None:
                kwargs["timeout"] = timeout
            if input is not None:
                kwargs["input"] = input
            if stdin is not None:
                kwargs["stdin"] = stdin
            return replacement(list(argv), **kwargs)

        if hasattr(module, "run_process"):
            monkeypatch.setattr(module, "run_process", adapted_run_process)
        if hasattr(module, "subprocess"):
            monkeypatch.setattr(module.subprocess, "run", replacement)
        if module.__name__ in {"cmru.cli", "cmru.transaction", "cmru.version"}:
            monkeypatch.setattr(git_auth, "run_process", adapted_run_process)

    return install

#: Keep the known integration/install/e2e cost centers out of a fail-fast kill's
#: prefix. The env gate keeps every lane except the first-release R2 lane on
#: pytest's normal collection order.
HEAVY_LAST_ORDER_ENV = "CMRU_TEST_ORDER"
HEAVY_LAST_ORDER_VALUE = "heavy-last"
HEAVY_LAST_FILES = frozenset(
    {
        "test_release_end_to_end_real_git.py",
        "test_tester_unified_image.py",
        "test_installer_w1_round1.py",
        "test_installer_inst62.py",
        "test_installer_w1.py",
        "test_w2_integ.py",
        "test_release_gate.py",
        "test_cli_review_cases.py",
    }
)


def _is_heavy_last_item(item: pytest.Item) -> bool:
    return Path(item.path).name in HEAVY_LAST_FILES


def pytest_collection_modifyitems(config, items):
    if os.environ.get(HEAVY_LAST_ORDER_ENV) == HEAVY_LAST_ORDER_VALUE:
        items.sort(key=_is_heavy_last_item)


@pytest.fixture(scope="session", autouse=True)
def _pytest_adopts_orphaned_test_children():
    previous = enable_child_subreaper()
    try:
        yield
    finally:
        restore_child_subreaper(previous)


@pytest.fixture(scope="session", autouse=True)
def _pytest_disables_detached_git_maintenance():
    """Keep every test Git child inside its subprocess lifetime.

    Run-Gate may disable system config for its container copy. Append explicit
    settings to that environment rather than relying on tester-unified's
    system config to keep automatic maintenance foreground-only.
    """
    raw_count = os.environ.get("GIT_CONFIG_COUNT", "0")
    if not raw_count.isdecimal():
        raise RuntimeError("GIT_CONFIG_COUNT is malformed; refusing test Git children")
    count = int(raw_count)
    additions = {
        "GIT_CONFIG_COUNT": str(count + 4),
        f"GIT_CONFIG_KEY_{count}": "maintenance.auto",
        f"GIT_CONFIG_VALUE_{count}": "false",
        f"GIT_CONFIG_KEY_{count + 1}": "maintenance.autoDetach",
        f"GIT_CONFIG_VALUE_{count + 1}": "false",
        f"GIT_CONFIG_KEY_{count + 2}": "gc.auto",
        f"GIT_CONFIG_VALUE_{count + 2}": "0",
        f"GIT_CONFIG_KEY_{count + 3}": "gc.autoDetach",
        f"GIT_CONFIG_VALUE_{count + 3}": "false",
    }
    with preserved_environ():
        os.environ.update(additions)
        yield


@pytest.fixture(autouse=True)
def _versions_resume_cache_in_tmp(tmp_path_factory, monkeypatch):
    """`cmru versions resolve` keeps its interrupted-run resume file under XDG_CACHE_HOME."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))


@pytest.fixture(autouse=True)
def _each_test_reaps_its_process_descendants(request):
    """Fail a test if new process descendants outlive its boundary.

    Release and shell integration tests can start helpers below pytest that
    outlive their immediate parent. The guard kills and reaps detected leaks
    to protect later tests, but still fails the creating test even when cleanup
    succeeds. Captured stdout/stderr are restored too: release logging redirects
    those process-wide descriptors in in-process CLI tests.
    """
    guard = DescendantGuard(root_pid=os.getpid())
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    leak_error: DescendantLeakError | None = None
    try:
        yield
    finally:
        try:
            guard.check()
        except DescendantLeakError as exc:
            leak_error = exc
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
        if leak_error is not None:
            pytest.fail(
                f"{request.node.nodeid} leaked process descendants: {leak_error}",
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
        "real_release_target_tag_guard: run the real KI-130 already-tagged target "
        "guard instead of the neutral stub unrelated release-child tests get",
    )
    config.addinivalue_line(
        "markers",
        "real_release_gates_plan: run the real CMRU-SKIP-01 gates record "
        "instead of the pass-through stub unrelated release-child tests get",
    )
    config.addinivalue_line(
        "markers",
        "real_dind_release_preflight: run the real KI-182 per-project DinD sidecar "
        "image preflight instead of the no-op unrelated launcher tests get",
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
def _neutral_release_target_tag_guard(request):
    """Release-child tests of OTHER behaviour fake their repository and tags.

    The KI-130 preflight asks local Git and origin which release tags exist.
    Mocked, non-repository release-child fixtures have neither. They get a
    neutral step; the KI-130 tests carry the
    ``real_release_target_tag_guard`` marker and exercise the real guard.
    """
    if request.node.get_closest_marker("real_release_target_tag_guard"):
        yield
        return
    from cmru import cli

    saved = cli._refuse_existing_release_targets
    cli._refuse_existing_release_targets = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        cli._refuse_existing_release_targets = saved


@pytest.fixture(autouse=True)
def _neutral_dind_release_preflight(request):
    """Launcher tests of OTHER behaviour script their git snapshots.

    The KI-182 preflight reads each scope project's release-source config blob
    from git, which those scripted, non-repository fixtures lack. They get a
    no-op; tests carrying ``real_dind_release_preflight`` exercise the real one.
    """
    if request.node.get_closest_marker("real_dind_release_preflight"):
        yield
        return
    from cmru import cli

    saved = cli._release_dind_image_preconditions
    cli._release_dind_image_preconditions = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        cli._release_dind_image_preconditions = saved


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
