"""Behavior of vm_lab.guest at fake SSH, serial, and QMP boundaries."""

from __future__ import annotations

import dataclasses
import io
import json
import os
import re
import stat
from pathlib import Path

import pytest
from guest_fakes import BOOT_IDS, HOST_KEY, FakeClock, FakeMachine, FakeQmp, FakeSerial, write_fake_ssh
from vm_lab import GENERIC_PROFILE, PortForward
from vm_lab import guest as vm_guest

INIT_FAILURE = re.compile(
    r"Kernel panic - not syncing: (?:No working init found|Attempted to kill init!)", re.IGNORECASE
)
INLINE_INIT_FAILURE = re.compile(r"(?i)kernel panic - not syncing: attempted to kill init!")


@dataclasses.dataclass
class Lab:
    root: Path
    state_dir: Path
    private_dir: Path
    machine: FakeMachine
    guest: vm_guest.Guest
    clock: FakeClock
    transcript: io.StringIO


def runner_manifest(
    *,
    name: str = "guest-one",
    network: str = "guest-net",
    forward_name: str = "fwd1",
    host_port: int = 2222,
    guest_port: int | None = 22,
    serial_console: bool | None = None,
) -> dict[str, object]:
    manifest: dict[str, object] = {"schema": 1, "name": name, "network": network, f"{forward_name}_port": host_port}
    if guest_port is not None:
        manifest[f"{forward_name}_guest_port"] = guest_port
    if serial_console is not None:
        manifest["serial_console"] = serial_console
    return manifest


def write_runner(state: Path, **kwargs: object) -> None:
    (state / "runner.json").write_text(json.dumps(runner_manifest(**kwargs)))


@pytest.fixture
def make_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_ssh = write_fake_ssh(tmp_path)
    counter = 0

    def create(
        *,
        serial_console: bool = True,
        clock: FakeClock | None = None,
        log: io.StringIO | None = None,
    ) -> Lab:
        nonlocal counter
        counter += 1
        root = tmp_path / f"guest-{counter}"
        root.mkdir()
        state_dir = root / "state"
        state_dir.mkdir()
        machine = FakeMachine(state_dir)
        write_runner(state_dir, network="guest-one-net", serial_console=serial_console)
        private_dir = root / "private"
        private_dir.mkdir(mode=0o700)
        key = private_dir / "id_ed25519"
        key.write_text("not real private key material\n")
        key.chmod(0o600)
        known_hosts = private_dir / "known_hosts"
        known_hosts.write_text(f"guest-one ssh-ed25519 {HOST_KEY}\n")
        monkeypatch.setenv("FAKE_SSH_STATE", str(machine.state_path))
        active_clock = clock or FakeClock()
        transcript = log or io.StringIO()
        endpoint = vm_guest.SshEndpoint(
            host="192.0.2.10", port=2222, alias="guest-one", key=key, known_hosts=known_hosts
        )
        guest = vm_guest.Guest(
            state_dir, endpoint, ssh_program=str(fake_ssh), log=transcript, clock=active_clock, sleep=active_clock.sleep
        )
        return Lab(root, state_dir, private_dir, machine, guest, active_clock, transcript)

    return create


@pytest.fixture
def lab(make_lab) -> Lab:
    active = make_lab()
    yield active
    active.guest.close()


def ready(lab: Lab) -> None:
    lab.guest.wait_ready(10)


# --- endpoint resolution and private material -----------------------------------------------
def write_credentials(path: Path, *, host_keys: str | None = None) -> Path:
    path.mkdir(mode=0o700)
    key = path / "id_ed25519"
    key.write_text("private key bytes\n")
    key.chmod(0o600)
    if host_keys is not None:
        (path / "known_hosts").write_text(host_keys)
    return path


