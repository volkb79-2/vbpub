"""Regression tests for the third review of 3425c9902."""
from __future__ import annotations

import errno
import json
import os
import pathlib
import signal
import time

import pytest
from cli_extended import CliIdentity

from fakeqmp import FakeGuest, FakeQmp
from vmscreen import capture, cli, serialtext, sources
from vmscreen.errors import NoExactText, QmpError, ResumeFailed, VmscreenError
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


def block(row, col, scanlines, width=9):
    return {(col * 9 + dx, row * 16 + dy) for dx in range(width) for dy in scanlines}


# ---------------------------------------------------------------- 1: a tolerated cursor-like cell is never "verified"
@pytest.mark.parametrize("name,ink", [
    ("underscore, one scanline (14)", block(10, 20, (14,), 8)),
    ("one scanline at the very bottom", block(10, 20, (15,))),
    ("lower half block, eight scanlines", block(10, 20, range(8, 16), 8)),
    ("three scanlines", block(10, 20, (13, 14, 15))),
    ("two scanlines, one too high", block(10, 20, (13, 14))),
    ("two scanlines but a narrow bar", block(10, 20, (14, 15), 4)),
])
def test_1_shapes_outside_the_cursor_geometry_are_unverified(tmp_path, name, ink):
    g = guest()
    g.extra_ink = ink
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "unverified", name


@pytest.mark.parametrize("width", [8, 9])
def test_1_exact_cursor_geometry_is_partial_with_a_placeholder_never_exact_true(tmp_path, width):
    g = guest()
    g.extra_ink = block(10, 20, (14, 15), width)  # also what a one-eighth lower block looks like: cannot be told apart
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "partial" and res.exact is not True
    assert res.verification["uncertain_cells"] == res.geometry["uncertain_cells"] and res.geometry["uncertain_cells"][0]["row"] == 10
    assert res.lines[10].rstrip().endswith("�") and "Welcome" in res.text  # the rest is still there


def test_1_partial_is_not_matched_and_not_captured_without_the_explicit_flag(tmp_path, capsys):
    g = guest()
    g.extra_ink = block(10, 20, (14, 15))
    srv, path = serve(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path)]
        assert run(["wait-text", "Welcome", *base, "--timeout", "0"], capsys)[0] == 3
        assert run(["wait-text", "Welcome", *base, "--timeout", "0", "--accept-partial"], capsys)[0] == 0
        assert run(["ocr-pair", *base, "--out", str(tmp_path / "a")], capsys)[0] == 4 and g.status == "running"
        assert run(["ocr-pair", *base, "--out", str(tmp_path / "b"), "--accept-partial"], capsys)[0] == 0
        probe = json.loads(run(["probe", *base, "--json"], capsys)[1])
    side = json.loads((tmp_path / "b.json").read_text())
    assert side["exact"] == "partial" and side["uncertain_cells"][0]["col"] == 20 and "�" in side["text"]
    assert probe["exact"] == "partial" and probe["exact_text_available"] is False


def test_1_auto_prefers_the_serial_log_over_a_partial_screen(tmp_path):
    g = guest()
    g.extra_ink = block(10, 20, (14, 15))
    log = tmp_path / "s.log"
    log.write_bytes(b"login: ")
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        assert sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path)).source == "serial"


# ---------------------------------------------------------------- 2: marks fingerprint the bytes BEFORE the offset
def test_2_a_restarted_vm_with_the_same_banner_is_detected_by_the_tail_window(tmp_path):
    log = tmp_path / "s.log"
    banner = b"SAME BANNER LINE " * 8
    boot1 = banner + b"\r\n" + b"first boot output " * 8 + b"\r\nlogin: "
    log.write_bytes(boot1)
    mark = sources.make_mark(str(log))
    boot2 = banner + b"\r\n" + b"second boot differs " * 12 + b"\r\nlogin: more"
    assert len(boot2) > len(boot1) and boot2[:64] == boot1[:64]  # identical head, regrown past the offset
    log.write_bytes(boot2)
    with pytest.raises(NoExactText, match="truncated or replaced"):
        sources.read_serial(str(log), mark=mark)
    log.write_bytes(boot1 + b"\r\nnew")
    assert "new" in sources.read_serial(str(log), mark=mark).text  # genuine growth is fine


def test_2_make_mark_reads_two_windows_not_the_whole_log(tmp_path, monkeypatch):
    log = tmp_path / "big.log"
    with open(log, "wb") as f:
        f.truncate(300_000_000)  # sparse
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("whole log read")))
    t0 = time.monotonic()
    token = sources.make_mark(str(log))
    assert time.monotonic() - t0 < 1 and token.startswith("300000000.")
    monkeypatch.undo()
    assert sources.parse_mark(token)[0] == 300_000_000


