"""Raw base images cloned by reflink, and `disk promote`.

No Docker, no QEMU, no btrfs: every host command goes through the injectable
`vm_lab.docker_run`, which these tests replace by a recorder. `cp` is a fake
that copies bytes (or fails the way a cross-device reflink does); the guest
script is run only against fake `qemu-system-x86_64` and `qemu-img` programs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import vm_lab
from vm_lab import GENERIC_PROFILE, PortForward, RunnerError

QCOW2_BYTES = b"QFI\xfb" + b"\x00" * 60
RAW_BYTES = b"\xeb\x63\x90 raw disk image " + b"\x01" * 100
CROSS_DEVICE = "cp: failed to clone '/b/base.raw' from '/s/disk.raw': Invalid cross-device link"


# --- a fake host ------------------------------------------------------------------------


class FakeHost:
    """Replaces `vm_lab.docker_run`: records every argv and answers `cp`, `docker ps` and `docker run`."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # One entry per `cp` call, consumed in order; the last one repeats. Each is None (copy the
        # bytes, like a working reflink) or (stderr, leaves_partial_file).
        # "short" is a cp that reports success but leaves a truncated file.
        self.cp_plan: list[tuple[str, bool] | str | None] = [None]
        self.running: dict[str, dict] = {}  # container id -> inspect item
        self.cp_umasks: list[int] = []  # the process umask at each cp call

    def cp_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "cp"]

    def docker_run_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["docker", "run"]]

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "cp":
            during = os.umask(0o077)  # read the umask in force while cp runs, then put it back
            os.umask(during)
            self.cp_umasks.append(during)
            index = min(len(self.cp_calls()) - 1, len(self.cp_plan) - 1)
            plan = self.cp_plan[index]
            source, destination = Path(argv[-2]), Path(argv[-1])
            if plan is None:
                shutil.copyfile(source, destination)
                os.chmod(destination, source.stat().st_mode & 0o777)  # cp keeps the source's mode
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if plan == "short":
                destination.write_bytes(b"short")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if plan == "interrupt":
                destination.write_bytes(b"half a copy")
                raise KeyboardInterrupt
            if plan == "interrupt-after-success":
                shutil.copyfile(source, destination)
                raise KeyboardInterrupt
            stderr, partial = plan
            if partial:
                destination.write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stdout="", stderr=stderr)
        if argv[:2] == ["docker", "run"]:
            raise RunnerError("stop after capturing the run argv")
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="absent")
        if argv[:3] == ["docker", "ps", "-q"]:
            wanted = argv[argv.index("--filter") + 1] if "--filter" in argv else ""
            ids = [cid for cid, item in self.running.items() if wanted in item["_ps_filters"]]
            return SimpleNamespace(returncode=0, stdout="\n".join(ids), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def host(monkeypatch) -> FakeHost:
    fake = FakeHost()
    monkeypatch.setattr(vm_lab, "docker_run", fake)
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda cid: fake.running[cid])
    return fake


LEVELS = [{
    "path": "/x", "cpu_max": 5.0, "memory_high": 5 * 1024**3, "memory_max": 8 * 1024**3,
    "memory_current": 1024**3, "memory_psi_some_avg10": 0.0, "memory_psi_full_avg10": 0.0,
}]


class StartRig:
    def __init__(self, root: Path, host: FakeHost) -> None:
        self.root = root
        self.host = host
        self.state = root / "state"
        self.state.mkdir()
        for name, content in (("c.fd", b"c"), ("v.fd", b"v")):
            (root / name).write_bytes(content)

    def base(self, name: str, content: bytes, mode: int = 0o444) -> Path:
        path = self.root / name
        if path.exists():
            path.chmod(0o644)
        path.write_bytes(content)
        path.chmod(mode)
        return path

    def argv(self, base: Path, *extra: str) -> list[str]:
        return [
            "start", "--name", "deb1", "--base-disk", str(base),
            "--ovmf-code", str(self.root / "c.fd"), "--ovmf-vars", str(self.root / "v.fd"),
            "--state-dir", str(self.state), "--image", "img:1",
            "--memory-mib", "2048", "--cpus", "2", "--forward", "2222:22", "--display-device", "std",
            *extra,
        ]

    def start(self, base: Path, *extra: str) -> int:
        return vm_lab.main(GENERIC_PROFILE, self.argv(base, *extra))


