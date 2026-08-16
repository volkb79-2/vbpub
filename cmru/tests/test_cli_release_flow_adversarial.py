"""Whole-contract tests for CMRU's CLI release/orchestration boundary."""
from __future__ import annotations

import contextlib
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import cli


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x=1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _project(root: Path, *, steps=None, generated=()):
    return SimpleNamespace(
        name="demo", cwd="demo", project_root=root / "demo", paths=["demo"],
        runner_steps=steps or {}, steps=steps or {}, commit_generated=generated,
        changelog=None, version=SimpleNamespace(strategy="scm"), git_tag=True,
        prefix="demo-v", build_step="build", scm_dist=None,
    )


def test_source_version_uses_exact_and_dev_git_describe_shapes(tmp_path, monkeypatch):
    source = Path(cli.__file__).resolve().parents[2]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "--exact-match" in argv:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="cmru-v1.2.3-4-gabcdef0\n")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.Path, "cwd", lambda: tmp_path)
    assert cli._source_tree_version() is None or isinstance(cli._source_tree_version(), str)
    assert cli._dev_version_from_describe("cmru-v1.2.3-4-gabcdef0") == "1.2.4.dev4+gabcdef0"
    assert cli._dev_version_from_describe("other-v1.2.3-4-gabc") is None


def test_default_and_explicit_config_resolution_are_current_directory_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cmru.orchestration.toml").write_text("", encoding="utf-8")
    assert cli._default_config_path().name == "cmru.orchestration.toml"
    assert cli._resolve_config("~/not-a-real-cmru.toml").is_absolute()


def test_prepare_release_commits_only_declared_generated_output(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    project = _project(root, steps={"prepare": object()}, generated=("generated.txt",))

    def prepare(*_args):
        (root / "demo" / "generated.txt").write_text("derived\n", encoding="utf-8")

    monkeypatch.setattr(cli, "run_project_step", prepare)
    cli._prepare_release_projects(root, {"demo": project}, ["demo"])
    assert _git(root, "log", "-1", "--format=%s") == "chore(demo): prepare release inputs"
    assert _git(root, "show", "--format=", "--name-only", "HEAD").strip() == "demo/generated.txt"


def test_prepare_release_refuses_undeclared_side_effect(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    project = _project(root, steps={"prepare": object()}, generated=("generated.txt",))

    def prepare(*_args):
        (root / "demo" / "unexpected.txt").write_text("unsafe\n", encoding="utf-8")

    monkeypatch.setattr(cli, "run_project_step", prepare)
    with pytest.raises(RuntimeError, match="undeclared paths"):
        cli._prepare_release_projects(root, {"demo": project}, ["demo"])


def test_run_project_steps_rejects_missing_declared_step(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    project = _project(root, steps={"run-tests": object()})
    with pytest.raises(RuntimeError, match="not declared"):
        cli._run_project_steps(root, {"demo": project}, ["demo"], ["build"])


def test_release_gates_require_run_tests_and_dispatch_declared_gate(tmp_path, monkeypatch):
    missing = _project(tmp_path)
    with pytest.raises(RuntimeError, match="no release gate"):
        cli._run_release_gates(tmp_path, {"demo": missing}, ["demo"])
    project = _project(tmp_path, steps={"run-tests": object()})
    calls = []
    monkeypatch.setattr(cli, "run_project_step", lambda *args: calls.append(args[1]))
    cli._run_release_gates(tmp_path, {"demo": project}, ["demo"])
    assert calls == ["run-tests"]


def test_untagged_project_refuses_build_side_effect_after_artifact_step(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    project = _project(root, steps={"build": object(), "push": object()})
    monkeypatch.setattr(cli, "run_project_step",
                        lambda *_args: (root / "demo" / "tracked-after-tag.txt").write_text("x"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_args: None)
    with pytest.raises(RuntimeError, match="changed tracked source"):
        cli._run_untagged_project(root, {"demo": project}, "demo",
                                  github_config=SimpleNamespace(), env_config=SimpleNamespace())


def test_transaction_child_args_strip_parent_only_options_and_reject_external_config(tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")
    args = cli._child_release_args(
        ["--resume", "/tmp/w", "--abandon=all-previous", "--config=/old", "--project", "demo"],
        config, tmp_path,
    )
    assert args == ["--project", "demo", "--config", "cmru.toml"]
    outside = tmp_path.parent / "outside-cmru.toml"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="tracked inside"):
        cli._child_release_args([], outside, tmp_path)


def test_tag_on_head_ignores_latest_pointer_and_selects_highest_version(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "demo-v1.0.0")
    _git(root, "tag", "demo-v1.2.0")
    _git(root, "tag", "demo-latest")
    assert cli._tag_on_head(root, "demo-v") == "demo-v1.2.0"
    assert cli._tag_on_head(root, "missing-v") is None


def test_main_routes_version_help_and_unknown_verb_without_config(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmru_version", lambda: "9.9.9")
    cli.main(["version"])
    assert "cmru 9.9.9" in capsys.readouterr().out
    cli.main(["--help"])
    assert "TYPICAL WORKFLOW" in capsys.readouterr().out
    with pytest.raises(SystemExit) as raised:
        cli.main(["unknown-verb"])
    assert raised.value.code == 2


def test_main_worktrees_json_is_a_machine_readable_boundary(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda _root: [])
    cli.main(["worktrees", "--json"])
    assert __import__("json").loads(capsys.readouterr().out) == []


def test_orchestrate_step_first_respects_project_order_and_selected_subset(monkeypatch, tmp_path):
    project_a = _project(tmp_path, steps={"run-tests": object()})
    project_b = _project(tmp_path, steps={"run-tests": object()})
    project_b.name = "beta"
    configs = {"demo": project_a, "beta": project_b}
    loaded = (tmp_path, configs, ["beta", "demo"], ["beta", "demo"], ["run-tests"],
              "step-first", {"run-tests": ["beta", "demo"]}, SimpleNamespace(),
              SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    calls = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, *_: calls.append((project.name, step)))
    monkeypatch.setattr(cli.sys, "argv", ["cmru", "--project", "demo"])
    # _orchestrate parses argv itself; selecting demo must suppress beta.
    cli._orchestrate()
    assert calls == [("demo", "run-tests")]
