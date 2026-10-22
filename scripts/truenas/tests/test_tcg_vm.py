from pathlib import Path
from types import SimpleNamespace
import json
import os
import pty
import select
import socket
import subprocess
import threading
import time
import termios

import pytest

import tcg_vm


def test_cpu_quota_parsing_handles_limited_and_unlimited_values():
    assert tcg_vm.parse_cpu_max("200000 100000") == 2.0
    assert tcg_vm.parse_cpu_max("max 100000") is None


@pytest.mark.parametrize("value", ["", "200000", "0 100000", "100000 0"])
def test_cpu_quota_parsing_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        tcg_vm.parse_cpu_max(value)


def test_memory_limit_parsing_handles_finite_and_unlimited_values():
    assert tcg_vm.parse_memory_limit("5368709120\n") == 5_368_709_120
    assert tcg_vm.parse_memory_limit("max\n") is None


def test_memory_psi_parser_reads_some_and_full_avg10():
    raw = "some avg10=2.50 avg60=1.25 avg300=0.75 total=123\nfull avg10=0.25 avg60=0.10 avg300=0.05 total=10\n"
    assert tcg_vm.parse_memory_psi(raw) == (2.5, 0.25)


@pytest.mark.parametrize("raw", ["", "some avg10=NaN full avg10=0"])
def test_memory_psi_parser_rejects_missing_or_invalid_values(raw):
    with pytest.raises(ValueError, match="memory PSI"):
        tcg_vm.parse_memory_psi(raw)


def test_mount_translation_uses_longest_authoritative_mount():
    mounts = [
        {"Destination": "/workspaces", "Source": "/host/workspaces", "RW": True},
        {"Destination": "/workspaces/vbpub", "Source": "/host/vbpub", "RW": True},
    ]
    assert tcg_vm.mount_source_for_path(
        Path("/workspaces/vbpub/scripts/truenas/base.qcow2"), mounts, require_writable=False
    ) == "/host/vbpub/scripts/truenas/base.qcow2"


def test_mount_translation_refuses_read_only_state_mount():
    mounts = [{"Destination": "/state", "Source": "/host/state", "RW": False}]
    with pytest.raises(tcg_vm.RunnerError, match="writable Docker mount"):
        tcg_vm.mount_source_for_path(Path("/state"), mounts, require_writable=True)


def test_memory_preflight_checks_selected_slice_and_ancestors():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 5 * 1024**3,
            "memory_max": 8 * 1024**3,
            "memory_current": 1 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
        {
            "path": "/dev.slice",
            "cpu_max": 7.0,
            "memory_high": 11 * 1024**3,
            "memory_max": 15 * 1024**3,
            "memory_current": 6 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
    ]
    tcg_vm.validate_memory_headroom(levels)


def test_memory_preflight_allows_vm_caps_to_overcommit_high_and_max_when_psi_is_low():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 3 * 1024**3,
            "memory_max": 5 * 1024**3,
            "memory_current": 2 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
        {
            "path": "/dev.slice",
            "cpu_max": 8.0,
            "memory_high": 10_000_000_000,
            "memory_max": 12_000_000_000,
            "memory_current": 11_000_000_000,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
    ]

    tcg_vm.validate_memory_headroom(levels)
    assert levels[1]["memory_current"] + tcg_vm.VM_MEMORY_BYTES > levels[1]["memory_max"]


def test_preflight_reports_high_advisory_and_hard_max_projections_separately(capsys):
    levels = [{
        "path": "/dev.slice/dev-background.slice",
        "cpu_max": 5.0,
        "memory_high": 7 * 1024**3,
        "memory_max": 10 * 1024**3,
        "memory_current": 4 * 1024**3,
        "memory_psi_some_avg10": 0.0,
        "memory_psi_full_avg10": 0.0,
    }]

    tcg_vm.print_preflight("dev-background.slice", levels, "/dev.slice/dev-background.slice")
    output = capsys.readouterr().out

    assert "cap-exceeds-high-advisory=True" in output
    assert "cap-exceeds-hard-max=False" in output


def test_memory_preflight_refuses_when_actual_current_has_reached_hard_max():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 10 * 1024**3,
            "memory_max": 12 * 1024**3,
            "memory_current": 2 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
        {
            "path": "/dev.slice",
            "cpu_max": 8.0,
            "memory_high": 10_000_000_000,
            "memory_max": 11_000_000_000,
            "memory_current": 11_000_000_000,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        },
    ]

    with pytest.raises(tcg_vm.RunnerError, match="reached hard memory.max"):
        tcg_vm.validate_memory_headroom(levels)


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_guest_admission_accepts_configured_limits_up_to_three(limit):
    tcg_vm.validate_max_concurrent_guests(limit)
    tcg_vm.validate_active_guest_count([f"guest-{n}" for n in range(limit - 1)], limit)


@pytest.mark.parametrize("limit", [0, 4, True, "3"])
def test_guest_admission_rejects_invalid_configured_limit(limit):
    with pytest.raises(tcg_vm.RunnerError, match="between 1 and 3"):
        tcg_vm.validate_max_concurrent_guests(limit)


def test_guest_admission_refuses_a_fourth_managed_guest():
    active = ["guest-a", "guest-b", "guest-c"]
    with pytest.raises(tcg_vm.RunnerError, match="limit 3 reached.*guest-a.*guest-c"):
        tcg_vm.validate_active_guest_count(active, 3)


