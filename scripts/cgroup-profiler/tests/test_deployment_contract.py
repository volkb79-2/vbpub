"""Structural checks for the daemon's security-sensitive Compose contract."""

from pathlib import Path


COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "ciu.compose.yml.j2"
PROJECT_ROOT = COMPOSE_TEMPLATE.parent


def test_daemon_keeps_namespaces_private_and_mount_modes_explicit():
    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    gate_script = (COMPOSE_TEMPLATE.parent / "tools" / "gate.sh").read_text(
        encoding="utf-8"
    )

    assert 'cgroup: "private"' in compose
    assert 'network_mode: "none"' in compose
    assert 'pid: "host"' not in compose
    assert 'cgroup: "host"' not in compose
    assert 'network_mode: "host"' not in compose
    assert "source: /proc\n        target: /hostproc\n        read_only: true" in compose
    assert (
        "source: /sys/fs/cgroup\n        target: /sys/fs/cgroup\n"
        "        read_only: false"
    ) in compose
    assert (
        "source: /run/dbus/system_bus_socket\n"
        "        target: /run/dbus/system_bus_socket\n"
        "        read_only: true\n"
        "        bind:\n"
        "          create_host_path: false"
    ) in compose
    assert "AttachProcessesToUnit" in compose
    assert "lib.placement.CgroupWriteGuard" in compose
    assert "--cgroupns=host" not in gate_script
    assert "--pid=host" not in gate_script
    assert "--net=host" not in gate_script


def test_user_docs_link_the_namespace_and_placement_rationale():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")

    target = "DESIGN-GUIDE.md#daemon-safety-and-placement"
    assert target in readme
    assert target in consumers
    assert "## Daemon safety and placement" in design
    assert "one-shot helper also keeps its host cgroup bind read-only" in design
    assert "daemon's host cgroup-v2 bind is therefore\nwritable" in design
