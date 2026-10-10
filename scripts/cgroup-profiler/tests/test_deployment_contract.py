"""Structural checks for the daemon's security-sensitive Compose contract."""

from pathlib import Path
import tomllib


COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "ciu.compose.yml.j2"
PROJECT_ROOT = COMPOSE_TEMPLATE.parent


def test_observer_r2_serializes_mutants_that_share_one_tester_cgroup():
    gate = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    assay = tomllib.loads((PROJECT_ROOT / "assay.toml").read_text(encoding="utf-8"))
    assert gate["lanes"]["r2"]["resource_observer"] == "cgprofile"
    assert assay["lanes"]["r2"]["judge"]["mutation"]["jobs"] == 1


def test_only_daemon_uses_host_pid_namespace_and_mount_modes_are_explicit():
    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    defaults = " ".join(
        (COMPOSE_TEMPLATE.parent / "ciu.defaults.toml.j2")
        .read_text(encoding="utf-8")
        .split()
    )
    gate_script = (COMPOSE_TEMPLATE.parent / "tools" / "gate.sh").read_text(
        encoding="utf-8"
    )

    assert 'cgroup: "private"' in compose
    assert 'network_mode: "none"' in compose
    assert compose.count('pid: "host"') == 1
    assert "CGPROFILE_PID_NAMESPACE_MODE: host" in compose
    assert "managed service alone joins the host PID namespace" in defaults
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
    assert (
        "source: {{ cgprofile.daemon.socket_dir }}\n"
        "        target: {{ cgprofile.daemon.socket_dir }}\n"
        "        bind:\n"
        "          create_host_path: false"
    ) in compose
    assert (
        "- {{ cgprofile.daemon.socket_dir }}:"
        "{{ cgprofile.daemon.socket_dir }}"
    ) not in compose
    assert "AttachProcessesToUnit" in compose
    assert "lib.placement.CgroupWriteGuard" in compose
    assert "--cgroupns=host" not in gate_script
    assert "--pid=host" not in gate_script
    assert "--net=host" not in gate_script


def test_user_docs_link_the_namespace_and_placement_rationale():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    consumers_normalized = " ".join(consumers.split())
    defaults = (PROJECT_ROOT / "ciu.defaults.toml.j2").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    target = "DESIGN-GUIDE.md#daemon-safety-and-placement"
    assert target in readme
    assert target in consumers
    assert "The daemon alone uses" in readme
    assert "host PID namespace so DAMON sysfs can resolve host" in readme
    assert "the daemon never joins a host namespace" not in readme
    assert "PID and cgroup namespaces remain private." not in readme
    assert "consistency check, not host authentication" in readme
    assert "## Daemon safety and placement" in design
    assert "host PID namespace" in design
    assert "not an independent attestation" in design
    assert "create_host_path: false" in design
    assert "refuses a missing source" in consumers
    assert "process-table/PID-operation authority" in " ".join(design.split())
    assert "host-PID mode" in consumers
    assert "not an independent proof" in consumers_normalized
    assert "it is not PID 1 in the host namespace" in defaults
    assert "never joins a host" not in dockerfile
    assert "This image does not" in dockerfile
    assert (
        "host cgroup bind is read-only because it does not perform daemon placement"
        in " ".join(design.split())
    )
    assert (
        "daemon's host cgroup-v2 bind is therefore writable"
        in " ".join(design.split())
    )