def test_ssh_endpoint_resolves_the_dynamic_forward_and_copies_pinned_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state, name="endpoint-guest", network="endpoint-net")
    credentials = write_credentials(
        tmp_path / "credentials",
        host_keys=f"DR-HOSTKEY ssh-ed25519 {HOST_KEY}\n",
    )
    monkeypatch.setattr(
        vm_guest,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"endpoint-net": {"IPAddress": "192.0.2.44"}}}},
    )
    endpoint = vm_guest.ssh_endpoint(
        state,
        credentials,
        private_dir=tmp_path / "private",
    )
    assert endpoint.host == "192.0.2.44"
    assert endpoint.port == 2222
    assert endpoint.alias == "endpoint-guest"
    assert endpoint.user == "root"
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700
    assert stat.S_IMODE(endpoint.key.stat().st_mode) == 0o600
    assert stat.S_IMODE(endpoint.known_hosts.stat().st_mode) == 0o600
    assert endpoint.key.read_text() == "private key bytes\n"
    assert endpoint.known_hosts.read_text() == f"endpoint-guest ssh-ed25519 {HOST_KEY}\n"


def test_ssh_endpoint_uses_a_fixed_profile_forward_even_without_a_manifest_guest_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(
        state,
        name="fixed-guest",
        network="fixed-net",
        forward_name="ssh1",
        host_port=2022,
        guest_port=None,
    )
    credentials = write_credentials(
        tmp_path / "credentials",
        host_keys=f"fixed-guest ssh-ed25519 {HOST_KEY}\n",
    )
    profile = dataclasses.replace(
        GENERIC_PROFILE,
        sizing_from_cli=False,
        guest_memory_mib=512,
        cpus=1,
        forwards=(PortForward("ssh1", 2022, 22),),
    )
    monkeypatch.setattr(
        vm_guest,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"fixed-net": {"IPAddress": "192.0.2.45"}}}},
    )
    endpoint = vm_guest.ssh_endpoint(
        state,
        credentials,
        profile=profile,
        private_dir=tmp_path / "private",
    )
    assert endpoint.port == 2022


def test_ssh_endpoint_refuses_to_invent_a_network_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    credentials = write_credentials(
        tmp_path / "credentials",
        host_keys=f"guest-one ssh-ed25519 {HOST_KEY}\n",
    )
    monkeypatch.setattr(
        vm_guest,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"guest-net": {}}}},
    )
    with pytest.raises(vm_guest.GuestError, match="no network address on the exact network"):
        vm_guest.ssh_endpoint(state, credentials, private_dir=tmp_path / "private")


def test_ssh_endpoint_refuses_first_contact_without_base_host_keys(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    credentials = write_credentials(tmp_path / "credentials", host_keys="")
    with pytest.raises(vm_guest.GuestError, match="contains no usable host keys"):
        vm_guest.ssh_endpoint(state, credentials, private_dir=tmp_path / "private")


def test_ssh_endpoint_refuses_an_insecure_private_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    private = tmp_path / "private"
    private.mkdir(mode=0o755)
    monkeypatch.setattr(
        vm_guest,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"guest-net": {"IPAddress": "192.0.2.9"}}}},
    )
    with pytest.raises(vm_guest.GuestError, match="must have mode 0700"):
        vm_guest.ssh_endpoint(
            state,
            write_credentials(
                tmp_path / "credentials",
                host_keys=f"guest-one ssh-ed25519 {HOST_KEY}\n",
            ),
            private_dir=private,
        )


def test_ssh_endpoint_refuses_a_private_directory_inside_the_state_directory(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    with pytest.raises(vm_guest.GuestError, match="outside the guest state directory"):
        vm_guest.ssh_endpoint(state, tmp_path / "missing-credentials", private_dir=state / "private")


@pytest.mark.parametrize("directory_name", ["private key", "private%key"])
def test_ssh_endpoint_refuses_unsafe_ssh_paths(tmp_path: Path, directory_name: str):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    with pytest.raises(vm_guest.GuestError, match="must not contain"):
        vm_guest.ssh_endpoint(state, tmp_path / "missing-credentials", private_dir=tmp_path / directory_name)


def test_ssh_endpoint_refuses_a_private_directory_owned_by_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state = tmp_path / "state"
    state.mkdir()
    write_runner(state)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    other_uid = os.geteuid() + 1
    monkeypatch.setattr(vm_guest.os, "geteuid", lambda: other_uid)
    monkeypatch.setattr(
        vm_guest,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"guest-net": {"IPAddress": "192.0.2.9"}}}},
    )
    with pytest.raises(vm_guest.GuestError, match="must be owned by uid"):
        vm_guest.ssh_endpoint(
            state,
            write_credentials(
                tmp_path / "credentials",
                host_keys=f"guest-one ssh-ed25519 {HOST_KEY}\n",
            ),
            private_dir=private,
        )