def test_vm_start_admission_lock_serializes_start_checks(monkeypatch, tmp_path):
    lock_path = tmp_path / "admission.lock"
    monkeypatch.setattr(tcg_vm, "vm_start_lock_path", lambda: lock_path)

    with tcg_vm.vm_start_admission_lock():
        with pytest.raises(tcg_vm.RunnerError, match="another tcg_vm.py start is in progress"):
            with tcg_vm.vm_start_admission_lock():
                pass
    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_start_parser_defaults_to_three_and_can_lower_the_limit():
    required = [
        "start", "--name", "guest", "--base-disk", "disk.qcow2",
        "--ovmf-code", "OVMF_CODE_4M.fd", "--ovmf-vars", "OVMF_VARS_4M.fd",
        "--state-dir", ".vm/guest",
    ]
    assert tcg_vm.build_parser().parse_args(required).max_concurrent_guests == 3
    assert tcg_vm.build_parser().parse_args(required + ["--max-concurrent-guests", "2"]).max_concurrent_guests == 2
    assert tcg_vm.build_parser().parse_args(required).memory_psi_full_avg10_limit == 1.0
    assert (
        tcg_vm.build_parser()
        .parse_args(required + ["--memory-psi-full-avg10-limit", "2"])
        .memory_psi_full_avg10_limit
        == 2.0
    )
    assert tcg_vm.build_parser().parse_args(required).vnc_console is False
    assert tcg_vm.build_parser().parse_args(required + ["--vnc-console"]).vnc_console is True
    assert tcg_vm.build_parser().parse_args(required).serial_console is False
    assert tcg_vm.build_parser().parse_args(required + ["--serial-console"]).serial_console is True
    extra_disks = tcg_vm.build_parser().parse_args(
        required + ["--data-disk-size", "1G", "--data-disk-size", "512M"]
    )
    assert extra_disks.data_disk_size == ["1G", "512M"]
    assert tcg_vm.build_parser().parse_args(required).data_disk_size == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 1), ("512K", 512 * 1024), ("128m", 128 * 1024**2),
     ("2G", 2 * 1024**3), ("1T", 1024**4)],
)
def test_extra_disk_size_accepts_positive_bytes_and_binary_suffixes(value, expected):
    assert tcg_vm.parse_extra_disk_size(value) == expected


@pytest.mark.parametrize("value", ["", "0", "0G", "-1G", "1.5G", "1GB", "1 G", "999999999999999999999T"])
def test_extra_disk_size_refuses_ambiguous_or_oversized_values(value):
    with pytest.raises(tcg_vm.RunnerError, match="extra disk size"):
        tcg_vm.parse_extra_disk_size(value)


def test_extra_disk_size_count_is_bounded():
    with pytest.raises(tcg_vm.RunnerError, match=f"at most {tcg_vm.MAX_EXTRA_DISKS}"):
        tcg_vm.extra_disk_sizes_bytes(["1G"] * (tcg_vm.MAX_EXTRA_DISKS + 1))


def test_extra_disks_are_recorded_in_resume_identity_and_size_changes_refuse(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    base.write_bytes(b"base disk identity")
    code.write_bytes(b"firmware code")
    variables.write_bytes(b"fresh variables")
    manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
        extra_disk_sizes=[1024**3, 2 * 1024**3],
    )
    path = tmp_path / "runner.json"
    tcg_vm.write_runner_manifest(path, manifest)
    tcg_vm.check_runner_manifest(path, manifest)
    assert manifest["extra_disk_sizes_bytes"] == [1024**3, 2 * 1024**3]

    changed = dict(manifest, extra_disk_sizes_bytes=[1024**3])
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(path, changed)


