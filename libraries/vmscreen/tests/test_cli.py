"""CLI behaviour (exit codes, --json) against the fake QMP server."""
from __future__ import annotations

import json

import pytest
from cli_extended import CliIdentity

from fakeqmp import FakeGuest, FakeQmp, make_png
from vmscreen import cli

IDENT = CliIdentity("vmscreen", "0.1.0", "exact console text of local QEMU guests", "vmscreen")


def run(argv, capsys):
    code = cli.build_cli(IDENT).run(argv=argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def guest_sock(tmp_path):
    g = FakeGuest()
    g.put(0, 0, "Welcome to the installer")
    g.put(1, 0, "login:")
    srv = FakeQmp(tmp_path / "q.sock", g.handlers())
    yield g, srv, ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path)]
    srv.close()


def test_identity_has_no_literal_fallback():
    with pytest.raises(Exception):
        cli.identity()  # vmscreen is not installed in the test environment


def test_text_json_reports_source(guest_sock, capsys):
    _, _, base = guest_sock
    code, out, _ = run(["text", *base, "--json"], capsys)
    d = json.loads(out)
    assert code == 0 and d["ok"] and d["source"] == "vga-text" and d["exact"] and d["text"] == "Welcome to the installer\nlogin:"


def test_text_human_tail_and_cells(guest_sock, capsys):
    _, _, base = guest_sock
    code, out, err = run(["text", *base, "--tail", "1"], capsys)
    assert code == 0 and out.strip() == "login:" and "source: vga-text" in err
    code, out, _ = run(["text", *base, "--json", "--cells"], capsys)
    assert json.loads(out)["cells"][1][0]["c"] == "l"


def test_wait_text_ok_and_timeout_exit_3(guest_sock, capsys):
    _, _, base = guest_sock
    code, out, _ = run(["wait-text", "^login:", *base, "--timeout", "2", "--json"], capsys)
    assert code == 0 and json.loads(out)["matched"] == "login:"
    code, out, err = run(["wait-text", "never.*", *base, "--timeout", "0", "--json"], capsys)
    d = json.loads(out)
    assert code == 3 and d["ok"] is False and "login:" in d["last_text"] and "not seen" in err
    code, out, _ = run(["wait-text", "a.b", "--literal", *base, "--timeout", "0"], capsys)
    assert code == 3


def test_graphical_screen_exits_4_with_tried_list(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))
    with FakeQmp(tmp_path / "q.sock", g.handlers()):
        base = ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path)]
        code, out, _ = run(["text", *base, "--json"], capsys)
        d = json.loads(out)
        assert code == 4 and d["exact"] is False and {t["source"] for t in d["tried"]} >= {"vga-text", "none"}
        code, out, _ = run(["probe", *base], capsys)
        d = json.loads(out)  # non-tty stdout is JSON-less in human mode: probe always prints its object
        assert code == 0 and d["exact_text_available"] is False and d["vm_status"] == "running"
        code, out, _ = run(["ocr-pair", *base, "--out", str(tmp_path / "x")], capsys)
        assert code == 4


def test_probe_success(guest_sock, capsys):
    _, _, base = guest_sock
    code, out, _ = run(["probe", *base, "--json"], capsys)
    assert code == 0 and json.loads(out)["source"] == "vga-text"


def test_screen_writes_png(guest_sock, tmp_path, capsys):
    _, _, base = guest_sock
    code, out, _ = run(["screen", *base, "--out", str(tmp_path / "s.png"), "--json"], capsys)
    assert code == 0 and json.loads(out)["width"] == 720 and (tmp_path / "s.png").read_bytes()[:4] == b"\x89PNG"


def test_send_keys_type_and_validation(guest_sock, capsys):
    g, srv, base = guest_sock
    code, out, _ = run(["send", *base, "--keys", "ctrl+alt+f2,ret", "--json"], capsys)
    assert code == 0 and json.loads(out)["sent"] == 2
    code, _, _ = run(["send", *base, "--type", "Hi"], capsys)
    assert code == 0 and srv.names().count("send-key") == 4
    before = srv.names().count("send-key")
    code, _, err = run(["send", *base, "--type", "é"], capsys)
    assert code == 2 and "unmappable" in err and srv.names().count("send-key") == before
    code, _, _ = run(["send", *base], capsys)
    assert code == 2
    code, _, _ = run(["send", *base, "--keys", "a", "--type", "b"], capsys)
    assert code == 2


def test_send_type_file(guest_sock, tmp_path, capsys):
    _, srv, base = guest_sock
    f = tmp_path / "secret.txt"
    f.write_text("ab")
    assert run(["send", *base, "--type-file", str(f)], capsys)[0] == 0 and srv.names().count("send-key") == 2


def test_ocr_pair_cli(guest_sock, tmp_path, capsys):
    _, _, base = guest_sock
    code, out, _ = run(["ocr-pair", *base, "--out", str(tmp_path / "p" / "one"), "--json"], capsys)
    d = json.loads(out)
    assert code == 0 and d["exact"] and (tmp_path / "p" / "one.json").exists()


def test_qmp_problems_exit_5_and_missing_socket_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("VMSCREEN_QMP", raising=False)
    code, _, err = run(["text", "--qmp", str(tmp_path / "gone.sock")], capsys)
    assert code == 5 and "cannot connect" in err
    code, _, err = run(["text"], capsys)
    assert code == 2 and "VMSCREEN_QMP" in err
    monkeypatch.setenv("VMSCREEN_QMP", str(tmp_path / "gone.sock"))
    assert run(["text"], capsys)[0] == 5


def test_serial_source_via_cli(tmp_path, capsys):
    g = FakeGuest(png_size=(1280, 800))
    log = tmp_path / "s.log"
    log.write_bytes(b"hello\r\nlogin: ")
    with FakeQmp(tmp_path / "q.sock", g.handlers()):
        base = ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path), "--serial-log", str(log)]
        code, out, _ = run(["text", *base, "--json"], capsys)
        d = json.loads(out)
        assert code == 0 and d["source"] == "serial" and d["scope"] == "stream"
        assert run(["wait-text", "login:", *base, "--source", "serial", "--timeout", "1"], capsys)[0] == 0


def test_main_uses_installed_identity():
    with pytest.raises(Exception):
        cli.main(["--help"])  # not installed: identity() fails loudly instead of inventing a version
