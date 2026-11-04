"""QMP client against a fake server, the source chain, and ocr-pair (no QEMU)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakeqmp import FakeGuest, FakeQmp
from vmscreen import capture, sources
from vmscreen.errors import NoExactText, QmpError
from vmscreen.qmp import QmpClient


def sock(tmp_path):
    return tmp_path / "q.sock"


def test_qmp_handshake_execute_and_event_skipping(tmp_path):
    with FakeQmp(sock(tmp_path), {"query-status": lambda a: {"status": "running"}}, events=True) as srv:
        with QmpClient(str(sock(tmp_path)), 2) as c:
            assert c.execute("query-status") == {"status": "running"}
            c.connect()  # idempotent
        assert srv.names() == ["qmp_capabilities", "query-status"]


def test_qmp_error_reply_and_unknown_command(tmp_path):
    def boom(a):
        raise ValueError("kaput")
    with FakeQmp(sock(tmp_path), {"boom": boom}):
        with QmpClient("unix:" + str(sock(tmp_path)) + ",server", 2) as c:
            with pytest.raises(QmpError, match="GenericError: kaput"):
                c.execute("boom")
            with pytest.raises(QmpError, match="CommandNotFound"):
                c.execute("nope", x=1)


def test_qmp_connect_failures(tmp_path):
    with pytest.raises(QmpError, match="cannot connect"):
        QmpClient(str(tmp_path / "absent.sock"), 1).connect()
    with FakeQmp(sock(tmp_path), greeting=False):
        with pytest.raises(QmpError, match="greeting"):
            QmpClient(str(sock(tmp_path)), 1).connect()


def test_qmp_timeout_and_garbage_and_close(tmp_path):
    with FakeQmp(sock(tmp_path), {"slow": lambda a: {}}, silent={"slow"}, junk_on="junk"):
        c = QmpClient(str(sock(tmp_path)), 0.3)
        c.connect()
        with pytest.raises(QmpError, match="read failed"):
            c.execute("slow")
        c.close()
        c.close()  # double close is harmless
    with FakeQmp(tmp_path / "j.sock", junk_on="junk"):
        c = QmpClient(str(tmp_path / "j.sock"), 1)
        c.connect()
        with pytest.raises(QmpError, match="non-JSON"):
            c.execute("junk")
    # server vanishes mid-session
    srv = FakeQmp(tmp_path / "g.sock")
    c = QmpClient(str(tmp_path / "g.sock"), 1)
    c.connect()
    c._sock.shutdown(2)
    with pytest.raises(QmpError):
        c.execute("anything")
    srv.close()


def guest_server(tmp_path, guest):
    return FakeQmp(sock(tmp_path), guest.handlers())


def test_vga_text_source_reads_the_buffer_and_reports_itself(tmp_path):
    g = FakeGuest()
    g.put(0, 0, "SeaBIOS (version 1.16)")
    g.put(2, 0, "Booting from Hard Disk...", 0x1F)
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        res = sources.read_text(c, dump_dir=str(tmp_path))
        assert res.source == "vga-text" and res.scope == "screen" and res.exact
        assert res.text == "SeaBIOS (version 1.16)\n\nBooting from Hard Disk..."
        assert res.geometry["cols"] == 80 and res.geometry["verified_by_screendump"]
        d = res.as_dict(include_cells=True)
        assert d["cells"][2][0] == {"c": "B", "fg": "white", "bg": "blue", "blink": False}
        assert "pmemsave" in srv.names() and "screendump" in srv.names()
    assert not list(tmp_path.glob("vmscreen-*"))  # temp dumps are removed


def test_vga_falls_back_to_hmp_xp_when_pmemsave_is_unusable(tmp_path):
    g = FakeGuest()
    g.xp_only = True
    g.put(0, 0, "via xp")
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        assert sources.read_text(c, dump_dir=str(tmp_path), source="vga").text == "via xp"
        assert "human-monitor-command" in srv.names()


def test_xp_fallback_short_output_is_an_error(tmp_path):
    g = FakeGuest()
    g.xp_only = True
    h = g.handlers()
    h["human-monitor-command"] = lambda a: "garbage"
    with FakeQmp(sock(tmp_path), h), QmpClient(str(sock(tmp_path)), 2) as c:
        with pytest.raises(QmpError, match="gave 0"):
            sources.read_vga(c, str(tmp_path))


def test_empty_screendump_file_is_reported(tmp_path):
    g = FakeGuest()
    h = g.handlers()
    h["screendump"] = lambda a: {}  # QEMU "succeeds" but wrote nothing we can see
    with FakeQmp(sock(tmp_path), h), QmpClient(str(sock(tmp_path)), 2) as c:
        with pytest.raises(QmpError, match="not readable"):
            sources.screendump_png(c, str(tmp_path))
    h["screendump"] = lambda a: Path(a["filename"]).write_bytes(b"junk") and {}
    with FakeQmp(tmp_path / "e2.sock", h), QmpClient(str(tmp_path / "e2.sock"), 2) as c:
        with pytest.raises(QmpError, match="not a PNG"):
            sources.screendump_png(c, str(tmp_path))


def test_graphical_screen_has_no_exact_text_and_says_so(tmp_path):
    g = FakeGuest(png_size=(1280, 800))
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        with pytest.raises(NoExactText) as ei:
            sources.read_text(c, dump_dir=str(tmp_path))
        tried = {t["source"]: t for t in ei.value.tried}
        assert set(tried) == {"vga-text", "serial", "vcs", "none"} and not any(t["ok"] for t in tried.values())
        assert "graphical" in tried["vga-text"]["reason"] and "no --serial-log" in tried["serial"]["reason"]
        assert ei.value.exit_code == 4


def test_graphical_screen_falls_through_to_serial(tmp_path):
    log = tmp_path / "serial.log"
    log.write_bytes(b"Linux version 6.1\r\nlogin: ")
    g = FakeGuest(png_size=(1280, 800))
    with guest_server(tmp_path, g), QmpClient(str(sock(tmp_path)), 2) as c:
        res = sources.read_text(c, serial_log=str(log), dump_dir=str(tmp_path))
    assert res.source == "serial" and res.scope == "stream" and res.text == "Linux version 6.1\nlogin:"
    assert [t["source"] for t in res.tried] == ["vga-text", "serial"] and not res.tried[0]["ok"] and res.tried[1]["ok"]


def test_forced_sources_and_unreadable_serial(tmp_path):
    g = FakeGuest(png_size=(1280, 800))
    with guest_server(tmp_path, g), QmpClient(str(sock(tmp_path)), 2) as c:
        with pytest.raises(NoExactText, match="serial log unreadable"):
            sources.read_text(c, source="serial", serial_log=str(tmp_path / "missing.log"))
        with pytest.raises(NoExactText) as ei:
            sources.read_text(c, source="vga", dump_dir=str(tmp_path))
        assert [t["source"] for t in ei.value.tried] == ["vga-text", "none"]
        with pytest.raises(ValueError):
            sources.read_text(c, source="bogus")
    log = tmp_path / "s.log"
    log.write_bytes(b"1\n2\n3\n4\n")
    assert sources.read_serial(str(log), from_offset=4).text == "3\n4"


def test_ocr_pair_pauses_resumes_and_writes_sidecar(tmp_path):
    g = FakeGuest()
    g.put(0, 0, "Press F1", 0x4E)
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        out = capture.ocr_pair(c, tmp_path / "corpus" / "shot1", dump_dir=str(tmp_path))
        assert g.status == "running"
        names = srv.names()
        assert names.index("stop") < names.index("screendump") < names.index("cont")
    side = json.loads((tmp_path / "corpus" / "shot1.json").read_text())
    assert out["exact"] and side["schema"] == "vmscreen.ocr-pair/2" and side["guest_paused_for_capture"] is True and side["guest_was_paused_before"] is False
    assert (side["width"], side["height"]) == (720, 400) and side["source"] == "vga-text"
    assert side["lines"][0] == "Press F1" and side["char_rows_hex"][0].startswith("5072657373204631")
    assert side["attr_rows_hex"][0].startswith("4e4e4e") and side["cells"][0][0] == ["yellow", "red", False]
    import hashlib
    assert side["sha256"] == hashlib.sha256((tmp_path / "corpus" / "shot1.png").read_bytes()).hexdigest()


def test_ocr_pair_graphical_refuses_unless_allowed_and_still_resumes(tmp_path):
    g = FakeGuest(png_size=(1280, 800))
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        with pytest.raises(NoExactText):
            capture.ocr_pair(c, tmp_path / "a", dump_dir=str(tmp_path))
        assert g.status == "running" and not (tmp_path / "a.png").exists()
        out = capture.ocr_pair(c, tmp_path / "b", dump_dir=str(tmp_path), allow_inexact=True)
    side = json.loads((tmp_path / "b.json").read_text())
    assert not out["exact"] and side["text"] is None and "graphical" in side["reason"] and side["width"] == 1280


def test_ocr_pair_no_pause_and_already_paused_guest(tmp_path):
    g = FakeGuest()
    with guest_server(tmp_path, g) as srv, QmpClient(str(sock(tmp_path)), 2) as c:
        capture.ocr_pair(c, tmp_path / "n", dump_dir=str(tmp_path), pause=False)
        assert "stop" not in srv.names()
        g.status = "paused"
        capture.ocr_pair(c, tmp_path / "p", dump_dir=str(tmp_path))
        assert "stop" not in srv.names() and "cont" not in srv.names() and g.status == "paused"
    assert (lambda d: d["guest_was_paused_before"] is True and d["guest_paused_for_capture"] is False)(json.loads((tmp_path / "p.json").read_text()))
