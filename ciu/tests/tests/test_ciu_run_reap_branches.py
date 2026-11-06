"""CIU-141 reap/liveness branch coverage: every refusal stays fail-closed."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ciu import deploy, engine, oneshot

_ID = "a" * 64
_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
_BOOT = "boot-1"


# ---------------------------------------------------------------------------
# Liveness classification
# ---------------------------------------------------------------------------


def _proc(tmp_path, *, with_boot=True, with_self_ns=True):
    proc = tmp_path / "proc"
    (proc / "self" / "ns").mkdir(parents=True)
    if with_self_ns:
        (proc / "self" / "ns" / "pid").write_text("ns")
    if with_boot:
        (proc / "sys" / "kernel" / "random").mkdir(parents=True)
        (proc / "sys" / "kernel" / "random" / "boot_id").write_text(_BOOT)
    return proc


def _add(proc, pid, *, stat_text=None, ns=True, link_to=None):
    process = proc / str(pid)
    (process / "ns").mkdir(parents=True)
    if stat_text is not None:
        (process / "stat").write_text(stat_text)
    if ns:
        if link_to is not None:
            os.link(link_to, process / "ns" / "pid")
        else:
            (process / "ns" / "pid").write_text("other")
    return process


def _stat(state="S", start=10):
    return f"7 (cmd) {state} " + " ".join(["0"] * 18) + f" {start}\n"


def _labels(proc, **updates):
    namespace = (proc / "self" / "ns" / "pid").stat().st_ino
    labels = {
        "ciu.run.owner-pid": "7", "ciu.run.owner-start": "10",
        "ciu.run.pidns": str(namespace), "ciu.run.boot-id": _BOOT,
    }
    labels.update(updates)
    return labels


@pytest.mark.parametrize(
    "updates",
    [
        {"ciu.run.owner-pid": "x"},
        {"ciu.run.owner-pid": "0"},
        {"ciu.run.owner-start": "-1"},
        {"ciu.run.pidns": "0"},
        {"ciu.run.boot-id": ""},
    ],
)
def test_malformed_owner_labels_are_unknown(tmp_path, updates):
    proc = _proc(tmp_path)
    state, _reason = oneshot._reap_owner_state(_labels(proc, **updates), proc_root=proc)
    assert state == "unknown"
    state, _reason = oneshot._reap_owner_state({}, proc_root=proc)
    assert state == "unknown"


def test_owner_liveness_classification(tmp_path):
    proc = _proc(tmp_path)
    labels = _labels(proc)
    namespace_file = proc / "self" / "ns" / "pid"

    # the owner process does not exist
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "dead"
    # matching identity
    _add(proc, 7, stat_text=_stat(), link_to=namespace_file)
    assert oneshot._reap_owner_state(labels, proc_root=proc) == (
        "alive", "owner process identity still matches",
    )
    # recycled pid
    assert oneshot._reap_owner_state(
        _labels(proc, **{"ciu.run.owner-start": "11"}), proc_root=proc,
    )[0] == "dead"
    # a previous boot is dead regardless of anything else
    assert oneshot._reap_owner_state(
        _labels(proc, **{"ciu.run.boot-id": "older-boot"}), proc_root=proc,
    )[0] == "dead"
    # a different pid namespace: unknown
    assert oneshot._reap_owner_state(
        _labels(proc, **{"ciu.run.pidns": "999999"}), proc_root=proc,
    )[0] == "unknown"


def test_owner_liveness_fail_closed_on_unreadable_state(tmp_path):
    proc = _proc(tmp_path)
    namespace_file = proc / "self" / "ns" / "pid"
    labels = _labels(proc)

    zombie = _add(proc, 7, stat_text=_stat("Z"), link_to=namespace_file)
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "dead"
    (zombie / "stat").write_text(_stat("X"))
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "dead"

    (zombie / "stat").unlink()
    (zombie / "stat").mkdir()  # reading a directory raises a non-FileNotFound OSError
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "unknown"
    (zombie / "stat").rmdir()

    (zombie / "stat").write_text("7 (cmd) S 1 2\n")  # too few fields
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "unknown"

    (zombie / "stat").write_text(_stat())
    (zombie / "ns" / "pid").unlink()  # the process namespace cannot be read
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "unknown"
    (zombie / "ns" / "pid").write_text("elsewhere")  # a different namespace inode
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "unknown"


def test_owner_liveness_without_host_identity(tmp_path):
    proc = _proc(tmp_path, with_boot=False)
    assert oneshot._reap_owner_state(_labels(proc), proc_root=proc)[0] == "unknown"
    proc = _proc(tmp_path / "second", with_self_ns=False)
    labels = {
        "ciu.run.owner-pid": "7", "ciu.run.owner-start": "10",
        "ciu.run.pidns": "5", "ciu.run.boot-id": _BOOT,
    }
    assert oneshot._reap_owner_state(labels, proc_root=proc)[0] == "unknown"


def test_started_at_parsing():
    assert oneshot._started_at({}) is None
    assert oneshot._started_at({"ciu.run.started": ""}) is None
    assert oneshot._started_at({"ciu.run.started": "yesterday"}) is None
    assert oneshot._started_at({"ciu.run.started": "2026-11-05T12:00:00"}) is None
    parsed = oneshot._started_at({"ciu.run.started": "2026-11-05T12:00:00Z"})
    assert parsed == datetime(2026, 11, 5, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# reap_project
# ---------------------------------------------------------------------------


def _container(proc, *, project="demo", oneoff="True", run="1", status="exited",
               started=None, working_dir="/root", owner_pid="7", **labels):
    namespace = (proc / "self" / "ns" / "pid").stat().st_ino
    document_labels = {
        "com.docker.compose.project": project,
        "com.docker.compose.oneoff": oneoff,
        "com.docker.compose.project.working_dir": working_dir,
        "ciu.run": run,
        "ciu.run.owner-pid": owner_pid, "ciu.run.owner-start": "10",
        "ciu.run.pidns": str(namespace), "ciu.run.boot-id": _BOOT,
    }
    if started is not None:
        document_labels["ciu.run.started"] = started
    document_labels.update(labels)
    return {
        "Id": _ID, "Name": "/one-shot",
        "Config": {"Labels": document_labels},
        "State": {"Status": status},
    }


class _Docker:
    def __init__(self, monkeypatch, document, *, ids=(_ID,)):
        self.document = document
        self.ids = list(ids)
        self.ps_rc = 0
        self.ps_raises = None
        self.inspect_rc = 0
        self.inspect_stdout = None
        self.inspect_raises = None
        self.rm_rc = 0
        self.rm_raises = None
        self.calls = []
        monkeypatch.setattr(oneshot.procutil, "run_cmd", self.run)

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            if self.ps_raises:
                raise self.ps_raises
            return subprocess.CompletedProcess(argv, self.ps_rc, "".join(i + "\n" for i in self.ids), "")
        if argv[:2] == ["docker", "inspect"]:
            assert argv[:4] == ["docker", "inspect", "--type", "container"], argv
            if self.inspect_raises:
                raise self.inspect_raises
            stdout = self.inspect_stdout
            if stdout is None:
                stdout = json.dumps([self.document])
            return subprocess.CompletedProcess(argv, self.inspect_rc, stdout, "")
        if argv[:3] == ["docker", "rm", "-f"]:
            if self.rm_raises:
                raise self.rm_raises
            return subprocess.CompletedProcess(argv, self.rm_rc, "", "")
        raise AssertionError(argv)

    def removals(self):
        return [argv for argv in self.calls if argv[:3] == ["docker", "rm", "-f"]]


@pytest.fixture
def reap_world(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    _add(proc, 7, stat_text=_stat(), link_to=proc / "self" / "ns" / "pid")
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(oneshot, "_PROC_ROOT", proc)
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)
    return SimpleNamespaceLike(proc=proc, root=root)


class SimpleNamespaceLike:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _reap(world, **kwargs):
    kwargs.setdefault("older_than_seconds", 3600)
    kwargs.setdefault("now", _NOW)
    return oneshot.reap_project(world.root, "demo", **kwargs)


def test_reap_refuses_when_checkout_identity_is_unreadable(reap_world, monkeypatch):
    def broken(_root):
        raise OSError("no")

    monkeypatch.setattr(engine, "workspace_ownership_labels", broken)
    with pytest.raises(RuntimeError, match="cannot read this checkout's ownership identity"):
        _reap(reap_world)


def test_reap_listing_failures_are_fail_closed(reap_world, monkeypatch):
    docker = _Docker(monkeypatch, _container(reap_world.proc))
    docker.ps_raises = FileNotFoundError("docker")
    with pytest.raises(RuntimeError, match="Docker unavailable while listing"):
        _reap(reap_world)
    docker.ps_raises = None
    docker.ps_rc = 1
    with pytest.raises(RuntimeError, match="Docker failed while listing"):
        _reap(reap_world)
    docker.ps_rc = 0
    docker.ids = ["abc123"]
    with pytest.raises(RuntimeError, match="did not return full container IDs"):
        _reap(reap_world)
    docker.ids = []
    assert _reap(reap_world) == []


def test_reap_inspection_failures_are_fail_closed(reap_world, monkeypatch):
    docker = _Docker(monkeypatch, _container(reap_world.proc))
    docker.inspect_raises = FileNotFoundError("docker")
    with pytest.raises(RuntimeError, match="Docker unavailable while inspecting"):
        _reap(reap_world)
    docker.inspect_raises = None
    docker.inspect_rc = 1
    with pytest.raises(RuntimeError, match="changed during inspection"):
        _reap(reap_world)
    docker.inspect_rc = 0
    for stdout, match in (
        ("not json", "invalid one-shot JSON"),
        (json.dumps({"Id": _ID}), "exact one-shot"),
        (json.dumps([{}, {}]), "exact one-shot"),
        (json.dumps([5]), "exact one-shot"),
        (json.dumps([{"Id": "b" * 64}]), "changed ID"),
    ):
        docker.inspect_stdout = stdout
        with pytest.raises(RuntimeError, match=match):
            _reap(reap_world)


@pytest.mark.parametrize(
    "updates",
    [{"project": "other"}, {"oneoff": "False"}, {"run": "0"}],
)
def test_reap_refuses_a_container_that_stopped_matching_its_filter(
    reap_world, monkeypatch, updates,
):
    _Docker(monkeypatch, _container(reap_world.proc, **updates))
    with pytest.raises(RuntimeError, match="no longer matches its exact label filter"):
        _reap(reap_world)


def test_reap_primary_checkout_requires_the_exact_working_dir(reap_world, monkeypatch):
    docker = _Docker(monkeypatch, _container(
        reap_world.proc, working_dir="/elsewhere", owner_pid="999",
    ))
    rows = _reap(reap_world, yes=True)
    assert rows[0]["action"] == "left" and "primary checkout" in rows[0]["reason"]
    assert docker.removals() == []


def test_reap_managed_instance_requires_the_matching_ownership(reap_world, monkeypatch):
    monkeypatch.setattr(
        engine, "workspace_ownership_labels",
        lambda root: {"ciu.instance": "mine", "ciu.repo-root": str(root)},
    )
    docker = _Docker(monkeypatch, _container(reap_world.proc, owner_pid="999", **{
        "ciu.instance": "theirs",
    }))
    rows = _reap(reap_world, yes=True)
    assert rows[0]["action"] == "left" and "another or unknown checkout" in rows[0]["reason"]
    assert docker.removals() == []
    query = next(argv for argv in docker.calls if argv[:2] == ["docker", "ps"])
    assert "label=ciu.instance=mine" in query


def test_reap_actions_by_owner_age_and_status(reap_world, monkeypatch):
    root = str(reap_world.root)
    recent = (_NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    old = (_NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z")

    def rows(document, **kwargs):
        docker = _Docker(monkeypatch, document)
        return _reap(reap_world, **kwargs), docker

    base = {"working_dir": root, "owner_pid": "999"}  # pid 999 does not exist: dead

    # live owner: left alone
    live, docker = rows(_container(reap_world.proc, working_dir=root, started=old), yes=True)
    assert live[0]["action"] == "left" and docker.removals() == []

    # dead owner, exited: survey says would-remove, --yes removes by full id
    survey, docker = rows(_container(reap_world.proc, **base))
    assert survey[0]["action"] == "would-remove" and docker.removals() == []
    removed, docker = rows(_container(reap_world.proc, **base), yes=True)
    assert removed[0]["action"] == "removed"
    assert docker.removals() == [["docker", "rm", "-f", _ID]]

    # dead owner, running, recent or unknown start: left alone
    for started in (recent, None):
        kept, docker = rows(
            _container(reap_world.proc, status="running", started=started, **base), yes=True,
        )
        assert kept[0]["action"] == "left" and "not older than the bound" in kept[0]["reason"]
        assert docker.removals() == []
    # dead owner, running, older than the bound: removed
    stale, docker = rows(
        _container(reap_world.proc, status="running", started=old, **base), yes=True,
    )
    assert stale[0]["action"] == "removed"

    # a failed removal is reported, not hidden
    document = _container(reap_world.proc, **base)
    docker = _Docker(monkeypatch, document)
    docker.rm_rc = 1
    failed = _reap(reap_world, yes=True)
    assert failed[0]["action"] == "failed" and failed[0]["reason"] == "docker rm -f failed"
    docker.rm_raises = FileNotFoundError("docker")
    with pytest.raises(RuntimeError, match="Docker unavailable while removing"):
        _reap(reap_world, yes=True)


# ---------------------------------------------------------------------------
# reap_cli
# ---------------------------------------------------------------------------


def _identities(monkeypatch, identities):
    monkeypatch.setattr(
        deploy, "resolve_identities",
        lambda *_a, **_k: {"resolved": {"identities": identities}},
    )


def test_reap_cli_age_validation_and_discovery(tmp_path, monkeypatch, capsys):
    with pytest.raises(ValueError, match="not a duration"):
        oneshot.reap_cli(tmp_path, older_than="soon")
    with pytest.raises(ValueError, match="greater than zero"):
        oneshot.reap_cli(tmp_path, older_than="0s")
    _identities(monkeypatch, "not a mapping")
    with pytest.raises(RuntimeError, match="invalid shape"):
        oneshot.reap_cli(tmp_path)
    _identities(monkeypatch, {"tools/a": {"w": {"x": 1}}, "tools/b": "bad", "tools/c": {"w": "bad"}})
    assert oneshot.reap_cli(tmp_path) == 0
    assert "no selected Compose projects" in capsys.readouterr().out


def test_reap_cli_reports_every_action_and_fails_on_a_failed_removal(
    tmp_path, monkeypatch, capsys,
):
    _identities(monkeypatch, {
        "tools/a": {"w": {"compose_project": "p2"}},
        "tools/b": {"w": {"compose_project": "p1"}, "v": {"compose_project": "p1"}},
    })
    seen = []
    actions = {"p1": "failed", "p2": "removed"}

    def reap_project(_root, project, **kwargs):
        seen.append((project, kwargs["older_than_seconds"], kwargs["yes"]))
        action = actions[project]
        return [{"id": "i" * 64, "name": "n", "action": action, "reason": f"because {action}"}]

    monkeypatch.setattr(oneshot, "reap_project", reap_project)

    result = oneshot.reap_cli(tmp_path, older_than="2h", yes=True)

    out = capsys.readouterr().out
    assert result == 1
    assert seen == [("p1", 7200.0, True), ("p2", 7200.0, True)]
    assert "failed to remove" in out and "] removed " in out

    seen.clear()
    actions.update({"p1": "would-remove", "p2": "left"})
    assert oneshot.reap_cli(tmp_path) == 0
    out = capsys.readouterr().out
    assert "would remove" in out and "left alone" in out
    assert seen[0][1] == oneshot._DEFAULT_REAP_AGE_SECONDS and seen[0][2] is False


def test_parse_run_label_requires_a_separator():
    for bad in ("novalue", 5):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            oneshot.parse_run_label(bad)
    with pytest.raises(ValueError, match="invalid label key"):
        oneshot.parse_run_label("bad key=v")
    assert oneshot.validate_env_name("OK_1") == "OK_1"
    assert Path  # keep import used
