"""Regression tests for the re-review of 4c7f0fff0 (F1-F6)."""
from __future__ import annotations

import json
import os
import signal
import time

import pytest
from cli_extended import CliIdentity

from fakeqmp import FakeGuest, FakeQmp
from vmscreen import capture, cli, serialtext, sources
from vmscreen.errors import NoExactText, OutputExists, QmpError, ResumeFailed
from vmscreen.qmp import QmpClient

IDENT = CliIdentity("vmscreen", "0.1.0", "exact console text of local QEMU guests", "vmscreen")
NOSLEEP = lambda s: None  # noqa: E731


def run(argv, capsys):
    code = cli.build_cli(IDENT).run(argv=argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def guest():
    g = FakeGuest()
    g.put(0, 0, "Welcome")
    g.put(1, 0, "login:", 0x1F)
    return g


def serve(tmp_path, g, name="q.sock", **kw):
    return FakeQmp(tmp_path / name, g.handlers(), **kw), str(tmp_path / name)


# ---------------------------------------------------------------- F1: the reconnect is part of the retried step
def test_f1_a_failing_first_reconnect_is_retried_and_the_guest_resumed(tmp_path):
    g = guest()
    srv, path = serve(tmp_path, g, silent={"screendump"})
    calls = []

    def reconnect():
        calls.append(1)
        if len(calls) == 1:
            raise QmpError("connection refused")  # what the CLI's connecting factory raises
        return QmpClient(path, 1)
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError, match="read failed"):  # the original error
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), reconnect=reconnect, sleep=NOSLEEP)
    assert g.status == "running" and len(calls) == 2


def test_f1_all_reconnects_failing_keeps_the_original_error_resumes_once_and_names_the_pause(tmp_path):
    g = guest()
    srv, path = serve(tmp_path, g, silent={"screendump"})
    calls = []

    def reconnect():
        calls.append(1)
        raise QmpError("connection refused")
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError) as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), reconnect=reconnect, sleep=NOSLEEP)
    assert "read failed" in str(ei.value) and not isinstance(ei.value, ResumeFailed)
    assert any("PAUSED" in n and "cont" in n for n in ei.value.__notes__)
    assert len(calls) == capture.RESUME_ATTEMPTS  # one resume sequence, never a second one from the finally block


def test_f1_resume_failure_without_an_original_error_is_resumefailed_with_a_cause(tmp_path):
    g = guest()
    h = g.handlers()
    h["cont"] = lambda _: (_ for _ in ()).throw(ValueError("cont refused"))
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 1) as c:
        with pytest.raises(ResumeFailed, match="PAUSED") as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), reconnect=lambda: QmpClient(c.path, 1), sleep=NOSLEEP)
    assert isinstance(ei.value.__cause__, QmpError)


def test_f1_cli_reports_exit_5_with_the_paused_hint(tmp_path, capsys):
    g = guest()
    h = g.handlers()
    h["cont"] = lambda _: (_ for _ in ()).throw(ValueError("cont refused"))
    with FakeQmp(tmp_path / "q.sock", h):
        code, out, err = run(["ocr-pair", "--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path), "--out", str(tmp_path / "z"), "--json"], capsys)
    assert code == 5 and "PAUSED" in json.loads(out)["error"] and "PAUSED" in err


# ---------------------------------------------------------------- F2: an unverified VGA result falls through
def unverified_guest():
    g = guest()
    g.display_offset = 160
    return g


def test_f2_auto_falls_through_to_serial_when_vga_is_unverified(tmp_path):
    log = tmp_path / "s.log"
    log.write_bytes(b"kernel up\r\nlogin: ")
    srv, path = serve(tmp_path, unverified_guest())
    with srv, QmpClient(path, 2) as c:
        res = sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path))
    assert res.source == "serial" and res.exact == "stream" and "login:" in res.text
    assert res.tried[0]["source"] == "vga-text" and not res.tried[0]["ok"] and "unverified" in res.tried[0]["reason"]