def test_private_file_writer_closes_the_descriptor_when_fchmod_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    descriptors: list[int] = []

    def fail_fchmod(fd: int, _mode: int) -> None:
        descriptors.append(fd)
        raise OSError("fchmod failed")

    monkeypatch.setattr(vm_guest.os, "fchmod", fail_fchmod)
    with pytest.raises(vm_guest.GuestError, match="cannot write private file"):
        vm_guest._write_private_file(private / "id_ed25519", b"private")
    with pytest.raises(OSError):
        os.close(descriptors[0])


# --- SSH command behavior ------------------------------------------------------------------
def test_run_carries_remote_exit_status_stdout_and_stderr(lab: Lab):
    ready(lab)
    result = lab.guest.run("printf out; printf err >&2; exit 7")
    assert (result.rc, result.out, result.err) == (7, "out", "err")
    record = json.loads(lab.transcript.getvalue().splitlines()[-1])
    assert record["rc"] == 7
    assert record["stdout"] == "out"
    assert record["stderr"] == "err"


def test_run_feeds_input_to_remote_stdin(lab: Lab):
    ready(lab)
    result = lab.guest.run("read -r line; printf '<%s>' \"$line\"", input=b"payload\n")
    assert (result.rc, result.out, result.err) == (0, "<payload>", "")


def test_run_refuses_a_nul_byte_as_a_guest_error(lab: Lab):
    with pytest.raises(vm_guest.GuestError, match="NUL byte"):
        lab.guest.run("printf '\0'")


def test_invalid_timeouts_become_guest_errors(lab: Lab):
    with pytest.raises(vm_guest.GuestError, match="positive finite"):
        lab.guest.run("true", timeout="not-a-number")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("false;", 1),
        ("false &&", 2),
        ("true ||", 2),
        ("# comment", 0),
    ],
    ids=["trailing-semicolon", "trailing-and", "trailing-or", "comment"],
)
def test_syntax_broken_commands_keep_their_shell_status(lab: Lab, command: str, expected: int):
    ready(lab)
    assert lab.guest.run(command).rc == expected


def test_commands_run_in_bash_not_dash(lab: Lab):
    ready(lab)
    result = lab.guest.run("read -r -t 1 x </dev/null; echo $?")
    assert result.rc == 0
    assert result.out.strip() == "1"


def test_a_genuine_remote_255_is_the_commands_status(lab: Lab):
    ready(lab)
    result = lab.guest.run("exit 255")
    assert result.rc == 255
    assert lab.machine.read()["commands"] >= 1


def test_a_lost_link_is_a_transport_error_and_the_command_is_not_retried(lab: Lab):
    ready(lab)
    lab.machine.update(link_loss=True)
    before = lab.machine.read()["commands"]
    with pytest.raises(vm_guest.GuestTransportError, match="closed by remote host"):
        lab.guest.run("touch executed")
    assert lab.machine.read()["commands"] == before


def test_a_late_master_exit_with_silent_255_is_a_transport_error(lab: Lab):
    ready(lab)
    lab.machine.update(late_check=1, silent_loss=True)
    with pytest.raises(vm_guest.GuestTransportError, match="SSH link was lost"):
        lab.guest.run("exit 255")
    assert lab.machine.read()["commands"] == 1


def test_the_local_ssh_deadline_kills_the_client(lab: Lab, monkeypatch: pytest.MonkeyPatch):
    ready(lab)
    monkeypatch.setattr(vm_guest, "SSH_LOCAL_TIMEOUT_GRACE_SECONDS", 0.05)
    with pytest.raises(vm_guest.GuestTimeout, match="local SSH deadline"):
        lab.guest.run("trap '' TERM; sleep 2", timeout=0.2)


def test_a_remote_timeout_is_classified_from_elapsed_time(lab: Lab):
    ready(lab)
    lab.guest.clock = __import__("time").monotonic
    with pytest.raises(vm_guest.GuestTimeout, match="remote command deadline"):
        lab.guest.run("sleep 1", timeout=0.2)


def test_a_changed_host_key_is_identity_not_transport(lab: Lab):
    lab.machine.update(host_key="AAAAB3NzaC1lZDI1NTE5AAAAIChangedHostKey")
    with pytest.raises(vm_guest.GuestIdentityChanged, match="host key changed"):
        lab.guest.wait_ready(1)


