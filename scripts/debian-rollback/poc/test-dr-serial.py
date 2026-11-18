#!/usr/bin/env python3
"""Offline test of dr-serial.py's marker logic against a fake serial socket. No guest, no host.

  python3 -I scripts/debian-rollback/poc/test-dr-serial.py
"""
import importlib.util
import io
import re
import socket
import sys
import threading
from pathlib import Path

spec = importlib.util.spec_from_file_location("drs", Path(__file__).with_name("dr-serial.py"))
drs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drs)


def fake_guest(reply: bytes):
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a

    def serve():
        b.recv(4096)
        b.sendall(reply)

    threading.Thread(target=serve, daemon=True).start()
    return g


def main() -> int:
    failures = []

    # the echoed command line contains `$?`, the real marker contains digits: only the latter ends the command
    g = fake_guest(b"ls; echo __DR_END_$?__\r\nfile1\r\nfile2\r\n__DR_END_0__\r\n")
    rc, text = g.run("ls", 5)
    if rc != 0 or "file2" not in text or re.search(r"__DR_END_\d+__", text):
        failures.append(f"plain command: rc={rc} text={text!r}")

    # a failing command reports its exit status
    g = fake_guest(b"false; echo __DR_END_$?__\r\n__DR_END_1__\r\n")
    rc, _ = g.run("false", 5)
    if rc != 1:
        failures.append(f"exit status not carried: {rc}")

    # a marker split across two reads is still found
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a

    def serve_split():
        b.recv(4096)
        b.sendall(b"out\r\n__DR_EN")
        threading.Event().wait(0.3)
        b.sendall(b"D_7__\r\n")

    threading.Thread(target=serve_split, daemon=True).start()
    rc, _ = g.run("x", 5)
    if rc != 7:
        failures.append(f"split marker: rc={rc}")

    # login: the first probe is discarded by login (typed too early), the second is answered
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a

    def recv_until(needle: bytes) -> None:
        b.settimeout(25)
        data = b""
        while needle not in data:
            data += b.recv(256)

    def serve_login():
        recv_until(b"__DR_READY_")           # the up-front "already logged in?" probe: no shell yet, no answer
        b.sendall(b"drpoc login: ")
        recv_until(b"root\n")
        b.sendall(b"Password: ")
        recv_until(b"root\n")
        b.sendall(b"\r\nLinux drpoc 6.12 ...\r\n")
        recv_until(b"__DR_READY_")           # first probe: swallowed, as login(1) does
        recv_until(b"__DR_READY_")           # second probe: answered
        b.sendall(b"echo __DR_READY_$((20+22))__\r\n__DR_READY_42__\r\n")

    threading.Thread(target=serve_login, daemon=True).start()
    try:
        g.login(30)
    except Exception as exc:  # noqa: BLE001 - report any failure of the fake exchange
        failures.append(f"login with a swallowed first probe failed: {exc!r}")

    # already logged in: the first probe is answered, no login prompt is needed
    g = fake_guest(b"echo __DR_READY_$((20+22))__\r\n__DR_READY_42__\r\n")
    try:
        g.login(30)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"login on an already logged-in shell failed: {exc!r}")

    # tags: the echoed command line, also wrapped by the tty at 80 columns, never satisfies a check
    echoed = ("root@drpoc:~# echo OFFER:$(apt list --upgradable 2>/dev/null | grep -c '^linux-image'); echo KVER:$(uname\n"
              " -r); echo MODRC:$?\nOFFER:2\nKVER:6.12.115+deb13-amd64\nMODRC:0\n")
    if drs.tag(echoed, "OFFER") != "2" or drs.tag(echoed, "KVER") != "6.12.115+deb13-amd64" or drs.tag(echoed, "MODRC") != "0":
        failures.append(f"tag() read wrong values from {echoed!r}")
    only_echo = "root@drpoc:~# echo OFFER:$(apt list | grep -c linux-image); echo BORC:$?\n"
    if drs.tag(only_echo, "OFFER") is not None or drs.tag(only_echo, "BORC") is not None:
        failures.append("tag() matched the echoed command line (vacuous check)")
    if drs.tags("MP:/home\nMP:/srv\nNOTMP:/opt\nNOTMP:$m\nMP:$m\n", "MP") != ["/home", "/srv"]:
        failures.append("tags() with a loop variable echo")
    if drs.running_state("BOOT_IMAGE=/@os-pre-20261118072759-647-apt/boot/vmlinuz-6 rootflags=subvol=@os-pre-20261118072759-647-apt ro") != "@os-pre-20261118072759-647-apt":
        failures.append("running_state() on a snapshot state")

    # reboot proof: the boot id must change, and the prompt must come from the NEW boot
    def reboot_guest(first_id, second_id):
        a, b = socket.socketpair()
        a.setblocking(False)
        gg = drs.Guest("unused", io.StringIO())
        gg.sock = a

        def rv(needle: bytes) -> None:
            b.settimeout(30)
            data = b""
            while needle not in data:
                data += b.recv(512)

        def serve():
            rv(b"boot_id")
            b.sendall(b"echo BOOTID:$(cat /proc/sys/kernel/random/boot_id)\r\nBOOTID:" + first_id + b"\r\n__DR_END_0__\r\n")
            rv(b"reboot\n")
            b.sendall(b"[ OK ] Stopped systemd-logind.service - User Login Management.\r\nReached target reboot.target\r\n")
            b.sendall(b"drpoc login: ")
            rv(b"root\n")
            b.sendall(b"Password: ")
            rv(b"root\n")
            rv(b"__DR_READY_")
            b.sendall(b"__DR_READY_42__\r\n")
            rv(b"boot_id")
            b.sendall(b"BOOTID:" + second_id + b"\r\n__DR_END_0__\r\n")

        threading.Thread(target=serve, daemon=True).start()
        return gg

    if reboot_guest(b"aaaa-1111", b"bbbb-2222").reboot(60) != "bbbb-2222":
        failures.append("reboot did not return the new boot id")
    try:
        reboot_guest(b"same-id", b"same-id").reboot(60)
        failures.append("a reboot with an unchanged boot id was accepted")
    except RuntimeError:
        pass

    # QMP: greeting, capabilities, an event before the reply, then the reply
    import json
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        qpath = os.path.join(tmp, "qmp.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(qpath)
        srv.listen(1)
        seen = []

        def qmp_server():
            conn, _ = srv.accept()
            f = conn.makefile("rw")
            f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
            f.flush()
            for _ in range(2):
                req = json.loads(f.readline())
                seen.append(req["execute"])
                if req["execute"] == "system_reset":
                    f.write(json.dumps({"event": "RESET", "data": {}}) + "\n")
                f.write(json.dumps({"return": {}}) + "\n")
                f.flush()
            conn.close()

        threading.Thread(target=qmp_server, daemon=True).start()
        reply = drs.qmp_command(qpath, "system_reset", timeout=10)
        if reply != {"return": {}} or seen != ["qmp_capabilities", "system_reset"]:
            failures.append(f"qmp_command: reply={reply!r} seen={seen!r}")
        srv.close()

    # silence times out instead of hanging
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a
    try:
        g.run("x", 1)
        failures.append("silent guest did not time out")
    except TimeoutError:
        pass

    if failures:
        print("FAIL\n" + "\n".join(failures))
        return 1
    print("test-dr-serial: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
