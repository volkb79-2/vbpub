"""Behavioral tests for the standalone RG-80 Docker ticket module."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_gate_admission as admission
import run_gate


class Clock:
    def __init__(self, value=1_800_000_000):
        self.value = float(value)

    def wall(self):
        return self.value

    def mono(self):
        return self.value - 1_700_000_000

    def sleep(self, seconds):
        self.value += seconds


class FakeDocker:
    """Small in-memory Docker surface; names and labels drive real decisions."""
    def __init__(self):
        self.calls = []
        self.objects = {}
        self.images = {"ticket:local": True}
        self.before_create = None
        self._next_id = 1

    @staticmethod
    def _completed(argv, code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    def _list(self, args):
        names = [arg.split("=", 1)[1] for arg in args
                 if arg.startswith("name=")]
        labels = [arg.split("=", 1)[1] for arg in args
                  if arg.startswith("label=")]
        selected = []
        for name, item in self.objects.items():
            if names and not all(needle in name for needle in names):
                continue
            if labels and not all(item["Config"]["Labels"].get(
                    key, "") == value for key, value in
                    (label.split("=", 1) for label in labels)):
                continue
            selected.append(item)
        return selected

    def __call__(self, argv, *, capture_output=False, text=False, timeout=None):
        self.calls.append(list(argv))
        args = list(argv[1:])
        if args[:1] == ["image"] and args[1:2] == ["inspect"]:
            image = args[2]
            return self._completed(argv, 0 if image in self.images else 1,
                                   "[]" if image in self.images else "",
                                   "" if image in self.images else "No such image")
        if args[:1] == ["run"]:
            image = args[-1]
            if image not in self.images:
                return self._completed(argv, 125, stderr="No such image")
            if not self.images[image]:
                return self._completed(argv, 126, stderr="/bin/true not found")
            return self._completed(argv, 0)
        if args[:1] == ["ps"]:
            objects = self._list(args)
            return self._completed(argv, 0,
                                   "\n".join(item["Id"] for item in objects))
        if args[:1] == ["inspect"]:
            wanted = set(args[1:])
            objects = [item for item in self.objects.values()
                       if item["Id"] in wanted]
            return self._completed(argv, 0, json.dumps(objects))
        if args[:1] == ["create"]:
            if self.before_create is not None:
                hook, self.before_create = self.before_create, None
                hook(self, args)
            name = args[args.index("--name") + 1]
            if name in self.objects:
                return self._completed(argv, 125, stderr="Conflict: name is already in use")
            labels = {}
            index = 0
            while index < len(args):
                if args[index] == "--label":
                    key, value = args[index + 1].split("=", 1)
                    labels[key] = value
                    index += 2
                else:
                    index += 1
            self._add(name, labels)
            return self._completed(argv, 0, self.objects[name]["Id"])
        if args[:1] == ["start"]:
            self.objects[args[1]]["State"]["Status"] = "exited"
            return self._completed(argv)
        if args[:1] == ["wait"]:
            return self._completed(argv, stdout="0\n")
        if args[:1] == ["stop"]:
            self.objects[args[-1]]["State"]["Status"] = "exited"
            return self._completed(argv, stdout=args[-1] + "\n")
        if args[:1] == ["rm"]:
            self.objects.pop(args[-1], None)
            return self._completed(argv)
        if args[:2] == ["context", "show"]:
            return self._completed(argv, stdout="default\n")
        if args[:2] == ["context", "inspect"]:
            return self._completed(argv, stdout="unix:///var/run/docker.sock\n")
        raise AssertionError(f"unhandled fake docker call: {args!r}")

    def _add(self, name, labels, status="created"):
        ident = f"fake-{self._next_id}"
        self._next_id += 1
        self.objects[name] = {
            "Id": ident,
            "Name": "/" + name,
            "Config": {"Labels": dict(labels)},
            "State": {"Status": status, "ExitCode": 0},
        }


def make_manager(fake, clock, notices=None):
    return admission.DockerAdmission(
        run=fake, clock=clock.wall, monotonic=clock.mono,
        sleep=clock.sleep, owner_provider=lambda lane, run_id: admission.owner_tuple(
            lane, run_id), conflict_wait=6, poll_seconds=1,
        cgroup_parent="dev-gates.slice",
        notice=(notices.append if notices is not None else None))


def publish(manager, limit=1, policy="refuse"):
    return manager.publish("ticket:local", limit, owner_version="run-gate-test",
                           unreadable_policy=policy)


def add_ticket(fake, name, owner, deadline, *, kind="lane", status="created",
               group=None, scheme="ticket"):
    group = group or name
    labels = {
        "ciu.reservation.deadline": str(deadline),
        "ciu.reservation.group": group,
        "ciu.reservation.kind": kind,
        "ciu.reservation.owner": admission.compact_json(owner),
        "ciu.reservation.scheme": scheme,
        "ciu.reservation.tier": "gates",
    }
    fake._add(name, labels, status)


def test_owner_and_label_encodings_match_the_shared_v8_fixture():
    fixture = (Path(__file__).resolve().parents[2]
               / "tests" / "fixtures" / "admission-label-grammar.json")
    expected = json.loads(fixture.read_text())
    assert expected["surface"] == "label_value_grammar"
    assert expected["labels"] == admission.LABEL_VALUE_GRAMMAR
    owner = admission.owner_tuple("unit", "run-1")
    encoded = admission.compact_json(owner)
    assert owner["pid"] == os.getpid()
    assert admission.validate_owner(encoded) == owner
    assert list(json.loads(encoded)) == sorted(owner)
    labels = {
        "ciu.reservation.deadline": "1800000300",
        "ciu.reservation.group": "ciu-res-gates-1",
        "ciu.reservation.kind": "lane",
        "ciu.reservation.owner": encoded,
        "ciu.reservation.scheme": "ticket",
        "ciu.reservation.tier": "gates",
    }
    assert admission.parse_reservation_labels(labels)["owner"] == owner
    assert set(expected["labels"]) == set(admission.LABEL_VALUE_GRAMMAR)


def test_process_start_ticks_parses_field_22_after_parenthesized_comm(tmp_path):
    proc = tmp_path / "4242"
    proc.mkdir()
    # field 3 (state), fields 4–21, and field 22 (starttime). The command
    # contains both spaces and a closing parenthesis, which naive split-based
    # parsers mistake for the end of the comm field.
    fields = ["S", *(["0"] * 18), "7654321"]
    (proc / "stat").write_text(f"4242 (runner ) with spaces) {' '.join(fields)}\n")

    assert admission.process_start_ticks(4242, tmp_path) == 7654321


def test_owner_validation_rejects_pid_zero():
    owner = admission.owner_tuple("unit", "run-1")
    owner["pid"] = 0

    assert admission.validate_owner(admission.compact_json(owner)) is None


def test_ticket_tombstone_preserves_monotone_numbers_and_reaps_lower_tombstone():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    publish(manager, 1)
    first = manager.acquire(lane="unit", run_id="r1", image="ticket:local",
                            wait_seconds=0, budget_seconds=30)
    assert first is not None and first.number == 1
    first_labels = fake.objects[first.name]["Config"]["Labels"]
    assert admission.parse_reservation_labels(first_labels)["group"] == first.name
    assert first.marker == "ciu-run-" + first.name
    manager.release(first)
    assert fake.objects[first.name]["State"]["Status"] == "exited"
    assert first.marker not in fake.objects

    second = manager.acquire(lane="unit", run_id="r2", image="ticket:local",
                             wait_seconds=0, budget_seconds=30)
    assert second is not None and second.number == 2
    assert first.name not in fake.objects  # lower tombstone retired after #2 exists
    manager.release(second)
    assert fake.objects[second.name]["State"]["Status"] == "exited"


def test_ticket_cannot_be_released_while_a_group_member_is_active():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    publish(manager, 1)
    ticket = manager.acquire(lane="unit", run_id="r1", image="ticket:local",
                             wait_seconds=0, budget_seconds=30)
    assert ticket is not None
    fake._add("lane-container", {"ciu.reservation.group": ticket.name}, "running")
    with pytest.raises(admission.AdmissionError, match="live members"):
        manager.release(ticket)
    with pytest.raises(admission.AdmissionError, match="live members"):
        manager.release_name(ticket.name, ticket.number)
    assert ticket.marker in fake.objects
    assert fake.objects[ticket.name]["State"]["Status"] == "created"
    fake.objects["lane-container"]["State"]["Status"] = "exited"
    manager.release(ticket)


def test_fifo_ticket_admits_when_earlier_ticket_releases():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    publish(manager, 1)
    first = manager.acquire(lane="first", run_id="r1", image="ticket:local",
                            wait_seconds=0, budget_seconds=30)
    assert first is not None and first.number == 1

    released = False
    def wait_one_second(seconds):
        nonlocal released
        clock.value += seconds
        if not released:
            manager.release(first)
            released = True

    manager.sleep = wait_one_second
    second = manager.acquire(lane="second", run_id="r2", image="ticket:local",
                             wait_seconds=3, budget_seconds=30)
    assert second is not None and second.number == 2
    assert second.waited_s == 1
    manager.release(second)


def test_publish_uses_the_winning_name_as_generation_and_replaces_old_object():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    first = publish(manager, 1)
    assert first["generation"] == 1

    second = manager.publish("ticket:local", 2,
                             owner_version="run-gate-test",
                             unreadable_policy="refuse", replace=True)
    assert second["generation"] == 2
    assert set(name for name in fake.objects
               if name.startswith(admission.ADMISSION_PREFIX)) == {
                   "ciu-admission-2"}
    assert fake.objects["ciu-admission-2"]["Config"]["Labels"][
        "ciu.admission.generation"] == "2"


def test_publish_retries_generation_after_a_concurrent_name_winner():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    owner = {"ciu_version": "ciu-test", "host": "host", "time": 1,
             "user": "user"}

    def race(fake_docker, args):
        name = args[args.index("--name") + 1]
        if name == "ciu-admission-1":
            fake_docker._add(name, {
                "ciu.admission.generation": "1",
                "ciu.admission.owner": admission.compact_json(owner),
                "ciu.admission.unreadable_policy": "refuse",
                "ciu.admission.tiers.gates.max_concurrent": "1",
            })

    fake.before_create = race
    result = publish(manager, 2)
    assert result["generation"] == 2
    assert set(name for name in fake.objects
               if name.startswith(admission.ADMISSION_PREFIX)) == {
                   "ciu-admission-2"}


def test_publish_refuses_visible_unreadable_generation_conflict():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)

    def race(fake_docker, args):
        name = args[args.index("--name") + 1]
        fake_docker._add(name, {"ciu.admission.generation": "1"})

    fake.before_create = race

    with pytest.raises(admission.AdmissionError,
                       match="visible object does not advance the allocator"):
        publish(manager, 1)


def test_zero_generation_is_reported_as_unreadable_and_never_used():
    fake, clock, notices = FakeDocker(), Clock(), []
    manager = make_manager(fake, clock, notices)
    owner = {"ciu_version": "ciu-test", "host": "host", "time": 1,
             "user": "user"}
    fake._add("ciu-admission-0", {
        "ciu.admission.generation": "0",
        "ciu.admission.owner": admission.compact_json(owner),
        "ciu.admission.unreadable_policy": "refuse",
        "ciu.admission.tiers.gates.max_concurrent": "1",
    })

    assert manager.read_limit() == (None, "unreadable", None, None)
    assert any("unreadable published object" in message for message in notices)


def test_ticket_names_must_be_positive_decimal_numbers():
    assert admission.DockerAdmission._ticket_number("ciu-res-gates-1") == 1
    with pytest.raises(admission.AdmissionError, match="malformed"):
        admission.DockerAdmission._ticket_number("ciu-res-gates-0")


def test_name_conflict_retries_same_number_then_advances_after_winner_is_visible():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    publish(manager, 2)
    owner = admission.owner_tuple("other", "r0")

    def race(fake_docker, args):
        name = args[args.index("--name") + 1]
        add_ticket(fake_docker, name, owner, int(clock.wall()) + 300)

    fake.before_create = race
    acquired = manager.acquire(lane="unit", run_id="r1", image="ticket:local",
                               wait_seconds=0, budget_seconds=30)
    assert acquired is not None and acquired.number == 2
    assert "ciu-res-gates-1" in fake.objects


def test_name_conflict_retries_same_number_while_name_is_not_visible():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    attempts = []

    def create(name, image, labels, *, timeout=None):
        attempts.append(name)
        return len(attempts) > 1

    manager._create_named = create
    name, number = manager._create_next(
        admission.TICKET_PREFIX, "ticket:local", lambda _name: {}, lambda: 1)

    assert (name, number) == ("ciu-res-gates-1", 1)
    assert attempts == ["ciu-res-gates-1", "ciu-res-gates-1"]
    assert clock.value > 1_800_000_000


def test_name_conflict_uses_newly_visible_higher_number_without_waiting():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    attempts = []
    numbers = iter((1, 2, 2))

    def create(name, image, labels, *, timeout=None):
        attempts.append(name)
        return len(attempts) > 1

    manager._create_named = create
    name, number = manager._create_next(
        admission.TICKET_PREFIX, "ticket:local", lambda _name: {},
        lambda: next(numbers))

    assert (name, number) == ("ciu-res-gates-2", 2)
    assert attempts == ["ciu-res-gates-1", "ciu-res-gates-2"]
    assert clock.value == 1_800_000_000


def test_malformed_ticket_counts_live_and_refusal_releases_own_ticket():
    fake, clock, notices = FakeDocker(), Clock(), []
    manager = make_manager(fake, clock, notices)
    publish(manager, 1)
    fake._add("ciu-res-gates-1", {"ciu.reservation.tier": "gates"})
    with pytest.raises(admission.AdmissionRefused):
        manager.acquire(lane="unit", run_id="r1", image="ticket:local",
                        wait_seconds=0, budget_seconds=30)
    assert any("malformed ticket labels" in message for message in notices)
    assert fake.objects["ciu-res-gates-2"]["State"]["Status"] == "exited"


def test_expired_marker_stops_group_member_but_never_removes_it():
    fake, clock, notices = FakeDocker(), Clock(), []
    manager = make_manager(fake, clock, notices)
    owner = admission.owner_tuple("unit", "dead-run")
    owner["pid"] = 999_999_999
    owner["start_ticks"] = 1
    add_ticket(fake, "ciu-res-gates-1", owner, int(clock.wall()) + 300)
    marker = dict(fake.objects["ciu-res-gates-1"]["Config"]["Labels"])
    marker.update({"ciu.reservation.kind": "marker",
                   "ciu.reservation.deadline": str(int(clock.wall()) - 1)})
    fake._add("ciu-run-ciu-res-gates-1", marker)
    lane_labels = {"ciu.reservation.group": "ciu-res-gates-1"}
    fake._add("run-gate-test-lane", lane_labels, "running")

    manager.reap()

    assert fake.objects["run-gate-test-lane"]["State"]["Status"] == "exited"
    assert "run-gate-test-lane" in fake.objects
    assert "ciu-run-ciu-res-gates-1" not in fake.objects
    assert fake.objects["ciu-res-gates-1"]["State"]["Status"] == "exited"
    assert any("reaped abandoned ticket" in message for message in notices)


def test_dead_owner_without_marker_is_reaped_after_its_wait_deadline():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    owner = admission.owner_tuple("unit", "dead-before-marker")
    owner["pid"] = 999_999_999
    owner["start_ticks"] = 1
    ticket_name = "ciu-res-gates-1"
    add_ticket(fake, ticket_name, owner, int(clock.wall()) - 1)

    manager.reap()

    assert fake.objects[ticket_name]["State"]["Status"] == "exited"
    assert "ciu-run-" + ticket_name not in fake.objects


def test_expired_marker_keeps_ticket_when_group_member_is_only_created():
    fake, clock, notices = FakeDocker(), Clock(), []
    manager = make_manager(fake, clock, notices)
    owner = admission.owner_tuple("unit", "run")
    ticket_name = "ciu-res-gates-1"
    add_ticket(fake, ticket_name, owner, int(clock.wall()) + 300)
    marker = dict(fake.objects[ticket_name]["Config"]["Labels"])
    marker.update({"ciu.reservation.kind": "marker",
                   "ciu.reservation.deadline": str(int(clock.wall()) - 1)})
    fake._add("ciu-run-" + ticket_name, marker)
    fake._add("created-lane-container",
              {"ciu.reservation.group": ticket_name}, "created")

    manager.reap()

    assert fake.objects["created-lane-container"]["State"]["Status"] == "created"
    assert fake.objects[ticket_name]["State"]["Status"] == "created"
    assert "ciu-run-" + ticket_name in fake.objects
    assert any("is created; keeping" in message for message in notices)


def test_unmatched_owner_namespace_is_unknown_until_deadline():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    owner = admission.owner_tuple("unit", "remote")
    owner["pid_ns"] = str(int(owner["pid_ns"]) + 1)
    assert manager._owner_is_provably_dead(owner) is False
    add_ticket(fake, "ciu-res-gates-1", owner, int(clock.wall()) + 300)
    manager.reap()
    assert fake.objects["ciu-res-gates-1"]["State"]["Status"] == "created"


@pytest.mark.parametrize(
    ("kill_error", "expected_dead"),
    [(None, False), (ProcessLookupError(), True), (PermissionError(), False)],
)
def test_missing_proc_stat_requires_kernel_proof_of_death(
        tmp_path, monkeypatch, kill_error, expected_dead):
    proc = tmp_path
    boot = proc / "sys/kernel/random"
    boot.mkdir(parents=True)
    (boot / "boot_id").write_text("fixture-boot\n")
    pid_ns = proc / "self/ns/pid"
    pid_ns.parent.mkdir(parents=True)
    pid_ns.write_text("namespace fixture\n")
    namespace_inode = str(os.stat(pid_ns).st_ino)
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    manager.proc_root = str(proc)
    owner = {
        "boot_id": "fixture-boot",
        "host": admission.socket.gethostname().split(".", 1)[0],
        "lane": "unit",
        "pid": 77,
        "pid_ns": namespace_inode,
        "run_id": "run-1",
        "start_ticks": 1,
    }

    def fake_kill(pid, signal):
        assert (pid, signal) == (77, 0)
        if kill_error is not None:
            raise type(kill_error)()

    monkeypatch.setattr(admission.os, "kill", fake_kill)

    assert manager._owner_is_provably_dead(owner) is expected_dead


def test_same_namespace_live_owner_remains_live():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    owner = admission.owner_tuple("unit", "live-run")
    ticket_name = "ciu-res-gates-1"
    add_ticket(fake, ticket_name, owner, int(clock.wall()) + 300)

    assert manager._owner_is_provably_dead(owner) is False
    manager.reap()

    assert fake.objects[ticket_name]["State"]["Status"] == "created"


def test_local_image_probe_is_bounded_and_placed_in_the_gates_slice():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    manager.verify_image("ticket:local")
    probe = next(call for call in fake.calls if call[1:2] == ["run"])
    assert probe[1:] == ["run", "--rm", "--pull=never", "--network=none",
                         "--cgroup-parent", "dev-gates.slice", "--entrypoint",
                         "/bin/true", "ticket:local"]


def test_missing_policy_uses_local_unreadable_policy_without_taking_a_ticket():
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    assert manager.acquire(lane="unit", run_id="r1", image="ticket:local",
                           wait_seconds=0, unreadable_policy="unbudgeted") is None
    assert not any(call[1:2] == ["create"] and
                   any("ciu-res-gates-" in arg for arg in call)
                   for call in fake.calls)
    with pytest.raises(admission.AdmissionRefused, match="unreadable_policy=refuse"):
        manager.acquire(lane="unit", run_id="r2", image="ticket:local",
                        wait_seconds=0, unreadable_policy="refuse")


def test_disabled_switch_ignores_both_flags_once_and_never_builds_docker(
        monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled admission touched Docker")

    monkeypatch.setattr(run_gate, "_admission_manager", forbidden)
    args = type("Args", (), {"admission_wait": "3m",
                              "override_admission": True,
                              "dry_run": False})()
    result = run_gate._admit_lane("unit", {"kind": "command"},
                                  {"admission": {"enabled": False}},
                                  args, None)
    assert result[:3] == (None, None, None)
    assert len([line for line in capsys.readouterr().out.splitlines()
                if "admission is disabled" in line]) == 1


def test_enabled_admission_refuses_a_remote_docker_context(monkeypatch):
    monkeypatch.setattr(run_gate, "local_docker_endpoint",
                        lambda: (False, "context 'remote' uses tcp"))

    def forbidden(*args, **kwargs):
        raise AssertionError("remote daemon must not be read for admission")

    monkeypatch.setattr(run_gate, "_admission_manager", forbidden)
    with pytest.raises(run_gate.GateError, match="admission lane runs needs the local Docker daemon"):
        run_gate._admit_lane(
            "unit", {"kind": "command"},
            {"admission": {"enabled": True, "ticket_image": "ticket:local"}},
            type("Args", (), {"admission_wait": None,
                               "override_admission": False,
                               "dry_run": False})(), None)

    args = type("Args", (), {"target": "show", "max_concurrent": None,
                              "replace": False,
                              "unreadable_policy": None})()
    with pytest.raises(run_gate.GateError, match="admission show needs the local Docker daemon"):
        run_gate._dispatch_admission(
            args, {"admission": {"enabled": False}}, Path("run-gate.toml"))


def test_admission_config_is_project_local_and_requires_an_explicit_image():
    with pytest.raises(run_gate.GateError, match="never inherited"):
        run_gate._validate_config(
            {"schema_version": 1, "admission": {"enabled": False}},
            Path("run-gate.root.toml"), central=True)
    with pytest.raises(run_gate.GateError, match="requires an explicit"):
        run_gate._validate_config(
            {"schema_version": 1, "admission": {"enabled": True}},
            Path("run-gate.toml"), central=False)
    run_gate._validate_config(
        {"schema_version": 1, "admission": {
            "enabled": True, "ticket_image": "local:ticket",
            "unreadable_policy": "refuse"}},
        Path("run-gate.toml"), central=False)


def test_disabled_cli_accepts_flags_with_one_notice_and_no_admission_docker_calls(
        tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@example.invalid"],
            ["git", "config", "user.name", "t"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text(".run-gate/\n")
    (repo / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    config = """
        schema_version = 1
        [admission]
        enabled = false
        [environments.bare-host]
        mode = "host"
        [lanes.unit]
        kind = "command"
        environment = "bare-host"
        argv = ["true"]
        clean_tree = false
    """
    project = repo / "proj"
    project.mkdir()
    (project / "run-gate.toml").write_text(config)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "project config"], cwd=repo,
                   check=True)
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    log = tmp_path / "docker-calls.log"
    log.write_text("")
    docker = shim_dir / "docker"
    docker.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
    invoke_dir = tmp_path / "invoke"
    invoke_dir.mkdir()
    invoke = invoke_dir / "run-gate.py"
    invoke.symlink_to(Path(run_gate.__file__))
    proc = subprocess.run([sys.executable, str(invoke), "unit",
                           "--admission-wait", "3m", "--override-admission"],
                          cwd=project, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("admission is disabled") == 1
    calls = log.read_text().splitlines()
    assert not any("ciu-res-gates-" in call or "ciu-admission-" in call
                   for call in calls)
    assert not any(call.startswith("create ") for call in calls)


def test_admission_set_creates_the_published_object_in_the_configured_image(
        monkeypatch, capsys):
    fake, clock = FakeDocker(), Clock()
    manager = make_manager(fake, clock)
    monkeypatch.setattr(run_gate, "_admission_manager",
                        lambda policy: (manager, policy["ticket_image"]))
    monkeypatch.setattr(run_gate, "local_docker_endpoint",
                        lambda: (True, "local Docker Unix endpoint"))
    args = type("Args", (), {
        "target": "set", "max_concurrent": 2, "replace": False,
        "unreadable_policy": None, "admission_wait": None,
        "override_admission": False,
    })()
    result = run_gate._dispatch_admission(
        args, {"admission": {"enabled": False, "ticket_image": "ticket:local"}},
        Path("run-gate.toml"))
    assert result.verdict == "PASS"
    assert "max_concurrent=2" in capsys.readouterr().out
    name = "ciu-admission-1"
    labels = fake.objects[name]["Config"]["Labels"]
    assert labels["ciu.admission.tiers.gates.max_concurrent"] == "2"
    assert labels["ciu.admission.unreadable_policy"] == "unbudgeted"
    assert "--cgroup-parent" in next(call for call in fake.calls
                                     if call[1:2] == ["create"])


def _admission_run_project(tmp_path, argv, budget=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@example.invalid"],
            ["git", "config", "user.name", "t"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text(".run-gate/\n")
    project = repo / "project"
    project.mkdir()
    budget_line = f'budget = "{budget}"\n' if budget else ""
    (project / "run-gate.toml").write_text(
        "schema_version = 1\n"
        "[admission]\n"
        "enabled = true\n"
        'ticket_image = "ticket:local"\n'
        "[environments.local]\n"
        'mode = "host"\n'
        "[lanes.unit]\n"
        'kind = "command"\n'
        'environment = "local"\n'
        f"argv = {json.dumps(argv)}\n"
        "clean_tree = false\n"
        "profile = false\n"
        + budget_line)
    (project / "run-gate.py").symlink_to(Path(run_gate.__file__))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "admission lane"], cwd=repo,
                   check=True, capture_output=True)
    return repo, project


class RecordingAdmission:
    def __init__(self):
        self.released = []

    def acquire(self, **kwargs):
        now = time.time()
        return admission.AdmissionTicket(
            name="ciu-res-gates-test", number=1, marker="ciu-run-test",
            waited_s=0, override=kwargs.get("override", False),
            admitted_at=now, admitted_monotonic=time.monotonic(),
            run_deadline=int(now) + 3600)

    def release(self, ticket):
        self.released.append(ticket.name)


@pytest.mark.parametrize(
    ("argv", "budget", "expected"),
    [(["true"], None, 0), (["bash", "-c", "exit 1"], None, 1),
     (["sleep", "5"], "1s", 4)],
    ids=("pass", "fail", "budget-exceeded"))
def test_lane_outcome_releases_admission_ticket(
        tmp_path, monkeypatch, argv, budget, expected):
    _repo, project = _admission_run_project(tmp_path, argv, budget)
    manager = RecordingAdmission()
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("RUN_GATE_PROFILE", "off")
    monkeypatch.setenv("RUN_GATE_LOCK_DIR", str(lock_dir))
    monkeypatch.setattr(sys, "argv", [str(project / "run-gate.py")])
    monkeypatch.chdir(project)
    monkeypatch.setattr(run_gate, "local_docker_endpoint",
                        lambda: (True, "local test endpoint"))
    monkeypatch.setattr(run_gate, "_admission_manager",
                        lambda policy: (manager, policy["ticket_image"]))

    assert run_gate.main(["unit"]) == expected
    assert manager.released == ["ciu-res-gates-test"]


def test_sigterm_unwinds_and_releases_admission_ticket(tmp_path, monkeypatch):
    _repo, project = _admission_run_project(tmp_path, ["true"])
    manager = RecordingAdmission()
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("RUN_GATE_PROFILE", "off")
    monkeypatch.setenv("RUN_GATE_LOCK_DIR", str(lock_dir))
    monkeypatch.setattr(sys, "argv", [str(project / "run-gate.py")])
    monkeypatch.chdir(project)
    monkeypatch.setattr(run_gate, "local_docker_endpoint",
                        lambda: (True, "local test endpoint"))
    monkeypatch.setattr(run_gate, "_admission_manager",
                        lambda policy: (manager, policy["ticket_image"]))

    def sigterm(*args, **kwargs):
        raise SystemExit(143)

    monkeypatch.setattr(run_gate, "run_bare_host_lane", sigterm)
    assert run_gate.main(["unit"]) == 2
    assert manager.released == ["ciu-res-gates-test"]
