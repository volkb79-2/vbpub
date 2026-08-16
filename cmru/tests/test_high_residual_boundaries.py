"""Behavior-led tests for high-residual CMRU source boundaries."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, runner, transaction, version


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_config_deep_merge_and_secret_precedence_are_explicit(monkeypatch):
    assert config._deep_merge({"github": {"owner": "o", "token": "root"}, "x": 1},
                              {"github": {"token": "project"}}) == {
                                  "github": {"owner": "o", "token": "project"}, "x": 1}
    monkeypatch.setenv("GITHUB_PUSH_PAT", "environment")
    assert config._environment_token() == "environment"
    monkeypatch.delenv("GITHUB_PUSH_PAT")
    monkeypatch.setenv("GITHUB_TOKEN", "legacy")
    assert config._environment_token() == "legacy"


def test_config_runner_validation_rejects_unknown_command_login_and_empty_values():
    with pytest.raises(SystemExit):
        config._validate_runner_steps({"build": {"commands": [{"label": "x", "argv": ["true"], "cwd": ".", "bad": 1}], "quiet": True}})
    with pytest.raises(SystemExit):
        config._validate_runner_steps({"build": {"commands": [{"label": "x", "argv": ["true"], "cwd": "."}], "quiet": True,
                                               "login": {"registry": "r", "username_env": "U", "token_env": "T"}}})
    with pytest.raises(SystemExit):
        config._targets({"host": "", "registry": []})


def test_config_cleanup_requires_complete_lists_and_positive_age():
    with pytest.raises(SystemExit):
        config._parse_cleanup({"max_age_days": 0, "release_tag_prefixes": [], "keep_release_tags": [], "ghcr_packages": [], "ghcr_delete_packages": []})
    with pytest.raises(SystemExit):
        config._parse_cleanup({"release_tag_prefixes": [], "keep_release_tags": [], "ghcr_packages": []})


def test_runner_evidence_accepts_unittest_and_rejects_similar_noise():
    assert runner._success_evidence(["Ran 2 tests in 0.1s\n", "OK (skipped=1)\n"])
    assert runner._success_evidence(["2 passed\n"]) is None
    assert runner._truthy_env("MISSING_ENV_FOR_CMRU") is False


def test_runner_aggregate_log_does_not_duplicate_local_log(tmp_path, monkeypatch):
    local = tmp_path / "step.log"
    monkeypatch.setenv("CMRU_RUN_LOG", str(local))
    assert runner._open_aggregate_log(local, quiet=True) is None
    monkeypatch.setenv("CMRU_RUN_LOG", str(tmp_path / "all.log"))
    handle = runner._open_aggregate_log(local, quiet=True)
    assert handle is not None
    handle.close()


def test_transaction_digest_tree_is_sorted_and_content_authenticated(tmp_path):
    (tmp_path / "z").write_text("z")
    (tmp_path / "a").write_text("a")
    entries = transaction._digest_tree(tmp_path)
    assert [entry["path"] for entry in entries] == ["a", "z"]
    assert entries[0]["bytes"] == "1"
    assert entries[0]["sha256"] == hashlib.sha256(b"a").hexdigest()
    assert entries[1]["sha256"] == hashlib.sha256(b"z").hexdigest()

    (tmp_path / "a").write_text("b")
    changed = transaction._digest_tree(tmp_path)
    assert changed[0]["bytes"] == entries[0]["bytes"] == "1"
    assert changed[0]["sha256"] != entries[0]["sha256"]


def test_transaction_workspace_roots_reject_outside_project(tmp_path):
    project = SimpleNamespace(project_root=tmp_path.parent / "outside")
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "", "")
    with pytest.raises(RuntimeError, match="outside repository"):
        transaction._project_roots_for_retention(tmp_path, workspace, project, "demo")


def test_version_bump_rejects_non_semver_and_breaking_commit_precedes_feature():
    with pytest.raises(ValueError):
        version._parse_semver("1.2")
    assert version._bump_from_commits(["feat: add", "fix!: break"]) == "major"
    assert version.bump_version("1.2.3", "minor") == "1.3.0"


def test_version_counter_handles_no_numeric_suffix_without_inventing_existing_count(tmp_path):
    with patch.object(version.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="demo-v1.0.0-rbad\n")):
        assert version._next_counter_version(tmp_path, "demo-v", "1.0.0") == "1.0.0-r1"
