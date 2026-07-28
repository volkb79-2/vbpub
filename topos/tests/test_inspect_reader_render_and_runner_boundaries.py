"""Pure boundary contracts for bounded inspect-files rendering and journald."""

from __future__ import annotations

import io
import subprocess
from unittest.mock import Mock

from topos.inspect_files.catalog import InspectFilesKind
from topos.inspect_files.reader import (
    InspectFilesReadResult,
    _bounded_read,
    _bound_rendered_text,
    _default_journald_runner,
)


def test_bounded_read_does_not_consume_a_later_file_after_aggregate_cap() -> None:
    """Aggregate exhaustion must withhold later content without advancing it."""
    later_file = io.BytesIO(b"must remain unread\n")

    content, byte_cut, line_cut, bytes_used, lines_used = _bounded_read(
        later_file,
        max_bytes=10,
        max_lines=5,
        aggregate_bytes=10,
    )

    assert content == ""
    assert byte_cut is True
    assert line_cut is False
    assert (bytes_used, lines_used) == (10, 0)
    assert later_file.tell() == 0


def test_bounded_read_replaces_c1_controls_without_changing_safe_layout() -> None:
    """C1 terminal controls cannot be replayed, while tab and newline survive."""
    content, byte_cut, line_cut, _, _ = _bounded_read(
        io.BytesIO(b"safe\tfield\x85next\n"), max_bytes=64, max_lines=4
    )

    assert content == "safe\tfield\ufffdnext\n"
    assert byte_cut is False
    assert line_cut is False


def test_rendered_text_reports_the_first_unrepresentable_unicode_character() -> None:
    """The public byte cap never splits a UTF-8 character or overstates content."""
    content, byte_cut, line_cut = _bound_rendered_text(
        "ok\n\ufffd", max_bytes=4, max_lines=2
    )

    assert content == "ok\n"
    assert byte_cut is True
    assert line_cut is False
    assert len(content.encode("utf-8")) <= 4


def test_read_result_text_exposes_both_truncation_causes_before_content() -> None:
    """Human output makes byte and line withholding visible to an operator."""
    result = InspectFilesReadResult(
        kind=InspectFilesKind.DOCKER_JSON_LOG,
        target="a" * 64,
        kind_label="Docker JSON log",
        description="bounded fixture",
        path="/safe/log",
        content="safe body\n",
        truncated_bytes=True,
        truncated_lines=True,
    )

    rendered = result.to_text()

    assert "[TRUNCATED: byte limit exceeded]" in rendered
    assert "[TRUNCATED: line limit exceeded]" in rendered
    assert rendered.index("[TRUNCATED: byte") < rendered.index("safe body")


def test_default_journald_runner_turns_timeout_into_a_typed_safe_result(
    monkeypatch,
) -> None:
    """A subprocess timeout returns no partial output and no raised transport error."""
    run = Mock(side_effect=subprocess.TimeoutExpired(["/usr/bin/journalctl"], 1))
    monkeypatch.setattr("topos.inspect_files.reader.subprocess.run", run)

    result = _default_journald_runner(("/usr/bin/journalctl", "--no-pager"), timeout=1)

    assert result.timed_out is True
    assert result.returncode is None
    assert result.stdout == ""
    assert result.stderr == ""
    assert run.call_args.kwargs["shell"] is False


def test_default_journald_runner_turns_oserror_into_non_timeout_result(
    monkeypatch,
) -> None:
    """Missing journalctl is represented as bounded diagnostics, never an exception."""
    monkeypatch.setattr(
        "topos.inspect_files.reader.subprocess.run",
        Mock(side_effect=OSError("journalctl unavailable")),
    )

    result = _default_journald_runner(("/usr/bin/journalctl", "--no-pager"), timeout=1)

    assert result.timed_out is False
    assert result.returncode is None
    assert result.stdout == ""
    assert result.stderr == "OSError: journalctl unavailable"
