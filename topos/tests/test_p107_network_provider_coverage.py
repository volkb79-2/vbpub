"""P107 exact coverage for private-netns and BPF providers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from topos.model import Entity
from topos.providers.base import NetSample, unavailable_sample
from topos.providers.net_bpf import BpfProvider
from topos.providers.net_netns import NetnsProvider, _Candidate


def entity(key: str, *, parent: str | None = None) -> Entity:
    return Entity(key=key, kind="scope", parent=parent)


def test_netns_collect_rejects_multiple_namespaces() -> None:
    provider = NetnsProvider(
        Path("/virtual/cgroup"),
        proc_root=Path("/virtual/proc"),
        host_netns_id=99,
    )
    with (
        patch("topos.providers.net_netns.time.time", return_value=10.0),
        patch.object(provider, "_read_pids", return_value=(1, 2)),
        patch.object(provider, "_ns_id_for_pid", side_effect=(10, 11)),
    ):
        result = provider.collect({"alpha": entity("alpha")})

    assert result == {
        "alpha": unavailable_sample("multiple netns in cgroup"),
    }
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 10.0,
        "errors": [],
        "host_netns_id": 99,
        "entities_seen": 1,
        "private_namespaces": 0,
        "shared_namespaces": 0,
    }


def test_netns_collect_rejects_missing_device_and_shared_namespace() -> None:
    provider = NetnsProvider(
        Path("/virtual/cgroup"),
        proc_root=Path("/virtual/proc"),
        host_netns_id=99,
    )
    pids = {"missing": (1,), "shared-a": (2,), "shared-b": (3,)}
    namespaces = {1: 10, 2: 20, 3: 20}

    def candidate(pid: int, ns_id: int) -> _Candidate | None:
        if pid == 1:
            return None
        return _Candidate(
            ns_id=ns_id,
            rx_bytes=pid * 100,
            tx_bytes=pid * 200,
            rx_pkts=pid,
            tx_pkts=pid * 2,
        )

    with (
        patch("topos.providers.net_netns.time.time", return_value=11.0),
        patch.object(
            provider,
            "_read_pids",
            side_effect=lambda key: pids[key],
        ),
        patch.object(
            provider,
            "_ns_id_for_pid",
            side_effect=lambda pid: namespaces[pid],
        ),
        patch.object(
            provider,
            "_candidate_from_pid",
            side_effect=candidate,
        ),
    ):
        result = provider.collect(
            {
                "missing": entity("missing"),
                "shared-a": entity("shared-a"),
                "shared-b": entity("shared-b"),
            }
        )

    assert result == {
        "missing": unavailable_sample("missing /proc/<pid>/net/dev"),
        "shared-a": unavailable_sample("shared private netns"),
        "shared-b": unavailable_sample("shared private netns"),
    }
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 11.0,
        "errors": [],
        "host_netns_id": 99,
        "entities_seen": 3,
        "private_namespaces": 1,
        "shared_namespaces": 1,
    }


def test_netns_host_detection_failure_and_status_copy() -> None:
    with patch.object(
        Path,
        "stat",
        side_effect=OSError("namespace unavailable"),
    ) as path_stat:
        provider = NetnsProvider(
            Path("/virtual/cgroup"),
            proc_root=Path("/virtual/proc"),
        )

    assert provider.host_netns_id is None
    assert path_stat.call_count == 1
    returned = provider.status()
    assert returned == {
        "loaded": True,
        "attached": False,
        "last_read": None,
        "errors": [],
    }
    returned["loaded"] = False
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": None,
        "errors": [],
    }


def test_netns_pid_reader_skips_invalid_lines(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.procs").write_text("101\ninvalid\n\n202\n")
    provider = NetnsProvider(
        cgroup_root,
        proc_root=tmp_path / "proc",
        host_netns_id=99,
    )

    assert provider._read_pids("") == (101, 202)


def test_netns_candidate_returns_none_without_device_file(
    tmp_path: Path,
) -> None:
    provider = NetnsProvider(
        tmp_path / "cgroup",
        proc_root=tmp_path / "proc",
        host_netns_id=99,
    )

    assert provider._candidate_from_pid(101, 10) is None


def test_bpf_collect_rejects_invalid_snapshot_shape(
    tmp_path: Path,
) -> None:
    (tmp_path / "snapshot.json").write_text(
        json.dumps({"maps": [], "cgroup_map": {}})
    )
    provider = BpfProvider(tmp_path)
    with patch("topos.providers.net_bpf.time.time", return_value=20.0):
        result = provider.collect({"alpha": entity("alpha")})

    reason = f"invalid BPF snapshot shape at {tmp_path / 'snapshot.json'}"
    assert result == {}
    assert provider.status() == {
        "loaded": False,
        "attached": False,
        "last_read": 20.0,
        "errors": [reason],
    }


def test_bpf_collect_skips_invalid_mappings_and_rows(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "maps": {
                    "not-a-list": "ignored",
                    "rows": [
                        "not-a-dict",
                        {
                            "cgroup_id": 3,
                            "direction": "ingress",
                            "bytes": 30,
                            "packets": 3,
                        },
                    ],
                },
                "cgroup_map": {
                    "1": 42,
                    "invalid": "alpha",
                    "2": "beta",
                },
            }
        )
    )
    provider = BpfProvider(tmp_path)
    with patch("topos.providers.net_bpf.time.time", return_value=21.0):
        result = provider.collect({"beta": entity("beta")})

    assert result == {
        "beta": unavailable_sample(
            "no BPF counters found for mapped cgroup ids"
        ),
    }
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 21.0,
        "errors": [],
        "entities_seen": 1,
        "entities_with_bpf": 0,
        "snapshot_path": str(snapshot_path),
        "state_dir": str(tmp_path),
    }


def test_bpf_snapshot_read_error_is_reported_exactly() -> None:
    state_dir = Path("/virtual/bpf")
    snapshot_path = state_dir / "snapshot.json"
    provider = BpfProvider(state_dir)
    with (
        patch("topos.providers.net_bpf.time.time", return_value=22.0),
        patch.object(Path, "exists", return_value=True) as exists,
        patch.object(
            Path,
            "read_text",
            side_effect=OSError("permission denied"),
        ) as read_text,
    ):
        result = provider.collect({"alpha": entity("alpha")})

    assert result == {}
    assert provider.status() == {
        "loaded": False,
        "attached": False,
        "last_read": 22.0,
        "errors": [
            "read error: permission denied",
            f"failed to parse BPF snapshot at {snapshot_path}",
        ],
    }
    exists.assert_called_once_with()
    read_text.assert_called_once_with(encoding="utf-8")


def test_bpf_aggregate_returns_none_for_unmatched_entries() -> None:
    provider = BpfProvider(Path("/virtual/bpf"))
    entries = [
        {
            "cgroup_id": 2,
            "direction": "ingress",
            "bytes": 30,
            "packets": 3,
        }
    ]

    assert provider._aggregate_for_entity("alpha", [1], entries) is None


def test_bpf_aggregate_ignores_invalid_cgroup_ids() -> None:
    provider = BpfProvider(Path("/virtual/bpf"))
    entries = [
        {
            "direction": "ingress",
            "bytes": 10,
            "packets": 1,
        },
        {
            "cgroup_id": "1",
            "direction": "egress",
            "bytes": 20,
            "packets": 2,
        },
        {
            "cgroup_id": 1,
            "direction": "ingress",
            "family": "ipv4",
            "proto": "tcp",
            "bytes": 30,
            "packets": 3,
        },
    ]

    assert provider._aggregate_for_entity("alpha", [1], entries) == NetSample(
        rx_bytes=30,
        tx_bytes=0,
        rx_pkts=3,
        tx_pkts=0,
        proto={"family": {"ipv4": {"tcp": 3}}},
        source_label="net:BPF",
        confidence="exact",
        aggregation="exact",
        unavailable_reason=None,
    )
