"""CIU-141 (round 2): `ciu clean` refuses on a foreign one-shot BEFORE any removal.

The guard used to run only inside ``reset_service`` (clean's step 2), after step 1
had already removed the checkout's containers, so a refusal left a half-torn-down
checkout. It is now also evaluated in clean's pre-validation block.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ciu import deploy, engine

TAGGED = {"project_name": "p", "environment_tag": "e"}


def _profile(deploy_cfg):
    profile = MagicMock()
    profile.config = {"deploy": deploy_cfg}
    return profile


def _selection(tmp_path, *names, shipped=()):
    selection = []
    for name in names:
        (tmp_path / name).mkdir(exist_ok=True)
        selection.append({"path": name, "service": {"shipped": name in shipped}})
    return selection


def _stub(monkeypatch, rendered, calls, *, guard=None):
    standing = ["p-e-x"]

    def listing(*a, **k):
        names, standing[:] = list(standing), []
        return names

    monkeypatch.setattr(deploy, "_matching_containers", listing)
    monkeypatch.setattr(deploy, "_stack_project_containers", listing)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: rendered)
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *a, **k: calls.append(["volumes"]) or [])
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
    guarded: list = []

    def guard_stub(config, stack_dir, *, repo_root):
        guarded.append((config["deploy"].get("project_name"), Path(stack_dir).name))
        if guard is not None:
            guard()

    monkeypatch.setattr(deploy.engine, "guard_clean_stack", guard_stub)
    return guarded


def _refuse():
    raise engine.ComposeError("[CIU-141] refusing clean: one-shot abc belongs to another checkout")


def test_foreign_one_shot_refusal_removes_nothing(monkeypatch, tmp_path, capsys):
    calls: list = []
    guarded = _stub(monkeypatch, {"stack-a": {}}, calls, guard=_refuse)

    rc = deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a"), ignore_errors=False,
    )

    out = capsys.readouterr()
    assert rc == 1
    assert calls == []  # no `docker rm -f`, no reset, no volume removal
    assert guarded == [("p", "stack-a")]
    text = out.err + out.out
    assert "[CIU-141] clean refuses: stack-a: [CIU-141] refusing clean" in text
    assert "nothing was removed" in text


def test_foreign_one_shot_dry_run_reports_the_refusal(monkeypatch, tmp_path, capsys):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls, guard=_refuse)

    rc = deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a"), ignore_errors=False,
        plan_only=True,
    )

    out = capsys.readouterr()
    assert rc == 1
    assert "plan: clean would refuse: stack-a:" in out.err + out.out
    assert ["reset"] not in calls


def test_clean_proceeds_when_the_guard_passes(monkeypatch, tmp_path):
    calls: list = []
    guarded = _stub(monkeypatch, {"stack-a": {}, "stack-b": {}}, calls)

    rc = deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a", "stack-b"),
        ignore_errors=False,
    )

    assert rc == 0
    assert [name for _project, name in guarded] == ["stack-a", "stack-b"]
    assert any(call[:2] == ["rm", "-f"] for call in calls) and ["reset"] in calls


def test_one_refusing_stack_among_several_still_removes_nothing(monkeypatch, tmp_path):
    calls: list = []
    state = {"n": 0}

    def second_refuses():
        state["n"] += 1
        if state["n"] == 2:
            _refuse()

    _stub(monkeypatch, {"stack-a": {}, "stack-b": {}}, calls, guard=second_refuses)

    rc = deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a", "stack-b"),
        ignore_errors=False,
    )

    assert rc == 1 and calls == []


def test_ignore_errors_keeps_best_effort_and_reset_service_stays_the_second_guard(
    monkeypatch, tmp_path,
):
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls, guard=_refuse)

    deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a"), ignore_errors=True,
    )

    # --ignore-errors does not block the ordinary passes; reset_service (stubbed
    # here) still carries its own guard call for the foreign one-shots.
    assert any(call[:2] == ["rm", "-f"] for call in calls)
    assert ["reset"] in calls


def test_missing_docker_does_not_pre_refuse_and_shipped_or_refused_stacks_skip_the_guard(
    monkeypatch, tmp_path,
):
    calls: list = []

    def no_docker():
        raise FileNotFoundError("docker")

    guarded = _stub(monkeypatch, {"stack-a": {}}, calls, guard=no_docker)
    rc = deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a"), ignore_errors=False,
    )
    assert rc == 0 and guarded == [("p", "stack-a")]  # later steps report docker problems

    calls.clear()
    guarded = _stub(monkeypatch, {"stack-a": {}, "ship": {}}, calls)
    assert deploy.action_clean(
        tmp_path, _profile(TAGGED), _selection(tmp_path, "stack-a", "ship", shipped=("ship",)),
        ignore_errors=False,
    ) == 0
    assert [name for _project, name in guarded] == ["stack-a"]

    calls.clear()
    guarded = _stub(monkeypatch, {"stack-a": {}}, calls)
    deploy.action_clean(  # no project_name: reset itself refuses first, guard not asked
        tmp_path, _profile({"environment_tag": "e"}), _selection(tmp_path, "stack-a"),
        ignore_errors=False,
    )
    assert guarded == []


def _undetermined_project():
    raise ValueError("[S8.7] deploy.project_name set but declares no repo_name/instance_id")


@pytest.mark.parametrize("mode", ["clean", "dry-run", "ignore-errors"])
def test_undeterminable_project_is_reported_not_a_traceback(monkeypatch, tmp_path, capsys, mode):
    """project_name without environment_tag or identity facts: ValueError, not ComposeError."""
    calls: list = []
    _stub(monkeypatch, {"stack-a": {}}, calls, guard=_undetermined_project)
    if mode == "ignore-errors":
        monkeypatch.setattr(
            deploy.engine, "reset_service",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("reset: no identity")),
        )

    rc = deploy.action_clean(
        tmp_path, _profile({"project_name": "p"}), _selection(tmp_path, "stack-a"),
        ignore_errors=mode == "ignore-errors", plan_only=mode == "dry-run",
    )

    out = capsys.readouterr()
    text = out.err + out.out
    assert rc == 1
    assert "Traceback" not in text
    if mode != "ignore-errors":
        assert "no repo_name/instance_id" in text
    if mode == "ignore-errors":
        assert "reset failed for stack-a" in text  # the old best-effort message path
        assert any(call[:2] == ["rm", "-f"] for call in calls)
    elif mode == "dry-run":
        assert "plan: clean would refuse: stack-a:" in text
        assert calls == []
    else:
        assert "nothing was removed" in text
        assert calls == []


@pytest.mark.real_clean_guard
@pytest.mark.parametrize("mode", ["clean", "dry-run", "ignore-errors"])
def test_real_guard_with_undeterminable_project_does_not_escape(
    monkeypatch, tmp_path, capsys, mode,
):
    calls: list = []
    real_guard = engine.guard_clean_stack  # marker: conftest left the real function
    _stub(monkeypatch, {"stack-a": {}}, calls)
    monkeypatch.setattr(engine, "guard_clean_stack", real_guard)
    monkeypatch.setattr(
        engine, "identity_compose_project_name",
        lambda root, stack_dir: (_ for _ in ()).throw(ValueError("no repo_name/instance_id")),
    )
    if mode == "ignore-errors":
        monkeypatch.setattr(
            deploy.engine, "reset_service",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("reset: no identity")),
        )

    rc = deploy.action_clean(
        tmp_path, _profile({"project_name": "p"}), _selection(tmp_path, "stack-a"),
        ignore_errors=mode == "ignore-errors", plan_only=mode == "dry-run",
    )

    out = capsys.readouterr()
    assert rc == 1
    expected = "reset failed for stack-a" if mode == "ignore-errors" else "no repo_name/instance_id"
    assert expected in out.err + out.out


@pytest.mark.real_clean_guard
def test_guard_clean_stack_derives_the_project_reset_service_uses(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        engine, "guard_clean_one_shots",
        lambda project, *, repo_root, stack_dir: seen.append((project, repo_root, stack_dir)),
    )
    stack = tmp_path / "tools" / "runner"
    stack.mkdir(parents=True)

    engine.guard_clean_stack({"deploy": TAGGED}, stack, repo_root=tmp_path)
    monkeypatch.setattr(
        engine, "identity_compose_project_name", lambda root, stack_dir: "identity-project",
    )
    engine.guard_clean_stack({"deploy": {}}, stack, repo_root=tmp_path)

    assert seen == [
        ("p-e-runner", tmp_path, stack.resolve()),
        ("identity-project", tmp_path, stack.resolve()),
    ]
    with pytest.raises(ValueError, match="no repo_root was provided"):
        engine.reset_compose_project({"deploy": {}}, stack, None)
