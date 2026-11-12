"""Behaviour of the general launcher: sizing, forwards, labels, admission, console reset.

No Docker, no QEMU, no timing: Docker and QMP calls are replaced by recorders,
and the container shell script is run with fake `qemu-system-x86_64` and
`qemu-img` programs that only write their arguments to a file.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import vm_lab
from vm_lab import GENERIC_PROFILE, RunnerError, VmProfile

TRUENAS_FRONT = Path(__file__).resolve().parents[2] / "truenas" / "tcg_vm.py"


def load_truenas_front():
    spec = importlib.util.spec_from_file_location("tcg_vm_under_test", TRUENAS_FRONT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TCG_VM = load_truenas_front()
TRUENAS = TCG_VM.PROFILE

START_ARGS = [
    "start", "--name", "deb1", "--base-disk", "base.qcow2",
    "--ovmf-code", "OVMF_CODE_4M.fd", "--ovmf-vars", "OVMF_VARS_4M.fd",
]
SIZING = ["--memory-mib", "2048", "--cpus", "2", "--forward", "2222:22"]


def parse_start(*extra: str) -> object:
    return vm_lab.build_parser(GENERIC_PROFILE).parse_args(
        START_ARGS + ["--state-dir", "artifacts/vm-lab/deb1", *extra]
    )


def sized_profile(memory_mib: int = 2048, cpus: int = 2, forwards=("2222:22",)) -> VmProfile:
    extra: list[str] = ["--memory-mib", str(memory_mib), "--cpus", str(cpus)]
    for forward in forwards:
        extra += ["--forward", forward]
    return vm_lab.resolve_profile(GENERIC_PROFILE, parse_start(*extra))


def create_argv(profile: VmProfile, host_ports: dict[str, int], **overrides) -> list[str]:
    values = dict(
        name="deb1",
        slice_name="dev-background.slice",
        network="internal",
        state_hash="a" * 64,
        host_base="/host/base.qcow2",
        host_state="/host/state",
        host_media=None,
        host_ports=host_ports,
        resume=False,
        vnc_console=False,
        serial_console=False,
        disk_sizes_bytes=[],
        image="qemu:test",
    )
    values.update(overrides)
    return vm_lab.docker_create_argv(profile, **values)


def option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, item in enumerate(argv) if item == option]


# --- sizing --------------------------------------------------------------


@pytest.mark.parametrize(
    ("memory_mib", "expected_bytes"),
    [
        (512, 1024 * 1024 * 1024),
        (2048, 2560 * 1024 * 1024),
        (3072, 3_758_096_384),
        (8192, 8704 * 1024 * 1024),
    ],
)
def test_container_memory_is_guest_ram_plus_512_mib(memory_mib, expected_bytes):
    profile = sized_profile(memory_mib=memory_mib)
    assert profile.container_memory_bytes == expected_bytes
    argv = create_argv(profile, {"fwd1": 2222})
    assert option_values(argv, "--memory") == [str(expected_bytes)]
    assert option_values(argv, "--memory-swap") == [str(expected_bytes)]
    assert f"-m {memory_mib} " in vm_lab.host_command_script(profile)


def test_three_gib_guest_reproduces_the_truenas_container_limit():
    assert sized_profile(memory_mib=3072).container_memory_bytes == TRUENAS.container_memory_bytes
    assert TRUENAS.container_memory_bytes == 3_758_096_384


@pytest.mark.parametrize("cpus", [1, 2, 4])
def test_cpu_count_sets_smp_and_the_docker_cpu_limit(cpus):
    profile = sized_profile(cpus=cpus)
    assert profile.nano_cpus == cpus * 1_000_000_000
    assert f"-smp {cpus} " in vm_lab.host_command_script(profile)
    assert option_values(create_argv(profile, {"fwd1": 2222}), "--cpus") == [str(cpus)]


def test_memory_headroom_check_uses_the_computed_container_limit(capsys):
    profile = sized_profile(memory_mib=1024)
    levels = [{
        "path": "/dev.slice/dev-background.slice",
        "cpu_max": 5.0,
        "memory_high": 5 * 1024**3,
        "memory_max": 8 * 1024**3,
        "memory_current": 1 * 1024**3,
        "memory_psi_some_avg10": 0.0,
        "memory_psi_full_avg10": 0.0,
    }]
    vm_lab.print_preflight(
        "dev-background.slice", levels, "/dev.slice/dev-background.slice",
        memory_bytes=profile.container_memory_bytes,
    )
    output = capsys.readouterr().out
    assert f"new-guest-cap={1536 * 1024 * 1024}" in output
    assert f"projected-memory={1024**3 + 1536 * 1024 * 1024}" in output


def test_cpu_quota_check_refuses_more_cpus_than_the_slice_allows():
    levels = [{
        "path": "/dev.slice/dev-background.slice",
        "cpu_max": 3.0,
        "memory_high": None,
        "memory_max": 8 * 1024**3,
        "memory_current": 1024**3,
        "memory_psi_some_avg10": 0.0,
        "memory_psi_full_avg10": 0.0,
    }]
    vm_lab.validate_memory_headroom(levels, requested_cpus=3.0)
    with pytest.raises(RunnerError, match="requires 4 CPUs but the tightest cgroup quota is 3"):
        vm_lab.validate_memory_headroom(levels, requested_cpus=4.0)


# --- forwards ------------------------------------------------------------


def test_forwards_are_rendered_into_hostfwd_and_container_environment():
    profile = sized_profile(forwards=("2222:22", "8080:80"))
    script = vm_lab.host_command_script(profile)
    assert (
        '-netdev "user,id=net0,hostfwd=tcp:0.0.0.0:${VM_FWD1_PORT}-:22,'
        'hostfwd=tcp:0.0.0.0:${VM_FWD2_PORT}-:80" \\\n'
    ) in script
    argv = create_argv(profile, {"fwd1": 2222, "fwd2": 8080})
    environment = option_values(argv, "--env")
    assert "VM_FWD1_PORT=2222" in environment
    assert "VM_FWD2_PORT=8080" in environment


def test_forwards_stay_inside_the_container_network_and_are_never_published():
    argv = create_argv(sized_profile(), {"fwd1": 2222})
    assert "--publish" not in argv and "-p" not in argv
    assert not any(item.startswith("--publish") for item in argv)
    assert option_values(argv, "--network") == ["internal"]


def run_guest_script(tmp_path: Path, profile: VmProfile, environment: dict[str, str]) -> list[str]:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "OVMF_CODE_4M.fd").write_bytes(b"code")
    (state_dir / "OVMF_VARS_4M.fd").write_bytes(b"vars")
    base_disk = tmp_path / "base.qcow2"
    base_disk.write_bytes(b"base")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "qemu-args"
    qemu = fake_bin / "qemu-system-x86_64"
    qemu.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGS_FILE\"\n")
    qemu.chmod(0o755)
    qemu_img = fake_bin / "qemu-img"
    qemu_img.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = create ]; then\n"
        "  if [ \"$7\" = -b ]; then touch \"$9\"; else touch \"$7\"; fi\n"
        "fi\n"
        "exit 0\n"
    )
    qemu_img.chmod(0o755)
    command = vm_lab.host_command_script(profile).replace("/state", str(state_dir)).replace(
        "/base/disk.qcow2", str(base_disk)
    )
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "ARGS_FILE": str(args_file),
            "VM_MODE": "create",
            "VM_NAME": "deb1",
            "VM_READONLY_MEDIA": "",
            "VM_VNC_CONSOLE": "0",
            "VM_SERIAL_CONSOLE": "0",
            **environment,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return args_file.read_text().splitlines()


def test_guest_script_gives_qemu_the_requested_sizing_and_forwards(tmp_path):
    profile = sized_profile(memory_mib=1536, cpus=3, forwards=("2222:22", "8080:80"))
    qemu_args = run_guest_script(
        tmp_path, profile, {"VM_FWD1_PORT": "2222", "VM_FWD2_PORT": "8080"}
    )
    assert qemu_args[qemu_args.index("-smp") + 1] == "3"
    assert qemu_args[qemu_args.index("-m") + 1] == "1536"
    assert qemu_args[qemu_args.index("-netdev") + 1] == (
        "user,id=net0,hostfwd=tcp:0.0.0.0:2222-:22,hostfwd=tcp:0.0.0.0:8080-:80"
    )


def test_generic_extra_disks_use_the_generic_drive_and_serial_names(tmp_path):
    profile = sized_profile()
    qemu_args = run_guest_script(
        tmp_path, profile, {"VM_FWD1_PORT": "2222", "VM_EXTRA_DISK_SIZES_BYTES": "1073741824"}
    )
    assert any(item.endswith(",if=none,id=vmlabdata1,format=qcow2,cache=unsafe") for item in qemu_args)
    assert "virtio-blk-pci,drive=vmlabdata1,serial=VMLAB001,addr=0x8" in qemu_args


# --- required and bounded command-line values ---------------------------------


@pytest.mark.parametrize(
    "missing",
    ["--state-dir", "--memory-mib", "--cpus", "--forward"],
)
def test_start_refuses_a_missing_required_option(missing, capsys):
    arguments = START_ARGS + ["--state-dir", "artifacts/vm-lab/deb1", *SIZING]
    index = arguments.index(missing)
    del arguments[index:index + 2]
    with pytest.raises(SystemExit) as exit_info:
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(arguments)
    assert exit_info.value.code == 2
    assert missing in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["status", "stop", "terminate-paused"])
def test_every_state_dir_verb_requires_an_explicit_state_dir(verb, capsys):
    parser = vm_lab.build_parser(GENERIC_PROFILE)
    with pytest.raises(SystemExit):
        parser.parse_args([verb, "--name", "deb1"])
    assert "--state-dir" in capsys.readouterr().err
    assert parser.parse_args([verb, "--name", "deb1", "--state-dir", "x"]).state_dir == "x"


@pytest.mark.parametrize("action", [["console", "sendkey", "ret"], ["console", "screendump"],
                                    ["console", "reset"], ["console", "serial"]])
def test_every_console_verb_requires_an_explicit_state_dir(action, capsys):
    parser = vm_lab.build_parser(GENERIC_PROFILE)
    with pytest.raises(SystemExit):
        parser.parse_args(action[:2] + ["--name", "deb1"] + action[2:])
    assert "--state-dir" in capsys.readouterr().err


def test_rebind_media_requires_an_explicit_state_dir(capsys):
    with pytest.raises(SystemExit):
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(
            ["rebind-media", "--name", "deb1", "--readonly-media", "x.iso"]
        )
    assert "--state-dir" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--memory-mib", "511"),
        ("--memory-mib", "8193"),
        ("--memory-mib", "lots"),
        ("--cpus", "0"),
        ("--cpus", "5"),
        ("--cpus", "1.5"),
        ("--forward", "2222"),
        ("--forward", "2222:"),
        ("--forward", "ssh:22"),
        ("--forward", "-1:22"),
    ],
)
def test_start_refuses_out_of_range_or_malformed_sizing(option, value, capsys):
    arguments = START_ARGS + ["--state-dir", "artifacts/vm-lab/deb1", *SIZING]
    arguments[arguments.index(option) + 1] = value
    with pytest.raises(SystemExit) as exit_info:
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(arguments)
    assert exit_info.value.code == 2
    assert option in capsys.readouterr().err


@pytest.mark.parametrize("memory_mib", [512, 8192])
@pytest.mark.parametrize("cpus", [1, 4])
def test_sizing_at_the_bounds_is_accepted(memory_mib, cpus):
    profile = sized_profile(memory_mib=memory_mib, cpus=cpus)
    assert (profile.guest_memory_mib, profile.cpus) == (memory_mib, cpus)


def test_forward_ports_are_range_checked_on_both_sides():
    with pytest.raises(RunnerError, match="guest port of forward 2222:70000 must be between 1 and 65535"):
        sized_profile(forwards=("2222:70000",))
    profile = sized_profile(forwards=("70000:22",))
    with pytest.raises(RunnerError, match="FWD1 port must be between 1 and 65535"):
        vm_lab.validate_host_ports(profile, {"fwd1": 70000})
    with pytest.raises(RunnerError, match="FWD1 port must be between 1 and 65535"):
        vm_lab.validate_host_ports(profile, {"fwd1": 0})


def test_two_forwards_cannot_share_a_host_port():
    profile = sized_profile(forwards=("2222:22", "2222:80"))
    with pytest.raises(RunnerError, match="FWD1 and FWD2 forwarding ports must differ"):
        vm_lab.validate_host_ports(profile, {"fwd1": 2222, "fwd2": 2222})


def test_more_than_eight_forwards_are_refused():
    with pytest.raises(RunnerError, match="at most 8 port forwards"):
        sized_profile(forwards=tuple(f"{3000 + n}:{n + 1}" for n in range(9)))


def test_a_fixed_size_profile_must_define_its_sizing():
    with pytest.raises(RunnerError, match="fixed-size profile must define"):
        dataclasses.replace(GENERIC_PROFILE, sizing_from_cli=False)
    with pytest.raises(RunnerError, match="guest memory must be between 512 and 8192 MiB"):
        dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=100)
    with pytest.raises(RunnerError, match="guest CPU count must be between 1 and 4"):
        dataclasses.replace(GENERIC_PROFILE, cpus=9)


def test_truenas_front_keeps_its_fixed_sizing_and_gains_no_sizing_options():
    parser = TCG_VM.build_parser()
    base = START_ARGS + ["--state-dir", "artifacts/vm-lab/deb1"]
    for option, value in (("--memory-mib", "2048"), ("--cpus", "2"), ("--forward", "2222:22")):
        with pytest.raises(SystemExit):
            parser.parse_args(base + [option, value])
    args = parser.parse_args(base)
    assert (args.ssh_port, args.https_port) == (2222, 8443)
    assert (TRUENAS.guest_memory_mib, TRUENAS.cpus) == (3072, 2)


def test_start_refuses_without_an_image_from_flag_or_environment(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path / "admission.lock")
    monkeypatch.delenv("VM_LAB_QEMU_IMAGE", raising=False)
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "internal")
    arguments = START_ARGS + ["--state-dir", str(tmp_path), *SIZING]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "vm_lab.py: pass --image or set VM_LAB_QEMU_IMAGE; no image fallback is selected" in (
        capsys.readouterr().err
    )


# --- labels and shared admission -------------------------------------------


def test_container_carries_the_generic_labels_and_nothing_from_truenas():
    argv = create_argv(sized_profile(), {"fwd1": 2222}, state_hash="b" * 64)
    assert option_values(argv, "--label") == [
        "org.vbpub.vm-lab.vm=true",
        "org.vbpub.vm-lab.state-dir-sha256=" + "b" * 64,
    ]


def test_all_profiles_share_one_label_registry_and_one_lock_file(monkeypatch, tmp_path):
    assert (TRUENAS.vm_label, TRUENAS.label_value) in vm_lab.KNOWN_VM_LABELS
    assert (GENERIC_PROFILE.vm_label, GENERIC_PROFILE.label_value) in vm_lab.KNOWN_VM_LABELS
    assert len(vm_lab.KNOWN_VM_LABELS) == len(set(vm_lab.KNOWN_VM_LABELS))
    assert vm_lab.ADMISSION_LOCK_FILENAME == "truenas-tcg-vm-admission.lock"

    git_dir = tmp_path / "git-common"
    git_dir.mkdir()
    monkeypatch.setattr(
        vm_lab.subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=f"{git_dir}\n", stderr=""),
    )
    assert vm_lab.vm_start_lock_path() == git_dir / "truenas-tcg-vm-admission.lock"


def test_a_second_start_is_refused_naming_the_calling_program(monkeypatch, tmp_path):
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path / "admission.lock")
    with vm_lab.vm_start_admission_lock("tcg_vm.py"):
        with pytest.raises(RunnerError, match="another vm_lab.py start is in progress; retry after it finishes"):
            with vm_lab.vm_start_admission_lock("vm_lab.py"):
                pass


def fake_running_guests(monkeypatch, by_label: dict[tuple[str, str], list[tuple[str, str]]]):
    """Docker answers `ps --filter label=KEY=VALUE` and `inspect ID` from a table.

    The table is keyed by (label key, label value); a query with another value
    finds nothing, as real Docker would.
    """
    names = {container_id: name for guests in by_label.values() for container_id, name in guests}
    ps_calls: list[list[str]] = []

    def fake_docker_run(argv, **_kwargs):
        assert argv[:4] == ["docker", "ps", "-q", "--filter"], argv
        ps_calls.append(argv)
        key, _, value = argv[4].removeprefix("label=").partition("=")
        ids = "\n".join(container_id for container_id, _ in by_label.get((key, value), []))
        return SimpleNamespace(returncode=0, stdout=ids, stderr="")

    monkeypatch.setattr(vm_lab, "docker_run", fake_docker_run)
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda container_id: {"Name": "/" + names[container_id]})
    return ps_calls


def profile_pair(profile: VmProfile) -> tuple[str, str]:
    return (profile.vm_label, profile.label_value)


def test_admission_counts_truenas_and_generic_guests_together(monkeypatch):
    ps_calls = fake_running_guests(monkeypatch, {
        profile_pair(TRUENAS): [("id-t", "tns-one")],
        profile_pair(GENERIC_PROFILE): [("id-g", "deb-one")],
    })
    active = vm_lab.active_guest_names()
    assert active == ["tns-one", "deb-one"]
    assert [call[4] for call in ps_calls] == [f"label={key}={value}" for key, value in vm_lab.KNOWN_VM_LABELS]
    # One guest of each profile fills a host-wide cap of 2 for BOTH profiles.
    with pytest.raises(RunnerError, match=r"limit 2 reached; active runner\(s\): tns-one, deb-one"):
        vm_lab.validate_active_guest_count(active, 2, GENERIC_PROFILE.max_concurrent_guests)
    with pytest.raises(RunnerError, match=r"limit 2 reached; active runner\(s\): tns-one, deb-one"):
        TCG_VM.validate_active_guest_count(active, 2)
    vm_lab.validate_active_guest_count(active, 3, GENERIC_PROFILE.max_concurrent_guests)


def test_each_known_profile_counts_against_the_others_cap_even_with_a_different_label_value(monkeypatch):
    other = dataclasses.replace(
        GENERIC_PROFILE,
        product="example",
        vm_label="org.vbpub.example.vm",
        state_label="org.vbpub.example.state-dir-sha256",
        label_value="enabled",
    )
    monkeypatch.setattr(vm_lab, "KNOWN_VM_LABELS", (*vm_lab.KNOWN_VM_LABELS, profile_pair(other)))
    ps_calls = fake_running_guests(monkeypatch, {
        profile_pair(TRUENAS): [("id-t", "tns-one")],
        profile_pair(GENERIC_PROFILE): [("id-g", "deb-one")],
        profile_pair(other): [("id-o", "ex-one")],
    })
    active = vm_lab.active_guest_names()
    assert active == ["tns-one", "deb-one", "ex-one"]
    assert "label=org.vbpub.example.vm=enabled" in [call[4] for call in ps_calls]
    assert "label=org.vbpub.example.vm=true" not in [call[4] for call in ps_calls]
    # The TrueNAS and generic starts see the example profile's guest, and the
    # example start sees theirs: the same count serves every profile.
    for caller_max in (TRUENAS.max_concurrent_guests, other.max_concurrent_guests):
        with pytest.raises(RunnerError, match=r"limit 3 reached; active runner\(s\): tns-one, deb-one, ex-one"):
            vm_lab.validate_active_guest_count(active, 3, caller_max)


def test_a_profile_missing_from_the_registry_is_refused_at_start(monkeypatch, tmp_path):
    vm_lab.require_registered_profile(TRUENAS)
    vm_lab.require_registered_profile(GENERIC_PROFILE)
    unknown = dataclasses.replace(GENERIC_PROFILE, product="example", vm_label="org.vbpub.example.vm")
    with pytest.raises(RunnerError, match="org.vbpub.example.vm=true is not in KNOWN_VM_LABELS"):
        vm_lab.require_registered_profile(unknown)
    same_key_other_value = dataclasses.replace(GENERIC_PROFILE, label_value="enabled")
    with pytest.raises(RunnerError, match="is not in KNOWN_VM_LABELS"):
        vm_lab.require_registered_profile(same_key_other_value)

    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path / "admission.lock")
    arguments = START_ARGS + ["--state-dir", str(tmp_path), *SIZING]
    args = vm_lab.build_parser(unknown).parse_args(arguments)
    with pytest.raises(RunnerError, match="is not in KNOWN_VM_LABELS"):
        vm_lab.cmd_start(unknown, args)
    monkeypatch.setattr(vm_lab, "KNOWN_VM_LABELS", (*vm_lab.KNOWN_VM_LABELS, profile_pair(unknown)))
    vm_lab.require_registered_profile(unknown)


def test_admission_with_no_running_guests_lists_none(monkeypatch):
    fake_running_guests(monkeypatch, {})
    assert vm_lab.active_guest_names() == []
    vm_lab.validate_active_guest_count([], 1, 3)


def test_the_concurrent_guest_limit_is_bounded_by_the_profile_maximum():
    with pytest.raises(RunnerError, match="between 1 and 3"):
        vm_lab.validate_max_concurrent_guests(4, GENERIC_PROFILE.max_concurrent_guests)
    with pytest.raises(RunnerError, match="between 1 and 3"):
        vm_lab.validate_max_concurrent_guests(0, GENERIC_PROFILE.max_concurrent_guests)
    assert vm_lab.build_parser(GENERIC_PROFILE).parse_args(
        START_ARGS + ["--state-dir", "s", *SIZING]
    ).max_concurrent_guests == 3


# --- verbs that act on one guest -------------------------------------------


def labelled_guest(profile: VmProfile, state_dir: Path, *, running: bool = True) -> dict:
    return {
        "Config": {"Labels": {
            profile.vm_label: profile.label_value,
            profile.state_label: vm_lab.state_identity(state_dir),
        }},
        "State": {"Running": running, "Status": "running" if running else "exited"},
    }


def record_qmp(monkeypatch) -> list[tuple[Path, str]]:
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(vm_lab, "qmp_command", lambda path, command, *_a, **_k: calls.append((path, command)))
    return calls


def test_console_reset_sends_system_reset_to_a_managed_running_guest(monkeypatch, tmp_path, capsys):
    guest = labelled_guest(GENERIC_PROFILE, tmp_path)
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: guest)
    calls = record_qmp(monkeypatch)
    args = SimpleNamespace(name="deb1", state_dir=str(tmp_path))
    assert vm_lab.cmd_console_reset(GENERIC_PROFILE, args) == 0
    assert calls == [(tmp_path.resolve() / "qmp.sock", "system_reset")]
    assert "[reset] deb1 received system_reset" in capsys.readouterr().out


def test_console_reset_is_also_available_through_the_truenas_front(monkeypatch, tmp_path):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(TRUENAS, tmp_path))
    calls = record_qmp(monkeypatch)
    assert TCG_VM.cmd_console_reset(SimpleNamespace(name="tns-a", state_dir=str(tmp_path))) == 0
    assert calls == [(tmp_path.resolve() / "qmp.sock", "system_reset")]
    args = TCG_VM.build_parser().parse_args(["console", "reset", "--name", "tns-a", "--state-dir", "x"])
    assert args.func is vm_lab.cmd_console_reset


def test_console_reset_refuses_a_container_without_the_profile_labels(monkeypatch, tmp_path):
    calls = record_qmp(monkeypatch)
    args = SimpleNamespace(name="deb1", state_dir=str(tmp_path))
    for labels in ({}, {TRUENAS.vm_label: "true", TRUENAS.state_label: vm_lab.state_identity(tmp_path)}):
        guest = {"Config": {"Labels": labels}, "State": {"Running": True}}
        monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name, guest=guest: guest)
        with pytest.raises(RunnerError, match="container 'deb1' is not a managed vm-lab TCG VM"):
            vm_lab.cmd_console_reset(GENERIC_PROFILE, args)
    assert calls == []


def test_console_reset_refuses_wrong_state_dir_and_stopped_guest(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    calls = record_qmp(monkeypatch)
    args = SimpleNamespace(name="deb1", state_dir=str(tmp_path))
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(GENERIC_PROFILE, other))
    with pytest.raises(RunnerError, match="does not belong to state directory"):
        vm_lab.cmd_console_reset(GENERIC_PROFILE, args)
    monkeypatch.setattr(
        vm_lab, "docker_inspect", lambda _name: labelled_guest(GENERIC_PROFILE, tmp_path, running=False)
    )
    with pytest.raises(RunnerError, match="container 'deb1' is not running"):
        vm_lab.cmd_console_reset(GENERIC_PROFILE, args)
    assert calls == []


@pytest.mark.parametrize("command", ["status", "stop", "terminate_paused", "destroy"])
def test_single_guest_verbs_refuse_the_other_profiles_guest(command, monkeypatch, tmp_path):
    docker_calls: list[list[str]] = []
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(TRUENAS, tmp_path))
    monkeypatch.setattr(vm_lab, "docker_run", lambda argv, **_k: docker_calls.append(argv))
    calls = record_qmp(monkeypatch)
    args = SimpleNamespace(name="tns-a", state_dir=str(tmp_path))
    handler = getattr(vm_lab, f"cmd_{command}")
    with pytest.raises(RunnerError, match="container 'tns-a' is not a managed vm-lab TCG VM"):
        handler(GENERIC_PROFILE, args)
    assert docker_calls == [] and calls == []


def test_truenas_keeps_its_own_refusal_message_for_a_generic_guest(monkeypatch, tmp_path):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(GENERIC_PROFILE, tmp_path))
    with pytest.raises(RunnerError, match="container 'deb1' is not a managed TrueNAS TCG VM"):
        TCG_VM.cmd_destroy(SimpleNamespace(name="deb1", state_dir=str(tmp_path)))


def test_destroy_removes_only_a_stopped_guest_of_its_own_profile(monkeypatch, tmp_path, capsys):
    docker_calls: list[list[str]] = []
    guests = iter([labelled_guest(GENERIC_PROFILE, tmp_path),
                   labelled_guest(GENERIC_PROFILE, tmp_path, running=False)])
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: next(guests))
    monkeypatch.setattr(vm_lab, "docker_run", lambda argv, **_k: docker_calls.append(argv))
    args = SimpleNamespace(name="deb1")
    with pytest.raises(RunnerError, match="refusing to destroy a running guest"):
        vm_lab.cmd_destroy(GENERIC_PROFILE, args)
    assert docker_calls == []
    assert vm_lab.cmd_destroy(GENERIC_PROFILE, args) == 0
    assert docker_calls == [["docker", "rm", "deb1"]]
    assert "[destroyed] removed stopped container deb1" in capsys.readouterr().out


# --- resume identity -------------------------------------------------------


def manifest_for(profile: VmProfile, tmp_path: Path, host_ports: dict[str, int]) -> dict:
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    for path, content in ((base, b"base"), (code, b"code"), (variables, b"vars")):
        if not path.exists():  # rewriting would change mtime and make manifests differ for the wrong reason
            path.write_bytes(content)
    return vm_lab.runner_manifest(
        profile,
        name="deb1",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="internal",
        cgroup_parent="dev-background.slice",
        host_ports=host_ports,
    )


def test_manifest_records_sizing_and_both_ports_of_every_cli_forward(tmp_path):
    manifest = manifest_for(sized_profile(memory_mib=2048, cpus=3), tmp_path, {"fwd1": 2222})
    assert manifest["guest_memory_mib"] == 2048
    assert manifest["memory_bytes"] == manifest["memory_swap_bytes"] == 2560 * 1024 * 1024
    assert manifest["cpu_nano_cpus"] == 3_000_000_000
    assert (manifest["fwd1_port"], manifest["fwd1_guest_port"]) == (2222, 22)
    assert "ssh_port" not in manifest and "https_port" not in manifest


def test_resume_refuses_a_changed_guest_port_memory_or_cpu_count(tmp_path):
    path = tmp_path / "runner.json"
    original = manifest_for(sized_profile(), tmp_path, {"fwd1": 2222})
    vm_lab.write_runner_manifest(path, original)
    vm_lab.check_runner_manifest(path, original)
    for changed in (
        manifest_for(sized_profile(forwards=("2222:2200",)), tmp_path, {"fwd1": 2222}),
        manifest_for(sized_profile(memory_mib=4096), tmp_path, {"fwd1": 2222}),
        manifest_for(sized_profile(cpus=4), tmp_path, {"fwd1": 2222}),
    ):
        with pytest.raises(RunnerError, match="resume identity differs"):
            vm_lab.check_runner_manifest(path, changed)


def test_truenas_manifest_keeps_its_historical_keys(tmp_path):
    manifest = manifest_for(TRUENAS, tmp_path, {"ssh": 2222, "https": 8443})
    assert (manifest["ssh_port"], manifest["https_port"]) == (2222, 8443)
    assert manifest["cpu_nano_cpus"] == 2_000_000_000
    assert manifest["memory_bytes"] == 3_758_096_384
    assert manifest["guest_memory_mib"] == 3072
    assert not any(key.endswith("_guest_port") for key in manifest)


def test_manifest_refuses_host_ports_that_do_not_match_the_profile(tmp_path):
    with pytest.raises(RunnerError, match="host ports must name exactly"):
        manifest_for(sized_profile(), tmp_path, {"ssh": 2222})


# --- the two profiles build separate scripts --------------------------------


def test_truenas_script_has_no_generic_text_and_generic_script_has_no_truenas_text():
    truenas_script = vm_lab.host_command_script(TRUENAS)
    generic_script = vm_lab.host_command_script(sized_profile(forwards=("2222:22", "8443:443")))
    generic_argv = create_argv(sized_profile(), {"fwd1": 2222}, host_media="/host/x.iso")

    assert "id=truenasdata${index}" in truenas_script
    assert "serial=TNEXTRA" in truenas_script
    assert "${VM_SSH_PORT}-:22,hostfwd=tcp:0.0.0.0:${VM_HTTPS_PORT}-:443" in truenas_script
    assert "-smp 2 " in truenas_script and "-m 3072 " in truenas_script
    for generic_only in ("vmlab", "VMLAB", "VM_FWD", "vm-lab"):
        assert generic_only not in truenas_script

    assert "vmlabdata" in generic_script and "VMLAB" in generic_script and "VM_FWD1_PORT" in generic_script
    haystacks = [generic_script, "\n".join(generic_argv)]
    for text in haystacks:
        lowered = text.lower()
        assert "truenas" not in lowered
        assert "tnextra" not in lowered
    assert "dst=/media/vm-lab-readonly.iso,readonly" in "\n".join(generic_argv)


def test_generic_help_is_product_neutral_and_truenas_help_is_unchanged(capsys):
    with pytest.raises(SystemExit):
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(["--help"])
    generic_help = capsys.readouterr().out
    assert "truenas" not in generic_help.lower()
    assert "tcg_vm" not in generic_help
    assert "state directory of the project that owns it" in generic_help
    with pytest.raises(SystemExit):
        TCG_VM.build_parser().parse_args(["--help"])
    truenas_help = capsys.readouterr().out
    assert "Run up to three bounded TrueNAS QEMU/TCG guests in the host background slice." in truenas_help


def test_truenas_docker_argv_is_built_from_its_profile_values():
    argv = create_argv(TRUENAS, {"ssh": 2222, "https": 8443}, host_media="/host/x.iso")
    assert option_values(argv, "--cpus") == ["2"]
    assert option_values(argv, "--memory") == ["3758096384"]
    assert option_values(argv, "--label")[0] == "org.vbpub.truenas.tcg-vm=true"
    environment = option_values(argv, "--env")
    assert environment[:3] == ["VM_NAME=deb1", "VM_SSH_PORT=2222", "VM_HTTPS_PORT=8443"]
    assert "VM_READONLY_MEDIA=/media/truenas-readonly.iso" in environment