def test_guest_command_creates_sparse_extra_disks_and_refuses_wrong_resume_size(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "OVMF_CODE_4M.fd").write_bytes(b"code")
    (state_dir / "OVMF_VARS_4M.fd").write_bytes(b"vars")
    base_disk = tmp_path / "base.qcow2"
    base_disk.write_bytes(b"base")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "qemu-args"
    qemu_img_args = tmp_path / "qemu-img-args"
    qemu = fake_bin / "qemu-system-x86_64"
    qemu.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$ARGS_FILE\"\n"
    )
    qemu.chmod(0o755)
    qemu_img = fake_bin / "qemu-img"
    qemu_img.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" >> \"$QEMU_IMG_ARGS\"\n"
        "if [ \"$1\" = create ]; then\n"
        "  if [ \"$7\" = -b ]; then touch \"$9\"; else touch \"$7\"; fi\n"
        "elif [ \"$1\" = info ]; then\n"
        "  case \"$3\" in\n"
        "    *data-disk-1*) printf '{\"virtual-size\":1073741824}\\n' ;;\n"
        "    *data-disk-2*) printf '{\"virtual-size\":2147483648}\\n' ;;\n"
        "    *) exit 2 ;;\n"
        "  esac\n"
        "else exit 2; fi\n"
    )
    qemu_img.chmod(0o755)
    command = tcg_vm.host_command_script().replace("/state", str(state_dir)).replace(
        "/base/disk.qcow2", str(base_disk)
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "ARGS_FILE": str(args_file),
        "QEMU_IMG_ARGS": str(qemu_img_args),
        "VM_MODE": "create",
        "VM_NAME": "tns-extra-disk-test",
        "VM_SSH_PORT": "2222",
        "VM_HTTPS_PORT": "8443",
        "VM_EXTRA_DISK_SIZES_BYTES": "1073741824,2147483648",
        "VM_READONLY_MEDIA": "",
        "VM_VNC_CONSOLE": "0",
        "VM_SERIAL_CONSOLE": "0",
    }
    result = subprocess.run(["/bin/sh", "-c", command], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (state_dir / "data-disk-1.qcow2").is_file()
    assert (state_dir / "data-disk-2.qcow2").is_file()
    qemu_args = args_file.read_text().splitlines()
    first_disk_drive = (
        f"file={state_dir / 'data-disk-1.qcow2'},if=none,id=truenasdata1,"
        "format=qcow2,cache=writeback"
    )
    assert first_disk_drive in qemu_args
    assert "virtio-blk-pci,drive=truenasdata1,serial=TNEXTRA001,addr=0x8" in qemu_args
    second_disk_drive = (
        f"file={state_dir / 'data-disk-2.qcow2'},if=none,id=truenasdata2,"
        "format=qcow2,cache=writeback"
    )
    assert second_disk_drive in qemu_args
    assert "virtio-blk-pci,drive=truenasdata2,serial=TNEXTRA002,addr=0x9" in qemu_args
    boot_drive = f"file={state_dir / 'disk-overlay.qcow2'},if=virtio,format=qcow2,cache=writeback"
    assert qemu_args.index(boot_drive) < qemu_args.index(first_disk_drive)
    assert "-o" in qemu_img_args.read_text().splitlines()
    assert "preallocation=off" in qemu_img_args.read_text().splitlines()
    assert "preallocation=off" in tcg_vm.host_command_script()

    args_file.unlink()
    (state_dir / "disk-overlay.qcow2").write_bytes(b"overlay")
    environment["VM_MODE"] = "resume"
    environment["VM_EXTRA_DISK_SIZES_BYTES"] = "2147483648"
    result = subprocess.run(
        ["/bin/sh", "-c", command], env=environment, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert not args_file.exists()


def test_guest_limit_and_memory_policy_are_documented_across_user_guides():
    project = Path(tcg_vm.__file__).resolve().parent
    readme = (project / "README.md").read_text()
    design = (project / "docs" / "DESIGN-GUIDE.md").read_text()
    consumers = (project / "docs" / "CONSUMERS.md").read_text()

    assert "up to three disposable TrueNAS guests" in readme
    assert "docs/CONSUMERS.md#run-a-local-truenas-tcg-guest" in readme
    assert "docs/DESIGN-GUIDE.md#isolated-local-qemu-guests" in readme
    assert "--max-concurrent-guests 3" in consumers
    assert "--memory-psi-full-avg10-limit" in consumers
    assert "`--max-concurrent-guests 1` or `2`" in consumers
    assert "memory PSI" in consumers and "advisory" in consumers
    assert "memory.pressure" in design and "advisory" in design
    assert "nominal cap overcommit" in design
    assert "Starts share" in design and "advisory lock" in design
    assert "--vnc-console" in consumers
    assert "--serial-console" in readme and "--serial-console" in design and "--serial-console" in consumers
    assert "console serial" in readme and "console serial" in consumers
    assert "Unix socket only" in readme
    assert "-vnc unix:/state/vnc.sock" in design
    assert "two-way Unix socket chardev" in design and "serial.sock" in design
    assert "rebind-media" in readme and "rebind-media" in design and "rebind-media" in consumers
    assert "--data-disk-size" in readme and "--data-disk-size" in consumers
    assert "#sparse-additional-data-disks" in readme and "#sparse-additional-data-disks" in consumers


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("memory_psi_some_avg10", tcg_vm.MAX_MEMORY_PSI_SOME_AVG10, "memory PSI some"),
        ("memory_psi_full_avg10", tcg_vm.MAX_MEMORY_PSI_FULL_AVG10, "memory PSI full"),
    ],
)
def test_memory_preflight_refuses_high_recent_pressure(key, value, message):
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 5 * 1024**3,
            "memory_max": 8 * 1024**3,
            "memory_current": 2 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        }
    ]
    levels[0][key] = value
    with pytest.raises(tcg_vm.RunnerError, match=message):
        tcg_vm.validate_memory_headroom(levels)


def test_memory_preflight_accepts_explicit_full_pressure_limit_without_disabling_other_guards():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 5 * 1024**3,
            "memory_max": 8 * 1024**3,
            "memory_current": 2 * 1024**3,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 1.45,
        }
    ]
    tcg_vm.validate_memory_headroom(levels, max_memory_psi_full_avg10=2.0)
    with pytest.raises(tcg_vm.RunnerError, match="1.45% admission limit"):
        tcg_vm.validate_memory_headroom(levels, max_memory_psi_full_avg10=1.45)


def test_memory_preflight_explicit_psi_override_keeps_hard_memory_and_cpu_guards():
    levels = [{
        "path": "/dev.slice/dev-gates.slice",
        "cpu_max": 1.0,
        "memory_high": 5 * 1024**3,
        "memory_max": 8 * 1024**3,
        "memory_current": 2 * 1024**3,
        "memory_psi_some_avg10": tcg_vm.MAX_MEMORY_PSI_SOME_AVG10,
        "memory_psi_full_avg10": tcg_vm.MAX_MEMORY_PSI_FULL_AVG10,
    }]
    tcg_vm.validate_memory_headroom(
        levels, requested_cpus=0.5, allow_memory_psi_pressure=True,
    )

    levels[0]["memory_current"] = levels[0]["memory_max"]
    with pytest.raises(tcg_vm.RunnerError, match="reached hard memory.max"):
        tcg_vm.validate_memory_headroom(
            levels, requested_cpus=0.5, allow_memory_psi_pressure=True,
        )

    levels[0]["memory_current"] = 2 * 1024**3
    levels[0]["cpu_max"] = 0.25
    with pytest.raises(tcg_vm.RunnerError, match="quota"):
        tcg_vm.validate_memory_headroom(
            levels, requested_cpus=0.5, allow_memory_psi_pressure=True,
        )

    levels[0]["cpu_max"] = 1.0
    levels[0].pop("memory_psi_some_avg10")
    with pytest.raises(tcg_vm.RunnerError, match="cannot read memory PSI"):
        tcg_vm.validate_memory_headroom(
            levels, requested_cpus=0.5, allow_memory_psi_pressure=True,
        )


