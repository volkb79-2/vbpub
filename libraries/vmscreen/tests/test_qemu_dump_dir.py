"""--qemu-dump-dir: QEMU sees the dump directory under another path (a container that mounts the guest state at /state)."""
from __future__ import annotations

import json
import os
import re

import pytest
from cli_extended import CliIdentity

from fakeqmp import FakeGuest, FakeQmp
from vmscreen import cli, sources
from vmscreen.errors import QmpError
from vmscreen.qmp import QmpClient

IDENT = CliIdentity("vmscreen", "0.1.0", "exact console text of local QEMU guests", "vmscreen")
QDIR = "/qemu-state"  # exists only for the fake QEMU: vmscreen must never touch it


def run(argv, capsys):
    code = cli.build_cli(IDENT).run(argv=argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def recording(guest, local, qdir, seen):
    """Handlers of a fake QEMU that sees ``local`` as ``qdir``: it records every file name it is told, writes into ``local`` for a
    name under ``qdir`` and, like a real QEMU in another container, cannot open any other directory."""
    handlers = guest.handlers()

    def wrap(name):
        inner = handlers[name]

        def fn(args):
            seen.append((name, args["filename"]))
            if qdir is None:
                return inner(args)
            if not args["filename"].startswith(qdir + "/"):
                raise ValueError(f"Could not open '{args['filename']}': No such file or directory")
            return inner({**args, "filename": os.path.join(str(local), args["filename"][len(qdir) + 1:])})
        return fn
    for name in ("screendump", "pmemsave"):
        handlers[name] = wrap(name)
    return handlers


@pytest.fixture
def mapped(tmp_path):
    g = FakeGuest()
    g.put(0, 0, "Welcome to the installer")
    local = tmp_path / "local"
    local.mkdir()
    seen: list[tuple[str, str]] = []
    srv = FakeQmp(tmp_path / "q.sock", recording(g, local, QDIR, seen))
    yield g, srv, seen, local, ["--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(local)]
    srv.close()


def test_screendump_sends_the_mapped_path_and_reads_the_local_file(mapped):
    _, srv, seen, local, _ = mapped
    with QmpClient(str(srv.path), 2) as c:
        png = sources.screendump_png(c, str(local), QDIR)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    [(name, sent)] = seen
    assert name == "screendump" and re.fullmatch(QDIR + r"/vmscreen-\d+-[0-9a-f]{12}\.png", sent)
    assert os.listdir(local) == []  # the local file was read and removed


def test_pmemsave_sends_the_mapped_path_and_reads_the_local_file(mapped):
    g, srv, seen, local, _ = mapped
    with QmpClient(str(srv.path), 2) as c:
        data = sources.read_phys(c, 0xB8000, 8, str(local), QDIR)
    assert data == bytes(g.mem[0xB8000:0xB8008])
    [(name, sent)] = seen
    assert name == "pmemsave" and re.fullmatch(QDIR + r"/vmscreen-\d+-[0-9a-f]{12}\.bin", sent)
    assert "human-monitor-command" not in srv.names()  # the file route worked, no xp fallback
    assert os.listdir(local) == []


def test_ocr_pair_end_to_end_with_mapping(mapped, tmp_path, capsys):
    _, srv, seen, local, base = mapped
    out = tmp_path / "out" / "pair"
    code, stdout, _ = run(["ocr-pair", *base, "--qemu-dump-dir", QDIR, "--out", str(out), "--json"], capsys)
    d = json.loads(stdout)
    assert code == 0 and d["exact"] is True
    side = json.loads((tmp_path / "out" / "pair.json").read_text())
    assert side["text"] == "Welcome to the installer" and (tmp_path / "out" / "pair.png").read_bytes()[:4] == b"\x89PNG"
    assert {n for n, _ in seen} == {"screendump", "pmemsave"}
    assert all(p.startswith(QDIR + "/") for _, p in seen)
    assert os.listdir(local) == []


def test_text_and_screen_cli_with_mapping(mapped, tmp_path, capsys):
    _, _, seen, local, base = mapped
    code, out, _ = run(["text", *base, "--qemu-dump-dir", QDIR, "--json"], capsys)
    assert code == 0 and json.loads(out)["text"] == "Welcome to the installer"
    code, _, _ = run(["screen", *base, "--qemu-dump-dir", QDIR, "--out", str(tmp_path / "s.png")], capsys)
    assert code == 0 and (tmp_path / "s.png").read_bytes()[:4] == b"\x89PNG"
    assert seen and all(p.startswith(QDIR + "/") for _, p in seen) and os.listdir(local) == []


def test_without_the_option_the_path_sent_is_the_local_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("VMSCREEN_QEMU_DUMP_DIR", raising=False)
    g = FakeGuest()
    g.put(0, 0, "plain")
    seen: list[tuple[str, str]] = []
    with FakeQmp(tmp_path / "q.sock", recording(g, tmp_path, None, seen)):
        code, out, _ = run(["text", "--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path), "--json"], capsys)
    assert code == 0 and json.loads(out)["text"] == "plain"
    assert seen and all(os.path.dirname(p) == str(tmp_path) for _, p in seen)


@pytest.mark.parametrize("verb", [["text"], ["screen", "--out", "x.png"], ["ocr-pair", "--out", "x"], ["wait-text", "a"], ["probe"]])
@pytest.mark.parametrize("opts, message", [
    (["--qemu-dump-dir", QDIR], "needs an explicit --dump-dir"),
    (["--dump-dir", "{tmp}", "--qemu-dump-dir", "state/rel"], "must be an absolute path"),
])
def test_validation_refusals_exit_2_before_connecting(tmp_path, capsys, monkeypatch, verb, opts, message):
    monkeypatch.delenv("VMSCREEN_QEMU_DUMP_DIR", raising=False)
    g = FakeGuest()
    opts = [o.replace("{tmp}", str(tmp_path)) for o in opts]
    with FakeQmp(tmp_path / "q.sock", g.handlers()) as srv:
        code, _, err = run([verb[0], "--qmp", str(tmp_path / "q.sock"), *opts, *verb[1:]], capsys)
        assert code == 2 and message in err
        assert srv.log == []  # not even the capabilities handshake: nothing connected


def test_environment_variable_alone_needs_an_explicit_dump_dir(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VMSCREEN_QEMU_DUMP_DIR", QDIR)
    with FakeQmp(tmp_path / "q.sock", FakeGuest().handlers()) as srv:
        code, _, err = run(["text", "--qmp", str(tmp_path / "q.sock")], capsys)
        assert code == 2 and "needs an explicit --dump-dir" in err and srv.log == []
        monkeypatch.setenv("VMSCREEN_QEMU_DUMP_DIR", "relative")
        code, _, err = run(["text", "--qmp", str(tmp_path / "q.sock"), "--dump-dir", str(tmp_path)], capsys)
        assert code == 2 and "absolute" in err and srv.log == []


def test_environment_variable_fallback_and_option_overrides_it(mapped, capsys, monkeypatch):
    _, _, seen, local, base = mapped
    monkeypatch.setenv("VMSCREEN_QEMU_DUMP_DIR", QDIR)
    code, _, _ = run(["text", *base, "--json"], capsys)
    assert code == 0 and seen and all(p.startswith(QDIR + "/") for _, p in seen)
    seen.clear()
    monkeypatch.setenv("VMSCREEN_QEMU_DUMP_DIR", "/somewhere/else")  # the fake would refuse it: the option must win
    code, _, _ = run(["text", *base, "--qemu-dump-dir", QDIR, "--json"], capsys)
    assert code == 0 and seen and all(p.startswith(QDIR + "/") for _, p in seen)


def test_qemu_refusal_names_both_paths(tmp_path):
    h = FakeGuest().handlers()
    h["screendump"] = lambda a: (_ for _ in ()).throw(ValueError("Could not open: Permission denied"))
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(QmpError) as ei:
            sources.screendump_png(c, str(tmp_path), QDIR)
    assert re.search(rf"QEMU writes to {QDIR}/vmscreen-\S+\.png, vmscreen reads {re.escape(str(tmp_path))}/vmscreen-\S+\.png", str(ei.value))
    assert "Permission denied" in str(ei.value)


def test_success_without_a_local_file_says_the_paths_probably_differ(tmp_path, capsys):
    h = FakeGuest().handlers()
    h["screendump"] = lambda a: {}  # QEMU reports success but wrote somewhere vmscreen does not look
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(QmpError) as ei:
            sources.screendump_png(c, str(tmp_path), QDIR)
    msg = str(ei.value)
    assert "probably do not point at the same directory" in msg and f"{QDIR}/vmscreen-" in msg and f"{tmp_path}/vmscreen-" in msg
    with FakeQmp(tmp_path / "q2.sock", h):
        code, _, err = run(["screen", "--qmp", str(tmp_path / "q2.sock"), "--dump-dir", str(tmp_path), "--qemu-dump-dir", QDIR,
                            "--out", str(tmp_path / "s.png")], capsys)
    assert code == 5 and "probably do not point at the same directory" in err and not (tmp_path / "s.png").exists()


def test_success_without_a_local_file_unmapped_keeps_the_old_message(tmp_path):
    h = FakeGuest().handlers()
    h["screendump"] = lambda a: {}
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(QmpError, match="not readable by vmscreen") as ei:
            sources.screendump_png(c, str(tmp_path))
    assert "probably" not in str(ei.value)


def test_wrong_png_content_with_mapping_names_both_paths(tmp_path):
    h = FakeGuest().handlers()
    h["screendump"] = lambda a: open(os.path.join(str(tmp_path), os.path.basename(a["filename"])), "wb").write(b"junk") and {}
    with FakeQmp(tmp_path / "q.sock", h), QmpClient(str(tmp_path / "q.sock"), 2) as c:
        with pytest.raises(QmpError, match="not a PNG.*QEMU wrote /qemu-state/.*vmscreen read "):
            sources.screendump_png(c, str(tmp_path), QDIR)
