"""CIU-141 foreground and detached lifecycle contracts through a stateful docker fake.

No real docker: every ``docker`` call goes through ``oneshot.procutil`` and is
answered by ``_FakeDocker``, which models containers (ours and foreign ones),
``--type container`` inspect by name or full ID, label queries and removal.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ciu import engine, oneshot

_CONTAINER_ID = "a" * 64
_FOREIGN_ID = "f" * 64
_OTHER_ID = "b" * 64
_NETWORK_NAME = "demo_instance-net"
_ZERO_TIME = "0001-01-01T00:00:00Z"


def _service():
    return {
        "image": "python:3.11-slim",
        "user": "1001:1001",
        "working_dir": "/workspace",
        "cgroup_parent": "dev-gates.slice",
        "tty": True,
        "stdin_open": True,
        "networks": {"instance-net": {}},
        "volumes": [],
        "environment": {"TOKEN": "planned-secret"},
        "labels": {"service.label": "kept"},
    }


def _prepared(tmp_path):
    return SimpleNamespace(
        working_dir=tmp_path,
        repo_root=tmp_path,
        project="demo-runner",
        file_args=["-f", "ciu.compose.yml"],
        compose_env={"SCHEMA_GATE_DSN": "password-bearing-dsn"},
        rendered_compose="services: {}\n",
        ownership_path=None,
        config={},
        secret_values=frozenset({"planned-secret"}),
    )


def _compose_config():
    return {
        "services": {"worker": _service()},
        "networks": {"instance-net": {"name": _NETWORK_NAME, "external": True}},
    }


def _labels_from(argv):
    labels = {}
    for index, token in enumerate(argv[:-1]):
        if token in {"-l", "--label"}:
            key, _, value = argv[index + 1].partition("=")
            labels[key] = value
    return labels


def _name_from(argv):
    return argv[argv.index("--name") + 1]


def _inspect_document(name, labels, *, status="running", exit_code=0, cid=_CONTAINER_ID):
    started = _ZERO_TIME if status == "created" else "2026-11-05T00:00:00Z"
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Image": "sha256:imageid",
        "Config": {
            "Image": "python:3.11-slim",
            "User": "1001:1001",
            "Labels": labels,
        },
        "HostConfig": {"CgroupParent": "dev-gates.slice", "CgroupnsMode": "private"},
        "NetworkSettings": {"Networks": {_NETWORK_NAME: {}}},
        "State": {
            "Status": status,
            "ExitCode": exit_code,
            "OOMKilled": False,
            "StartedAt": started,
            "FinishedAt": "2026-11-05T00:00:00Z",
        },
    }


class _FakeProcess:
    def __init__(self, returncode, *, event_file=None):
        self.returncode = returncode
        self.event_file = Path(event_file) if event_file else None
        self.argv = None
        self.pid = 424242
        self.terminated = False
        self.killed = False
        self.wait_timeouts = 0

    def poll(self):
        if self.terminated or self.killed:
            return -15
        if (
            self.event_file is not None
            and (
                not self.event_file.exists()
                or '"event":"launched"' not in self.event_file.read_text()
            )
        ):
            return None
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.wait_timeouts > 0:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired("compose", timeout)
        return self.returncode


class _FakeDocker:
    """A small stateful docker model; one 'ours' container plus foreign ones."""

    def __init__(self, monkeypatch, *, exit_code=0, event_file=None, statuses=("exited",)):
        self.calls = []
        self.foreign = {}  # id -> inspect document
        self.statuses = list(statuses)
        self.exit_code = exit_code
        self.process = _FakeProcess(exit_code, event_file=event_file)
        self.ours_created = False
        self.ours_name = None
        self.ours_labels = {}
        self.our_id = _CONTAINER_ID
        self.create_in_run = True
        self.detached_rc = 0
        self.detached_stdout = None
        self.detached_raises = None
        self.hide_name_inspects = 0
        self.kill_rc = 0
        self.kill_raises = None
        self.rm_rc = 0
        self.ps_rc = 0
        self.ps_stdout = None
        self.ps_raises = None
        self.inspect_stdout = None
        self.inspect_rc_override = None
        self.on_compose_run = None
        self.on_name_inspect = None
        self.removed = []
        monkeypatch.setattr(oneshot.procutil, "run_cmd", self._run_cmd)
        monkeypatch.setattr(oneshot.procutil, "popen", self._popen, raising=False)

    # -- model -----------------------------------------------------------
    def add_foreign(self, name, *, cid=_FOREIGN_ID, labels=None, status="running"):
        self.foreign[cid] = _inspect_document(name, labels or {}, status=status, cid=cid)

    def _create_ours(self, argv):
        if self.on_compose_run is not None:
            self.on_compose_run(argv)
        if self.create_in_run:
            self.ours_created = True
            self.ours_name = _name_from(argv)
            self.ours_labels = _labels_from(argv)

    def _ours_doc(self):
        status = self.statuses[0]
        if len(self.statuses) > 1:
            self.statuses.pop(0)
        return _inspect_document(
            self.ours_name, self.ours_labels, status=status,
            exit_code=self.exit_code, cid=self.our_id,
        )

    def _completed(self, argv, rc=0, stdout=""):
        return subprocess.CompletedProcess(argv, rc, stdout, "")

    # -- procutil seams ----------------------------------------------------
    def _popen(self, argv, **kwargs):
        self.process.argv = list(argv)
        self.calls.append((list(argv), kwargs))
        self._create_ours(list(argv))
        return self.process

    def _run_cmd(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        if argv[:3] == ["docker", "compose", "--progress"] and "config" in argv:
            return self._completed(argv, 0, json.dumps(_compose_config()))
        if argv[:3] == ["docker", "compose", "--progress"] and "run" in argv:
            if self.detached_raises is not None:
                raise self.detached_raises
            if "-d" not in argv:
                raise AssertionError("foreground compose run must use procutil.popen")
            self._create_ours(argv)
            stdout = self.detached_stdout
            if stdout is None:
                stdout = self.our_id + "\n"
            return self._completed(argv, self.detached_rc, stdout)
        if argv[:3] == ["docker", "network", "inspect"]:
            return self._completed(argv, 0, json.dumps([{"Name": argv[-1], "Labels": {}}]))
        if argv[:2] == ["docker", "inspect"]:
            return self._inspect(argv)
        if argv[:2] == ["docker", "ps"]:
            if self.ps_raises is not None:
                raise self.ps_raises
            if self.ps_stdout is not None:
                return self._completed(argv, self.ps_rc, self.ps_stdout)
            wanted = next(
                token.split("=", 2)[2] for token in argv if token.startswith("label=ciu.run.id=")
            )
            ids = [self.our_id] if (
                self.ours_created and self.ours_labels.get("ciu.run.id") == wanted
            ) else []
            return self._completed(argv, self.ps_rc, "".join(item + "\n" for item in ids))
        if argv[:3] == ["docker", "rm", "-f"]:
            ref = argv[-1]
            self.removed.append(ref)
            if ref in {self.our_id, self.ours_name}:
                self.ours_created = False
            self.foreign.pop(ref, None)
            return self._completed(argv, self.rm_rc, ref + "\n")
        if argv[:2] == ["docker", "kill"]:
            if self.kill_raises is not None:
                raise self.kill_raises
            return self._completed(argv, self.kill_rc)
        raise AssertionError(f"unexpected docker command: {argv!r}")

    def _inspect(self, argv):
        # Every inspect must be limited to containers (F2).
        assert argv[:4] == ["docker", "inspect", "--type", "container"], argv
        ref = argv[-1]
        if self.inspect_stdout is not None:
            return self._completed(argv, 0, self.inspect_stdout)
        if self.inspect_rc_override is not None:
            return self._completed(argv, self.inspect_rc_override, "")
        by_id = len(ref) == 64 and set(ref) <= set("0123456789abcdef")
        document = None
        if by_id:
            if ref == self.our_id and self.ours_created:
                document = self._ours_doc()
            else:
                document = self.foreign.get(ref)
        else:
            if self.on_name_inspect is not None:
                self.on_name_inspect()
            if self.hide_name_inspects > 0:
                self.hide_name_inspects -= 1
            elif self.ours_created and ref == self.ours_name:
                document = self._ours_doc()
            else:
                document = next(
                    (item for item in self.foreign.values() if item["Name"] == f"/{ref}"),
                    None,
                )
        if document is None:
            return self._completed(argv, 1)
        return self._completed(argv, 0, json.dumps([document]))

    # -- queries -------------------------------------------------------------
    def commands(self, *prefix):
        return [argv for argv, _ in self.calls if argv[: len(prefix)] == list(prefix)]

    def kills(self):
        return self.commands("docker", "kill")

    def removals(self):
        return self.commands("docker", "rm")

    def compose_run(self):
        return next(
            (argv, kwargs) for argv, kwargs in self.calls
            if "run" in argv and "compose" in argv
        )


def _install_fake_docker(monkeypatch, **kwargs):
    fake = _FakeDocker(monkeypatch, **kwargs)
    return fake.calls, fake.process, fake


def _mock_preparation(monkeypatch, prepared):
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda *_args: None)
    monkeypatch.setattr(
        oneshot, "_prepare_run_stack",
        lambda *args, **kwargs: (prepared, "tools/runner", "worker"),
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    return prepared


def _detached(tmp_path, name, **kwargs):
    return oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["true"], detach=True, json_output=True,
        name=name, owner_pid=os.getpid(), **kwargs,
    )


def _foreground(tmp_path, name, events_path=None, **kwargs):
    return oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["true"], name=name, owner_pid=os.getpid(),
        events_file=events_path, **kwargs,
    )


def _events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _deliver_on_install(monkeypatch, signum):
    """Deliver ``signum`` to ciu's handler the moment it is installed."""
    real_signal = signal.signal
    state = {"handlers": {}, "done": False}

    def install(sig, handler):
        previous = real_signal(sig, handler)
        if callable(handler):
            state["handlers"][sig] = handler
        if sig == signum and callable(handler) and not state["done"]:
            state["done"] = True
            handler(signum, None)
        return previous

    monkeypatch.setattr(oneshot.signal, "signal", install)
    return state


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_detached_run_crosschecks_exact_id_and_prints_one_launch_document(
    tmp_path, monkeypatch, capsys, world,
):
    calls, _process, fake = _install_fake_docker(monkeypatch)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "print('ok')"],
        detach=True, json_output=True, name="ciurun-test-unit-1",
        labels=["run-gate.job=lane 1"], env_names=["SCHEMA_GATE_DSN"],
        owner_pid=os.getpid(),
    )

    captured = capsys.readouterr()
    event = json.loads(captured.out)
    assert result == 0
    assert captured.err == ""
    assert event["schema"] == "ciu-run-event/1"
    assert event["event"] == "launched"
    assert event["container"] == {"id": _CONTAINER_ID, "name": "ciurun-test-unit-1"}
    assert event["run_id"]
    assert event["labels"]["ciu.run"] == "1"
    assert event["labels"]["run-gate.job"] == "lane 1"
    assert "password-bearing-dsn" not in captured.out

    compose, kwargs = fake.compose_run()
    assert compose[:4] == ["docker", "compose", "--progress", "quiet"]
    assert "--no-deps" in compose and "-d" in compose
    assert "--name" in compose and "ciurun-test-unit-1" in compose
    assert "-T" in compose and "--interactive=false" in compose
    assert "--use-aliases" not in compose and "--rm" not in compose
    assert "--remove-orphans" not in compose and "--pull" not in compose
    assert "-e" in compose and "SCHEMA_GATE_DSN" in compose
    assert "password-bearing-dsn" not in compose
    assert "CIU_RUN_CONTAINER_NAME=ciurun-test-unit-1" in compose
    assert any(token.startswith("CIU_RUN_ID=") for token in compose)
    assert kwargs["env"]["SCHEMA_GATE_DSN"] == "password-bearing-dsn"
    # The pre-flight looks the name up as a CONTAINER; the launch check uses the full id.
    assert [
        "docker", "inspect", "--type", "container", "ciurun-test-unit-1",
    ] in [argv for argv, _ in calls]
    assert ["docker", "inspect", "--type", "container", _CONTAINER_ID] in [
        argv for argv, _ in calls
    ]
    assert fake.removals() == []


