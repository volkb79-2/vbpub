"""Structural checks for the daemon's security-sensitive Compose contract."""

from pathlib import Path


COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "ciu.compose.yml.j2"


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
    assert "lib.placement.CgroupWriteGuard" in compose
    assert "--cgroupns=host" not in gate_script
    assert "--pid=host" not in gate_script
    assert "--net=host" not in gate_script
