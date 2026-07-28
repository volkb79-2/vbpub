"""Final deterministic boundary contracts for :mod:`topos.acceptance`.

These tests deliberately keep the acceptance harness in-process: MCP readiness
and cleanup are driven with process/socket fakes, not a daemon, MCP server, or
real Unix socket.  Each assertion pins an operator-visible fail-closed result.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.acceptance as acceptance


def test_replay_and_source_labels_ignore_empty_or_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty recording and malformed metrics produce honest empty evidence."""

    class _Driver:
        frames: list[object] = []

        @classmethod
        def from_path(cls, _path: Path) -> "_Driver":
            return cls()

    import topos.record.replay as replay

    monkeypatch.setattr(replay, "ReplayDriver", _Driver)
    assert acceptance._run_replay(Path("unused.jsonl")) == {
        "frame_count": 0,
        "first_ts": None,
        "last_ts": None,
    }
    assert acceptance._count_source_labels(
        {
            "host": {"malformed": [1]},
            "entities": {"not-a-frame": "unexpected", "valid": {"metrics": {"cpu": [1, "proc"]}}},
        }
    ) == {"proc": 1}


def test_steady_zero_wall_time_reports_zero_cpu_without_dividing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen timer yields a defined 0% measurement rather than an exception."""
    monkeypatch.setattr(acceptance.resource, "getrusage", lambda _which: SimpleNamespace(
        ru_utime=3.0, ru_stime=2.0, ru_maxrss=7
    ))

    result = acceptance.run_steady(
        samples=1,
        interval_s=0,
        _collect=lambda _root: {"entities": {}},
        _perf_counter=lambda: 10.0,
    )

    assert result.ok is True
    assert result.measurements["wall_s"] == 0.0
    assert result.measurements["cpu_pct"] == 0.0


def test_parsers_and_formatters_omit_invalid_optional_fields() -> None:
    """Unknown UI fields, empty MCP text, and absent frame summary stay absent."""
    assert acceptance._parse_ui_smoke_line("ui smoke ok frames=3 unknown=value") == {"frames": 3}
    assert acceptance._parse_tool_content(SimpleNamespace(content=[SimpleNamespace()])) == {}

    smoke = acceptance.SmokeResult(
        ok=True,
        version="1.0",
        python="3.14 test",
        platform="test",
        checks=[],
        measurements={"wall_s": 1.0, "user_s": 0.0, "sys_s": 0.0, "rss_kb": 1.0},
        frame_summary=None,
    )
    assert "frame_summary" not in json.loads(acceptance.format_json(smoke))
    assert "Frame summary:" not in acceptance.format_text(smoke)

    no_entities = acceptance.SmokeResult(
        **{**smoke.__dict__, "frame_summary": {"schema_version": 1, "ts": 1.0, "interval_s": 1, "entity_count": 0, "host_metric_count": 0, "entity_keys": []}}
    )
    assert "entities:" not in acceptance.format_text(no_entities)


class _Proc:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = iter(polls)
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        value = next(self._polls, self.returncode)
        if value is not None:
            self.returncode = value
        return value

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0


class _RefusingProbe:
    def __enter__(self) -> "_RefusingProbe":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, _address: str) -> None:
        raise OSError("connection refused")


def test_mcp_readiness_refusal_becomes_typed_timeout_without_real_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that exists but refuses connections must never be treated as ready."""
    supplied_socket = tmp_path / "not-a-socket"
    supplied_socket.touch()
    proc = _Proc([None, None])
    monotonic = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(socket, "socket", lambda *_args: _RefusingProbe())
    monkeypatch.setattr(acceptance.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)

    result = acceptance.run_mcp_smoke(socket_path=supplied_socket, timeout_s=0.1)

    assert result.ok is False
    assert result.checks[0].name == "hello"
    assert "did not accept a connection" in result.checks[0].message
    assert proc.terminated is True
    assert supplied_socket.exists(), "caller-owned path must not be removed"


def test_mcp_cleanup_swallows_unlink_failure_for_its_generated_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup failure is non-fatal after a daemon dies before readiness."""
    generated_socket = tmp_path / "topos.sock"
    generated_socket.touch()
    proc = _Proc([7])
    original_unlink = Path.unlink

    def _unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == generated_socket:
            raise OSError("simulated unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(Path, "unlink", _unlink)

    result = acceptance.run_mcp_smoke(timeout_s=0.1)

    assert result.ok is False
    assert result.checks[0].details["daemon_exit_code"] == 7


def test_acceptance_main_fails_closed_for_an_unrecognized_parser_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A future parser/dispatcher mismatch is a typed usage failure, not success."""
    monkeypatch.setattr(acceptance, "build_parser", lambda: SimpleNamespace(
        parse_args=lambda _argv: SimpleNamespace(command="unknown-future-command")
    ))

    assert acceptance.acceptance_main([]) == 2
    assert capsys.readouterr().err == "Unknown command: unknown-future-command\n"


def test_module_entrypoint_exits_with_validation_status_without_live_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m`` delegates through ``main`` and preserves usage exit status."""
    monkeypatch.setattr(sys, "argv", ["topos.acceptance", "mcp-smoke", "--timeout-s", "-1"])
    monkeypatch.delitem(sys.modules, "topos.acceptance")
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("topos.acceptance", run_name="__main__")
    assert raised.value.code == 2