def test_memory_preflight_refuses_when_selected_slice_has_no_finite_memory_max():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 5.0,
            "memory_high": 5 * 1024**3,
            "memory_max": None,
            "memory_current": 0,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        }
    ]
    with pytest.raises(tcg_vm.RunnerError, match="no finite memory.max"):
        tcg_vm.validate_memory_headroom(levels)


def test_memory_preflight_refuses_when_memory_psi_is_unavailable():
    levels = [{
        "path": "/dev.slice/dev-background.slice",
        "cpu_max": 5.0,
        "memory_high": 5 * 1024**3,
        "memory_max": 8 * 1024**3,
        "memory_current": 1 * 1024**3,
    }]
    with pytest.raises(tcg_vm.RunnerError, match="cannot read memory PSI"):
        tcg_vm.validate_memory_headroom(levels)


def test_memory_preflight_refuses_cpu_budget_above_slice_limit():
    levels = [
        {
            "path": "/dev.slice/dev-background.slice",
            "cpu_max": 1.5,
            "memory_high": 5 * 1024**3,
            "memory_max": 8 * 1024**3,
            "memory_current": 0,
            "memory_psi_some_avg10": 0.0,
            "memory_psi_full_avg10": 0.0,
        }
    ]
    with pytest.raises(tcg_vm.RunnerError, match="requires 2 CPUs"):
        tcg_vm.validate_memory_headroom(levels)


def test_qmp_command_aliases_long_state_paths_through_short_unix_socket(tmp_path):
    server_path = tmp_path / "server.sock"
    long_path = tmp_path / ("s" * 80) / "qmp.sock"
    long_path.parent.mkdir()
    os.symlink(server_path, long_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(server_path))
    server.listen(1)
    errors = []

    def serve_qmp():
        try:
            connection, _ = server.accept()
            with connection:
                stream = connection.makefile("rwb", buffering=0)
                stream.write(b'{"QMP":{}}\r\n')
                assert json.loads(stream.readline())["execute"] == "qmp_capabilities"
                stream.write(b'{"return":{}}\r\n')
                assert json.loads(stream.readline())["execute"] == "query-status"
                stream.write(b'{"return":{"status":"running","running":true}}\r\n')
        except Exception as exc:  # transfer worker failures back to the test thread
            errors.append(exc)

    worker = threading.Thread(target=serve_qmp)
    worker.start()
    try:
        result = tcg_vm.qmp_command(long_path, "query-status")
    finally:
        server.close()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    assert result == {"status": "running", "running": True}


def test_qmp_human_monitor_command_sends_command_line_argument(tmp_path):
    socket_path = tmp_path / "qmp.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    errors = []

    def serve_qmp():
        try:
            connection, _ = server.accept()
            with connection:
                stream = connection.makefile("rwb", buffering=0)
                stream.write(b'{"QMP":{}}\r\n')
                assert json.loads(stream.readline())["execute"] == "qmp_capabilities"
                stream.write(b'{"return":{}}\r\n')
                request = json.loads(stream.readline())
                assert request["execute"] == "human-monitor-command"
                assert request["arguments"] == {"command-line": "sendkey ret"}
                stream.write(b'{"return":"key queued"}\r\n')
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve_qmp)
    worker.start()
    try:
        result = tcg_vm.qmp_human_monitor_command(socket_path, "sendkey ret")
    finally:
        server.close()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    assert result == "key queued"


def test_prepare_vnc_socket_removes_stale_socket_but_refuses_live_or_regular_path(tmp_path):
    stale_path = tmp_path / "stale.sock"
    stale_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale_socket.bind(str(stale_path))
    stale_socket.close()
    tcg_vm.prepare_vnc_socket(stale_path)
    assert not stale_path.exists()

    live_path = tmp_path / "live.sock"
    live_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    live_socket.bind(str(live_path))
    live_socket.listen(1)
    try:
        with pytest.raises(tcg_vm.RunnerError, match="already accepts connections"):
            tcg_vm.prepare_vnc_socket(live_path)
    finally:
        live_socket.close()
    assert live_path.exists()

    regular_path = tmp_path / "regular"
    regular_path.write_text("keep")
    with pytest.raises(tcg_vm.RunnerError, match="non-socket VNC path"):
        tcg_vm.prepare_vnc_socket(regular_path)
    assert regular_path.read_text() == "keep"


def test_vnc_socket_client_path_uses_a_short_alias_when_needed(tmp_path):
    long_state_dir = tmp_path / ("state-" + "x" * 100)
    long_state_dir.mkdir()
    alias = tcg_vm.vnc_socket_client_path(long_state_dir)
    assert len(os.fsencode(alias)) <= tcg_vm.QMP_SOCKET_PATH_MAX_BYTES
    assert alias.is_symlink()
    assert os.readlink(alias) == str(long_state_dir.resolve() / "vnc.sock")
    alias.unlink()


