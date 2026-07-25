"""P108 exact behavioral coverage for the host network provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from topos.model import Entity
from topos.providers.base import NetSample, unavailable_sample
from topos.providers.net_host import (
    NetHostProvider,
    parse_net_dev,
    parse_qdisc_stats,
    parse_snmp_like,
    parse_softnet_stat,
)


ROOT_ENTITY = Entity(key="", kind="root", parent=None)


def test_parse_net_dev_skips_short_and_non_numeric_rows() -> None:
    text = "\n".join(
        (
            "Inter-| Receive | Transmit",
            "eth0: 1 2",
            "eth1: 1 invalid 3 4 5 6 7 8 9 10 11 12 13 14 15 16",
        )
    )

    assert parse_net_dev(text) == {}


def test_parse_softnet_skips_short_and_non_hex_rows() -> None:
    text = "\n".join(
        (
            "00000000 00000001",
            "00000000 invalid 00000003",
            "00000000 00000002 00000003",
        )
    )

    assert parse_softnet_stat(text) == {
        "cpu_count": 1,
        "dropped": 2,
        "time_squeeze": 3,
    }


def test_parse_snmp_like_skips_mismatch_and_non_numeric_value() -> None:
    text = "\n".join(
        (
            "Tcp: A B",
            "Udp: 1 2",
            "Tcp: Good Bad",
            "Tcp: 7 invalid",
        )
    )

    assert parse_snmp_like(text) == {"Tcp": {"Good": 7}}


def test_parse_qdisc_skips_blank_malformed_and_preheader_lines() -> None:
    text = "\n".join(
        (
            "",
            "Sent 99 bytes 9 pkt (dropped 9, overlimits 9)",
            "qdisc malformed",
            "backlog 99b 9p",
            "qdisc fq 0: dev eth0 root",
            "Sent 10 bytes 1 pkt (dropped 2, overlimits 3)",
            "backlog 4b 5p",
        )
    )

    assert parse_qdisc_stats(text) == {
        "eth0": {
            "dropped": 2,
            "overlimits": 3,
            "backlog_bytes": 4,
            "backlog_packets": 5,
        }
    }


def test_collect_without_root_entity_is_exact() -> None:
    provider = NetHostProvider(
        proc_root=Path("/virtual/proc"),
        command_runner=lambda _argv: "",
    )
    with patch("topos.providers.net_host.time.time", return_value=30.0):
        result = provider.collect(
            {"alpha": Entity(key="alpha", kind="scope", parent="")}
        )

    assert result == {}
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 30.0,
        "errors": [],
    }


def test_collect_without_net_dev_is_exact(tmp_path: Path) -> None:
    provider = NetHostProvider(
        proc_root=tmp_path,
        command_runner=lambda _argv: "",
    )
    with patch("topos.providers.net_host.time.time", return_value=31.0):
        result = provider.collect({"": ROOT_ENTITY})

    assert result == {
        "": unavailable_sample(
            "missing /proc/net/dev",
            source_label="net:HOST",
            confidence="n/a",
        )
    }
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 31.0,
        "errors": ["/proc/net/dev:unavail_kernel"],
    }


def test_collect_with_missing_auxiliary_files_is_exact(
    tmp_path: Path,
) -> None:
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    (net_dir / "dev").write_text(
        "eth0: 10 2 0 0 0 0 0 0 20 3 0 0 0 0 0 0\n"
    )
    provider = NetHostProvider(
        proc_root=tmp_path,
        command_runner=lambda _argv: "",
    )
    with patch("topos.providers.net_host.time.time", return_value=32.0):
        result = provider.collect({"": ROOT_ENTITY})

    protocols = {
        "tcp": {
            "retrans_segs": None,
            "out_rsts": None,
            "timeouts": None,
            "syn_retrans": None,
        },
        "udp": {
            "in_errors": None,
            "rcvbuf_errors": None,
            "sndbuf_errors": None,
        },
    }
    assert result == {
        "": NetSample(
            rx_bytes=10,
            tx_bytes=20,
            rx_pkts=2,
            tx_pkts=3,
            proto=protocols,
            source_label="net:HOST",
            confidence="exact",
            aggregation="exact",
            unavailable_reason=None,
        )
    }
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": 32.0,
        "errors": [
            "/proc/net/softnet_stat:unavail_kernel",
            "/proc/net/snmp:unavail_kernel",
            "/proc/net/netstat:unavail_kernel",
        ],
        "interfaces": {
            "eth0": {
                "rx_bytes": 10,
                "rx_pkts": 2,
                "rx_errs": 0,
                "rx_drop": 0,
                "tx_bytes": 20,
                "tx_pkts": 3,
                "tx_errs": 0,
                "tx_drop": 0,
            }
        },
        "softnet": None,
        "protocols": protocols,
        "qdisc": {},
    }


def test_qdisc_runner_failure_is_exact() -> None:
    def missing_tc(_argv: list[str]) -> str:
        raise FileNotFoundError("tc")

    provider = NetHostProvider(
        proc_root=Path("/virtual/proc"),
        command_runner=missing_tc,
    )

    assert provider._read_qdisc() is None
    assert provider.status() == {
        "loaded": True,
        "attached": False,
        "last_read": None,
        "errors": ["tc:FileNotFoundError"],
    }
