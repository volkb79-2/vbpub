"""Small deterministic contracts for otherwise-uncommon public boundary paths."""

from __future__ import annotations

import io
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.acceptance as acceptance
import topos.inspect_files.reader as reader
from conftest import fixture_frame
from topos.daemon import broker as broker_module
from topos.daemon.broker import FrameBroker, _BrokerHandler, _frame_response
from topos.daemon.client import DaemonClient
from topos.inspect_files.catalog import InspectFilesKind
from topos.inspect_files.reader import InspectFilesReadError, _confine_and_open, build_inspect_read


def test_smoke_main_preserves_the_compatibility_delegate_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The older entry point remains a transparent delegate to the real parser."""
    seen: list[list[str] | None] = []
    monkeypatch.setattr(acceptance, "acceptance_main", lambda argv: seen.append(argv) or 7)

    assert acceptance.smoke_main(["smoke", "--json"]) == 7
    assert seen == [["smoke", "--json"]]


def test_stream_window_without_bounds_and_frame_response_without_sequence() -> None:
    """An unbounded history request has no synthetic gap or cursor field."""
    base = fixture_frame()
    broker = FrameBroker([base])
    batch = broker.stream_window()

    assert [frame for _seq, frame in batch.entries] == [base]
    assert batch.gap is False
    assert _frame_response(base) == {"type": "frame", "frame": broker_module.frame_to_jsonable(base)}


class _TimeoutReader:
    def readline(self, _size: int) -> bytes:
        raise socket.timeout("timed out")


class _Connection:
    def settimeout(self, _timeout: float) -> None:
        return None


def test_broker_handler_maps_a_read_timeout_to_safe_wire_error() -> None:
    """A client that stalls before a request line gets a stable timeout response."""
    handler = object.__new__(_BrokerHandler)
    handler.connection = _Connection()
    handler.rfile = _TimeoutReader()
    handler.wfile = io.BytesIO()
    handler.server = SimpleNamespace(request_timeout_s=0.1, broker=SimpleNamespace())

    handler.handle()

    assert json.loads(handler.wfile.getvalue()) == {"type": "error", "error": "request timed out"}


def test_confined_open_transfers_descriptor_ownership_on_a_regular_file(tmp_path: Path) -> None:
    """A successful confined open returns a live reader after its dir fds close."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "evidence.txt"
    target.write_bytes(b"bounded evidence")

    with _confine_and_open(target, allowed) as opened:
        assert opened.read() == b"bounded evidence"


def test_known_catalog_kind_missing_from_a_future_catalog_is_a_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog drift cannot cause the reader to guess a filesystem path."""
    monkeypatch.setattr(reader, "INSPECT_CATALOG", {})

    result = build_inspect_read(
        InspectFilesKind.DOCKER_JSON_LOG.value,
        "a" * 64,
        inspect_files=True,
        admin=True,
        fixture_root=tmp_path,
        is_root=lambda: True,
    )

    assert isinstance(result, InspectFilesReadError)
    assert result.kind is InspectFilesKind.DOCKER_JSON_LOG
    assert "unknown inspection kind" in result.error


def test_health_error_code_is_retained_when_present() -> None:
    """The client preserves a validated daemon error code for operator diagnosis."""
    components = [
        {
            "name": name,
            "state": "healthy",
            "detail": "ready",
            "last_attempt_ts": None,
            "last_success_ts": None,
            "consecutive_failures": 0,
            "state_change_count": 0,
            "error": None,
        }
        for name in ("collector", "bpf_snapshot_bridge", "paddr_lifecycle")
    ]
    components[0]["error"] = {"message": "collector delayed", "error_code": "retry_later"}
    payload = {"schema_version": 1, "capability": "health-v1", "components": components}

    snapshot = DaemonClient(Path("/fixture/daemon.sock"))._parse_health_payload(payload)

    assert snapshot.snapshots[0].error is not None
    assert snapshot.snapshots[0].error.error_code == "retry_later"
