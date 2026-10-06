"""``cmru run --step`` pins what ``runner.run_step`` used to pin.

``runner.run_step`` (a second, Python-only way to run one declared step) was deleted
in the W2-INTEG fix round: it had no production caller, and ``cmru run --step`` is the
one supported route.  Each behaviour its direct tests pinned is pinned here through
the real ``cmru run --step`` command:

* the step runs in the project root with the project ``[env]`` as extra env and the
  protected runtime/launcher environment (was ``test_runner_step_uses_nearest_central_config``);
* a quiet step writes its detail to the project-local ``logs/cmru`` root
  (was ``test_raw_runner_uses_project_local_log_root``);
* an undeclared step and an unknown project are refused, exit 2
  (was ``test_runner_run_step_requires_one_project_and_declared_step`` and
  ``test_runner_step_refuses_central_config_without_exact_project_match``).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cmru import cli, exit_codes

_PROJECT = """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[runtime]
kind = "none"
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
[env]
DEMO_FLAG = "on"
[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["bash", "-c", "echo inner detail"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
"""


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    # ``cmru run`` applies the release env to the process: swap in a copy so
    # nothing it sets (GITHUB_*, CMRU_INTERNAL_*, DEMO_FLAG) outlives the test.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in [n for n in os.environ if n.startswith("CMRU_INTERNAL_")]:
        os.environ.pop(name)  # an earlier file may have left one (e.g. SHOW_RUN_DETAILS)
    root = tmp_path / "demo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    (root / "cmru.toml").write_text(_PROJECT, encoding="utf-8")
    return root


def test_run_step_executes_in_the_project_root_with_env_and_protected_runtime(project, monkeypatch):
    executed = []
    monkeypatch.setattr(
        "cmru.cli.execute_step",
        lambda step, root, log_dir, **kwargs: executed.append((step, root, log_dir, kwargs)),
    )

    assert cli.main(["run", "--step", "build", "--config", str(project / "cmru.toml")]) == 0

    assert len(executed) == 1
    _step, root, log_dir, kwargs = executed[0]
    assert Path(root).resolve() == project.resolve()
    assert Path(log_dir).resolve() == (project / "logs" / "cmru").resolve()
    assert kwargs["extra_env"]["DEMO_FLAG"] == "on"
    assert kwargs["protected_env"]["CMRU_RUNTIME_KIND"] == "none"
    assert Path(kwargs["protected_env"]["CMRU_INTERNAL_BIN"]).name == "cmru"


def test_run_step_quiet_detail_goes_to_the_project_local_log_root(project, monkeypatch, capsys):
    monkeypatch.delenv("CMRU_INTERNAL_RUN_LOG", raising=False)

    assert cli.main(["run", "--step", "build", "--config", str(project / "cmru.toml")]) == 0

    log = project / "logs" / "cmru" / "build.log"
    assert "inner detail" in log.read_text(encoding="utf-8")
    streamed = capsys.readouterr()
    # quiet: the command's own output is only in the log (its command line is announced)
    assert "inner detail" not in [line.strip() for line in (streamed.out + streamed.err).splitlines()]


def test_run_step_refuses_an_undeclared_step(project, capsys):
    assert cli.main(["run", "--step", "nope", "--config", str(project / "cmru.toml")]) == exit_codes.CONFIG_ERROR
    assert "step(s) not declared: nope" in capsys.readouterr().err
    assert not (project / "logs").exists()  # nothing ran


def test_run_step_refuses_a_project_the_config_does_not_register(project, capsys):
    rc = cli.main(["run", "nosuch", "--step", "build", "--config", str(project / "cmru.toml")])
    assert rc == exit_codes.CONFIG_ERROR
    assert "unknown project(s): nosuch" in capsys.readouterr().err
    assert not (project / "logs").exists()