def test_wait_ready_on_an_existing_boot_does_not_reboot(lab: Lab):
    assert lab.guest.wait_ready(10) == BOOT_IDS[0]
    assert lab.machine.read()["boots"] == 1


def test_a_booted_guest_gets_one_master_for_commands(lab: Lab):
    ready(lab)
    state = lab.machine.read()
    assert state["master"] is True
    assert state["master_boot_id"] == BOOT_IDS[0]
    assert lab.guest.run("true").rc == 0


def test_wait_ready_replaces_a_master_left_from_a_guest_side_reboot(lab: Lab):
    ready(lab)
    assert lab.guest.run("systemctl reboot").rc == 0
    assert lab.guest.wait_ready(5) == BOOT_IDS[1]
    assert lab.machine.read()["master_boot_id"] == BOOT_IDS[1]


# --- reboot and failure proof ---------------------------------------------------------------
def test_reboot_rejects_an_old_boot_that_answers_during_shutdown(lab: Lab):
    ready(lab)
    trigger = lab.guest.trigger

    def arm_old_boot_answer(payload: str) -> None:
        lab.machine.update(old_boot_answers=1)
        trigger(payload)

    lab.guest.trigger = arm_old_boot_answer
    new = lab.guest.reboot(timeout=5)
    assert new == BOOT_IDS[1]
    assert lab.machine.read()["boots"] == 2


def test_an_unchanged_boot_id_never_proves_a_reboot(lab: Lab):
    ready(lab)
    lab.machine.update(keep_boot_id=True)
    with pytest.raises(vm_guest.RebootNotProven, match="reboot was not proven"):
        lab.guest.reboot(timeout=0.5)
    assert lab.machine.read()["boot_id"] == BOOT_IDS[0]


def test_a_damaged_default_raises_boot_failure_from_serial(lab: Lab):
    ready(lab)
    mark = lab.guest.serial_mark()
    lab.guest.trigger("sleep 1; # __DAMAGE_PANIC__")
    with pytest.raises(vm_guest.BootFailure) as raised:
        lab.guest.wait_ready(5, fail_on=INIT_FAILURE, since=mark)
    assert "Attempted to kill init" in raised.value.match.group(0)
    assert lab.machine.read()["exec_broken"] is True


def test_an_inline_ignore_case_failure_pattern_matches_a_realistic_panic(lab: Lab):
    ready(lab)
    started = lab.clock.now
    mark = lab.guest.serial_mark()
    lab.guest.trigger("sleep 1; # __DAMAGE_PANIC__")
    with pytest.raises(vm_guest.BootFailure, match="serial fail pattern matched"):
        lab.guest.wait_ready(5, fail_on=INLINE_INIT_FAILURE, since=mark)
    assert lab.clock.now == started


def test_wait_ready_implicit_mark_ignores_an_older_panic(lab: Lab):
    ready(lab)
    lab.machine.append_serial(lab.machine.read()["panic_line"])
    assert lab.guest.wait_ready(5, fail_on=INIT_FAILURE) == BOOT_IDS[0]


def test_serial_alone_observes_the_init_failure_after_ssh_is_broken(lab: Lab):
    ready(lab)
    lab.machine.update(exec_broken=True, up=False)
    mark = lab.guest.serial_mark()
    lab.machine.append_serial("Kernel panic - not syncing: Attempted to kill init!\n")
    match = lab.guest.wait_serial(INIT_FAILURE, 2, since=mark)
    assert "Attempted to kill init" in match.group(0)


def test_qmp_reset_starts_a_new_boot_and_requires_wait_readiness(lab: Lab):
    ready(lab)
    with FakeQmp(lab.state_dir / "qmp.sock", lab.machine):
        lab.guest.reset()
    assert lab.machine.read()["boot_id"] == BOOT_IDS[1]
    assert lab.guest.wait_ready(10) == BOOT_IDS[1]


# --- serial marks, waits, writes, and keys ---------------------------------------------------
def test_serial_mark_framing_rejects_a_truncated_and_regrown_log(lab: Lab):
    mark = lab.guest.serial_mark()
    lab.machine.serial_log.write_text("regrown boot\nafter\n")
    with pytest.raises(vm_guest.GuestError, match="truncated or replaced"):
        lab.guest.wait_serial(re.compile("after"), 1, since=mark)


