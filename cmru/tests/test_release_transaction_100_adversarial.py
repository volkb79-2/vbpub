"""Final transaction/controller release boundary witnesses."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import release, transaction, version
from cmru.controller.rollout import RolloutEngine, _build_desired_json
from cmru.controller.planner import LandscapePlan, PlanStep


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "x").write_text("x")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def step(*, nodes=None, required=True, approval=False, phase=1):
    return PlanStep("plan", "wave", phase, "production", nodes or ["n1"], ["core"],
                    "demo-v1", "https://m", "a" * 64, "b" * 64, "plan.step",
                    required, approval)


class Backend:
    def __init__(self, observed=None, puts=None, gets=None):
        self.observed = observed or {}
        self.puts = puts if puts is not None else []
        self.gets = gets or []
    def _put(self, key, body): self.puts.append((key, body)); return (200, "")
    def _delete(self, key): self.puts.append(("DELETE", key)); return (200, "")
    def _get(self, key): return self.gets.pop(0) if self.gets else (404, "", {})
    def read_observed(self, node, landscape): return self.observed.get(node)


def test_desired_json_is_stable_contract_and_rollback_action_is_explicit():
    payload = json.loads(_build_desired_json(step(), 7, "rollback"))
    assert payload["generation"] == 7
    assert payload["action"] == "rollback"
    assert payload["release"]["manifest_sha256"] == "a" * 64


def test_rollout_publish_writes_wave_then_status_and_stops_on_failed_observation(monkeypatch):
    backend = Backend(observed={"n1": json.dumps({"schema_version": 1, "health": "failed", "applied_generation": 1, "adapter_phase": "apply", "error_class": "bad"})})
    engine = RolloutEngine(backend, "prod", poll_interval=0, wave_timeout=0)
    engine.publish(LandscapePlan("plan", "prod", [step()]))
    assert any("desired" in key for key, _ in backend.puts if key != "DELETE")
    status = [body for key, body in backend.puts if "plans/plan/status" in key][-1]
    assert json.loads(status)["status"] == "failed"


def test_rollout_wait_accepts_healthy_generation_and_ignores_malformed_observation():
    good = json.dumps({"schema_version": 1, "health": "healthy", "applied_generation": 101, "adapter_phase": "done", "error_class": None})
    backend = Backend(observed={"n1": good})
    engine = RolloutEngine(backend, "prod", poll_interval=0, wave_timeout=1)
    assert engine._wait_for_wave("plan", step()) is True
    bad = Backend(observed={"n1": "not-json"})
    engine = RolloutEngine(bad, "prod", poll_interval=0, wave_timeout=0)
    assert engine._wait_for_wave("plan", step()) is False


def test_rollout_approval_and_hold_poll_until_external_release(monkeypatch):
    backend = Backend(gets=[(404, "", {}), (200, "approved", {})])
    engine = RolloutEngine(backend, "prod", poll_interval=0)
    engine._wait_for_approval_if_needed("plan", step(approval=True))
    backend = Backend(gets=[(200, "hold", {}), (404, "", {})])
    engine = RolloutEngine(backend, "prod", poll_interval=0)
    engine._check_hold("plan")


def test_rollout_dry_run_and_rollback_target_override_do_not_write_backend():
    backend = Backend()
    engine = RolloutEngine(backend, "prod", dry_run=True)
    plan = LandscapePlan("plan", "prod", [step(nodes=["a", "b"])])
    engine.publish(plan)
    engine.rollback(plan, to_tag="old-v2", generation=99)
    assert not any("/nodes/" in key for key, _ in backend.puts)


def test_rollout_status_reports_standby_and_unknown_observation():
    backend = Backend(observed={"n1": "broken"})
    result = RolloutEngine(backend, "prod").status(LandscapePlan("plan", "prod", [step(nodes=["n1", "n2"]) ]))
    assert result["nodes"]["n1"]["health"] == "unknown"
    assert result["nodes"]["n2"]["health"] == "standby"


def test_transaction_promotion_non_rejection_fails_without_rebase(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", git(root, "rev-parse", "HEAD"))
    result = SimpleNamespace(returncode=1, stderr="protected branch", stdout="")
    original = transaction.subprocess.run
    def fail_push(argv, **kwargs):
        if len(argv) > 1 and argv[1] == "push":
            return result
        return original(argv, **kwargs)
    with patch.object(transaction.subprocess, "run", side_effect=fail_push):
        with pytest.raises(RuntimeError, match="push"):
            transaction.promote_workspace(workspace)


def test_transaction_backup_branch_uses_force_and_cleanup_is_best_effort(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", git(root, "rev-parse", "HEAD"))
    calls = []
    with patch.object(transaction.subprocess, "run", side_effect=lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0)):
        transaction.push_backup_branch(workspace)
        transaction.remove_backup_branch(workspace)
    assert ["--force", "origin", "HEAD:refs/heads/cmru/release/x"][-3:] == calls[0][-3:]
    assert "--delete" in calls[1]


def test_transaction_retain_release_logs_only_and_uses_immutable_destination(tmp_path):
    root = repo(tmp_path)
    child = tmp_path / "child"
    git(root, "worktree", "add", "-q", "-b", "cmru/release/log", str(child), "main")
    (child / "demo").mkdir(exist_ok=True)
    (child / "demo" / "logs").mkdir()
    (child / "demo" / "logs" / "gate.log").write_text("pass")
    project = SimpleNamespace(name="demo", project_root=root / "demo", artifact_dirs=())
    workspace = transaction.ReleaseWorkspace(root, child, "cmru/release/log", git(child, "rev-parse", "HEAD"))
    retained = transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "demo-v1"}, retain_logs=True, retain_artifacts=False)
    assert retained and (root / "demo" / "logs" / "cmru-release" / "demo-v1" / "gate.log").exists()
    git(root, "worktree", "remove", "--force", str(child)); git(root, "branch", "-D", "cmru/release/log")


def test_version_release_file_dry_run_does_not_write(tmp_path):
    target = tmp_path / "VERSION"
    result = version._apply_strategy_file(tmp_path, "demo-v", "1.2.3", "VERSION", tmp_path, dry_run=True)
    assert result == "demo-v1.2.3" and not target.exists()


def test_version_detect_changed_first_release_and_clean_tagged_project(tmp_path):
    root = repo(tmp_path)
    first = SimpleNamespace(name="new", cwd="new", paths=["new"], prefix="new-v", version=SimpleNamespace(bump="conventional"))
    (root / "new").mkdir(); (root / "new" / "x").write_text("x")
    assert version.detect_changed_projects(root, {"new": first})[0][0] == "new"
    tagged = SimpleNamespace(name="demo", cwd=".", paths=["."], prefix="demo-v", version=SimpleNamespace(bump="conventional"))
    git(root, "tag", "demo-v1.0.0")
    assert version.detect_changed_projects(root, {"demo": tagged}) == []


def test_release_latest_validation_refuses_missing_sha_sidecar():
    fake = SimpleNamespace(resolve_latest=lambda _: {"version": "1", "tag": "demo-v1", "assets": [{"name": "demo.whl", "url": "u"}]})
    with pytest.raises(SystemExit):
        release.validate_latest_release(fake, "demo", retries=1, delay=0)