def test_serial_socket_preparation_and_client_alias_are_fail_closed(tmp_path):
    stale_path = tmp_path / "stale.sock"
    stale_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale_socket.bind(str(stale_path))
    stale_socket.close()
    tcg_vm.prepare_serial_socket(stale_path)
    assert not stale_path.exists()

    regular_path = tmp_path / "regular"
    regular_path.write_text("keep")
    with pytest.raises(tcg_vm.RunnerError, match="non-socket serial path"):
        tcg_vm.prepare_serial_socket(regular_path)
    assert regular_path.read_text() == "keep"

    long_state_dir = tmp_path / ("serial-state-" + "y" * 100)
    long_state_dir.mkdir()
    alias = tcg_vm.serial_socket_client_path(long_state_dir)
    assert len(os.fsencode(alias)) <= tcg_vm.QMP_SOCKET_PATH_MAX_BYTES
    assert alias.is_symlink()
    assert os.readlink(alias) == str(long_state_dir.resolve() / "serial.sock")
    alias.unlink()


def test_console_commands_only_target_a_running_managed_guest(monkeypatch, tmp_path):
    state_dir = tmp_path / "guest-state"
    state_dir.mkdir()
    identity = tcg_vm.state_identity(state_dir)
    monkeypatch.setattr(
        tcg_vm,
        "docker_inspect",
        lambda _name: {
            "Config": {"Labels": {tcg_vm.VM_LABEL: tcg_vm.VM_LABEL_VALUE, tcg_vm.STATE_LABEL: identity}},
            "State": {"Running": True},
        },
    )
    hmp_calls = []

    def fake_hmp(_socket_path, command_line, _timeout=3.0):
        hmp_calls.append(command_line)
        if command_line.startswith("screendump "):
            (state_dir / command_line.rsplit("/", 1)[1]).write_bytes(b"ppm")
        return "ok"

    monkeypatch.setattr(tcg_vm, "qmp_human_monitor_command", fake_hmp)
    args = SimpleNamespace(name="guest", state_dir=str(state_dir), filename="installer.ppm")
    assert tcg_vm.cmd_console_screendump(args) == 0
    assert (state_dir / "installer.ppm").read_bytes() == b"ppm"
    assert hmp_calls == ["screendump /state/installer.ppm"]

    key_args = SimpleNamespace(name="guest", state_dir=str(state_dir), key="ctrl-alt-delete")
    assert tcg_vm.cmd_console_sendkey(key_args) == 0
    assert hmp_calls[-1] == "sendkey ctrl-alt-delete"

    unsafe_args = SimpleNamespace(name="guest", state_dir=str(state_dir), key="ret;quit")
    with pytest.raises(tcg_vm.RunnerError, match="QEMU sendkey"):
        tcg_vm.cmd_console_sendkey(unsafe_args)

    dump_args = SimpleNamespace(name="guest", state_dir=str(state_dir), filename="../outside.ppm")
    with pytest.raises(tcg_vm.RunnerError, match="simple filename"):
        tcg_vm.cmd_console_screendump(dump_args)


def test_serial_console_relays_input_and_output_and_restores_terminal(monkeypatch):
    master_fd, slave_fd = pty.openpty()
    original_terminal = termios.tcgetattr(slave_fd)
    serial_client, serial_server = socket.socketpair()
    stdin = os.fdopen(os.dup(slave_fd), "rb", buffering=0)
    stdout = os.fdopen(os.dup(slave_fd), "wb", buffering=0)
    worker_errors = []
    worker = None

    def relay():
        try:
            tcg_vm.relay_serial_console(serial_client)
        except Exception as exc:  # surfaced below so thread failures cannot be lost
            worker_errors.append(exc)

    try:
        monkeypatch.setattr(tcg_vm.sys, "stdin", stdin)
        monkeypatch.setattr(tcg_vm.sys, "stdout", stdout)
        worker = threading.Thread(target=relay)
        worker.start()
        deadline = time.monotonic() + 2
        while termios.tcgetattr(slave_fd)[3] & termios.ICANON:
            if time.monotonic() >= deadline:
                raise AssertionError("serial relay did not put the terminal in raw mode")
            time.sleep(0.01)

        os.write(master_fd, b"115200\r")
        serial_server.settimeout(2)
        received = bytearray()
        while len(received) < len(b"115200\r"):
            received.extend(serial_server.recv(32))
        assert bytes(received) == b"115200\r"

        serial_server.sendall(b"TrueNAS serial menu\r\n")
        expected_output = b"TrueNAS serial menu\r\n"
        output = bytearray()
        deadline = time.monotonic() + 2
        while len(output) < len(expected_output) and time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], max(0, deadline - time.monotonic()))
            assert ready, "guest serial output did not reach the terminal"
            output.extend(os.read(master_fd, 64))
        assert bytes(output) == expected_output

        os.write(master_fd, b"\x1d")
        worker.join(timeout=2)
        assert not worker.is_alive(), "Ctrl-] did not disconnect the serial console"
        assert worker_errors == []
        assert termios.tcgetattr(slave_fd) == original_terminal
    finally:
        if worker is not None and worker.is_alive():
            serial_client.shutdown(socket.SHUT_RDWR)
            worker.join(timeout=2)
        stdin.close()
        stdout.close()
        serial_client.close()
        serial_server.close()
        os.close(master_fd)
        os.close(slave_fd)


