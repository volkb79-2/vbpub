from __future__ import annotations

from pathlib import Path

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2 import host_facts


def make_installer(tmp_path: Path) -> Installer:
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    return Installer(config, HostActions(dry_run=True))


def test_collect_host_facts_never_raises_under_dry_run(tmp_path):
    installer = make_installer(tmp_path)
    facts = host_facts.collect_host_facts(installer)
    # Pure-python facts reflect the real (test) host, since they don't touch
    # HostActions at all.
    assert facts["cpu_count"] >= 1
    assert facts["numa_nodes"] >= 1
    assert facts["memory_total_mib"] > 0
    assert facts["kernel_release"]
    # Subprocess-based facts degrade to a placeholder under dry-run rather
    # than attempting a real command.
    assert facts["virtualization"] == "unknown (dry-run)"
    assert facts["partition_layout"] == "(dry-run: not executed)"
    assert facts["gpu_devices"] == []
    assert facts["interfaces"] == []


def test_collect_host_facts_survives_a_missing_tool(tmp_path, monkeypatch):
    """A tool absent from a minimal image (FileNotFoundError from subprocess)
    must degrade to a placeholder, never crash the report - or the install."""
    installer = make_installer(tmp_path)
    installer.actions.dry_run = False  # force the real (non-placeholder) code path

    def fake_run(argv, description="", dangerous=False):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: {argv[0]!r}")

    monkeypatch.setattr(installer, "_run", fake_run)
    facts = host_facts.collect_host_facts(installer)
    assert facts["gpu_devices"] == []
    assert facts["virtualization"] == "unknown"


def test_safe_catches_exception_and_returns_default():
    def boom():
        raise RuntimeError("nope")

    assert host_facts._safe(boom, "fallback") == "fallback"


def test_existing_users_filters_system_accounts(monkeypatch):
    passwd = (
        "root:x:0:0:root:/root:/bin/bash\n"
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
        "deploy:x:1000:1000:Deploy:/home/deploy:/bin/bash\n"
    )
    monkeypatch.setattr(host_facts, "_read_text", lambda path: passwd if path == "/etc/passwd" else "")
    assert host_facts._existing_users() == ["deploy"]


def test_detect_public_ip_and_reverse_dns(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"203.0.113.45\n"

    monkeypatch.setattr(host_facts.urllib.request, "urlopen", lambda req, timeout=5.0: FakeResp())
    assert host_facts._detect_public_ip() == "203.0.113.45"

    monkeypatch.setattr(host_facts.socket, "gethostbyaddr", lambda ip: ("controller.example.net", [], [ip]))
    assert host_facts._reverse_dns("203.0.113.45") == "controller.example.net"


def test_format_facts_html_wraps_copyable_values_in_code_tags():
    facts = {
        "cpu_count": 4, "numa_nodes": 1, "memory_total_mib": 8192,
        "kernel_release": "6.1.0-vbpub", "boot_mode": "UEFI", "virtualization": "kvm",
        "gpu_devices": [], "existing_users": [], "ssh_password_authentication": "no",
        "interfaces": [{"name": "eth0", "mac": "aa:bb:cc:dd:ee:ff", "ips": ["10.0.0.5"]}],
        "gateway": "10.0.0.1", "dns_servers": ["1.1.1.1"],
        "public_ip": "203.0.113.45", "public_ip_reverse_dns": "controller.example.net",
        "entropy_available": 3000, "existing_swap": "", "storage_devices": "vda 20G disk",
        "partition_layout": "label: gpt",
    }
    html = host_facts.format_facts_html(facts)
    assert "<code>eth0</code>" in html
    assert "<code>10.0.0.5</code>" in html
    assert "<code>203.0.113.45</code>" in html
    assert "<pre>vda 20G disk</pre>" in html
    assert "<pre>label: gpt</pre>" in html
