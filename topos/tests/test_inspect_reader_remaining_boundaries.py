"""Remaining safety contracts for inspect-files reader boundaries.

Every test controls the filesystem or subprocess boundary; none reads host
files or invokes journalctl.
"""

from __future__ import annotations

import io
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

import topos.inspect_files.reader as reader
from topos.inspect_files.catalog import InspectFilesKind
from topos.inspect_files.reader import (
    InspectFilesReadError,
    InspectFilesReadResult,
    _JournaldRunResult,
    _bounded_read,
    _confine_and_open,
    _default_journald_runner,
    build_inspect_read,
)


def test_confined_open_turns_unopenable_allow_root_into_a_safe_value_error(
    tmp_path: Path,
) -> None:
    """A vanished allow-root cannot fall back to an arbitrary path open."""
    missing_root = tmp_path / "missing-root"
    with pytest.raises(ValueError, match="cannot open allow_root"):
        _confine_and_open(missing_root / "secret", missing_root)


def test_confined_open_rejects_an_internal_parent_component_even_if_relpath_lies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth rejects an unsafe descriptor component before opening it."""
    allow_root = tmp_path / "allow"
    (allow_root / "safe").mkdir(parents=True)
    (allow_root / "safe" / "visible").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(reader.os.path, "relpath", lambda *_args: "safe/../visible")

    with pytest.raises(ValueError, match="not under"):
        _confine_and_open(allow_root / "safe" / "visible", allow_root)


def test_bounded_read_sanitizes_a_valid_utf8_c1_control_character() -> None:
    """A C1 control encoded as valid UTF-8 cannot be replayed to a terminal."""
    content, byte_cut, line_cut, _, _ = _bounded_read(
        io.BytesIO(b"safe\xc2\x85next\n"), max_bytes=64, max_lines=4
    )

    assert content == "safe\ufffdnext\n"
    assert byte_cut is False
    assert line_cut is False


def test_default_journald_runner_returns_decoded_successful_process_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixed runner returns bounded decoded output from its fixed argv."""
    run = Mock(
        return_value=types.SimpleNamespace(
            stdout=b"entry\xff\n", stderr=b"warning\n", returncode=0
        )
    )
    monkeypatch.setattr(reader.subprocess, "run", run)

    result = _default_journald_runner(("/usr/bin/journalctl", "--no-pager"), timeout=2)

    assert result == _JournaldRunResult(
        stdout="entry\ufffd\n", stderr="warning\n", returncode=0, timed_out=False
    )
    assert run.call_args.args[0] == ["/usr/bin/journalctl", "--no-pager"]
    assert run.call_args.kwargs["shell"] is False


def test_invalid_kind_stays_typed_for_early_limit_and_gate_refusals(tmp_path: Path) -> None:
    """Unknown kinds do not become authorized merely because another guard fails."""
    invalid_limit = build_inspect_read(
        "not-a-kind", "target", max_bytes=0, fixture_root=tmp_path, is_root=lambda: True
    )
    disabled = build_inspect_read(
        "not-a-kind", "target", inspect_files=False, admin=False,
        fixture_root=tmp_path, is_root=lambda: True,
    )

    assert isinstance(invalid_limit, InspectFilesReadError)
    assert invalid_limit.kind is None
    assert "max_bytes" in invalid_limit.error
    assert disabled.kind is None
    assert disabled.mode == "disabled"


def test_catalog_entry_without_a_content_reader_is_refused_before_any_path_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future catalog kind lacking a reader fails closed rather than guessing a path."""
    unsupported = object()
    # ``SimpleNamespace`` is not callable; use a tiny callable proxy while
    # retaining the existing enum constants the dispatcher compares against.
    class _Kinds:
        DOCKER_JSON_LOG = InspectFilesKind.DOCKER_JSON_LOG
        CGROUP_FILES = InspectFilesKind.CGROUP_FILES
        SYSTEMD_JOURNAL = InspectFilesKind.SYSTEMD_JOURNAL

        def __call__(self, _value: str) -> object:
            return unsupported

    monkeypatch.setattr(reader, "InspectFilesKind", _Kinds())
    monkeypatch.setattr(
        reader, "INSPECT_CATALOG", {unsupported: types.SimpleNamespace(
            kind_label="Future kind", description="must not read"
        )},
    )

    result = build_inspect_read(
        "future-kind", "target", inspect_files=True, admin=True,
        fixture_root=tmp_path, is_root=lambda: True,
    )

    assert isinstance(result, InspectFilesReadError)
    assert "does not support content reads" in result.error


def test_missing_docker_log_returns_typed_error_without_content(tmp_path: Path) -> None:
    """A catalog-resolved but absent Docker log is reported, never fabricated."""
    result = build_inspect_read(
        "docker-json-log", "d" * 64, inspect_files=True, admin=True,
        fixture_root=tmp_path, is_root=lambda: True,
    )

    assert isinstance(result, InspectFilesReadError)
    assert result.kind is InspectFilesKind.DOCKER_JSON_LOG
    assert result.mode == "error"
    assert "allow_root" in result.error


def test_cgroup_renderer_adds_a_separator_after_a_file_without_newline(
    tmp_path: Path,
) -> None:
    """Adjacent cgroup files remain distinct even when source files omit newlines."""
    target = "system.slice/demo.service"
    paths = reader._resolve_cgroup_file_paths(target, fixture_root=tmp_path)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    result = build_inspect_read(
        "cgroup-files", target, inspect_files=True, admin=True,
        fixture_root=tmp_path, is_root=lambda: True,
        max_bytes=32_000, max_lines=1000,
    )

    assert isinstance(result, InspectFilesReadResult)
    assert "memory.current\n\n#" in result.content
    assert "pids.max" in result.content