# ---------------------------------------------------------------- 3: an empty serial result is a failed attempt in auto
def test_3_empty_serial_falls_back_to_the_flagged_vga_text(tmp_path):
    g = guest()
    g.display_offset = 160  # unverified VGA
    log = tmp_path / "s.log"
    log.write_bytes(b"")
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path))
    assert res.source == "vga-text" and res.exact == "unverified" and any(t["source"] == "serial" and not t["ok"] for t in res.tried)


def test_3_both_empty_returns_the_empty_serial_result_with_exit_0_and_wait_keeps_polling(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))  # graphical: no VGA text at all
    log = tmp_path / "s.log"
    log.write_bytes(b"old text\r\n")
    srv, path = serve(tmp_path, g)
    with srv:
        with QmpClient(path, 2) as c:
            res = sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path), from_offset=len(b"old text\r\n"))
            assert res.source == "serial" and res.text == "" and res.exact == "stream" and "no text" in res.exact_reason
            log.write_bytes(b"")
            assert sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path)).text == ""
            assert sources.read_text(c, source="serial", serial_log=str(log)).text == ""  # forced: as is
        base = ["--qmp", path, "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        code, out, _ = run(["text", *base, "--json"], capsys)
        assert code == 0 and json.loads(out)["text"] == "" and json.loads(out)["source"] == "serial"
        assert run(["wait-text", "login:", *base, "--timeout", "0"], capsys)[0] == 3


# ---------------------------------------------------------------- 4: resume ordering, pending signals, undecodable replies
def test_4_a_signal_in_the_gap_before_hold_cannot_skip_the_resume(tmp_path, monkeypatch):
    g = guest()
    calls = []
    real = capture._Signals.hold

    def hold_once_raising(self):
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit(143)  # the signal lands exactly here, before the guest is resumed
        return real(self)
    monkeypatch.setattr(capture._Signals, "hold", hold_once_raising)
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(SystemExit):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert g.status == "running"


def test_4_a_pending_signal_never_replaces_the_resume_failure(tmp_path, monkeypatch):
    g = guest()

    def failing_resume(*a, **k):
        os.kill(os.getpid(), signal.SIGTERM)  # recorded, not raised: we are resuming
        raise ResumeFailed("could not resume; it may still be PAUSED: run `cont`")
    monkeypatch.setattr(capture, "resume_guest", failing_resume)
    before = signal.getsignal(signal.SIGTERM)
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(ResumeFailed, match="PAUSED") as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
    assert any("signal" in n for n in ei.value.__notes__) and signal.getsignal(signal.SIGTERM) == before


def test_4_a_pending_signal_after_a_good_resume_exits_with_it_and_writes_nothing(tmp_path, monkeypatch):
    g = guest()
    real = capture.resume_guest

    def resume_then_signal(*a, **k):
        os.kill(os.getpid(), signal.SIGTERM)
        return real(*a, **k)
    monkeypatch.setattr(capture, "resume_guest", resume_then_signal)
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(SystemExit) as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert ei.value.code == 128 + signal.SIGTERM and g.status == "running"
    assert not (tmp_path / "x.png").exists()


def test_4_undecodable_reply_bytes_do_not_abort_the_resume_retries(tmp_path):
    g = guest()
    srv, path = serve(tmp_path, g, silent={"screendump"}, bad_utf8_once={"cont"})
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError, match="read failed"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
    assert g.status == "running"


def test_4_qmp_client_turns_undecodable_bytes_into_qmperror(tmp_path):
    srv, path = serve(tmp_path, guest(), bad_utf8_once={"query-status"})
    with srv, QmpClient(path, 1) as c:
        with pytest.raises(QmpError, match="read failed"):
            c.execute("query-status")


# ---------------------------------------------------------------- minors
def test_m_parse_mark_is_strict(tmp_path):
    log = tmp_path / "s.log"
    log.write_bytes(b"hello")
    good = sources.make_mark(str(log))
    assert sources.parse_mark(good)[0] == 5
    off, rest = good.split(".", 1)
    for bad in ("+" + good, " " + good, good + "\n", "1_0." + rest, "٥." + rest, "-5." + rest, good + ".1", good.rsplit(".", 1)[0],
                good.replace(rest.split(".")[2], "x"), ""):
        with pytest.raises(ValueError, match="malformed"):
            sources.parse_mark(bad)


def test_m_mark_and_from_offset_conflict_and_since_mark_reports_the_real_offset(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))
    log = tmp_path / "s.log"
    log.write_bytes(b"old boot\r\nlogin: ")
    srv, path = serve(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        mark = sources.make_mark(str(log))
        assert run(["text", *base, "--mark", mark, "--from-offset", "3"], capsys)[0] == 2
        assert run(["wait-text", "x", *base, "--since-mark", "--from-offset", "3"], capsys)[0] == 2
        assert run(["wait-text", "x", *base, "--since-mark", "--mark", mark], capsys)[0] == 2
        code, out, _ = run(["wait-text", "login:", *base, "--since-mark", "--timeout", "0", "--json"], capsys)
        assert code == 3
        log.write_bytes(log.read_bytes() + b"\r\nlogin: ")
        code, out, _ = run(["wait-text", "login:", *base, "--mark", mark, "--timeout", "0", "--json"], capsys)
        assert code == 0 and json.loads(out)["from_offset"] == len(b"old boot\r\nlogin: ")
        assert run(["text", *base, "--from-offset", "0", "--json"], capsys)[0] == 0


def test_m_since_mark_json_has_the_real_offset(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))
    log = tmp_path / "s.log"
    log.write_bytes(b"abc\r\nlogin: ")
    srv, path = serve(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        # nothing new after the mark: a wait with timeout 0 fails, but the failure object has no offset; use a log that grows first
        import threading
        t = threading.Timer(0.3, lambda: log.write_bytes(log.read_bytes() + b"\r\nlogin: "))
        t.start()
        code, out, _ = run(["wait-text", "login:", *base, "--since-mark", "--timeout", "5", "--interval", "0.1", "--json"], capsys)
        t.join()
    assert code == 0 and json.loads(out)["from_offset"] == len(b"abc\r\nlogin: ")


def test_m_serial_cell_budget_drops_the_oldest_lines(monkeypatch):
    monkeypatch.setattr(serialtext, "MAX_CELLS", 2000)
    raw = b"".join(b"line%05d\r\n" % i for i in range(5000))
    out = serialtext.render(raw)
    assert "line04999" in out and "line00000" not in out and len(out) < 2000 + 2000


def test_m_serial_cell_budget_default_bounds_padding_amplification():
    raw = b"".join(b"\x1b[4000Gx\r\n" for _ in range(3000))  # 3000 lines x 4000 columns = 12M cells
    t0 = time.monotonic()
    out = serialtext.render(raw)
    assert len(out) < 5_000_000 and out.rstrip().endswith("x") and time.monotonic() - t0 < 30


def test_m_link_failure_names_force_and_loses_nothing(tmp_path, monkeypatch):
    g = guest()
    srv, path = serve(tmp_path, g)

    def no_links(src, dst, **kw):
        raise OSError(errno.EPERM, "Operation not permitted")
    with srv, QmpClient(path, 2) as c:
        monkeypatch.setattr(os, "link", no_links)
        with pytest.raises(VmscreenError, match="--force.*hard link|hard link.*--force"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path))
        assert g.status == "running" and not [p for p in tmp_path.iterdir() if p.name.startswith(("x", ".x"))]
        capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), force=True)  # replacement works without hard links
    assert (tmp_path / "x.json").exists() and (tmp_path / "x.png").exists()