def test_cgroup_levels_allow_root_without_controller_files(monkeypatch):
    values = {
        ("/dev.slice/dev-background.slice", "cpu.max"): "500000 100000",
        ("/dev.slice/dev-background.slice", "memory.high"): str(5 * 1024**3),
        ("/dev.slice/dev-background.slice", "memory.max"): str(8 * 1024**3),
        ("/dev.slice/dev-background.slice", "memory.current"): str(1 * 1024**3),
        ("/dev.slice/dev-background.slice", "memory.pressure"):
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
        ("/", "memory.current"): str(6 * 1024**3),
    }
    monkeypatch.setattr(tcg_vm, "cgroup_file", lambda _inspector, path, name: values.get((path, name)))
    levels = tcg_vm.cgroup_levels("inspector", "/dev.slice/dev-background.slice")
    assert levels[0]["cpu_max"] == 5.0
    assert levels[0]["memory_high"] == 5 * 1024**3
    assert levels[0]["memory_psi_some_avg10"] == 0.0
    assert levels[0]["memory_psi_full_avg10"] == 0.0
    assert levels[-1]["cpu_max"] is None
    assert levels[-1]["memory_high"] is None
    tcg_vm.validate_memory_headroom(levels)


def test_slice_name_requires_explicit_systemd_slice():
    tcg_vm.validate_slice_name("dev-background.slice")
    with pytest.raises(tcg_vm.RunnerError, match="systemd .slice"):
        tcg_vm.validate_slice_name("unbounded")


def test_host_proc_cgroup_path_is_normalized_from_namespace_relative_form():
    raw = "0::/../../dev.slice/dev-background.slice/docker-abc.scope\n"
    assert tcg_vm.parse_host_cgroup(raw) == "/dev.slice/dev-background.slice/docker-abc.scope"


def test_host_proc_cgroup_requires_one_unified_entry():
    with pytest.raises(tcg_vm.RunnerError, match="unique unified"):
        tcg_vm.parse_host_cgroup("1:cpu:/docker/x\n2:memory:/docker/x\n")


def test_failed_start_cleanup_uses_qmp_then_removes_only_stopped_container(monkeypatch, tmp_path):
    (tmp_path / "qmp.sock").touch()
    docker_calls = []
    qmp_calls = []
    inspect_responses = [
        json.dumps({"State": {"Running": True}}),
        json.dumps({"Running": False}),
    ]

    def fake_docker_run(argv, **_kwargs):
        docker_calls.append(argv)
        if argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=inspect_responses.pop(0), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tcg_vm, "docker_run", fake_docker_run)
    monkeypatch.setattr(tcg_vm, "qmp_command", lambda _path, command: qmp_calls.append(command))
    result = tcg_vm.cleanup_failed_start("tns-test", tmp_path, timeout=1)
    assert "shut down gracefully" in result
    assert qmp_calls == ["system_powerdown"]
    assert docker_calls[-1] == ["docker", "rm", "tns-test"]
    assert all("-f" not in call for call in docker_calls)


def test_failed_start_cleanup_leaves_running_container_without_qmp(monkeypatch, tmp_path):
    docker_calls = []

    def fake_docker_run(argv, **_kwargs):
        docker_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"State": {"Running": True}}), stderr="")

    monkeypatch.setattr(tcg_vm, "docker_run", fake_docker_run)
    result = tcg_vm.cleanup_failed_start("tns-test", tmp_path, timeout=1)
    assert "remains running" in result
    assert len(docker_calls) == 1


def test_terminate_paused_requires_paused_managed_guest_and_preserves_state(monkeypatch, tmp_path, capsys):
    name = "tns-paused"
    identity = tcg_vm.state_identity(tmp_path)
    managed = {
        "Config": {"Labels": {tcg_vm.VM_LABEL: tcg_vm.VM_LABEL_VALUE, tcg_vm.STATE_LABEL: identity}},
        "State": {"Running": True, "Status": "running"},
    }
    stopped = {**managed, "State": {"Running": False, "Status": "exited"}}
    inspect_responses = [managed, stopped]
    docker_calls = []

    def fake_docker_run(argv, **_kwargs):
        docker_calls.append(argv)
        if argv[1] == "inspect":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(inspect_responses.pop(0)), stderr="",
            )
        return SimpleNamespace(returncode=0, stdout=name, stderr="")

    monkeypatch.setattr(tcg_vm, "docker_run", fake_docker_run)
    qmp_calls = []
    monkeypatch.setattr(
        tcg_vm,
        "qmp_command",
        lambda _path, command: qmp_calls.append(command) or {"running": False, "status": "paused"},
    )
    monkeypatch.setattr(tcg_vm.time, "sleep", lambda _seconds: None)
    args = SimpleNamespace(name=name, state_dir=str(tmp_path))

    assert tcg_vm.cmd_terminate_paused(args) == 0
    assert qmp_calls == ["query-status"]
    assert docker_calls == [["docker", "inspect", name, "--format", "{{json .}}"],
                            ["docker", "kill", name],
                            ["docker", "inspect", name, "--format", "{{json .}}"]]
    assert "state remains at" in capsys.readouterr().out