def test_f2_unverified_text_is_returned_flagged_only_when_nothing_else_applies(tmp_path):
    srv, path = serve(tmp_path, unverified_guest())
    with srv, QmpClient(path, 2) as c:
        res = sources.read_text(c, dump_dir=str(tmp_path))
        assert res.source == "vga-text" and res.exact == "unverified" and res.tried[-1]["reason"].startswith("returned UNVERIFIED")
        forced = sources.read_text(c, source="vga", dump_dir=str(tmp_path), serial_log=str(tmp_path / "none.log"))
        assert forced.source == "vga-text" and forced.exact == "unverified"


def test_f2_wait_text_and_probe_use_the_serial_fallback(tmp_path, capsys):
    log = tmp_path / "s.log"
    log.write_bytes(b"login: ")
    srv, path = serve(tmp_path, unverified_guest())
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        d = json.loads(run(["wait-text", "login:", *base, "--timeout", "0", "--json"], capsys)[1])
        assert d["source"] == "serial" and d["exact"] == "stream"
        p = json.loads(run(["probe", *base, "--json"], capsys)[1])
        assert p["source"] == "serial" and p["exact_text_available"] is True
        assert json.loads(run(["text", *base, "--json"], capsys)[1])["source"] == "serial"


# ---------------------------------------------------------------- F3: a stale cursor cell does not poison the capture
def cursor_pixels(row, col, scanlines=(14, 15), width=9):
    return {(col * 9 + dx, row * 16 + dy) for dx in range(width) for dy in scanlines}


def test_f3_one_cursor_block_in_a_blank_cell_is_partial_not_exact(tmp_path):
    g = guest()
    g.extra_ink = cursor_pixels(10, 20)  # Linux moved the cursor; the BDA still says (5, 2)
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "partial" and res.verification["mismatched"] == 0
    assert res.verification["uncertain_cells"][0]["row"] == 10 and res.verification["uncertain_cells"][0]["col"] == 20
    assert res.screen.cells[10][20].char == "\ufffd" and res.lines[10].endswith("\ufffd") and res.geometry["uncertain_cells"]


@pytest.mark.parametrize("name,ink", [
    ("two cells", cursor_pixels(10, 20) | cursor_pixels(11, 3)),
    ("a dot, not a cursor", {(20 * 9 + 2, 10 * 16 + 14), (20 * 9 + 3, 10 * 16 + 14)}),
    ("top scanlines", cursor_pixels(10, 20, scanlines=(0, 1))),
    ("glyph-shaped", {(20 * 9 + 1, 10 * 16 + 9), (20 * 9 + 5, 10 * 16 + 12), (20 * 9 + 2, 10 * 16 + 14)}),
])
def test_f3_anything_else_stays_strict(tmp_path, name, ink):
    g = guest()
    g.extra_ink = ink
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "unverified" and "uncertain_cells" not in res.verification, name


# ---------------------------------------------------------------- F4: hostile escape parameters
def test_f4_cursor_parameters_are_clamped():
    t0 = time.monotonic()
    for seq in (b"\x1b[300000000Gx", b"\x1b[999999999Cx", b"\x1b[1;300000000Hx", b"\x1b[" + b"9" * 6000 + b"Gx", b"\x1b[" + b"9" * 6000 + b"Cx"):
        out = serialtext.render(seq)
        assert len(out) <= serialtext.MAX_COL + 1, seq[:20]
    assert len(serialtext.render(b"a" * 10000)) == serialtext.MAX_COL
    assert time.monotonic() - t0 < 5


