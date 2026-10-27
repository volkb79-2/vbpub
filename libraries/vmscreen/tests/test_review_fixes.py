"""Regression tests for the independent review of 8014fda18 (H1, M1-M5, L1-L8)."""
from __future__ import annotations

import json
import os
import signal

import pytest
from cli_extended import CliIdentity

from fakeqmp import FakeGuest, FakeQmp, make_png
from vmscreen import capture, cli, keys, pixels, serialtext, sources, textmode, waiter
from vmscreen.errors import NoExactText, OutputExists, QmpError, ResumeFailed, WaitTimeout
from vmscreen.qmp import QmpClient

IDENT = CliIdentity("vmscreen", "0.1.0", "exact console text of local QEMU guests", "vmscreen")
NOSLEEP = lambda s: None  # noqa: E731


def run(argv, capsys):
    code = cli.build_cli(IDENT).run(argv=argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def server(tmp_path, guest, name="q.sock", **kw):
    return FakeQmp(tmp_path / name, guest.handlers(), **kw), str(tmp_path / name)


def guest_with_text():
    g = FakeGuest()
    g.put(0, 0, "Welcome")
    g.put(1, 0, "login:", 0x1F)
    return g


# ---------------------------------------------------------------- H1: the guest is never left paused
def test_h1_timeout_inside_the_paused_window_still_resumes_and_keeps_the_original_error(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g, silent={"screendump"})
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError, match="read failed"):  # the ORIGINAL error, not a masking cont failure
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert g.status == "running"
        assert srv.names().count("stop") == 1 and srv.names()[-2:] == ["cont", "query-status"]
    assert not (tmp_path / "x.png").exists()


def test_h1_cont_failure_reports_the_guest_may_be_paused(tmp_path):
    g = guest_with_text()
    h = g.handlers()

    def bad_cont(_):
        raise ValueError("cont refused")
    h["cont"] = bad_cont
    with FakeQmp(tmp_path / "q.sock", h) as srv, QmpClient(str(tmp_path / "q.sock"), 1) as c:
        with pytest.raises(ResumeFailed, match="may still be PAUSED"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert srv.names().count("cont") == 1 + capture.RESUME_ATTEMPTS  # same connection once, then fresh ones
    assert not (tmp_path / "x.png").exists()


def test_h1_original_error_wins_over_a_resume_failure_and_carries_it_as_a_note(tmp_path):
    g = guest_with_text()
    h = g.handlers()
    h["cont"] = lambda _: (_ for _ in ()).throw(ValueError("cont refused"))
    with FakeQmp(tmp_path / "q.sock", h, silent={"screendump"}), QmpClient(str(tmp_path / "q.sock"), 0.3) as c:
        with pytest.raises(QmpError) as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
    assert not isinstance(ei.value, ResumeFailed) and "read failed" in str(ei.value)
    assert any("PAUSED" in n for n in ei.value.__notes__)


def test_h1_lost_stop_reply_still_resumes(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g, drop_reply={"stop"})  # QEMU stopped the guest but the reply never arrives
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError, match="read failed"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert g.status == "running"


def test_h1_stop_error_reply_is_reported_and_leaves_the_guest_running(tmp_path):
    g = guest_with_text()
    h = g.handlers()
    h["stop"] = lambda _: (_ for _ in ()).throw(ValueError("no stop for you"))
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 1) as c:
        with pytest.raises(QmpError, match="no stop for you"):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert g.status == "running"


def test_h1_sigterm_between_stop_and_cont_resumes_then_exits(tmp_path):
    g = guest_with_text()
    h = g.handlers()
    inner = h["screendump"]

    def kill_self(a):
        r = inner(a)
        os.kill(os.getpid(), signal.SIGTERM)
        return r
    h["screendump"] = kill_self
    before = signal.getsignal(signal.SIGTERM)
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(SystemExit) as ei:
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), sleep=NOSLEEP)
        assert ei.value.code == 128 + signal.SIGTERM and g.status == "running"
    assert signal.getsignal(signal.SIGTERM) == before


def test_h1_custom_reconnect_factory_is_used_for_the_fresh_connection(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g, silent={"screendump"})
    calls = []

    def reconnect():
        calls.append(1)
        return QmpClient(path, 1)
    with srv, QmpClient(path, 0.3) as c:
        with pytest.raises(QmpError):
            capture.ocr_pair(c, tmp_path / "x", dump_dir=str(tmp_path), reconnect=reconnect, sleep=NOSLEEP)
    assert calls == [1] and g.status == "running"