def test_terminate_paused_refuses_running_qmp_guest(monkeypatch, tmp_path):
    identity = tcg_vm.state_identity(tmp_path)
    item = {
        "Config": {"Labels": {tcg_vm.VM_LABEL: tcg_vm.VM_LABEL_VALUE, tcg_vm.STATE_LABEL: identity}},
        "State": {"Running": True, "Status": "running"},
    }
    docker_calls = []
    monkeypatch.setattr(tcg_vm, "docker_inspect", lambda _name: item)
    monkeypatch.setattr(tcg_vm, "docker_run", lambda argv, **_kwargs: docker_calls.append(argv))
    monkeypatch.setattr(
        tcg_vm, "qmp_command", lambda _path, _command: {"running": True, "status": "running"}
    )
    args = SimpleNamespace(name="tns-paused", state_dir=str(tmp_path))

    with pytest.raises(tcg_vm.RunnerError, match="only for a managed guest QEMU reports as paused"):
        tcg_vm.cmd_terminate_paused(args)
    assert docker_calls == []


def test_guest_command_is_tcg_with_fixed_bounded_guest_memory():
    command = tcg_vm.host_command_script()
    assert "-machine q35,accel=tcg" in command
    assert "-cpu max,-vmx,-svm" in command
    assert "-m 3072" in command
    assert "/state/OVMF_CODE_4M.fd" in command
    assert "/state/OVMF_VARS_4M.fd" in command
    assert "VM_READONLY_MEDIA" in command
    assert 'set -- "$@" -cdrom "$VM_READONLY_MEDIA"' in command
    assert '"${VM_VNC_CONSOLE:-0}" = "1"' in command
    assert '"${VM_SERIAL_CONSOLE:-0}" = "1"' in command
    assert "-vnc unix:/state/vnc.sock" in command
    assert "-chardev socket,id=serial0,path=/state/serial.sock" in command
    assert "logfile=/state/serial.log,logappend=on" in command
    assert "-serial chardev:serial0" in command
    assert 'set -- "$@" -display none' in command
    assert command.count('  "$@" \\\n') == 1
    assert "-accel kvm" not in command
    assert "-m 8192" not in command