@pytest.mark.parametrize("exit_code", [0, 1, 42, 130, 137])
def test_foreground_status_passthrough_and_event_order(
    tmp_path, monkeypatch, capsys, exit_code, world,
):
    events_path = tmp_path / "events.jsonl"
    calls, _process, fake = _install_fake_docker(
        monkeypatch, exit_code=exit_code, event_file=events_path,
    )

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "exit()"],
        events_file=events_path, name=f"ciurun-test-unit-{exit_code}",
        owner_pid=os.getpid(),
    )

    captured = capsys.readouterr()
    events = _events(events_path)
    assert result == exit_code
    assert captured.out == ""
    assert '"event"' not in captured.err
    assert [event["event"] for event in events] == ["launched", "exited", "removed"]
    assert events[1]["exit_code"] == exit_code
    assert events[2] == {
        "schema": "ciu-run-event/1", "event": "removed",
        "run_id": events[0]["run_id"], "removed": True, "error": None,
    }
    assert events_path.stat().st_mode & 0o777 == 0o600
    # Removal is keyed on the full container id, never on the name.
    assert fake.removed == [_CONTAINER_ID]
    compose, kwargs = fake.compose_run()
    assert "-T" in compose and "--interactive=false" in compose
    assert kwargs.get("stdout") is None and kwargs.get("stderr") is None
    assert calls


