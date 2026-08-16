"""Remaining version, release, transaction and controller outcome witnesses."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import release, transaction, version
from cmru.controller import cli as controller_cli
from cmru.controller.rollout import RolloutEngine


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "x@y.invalid"); git(root, "config", "user.name", "x")
    (root / "x").write_text("x"); git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def test_version_file_strategy_noop_and_counter_first_tag(tmp_path):
    root = repo(tmp_path)
    (root / "VERSION").write_text("1.0.0\n"); git(root, "add", "VERSION"); git(root, "commit", "-q", "-m", "version")
    assert version._apply_strategy_file(root, "demo-v", "1.0.0", "VERSION", root) == "demo-v1.0.0"
    assert version._next_counter_version(root, "demo-v", "1.0.0") == "1.0.0-r1"


def test_version_external_empty_value_and_bad_git_are_refused(tmp_path):
    (tmp_path / "cmru.vars").write_text("VERSION=\n")
    with pytest.raises(RuntimeError, match="does not define"):
        version._external_version(tmp_path, "VERSION")
    with patch.object(version.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="bad", stdout="")):
        with pytest.raises(RuntimeError, match="git"):
            version._git(tmp_path, "status")


def test_release_http_json_and_missing_release_shape_fail_closed():
    gh = release.GitHubReleases("o", "r", "t")
    with patch.object(gh, "_request", return_value=(200, "not-json")):
        with pytest.raises(json.JSONDecodeError):
            gh.list_releases()
    with patch.object(gh, "_request", return_value=(200, '{"id": 1}')):
        with pytest.raises(SystemExit):
            gh.publish("tag", "title", "notes", [])


def test_release_validate_latest_retries_then_accepts_hash_bound_asset():
    class Fake:
        def __init__(self): self.calls = 0
        def resolve_latest(self, _):
            self.calls += 1
            return None if self.calls == 1 else {"version": "1", "tag": "demo-v1", "assets": [
                {"name": "demo.whl", "url": "u"}, {"name": "demo.whl.sha256", "url": "h"}]}
    fake = Fake()
    with patch("time.sleep"):
        info = release.validate_latest_release(fake, "demo", retries=2, delay=0)
    assert info["sha256_url"] == "h"


def test_transaction_read_progress_handles_empty_and_write_result_rejects_corrupt_record(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/a", "a" * 40)
    assert transaction.read_release_progress(root, workspace) is None
    transaction.write_release_progress(root, workspace, "b" * 40)
    assert transaction.read_release_progress(root, workspace) == "b" * 40
    scope = transaction._scope_dir(root); scope.mkdir(exist_ok=True)
    (scope / "a.results.json").write_text("[]")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.write_release_result(root, workspace, "demo", "tag")


def test_transaction_sync_non_main_with_unrelated_local_main_returns_false(tmp_path):
    root = repo(tmp_path)
    git(root, "branch", "main-local")
    calls = []
    with patch.object(transaction.subprocess, "run", side_effect=lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0)), \
         patch.object(transaction, "_git", side_effect=["feature", "local", "origin", "base"]):
        assert transaction.sync_local_main(root) is False
    assert not any(argv[:2] == ["git", "branch"] and "-f" in argv for argv in calls)
    assert not any(argv[:2] == ["git", "checkout"] for argv in calls)


def test_controller_cli_reports_missing_plan_and_engine_failure(tmp_path, capsys, monkeypatch):
    args = SimpleNamespace(plan=str(tmp_path / "missing.toml"), landscape=None)
    assert controller_cli.cmd_publish(args) == 2
    plan = tmp_path / "plan.toml"
    plan.write_text("[plan]\nid='p'\nlandscape='prod'\nrelease_tag='r'\nmanifest_url='u'\nmanifest_sha256='s'\n[[plan.waves]]\nphase=1\nname='c'\ntype='canary'\nnodes=['n']\nprofiles=['p']\n")
    args.plan = str(plan)
    monkeypatch.setattr(controller_cli, "_build_engine", lambda *_: SimpleNamespace(publish=lambda _p: (_ for _ in ()).throw(RuntimeError("broken"))))
    assert controller_cli.cmd_publish(args) == 1
    assert "Publish failed" in capsys.readouterr().err


def test_controller_approve_hold_argument_refusal_without_backend():
    assert controller_cli.cmd_approve(SimpleNamespace(plan=None, landscape=None)) == 2
    assert controller_cli.cmd_hold(SimpleNamespace(plan="", landscape=None)) == 2


def test_rollout_write_wave_surfaces_backend_status_without_mutating_plan():
    class Backend:
        def __init__(self): self.calls=[]
        def _put(self, key, body): self.calls.append((key, body)); return (500, "no")
    from cmru.controller.planner import PlanStep
    step = PlanStep("p", "c", 1, "canary", ["n"], ["x"], "r", "u", "s", "h", "p.c")
    backend = Backend(); RolloutEngine(backend, "prod")._write_wave(step)
    assert backend.calls and b'"generation": 101' in backend.calls[0][1]