@pytest.mark.parametrize(
    ("vnc_console", "serial_console"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_guest_command_selects_optional_local_consoles_only_when_requested(
    tmp_path, vnc_console, serial_console
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "OVMF_CODE_4M.fd").write_bytes(b"code")
    (state_dir / "OVMF_VARS_4M.fd").write_bytes(b"vars")
    base_disk = tmp_path / "base.qcow2"
    base_disk.write_bytes(b"base")
    media = tmp_path / "installer.iso"
    media.write_bytes(b"iso")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "qemu-args"
    qemu = fake_bin / "qemu-system-x86_64"
    qemu.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGS_FILE\"\n")
    qemu.chmod(0o755)
    qemu_img = fake_bin / "qemu-img"
    qemu_img.write_text("#!/bin/sh\nexit 0\n")
    qemu_img.chmod(0o755)
    command = tcg_vm.host_command_script().replace("/state", str(state_dir)).replace(
        "/base/disk.qcow2", str(base_disk)
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "ARGS_FILE": str(args_file),
        "VM_MODE": "create",
        "VM_NAME": "tns-console-test",
        "VM_SSH_PORT": "2222",
        "VM_HTTPS_PORT": "8443",
        "VM_READONLY_MEDIA": str(media),
        "VM_VNC_CONSOLE": "1" if vnc_console else "0",
        "VM_SERIAL_CONSOLE": "1" if serial_console else "0",
    }
    result = subprocess.run(
        ["/bin/sh", "-c", command], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    qemu_args = args_file.read_text().splitlines()
    assert qemu_args.count("-cdrom") == 1
    assert qemu_args.count(str(media)) == 1
    if vnc_console:
        assert qemu_args.count("-vnc") == 1
        assert qemu_args.count(f"unix:{state_dir}/vnc.sock") == 1
        assert "-display" not in qemu_args
    else:
        assert qemu_args.count("-display") == 1
        assert qemu_args[qemu_args.index("-display") + 1] == "none"
        assert "-vnc" not in qemu_args
    if serial_console:
        assert qemu_args.count("-chardev") == 1
        assert qemu_args.count(
            f"socket,id=serial0,path={state_dir}/serial.sock,server=on,wait=off,"
            f"logfile={state_dir}/serial.log,logappend=on"
        ) == 1
        assert qemu_args.count("-serial") == 1
        assert qemu_args[qemu_args.index("-serial") + 1] == "chardev:serial0"
    else:
        assert "-chardev" not in qemu_args
        assert qemu_args.count("-serial") == 1
        assert qemu_args[qemu_args.index("-serial") + 1] == f"file:{state_dir}/serial.log"


def test_resume_manifest_binds_base_disk_image_network_and_firmware(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    base.write_bytes(b"base disk identity")
    code.write_bytes(b"firmware code")
    variables.write_bytes(b"fresh variables")
    manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
    )
    path = tmp_path / "runner.json"
    tcg_vm.write_runner_manifest(path, manifest)
    tcg_vm.check_runner_manifest(path, manifest)
    assert manifest["firmware_code"]["sha256"] == tcg_vm.file_identity(code)["sha256"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_resume_manifest_allows_only_ovmf_device_number_change(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    base.write_bytes(b"base disk identity")
    code.write_bytes(b"firmware code")
    variables.write_bytes(b"fresh variables")
    manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
    )
    path = tmp_path / "runner.json"
    changed_device = json.loads(json.dumps(manifest))
    changed_device["firmware_code"]["device"] += 1
    changed_device["firmware_vars_template"]["device"] += 1
    path.write_text(json.dumps(changed_device))
    tcg_vm.check_runner_manifest(path, manifest)

    changed_device["firmware_code"]["sha256"] = "0" * 64
    path.write_text(json.dumps(changed_device))
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(path, manifest)

    changed_device["firmware_code"]["sha256"] = manifest["firmware_code"]["sha256"]
    changed_device["firmware_vars_template"]["path"] = "/other/OVMF_VARS_4M.fd"
    path.write_text(json.dumps(changed_device))
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(path, manifest)


def test_resume_manifest_records_readonly_media_identity(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    media = tmp_path / "installer.iso"
    base.write_bytes(b"base disk identity")
    code.write_bytes(b"firmware code")
    variables.write_bytes(b"fresh variables")
    media.write_bytes(b"read-only image")
    manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
        readonly_media=media,
    )
    assert manifest["readonly_media"]["sha256"] == tcg_vm.file_identity(media)["sha256"]


def test_rebind_media_updates_path_only_for_identical_iso_bytes(tmp_path):
    old_dir = tmp_path / "legacy"
    new_dir = tmp_path / "canonical"
    old_dir.mkdir()
    new_dir.mkdir()
    old_media = old_dir / "release.iso"
    new_media = new_dir / "release.iso"
    old_media.write_bytes(b"same ISO contents")
    new_media.write_bytes(b"same ISO contents")
    manifest = {
        "schema": 1,
        "name": "tns-test",
        "readonly_media": tcg_vm.file_identity(old_media),
    }
    manifest_path = tmp_path / "runner.json"
    tcg_vm.write_runner_manifest(manifest_path, manifest)

    old_path, new_path = tcg_vm.rebind_runner_readonly_media(
        manifest_path,
        name="tns-test",
        readonly_media=new_media,
    )

    assert old_path == str(old_media.resolve())
    assert new_path == str(new_media.resolve())
    updated = json.loads(manifest_path.read_text())
    assert updated["readonly_media"] == tcg_vm.file_identity(new_media)
    assert updated["readonly_media"]["sha256"] == manifest["readonly_media"]["sha256"]
    assert manifest_path.stat().st_mode & 0o777 == 0o600


def test_rebind_media_refuses_different_iso_contents(tmp_path):
    old_media = tmp_path / "old.iso"
    new_media = tmp_path / "new.iso"
    old_media.write_bytes(b"old ISO")
    new_media.write_bytes(b"different ISO")
    manifest_path = tmp_path / "runner.json"
    tcg_vm.write_runner_manifest(manifest_path, {
        "schema": 1,
        "name": "tns-test",
        "readonly_media": tcg_vm.file_identity(old_media),
    })

    with pytest.raises(tcg_vm.RunnerError, match="exact same bytes"):
        tcg_vm.rebind_runner_readonly_media(
            manifest_path,
            name="tns-test",
            readonly_media=new_media,
        )
    assert json.loads(manifest_path.read_text())["readonly_media"]["path"] == str(old_media)


def test_rebind_media_refuses_if_recorded_source_was_changed(tmp_path):
    old_media = tmp_path / "old.iso"
    new_media = tmp_path / "new.iso"
    old_media.write_bytes(b"same ISO")
    new_media.write_bytes(b"same ISO")
    manifest_path = tmp_path / "runner.json"
    tcg_vm.write_runner_manifest(manifest_path, {
        "schema": 1,
        "name": "tns-test",
        "readonly_media": tcg_vm.file_identity(old_media),
    })
    old_media.write_bytes(b"changed ISO")

    with pytest.raises(tcg_vm.RunnerError, match="identity changed"):
        tcg_vm.rebind_runner_readonly_media(
            manifest_path,
            name="tns-test",
            readonly_media=new_media,
        )


def test_rebind_media_parser_requires_the_existing_guest_identity():
    args = tcg_vm.build_parser().parse_args([
        "rebind-media",
        "--name", "tns-test",
        "--state-dir", ".vm/tns-test",
        "--readonly-media", "release.iso",
    ])
    assert args.command == "rebind-media"
    assert args.name == "tns-test"
    assert args.readonly_media == "release.iso"


def test_resume_manifest_allows_only_explicit_readonly_media_addition(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "OVMF_CODE_4M.fd"
    variables = tmp_path / "OVMF_VARS_4M.fd"
    media = tmp_path / "installer.iso"
    base.write_bytes(b"base disk identity")
    code.write_bytes(b"firmware code")
    variables.write_bytes(b"fresh variables")
    media.write_bytes(b"read-only image")
    base_manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
    )
    resumed_manifest = tcg_vm.runner_manifest(
        name="tns-test",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="test-network",
        cgroup_parent="dev-background.slice",
        ssh_port=2222,
        https_port=8443,
        readonly_media=media,
    )
    path = tmp_path / "runner.json"
    path.write_text(json.dumps(base_manifest))
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(path, resumed_manifest)
    tcg_vm.check_runner_manifest(path, resumed_manifest, allow_readonly_media_addition=True)
    path.write_text(json.dumps({**base_manifest, "ssh_port": 2230}))
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(path, resumed_manifest, allow_readonly_media_addition=True)


def test_resume_manifest_rejects_a_different_base(tmp_path):
    manifest = tmp_path / "runner.json"
    manifest.write_text('{"schema": 1}\n')
    with pytest.raises(tcg_vm.RunnerError, match="resume identity differs"):
        tcg_vm.check_runner_manifest(manifest, {"schema": 2})
