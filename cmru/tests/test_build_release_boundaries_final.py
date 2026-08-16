"""Behavioral boundary tests for config, runner, handlers, bundle and changelog."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import bundle, changelog, config, handlers, runner


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x=1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def test_config_rejects_unknown_and_wrongly_typed_project_sections(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text("schema_version=1\n[project.demo]\nunknown=true\n", encoding="utf-8")
    with pytest.raises((ValueError, SystemExit)) as raised:
        config.load_forge_config(path)
    assert raised.value.code == 2 if isinstance(raised.value, SystemExit) else "unknown" in str(raised.value)
    path.write_text("schema_version=1\n[project.demo]\npaths='demo'\n", encoding="utf-8")
    with pytest.raises((ValueError, SystemExit)):
        config.load_forge_config(path)


def test_config_runner_step_requires_explicit_quiet_boolean():
    raw = {"commands": [{"label": "x", "argv": ["true"], "cwd": "."}], "quiet": "yes"}
    with pytest.raises(ValueError, match="quiet"):
        runner.parse_step({"steps": {"build": raw}}, "build")


def test_runner_required_env_dynamic_output_and_login_fail_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("NEEDED", raising=False)
    with pytest.raises(RuntimeError, match="NEEDED"):
        runner.ensure_required_env(["NEEDED"])
    bad = SimpleNamespace(stdout="not-key-value\n")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: bad)
    with pytest.raises(ValueError, match="KEY=VALUE"):
        runner.apply_env_command(["emit"], tmp_path)
    with pytest.raises(RuntimeError, match="TOKEN"):
        runner.maybe_login({"registry": "r", "username_env": "USER", "token_env": "TOKEN", "required": True})


def test_runner_executes_real_command_writes_log_and_restores_environment(tmp_path, monkeypatch):
    step = runner.StepConfig(
        name="tests", commands=[{"label": "pytest", "argv": [sys.executable, "-c", "print('2 passed in 0.01s')"], "cwd": "."}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None, clean_dirs=[], required_env=[],
        login=None, step_env={"CMRU_TEST_LOCAL": "scoped"}, env_command=None, quiet=False,
    )
    monkeypatch.delenv("CMRU_TEST_LOCAL", raising=False)
    runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert "CMRU_TEST_LOCAL" not in os.environ
    assert "2 passed" in (tmp_path / "logs" / "tests.log").read_text()


def test_runner_nonzero_real_command_surfaces_failure_and_keeps_log(tmp_path):
    step = runner.StepConfig(
        name="fail", commands=[{"label": "bad", "argv": [sys.executable, "-c", "raise SystemExit(3)"], "cwd": "."}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None, clean_dirs=[], required_env=[],
        login=None, step_env={}, env_command=None, quiet=True,
    )
    with pytest.raises(subprocess.CalledProcessError) as raised:
        runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert raised.value.returncode == 3
    assert (tmp_path / "logs" / "fail.log").is_file()


def test_runner_command_success_evidence_rejects_generic_output():
    assert runner._success_evidence(["done\n"]) is None
    assert runner._success_evidence(["Ran 1 test in 0.01s\n", "OK\n"]) is not None


def test_runner_bake_and_cache_flags_change_real_command_argv(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("VAR", "value")
    monkeypatch.setenv("NO_CACHE", "1")
    monkeypatch.setattr(runner, "run_command", lambda argv, *a, **k: calls.append(argv) or runner.CommandResult(0.0, None))
    step = runner.StepConfig("build", [{"label": "b", "argv": ["tool"], "cwd": "."}],
                             "set.", ["VAR"], "NO_CACHE", [], [], None, {}, None, False)
    runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert calls == [["tool", "--set", "set.VAR=value", "--no-cache"]]


def test_handler_extra_asset_glob_missing_is_refused(tmp_path, monkeypatch):
    args = SimpleNamespace(cwd=str(tmp_path), prefix="demo", glob="*.whl", notes_env=None,
                           extra_asset=[str(tmp_path / "missing-*.json")])
    monkeypatch.setenv("GITHUB_PUSH_PAT", "t")
    monkeypatch.setenv("GITHUB_USERNAME", "o")
    monkeypatch.setenv("GITHUB_REPO", "r")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "demo-1.whl").write_bytes(b"wheel")
    with patch.object(handlers, "read_wheel_version", return_value="1.0.0"):
        with pytest.raises(SystemExit) as raised:
            handlers.cmd_wheel_publish(args)
    assert "matched no existing file" in str(raised.value)


def test_handler_oci_build_repack_refuses_before_prerequisites():
    with pytest.raises(SystemExit) as raised:
        handlers._reject_experimental_repack(True)
    assert raised.value.code == 2


def test_bundle_copy_sources_applies_excludes_and_client_output(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "ok.py").write_text("ok")
    (root / "src" / "secret.key").write_text("secret")
    dist = root / "dist"
    client = dist / "client"
    (client / "pkg").mkdir(parents=True)
    (client / "pkg" / "__init__.py").write_text("x=1")
    bundle_dir = dist / "bundle"
    cfg = bundle.BundleConfig(root, root, dist, bundle_dir, client, False, "python", None,
                              "x-{version}.tar.xz", "VERSION", "xztar", [], ["src"])
    bundle.copy_sources(cfg)
    assert (bundle_dir / "src" / "ok.py").exists()
    assert not (bundle_dir / "src" / "secret.key").exists()
    assert (bundle_dir / "client" / "pkg" / "__init__.py").exists()


def test_bundle_build_wheel_command_uses_find_links_and_project_cwd(tmp_path):
    cfg = bundle.BundleConfig(tmp_path, tmp_path / "client", tmp_path / "dist", tmp_path / "bundle",
                              tmp_path / "client-dist", True, "python", tmp_path / "wheelhouse",
                              "x-{version}.tar.xz", "VERSION", "xztar", [], [])
    with patch.object(bundle.subprocess, "run") as run:
        bundle.build_wheel(cfg)
    argv = run.call_args.args[0]
    assert argv[-4:] == ["-w", str(tmp_path / "client-dist"), "--find-links", str(tmp_path / "wheelhouse")]
    assert run.call_args.kwargs["cwd"] == str(tmp_path / "client")


def test_bundle_non_xz_archive_requires_version_and_produces_tarball(tmp_path, monkeypatch):
    root = tmp_path / "p"
    (root / "dist" / "bundle").mkdir(parents=True)
    (root / "dist" / "bundle" / "a.txt").write_text("a")
    cfg = bundle.BundleConfig(root, root, root / "dist", root / "dist" / "bundle", root / "dist" / "client",
                              False, "python", None, "demo-{version}.tar.gz", "V", "gztar", [], [])
    monkeypatch.setenv("V", "1")
    out = bundle.create_archive(cfg)
    assert out.exists()


def test_changelog_subject_groups_classify_and_render_empty_release():
    with patch.object(changelog, "_git", return_value="abc123\x1ffeat: new thing\ndef456\x1ffix: bug\n"):
        groups = changelog._subject_groups(Path("."), None, ["demo"], exclude_paths=[])
    assert list(groups)[:2] == ["Added", "Fixed"]
    section = changelog._render_section("1.0.0", {}, source_end="a" * 40, date="2026-01-01")
    assert "Release metadata prepared" in section


def test_changelog_cursor_and_generated_exclusions_are_source_facts(tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", commit_generated=("generated.json",))
    path = tmp_path / "demo" / "CHANGES.md"
    path.parent.mkdir()
    assert changelog._last_generated_source_end("<!-- cmru: source-end=" + "a" * 40 + " -->") == "a" * 40
    assert changelog._generated_exclusions(project, path, tmp_path) == ["demo/CHANGES.md", "demo/generated.json"]


def test_changelog_backfill_wrong_and_missing_git_tag_facts_fail(tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md", prefix="demo-v")
    with pytest.raises(RuntimeError, match="release tag"):
        changelog.backfill_release_changelog(tmp_path, project, "other-v1")
