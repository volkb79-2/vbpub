"""cli-extended's subprocess proof for the estate test-policy child guard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import conftest


def _installed_lookalike(tmp_path, monkeypatch) -> None:
    """Put an importable fake installed package and pytest11 entry point first on the path."""
    site = tmp_path / "installed-site"
    package = site / "estate_test_policy"
    metadata = site / "estate_test_policy-0.0.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text(
        'raise AssertionError("the installed estate_test_policy was imported")\n',
        encoding="utf-8",
    )
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: estate-test-policy\nVersion: 0.0.0\n",
        encoding="utf-8",
    )
    (metadata / "entry_points.txt").write_text(
        "[pytest11]\nestate_test_policy = estate_test_policy.plugin\n",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(site), existing]) if existing else str(site),
    )


def _run_inner(pytester, monkeypatch, body: str):
    _installed_lookalike(pytester.path, monkeypatch)
    pytester.makeini(
        "[pytest]\n"
        "estate_policy = fail\n"
        "estate_policy_settle_seconds = 0\n"
    )
    pytester.makeconftest(
        "import sys\n"
        f"sys.path.insert(0, {str(conftest.POLICY_SRC)!r})\n"
        'pytest_plugins = ["estate_test_policy.plugin"]\n'
    )
    pytester.makepyfile(test_inner=body)
    return pytester.runpytest_subprocess(
        "-p", "no:estate_test_policy", "-p", "no:cacheprovider"
    )


def test_this_project_runs_the_source_plugin_in_fail_mode(request):
    """The project's OWN wiring: source plugin loaded, entry point blocked, mode fail."""
    import estate_test_policy.plugin as plugin

    assert Path(plugin.__file__).resolve().is_relative_to(conftest.POLICY_SRC)
    assert request.config.pluginmanager.is_blocked("estate_test_policy")
    assert request.config.getini("estate_policy") == "fail"


def test_source_plugin_wins_over_an_installed_lookalike(pytester, monkeypatch):
    result = _run_inner(
        pytester,
        monkeypatch,
        "from pathlib import Path\n"
        "import estate_test_policy.plugin as plugin\n"
        "def test_loaded_from_repository_source():\n"
        "    assert Path(plugin.__file__).resolve() == Path("
        f"{str(conftest.POLICY_SRC / 'estate_test_policy' / 'plugin.py')!r}"
        ").resolve()\n",
    )
    result.assert_outcomes(passed=1)


@pytest.mark.requires_contained_cgroup
def test_source_plugin_fails_a_test_that_leaves_a_live_sleep_child(pytester, monkeypatch):
    result = _run_inner(
        pytester,
        monkeypatch,
        "import subprocess\n"
        "from pathlib import Path\n"
        "KEPT = []\n"
        "def test_leaves_a_sleep_child():\n"
        '    child = subprocess.Popen(["sleep", "30"])\n'
        "    KEPT.append(child)\n"
        '    Path("pid.txt").write_text(str(child.pid))\n',
    )
    pid = int((pytester.path / "pid.txt").read_text(encoding="utf-8"))
    assert result.ret != 0
    result.stdout.fnmatch_lines([f"*pid {pid} comm sleep*"])
    assert not Path(f"/proc/{pid}").exists()
