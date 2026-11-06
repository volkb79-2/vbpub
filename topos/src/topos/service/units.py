"""Render the complete systemd surface from toposd.toml."""

from __future__ import annotations

from pathlib import Path

from .config import ServiceConfig


def render_units(cfg: ServiceConfig) -> dict[str, str]:
    """Render the socket, service, slice, and sysusers files in stable order."""
    socket = cfg.sockets.observe
    runtime_directory = cfg.state_dir.relative_to(Path("/run")).as_posix()
    socket_unit = "\n".join(
        [
            "[Unit]",
            "Description=topos daemon, observe access level",
            "# No PartOf=: it would propagate a service restart to the socket and recreate the inode.",
            "",
            "[Socket]",
            f"ListenStream={socket.path}",
            "SocketUser=root",
            f"SocketGroup={socket.group}",
            f"SocketMode={socket.mode}",
            "DirectoryMode=0755",
            "FileDescriptorName=observe",
            "RemoveOnStop=no",
            "Service=topos.service",
            "",
            "[Install]",
            "WantedBy=sockets.target",
            "",
        ]
    )
    service_unit = "\n".join(
        [
            "[Unit]",
            "Description=topos read-only daemon",
            "Requires=topos-observe.socket",
            "After=network.target topos-observe.socket",
            "",
            "[Service]",
            "Type=simple",
            "User=root",
            "UMask=0077",
            "Slice=topos.slice",
            f"RuntimeDirectory={runtime_directory}",
            "RuntimeDirectoryMode=0700",
            "RuntimeDirectoryPreserve=yes",
            "Sockets=topos-observe.socket",
            "ExecStart=/opt/topos/current/venv/bin/toposd run --config /etc/topos/toposd.toml",
            "Restart=on-failure",
            "RestartPreventExitStatus=2",
            "RestartSec=5s",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "",
            "# The daemon needs host cgroup, Docker, BPF and optional DAMON access, so it is",
            "# intentionally a host service rather than a privileged container.  Its own",
            "# slice bounds a leak without imposing a CPU quota on incident collection.",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    slice_unit = "\n".join(
        [
            "[Unit]",
            "Description=Resource envelope for the topos host-inspection daemon",
            "",
            "[Slice]",
            "# Protect the host from an accidental unbounded frame/history leak while still",
            "# allowing a short diagnostic burst.  CPU remains weight-based: observability",
            "# should yield to production workloads but must remain responsive under pressure.",
            "CPUWeight=50",
            "MemoryHigh=256M",
            "MemoryMax=512M",
            "MemorySwapMax=1G",
            "TasksMax=512",
            "",
        ]
    )
    sysusers = (
        "g topos-observe -\n"
        if socket.group == "topos-observe"
        else f"# observe level mapped to existing group {socket.group}; nothing created\n"
    )
    return {
        "topos-observe.socket": socket_unit,
        "topos.service": service_unit,
        "topos.slice": slice_unit,
        "topos.sysusers": sysusers,
    }


__all__ = ["render_units"]
