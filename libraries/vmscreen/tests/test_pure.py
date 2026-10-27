"""Pure-function tests: text-buffer decoder, mode detection, xp parser, serial renderer, key parser, wait loop."""
from __future__ import annotations

import struct

import pytest

from fakeqmp import make_png
from vmscreen import keys, serialtext, textmode, waiter
from vmscreen.errors import NoExactText, WaitTimeout


def bda(mode=3, cols=80, rows=25, cell_h=16, page_off=0, cursor=(5, 2)) -> bytes:
    b = bytearray(0xA0)
    b[0x49] = mode
    struct.pack_into("<H", b, 0x4A, cols)
    b[0x84] = rows - 1
    struct.pack_into("<H", b, 0x85, cell_h)
    struct.pack_into("<H", b, 0x4E, page_off)
    b[0x50], b[0x51] = cursor
    return bytes(b)


def test_cp437_mapping():
    data = bytes([0x41, 0x07, 0xB0, 0x07, 0xC4, 0x07, 0x01, 0x07, 0x00, 0x07, 0x7F, 0x07, 0xFF, 0x07, 0x80, 0x07])
    s = textmode.decode_buffer(data, 8, 1)
    assert [c.char for c in s.cells[0]] == ["A", "░", "─", "☺", " ", "⌂", " ", "Ç"]


def test_attribute_to_fg_bg_blink():
    assert textmode.decode_attribute(0x07) == ("light-gray", "black", False)
    assert textmode.decode_attribute(0x1F) == ("white", "blue", False)
    assert textmode.decode_attribute(0x8E) == ("yellow", "black", True)
    assert textmode.decode_attribute(0xF0, blink_mode=False) == ("black", "white", False)
    assert textmode.decode_attribute(0xF0) == ("black", "light-gray", True)  # bit 7 is blink, bg index 7


def test_text_trims_trailing_blank_rows_and_columns():
    data = bytearray(b" \x07" * (4 * 3))
    data[0:2] = b"H\x07"
    data[2:4] = b"i\x07"
    data[16:18] = b"x\x1f"
    s = textmode.decode_buffer(bytes(data), 4, 3)
    assert s.lines == ["Hi", "", "x"]
    assert s.text() == "Hi\n\nx"
    assert s.cells[2][0].bg == "blue" and s.cells[2][0].fg == "white"


def test_decode_buffer_short_and_bad_geometry():
    with pytest.raises(ValueError):
        textmode.decode_buffer(b"\x00" * 10, 80, 25)
    with pytest.raises(ValueError):
        textmode.decode_buffer(b"", 0, 0)


def test_png_size_and_not_png():
    assert textmode.png_size(make_png(720, 400)) == (720, 400)
    with pytest.raises(ValueError):
        textmode.png_size(b"GIF89a" + b"\x00" * 30)


def test_detect_text_mode_confirmed_by_screendump():
    d = textmode.detect(bda(), (720, 400))
    assert d.ok and (d.cols, d.rows, d.base, d.size, d.cell_w, d.cell_h) == (80, 25, 0xB8000, 4000, 9, 16)
    assert d.verified_by_screendump and d.cursor == (5, 2)
    assert textmode.detect(bda(), (640, 400)).cell_w == 8


def test_detect_without_screendump_is_marked_unverified():
    d = textmode.detect(bda(), None)
    assert d.ok and not d.verified_by_screendump and "not checked" in d.reason


def test_detect_rejects_graphics_and_stale_bda():
    assert "not a text mode" in textmode.detect(bda(mode=0x12), (640, 480)).reason
    stale = textmode.detect(bda(), (1280, 800))
    assert not stale.ok and "graphical" in stale.reason
    assert not textmode.detect(bda(), (720, 480)).ok
    assert not textmode.detect(b"\x00" * 10, None).ok
    assert "implausible" in textmode.detect(bda(cols=3), None).reason
    assert "implausible" in textmode.detect(bda(cell_h=5), None).reason


def test_detect_mono_mode_and_page_offset_window():
    d = textmode.detect(bda(mode=7), None)
    assert d.ok and d.base == 0xB0000
    assert textmode.detect(bda(page_off=0x1000), None).base == 0xB9000
    assert "leaves the VGA window" in textmode.detect(bda(page_off=0x7F00), None).reason
    assert "leaves the VGA window" in textmode.detect(bda(mode=7, page_off=0x7F00), None).reason