@pytest.fixture
def rig(monkeypatch, tmp_path, host) -> StartRig:
    root = tmp_path.resolve()
    mounts = [{"Destination": str(root), "Source": "/host" + str(root), "RW": True}]
    monkeypatch.setattr(vm_lab, "inspect_network", lambda _name: None)
    monkeypatch.setattr(vm_lab, "image_id", lambda _name: "sha256:img")
    monkeypatch.setattr(vm_lab, "inspect_current_container", lambda: ("cid", {"Mounts": mounts}))
    monkeypatch.setattr(vm_lab, "cgroup_preflight", lambda *_a, **_k: ("insp", LEVELS, "/cg"))
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: root / "admission.lock")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "net")
    return StartRig(root, host)


def fixed_size_profile():
    return dataclasses.replace(
        GENERIC_PROFILE,
        sizing_from_cli=False,
        guest_memory_mib=1024,
        cpus=1,
        forwards=(PortForward(name="ssh", host_port=2222, guest_port=22),),
        display_device=None,
    )


# --- base format detection ---------------------------------------------------------------


def test_auto_reads_the_magic_and_an_explicit_format_overrides_it(tmp_path):
    qcow2 = tmp_path / "a.img"
    raw = tmp_path / "b.img"
    qcow2.write_bytes(QCOW2_BYTES)
    raw.write_bytes(RAW_BYTES)
    detect = lambda path, requested: vm_lab.select_disk_format(GENERIC_PROFILE, path, requested)  # noqa: E731
    assert detect(qcow2, None) == "qcow2" and detect(qcow2, "auto") == "qcow2"
    assert detect(raw, None) == "raw" and detect(raw, "auto") == "raw"
    assert detect(qcow2, "qcow2") == "qcow2"
    assert detect(raw, "raw") == "raw"
    # An explicit raw is taken as stated, even for a file that happens to carry the qcow2 magic.
    assert detect(qcow2, "raw") == "raw"
    (tmp_path / "short.img").write_bytes(b"QF")
    assert detect(tmp_path / "short.img", "auto") == "raw"


def test_explicit_qcow2_that_does_not_match_the_magic_is_an_error(tmp_path):
    raw = tmp_path / "b.img"
    raw.write_bytes(RAW_BYTES)
    with pytest.raises(RunnerError, match="--base-format qcow2 given but .*qcow2 magic"):
        vm_lab.select_disk_format(GENERIC_PROFILE, raw, "qcow2")


def test_a_fixed_size_profile_keeps_qcow2_by_default_without_opening_the_file(tmp_path):
    fixed = fixed_size_profile()
    missing = tmp_path / "does-not-exist"
    assert vm_lab.select_disk_format(fixed, missing, None) == "qcow2"
    raw = tmp_path / "b.img"
    raw.write_bytes(RAW_BYTES)
    assert vm_lab.select_disk_format(fixed, raw, None) == "qcow2"
    assert vm_lab.select_disk_format(fixed, raw, "raw") == "raw"
    assert vm_lab.select_disk_format(fixed, raw, "auto") == "raw"


# --- raw start ---------------------------------------------------------------------------


