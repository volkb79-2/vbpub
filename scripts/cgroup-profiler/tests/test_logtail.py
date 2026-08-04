"""Tests for lib.logtail: turning a container's own log lines into phase
marks. Nothing here starts a real container or calls the real ``docker``
binary — ``subprocess.Popen`` is always monkeypatched to a fake that feeds
canned bytes, and nothing here sleeps: synchronization with the tailer's
background thread is done with ``threading.Event``/``Thread.join``, which
return as soon as the real condition is met rather than after a fixed delay.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

import pytest

from lib import logtail
from lib import phases as phases_mod


# ── test doubles for the docker-logs child process ──────────────────────────

class FakeStdout:
    """Feeds pre-canned lines, then either raises, blocks, or returns EOF."""

    def __init__(self, lines=(), block_after: bool = False, raise_after: Optional[BaseException] = None,
                 stdout_is_none: bool = False):
        self._items = list(lines)
        self._i = 0
        self._block_after = block_after
        self._raise_after = raise_after
        self._queue: "queue.Queue" = queue.Queue()
        self.entered = threading.Event()

    def readline(self) -> bytes:
        self.entered.set()
        if self._i < len(self._items):
            item = self._items[self._i]
            self._i += 1
            return item
        if self._raise_after is not None:
            raise self._raise_after
        if self._block_after:
            return self._queue.get()
        return b""

    def close_pipe(self) -> None:
        self._queue.put(b"")


class FakePopen:
    def __init__(
        self,
        command,
        lines=(),
        block_after: bool = False,
        raise_after: Optional[BaseException] = None,
        exit_code: Optional[int] = None,
        terminate_raises: Optional[BaseException] = None,
        kill_raises: Optional[BaseException] = None,
        wait_effects: Optional[list] = None,
        no_stdout: bool = False,
    ):
        self.command = command
        self.stdout = None if no_stdout else FakeStdout(
            lines=lines, block_after=block_after, raise_after=raise_after,
        )
        self.returncode = exit_code
        self.terminated = False
        self.killed = False
        self._terminate_raises = terminate_raises
        self._kill_raises = kill_raises
        self._wait_effects = list(wait_effects or [])

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self._terminate_raises is not None:
            raise self._terminate_raises
        if self.returncode is None:
            self.returncode = -15
        if self.stdout is not None:
            self.stdout.close_pipe()

    def kill(self):
        self.killed = True
        if self._kill_raises is not None:
            raise self._kill_raises
        self.returncode = -9
        if self.stdout is not None:
            self.stdout.close_pipe()

    def wait(self, timeout=None):
        if self._wait_effects:
            effect = self._wait_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            return effect
        return self.returncode


def install_fake_popen(monkeypatch, **fake_popen_kwargs):
    created: dict = {}
    ready = threading.Event()

    def _factory(command, **kwargs):
        proc = FakePopen(command, **fake_popen_kwargs)
        created["proc"] = proc
        ready.set()
        return proc

    monkeypatch.setattr(logtail.subprocess, "Popen", _factory)
    return created, ready


def _iso(dt_offset_seconds: float = 0.0) -> str:
    import datetime as _dt

    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=dt_offset_seconds)).isoformat()


def line(ts: str, text: str) -> bytes:
    return f"{ts} {text}\n".encode("utf-8")


# ── parse_pattern ────────────────────────────────────────────────────────

class TestParsePattern:
    def test_literal_pattern(self):
        p = logtail.parse_pattern("steam-update-started=Checking for available updates")
        assert p.name == "steam-update-started"
        assert p.literal == "Checking for available updates"
        assert p.regex is None
        assert p.repeat is False
        assert p.matches("[2026] Checking for available updates now") is True
        assert p.matches("nope") is False

    def test_regex_pattern_matches_either_alternative(self):
        p = logtail.parse_pattern(
            "steam-update-done=regex:Success! App '3017300' (already up to date|fully installed)"
        )
        assert p.regex is not None
        assert p.literal is None
        assert p.matches("Success! App '3017300' already up to date") is True
        assert p.matches("Success! App '3017300' fully installed") is True
        assert p.matches("Success! App '9999999' fully installed") is False

    def test_world_load_begin_from_the_estate_default(self):
        p = logtail.parse_pattern("world-load-begin=LogLoad: LoadMap:")
        assert p.matches("[2026.08.04] LogLoad: LoadMap: /Game/Maps/Base") is True

    def test_missing_equals_sign_raises(self):
        with pytest.raises(logtail.LogPatternError, match="needs name=pattern"):
            logtail.parse_pattern("no-equals-sign-here")

    def test_empty_text_raises(self):
        with pytest.raises(logtail.LogPatternError, match="empty pattern"):
            logtail.parse_pattern("")

    def test_whitespace_only_text_raises(self):
        with pytest.raises(logtail.LogPatternError, match="empty pattern"):
            logtail.parse_pattern("   ")

    def test_empty_name_raises(self):
        with pytest.raises(logtail.LogPatternError, match="needs a name"):
            logtail.parse_pattern("=some pattern")

    def test_empty_pattern_raises(self):
        with pytest.raises(logtail.LogPatternError, match="no match text"):
            logtail.parse_pattern("name=")

    def test_invalid_regex_raises(self):
        with pytest.raises(logtail.LogPatternError, match="invalid regex"):
            logtail.parse_pattern("broken=regex:(unclosed")

    def test_repeat_suffix_strips_from_name_and_sets_flag(self):
        p = logtail.parse_pattern("world-load-begin@repeat=LogLoad: LoadMap:")
        assert p.name == "world-load-begin"
        assert p.repeat is True

    def test_repeat_suffix_with_regex(self):
        p = logtail.parse_pattern("hb@repeat=regex:heartbeat \\d+")
        assert p.name == "hb"
        assert p.repeat is True
        assert p.matches("heartbeat 42") is True

    def test_leading_and_trailing_whitespace_is_stripped(self):
        p = logtail.parse_pattern("  spaced = value with spaces  ")
        assert p.name == "spaced"
        assert p.literal == " value with spaces"  # only the *name* side is trimmed of surrounding ws

    def test_raw_preserves_the_original_text(self):
        p = logtail.parse_pattern("name=pattern")
        assert p.raw == "name=pattern"


class TestLogPatternMatchesDefensive:
    def test_neither_literal_nor_regex_never_matches(self):
        # Not reachable through parse_pattern, but matches() must not raise
        # for a hand-built pattern with both unset.
        p = logtail.LogPattern(name="x", raw="x=?")
        assert p.matches("anything") is False


# ── load_patterns ────────────────────────────────────────────────────────

class TestLoadPatterns:
    def test_the_estates_default_set(self, tmp_path: Path):
        path = tmp_path / "patterns.txt"
        path.write_text(
            "steam-update-started=Checking for available updates\n"
            "steam-update-done=regex:Success! App '3017300' (already up to date|fully installed)\n"
            "world-load-begin=LogLoad: LoadMap:\n"
        )
        patterns = logtail.load_patterns(str(path))
        assert [p.name for p in patterns] == [
            "steam-update-started", "steam-update-done", "world-load-begin",
        ]
        assert patterns[1].regex is not None

    def test_empty_file_yields_no_patterns(self, tmp_path: Path):
        path = tmp_path / "empty.txt"
        path.write_text("")
        assert logtail.load_patterns(str(path)) == []

    def test_comments_and_blank_lines_are_skipped(self, tmp_path: Path):
        path = tmp_path / "comments.txt"
        path.write_text("# nothing to see here\n\n   \n# still nothing\n")
        assert logtail.load_patterns(str(path)) == []

    def test_comments_interleaved_with_real_patterns(self, tmp_path: Path):
        path = tmp_path / "mixed.txt"
        path.write_text("# header\nstart=hello\n\n# mid comment\ndone=regex:bye\n")
        patterns = logtail.load_patterns(str(path))
        assert [p.name for p in patterns] == ["start", "done"]

    def test_a_malformed_line_raises(self, tmp_path: Path):
        path = tmp_path / "bad.txt"
        path.write_text("good=value\nno-equals-sign\n")
        with pytest.raises(logtail.LogPatternError):
            logtail.load_patterns(str(path))

    def test_missing_file_raises_oserror(self, tmp_path: Path):
        with pytest.raises(OSError):
            logtail.load_patterns(str(tmp_path / "does-not-exist.txt"))


# ── timestamp parsing ────────────────────────────────────────────────────

class TestRfc3339ToEpoch:
    def test_z_suffix_with_nanoseconds(self):
        epoch = logtail._rfc3339_to_epoch("2026-08-04T19:43:44.123456789Z")
        assert epoch == pytest.approx(1785872624.123456, abs=1e-3)

    def test_explicit_offset(self):
        epoch_utc = logtail._rfc3339_to_epoch("2026-08-04T19:43:44Z")
        epoch_offset = logtail._rfc3339_to_epoch("2026-08-04T21:43:44+02:00")
        assert epoch_offset == pytest.approx(epoch_utc, abs=1e-6)

    def test_no_tzinfo_assumes_utc(self):
        epoch_naive = logtail._rfc3339_to_epoch("2026-08-04T19:43:44")
        epoch_utc = logtail._rfc3339_to_epoch("2026-08-04T19:43:44Z")
        assert epoch_naive == pytest.approx(epoch_utc, abs=1e-6)

    def test_no_t_is_not_a_timestamp(self):
        assert logtail._rfc3339_to_epoch("not-a-timestamp") is None

    def test_shape_like_a_timestamp_but_invalid_date(self):
        assert logtail._rfc3339_to_epoch("2026-13-99T99:99:99Z") is None


class TestSplitTimestamp:
    def test_normal_line(self):
        epoch, rest = logtail._split_timestamp("2026-08-04T19:43:44.000000000Z hello world")
        assert epoch is not None
        assert rest == "hello world"

    def test_timestamp_with_nothing_after_it_is_empty_rest(self):
        epoch, rest = logtail._split_timestamp("2026-08-04T19:43:44.000000000Z ")
        assert epoch is not None
        assert rest == ""

    def test_no_whitespace_at_all_is_no_timestamp(self):
        epoch, rest = logtail._split_timestamp("2026-08-04T19:43:44.000000000Z")
        assert epoch is None
        assert rest == "2026-08-04T19:43:44.000000000Z"

    def test_leading_token_that_is_not_a_timestamp(self):
        epoch, rest = logtail._split_timestamp("hello world, no timestamp here")
        assert epoch is None
        assert rest == "hello world, no timestamp here"


# ── LogTailer._handle_line (direct, no thread) ──────────────────────────

class TestHandleLine:
    def _tailer(self, tmp_path, patterns, **kw):
        return logtail.LogTailer("mycontainer", patterns, str(tmp_path), docker="docker", **kw)

    def test_literal_match_emits_a_mark_with_the_logs_own_timestamp(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("started=hello")]
        tailer = self._tailer(tmp_path, patterns)
        tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", "hello world"))
        assert tailer.matched == [(pytest.approx(1785872624.0, abs=1.0), "started")]
        marks = phases_mod.load_marks(str(tmp_path))
        assert len(marks) == 1
        assert marks[0].name == "started"
        assert marks[0].source == "log"
        assert marks[0].meta["source_container"] == "mycontainer"
        assert marks[0].meta["line"] == "hello world"
        assert "approx_time" not in marks[0].meta

    def test_no_timestamp_falls_back_to_wall_clock_and_flags_approx(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(logtail.time, "time", lambda: 1_700_000_000.0)
        patterns = [logtail.parse_pattern("started=hello")]
        tailer = self._tailer(tmp_path, patterns)
        tailer._handle_line(b"hello world, no docker timestamp\n")
        assert tailer.matched == [(1_700_000_000.0, "started")]
        marks = phases_mod.load_marks(str(tmp_path))
        assert marks[0].meta["approx_time"] is True
        assert marks[0].t == 1_700_000_000.0

    def test_first_match_wins_by_default(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("world-load-begin=LogLoad: LoadMap:")]
        tailer = self._tailer(tmp_path, patterns)
        for _ in range(400):
            tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", "LogLoad: LoadMap: /Game/Base"))
        assert tailer.matched == [(pytest.approx(1785872624.0, abs=1.0), "world-load-begin")]
        assert len(phases_mod.load_marks(str(tmp_path))) == 1

    def test_repeat_records_every_occurrence(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("heartbeat@repeat=regex:registe server .* succeed")]
        tailer = self._tailer(tmp_path, patterns)
        for _ in range(5):
            tailer._handle_line(
                line("2026-08-04T19:43:44.000000000Z", "[SERVER_LIST] registe server x succeed.")
            )
        assert len(tailer.matched) == 5
        assert len(phases_mod.load_marks(str(tmp_path))) == 5

    def test_multiple_patterns_on_the_same_line_all_fire(self, tmp_path: Path):
        patterns = [
            logtail.parse_pattern("a=hello"),
            logtail.parse_pattern("b=world"),
        ]
        tailer = self._tailer(tmp_path, patterns)
        tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", "hello world"))
        assert {name for _, name in tailer.matched} == {"a", "b"}

    def test_a_pattern_that_never_matches_never_fabricates_a_mark(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("never=this substring never appears")]
        tailer = self._tailer(tmp_path, patterns)
        for text in ("hello", "world", "nothing relevant", ""):
            tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", text))
        assert tailer.matched == []
        assert phases_mod.load_marks(str(tmp_path)) == []

    def test_blank_line_after_stripping_is_a_noop(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("a=x")]
        tailer = self._tailer(tmp_path, patterns)
        tailer._handle_line(b"\n")
        tailer._handle_line(b"   \n")
        assert tailer.matched == []

    def test_matched_line_text_is_truncated_to_200_chars(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("a=needle")]
        tailer = self._tailer(tmp_path, patterns)
        long_text = "x" * 300 + "needle" + "y" * 300
        tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", long_text))
        marks = phases_mod.load_marks(str(tmp_path))
        assert len(marks[0].meta["line"]) == 200

    def test_cjk_bytes_decode_and_match_correctly(self, tmp_path: Path):
        # The real production server (soulmask) logs Chinese text.
        patterns = [logtail.parse_pattern("ready=服务器已启动")]
        tailer = self._tailer(tmp_path, patterns)
        tailer._handle_line(line("2026-08-04T19:43:44.000000000Z", "状态: 服务器已启动，一切正常"))
        assert tailer.matched == [(pytest.approx(1785872624.0, abs=1.0), "ready")]
        marks = phases_mod.load_marks(str(tmp_path))
        assert "服务器已启动" in marks[0].meta["line"]

    def test_non_utf8_bytes_are_replaced_not_fatal(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("a=totally unrelated")]
        tailer = self._tailer(tmp_path, patterns)
        raw = "2026-08-04T19:43:44.000000000Z bad byte: ".encode("utf-8") + b"\xff\xfe" + b" end\n"
        tailer._handle_line(raw)  # must not raise
        assert tailer.matched == []  # garbled text does not spuriously match

    def test_non_utf8_bytes_can_still_match_the_surrounding_literal_text(self, tmp_path: Path):
        patterns = [logtail.parse_pattern("a=bad byte")]
        tailer = self._tailer(tmp_path, patterns)
        raw = "2026-08-04T19:43:44.000000000Z bad byte: ".encode("utf-8") + b"\xff\xfe" + b" end\n"
        tailer._handle_line(raw)
        assert len(tailer.matched) == 1


# ── LogTailer construction defaults ──────────────────────────────────────

class TestLogTailerConstruction:
    def test_label_defaults_to_the_container_ref(self, tmp_path: Path):
        tailer = logtail.LogTailer("my-container", [], str(tmp_path), docker="docker")
        assert tailer.label == "my-container"

    def test_explicit_label_is_kept(self, tmp_path: Path):
        tailer = logtail.LogTailer("abc123", [], str(tmp_path), label="soulmask", docker="docker")
        assert tailer.label == "soulmask"

    def test_docker_binary_defaults_via_access(self, tmp_path: Path, monkeypatch):
        from lib import access

        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        tailer = logtail.LogTailer("c", [], str(tmp_path))
        assert tailer.docker == "/usr/bin/docker"

    def test_docker_binary_falls_back_to_the_bare_name_when_not_found(self, tmp_path: Path, monkeypatch):
        from lib import access

        monkeypatch.setattr(access, "docker_bin", lambda: None)
        tailer = logtail.LogTailer("c", [], str(tmp_path))
        assert tailer.docker == "docker"


# ── LogTailer.start()/stop() through a fake docker-logs child ──────────────

class TestLogTailerStartStop:
    def test_processes_canned_lines_then_exits_cleanly_at_eof(self, tmp_path: Path, monkeypatch):
        lines = [
            line("2026-08-04T19:43:44.000000000Z", "Checking for available updates"),
            line("2026-08-04T19:44:00.000000000Z", "LogLoad: LoadMap: /Game/Base"),
        ]
        created, ready = install_fake_popen(monkeypatch, lines=lines, exit_code=None)
        patterns = [
            logtail.parse_pattern("steam-update-started=Checking for available updates"),
            logtail.parse_pattern("world-load-begin=LogLoad: LoadMap:"),
        ]
        tailer = logtail.LogTailer("c", patterns, str(tmp_path), docker="docker")
        tailer.start()
        assert tailer._thread.join(timeout=5.0) is None
        assert not tailer._thread.is_alive()
        assert tailer.error is None
        assert [name for _, name in tailer.matched] == ["steam-update-started", "world-load-begin"]
        assert created["proc"].terminated is True  # finally-block reap on natural EOF

    def test_container_exits_mid_tail_with_no_output_is_not_an_error(self, tmp_path: Path, monkeypatch):
        install_fake_popen(monkeypatch, lines=[], exit_code=0)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is None
        assert tailer.matched == []

    def test_docker_binary_missing_is_swallowed(self, tmp_path: Path, monkeypatch):
        def raise_not_found(command, **kwargs):
            raise FileNotFoundError("no such file: docker")

        monkeypatch.setattr(logtail.subprocess, "Popen", raise_not_found)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is not None
        assert "could not start" in tailer.error
        tailer.stop()  # must not raise even though _proc was never set

    def test_read_loop_exception_is_caught_and_recorded(self, tmp_path: Path, monkeypatch):
        install_fake_popen(monkeypatch, lines=[], raise_after=RuntimeError("pipe exploded"))
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is not None
        assert "stopped early" in tailer.error

    def test_missing_stdout_is_handled_defensively(self, tmp_path: Path, monkeypatch):
        install_fake_popen(monkeypatch, no_stdout=True)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is not None

    def test_stop_reaps_a_child_blocked_in_read(self, tmp_path: Path, monkeypatch):
        created, ready = install_fake_popen(monkeypatch, lines=[], block_after=True, exit_code=None)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0), "background thread never called Popen()"
        proc = created["proc"]
        assert proc.stdout.entered.wait(timeout=5.0), "background thread never reached readline()"
        tailer.stop(timeout=5.0)
        assert proc.terminated is True
        assert not tailer._thread.is_alive()

    def test_stop_before_start_is_a_safe_noop(self, tmp_path: Path):
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.stop()  # no _proc, no _thread

    def test_stop_is_idempotent(self, tmp_path: Path, monkeypatch):
        install_fake_popen(monkeypatch, lines=[], exit_code=0)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        tailer.stop()
        tailer.stop()

    def test_stop_kills_when_terminate_does_not_stop_it_in_time(self, tmp_path: Path, monkeypatch):
        created, ready = install_fake_popen(
            monkeypatch, lines=[], block_after=True, exit_code=None,
            wait_effects=[subprocess.TimeoutExpired(cmd="docker", timeout=5.0)],
        )
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0)
        proc = created["proc"]
        assert proc.stdout.entered.wait(timeout=5.0)
        tailer.stop(timeout=0.5)
        assert proc.killed is True

    def test_stop_swallows_a_second_wait_timeout_after_kill(self, tmp_path: Path, monkeypatch):
        created, ready = install_fake_popen(
            monkeypatch, lines=[], block_after=True, exit_code=None,
            wait_effects=[
                subprocess.TimeoutExpired(cmd="docker", timeout=0.5),
                subprocess.TimeoutExpired(cmd="docker", timeout=0.5),
            ],
        )
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0)
        assert created["proc"].stdout.entered.wait(timeout=5.0)
        tailer.stop(timeout=0.5)  # must not raise even though neither wait() ever completed
        assert created["proc"].killed is True

    def test_stop_swallows_a_terminate_that_raises_oserror(self, tmp_path: Path, monkeypatch):
        created, ready = install_fake_popen(
            monkeypatch, lines=[], block_after=True, exit_code=None,
            terminate_raises=OSError("already reaped"),
        )
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0)
        assert created["proc"].stdout.entered.wait(timeout=5.0)
        tailer.stop(timeout=1.0)  # must not raise
        assert created["proc"].terminated is True

    def test_stop_swallows_a_kill_that_raises_oserror(self, tmp_path: Path, monkeypatch):
        created, ready = install_fake_popen(
            monkeypatch, lines=[], block_after=True, exit_code=None,
            wait_effects=[subprocess.TimeoutExpired(cmd="docker", timeout=0.5)],
            kill_raises=OSError("already gone"),
        )
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0)
        assert created["proc"].stdout.entered.wait(timeout=5.0)
        tailer.stop(timeout=0.5)  # must not raise
        assert created["proc"].killed is True

    def test_run_finally_skips_terminate_when_already_exited(self, tmp_path: Path, monkeypatch):
        # The process reached EOF *and* had already exited on its own by the
        # time the loop notices — the finally block must not re-terminate
        # something that is already gone.
        install_fake_popen(monkeypatch, lines=[], exit_code=0)
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is None

    def test_run_finally_swallows_an_exception_while_reaping(self, tmp_path: Path, monkeypatch):
        install_fake_popen(
            monkeypatch, lines=[], exit_code=None,
            wait_effects=[RuntimeError("wait() blew up")],
        )
        tailer = logtail.LogTailer("c", [], str(tmp_path), docker="docker")
        tailer.start()
        tailer._thread.join(timeout=5.0)
        assert tailer.error is None  # the read loop itself succeeded; only the reap glitched

    def test_stop_requested_flag_stops_the_loop_before_the_next_line(self, tmp_path: Path, monkeypatch):
        # If should-stop is already set by the time a line arrives, that
        # line must not be processed — covers the loop's own stop check,
        # distinct from unblocking a read via terminate().
        patterns = [logtail.parse_pattern("a=stop-me-before-this")]
        created, ready = install_fake_popen(monkeypatch, lines=[], block_after=True, exit_code=None)
        tailer = logtail.LogTailer("c", patterns, str(tmp_path), docker="docker")
        tailer.start()
        assert ready.wait(timeout=5.0)
        proc = created["proc"]
        assert proc.stdout.entered.wait(timeout=5.0)
        tailer._stop_requested.set()
        proc.stdout._queue.put(line("2026-08-04T19:43:44.000000000Z", "stop-me-before-this"))
        tailer._thread.join(timeout=5.0)
        assert tailer.matched == []