def test_m_sigterm_between_backup_and_install_rolls_back(tmp_path, monkeypatch):
    g = guest()
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path))
        old = (tmp_path / "keep.png").read_bytes(), (tmp_path / "keep.json").read_bytes()
        g.put(4, 0, "different now")
        real = os.replace
        fired = []

        def replace_and_signal(src, dst):
            if ".tmp" in str(src) and not fired:
                fired.append(1)
                os.kill(os.getpid(), signal.SIGTERM)  # after the backups, before the install completes
            return real(src, dst)
        monkeypatch.setattr(os, "replace", replace_and_signal)
        with pytest.raises(SystemExit) as ei:
            capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path), force=True)
        monkeypatch.undo()
        assert ei.value.code == 128 + signal.SIGTERM and g.status == "running"
        assert ((tmp_path / "keep.png").read_bytes(), (tmp_path / "keep.json").read_bytes()) == old
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_m_failed_rollback_names_the_backup_files(tmp_path, monkeypatch):
    g = guest()
    srv, path = serve(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path))
        real = os.replace

        def failing(src, dst):
            if ".tmp" in str(src) and str(dst).endswith(".json"):
                raise OSError("disk full")
            if ".bak" in str(src):
                raise OSError("restore denied")
            return real(src, dst)
        monkeypatch.setattr(os, "replace", failing)
        with pytest.raises(OSError, match="disk full") as ei:
            capture.ocr_pair(c, tmp_path / "keep", dump_dir=str(tmp_path), force=True)
        monkeypatch.undo()
    notes = " ".join(ei.value.__notes__)
    assert "restore denied" in notes and ".bak" in notes
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".bak")]  # the previous files are still discoverable