# ---------------------------------------------------------------- M1/M2: exact only when it can be vouched for
def test_m1_verified_text_is_exact_true_and_says_how(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact is True and res.verification["ok"] and res.verification["mismatched"] == 0 and res.verification["compared"] > 1900


def test_m1_shifted_text_page_is_unverified_not_exact(tmp_path):
    g = guest_with_text()
    g.put(5, 0, "second page text")  # what is really displayed
    g.display_offset = 5 * 80 * 2     # CRTC start differs from BDA 0x44e: the screen shows row 5 first
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "unverified" and res.verification["mismatched"] > 0 and "disagree" in res.exact_reason


def test_m2_same_pixel_size_graphical_mode_is_unverified(tmp_path):
    g = FakeGuest()  # BDA says text mode 80x25 and the screendump is 720x400, but it is graphics
    g.graphic_noise = True
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path))
    assert res.exact == "unverified"


def test_m1_unverified_text_is_never_matched_by_wait_unless_accepted(tmp_path):
    g = guest_with_text()
    g.display_offset = 160
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        read = lambda: sources.read_text(c, source="vga", dump_dir=str(tmp_path))  # noqa: E731
        with pytest.raises(WaitTimeout, match="unverified"):
            waiter.wait_for(read, "Welcome", 0)
        assert waiter.wait_for(read, "Welcome", 0, accept_unverified=True)["exact"] == "unverified"


def test_m1_ocr_pair_refuses_unverified_text_unless_allowed(tmp_path):
    g = guest_with_text()
    g.display_offset = 160
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(NoExactText, match="no verified exact text"):
            capture.ocr_pair(c, tmp_path / "a", dump_dir=str(tmp_path))
        assert g.status == "running"
        capture.ocr_pair(c, tmp_path / "b", dump_dir=str(tmp_path), allow_inexact=True)
    side = json.loads((tmp_path / "b.json").read_text())
    assert side["exact"] == "unverified" and side["exact_reason"] and side["lines"][0] and side["verification"]["mismatched"] > 0


def test_m1c_a_screen_that_changes_during_the_reads_is_not_exact(tmp_path):
    g = guest_with_text()
    n = [0]
    real = g.render

    def flicker():
        n[0] += 1
        g.display_offset = 0
        data = bytearray(real())
        data[-20] ^= n[0] & 0xFF  # IDAT bytes differ on every dump
        return bytes(data)
    g.render = flicker
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        res = sources.read_vga(c, str(tmp_path), stable_check=True)
    assert res.exact == "unverified" and "changed during" in res.exact_reason


def test_m1d_forty_column_modes_are_rejected():
    d = textmode.detect(_bda(cols=40), (360, 400))
    assert not d.ok and "40-column" in d.reason


def _bda(cols=80):
    import struct
    b = bytearray(0xA0)
    b[0x49] = 1
    struct.pack_into("<H", b, 0x4A, cols)
    b[0x84] = 24
    struct.pack_into("<H", b, 0x85, 16)
    return bytes(b)


@pytest.mark.parametrize("ft", [1, 2, 3, 4])
def test_pixels_decode_all_row_filters(ft):
    ink = {(x, y) for x in range(2, 9) for y in range(1, 5)} | {(0, 0)}
    base = pixels.decode_png(make_png(12, 6, ink, 0))
    assert pixels.decode_png(make_png(12, 6, ink, ft))[3] == base[3]


def test_pixels_rejects_unsupported_png_and_reports_unverifiable():
    bad = bytearray(make_png(4, 4))
    bad[24] = 16  # bit depth 16
    with pytest.raises(ValueError, match="unsupported"):
        pixels.decode_png(bytes(bad))
    with pytest.raises(ValueError):
        pixels.decode_png(b"GIF89a")
    out = pixels.verify_ink(bytes(bad), 1, 1, 4, 4, b"A\x07", None, True)
    assert out["ok"] is None and "not decodable" in out["reason"]
    assert pixels.verify_ink(make_png(8, 8), 2, 1, 4, 4, b"A\x07 \x07", None, True)["ok"] is False  # size mismatch


