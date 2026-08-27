from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import urllib.parse
import urllib.request

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.bootstrap import main
from debian_install_v2.config import Config, ConfigError
from debian_install_v2.installer import Installer, SWAP_TYPE_GUID
from debian_install_v2.state import StateError, StateStore
from debian_install_v2.tests.test_fake_integration import FakeHostActions


def make_installer(tmp_path, *, dry_run=True, **config_overrides):
    defaults = {
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "auto_reboot_after_stage1": False,
        "credential_mode": "systemd",
    }
    defaults.update(config_overrides)
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        **defaults,
    )
    actions = FakeHostActions() if not dry_run else HostActions(dry_run=True)
    if not dry_run:
        actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer


def test_mkdir_real_path(tmp_path):
    actions = HostActions(dry_run=False)
    actions.mkdir(str(tmp_path / "real-dir"))
    assert (tmp_path / "real-dir").is_dir()


def test_stage2_config_missing_config_dict(tmp_path):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({"schema_version": 1, "config": 5}))
    from debian_install_v2.bootstrap import _stage2_config
    from debian_install_v2.state import StateStore
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(StateStore, "load", lambda self: {"schema_version": 1, "config": 5})
    with pytest.raises(StateError, match="configuration object"):
        _stage2_config(str(d))
    monkeypatch.undo()