def test_a_malformed_serial_mark_becomes_a_guest_error(lab: Lab):
    with pytest.raises(vm_guest.GuestError, match="malformed serial mark"):
        lab.guest.wait_serial(re.compile("anything"), 1, since="not-a-mark")


def test_the_earliest_serial_event_wins(lab: Lab):
    mark = lab.guest.serial_mark()
    lab.machine.append_serial("[  12.0] Kernel panic - not syncing\nbooted entry older\n")
    with pytest.raises(vm_guest.BootFailure):
        lab.guest.wait_serial(re.compile("booted entry older"), 1, since=mark, fail_on=re.compile("Kernel panic"))


def test_serial_success_wins_when_it_precedes_a_failure(lab: Lab):
    mark = lab.guest.serial_mark()
    lab.machine.append_serial("booted entry older\n[  12.0] Kernel panic - not syncing\n")
    match = lab.guest.wait_serial(re.compile("booted entry older"), 1, since=mark, fail_on=re.compile("Kernel panic"))
    assert match.group(0) == "booted entry older"


def test_a_kernel_banner_is_not_a_shell_prompt(lab: Lab):
    mark = lab.guest.serial_mark()
    lab.machine.append_serial("Linux build debian-kernel@lists.debian.org #1 root@host #\n")
    with pytest.raises(vm_guest.GuestTimeout):
        lab.guest.wait_serial(re.compile(r"^root@host #$"), 1, since=mark)


def test_serial_patterns_cannot_cross_a_line_boundary(lab: Lab):
    mark = lab.guest.serial_mark()
    lab.machine.append_serial("Linux build debian-kernel@lists.debian.org #1\nroot@host # ")
    with pytest.raises(vm_guest.GuestTimeout):
        lab.guest.wait_serial(re.compile(r"Linux build .*\nroot@host #$"), 1, since=mark)


def test_serial_prompt_matching_uses_an_unfinished_line(lab: Lab):
    lab.machine.append_serial("Linux build debian-kernel@lists.debian.org #1 root@host #\n")
    mark = lab.guest.serial_mark()
    prompt = re.compile(r"^root@host #$")

    def append_prompt(seconds: float) -> None:
        lab.clock.now += seconds
        if not getattr(append_prompt, "done", False):
            lab.machine.append_serial("root@host #")
            append_prompt.done = True

    lab.guest.sleep = append_prompt
    match = lab.guest.wait_serial(prompt, 2, since=mark)
    assert match.group(0) == "root@host #"


def test_press_until_boots_the_entry_the_fake_loader_selected(lab: Lab):
    lab.machine.update(up=False)
    mark = lab.guest.serial_mark()
    with FakeQmp(lab.state_dir / "qmp.sock", lab.machine):
        match = lab.guest.press_until(
            "down,ret",
            re.compile(r"booted entry older"),
            since=mark,
            every=0.1,
            timeout=1,
        )
    assert lab.machine.read()["entry"] == "older"
    assert match.group(0) == "booted entry older"
    assert lab.clock.now == pytest.approx(100.7)


def test_send_keys_with_one_unknown_key_sends_nothing(lab: Lab):
    with FakeQmp(lab.state_dir / "qmp.sock", lab.machine):
        with pytest.raises(ValueError, match="unknown QEMU key name"):
            lab.guest.send_keys("ret,not-a-key")
    assert lab.machine.read()["send_keys"] == 0


def test_serial_write_without_the_serial_console_record_is_an_explicit_error(lab: Lab):
    manifest = json.loads((lab.state_dir / "runner.json").read_text())
    manifest["serial_console"] = False
    (lab.state_dir / "runner.json").write_text(json.dumps(manifest))
    with pytest.raises(vm_guest.SerialInputUnavailable, match="--serial-console"):
        lab.guest.serial_write(b"x")


def test_serial_write_retries_until_the_socket_appears(lab: Lab):
    fake = FakeSerial(lab.state_dir / "serial.sock")

    def start_on_first_sleep(seconds: float) -> None:
        lab.clock.now += seconds
        if not getattr(start_on_first_sleep, "started", False):
            fake.__enter__()
            start_on_first_sleep.started = True

    lab.guest.sleep = start_on_first_sleep
    lab.guest.serial_write(b"ret\n")
    assert fake.received.wait(2)
    fake.__exit__()
    assert bytes(fake.written) == b"ret\n"