def test_pixels_ink_rules_skip_cursor_blink_and_full_block():
    ink = {(1, 1)}
    png = make_png(32, 16, ink)  # 4 cells of 8x16, ink only in cell 0
    # cell0 'A' ink ok, cell1 ' ', cell2 0xDB full block (skipped), cell3 blink attr (skipped)
    buf = b"A\x07" + b" \x07" + b"\xdb\x07" + b"B\x87"
    out = pixels.verify_ink(png, 4, 1, 8, 16, buf, None, True)
    assert out["ok"] is True and out["compared"] == 2 and out["skipped"] == 2
    # a glyph where the screen is blank is a mismatch; hidden text (fg == bg) predicts no ink
    assert pixels.verify_ink(png, 4, 1, 8, 16, b"A\x07C\x07\x00\x00\x00\x00", None, True)["mismatches"][0]["col"] == 1
    assert pixels.verify_ink(png, 4, 1, 8, 16, b"A\x07 \x07 \x07 \x07", (0, 0), True)["skipped"] == 1  # cursor cell skipped
    assert pixels.verify_ink(png, 4, 1, 8, 16, b"A\x00 \x07 \x07 \x07", None, True)["ok"] is False


# ---------------------------------------------------------------- M3: output naming and atomicity
def test_m3_dotted_prefixes_do_not_collide_and_existing_outputs_are_refused(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        a = capture.ocr_pair(c, tmp_path / "vm-1.5", dump_dir=str(tmp_path))
        b = capture.ocr_pair(c, tmp_path / "vm-1.6", dump_dir=str(tmp_path))
        assert a["png"].endswith("vm-1.5.png") and b["json"].endswith("vm-1.6.json")
        assert {p.name for p in tmp_path.glob("vm-*")} == {"vm-1.5.png", "vm-1.5.json", "vm-1.6.png", "vm-1.6.json"}
        before = (tmp_path / "vm-1.5.json").read_text()
        with pytest.raises(OutputExists, match="--force"):
            capture.ocr_pair(c, tmp_path / "vm-1.5", dump_dir=str(tmp_path))
        assert (tmp_path / "vm-1.5.json").read_text() == before and srv.names().count("stop") == 2  # refused BEFORE pausing
        capture.ocr_pair(c, tmp_path / "vm-1.5", dump_dir=str(tmp_path), force=True)


def test_m3_no_orphan_png_when_the_json_cannot_be_committed(tmp_path, monkeypatch):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    real = os.link

    def failing(src, dst, **kw):
        if str(dst).endswith(".json"):
            raise OSError("disk full")
        return real(src, dst, **kw)
    monkeypatch.setattr(os, "link", failing)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(Exception, match="disk full"):
            capture.ocr_pair(c, tmp_path / "o", dump_dir=str(tmp_path))
    assert not list(tmp_path.glob("o*")) and not list(tmp_path.glob(".o*")) and g.status == "running"


# ---------------------------------------------------------------- M4: serial stream
def test_m4_serial_render_erases_line_and_display():
    assert serialtext.render(b"hello\rXX\x1b[K") == "XX"
    assert serialtext.render(b"abcdef\x1b[1K") == ""
    assert serialtext.render(b"abcdef\x1b[3D\x1b[1K") == "    ef" and serialtext.render(b"ab\x1b[5Gc") == "ab  c"
    assert serialtext.render(b"login: root\r\x1b[2Kpassword:") == "password:"
    assert serialtext.render(b"abc\r\x1b[1KZ") == "Zbc"
    assert serialtext.render(b"old boot\r\nlogin: \x1b[2J\x1b[Hnew boot\r\n") == "new boot"
    assert serialtext.render(b"x\r\ny\x1bcz") == "z"
    assert serialtext.render(b"one\r\ntwo\x1b[0J") == "one\ntwo"
    assert serialtext.render(b"one\r\ntwo\x1b[1Jz") == "   z"


def test_m4_serial_is_a_stream_not_screen_exact(tmp_path):
    log = tmp_path / "s.log"
    log.write_bytes(b"login: ")
    res = sources.read_serial(str(log))
    assert res.exact == "stream" and res.scope == "stream"


def test_m4_since_mark_and_from_offset_ignore_a_previous_boot(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))
    log = tmp_path / "s.log"
    log.write_bytes(b"old boot\r\nlogin: ")
    with FakeQmp(tmp_path / "q.sock", g.handlers()):
        base = ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        assert run(["wait-text", "login:", *base, "--timeout", "0"], capsys)[0] == 0  # the stale one satisfies it...
        assert run(["wait-text", "login:", *base, "--timeout", "0", "--since-mark"], capsys)[0] == 3  # ...but not with a mark
        code, out, _ = run(["mark", "--serial-log", str(log), "--json"], capsys)
        off = json.loads(out)["offset"]
        assert code == 0 and off == len(b"old boot\r\nlogin: ")
        log.write_bytes(log.read_bytes() + b"\r\nnew boot\r\nlogin: ")
        code, out, _ = run(["wait-text", "login:", *base, "--timeout", "0", "--from-offset", str(off), "--json"], capsys)
        assert code == 0 and json.loads(out)["from_offset"] == off
        log.write_bytes(b"x")  # rotated: shorter than the offset
        assert run(["text", *base, "--from-offset", str(off), "--source", "serial"], capsys)[0] == 4
        assert run(["wait-text", "x", "--qmp", base[1], "--since-mark"], capsys)[0] == 2  # needs --serial-log


