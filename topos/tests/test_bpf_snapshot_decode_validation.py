"""Hybrid BTF-decoded bpftool rows accepted by the BPF snapshot parser."""

from __future__ import annotations

import json

from topos.daemon.bpf_snapshot import BpfSnapshotBridge


def test_parser_uses_top_level_fields_missing_from_nested_btf_objects() -> None:
    """Partial structured entries may supply their remaining decoded fields top-level."""
    rows = BpfSnapshotBridge._parse_bpftool_output(
        json.dumps(
            [
                {
                    "key": {"cgroup_id": 92, "direction": "ingress"},
                    "value": {},
                    "family": "ipv6",
                    "proto": "udp",
                    "bytes": 10,
                    "packets": 4,
                }
            ]
        )
    )

    assert rows == [
        {
            "cgroup_id": 92,
            "direction": "ingress",
            "family": "ipv6",
            "proto": "udp",
            "bytes": 10,
            "packets": 4,
        }
    ]
