from __future__ import annotations

import io
import json
import sys

import pytest

from cli_extended import (
    CliIdentity,
    CliOutput,
    CliRuntime,
    ProgressMode,
    ProgressRenderer,
)
from cli_extended.progress import _is_tty

IDENTITY = CliIdentity("TEST", "1", "Test Tool")


@pytest.mark.parametrize("value", ("spinner", "", "jsonl"))
def test_progress_mode_parser_refuses_unknown_values(value):
    with pytest.raises(ValueError, match="invalid progress mode"):
        ProgressMode.parse(value)


def test_progress_tty_probe_handles_missing_and_broken_isatty():
    class BrokenTTY:
        def isatty(self):
            raise OSError("descriptor closed")

    assert not _is_tty(object())
    assert not _is_tty(BrokenTTY())


def test_progress_default_stream_selection_and_auto_tty_detection(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    tty = TTY()
    progress = ProgressRenderer(ProgressMode.AUTO, stream=tty)
    assert progress.mode is ProgressMode.TTY
    progress.update("work")
    progress.finish("done")
    assert "\033[36m[INFO] done\033[0m" in tty.getvalue()

    json_progress = ProgressRenderer(ProgressMode.RAWJSON)
    plain_progress = ProgressRenderer(ProgressMode.PLAIN)
    assert json_progress.stream is sys.stdout
    assert plain_progress.stream is sys.stderr


def test_progress_color_policy_all_modes_and_explicit_overrides(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert ProgressRenderer(ProgressMode.TTY, stream=TTY())._color_enabled()
    assert not ProgressRenderer(
        ProgressMode.TTY, stream=TTY(), color=False
    )._color_enabled()
    assert ProgressRenderer(
        ProgressMode.TTY, stream=io.StringIO(), color=True
    )._color_enabled()
    assert not ProgressRenderer(
        ProgressMode.PLAIN, stream=TTY(), color=True
    )._color_enabled()
    assert not ProgressRenderer(
        ProgressMode.RAWJSON, stream=TTY(), color=True
    )._color_enabled()

    monkeypatch.setenv("NO_COLOR", "")
    assert not ProgressRenderer(ProgressMode.TTY, stream=TTY())._color_enabled()
    assert ProgressRenderer(ProgressMode.TTY, stream=TTY(), color=True)._color_enabled()


def test_progress_json_events_include_only_supplied_counters_and_can_be_raw():
    output = io.StringIO()
    renderer = ProgressRenderer(
        ProgressMode.RAWJSON,
        stream=output,
        secrets=("private",),
    )
    renderer.update("private data")
    renderer.finish("done", current=2, total=3)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rows == [
        {"type": "progress", "message": "<redacted> data", "done": False},
        {
            "type": "progress",
            "message": "done",
            "done": True,
            "current": 2,
            "total": 3,
        },
    ]

    raw = io.StringIO()
    ProgressRenderer(
        ProgressMode.RAWJSON,
        stream=raw,
        secrets=("private",),
        debug_raw=True,
    ).update("private data")
    assert '"message": "private data"' in raw.getvalue()
    raw_finish = ProgressRenderer(
        ProgressMode.RAWJSON,
        stream=raw,
        secrets=("private",),
        debug_raw=True,
    )
    raw_finish.finish("private done")
    assert '"message": "private done"' in raw.getvalue()


def test_progress_plain_counter_requires_both_values_and_exit_cleanup():
    class FlushTrackingStream(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flush_count = 0

        def flush(self):
            self.flush_count += 1
            super().flush()

    output = FlushTrackingStream()
    renderer = ProgressRenderer(ProgressMode.PLAIN, stream=output)
    renderer.update("known", current=1)
    renderer.update("counted", current=1, total=4)
    renderer.finish("done")
    assert "[INFO] known\n" in output.getvalue()
    assert "[INFO] counted (1/4)\n" in output.getvalue()
    assert output.getvalue().endswith("[INFO] done\n")
    assert output.flush_count == 3

    class TTY(io.StringIO):
        def isatty(self):
            return True

    tty = TTY()
    renderer = ProgressRenderer(ProgressMode.TTY, stream=tty)
    renderer.update("working")
    renderer.__exit__(None, None, None)
    assert tty.getvalue().endswith("\n")
    assert not renderer._active


def test_progress_context_without_update_does_not_emit_tty_completion():
    class TTY(io.StringIO):
        def isatty(self):
            return True

    output = TTY()
    with ProgressRenderer(ProgressMode.TTY, stream=output) as progress:
        progress.finish("no-op")
    assert output.getvalue() == ""


def test_runtime_progress_routes_rawjson_and_mutes_it_for_primary_json():
    stdout, stderr = io.StringIO(), io.StringIO()
    runtime = CliRuntime(
        IDENTITY,
        CliOutput(IDENTITY, json_mode=True, stdout=stdout, stderr=stderr),
        json_mode=True,
        progress_mode=ProgressMode.RAWJSON,
    )
    renderer = runtime.progress()
    assert renderer.mode is ProgressMode.QUIET
    assert renderer.stream is stderr
    renderer.update("must not contaminate primary JSON")
    assert stdout.getvalue() == stderr.getvalue() == ""

    plain = runtime.progress(mode="plain")
    assert plain.mode is ProgressMode.PLAIN
    assert plain.stream is stderr
