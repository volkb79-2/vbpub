"""Module-level execution of the `run_gate_profile` pytest plugin.

`run_gate_profile` is a `pytest11` entry-point plugin, so pytest imports it
before pytest-cov starts measuring: its imports, constants and `def`/`class`
header lines run outside coverage and would count as uncovered changed lines
for the diff-coverage floor. These tests execute a FRESH copy of the file
under a unique module name (never touching the registered plugin module or its
`_PROFILE` state) and assert real properties of what it defines.
"""

import importlib.util
import itertools
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROFILE_SOURCE = PROJECT_DIR / "run_gate_profile.py"
_COUNTER = itertools.count()

HOOK_NAMES = (
    "pytest_configure",
    "pytest_collection_finish",
    "pytest_xdist_node_collection_finished",
    "pytest_runtest_logreport",
    "pytest_unconfigure",
)


def load_fresh_profile_module():
    """Execute run_gate_profile.py as a new module; return it."""
    name = f"_run_gate_profile_fresh_{next(_COUNTER)}"
    spec = importlib.util.spec_from_file_location(name, PROFILE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.fixture
def fresh():
    return load_fresh_profile_module()


def test_fresh_copy_is_distinct_from_registered_plugin(fresh):
    registered = sys.modules.get("run_gate_profile")
    assert fresh is not registered
    assert fresh.__file__ == str(PROFILE_SOURCE)
    assert fresh._PROFILE is None


def test_entry_point_matches_pyproject_and_hooks_exist(fresh):
    pyproject = tomllib.loads(
        (PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["pytest11"]
    assert entry_points == {"run_gate_profile": "run_gate_profile"}
    assert PROFILE_SOURCE.name == entry_points["run_gate_profile"] + ".py"
    for hook in HOOK_NAMES:
        assert callable(getattr(fresh, hook)), hook


def test_constants(fresh):
    assert fresh.PROFILE_DIR_ENV == "RUN_GATE_PROFILE_DIR"
    assert fresh.OBSERVER_SOCKET_ENV == "ASSAY_RESOURCE_OBSERVER_SOCKET"
    assert fresh.WINDOW_INTERVAL_MS == 250
    assert fresh.OBSERVER_TIMEOUT_SECONDS == 5.0


def test_subject_for_distinguishes_worker_and_controller(fresh):
    worker = SimpleNamespace(workerinput={"workerid": "gw3"})
    assert fresh.subject_for(worker) == ("gw3", "xdist-worker")
    assert fresh.subject_for(SimpleNamespace()) == ("controller", "controller")


def test_inert_without_profile_dir(fresh, monkeypatch):
    monkeypatch.delenv(fresh.PROFILE_DIR_ENV, raising=False)
    fresh.pytest_configure(SimpleNamespace())
    fresh.pytest_collection_finish(SimpleNamespace(items=[1, 2]))
    fresh.pytest_xdist_node_collection_finished(None, ["a"])
    fresh.pytest_runtest_logreport(SimpleNamespace(when="call"))
    fresh.pytest_unconfigure(SimpleNamespace())
    assert fresh._PROFILE is None


def test_profile_without_observer_is_no_measurement(fresh, tmp_path, monkeypatch):
    monkeypatch.setenv(fresh.PROFILE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(fresh.OBSERVER_SOCKET_ENV, raising=False)
    config = SimpleNamespace()
    fresh.pytest_configure(config)
    assert fresh._PROFILE is not None
    assert fresh._PROFILE.kind == "controller"
    fresh.pytest_collection_finish(SimpleNamespace(items=[1, 2, 3]))
    fresh.pytest_xdist_node_collection_finished(None, ["a", "b"])
    fresh.pytest_runtest_logreport(SimpleNamespace(when="setup"))
    fresh.pytest_runtest_logreport(SimpleNamespace(when="call"))
    fresh.pytest_unconfigure(config)
    assert fresh._PROFILE is None
    written = (tmp_path / "controller.json").read_text(encoding="utf-8")
    assert '"status": "NO_MEASUREMENT"' in written
    assert fresh.OBSERVER_SOCKET_ENV in written
    assert '"tests": 1' in written
    assert '"tests_collected": 2' in written


def test_unwritable_profile_dir_never_raises(fresh, tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(fresh.PROFILE_DIR_ENV, str(blocker / "sub"))
    monkeypatch.delenv(fresh.OBSERVER_SOCKET_ENV, raising=False)
    fresh.pytest_configure(SimpleNamespace())
    fresh.pytest_unconfigure(SimpleNamespace())
    assert "NO_MEASUREMENT, could not write controller" in capsys.readouterr().err
    assert fresh._PROFILE is None