def test_parse_xp():
    out = "00000000000b8000: 0x48 0x07 0x69 0x07\r\n00000000000b8004: 0x21 0x07\r\nnoise\r\n"
    assert textmode.parse_xp(out) == bytes([0x48, 7, 0x69, 7, 0x21, 7])


def test_serial_render_strips_escapes_and_applies_controls():
    raw = b"\x1b[2J\x1b[1;1Hboot\r\nlogin: \x1b[31mroot\x1b[0m\r\nab\bX\r\n\x1b]0;title\x07\tx\n\x1b(B\x1b=end"
    assert serialtext.render(raw) == "boot\nlogin: root\naX\n        x\nend"
    assert serialtext.render(b"abc\rX") == "Xbc"
    assert serialtext.render(b"") == ""


def test_text_to_keys_and_parse_keys():
    assert keys.text_to_keys("aB1!_ \n") == [["a"], ["shift", "b"], ["1"], ["shift", "1"], ["shift", "minus"], ["spc"], ["ret"]]
    with pytest.raises(ValueError, match="unmappable"):
        keys.text_to_keys("é")
    assert keys.parse_keys("ret, down ,ctrl+alt+f2") == [["ret"], ["down"], ["ctrl", "alt", "f2"]]
    for bad in ("", "a,,b", "ctrl+"):
        with pytest.raises(ValueError):
            keys.parse_keys(bad)


class Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, name, **kw):
        self.calls.append((name, kw))


def test_send_sequence_delay_and_hold():
    r, slept = Recorder(), []
    assert keys.send_sequence(r, [["ctrl", "c"], ["ret"]], 0.1, 20, slept.append) == 2
    assert r.calls[0] == ("send-key", {"keys": [{"type": "qcode", "data": "ctrl"}, {"type": "qcode", "data": "c"}], "hold-time": 20})
    assert slept == [0.1]
    r2 = Recorder()
    keys.send_sequence(r2, [["a"]], 0.1, None, slept.append)
    assert "hold-time" not in r2.calls[0][1] and slept == [0.1]


class Res:
    def __init__(self, text, source="vga-text"):
        self.text, self.source = text, source


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_wait_for_matches_on_a_later_poll():
    clk, texts = Clock(), iter(["", "booting", "booting\nlogin: "])
    out = waiter.wait_for(lambda: Res(next(texts)), r"^login:", 10, 1, clock=clk, sleep=clk.sleep)
    assert out["matched"] == "login:" and out["polls"] == 3 and out["elapsed_s"] == 2 and out["source"] == "vga-text"


def test_wait_for_timeout_carries_last_text_and_bounds_the_sleep():
    clk = Clock()
    with pytest.raises(WaitTimeout) as ei:
        waiter.wait_for(lambda: Res("still booting"), "login", 2.5, 1, clock=clk, sleep=clk.sleep)
    assert ei.value.last_text == "still booting" and ei.value.exit_code == 3 and clk.t == 2.5


def test_wait_for_zero_timeout_polls_once_and_literal_escapes():
    clk = Clock()
    with pytest.raises(WaitTimeout):
        waiter.wait_for(lambda: Res("a.b"), "x", 0, clock=clk, sleep=clk.sleep)
    assert waiter.wait_for(lambda: Res("a.b"), "a.b", 0, literal=True, clock=clk, sleep=clk.sleep)["matched"] == "a.b"
    with pytest.raises(WaitTimeout):
        waiter.wait_for(lambda: Res("aXb"), "a.b", 0, literal=True, clock=clk, sleep=clk.sleep)


def test_wait_for_survives_no_exact_text_polls():
    clk, n = Clock(), [0]

    def read():
        n[0] += 1
        if n[0] < 3:
            raise NoExactText("graphical", [])
        return Res("ready")
    assert waiter.wait_for(read, "ready", 10, 1, clock=clk, sleep=clk.sleep)["polls"] == 3

    def never():
        raise NoExactText("graphical", [])
    with pytest.raises(WaitTimeout, match="no exact text: graphical"):
        waiter.wait_for(never, "x", 1, 1, clock=clk, sleep=clk.sleep)