# ---------------------------------------------------------------- M5: assumptions are recorded
def test_m5_assumptions_are_in_text_json_and_sidecar(tmp_path, capsys):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path)]
        d = json.loads(run(["text", *base, "--json"], capsys)[1])
        assert "cp437" in d["assumptions"]["font"] and "512-glyph" in d["assumptions"]["font"] and "blink" in d["assumptions"]["blink_mode"]
        assert d["geometry"]["blink_mode"] is True
        d2 = json.loads(run(["text", *base, "--json", "--no-blink-mode"], capsys)[1])
        assert d2["geometry"]["blink_mode"] is False
        run(["ocr-pair", *base, "--out", str(tmp_path / "m5")], capsys)
    side = json.loads((tmp_path / "m5.json").read_text())
    assert "512-glyph" in side["assumptions"]["font"]


# ---------------------------------------------------------------- L1-L8
def test_l1_invalid_regex_is_a_usage_error_before_any_connection(tmp_path, capsys):
    code, _, err = run(["wait-text", "(", "--qmp", str(tmp_path / "nowhere.sock")], capsys)
    assert code == 2 and "invalid regular expression" in err
    assert run(["wait-text", "a", "--qmp", str(tmp_path / "nowhere.sock"), "--timeout", "-1"], capsys)[0] == 2
    assert run(["wait-text", "a", "--qmp", str(tmp_path / "nowhere.sock"), "--interval", "0"], capsys)[0] == 2


def test_l2_bad_numbers_and_files_map_to_exit_codes_without_sending(tmp_path, capsys):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path)]
        assert run(["send", *base, "--keys", "ret,ret", "--delay", "-1"], capsys)[0] == 2
        assert run(["send", *base, "--type-file", str(tmp_path / "missing.txt")], capsys)[0] == 2
        assert "send-key" not in srv.names()
        code, _, err = run(["screen", *base, "--out", str(tmp_path / "no" / "dir" / "s.png")], capsys)
        assert code == 1 and "FileNotFoundError" in err


def test_l3_unknown_key_names_are_rejected_client_side_before_any_key(tmp_path, capsys):
    code, _, err = run(["send", "--qmp", str(tmp_path / "nowhere.sock"), "--keys", "ret,bogus_key"], capsys)
    assert code == 2 and "bogus_key" in err  # no connection attempted: not exit 5
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv:
        assert run(["send", "--qmp", path, "--keys", "ctrl+alt+f2,ret,kp_enter,spc"], capsys)[0] == 0
        n = srv.names().count("send-key")
        assert n == 4
        assert run(["send", "--qmp", path, "--keys", "ret,nonsense"], capsys)[0] == 2 and srv.names().count("send-key") == n
        assert run(["send", "--qmp", path, "--keys", "nonsense", "--allow-unknown-keys"], capsys)[0] == 0
    assert keys.QCODES >= {"ret", "f12", "kp_0", "z", "9"}


def test_l4_dump_dir_problems_name_permissions(tmp_path):
    g = guest_with_text()
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        with pytest.raises(Exception, match="permissions"):
            sources.screendump_png(c, str(tmp_path / "absent"))
    h = g.handlers()
    h["screendump"] = lambda a: (_ for _ in ()).throw(ValueError("Could not open: Permission denied"))
    with FakeQmp(tmp_path / "p.sock", h), QmpClient(str(tmp_path / "p.sock"), 2) as c:
        with pytest.raises(QmpError, match="QEMU process must be able to write.*permissions"):
            sources.screendump_png(c, str(tmp_path))