def test_foreground_terminal_keeps_compose_tty_and_stdin_enabled(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch, exit_code=0)
    monkeypatch.setattr(oneshot.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(oneshot.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    result = _foreground(tmp_path, "ciurun-test-unit-tty")

    assert result == 0
    compose, _kwargs = fake.compose_run()
    assert "-T" not in compose and "--interactive=false" not in compose


# ---------------------------------------------------------------------------
# F1: a name collision never removes a container this run did not create
# ---------------------------------------------------------------------------


def test_detached_name_collision_with_persistent_container_touches_nothing(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.add_foreign("test-runner", labels={"com.docker.compose.service": "test-runner"})

    with pytest.raises(RuntimeError, match="already in use by container ffffffffffff"):
        _detached(tmp_path, "test-runner")

    assert all("run" not in argv for argv in fake.commands("docker", "compose"))
    assert fake.removals() == [] and fake.kills() == []
    assert _FOREIGN_ID in fake.foreign


def test_foreground_name_collision_with_persistent_container_touches_nothing(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.add_foreign("test-runner", labels={})
    monkeypatch.setattr(
        oneshot.procutil, "popen",
        lambda *args, **kwargs: pytest.fail("compose must not be started"), raising=False,
    )

    with pytest.raises(RuntimeError, match="already in use"):
        _foreground(tmp_path, "test-runner")

    assert fake.removals() == [] and fake.kills() == []
    assert _FOREIGN_ID in fake.foreign


def test_dry_run_reports_the_same_name_collision(tmp_path, monkeypatch, world):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.add_foreign("test-runner")

    with pytest.raises(RuntimeError, match="already in use"):
        oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], dry_run=True, json_output=True,
            name="test-runner",
        )

    assert fake.removals() == [] and fake.kills() == []


def test_detached_compose_failure_after_a_race_leaves_the_foreign_holder(
    tmp_path, monkeypatch, world,
):
    """The name is taken between the pre-flight and compose: compose fails with a conflict."""
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.create_in_run = False
    fake.detached_rc = 1
    fake.on_compose_run = lambda argv: fake.add_foreign(_name_from(argv), labels={})

    with pytest.raises(RuntimeError, match="docker compose run failed"):
        _detached(tmp_path, "racer")

    assert fake.removed == []
    assert _FOREIGN_ID in fake.foreign


def test_detached_create_failure_removes_only_our_created_container_by_id(
    tmp_path, monkeypatch, world,
):
    """Compose created our (labelled) container but could not start it."""
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.detached_rc = 1
    fake.add_foreign("unrelated")

    with pytest.raises(RuntimeError, match="docker compose run failed"):
        _detached(tmp_path, "ciurun-test-unit-createfail")

    assert fake.removed == [_CONTAINER_ID]
    assert ["docker", "rm", "-f", "ciurun-test-unit-createfail"] not in fake.removals()
    assert _FOREIGN_ID in fake.foreign


def test_detached_id_mismatch_refuses_and_never_removes_the_foreign_container(
    tmp_path, monkeypatch, world,
):
    """Compose printed an id that is not ours (no ciu.run.id label): leave it alone."""
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.create_in_run = False
    fake.detached_stdout = _OTHER_ID + "\n"
    fake.on_compose_run = lambda argv: fake.add_foreign(
        _name_from(argv), cid=_OTHER_ID, labels={},
    )

    with pytest.raises(RuntimeError, match="different ciu.run.id"):
        _detached(tmp_path, "ciurun-test-unit-mismatch")

    assert fake.removed == []
    assert _OTHER_ID in fake.foreign


def test_detached_name_mismatch_removes_ours_by_id_but_not_the_printed_foreign_id(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.detached_stdout = _OTHER_ID + "\n"
    fake.add_foreign("somebody-else", cid=_OTHER_ID, labels={})

    with pytest.raises(RuntimeError, match="disagrees with the requested container name"):
        _detached(tmp_path, "ciurun-test-unit-namemismatch")

    assert fake.removed == [_CONTAINER_ID]
    assert _OTHER_ID in fake.foreign


def test_detached_stdout_that_is_not_a_full_id_cleans_up_only_ours_by_id(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.detached_stdout = "abc123\n"

    with pytest.raises(RuntimeError, match="did not return one full 64-hex"):
        _detached(tmp_path, "ciurun-test-unit-badid")

    assert fake.removed == [_CONTAINER_ID]


def test_detached_uninspectable_id_cleans_up_by_label(tmp_path, monkeypatch, world):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.detached_stdout = _OTHER_ID + "\n"  # nothing answers to this id

    with pytest.raises(RuntimeError, match="not inspectable by its full ID"):
        _detached(tmp_path, "ciurun-test-unit-gone")

    assert fake.removed == [_CONTAINER_ID]


def test_detached_created_but_not_started_is_refused_and_removed(
    tmp_path, monkeypatch, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch, statuses=("created",))

    with pytest.raises(RuntimeError, match="was not started"):
        _detached(tmp_path, "ciurun-test-unit-created")

    assert fake.removed == [_CONTAINER_ID]


def test_detached_compose_oserror_cleans_up_by_label_only(tmp_path, monkeypatch, world):
    _calls, _process, fake = _install_fake_docker(monkeypatch)
    fake.detached_raises = FileNotFoundError("docker")

    with pytest.raises(RuntimeError, match="could not start the one-shot"):
        _detached(tmp_path, "ciurun-test-unit-oserror")

    assert fake.removed == []


# -- foreground: collisions, races and cleanup --------------------------------


def test_foreground_foreign_holder_appearing_after_preflight_is_never_touched(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(monkeypatch, event_file=events_path)
    fake.create_in_run = False
    fake.on_compose_run = lambda argv: fake.add_foreign(_name_from(argv), labels={})

    with pytest.raises(RuntimeError, match="different ciu.run.id"):
        _foreground(tmp_path, "ciurun-test-unit-fgrace", events_path)

    assert fake.removed == [] and fake.kills() == []
    assert _FOREIGN_ID in fake.foreign
    assert process.terminated  # the aborted compose client is stopped


def test_foreground_created_but_never_started_emits_no_launched_event(
    tmp_path, monkeypatch, world,
):
    """Compose exits immediately; the container only reached 'created' (start failed)."""
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, exit_code=125, statuses=("created",),
    )

    with pytest.raises(RuntimeError, match="docker compose run failed"):
        _foreground(tmp_path, "ciurun-test-unit-fgcreated", events_path)

    assert not events_path.exists() or '"launched"' not in events_path.read_text()
    assert fake.removed == [_CONTAINER_ID]


def test_foreground_launched_waits_for_the_container_to_actually_start(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, event_file=events_path, statuses=("created", "created", "running", "exited"),
    )
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)

    result = _foreground(tmp_path, "ciurun-test-unit-fgwait", events_path)

    assert result == 0
    events = _events(events_path)
    assert [event["event"] for event in events] == ["launched", "exited", "removed"]
    assert fake.removed == [_CONTAINER_ID]


def test_foreground_start_timeout_removes_ours_and_stops_the_compose_client(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(
        monkeypatch, event_file=events_path, statuses=("created",),
    )
    ticks = iter(range(0, 10_000, 20))
    monkeypatch.setattr(oneshot.time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)
    process.wait_timeouts = 1  # terminate is ignored once, then killed

    with pytest.raises(RuntimeError, match="did not start within 30 seconds"):
        _foreground(tmp_path, "ciurun-test-unit-timeout", events_path)

    assert fake.removed == [_CONTAINER_ID]
    assert process.terminated and process.killed


def test_foreground_interrupt_before_the_container_exists_is_reported(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, event_file=events_path, statuses=("created",),
    )
    ticks = iter(range(0, 10_000, 31))  # every clock read is already past the deadline
    monkeypatch.setattr(oneshot.time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)
    _deliver_on_install(monkeypatch, signal.SIGINT)

    with pytest.raises(RuntimeError, match="interrupted before the one-shot container"):
        _foreground(tmp_path, "ciurun-test-unit-earlyint", events_path)

    assert fake.kills() == []
    assert fake.removed == [_CONTAINER_ID]


def test_foreground_exit_before_first_inspect_still_emits_launched(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(monkeypatch, exit_code=3)
    fake.hide_name_inspects = 2  # the pre-flight and the first poll see nothing

    result = _foreground(tmp_path, "ciurun-test-unit-quick", events_path)

    assert result == 3
    assert [event["event"] for event in _events(events_path)] == [
        "launched", "exited", "removed",
    ]


def test_foreground_final_inspection_failures_are_reported_and_cleaned_up(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    # 1: container disappeared before the exit status was read
    _calls, _process, fake = _install_fake_docker(monkeypatch, event_file=events_path)
    seen = {"by_id": 0}
    original = fake._inspect

    def vanish_on_final(argv):
        if argv[-1] == _CONTAINER_ID:
            seen["by_id"] += 1
            if seen["by_id"] == 1:
                fake.ours_created = False
        return original(argv)

    fake._inspect = vanish_on_final
    with pytest.raises(RuntimeError, match="disappeared before exit status"):
        _foreground(tmp_path, "ciurun-test-unit-vanish", events_path)

    # 2: still running when Compose returned
    events_path.unlink()
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, event_file=events_path, statuses=("running",),
    )
    with pytest.raises(RuntimeError, match="did not have an exited state"):
        _foreground(tmp_path, "ciurun-test-unit-running", events_path)
    assert fake.removed == [_CONTAINER_ID]

    # 3: no integer exit code in the inspected state
    events_path.unlink()
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, event_file=events_path, exit_code=True,
    )
    with pytest.raises(RuntimeError, match="no inspected exit code"):
        _foreground(tmp_path, "ciurun-test-unit-nocode", events_path)

    # 4: Compose and the container disagree about the exit status
    events_path.unlink()
    _calls, process, fake = _install_fake_docker(monkeypatch, event_file=events_path)
    fake.exit_code = 9
    process.returncode = 0
    with pytest.raises(RuntimeError, match="disagrees with inspected container"):
        _foreground(tmp_path, "ciurun-test-unit-disagree", events_path)
    assert fake.removed == [_CONTAINER_ID]


@pytest.mark.parametrize(("exit_code", "expected"), [(0, 1), (5, 5)])
def test_foreground_removal_failure_is_reported_and_never_hides_a_failure(
    tmp_path, monkeypatch, world, exit_code, expected,
):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, exit_code=exit_code, event_file=events_path,
    )
    fake.rm_rc = 1

    result = _foreground(tmp_path, "ciurun-test-unit-rmfail", events_path)

    assert result == expected
    removed = _events(events_path)[-1]
    assert removed["removed"] is False and removed["error"] == "docker rm -f failed"


def test_foreground_compose_that_cannot_start_raises(tmp_path, monkeypatch, world):
    _calls, _process, _fake = _install_fake_docker(monkeypatch)

    def no_popen(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(oneshot.procutil, "popen", no_popen, raising=False)
    with pytest.raises(RuntimeError, match="could not start the one-shot"):
        _foreground(tmp_path, "ciurun-test-unit-nopopen")


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signum", "name"),
    [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT"), (signal.SIGHUP, "SIGHUP")],
)
def test_foreground_signal_is_forwarded_once_to_the_exact_container_id(
    tmp_path, monkeypatch, world, signum, name,
):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, exit_code=143, event_file=events_path,
    )
    _deliver_on_install(monkeypatch, signum)

    result = _foreground(tmp_path, "ciurun-test-unit-sig", events_path)

    assert result == 143
    assert fake.kills() == [["docker", "kill", "-s", name, _CONTAINER_ID]]


def test_foreground_only_the_first_signal_is_remembered(tmp_path, monkeypatch, world):
    events_path = tmp_path / "events.jsonl"
    _calls, _process, fake = _install_fake_docker(
        monkeypatch, exit_code=143, event_file=events_path,
    )
    state = _deliver_on_install(monkeypatch, signal.SIGTERM)
    original_ps = fake.on_name_inspect

    def second_signal():
        if signal.SIGINT in state["handlers"]:
            state["handlers"][signal.SIGINT](signal.SIGINT, None)
        if original_ps:  # pragma: no cover - not set
            original_ps()

    fake.on_name_inspect = second_signal
    _foreground(tmp_path, "ciurun-test-unit-twosig", events_path)

    assert fake.kills() == [["docker", "kill", "-s", "SIGTERM", _CONTAINER_ID]]


def test_foreground_escalates_to_the_container_id_after_signal_grace(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(
        monkeypatch, exit_code=137, event_file=events_path,
    )
    process.returncode = None
    _deliver_on_install(monkeypatch, signal.SIGINT)
    clock_tick = 0

    def monotonic():
        nonlocal clock_tick
        clock_tick += 1
        return float(clock_tick)

    original_run_cmd = oneshot.procutil.run_cmd

    def run_cmd(argv, **kwargs):
        result = original_run_cmd(argv, **kwargs)
        if list(argv) == ["docker", "kill", _CONTAINER_ID]:
            process.returncode = 137
        return result

    monkeypatch.setattr(oneshot.time, "monotonic", monotonic)
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    result = _foreground(tmp_path, "ciurun-test-unit-escalate", events_path)

    assert result == 137
    assert fake.kills() == [
        ["docker", "kill", "-s", "SIGINT", _CONTAINER_ID],
        ["docker", "kill", _CONTAINER_ID],
    ]


@pytest.mark.parametrize(
    ("knob", "value", "match"),
    [
        ("kill_rc", 1, "docker kill -s SIGTERM failed"),
        ("kill_raises", FileNotFoundError("docker"), "could not forward SIGTERM"),
    ],
)
def test_foreground_signal_forwarding_failure_cleans_up_by_id(
    tmp_path, monkeypatch, world, knob, value, match,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(
        monkeypatch, exit_code=143, event_file=events_path,
    )
    setattr(fake, knob, value)
    process.returncode = None  # the compose client is still running
    _deliver_on_install(monkeypatch, signal.SIGTERM)

    with pytest.raises(RuntimeError, match=match):
        _foreground(tmp_path, "ciurun-test-unit-killfail", events_path)

    assert fake.removed == [_CONTAINER_ID]
    assert process.terminated


@pytest.mark.parametrize(
    ("knob", "value", "match"),
    [
        ("kill_rc", 1, "docker kill failed"),
        ("kill_raises", FileNotFoundError("docker"), "could not escalate"),
    ],
)
def test_foreground_escalation_failure_cleans_up_by_id(
    tmp_path, monkeypatch, world, knob, value, match,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(
        monkeypatch, exit_code=137, event_file=events_path,
    )
    process.returncode = None
    _deliver_on_install(monkeypatch, signal.SIGINT)
    clock = iter(range(1, 10_000))
    monkeypatch.setattr(oneshot.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)
    original = fake._run_cmd

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        if argv[:2] == ["docker", "kill"] and "-s" not in argv:
            setattr(fake, knob, value)
        elif argv[:2] == ["docker", "kill"]:
            fake.kill_rc = 0
            fake.kill_raises = None
        return original(argv, **kwargs)

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    with pytest.raises(RuntimeError, match=match):
        _foreground(tmp_path, "ciurun-test-unit-escfail", events_path)

    assert fake.removed == [_CONTAINER_ID]


def test_foreground_terminal_resize_is_relayed_to_the_compose_client(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _calls, process, fake = _install_fake_docker(monkeypatch, event_file=events_path)
    monkeypatch.setattr(oneshot.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(oneshot.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    state = _deliver_on_install(monkeypatch, signal.SIGWINCH)
    relayed = []
    real_kill = os.kill

    def kill(pid, signum):
        if pid == process.pid:
            relayed.append((pid, signum))
            if len(relayed) == 2:
                raise ProcessLookupError()
            return None
        return real_kill(pid, signum)

    monkeypatch.setattr(oneshot.os, "kill", kill)

    def resize_twice_then_finish():
        handler = state["handlers"].get(signal.SIGWINCH)
        if handler is not None:
            handler(signal.SIGWINCH, None)  # relayed
            handler(signal.SIGWINCH, None)  # relay fails: ignored
            process.terminated = True
            handler(signal.SIGWINCH, None)  # compose already gone: not relayed
            process.terminated = False
            fake.on_name_inspect = None

    fake.on_name_inspect = resize_twice_then_finish
    _foreground(tmp_path, "ciurun-test-unit-winch", events_path)

    assert relayed == [
        (process.pid, signal.SIGWINCH), (process.pid, signal.SIGWINCH),
    ]


def test_foreground_without_a_terminal_does_not_install_a_resize_handler(
    tmp_path, monkeypatch, world,
):
    events_path = tmp_path / "events.jsonl"
    _install_fake_docker(monkeypatch, event_file=events_path)
    state = _deliver_on_install(monkeypatch, signal.SIGHUP)

    _foreground(tmp_path, "ciurun-test-unit-nowinch", events_path)

    assert signal.SIGWINCH not in state["handlers"]


# ---------------------------------------------------------------------------
# Event sink: a broken sink never kills the run
# ---------------------------------------------------------------------------


def test_broken_event_pipe_is_reported_once_and_the_run_continues(
    tmp_path, monkeypatch, capsys, world,
):
    _calls, _process, fake = _install_fake_docker(monkeypatch, exit_code=0)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # the observer went away: writes raise EPIPE
    try:
        result = oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], events_fd=write_fd,
            name="ciurun-test-unit-epipe", owner_pid=os.getpid(),
        )
    finally:
        os.close(write_fd)

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err.count("event sink write failed") == 1
    assert fake.removed == [_CONTAINER_ID]  # normal removal still happened


def test_event_sink_write_failure_marks_the_sink_dead(tmp_path, capsys):
    read_fd, write_fd = os.pipe()
    writer = oneshot._EventWriter(fd=write_fd)
    os.close(read_fd)
    try:
        writer.write({"event": "one"})
        writer.write({"event": "two"})
    finally:
        os.close(write_fd)
    assert writer.failed is True
    assert capsys.readouterr().err.count("event sink write failed") == 1


def test_event_sink_short_write_is_a_failure_not_an_endless_loop(monkeypatch, capsys):
    writer = oneshot._EventWriter(fd=None)
    writer.fd = 99
    real_write = os.write
    monkeypatch.setattr(
        oneshot.os,
        "write",
        lambda fd, view: 0 if fd == 99 else real_write(fd, view),
    )

    writer.write({"event": "x"})

    assert writer.failed is True
    assert "short write" in capsys.readouterr().err


def test_detached_event_sink_failure_does_not_lose_the_launch_document(
    tmp_path, monkeypatch, capsys, world,
):
    _install_fake_docker(monkeypatch)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = _detached(tmp_path, "ciurun-test-unit-detepipe", events_fd=write_fd)
    finally:
        os.close(write_fd)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out)["event"] == "launched"
    assert "event sink write failed" in captured.err


def test_event_writer_validation_and_file_errors(tmp_path):
    assert oneshot._EventWriter().fd is None
    oneshot._EventWriter().close()
    oneshot._EventWriter().write({"event": "dropped"})
    with pytest.raises(ValueError, match="choose one event sink"):
        oneshot._EventWriter(fd=3, path=tmp_path / "x")
    with pytest.raises(ValueError, match="must be 3 or greater"):
        oneshot._EventWriter(fd=2)
    with pytest.raises(ValueError, match="is not open"):
        oneshot._EventWriter(fd=999)
    with pytest.raises(ValueError, match="cannot open event file"):
        oneshot._EventWriter(path=tmp_path)  # a directory
    with pytest.raises(ValueError, match="cannot open event file"):
        oneshot._EventWriter(path="/dev/null")  # not a regular file
    target = tmp_path / "events.jsonl"
    writer = oneshot._EventWriter(path=target)
    writer.write({"event": "ok"})
    writer.close()
    writer.close()
    assert target.read_text() == '{"event":"ok"}\n'
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="cannot open event file"):
        oneshot._EventWriter(path=link)  # O_NOFOLLOW


# ---------------------------------------------------------------------------
# Docker helpers (F1/F2)
# ---------------------------------------------------------------------------


def test_inspect_is_limited_to_containers_and_requires_the_exact_identity(monkeypatch):
    seen = []

    def run_cmd(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, run_cmd.stdout, "")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    run_cmd.stdout = json.dumps([_inspect_document("other", {})])
    with pytest.raises(RuntimeError, match="returned 'other'"):
        oneshot._docker_inspect("wanted")
    assert seen[-1] == ["docker", "inspect", "--type", "container", "wanted"]

    run_cmd.stdout = json.dumps([_inspect_document("x", {}, cid=_OTHER_ID)])
    with pytest.raises(RuntimeError, match=f"returned {_OTHER_ID}"):
        oneshot._docker_inspect(_CONTAINER_ID)

    run_cmd.stdout = json.dumps([_inspect_document("x", {}, cid="short")])
    with pytest.raises(RuntimeError, match="full 64-hex"):
        oneshot._docker_inspect("x")

    run_cmd.stdout = "not json"
    with pytest.raises(RuntimeError, match="invalid JSON"):
        oneshot._docker_inspect("x")

    run_cmd.stdout = json.dumps([{}, {}])
    with pytest.raises(RuntimeError, match="one exact container"):
        oneshot._docker_inspect("x")

    run_cmd.stdout = json.dumps("scalar")
    with pytest.raises(RuntimeError, match="invalid container object"):
        oneshot._docker_inspect("x")

    run_cmd.stdout = json.dumps(_inspect_document("x", {}))  # a bare object is accepted
    assert oneshot._docker_inspect("x")["Id"] == _CONTAINER_ID

    def no_docker(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", no_docker)
    with pytest.raises(RuntimeError, match="Docker became unavailable"):
        oneshot._docker_inspect("x")


@pytest.mark.parametrize(
    "name", ["abc", "deadbeef", "0123456789abcdef", "a" * 64, "1"],
)
def test_hex_lookalike_names_are_refused(name):
    with pytest.raises(ValueError, match="look like a Docker container ID"):
        oneshot.validate_container_name(name)


@pytest.mark.parametrize("name", ["run-1", "ABCDEF", "abc.def", "a" * 65, "abcg"])
def test_names_that_cannot_be_an_id_prefix_are_accepted(name):
    assert oneshot.validate_container_name(name) == name


def test_generated_name_can_never_resolve_as_an_id(tmp_path):
    name = oneshot._select_run_name("demo-runner", "worker", None)
    assert name.startswith("demo-runner-worker-run-")
    assert "-" in name
    with pytest.raises(ValueError, match="too long"):
        oneshot._select_run_name("p" * 130, "worker", None)


def test_remove_owned_container_helper_branches(monkeypatch):
    run_id = "run-1"
    removed = []
    state = {
        "ps": (0, _CONTAINER_ID + "\n"),
        "ps_raises": None,
        "docs": {_CONTAINER_ID: _inspect_document("x", {"ciu.run.id": run_id})},
        "inspect_raises": False,
        "rm_rc": 0,
    }

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        if argv[:2] == ["docker", "ps"]:
            if state["ps_raises"]:
                raise state["ps_raises"]
            return subprocess.CompletedProcess(argv, state["ps"][0], state["ps"][1], "")
        if argv[:2] == ["docker", "inspect"]:
            if state["inspect_raises"]:
                return subprocess.CompletedProcess(argv, 0, "not json", "")
            document = state["docs"].get(argv[-1])
            if document is None:
                return subprocess.CompletedProcess(argv, 1, "", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps([document]), "")
        removed.append(argv[-1])
        return subprocess.CompletedProcess(argv, state["rm_rc"], "", "")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    assert oneshot._remove_owned_container(run_id) is True
    assert removed == [_CONTAINER_ID]

    # a rm failure is reported
    state["rm_rc"] = 1
    assert oneshot._remove_owned_container(run_id) is False
    state["rm_rc"] = 0

    # not ours: left alone even when the caller names it as a candidate
    removed.clear()
    state["docs"][_OTHER_ID] = _inspect_document("y", {"ciu.run.id": "someone-else"}, cid=_OTHER_ID)
    state["ps"] = (0, "")
    assert oneshot._remove_owned_container(run_id, candidate_id=_OTHER_ID) is True
    assert removed == []

    # candidate that is already gone
    assert oneshot._remove_owned_container(run_id, candidate_id="c" * 64) is True

    # labels that cannot be read: not removed, reported as unclean
    state["docs"][_OTHER_ID]["Config"]["Labels"] = "bogus"
    assert oneshot._remove_owned_container(run_id, candidate_id=_OTHER_ID) is False
    assert removed == []

    # an inspect that returns garbage: not removed, reported as unclean
    state["inspect_raises"] = True
    assert oneshot._remove_owned_container(run_id, candidate_id=_OTHER_ID) is False
    state["inspect_raises"] = False

    # the label query itself failing still allows the verified candidate path
    state["docs"][_OTHER_ID]["Config"]["Labels"] = {"ciu.run.id": run_id}
    state["ps"] = (1, "")
    assert oneshot._remove_owned_container(run_id, candidate_id=_OTHER_ID) is False
    assert removed == [_OTHER_ID]
    removed.clear()
    state["ps"] = (0, "short\n")
    assert oneshot._remove_owned_container(run_id) is False
    state["ps_raises"] = FileNotFoundError("docker")
    assert oneshot._remove_owned_container(run_id) is False
    assert removed == []


def test_owned_container_id_query_is_keyed_on_the_run_label(monkeypatch):
    seen = []

    def run_cmd(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, _CONTAINER_ID + "\n", "")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    assert oneshot._owned_container_ids("run-9") == [_CONTAINER_ID]
    assert "label=ciu.run.id=run-9" in seen[0] and "--no-trunc" in seen[0]


@pytest.mark.parametrize(
    ("state", "started"),
    [
        ({"Status": "running"}, True),
        ({"Status": "paused"}, True),
        ({"Status": "restarting"}, True),
        ({"Status": "created", "StartedAt": _ZERO_TIME}, False),
        ({"Status": "exited", "StartedAt": "2026-11-05T00:00:00Z"}, True),
        ({"Status": "exited", "StartedAt": _ZERO_TIME}, False),
        ({"Status": "exited"}, False),
        ("not a mapping", False),
    ],
)
def test_container_started_semantics(state, started):
    assert oneshot._container_started({"State": state}) is started


# ---------------------------------------------------------------------------
# Remaining contracts
# ---------------------------------------------------------------------------


def test_stack_prepare_lock_is_exclusive_and_per_run_overlays_are_private(tmp_path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    with oneshot._stack_prepare_lock(stack_dir):
        lock_path = stack_dir / ".machine" / ".ciu-run.lock"
        assert lock_path.stat().st_mode & 0o777 == 0o600
        fd = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    ownership = stack_dir / ".machine" / "ciu.compose.ownership.yml"
    ownership.write_text("services:\n  worker:\n    labels:\n      ciu.instance: i1\n")
    prepared = _prepared(stack_dir)
    prepared.ownership_path = ownership
    prepared.file_args = [
        "-f", "ciu.compose.yml", "-f", ".machine/ciu.compose.ownership.yml",
    ]
    temp_roots = []
    for _ in range(2):
        with __import__("tempfile").TemporaryDirectory(prefix="ciu-run-test-") as temp:
            temp_dir = Path(temp)
            args = oneshot._compose_file_args(
                prepared, temp_dir=temp_dir, extend=None, service_name="worker",
            )
            overlay = Path(args[-1])
            assert overlay != ownership
            assert overlay.read_text() == ownership.read_text()
            temp_roots.append(temp_dir)
    assert temp_roots[0] != temp_roots[1]


def test_event_fd_is_writable_and_keeps_events_off_standard_streams(tmp_path, capsys):
    read_fd, write_fd = os.pipe()
    try:
        writer = oneshot._EventWriter(fd=write_fd)
        writer.write({"schema": "ciu-run-event/1", "event": "launched"})
        assert os.read(read_fd, 4096) == b'{"event":"launched","schema":"ciu-run-event/1"}\n'
        writer.close()  # borrowed descriptors remain open
        assert os.fstat(write_fd)
        with pytest.raises(ValueError, match="not writable"):
            oneshot._EventWriter(fd=read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_missing_stack_network_refuses_before_compose_run(tmp_path, monkeypatch, world):
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "compose", "--progress"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_compose_config()), "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        raise AssertionError(f"one-shot must not launch: {argv!r}")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(oneshot.procutil, "popen", lambda *args, **kwargs: pytest.fail("started"),
                        raising=False)

    with pytest.raises(ValueError, match="required stack network .* is not present"):
        _foreground(tmp_path, "ciurun-test-unit-no-network")

    assert not any("run" in argv and "compose" in argv for argv in calls)


def test_run_cli_argument_validation(tmp_path):
    def call(**kwargs):
        argv = kwargs.pop("argv", ["true"])
        return oneshot.run_cli(tmp_path, "tools/runner:worker", argv, **kwargs)

    with pytest.raises(ValueError, match="requires a command"):
        call(argv=[])
    with pytest.raises(ValueError, match="invalid environment variable name"):
        call(env_names=["bad name"])
    with pytest.raises(ValueError, match="--dry-run` requires --json"):
        call(dry_run=True)
    with pytest.raises(ValueError, match="--detach` requires --json"):
        call(detach=True)
    with pytest.raises(ValueError, match="uses --events-fd or --events-file, not --json"):
        call(json_output=True)
    with pytest.raises(ValueError, match="must be 3 or greater"):
        call(events_fd=1)
    with pytest.raises(ValueError, match="--env-name values must be unique"):
        call(env_names=["A", "A"])
    with pytest.raises(ValueError, match="--label keys must be unique"):
        call(labels=["a=1", "a=2"])
    with pytest.raises(ValueError, match="container name"):
        call(name="-bad")


def test_detached_default_owner_is_the_parent_process(tmp_path, monkeypatch, world):
    _install_fake_docker(monkeypatch)
    owners = []
    real = oneshot._make_runtime_labels

    def spy(**kwargs):
        owners.append(kwargs["owner_pid"])
        return real(**kwargs)

    monkeypatch.setattr(oneshot, "_make_runtime_labels", spy)
    oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["true"], detach=True, json_output=True,
        name="ciurun-test-unit-owner",
    )
    assert owners == [os.getppid()]
