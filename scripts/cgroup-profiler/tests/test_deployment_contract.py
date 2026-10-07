"""Structural checks for the daemon's security-sensitive Compose contract."""

from pathlib import Path


COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "ciu.compose.yml.j2"
PROJECT_ROOT = COMPOSE_TEMPLATE.parent


def test_only_daemon_uses_host_pid_namespace_and_mount_modes_are_explicit():
    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    defaults = (COMPOSE_TEMPLATE.parent / "ciu.defaults.toml.j2").read_text(
        encoding="utf-8"
    )
    gate_script = (COMPOSE_TEMPLATE.parent / "tools" / "gate.sh").read_text(
        encoding="utf-8"
    )

    assert 'cgroup: "private"' in compose
    assert 'network_mode: "none"' in compose
    assert compose.count('pid: "host"') == 1
    assert "CGPROFILE_PID_NAMESPACE_MODE: host" in compose
    assert "daemon alone" in defaults
    assert "uses the host PID namespace" in defaults
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
    assert "The daemon alone uses" in readme
    assert "host PID namespace so DAMON sysfs can resolve host" in readme
    assert "the daemon never joins a host namespace" not in readme
    assert "## Daemon safety and placement" in design
    assert "host PID namespace" in design
    assert "process-table/PID-operation authority" in " ".join(design.split())
    assert "host-PID mode" in consumers
    assert (
        "host cgroup bind is read-only because it does not perform daemon placement"
        in " ".join(design.split())
    )
    assert (
        "daemon's host cgroup-v2 bind is therefore writable"
        in " ".join(design.split())
    )
