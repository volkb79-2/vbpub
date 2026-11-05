"""One interface filter shared by host_meta, NetHostProvider and the netns candidate (A-02).

Packets forwarded to a container are counted on the physical NIC, the docker bridge
and the veth, and local traffic on ``lo`` is counted twice. Only real interfaces may
be summed into a "whole host" figure.
"""

from __future__ import annotations

# Prefix matches. ``lo`` is deliberately NOT a prefix (``lowpan0`` is real; A-17).
VIRTUAL_INTERFACE_PREFIXES = ("veth", "br-", "docker")
VIRTUAL_INTERFACE_EXACT = frozenset({"lo"})


def is_virtual_interface(name: str) -> bool:
    return name in VIRTUAL_INTERFACE_EXACT or name.startswith(VIRTUAL_INTERFACE_PREFIXES)