# ---------------------------------------------------------------- F5: signal handlers are always restored
def test_f5_handlers_restored_even_when_the_resume_raises_something_unexpected(tmp_path, monkeypatch):
    g = guest()
    srv, path = serve(tmp_path, g)

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(capture, "resume_guest", boom)
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(RuntimeError, match="unexpected"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before


# ---------------------------------------------------------------- F6: commit atomicity, boundaries, marks
def test_f6_force_rolls_the_old_files_back_when_the_new_json_cannot_be_committed(tmp_path, monkeypatch):
    g = guest()
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path))
        old_png, old_json = (tmp_path / "keep.png").read_bytes(), (tmp_path / "keep.json").read_bytes()
        g.put(4, 0, "different now")
        real = os.replace

        def failing(src, dst):
            if ".tmp" in str(src) and str(dst).endswith(".json"):
                raise OSError("disk full")
            return real(src, dst)
        monkeypatch.setattr(os, "replace", failing)
        with pytest.raises(OSError, match="disk full"):
            capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path), force=True)
        monkeypatch.undo()
        assert (tmp_path / "keep.png").read_bytes() == old_png and (tmp_path / "keep.json").read_bytes() == old_json
        assert g.status == "running"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


def test_f6_exclusive_creation_closes_the_precheck_race(tmp_path):
    g = guest()
    h = g.handlers()
    inner = h["screendump"]

    def other_writer(a):
        (tmp_path / "race.png").write_bytes(b"someone else's file")  # appears after the pre-check, before the commit
        return inner(a)
    h["screendump"] = other_writer
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(OutputExists, match="appeared"):
            capture.ocr_pair(c, tmp_path / "race", dump_dir=str(tmp_path))
        assert g.status == "running"
    assert (tmp_path / "race.png").read_bytes() == b"someone else's file" and not (tmp_path / "race.json").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".race")]


def test_f6_offset_inside_a_utf8_character_or_escape_sequence_starts_after_it(tmp_path):
    log = tmp_path / "s.log"
    log.write_bytes("abcédef".encode())
    assert sources.read_serial(str(log), from_offset=4).text == "def"  # offset 4 is the second byte of é
    log.write_bytes(b"ok \x1b[31mred\x1b[0m")
    res = sources.read_serial(str(log), from_offset=5)  # inside ESC[31m
    assert res.text == "red" and res.geometry["start"] == 8 and res.geometry["from_offset"] == 5
    assert sources.read_serial(str(log), from_offset=3).text == "red"  # a clean boundary is untouched (ESC starts at 3)


def test_f6_mark_detects_a_log_truncated_and_regrown(tmp_path, capsys):
    log = tmp_path / "s.log"
    log.write_bytes(b"boot one\r\nlogin: ")
    mark = sources.make_mark(str(log))
    log.write_bytes(b"boot one\r\nlogin: \r\nmore")
    assert "more" in sources.read_serial(str(log), mark=mark).text  # same log, grown: fine
    log.write_bytes(b"BOOT TWO LONGER THAN BEFORE\r\nlogin: ")  # same inode, truncated and regrown past the offset
    with pytest.raises(NoExactText, match="truncated or replaced"):
        sources.read_serial(str(log), mark=mark)
    g = FakeGuest(png_size=(1280, 800))
    with FakeQmp(tmp_path / "q.sock", g.handlers()):
        base = ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        assert run(["text", *base, "--mark", mark], capsys)[0] == 4
        assert run(["text", *base, "--mark", "garbage"], capsys)[0] == 2
        log.write_bytes(b"x")
        code, out, _ = run(["mark", "--serial-log", str(log), "--json"], capsys)
        d = json.loads(out)
        assert code == 0 and d["offset"] == 1 and d["mark"].startswith("1.")
    os.replace(log, tmp_path / "s.old")
    log.write_bytes(b"x")  # replaced file, same bytes: new inode
    with pytest.raises(NoExactText, match="truncated or replaced"):
        sources.read_serial(str(log), mark=d["mark"])


def test_f6_incomplete_escape_at_end_of_input_renders_as_nothing():
    assert serialtext.render(b"abc\x1b[31") == "abc"
    assert serialtext.render(b"abc\x1b]0;title") == "abc"
    assert serialtext.render(b"abc\x1b") == "abc"
    assert serialtext.render(b"abc\x1b[31mx") == "abcx"
