"""CIU-141 reaping is label-scoped, owner-aware, and exact-ID only."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ciu import engine, oneshot


def _proc_fixture(root: Path):
    proc = root / "proc"
    (proc / "self" / "ns").mkdir(parents=True)
    self_ns = proc / "self" / "ns" / "pid"
    self_ns.write_text("same namespace")
    boot_id = "boot-for-tests"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(boot_id)
    namespace = self_ns.stat().st_ino

    def add_process(pid: int, *, start: int, state: str = "S", foreign_ns: bool = False):
        process = proc / str(pid)
        (process / "ns").mkdir(parents=True)
        fields = [state, *(["0"] * 18), str(start)]
        (process / "stat").write_text(f"{pid} (worker test) {' '.join(fields)}\n")
        if foreign_ns:
            (process / "ns" / "pid").write_text("foreign namespace")
        else:
            os.link(self_ns, process / "ns" / "pid")

    return proc, boot_id, str(namespace), add_process


def _container(container_id, *, owner_pid, owner_start, boot, pidns, status,
               started, repo_root, instance="instance-1"):
    return {
        "Id": container_id,
        "Name": f"/run-{container_id[:8]}",
        "Config": {"Labels": {
            "com.docker.compose.project": "demo-runner",
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.project.working_dir": str(repo_root),
            "ciu.run": "1",
            "ciu.run.id": f"run-{container_id[:8]}",
            "ciu.run.owner-pid": str(owner_pid),
            "ciu.run.owner-start": str(owner_start),
            "ciu.run.boot-id": boot,
            "ciu.run.pidns": pidns,
            "ciu.run.started": started,
            "ciu.instance": instance,
            "ciu.repo-root": str(repo_root),
        }},
        "State": {"Status": status, "ExitCode": 0},
    }


def test_reap_removes_only_dead_owned_one_shots_by_full_id(tmp_path, monkeypatch):
    proc, boot, pidns, add_process = _proc_fixture(tmp_path)
    add_process(501, start=1001)
    add_process(502, start=2002)
    add_process(503, start=3003, foreign_ns=True)
    root = tmp_path / "checkout"
    root.mkdir()
    instant = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    old = (instant - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    recent = (instant - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    ids = [f"{n:064x}" for n in range(1, 7)]
    documents = {
        ids[0]: _container(ids[0], owner_pid=500, owner_start=900, boot=boot,
                           pidns=pidns, status="exited", started=recent, repo_root=root),
        # Existing pid with a different start tick: the original owner is gone.
        ids[1]: _container(ids[1], owner_pid=501, owner_start=9999, boot=boot,
                           pidns=pidns, status="running", started=old, repo_root=root),
        # Same owner identity and recent start: leave the live lane alone.
        ids[2]: _container(ids[2], owner_pid=501, owner_start=1001, boot=boot,
                           pidns=pidns, status="running", started=old, repo_root=root),
        # A different PID namespace makes owner liveness unknown, so leave it.
        ids[3]: _container(ids[3], owner_pid=503, owner_start=3003, boot=boot,
                           pidns=pidns, status="running", started=old, repo_root=root),
        # A dead owner is not enough to remove a recent running one-shot.
        ids[4]: _container(ids[4], owner_pid=599, owner_start=800, boot=boot,
                           pidns=pidns, status="running", started=recent, repo_root=root),
        # Foreign instance ownership must be left, even when the owner is dead.
        ids[5]: _container(ids[5], owner_pid=598, owner_start=700, boot=boot,
                           pidns=pidns, status="exited", started=recent,
                           repo_root=root, instance="other-instance"),
    }
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(ids) + "\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([documents[argv[-1]]]), "")
        if argv[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(argv, 0, argv[-1] + "\n", "")
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(oneshot, "_PROC_ROOT", proc, raising=False)
    monkeypatch.setattr(
        engine, "workspace_ownership_labels",
        lambda _root: {"ciu.instance": "instance-1", "ciu.repo-root": str(root)},
        raising=False,
    )

    report = oneshot.reap_project(
        root, "demo-runner", older_than_seconds=3600, yes=True, now=instant,
    )

    actions = {item["id"]: item["action"] for item in report}
    assert actions == {
        ids[0]: "removed", ids[1]: "removed", ids[2]: "left",
        ids[3]: "left", ids[4]: "left", ids[5]: "left",
    }
    removals = [argv for argv in calls if argv[:3] == ["docker", "rm", "-f"]]
    assert removals == [["docker", "rm", "-f", ids[0]], ["docker", "rm", "-f", ids[1]]]
    query = next(argv for argv in calls if argv[:3] == ["docker", "ps", "-a"])
    assert "--no-trunc" in query
    assert "--filter" in query
    assert "label=com.docker.compose.project=demo-runner" in query
    assert "label=com.docker.compose.oneoff=True" in query
    assert "label=ciu.run=1" in query
    assert "label=ciu.instance=instance-1" in query
    assert "label=ciu.repo-root=" + str(root) in query
    assert not any("ancestor" in arg or "image" in arg for arg in query)
    assert all(argv[-1] in ids for argv in calls if argv[:2] == ["docker", "inspect"])


def test_reap_without_yes_is_a_survey_and_reap_cli_discovers_current_projects(
    tmp_path, monkeypatch, capsys,
):
    proc, boot, pidns, _add_process = _proc_fixture(tmp_path)
    root = tmp_path / "checkout"
    root.mkdir()
    container_id = "f" * 64
    document = _container(
        container_id, owner_pid=900, owner_start=10, boot=boot, pidns=pidns,
        status="exited", started="2026-11-05T11:55:00Z", repo_root=root,
    )
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, 0, container_id + "\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([document]), "")
        raise AssertionError(f"survey must not remove: {argv!r}")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(oneshot, "_PROC_ROOT", proc, raising=False)
    monkeypatch.setattr(
        engine, "workspace_ownership_labels", lambda _root: None,
        raising=False,
    )
    from ciu import deploy

    monkeypatch.setattr(
        deploy, "resolve_identities",
        lambda *_args, **_kwargs: {
            "resolved": {"identities": {
                "tools/runner": {"worker": {"compose_project": "demo-runner"}},
                "tools/other": {"api": {"compose_project": "demo-other"}},
            }},
        },
    )
    monkeypatch.setattr(
        oneshot, "reap_project",
        lambda selected_root, project, **kwargs: [{
            "id": container_id, "name": "run", "action": "would-remove",
            "reason": f"project {project}",
        }],
    )

    result = oneshot.reap_cli(root, older_than="30m", yes=False)

    assert result == 0
    assert not any(argv[:3] == ["docker", "rm", "-f"] for argv in calls)
    assert "would remove" in capsys.readouterr().out