def test_cli_status_success(tmp_path, capsys):
    installer = make_installer(tmp_path)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "schema_version": 1, "fresh_install": True,
        "swap_disk_total_gb": 32, "swap_file_count": 8,
        "telegram_bot_token": "", "telegram_chat_id": "",
        "auto_reboot_after_stage1": False, "never_reboot": True,
        "credential_mode": "systemd",
        "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"),
    }))
    assert main(["--action", "status", "--config", str(cfg), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == 1


def test_module_main_exit():
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "debian_install_v2.bootstrap", "--action", "resume"],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_resume_systemd_credentials_missing(tmp_path):
    installer = make_installer(tmp_path)
    installer._stage2 = lambda: None
    installer.resume()
    assert installer.config.telegram_bot_token == ""


def test_show_plan_real_dump(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(512 * 1024 ** 3)
    installer.actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        "label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start=2500608, size=20971520, type=abc\n"
    )
    plan = installer.show_plan()
    assert plan["release"] == "trixie"
    assert len(plan["swap_partitions"]) == 8


def test_verify_missing_backup(tmp_path):
    installer = make_installer(tmp_path)
    state_dir = Path(installer.config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "disk-transaction.json").write_text(json.dumps({
        "backup": str(state_dir / "nope.sfdisk"),
        "checksum": str(state_dir / "nope.sha256"),
    }))
    with pytest.raises(RuntimeError, match="backup or checksum is missing"):
        installer.verify()


def test_disable_stage2_real_marker(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.disable_stage2()
    assert (Path(installer.config.state_dir) / "stage2_done").exists()


def test_configure_apt_real_backup_and_policy(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    sources = Path("/etc/apt/sources.list.d/debian.sources")
    monkeypatch.setattr("pathlib.Path.is_file", lambda self, *a, **kw: True)
    monkeypatch.setattr("shutil.copy2", lambda *a, **kw: None)
    installer.actions.outputs[("/usr/bin/apt-cache", "policy")] = "nothing"
    installer.actions.outputs[("/usr/bin/apt-get", "update", "-qq")] = ""
    installer.actions.outputs[("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "curl", "git", "python3")] = ""
    with pytest.raises(RuntimeError, match="APT configuration did not resolve"):
        installer._configure_apt()


def test_docker_unsupported_arch(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="unsupported architecture"):
        installer._install_docker()


def test_docker_non_https_scheme(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    monkeypatch.setattr(urllib.parse, "urlparse", lambda url: urllib.parse.ParseResult(
        scheme="http", netloc="", path="", params="", query="", fragment=""
    ))
    with pytest.raises(RuntimeError, match="must use HTTPS"):
        installer._install_docker()


def test_docker_real_download(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"FAKE-KEY"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    installer.actions.mkdir = lambda path: None
    installer.actions.outputs[("/usr/bin/apt-get", "update", "-qq")] = ""
    installer.actions.outputs[("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "curl", "gnupg")] = ""
    installer.actions.outputs[("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "containerd.io", "docker-buildx-plugin", "docker-ce", "docker-ce-cli", "docker-compose-plugin")] = ""
    installer.actions.outputs[("/usr/bin/systemctl", "enable", "--now", "docker")] = ""
    installer._install_docker()
    assert installer.actions.files["/etc/apt/keyrings/docker.asc"].decode() == "FAKE-KEY"


def test_parse_partition_entries_skips_non_digit(tmp_path):
    installer = make_installer(tmp_path)
    dump = (
        "label: gpt\n"
        "/dev/vda3 : start=1, size=2, type=abc\n"
        "/dev/vdax : start=9, size=9, type=def\n"
    )
    entries = installer._parse_partition_entries(dump)
    assert 3 in entries
    assert "vdax" not in {str(k) for k in entries}


def test_geometry_missing_root(tmp_path):
    installer = make_installer(tmp_path)
    with pytest.raises(RuntimeError, match="does not preserve the root partition"):
        installer._validate_plan_geometry({}, {}, 100)


def test_geometry_root_grows(tmp_path):
    installer = make_installer(tmp_path)
    current = {3: {"start": "100", "size": "100"}}
    plan = {3: {"start": "100", "size": "500"}}
    with pytest.raises(RuntimeError, match="grows the root partition"):
        installer._validate_plan_geometry(current, plan, 100)


def test_geometry_overlap(tmp_path):
    installer = make_installer(tmp_path)
    current = {3: {"start": "100", "size": "100"}}
    plan = {
        3: {"start": "100", "size": "100"},
        4: {"start": "150", "size": "100"},
    }
    with pytest.raises(RuntimeError, match="overlap"):
        installer._validate_plan_geometry(current, plan, 100)


def test_geometry_bad_swap_numbering(tmp_path):
    installer = make_installer(tmp_path)
    current = {3: {"start": "100", "size": "100"}}
    plan = {
        3: {"start": "100", "size": "100"},
        5: {"start": "250", "size": "100"},
    }
    with pytest.raises(RuntimeError, match="unexpected swap numbering"):
        installer._validate_plan_geometry(current, plan, 100)


def test_plan_too_small(tmp_path):
    installer = make_installer(tmp_path, swap_disk_total_gb=1, swap_file_count=2048)
    with pytest.raises(RuntimeError, match="too small"):
        installer._plan_swap_partitions()


def test_plan_disk_too_small(tmp_path):
    installer = make_installer(tmp_path, swap_disk_total_gb=10240)
    with pytest.raises(RuntimeError, match="lacks space"):
        installer._plan_swap_partitions()


def test_write_plan_real_keeps_earlier_partitions(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        "label: gpt\n"
        "device: /dev/vda\n"
        "\n"
        "/dev/vdax : start=1, size=1, type=bad\n"
        "/dev/vda1 : start=2048, size=1000, type=efi\n"
        "/dev/vda2 : start=4096, size=1000, type=boot\n"
        "/dev/vda3 : start=2500608, size=20971520, type=linux\n"
    )
    partitions = [(30000000, 1000), (30010000, 1000)]
    plan = installer._write_sfdisk_plan(partitions, 20971520)
    assert "/dev/vda1" in plan
    assert "/dev/vda2" in plan
    assert "size=20971520" in plan
    assert "type=0657fd6d" in plan


def test_write_plan_root_without_size(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        "label: gpt\n"
        "device: /dev/vda\n"
        "\n"
        "/dev/vda3 : start=2500608, type=linux\n"
    )
    plan = installer._write_sfdisk_plan([(30000000, 1000)], 20971520)
    assert "size=20971520" in plan


def test_docker_aarch64_dry_run(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=True)
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    installer._install_docker()
    assert installer.actions.dry_run_writes["/etc/apt/keyrings/docker.asc"] == "dry-run-gpg-key\n"


def test_health_gate_failures(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings")] = ""
    installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4")] = ""

    # missing PARTUUID
    with pytest.raises(RuntimeError, match="missing PARTUUID"):
        installer._health_gate_swap_devices()

    # present but not active
    installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4")] = "uuid4"
    for num in range(5, 12):
        installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", f"/dev/vda{num}")] = f"uuid{num}"
    with pytest.raises(RuntimeError, match="formatted but not active"):
        installer._health_gate_swap_devices()

    # active but not in fstab
    installer.actions.outputs[("/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings")] = (
        "\n".join(f"/dev/vda{n} partition 1G 10" for n in range(4, 12))
    )
    with pytest.raises(RuntimeError, match="absent from fstab"):
        installer._health_gate_swap_devices()


def test_activate_swap_partitions_real(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/sbin/swapoff", "-a")] = ""
    for num in range(4, 12):
        p = f"/dev/vda{num}"
        installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", p)] = f"uuid{num}"
        installer.actions.outputs[("/usr/sbin/mkswap", p)] = ""
        installer.actions.outputs[("/usr/bin/swapon", "-p", "10", p)] = ""
    installer.actions.outputs[("/usr/bin/swapon", "-p", "10", "/dev/vda4")] = ""
    installer._activate_swap_partitions()
    fstab = installer.actions.files["/etc/fstab"].decode()
    assert fstab.count(" none swap sw,pri=10 0 0") == 8


def test_install_stage2_systemd_credentials(tmp_path):
    installer = make_installer(
        tmp_path, dry_run=False,
        telegram_bot_token="123:token", telegram_chat_id="456",
        credential_mode="systemd",
    )
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/bin/systemctl", "daemon-reload")] = ""
    installer.actions.outputs[("/usr/bin/systemctl", "enable", "vbpub-bootstrap-stage2.service")] = ""
    installer._install_stage2()
    assert installer.actions.files["/etc/vbpub/credentials/telegram_bot_token"].decode() == "123:token\n"
    assert installer.actions.files["/etc/vbpub/credentials/telegram_chat_id"].decode() == "456\n"
    assert (Path(installer.config.state_dir) / "stage1_done").exists()


def test_notify_failure(tmp_path, monkeypatch, capsys):
    installer = make_installer(tmp_path, dry_run=False, telegram_bot_token="tok", telegram_chat_id="cid")
    def boom(*a, **kw):
        raise OSError("network down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    installer._notify("hello")
    assert "WARN" in capsys.readouterr().out


def test_reboot_real(tmp_path):
    installer = make_installer(tmp_path, dry_run=False, auto_reboot_after_stage1=True, never_reboot=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/bin/systemctl", "reboot")] = ""
    installer._reboot()
    assert any(a.argv == ("/usr/bin/systemctl", "reboot") for a in installer.actions.planned)


def test_module_main_block():
    import runpy, sys
    old_argv = sys.argv
    saved_module = sys.modules.pop("debian_install_v2.bootstrap", None)
    sys.argv = ["bootstrap", "--action", "resume"]
    try:
        with pytest.raises(SystemExit):
            runpy.run_module("debian_install_v2.bootstrap", run_name="__main__",
                             alter_sys=True)
    finally:
        sys.argv = old_argv
        if saved_module is not None:
            sys.modules["debian_install_v2.bootstrap"] = saved_module


def test_activate_swap_missing_partuuid(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/sbin/swapoff", "-a")] = ""
    installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4")] = ""
    with pytest.raises(RuntimeError, match="no PARTUUID"):
        installer._activate_swap_partitions()


def test_activate_swap_partuuid_disappears(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/sbin/swapoff", "-a")] = ""
    installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4")] = "uuid4"
    installer.actions.outputs[("/usr/sbin/mkswap", "/dev/vda4")] = ""
    installer.actions.outputs[("/usr/bin/swapon", "-p", "10", "/dev/vda4")] = ""
    real_run = installer.actions.run
    calls = {"n": 0}
    def flaky(argv, **kw):
        if argv == ["/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4"]:
            calls["n"] += 1
            if calls["n"] == 2:
                return ""
        return real_run(argv, **kw)
    monkeypatch.setattr(installer.actions, "run", flaky)
    with pytest.raises(RuntimeError, match="PARTUUID disappeared"):
        installer._activate_swap_partitions()


def test_notify_dry_run_returns_early(tmp_path):
    installer = make_installer(tmp_path, dry_run=True, telegram_bot_token="tok", telegram_chat_id="cid")
    installer._notify("hello")  # must not raise; early return path


def test_health_gate_compressor_and_log(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    installer.actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer.actions.outputs[("/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings")] = (
        "\n".join(f"/dev/vda{n} partition 1G 10" for n in range(4, 12))
    )
    for num in range(4, 12):
        installer.actions.outputs[("/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", f"/dev/vda{num}")] = f"uuid{num}"
    fstab_content = "\n".join(f"PARTUUID=uuid{num} none swap sw,pri=10 0 0" for num in range(4, 12))
    real_read = Path.read_text
    real_is_file = Path.is_file

    def fake_read(self, *a, **kw):
        s = str(self)
        if s == "/etc/fstab":
            return fstab_content
        if s.endswith("compressor"):
            return "lz4\n"

    def fake_is_file(self, *a, **kw):
        return str(self) == "/etc/fstab" or real_is_file(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", fake_read)
    monkeypatch.setattr(Path, "is_file", fake_is_file)
    with pytest.raises(RuntimeError, match="zswap compressor"):
        installer._health_gate_swap_devices()

    def fake_read_match(self, *a, **kw):
        s = str(self)
        if s == "/etc/fstab":
            return fstab_content
        if s.endswith("compressor"):
            return "zstd\n"

    monkeypatch.setattr(Path, "read_text", fake_read_match)
    with pytest.raises(RuntimeError, match="stage2 log does not exist"):
        installer._health_gate_swap_devices()