def test_l4_scratch_files_are_not_created_by_us(tmp_path):
    """QEMU may run as another user: vmscreen must not pre-create a 0600 file there."""
    seen = []
    g = guest_with_text()
    h = g.handlers()
    inner = h["screendump"]

    def spy(a):
        seen.append(os.path.exists(a["filename"]))
        return inner(a)
    h["screendump"] = spy
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        sources.screendump_png(c, str(tmp_path))
    assert seen == [False]


def test_l5_paused_semantics_in_sidecar(tmp_path):
    g = guest_with_text()
    g.status = "paused"
    srv, path = server(tmp_path, g)
    with srv, QmpClient(path, 2) as c:
        out = capture.ocr_pair(c, tmp_path / "p", dump_dir=str(tmp_path))
    assert (out["guest_was_paused_before"], out["guest_paused_for_capture"], out["vm_status_before"]) == (True, False, "paused")
    assert g.status == "paused"  # never resumes a guest somebody else paused


def test_l6_bad_greeting_closes_the_socket_and_commas_in_plain_paths_work(tmp_path):
    with FakeQmp(tmp_path / "g.sock", greeting=False):
        c = QmpClient(str(tmp_path / "g.sock"), 1)
        with pytest.raises(QmpError):
            c.connect()
        assert c._sock is None and c._file is None
    g = guest_with_text()
    odd = tmp_path / "a,b.sock"
    with FakeQmp(odd, g.handlers()):
        with QmpClient(str(odd), 2) as c:
            assert c.execute("query-status")["status"] == "running"
        assert QmpClient("unix:" + str(odd).replace(",", ",,") + ",server=on").path == str(odd)  # QEMU's ,, escape
    assert QmpClient(str(odd)).path == str(odd)


def test_l7_tail_semantics(tmp_path, capsys):
    g = FakeGuest()
    for i in range(5):
        g.put(i, 0, f"line{i}")
    srv, path = server(tmp_path, g)
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path)]
        assert run(["text", *base, "--tail", "0"], capsys)[0] == 2
        code, out, _ = run(["text", *base, "--tail", "2", "--json"], capsys)
        d = json.loads(out)
        assert code == 0 and d["text"] == "line3\nline4" and d["tail"] == 2
    log = tmp_path / "s.log"
    log.write_bytes(b"a\r\nb\r\nc\r\n")
    g2 = FakeGuest(png_size=(1280, 800))
    with FakeQmp(tmp_path / "q2.sock", g2.handlers()):
        code, out, _ = run(["text", "--qmp", str(tmp_path / "q2.sock"), "--dump-dir", str(tmp_path), "--serial-log", str(log), "--tail", "2", "--json"], capsys)
        assert json.loads(out)["text"] == "b\nc"


def test_probe_reports_exact_state(tmp_path, capsys):
    g = guest_with_text()
    g.display_offset = 160
    srv, path = server(tmp_path, g)
    with srv:
        d = json.loads(run(["probe", "--qmp", path, "--dump-dir", str(tmp_path), "--json"], capsys)[1])
    assert d["exact"] == "unverified" and d["exact_text_available"] is False and d["exact_reason"]


def test_cli_ocr_pair_force_and_resume_over_fresh_connection(tmp_path, capsys):
    g = guest_with_text()
    srv, path = server(tmp_path, g, silent={"screendump"})
    with srv:
        base = ["--qmp", path, "--dump-dir", str(tmp_path), "--qmp-timeout", "0.3"]
        code, out, _ = run(["ocr-pair", *base, "--out", str(tmp_path / "t"), "--json"], capsys)
        assert code == 5 and json.loads(out)["ok"] is False and g.status == "running"
    g2 = guest_with_text()
    srv2, path2 = server(tmp_path, g2, "q2.sock")
    with srv2:
        base = ["--qmp", path2, "--dump-dir", str(tmp_path)]
        assert run(["ocr-pair", *base, "--out", str(tmp_path / "f")], capsys)[0] == 0
        assert run(["ocr-pair", *base, "--out", str(tmp_path / "f")], capsys)[0] == 1
        assert run(["ocr-pair", *base, "--out", str(tmp_path / "f"), "--force"], capsys)[0] == 0
