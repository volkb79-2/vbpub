"""CIU-200 / CIU-198 -- `ciu clean` refuses a reset it cannot perform BEFORE
removing anything, and reports the same refusal in `--dry-run`.

Shape found on tls-edge (ciu 7.23.1): the rendered config had no
``deploy.labels.prefix``; step 1 removed the containers, then step 2's
``reset_service`` refused, leaving volumes, vol-* dirs and rendered files.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def _profile(deploy_cfg):
    profile = MagicMock()
    profile.config = {"deploy": deploy_cfg}
    return profile


def _selection(tmp_path, name="stack-a"):
    (tmp_path / name).mkdir(exist_ok=True)
    return [{"path": name, "service": {}}]


def _stub(monkeypatch, rendered, calls):
    standing = ["p-e-x"]

    def listing(*a, **k):
        names, standing[:] = list(standing), []  # gone after the first removal pass
        return names

    monkeypatch.setattr(deploy, "_matching_containers", listing)
    monkeypatch.setattr(deploy, "_stack_project_containers", listing)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: rendered)
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_tagged_project_networks", lambda *a, **k: ([], []))
    monkeypatch.setattr(deploy, "_workspace_identity_network", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_list_stack_project_networks", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_remove_identity_networks", lambda nets: ([], []))
    monkeypatch.setattr(deploy, "_network_exists", lambda net: False)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *a, **k: [])
    monkeypatch.setattr(deploy.worktree_pkg, "release_own_lease", lambda *a, **k: None)
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda args, **k: calls.append(list(args)) or MagicMock(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        deploy.engine, "reset_service", lambda *a, **k: calls.append(["reset"]),
    )


TAGGED = {"project_name": "p", "environment_tag": "e"}
UNNAMED = {"environment_tag": "e"}  # no project_name -> reset refuses


def test_missing_label_prefix_no_longer_refuses(monkeypatch, tmp_path):
    """The tls-edge shape: project_name present, no [deploy.labels]."""
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls)
    rc = deploy.action_clean(tmp_path, _profile(TAGGED), _selection(tmp_path), ignore_errors=False)
    assert rc == 0
    assert ["reset"] in calls


def test_refusal_happens_before_any_container_is_removed(monkeypatch, tmp_path, capsys):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls)
    rc = deploy.action_clean(
        tmp_path, _profile({"environment_tag": "e"}), _selection(tmp_path), ignore_errors=False,
    )
    assert rc == 1
    assert calls == []  # no `docker rm`, no reset, nothing touched
    out = capsys.readouterr()
    assert "[CIU-200]" in out.err + out.out
    assert "stack-a: deploy.project_name is required for reset" in out.err + out.out
    assert "nothing was removed" in out.err + out.out


def test_dry_run_reports_the_refusal(monkeypatch, tmp_path, capsys):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls)
    rc = deploy.action_clean(
        tmp_path, _profile({"environment_tag": "e"}), _selection(tmp_path),
        ignore_errors=False, plan_only=True,
    )
    assert rc == 1
    out = capsys.readouterr()
    assert "plan: clean would refuse to reset stack-a: deploy.project_name" in out.err + out.out
    assert ["reset"] not in calls


def test_dry_run_clean_config_still_succeeds(monkeypatch, tmp_path):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls)
    assert deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path), ignore_errors=False, plan_only=True,
    ) == 0


def test_ignore_errors_keeps_the_best_effort_path(monkeypatch, tmp_path):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls)
    monkeypatch.setattr(
        deploy.engine, "reset_service",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("refused")),
    )
    rc = deploy.action_clean(
        tmp_path, _profile({"environment_tag": "e"}), _selection(tmp_path), ignore_errors=True,
    )
    assert rc == 1
    assert ["rm", "-f", "p-e-x"] in calls or any(c[:2] == ["rm", "-f"] for c in calls)


def test_missing_stack_dir_and_shipped_stack_are_not_preflighted(monkeypatch, tmp_path):
    calls: list = []
    _stub(monkeypatch, {"gone": {}}, calls)
    selection = [{"path": "gone", "service": {}}, {"path": "shipped", "service": {}}]
    assert deploy.action_clean(
        tmp_path, _profile({"environment_tag": "e"}), selection, ignore_errors=False,
        plan_only=True,
    ) == 0
