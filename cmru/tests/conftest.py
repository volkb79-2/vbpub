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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest_plugins = ["cli_extended.pytest_plugin"]

#: The process environment when the session started (before any test ran).
SESSION_START_ENVIRON = dict(os.environ)


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


@pytest.fixture(autouse=True)
def _neutral_release_controller_provenance(request, monkeypatch):
    """Keep release-transaction unit tests independent of the test image's CMRU install."""
    if request.node.get_closest_marker("release_controller_provenance"):
        yield
        return
    from cmru import cli

    monkeypatch.setattr(
        cli, "_record_release_controller_identity",
        lambda _phase: {
            "version": "6.2.0",
            "wheel_sha256": "a" * 64,
            "distribution_sha256": "b" * 64,
            "module_path": "/site-packages/cmru/cli.py",
        },
    )
    yield


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
def _restore_process_environment():
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
        yield