def test_a_permanent_serial_socket_error_fails_immediately(lab: Lab, monkeypatch: pytest.MonkeyPatch):
    long_path = Path("/tmp") / ("x" * 120)
    monkeypatch.setattr(vm_guest, "unix_socket_client_path", lambda *_args: long_path)
    with pytest.raises(vm_guest.GuestError, match="cannot connect"):
        lab.guest.serial_write(b"x")
    assert lab.clock.now == 100.0


def test_serial_mark_refuses_a_missing_log(lab: Lab):
    lab.machine.serial_log.unlink()
    with pytest.raises(vm_guest.GuestError, match="cannot take a serial mark"):
        lab.guest.serial_mark()


# --- network attachment --------------------------------------------------------------------
def test_attached_to_network_connects_only_for_the_block(monkeypatch: pytest.MonkeyPatch):
    networks = set()

    def docker_run(argv, **kwargs):
        if argv[2] == "connect":
            networks.add(argv[3])
        elif argv[2] == "disconnect":
            networks.discard(argv[3])
        return None

    monkeypatch.setattr(
        vm_guest,
        "inspect_current_container",
        lambda: ("caller-id", {"NetworkSettings": {"Networks": {}}}),
    )
    monkeypatch.setattr(vm_guest, "docker_run", docker_run)
    with vm_guest.attached_to_network("guest-net", plain_host=False):
        assert networks == {"guest-net"}
    assert networks == set()


def test_attached_to_network_leaves_an_existing_connection(monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        vm_guest,
        "inspect_current_container",
        lambda: ("caller-id", {"NetworkSettings": {"Networks": {"guest-net": {}}}}),
    )
    monkeypatch.setattr(vm_guest, "docker_run", lambda argv, **kwargs: calls.append(argv[2]))
    with vm_guest.attached_to_network("guest-net", plain_host=False):
        pass
    assert calls == []


def test_attached_to_network_on_a_plain_host_never_calls_docker(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(vm_guest, "inspect_current_container", lambda: pytest.fail("inspected"))
    monkeypatch.setattr(vm_guest, "docker_run", lambda *_args, **_kwargs: pytest.fail("docker"))
    with vm_guest.attached_to_network("guest-net", plain_host=True):
        pass


def test_attached_to_network_disconnects_after_a_body_error(
    monkeypatch: pytest.MonkeyPatch,
):
    networks = set()

    def docker_run(argv, **kwargs):
        if argv[2] == "connect":
            networks.add(argv[3])
        elif argv[2] == "disconnect":
            networks.discard(argv[3])
        return None

    monkeypatch.setattr(
        vm_guest,
        "inspect_current_container",
        lambda: ("caller-id", {"NetworkSettings": {"Networks": {}}}),
    )
    monkeypatch.setattr(vm_guest, "docker_run", docker_run)
    with pytest.raises(RuntimeError, match="body"):
        with vm_guest.attached_to_network("guest-net", plain_host=False):
            raise RuntimeError("body")
    assert networks == set()


def test_a_disconnect_failure_does_not_mask_a_body_error(monkeypatch: pytest.MonkeyPatch):
    def docker_run(argv, **kwargs):
        if argv[2] == "connect":
            return None
        if argv[2] == "disconnect":
            raise vm_guest.RunnerError("disconnect failed")
        raise AssertionError(argv)

    monkeypatch.setattr(
        vm_guest,
        "inspect_current_container",
        lambda: ("caller-id", {"NetworkSettings": {"Networks": {}}}),
    )
    monkeypatch.setattr(vm_guest, "docker_run", docker_run)
    with pytest.raises(RuntimeError, match="body"):
        with vm_guest.attached_to_network("guest-net", plain_host=False):
            raise RuntimeError("body")


def test_missing_cockpit_hostname_becomes_a_guest_error(monkeypatch: pytest.MonkeyPatch):
    def fail_inspect():
        raise OSError("missing /etc/hostname")

    monkeypatch.setattr(vm_guest, "inspect_current_container", fail_inspect)
    with pytest.raises(vm_guest.GuestError, match="cannot identify the calling container"):
        with vm_guest.attached_to_network("guest-net", plain_host=False):
            pass