def test_raw_start_clones_with_exact_cp_argv_and_boots_the_clone(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()
    # The copy goes to a temporary name and is renamed onto disk.raw only when whole.
    assert host.cp_calls() == [["cp", "--reflink=always", "--", str(base), str(rig.state / ".disk.raw.partial")]]
    assert ".disk.raw.partial" not in {path.name for path in rig.state.iterdir()}
    assert (rig.state / "disk.raw").read_bytes() == RAW_BYTES
    # The base is read-only (0444); the run disk must be writable by the guest's user.
    assert (rig.state / "disk.raw").stat().st_mode & 0o777 == 0o600

    [run] = host.docker_run_calls()
    script = run[-1]
    assert "-drive file=/state/disk.raw,if=virtio,format=raw,cache=unsafe \\\n" in script
    assert "disk-overlay" not in script
    assert "-b /base" not in script and "qemu-img create -q -f qcow2 -F qcow2" not in script
    joined = "\n".join(run[:-1])
    assert "/base/" not in joined and str(base) not in joined and "/host" + str(base) not in joined
    assert [item for item in run if item.startswith("type=bind")] == [
        f"type=bind,src=/host{rig.root}/state,dst=/state"
    ]

    manifest = json.loads((rig.state / "runner.json").read_text())
    assert manifest["disk_format"] == "raw"
    assert manifest["base_disk"] == str(base)
    assert manifest["base_size"] == len(RAW_BYTES)
    assert "base_identity" not in manifest


def test_raw_start_prints_the_reflink_line_and_no_full_copy_line(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    rig.start(base)
    out = capsys.readouterr().out
    assert f"[disk] reflink clone of {base}" in out
    assert "full copy" not in out
    assert "the raw base is not mounted" in out


def test_qcow2_start_is_unchanged_mounts_the_base_and_never_calls_cp(rig, host, capsys):
    base = rig.base("base.qcow2", QCOW2_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()
    assert host.cp_calls() == []
    assert not (rig.state / "disk.raw").exists()
    [run] = host.docker_run_calls()
    assert f"type=bind,src=/host{base},dst=/base/disk.qcow2,readonly" in run
    profile = vm_lab.resolve_profile(
        GENERIC_PROFILE, vm_lab.build_parser(GENERIC_PROFILE).parse_args(rig.argv(base)[:]),
    )
    assert run[-1] == vm_lab.host_command_script(profile)
    assert run[-1] == vm_lab.host_command_script(profile, "qcow2")
    assert "qemu-img create -q -f qcow2 -F qcow2 -b /base/disk.qcow2 /state/disk-overlay.qcow2" in run[-1]
    assert "-drive file=/state/disk-overlay.qcow2,if=virtio,format=qcow2,cache=unsafe \\\n" in run[-1]
    assert "disk.raw" not in run[-1]
    manifest = json.loads((rig.state / "runner.json").read_text())
    assert manifest["disk_format"] == "qcow2" and "base_identity" in manifest and "base_size" not in manifest


def test_explicit_base_format_overrides_auto_in_a_real_start(rig, host, capsys):
    qcow2_named_raw = rig.base("odd.qcow2", QCOW2_BYTES)
    assert rig.start(qcow2_named_raw, "--base-format", "raw") == 1
    assert [call[:2] for call in host.cp_calls()] == [["cp", "--reflink=always"]]
    capsys.readouterr()


def test_explicit_qcow2_with_a_raw_base_fails_before_anything_is_written(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base, "--base-format", "qcow2") == 1
    assert "qcow2 magic" in capsys.readouterr().err
    assert host.calls == []
    assert list(rig.state.iterdir()) == []


def test_a_raw_base_that_is_a_symlink_is_refused(rig, host, capsys):
    real = rig.base("real.raw", RAW_BYTES)
    link = rig.root / "link.raw"
    link.symlink_to(real)
    assert rig.start(link) == 1
    assert "must not be a symlink" in capsys.readouterr().err
    assert host.cp_calls() == [] and list(rig.state.iterdir()) == []


# --- clone failure -----------------------------------------------------------------------


def test_clone_failure_is_one_loud_line_removes_the_partial_and_starts_nothing(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    host.cp_plan = [(CROSS_DEVICE, True)]
    assert rig.start(base) == 1
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert lines == [
        f"vm_lab.py: cannot reflink {base} into {rig.state}: {CROSS_DEVICE}; base image and state "
        'directory must be on one btrfs filesystem (see AGENTS.md "Directory conventions and VM files"); '
        "pass --allow-full-copy to copy the whole file instead"
    ]
    assert not (rig.state / "disk.raw").exists()
    assert list(rig.state.iterdir()) == []  # a retry on the same directory is still a first start
    assert host.docker_run_calls() == []
    assert len(host.cp_calls()) == 1


def test_allow_full_copy_retries_with_reflink_auto_and_says_so(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    host.cp_plan = [(CROSS_DEVICE, True), None]
    assert rig.start(base, "--allow-full-copy") == 1
    out = capsys.readouterr().out
    assert host.cp_calls() == [
        ["cp", "--reflink=always", "--", str(base), str(rig.state / ".disk.raw.partial")],
        ["cp", "--reflink=auto", "--", str(base), str(rig.state / ".disk.raw.partial")],
    ]
    assert f"[disk] full copy of {base} (no reflink possible)" in out
    assert "reflink clone of" not in out
    assert (rig.state / "disk.raw").read_bytes() == RAW_BYTES
    assert len(host.docker_run_calls()) == 1


def test_allow_full_copy_does_not_copy_fully_when_the_reflink_works(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    rig.start(base, "--allow-full-copy")
    out = capsys.readouterr().out
    assert [call[1] for call in host.cp_calls()] == ["--reflink=always"]
    assert "[disk] reflink clone of" in out and "full copy" not in out


def test_a_failing_full_copy_is_also_an_error_without_a_partial_file(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    host.cp_plan = [(CROSS_DEVICE, True), ("cp: error writing: No space left on device", True)]
    assert rig.start(base, "--allow-full-copy") == 1
    err = capsys.readouterr().err
    assert "cannot copy" in err and "No space left on device" in err
    assert list(rig.state.iterdir()) == [] and host.docker_run_calls() == []


def test_cp_that_succeeds_but_leaves_a_wrong_sized_file_is_an_error(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    host.cp_plan = ["short"]
    assert rig.start(base) == 1
    assert f"is not a {len(RAW_BYTES)}-byte copy" in capsys.readouterr().err
    assert list(rig.state.iterdir()) == [] and host.docker_run_calls() == []


# --- resume ------------------------------------------------------------------------------


def test_resume_reuses_the_disk_and_never_clones_again(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    disk = rig.state / "disk.raw"
    disk.write_bytes(b"guest wrote here")  # a resumed guest keeps its own changes
    assert rig.start(base, "--resume") == 1
    capsys.readouterr()
    assert len(host.cp_calls()) == 1
    assert disk.read_bytes() == b"guest wrote here"
    assert len(host.docker_run_calls()) == 2
    resume_run = host.docker_run_calls()[-1]
    assert "VM_MODE=resume" in resume_run


def test_resume_with_a_different_format_is_an_error_naming_both(rig, host, capsys):
    raw_base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(raw_base) == 1
    capsys.readouterr()
    qcow2_base = rig.base("other.qcow2", QCOW2_BYTES)
    assert rig.start(qcow2_base, "--resume") == 1
    err = capsys.readouterr().err
    assert "resume disk format differs" in err and "uses a raw disk" in err and "asks for qcow2" in err
    assert len(host.docker_run_calls()) == 1


def test_resume_without_the_raw_disk_is_refused(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    (rig.state / "disk.raw").unlink()
    capsys.readouterr()
    assert rig.start(base, "--resume") == 1
    assert "--resume requires disk.raw, OVMF_CODE_4M.fd" in capsys.readouterr().err
    assert len(host.cp_calls()) == 1


def test_a_guest_recorded_before_raw_bases_existed_resumes_as_qcow2(tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "c.fd"
    variables = tmp_path / "v.fd"
    for path, content in ((base, QCOW2_BYTES), (code, b"c"), (variables, b"v")):
        path.write_bytes(content)
    profile = vm_lab.resolve_profile(
        GENERIC_PROFILE,
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(
            ["start", "--name", "g", "--base-disk", "b", "--ovmf-code", "c", "--ovmf-vars", "v",
             "--state-dir", "s", "--memory-mib", "2048", "--cpus", "2", "--forward", "2222:22",
             "--display-device", "std"]
        ),
    )

    def manifest(disk_format: str) -> dict:
        return vm_lab.runner_manifest(
            profile, name="g", base_disk=base, firmware_code=code, firmware_vars=variables,
            image_id="sha256:i", network="n", cgroup_parent="s.slice", host_ports={"fwd1": 2222},
            disk_format=disk_format,
        )

    legacy = manifest("qcow2")
    del legacy["disk_format"]
    path = tmp_path / "runner.json"
    vm_lab.write_runner_manifest(path, legacy)
    vm_lab.check_runner_manifest(path, manifest("qcow2"))
    with pytest.raises(RunnerError, match="uses a qcow2 disk but this start asks for raw"):
        vm_lab.check_runner_manifest(path, manifest("raw"))
    assert manifest("raw")["base_size"] == len(QCOW2_BYTES)
    assert "base_identity" not in manifest("raw") and "base_identity" in manifest("qcow2")


# --- status ------------------------------------------------------------------------------


def test_status_prints_the_disk_format_and_file(host, tmp_path, capsys, monkeypatch):
    guest = {
        "Config": {"Labels": {GENERIC_PROFILE.vm_label: GENERIC_PROFILE.label_value}},
        "State": {"Running": False, "Status": "exited", "ExitCode": 0},
        "HostConfig": {},
    }
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: guest)
    (tmp_path / "disk.raw").write_bytes(RAW_BYTES)
    assert vm_lab.cmd_status(GENERIC_PROFILE, SimpleNamespace(name="deb1", state_dir=str(tmp_path))) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["disk_format"] == "raw" and result["disk_file"] == str(tmp_path / "disk.raw")

    other = tmp_path / "overlay-guest"
    other.mkdir()
    (other / "disk-overlay.qcow2").write_bytes(QCOW2_BYTES)
    vm_lab.cmd_status(GENERIC_PROFILE, SimpleNamespace(name="deb1", state_dir=str(other)))
    result = json.loads(capsys.readouterr().out)
    assert result["disk_format"] == "qcow2" and result["disk_file"] == str(other / "disk-overlay.qcow2")

    empty = tmp_path / "empty"
    empty.mkdir()
    vm_lab.cmd_status(GENERIC_PROFILE, SimpleNamespace(name="deb1", state_dir=str(empty)))
    assert "disk_format" not in json.loads(capsys.readouterr().out)


# --- the guest script --------------------------------------------------------------------


def run_raw_script(tmp_path: Path, *, with_disk: bool, mode: str = "create") -> subprocess.CompletedProcess:
    profile = vm_lab.resolve_profile(
        GENERIC_PROFILE,
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(
            ["start", "--name", "g", "--base-disk", "b", "--ovmf-code", "c", "--ovmf-vars", "v",
             "--state-dir", "s", "--memory-mib", "2048", "--cpus", "2", "--forward", "2222:22",
             "--display-device", "std"]
        ),
    )
    state = tmp_path / "state"
    state.mkdir()
    (state / "OVMF_CODE_4M.fd").write_bytes(b"code")
    (state / "OVMF_VARS_4M.fd").write_bytes(b"vars")
    if with_disk:
        (state / "disk.raw").write_bytes(RAW_BYTES)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    qemu = fake_bin / "qemu-system-x86_64"
    qemu.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGS_FILE\"\n")
    qemu.chmod(0o755)
    qemu_img = fake_bin / "qemu-img"
    qemu_img.write_text("#!/bin/sh\ntouch \"$IMG_CALLED\"\nexit 0\n")
    qemu_img.chmod(0o755)
    command = vm_lab.host_command_script(profile, "raw").replace("/state", str(state))
    return subprocess.run(
        ["/bin/sh", "-c", command],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "ARGS_FILE": str(tmp_path / "qemu-args"),
            "IMG_CALLED": str(tmp_path / "qemu-img-called"),
            "VM_MODE": mode, "VM_NAME": "g", "VM_FWD1_PORT": "2222", "VM_READONLY_MEDIA": "",
            "VM_VNC_CONSOLE": "0", "VM_SERIAL_CONSOLE": "0",
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("mode", ["create", "resume"])
def test_raw_script_boots_the_clone_and_never_creates_an_overlay(tmp_path, mode):
    result = run_raw_script(tmp_path, with_disk=True, mode=mode)
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "qemu-args").read_text().splitlines()
    assert f"file={tmp_path / 'state' / 'disk.raw'},if=virtio,format=raw,cache=unsafe" in args
    assert not any("qcow2" in item for item in args)
    assert not (tmp_path / "qemu-img-called").exists()


def test_raw_script_refuses_to_start_without_its_disk(tmp_path):
    result = run_raw_script(tmp_path, with_disk=False)
    assert result.returncode != 0
    assert not (tmp_path / "qemu-args").exists()


# --- disk promote ------------------------------------------------------------------------


@pytest.fixture
def promote_rig(host, tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "lock-dir").mkdir()
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: root / "lock-dir" / "admission.lock")
    state = root / "state"
    dest = root / "images"
    state.mkdir()
    dest.mkdir()
    (state / "disk.raw").write_bytes(RAW_BYTES * 50)
    return SimpleNamespace(state=state, dest=dest, tmp=root)


def promote(rig, name="debian-13", *extra: str) -> int:
    return vm_lab.main(
        GENERIC_PROFILE,
        ["disk", "promote", "--state-dir", str(rig.state), "--dest-dir", str(rig.dest), "--name", name, *extra],
    )


def running_guest_on(host: FakeHost, state: Path, *, cid="abc123", name="deb1") -> None:
    label, value = vm_lab.KNOWN_VM_LABELS[1]
    host.running[cid] = {
        "Name": "/" + name,
        "Config": {"Labels": {label: value, GENERIC_PROFILE.state_label: vm_lab.state_identity(state)}},
        "_ps_filters": {f"label={label}={value}"},
    }


def test_promote_copies_hashes_and_makes_both_files_read_only(promote_rig, host, capsys):
    assert promote(promote_rig) == 0
    image = promote_rig.dest / "debian-13.raw"
    digest_file = promote_rig.dest / "debian-13.raw.sha256"
    expected = hashlib.sha256((promote_rig.state / "disk.raw").read_bytes()).hexdigest()
    assert host.cp_calls() == [[
        "cp", "--reflink=always", "--", str(promote_rig.state / "disk.raw"),
        str(promote_rig.dest / ".debian-13.raw.partial"),
    ]]
    assert sorted(path.name for path in promote_rig.dest.iterdir()) == ["debian-13.raw", "debian-13.raw.sha256"]
    assert image.read_bytes() == RAW_BYTES * 50
    assert digest_file.read_text() == f"{expected}  debian-13.raw\n"
    assert image.stat().st_mode & 0o777 == 0o444 and digest_file.stat().st_mode & 0o777 == 0o444
    assert f"[promoted] {image} sha256={expected}" in capsys.readouterr().out
    # The guest's own disk is untouched and stays writable.
    assert (promote_rig.state / "disk.raw").read_bytes() == RAW_BYTES * 50
    assert os.access(promote_rig.state / "disk.raw", os.W_OK)


def test_promote_hashes_the_final_file_in_one_mebibyte_chunks(promote_rig, host, monkeypatch, capsys):
    (promote_rig.state / "disk.raw").write_bytes(b"\x07" * (2 * 1024 * 1024 + 5))
    reads: list[int] = []
    real_open = Path.open

    def spying_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name == "debian-13.raw" and args and args[0] == "rb":
            original = handle.read

            class Spy:
                def read(self_inner, size=-1):
                    reads.append(size)
                    return original(size)

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    handle.close()

            return Spy()
        return handle

    monkeypatch.setattr(Path, "open", spying_open)
    assert promote(promote_rig) == 0
    capsys.readouterr()
    assert reads and set(reads) == {1024 * 1024}


def test_promote_refuses_a_running_guest(promote_rig, host, capsys):
    running_guest_on(host, promote_rig.state)
    assert promote(promote_rig) == 1
    assert "refusing to promote while guest deb1 is running" in capsys.readouterr().err
    assert host.cp_calls() == [] and list(promote_rig.dest.iterdir()) == []


def test_promote_ignores_a_running_guest_of_another_state_directory(promote_rig, host, capsys):
    other = promote_rig.tmp / "other-state"
    other.mkdir()
    running_guest_on(host, other)
    assert promote(promote_rig) == 0
    capsys.readouterr()


def test_promote_refuses_a_qcow2_state_directory(promote_rig, host, capsys):
    (promote_rig.state / "disk.raw").unlink()
    (promote_rig.state / "disk-overlay.qcow2").write_bytes(QCOW2_BYTES)
    assert promote(promote_rig) == 1
    assert "promote needs a raw disk" in capsys.readouterr().err
    assert host.cp_calls() == []


def test_promote_refuses_a_state_directory_without_any_disk(promote_rig, host, capsys):
    (promote_rig.state / "disk.raw").unlink()
    assert promote(promote_rig) == 1
    assert "nothing to promote" in capsys.readouterr().err


@pytest.mark.parametrize("existing", ["debian-13.raw", "debian-13.raw.sha256"])
def test_promote_never_overwrites(promote_rig, host, capsys, existing):
    (promote_rig.dest / existing).write_bytes(b"precious")
    assert promote(promote_rig) == 1
    assert "refusing to overwrite existing" in capsys.readouterr().err
    assert (promote_rig.dest / existing).read_bytes() == b"precious"
    assert host.cp_calls() == []


@pytest.mark.parametrize("name", [".hidden", "a/b", "../x", "", "has space", "a:b", "é"])
def test_promote_refuses_a_bad_name(promote_rig, host, capsys, name):
    assert promote(promote_rig, name) == 1
    assert "invalid image name" in capsys.readouterr().err
    assert host.cp_calls() == [] and list(promote_rig.dest.iterdir()) == []


@pytest.mark.parametrize("name", ["debian-13", "a.b_c-1", "X"])
def test_promote_accepts_the_documented_names(promote_rig, host, capsys, name):
    assert promote(promote_rig, name) == 0
    capsys.readouterr()
    assert (promote_rig.dest / f"{name}.raw").is_file()


def test_promote_requires_an_existing_destination_directory(promote_rig, host, capsys):
    missing = promote_rig.tmp / "nope"
    code = vm_lab.main(
        GENERIC_PROFILE,
        ["disk", "promote", "--state-dir", str(promote_rig.state), "--dest-dir", str(missing), "--name", "n"],
    )
    assert code == 1 and host.cp_calls() == [] and not missing.exists()
    capsys.readouterr()
    plain = promote_rig.tmp / "plain-file"
    plain.write_bytes(b"x")
    code = vm_lab.main(
        GENERIC_PROFILE,
        ["disk", "promote", "--state-dir", str(promote_rig.state), "--dest-dir", str(plain), "--name", "n"],
    )
    assert code == 1 and "destination is not a directory" in capsys.readouterr().err


def test_a_failed_promote_copy_leaves_no_image_and_no_digest(promote_rig, host, capsys):
    host.cp_plan = [(CROSS_DEVICE, True)]
    assert promote(promote_rig) == 1
    err = capsys.readouterr().err
    assert "cannot reflink" in err and "pass --allow-full-copy" in err and str(promote_rig.dest) in err
    assert list(promote_rig.dest.iterdir()) == []


def test_promote_allow_full_copy_retries_with_reflink_auto(promote_rig, host, capsys):
    host.cp_plan = [(CROSS_DEVICE, True), None]
    assert promote(promote_rig, "debian-13", "--allow-full-copy") == 0
    out = capsys.readouterr().out
    assert [call[1] for call in host.cp_calls()] == ["--reflink=always", "--reflink=auto"]
    assert "[disk] full copy of" in out and "[promoted]" in out


def test_an_interrupt_while_promote_hashes_leaves_no_image_digest_or_partial(promote_rig, host, monkeypatch):
    real_open = Path.open
    reads: list[int] = []

    def interrupting_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name == "debian-13.raw" and args and args[0] == "rb":

            class Interrupted:
                def read(self_inner, size=-1):
                    reads.append(size)
                    raise KeyboardInterrupt

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    handle.close()

            return Interrupted()
        return handle

    monkeypatch.setattr(Path, "open", interrupting_open)
    with pytest.raises(KeyboardInterrupt):
        promote(promote_rig)
    assert reads == [1024 * 1024], "the interrupt must have hit the hashing read of the copied file"
    assert len(host.cp_calls()) == 1
    assert list(promote_rig.dest.iterdir()) == []  # no N.raw, no .sha256, no hidden partial
    # The guest's own disk is untouched.
    assert (promote_rig.state / "disk.raw").read_bytes() == RAW_BYTES * 50


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
@pytest.mark.parametrize("where", ["hashing", "digest-written"])
def test_a_signal_during_promote_removes_the_copy_and_every_partial_file(
    promote_rig, host, monkeypatch, capsys, signum, where
):
    before = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGHUP)}
    if before[signum] is signal.SIG_IGN:
        pytest.skip("this signal is ignored by the test process (nohup); promote leaves it ignored")
    real_hash = vm_lab.sha256_of_file
    real_replace = os.replace

    def killed_hash(path):
        signal.raise_signal(signum)
        return real_hash(path)  # not reached

    def killed_after_publish(src, dst):
        real_replace(src, dst)
        if str(dst).endswith(".sha256"):  # the digest file now exists, the command is not finished
            signal.raise_signal(signum)

    if where == "hashing":
        monkeypatch.setattr(vm_lab, "sha256_of_file", killed_hash)
    else:
        monkeypatch.setattr(vm_lab.os, "replace", killed_after_publish)
    assert promote(promote_rig) == 1
    assert f"interrupted by {signal.Signals(signum).name}" in capsys.readouterr().err
    # No N.raw, no N.raw.sha256, no hidden .partial or digest partial.
    assert list(promote_rig.dest.iterdir()) == []
    assert (promote_rig.state / "disk.raw").read_bytes() == RAW_BYTES * 50
    assert {n: signal.getsignal(n) for n in before} == before


def test_promote_removes_both_files_when_the_digest_cannot_be_written(promote_rig, host, monkeypatch, capsys):
    def explode(_path):
        raise OSError("disk read failed")

    monkeypatch.setattr(vm_lab, "sha256_of_file", explode)
    assert promote(promote_rig) == 1
    assert "disk read failed" in capsys.readouterr().err
    assert list(promote_rig.dest.iterdir()) == []


# --- help --------------------------------------------------------------------------------


def test_start_help_names_the_new_options_and_disk_promote_has_help(capsys):
    parser = vm_lab.build_parser(GENERIC_PROFILE)
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--help"])
    start_help = " ".join(capsys.readouterr().out.split())
    assert "--base-format {auto,qcow2,raw}" in start_help and "--allow-full-copy" in start_help
    assert "(default: auto)" in start_help
    with pytest.raises(SystemExit):
        parser.parse_args(["disk", "promote", "--help"])
    promote_help = " ".join(capsys.readouterr().out.split())
    for option in ("--state-dir", "--dest-dir", "--name", "--allow-full-copy"):
        assert option in promote_help
    assert "truenas" not in start_help.lower() + promote_help.lower()


def test_a_fixed_size_front_documents_its_qcow2_default(capsys):
    parser = vm_lab.build_parser(fixed_size_profile())
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--help"])
    assert "(default: qcow2, without reading the file)" in " ".join(capsys.readouterr().out.split())


# --- fix round 1 ---------------------------------------------------------------------------


def test_raw_resume_does_not_depend_on_the_base_file(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()

    def resume(path: Path) -> str:
        assert rig.start(path, "--resume") == 1
        return capsys.readouterr().err

    moved = rig.root / "moved.raw"
    base.rename(moved)
    moved.chmod(0o644)
    steps = [
        ("renamed", lambda: None),
        ("resized", lambda: moved.write_bytes(RAW_BYTES + b"a refreshed, larger build")),
        ("deleted", lambda: moved.unlink()),
    ]
    for count, (what, change) in enumerate(steps, start=2):
        change()
        err = resume(moved)
        assert "stop after capturing the run argv" in err, (what, err)
        assert "identity differs" not in err and "No such file" not in err, (what, err)
        assert len(host.docker_run_calls()) == count
    assert len(host.cp_calls()) == 1
    assert not moved.exists()


def test_raw_resume_with_the_base_gone_still_refuses_another_disk_format(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()
    base.chmod(0o644)
    base.unlink()
    # An explicit qcow2 start cannot be satisfied by a missing base: the strict error stays.
    assert rig.start(base, "--resume", "--base-format", "qcow2") == 1
    assert "No such file" in capsys.readouterr().err
    assert len(host.docker_run_calls()) == 1


def test_qcow2_resume_is_still_blocked_by_a_changed_base(rig, host, capsys):
    base = rig.base("base.qcow2", QCOW2_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()
    rig.base("base.qcow2", QCOW2_BYTES + b"\x00" * 16)  # same magic, different size and mtime
    assert rig.start(base, "--resume") == 1
    assert "resume identity differs" in capsys.readouterr().err
    assert len(host.docker_run_calls()) == 1


def test_raw_resume_still_compares_everything_else(rig, host, capsys):
    base = rig.base("base.raw", RAW_BYTES)
    assert rig.start(base) == 1
    capsys.readouterr()
    argv = rig.argv(base, "--resume")
    argv[argv.index("--cpus") + 1] = "3"
    assert vm_lab.main(GENERIC_PROFILE, argv) == 1
    assert "resume identity differs" in capsys.readouterr().err
    assert len(host.docker_run_calls()) == 1


@pytest.fixture
def known_umask():
    previous = os.umask(0o022)
    try:
        yield 0o022
    finally:
        os.umask(previous)


def test_cp_runs_under_umask_0077_and_the_umask_is_restored(rig, host, capsys, known_umask):
    base = rig.base("base.raw", RAW_BYTES)
    rig.start(base)
    capsys.readouterr()
    assert host.cp_umasks == [0o077]
    assert os.umask(known_umask) == known_umask
    assert (rig.state / "disk.raw").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("plan", ["interrupt", "interrupt-after-success"])
def test_an_interrupted_start_clone_leaves_nothing_and_restores_the_umask(rig, host, known_umask, plan):
    base = rig.base("base.raw", RAW_BYTES)
    host.cp_plan = [plan]
    with pytest.raises(KeyboardInterrupt):
        rig.start(base)
    assert list(rig.state.iterdir()) == []
    assert host.docker_run_calls() == []
    assert os.umask(known_umask) == known_umask
    # The directory is still a first-start directory: a retry clones again.
    host.cp_plan = [None]
    assert rig.start(base) == 1
    assert (rig.state / "disk.raw").read_bytes() == RAW_BYTES


@pytest.mark.parametrize("plan", ["interrupt", "interrupt-after-success"])
def test_an_interrupted_promote_leaves_no_image_digest_or_temporary_file(promote_rig, host, known_umask, plan):
    host.cp_plan = [plan]
    with pytest.raises(KeyboardInterrupt):
        promote(promote_rig)
    assert list(promote_rig.dest.iterdir()) == []
    assert os.umask(known_umask) == known_umask
    # The admission lock was released: the retry works.
    host.cp_plan = [None]
    assert promote(promote_rig) == 0
    assert sorted(p.name for p in promote_rig.dest.iterdir()) == ["debian-13.raw", "debian-13.raw.sha256"]


def test_promote_takes_the_start_admission_lock(promote_rig, host, capsys):
    with vm_lab.vm_start_admission_lock("vm_lab.py"):
        assert promote(promote_rig) == 1
    assert "another vm_lab.py start is in progress" in capsys.readouterr().err
    assert host.cp_calls() == [] and list(promote_rig.dest.iterdir()) == []
    assert promote(promote_rig) == 0


def test_promote_refuses_and_keeps_a_leftover_temporary_file(promote_rig, host, capsys):
    leftover = promote_rig.dest / ".debian-13.raw.partial"
    leftover.write_bytes(b"someone else's copy")
    assert promote(promote_rig) == 1
    assert "refusing to reuse existing" in capsys.readouterr().err
    assert leftover.read_bytes() == b"someone else's copy"
    assert host.cp_calls() == []
