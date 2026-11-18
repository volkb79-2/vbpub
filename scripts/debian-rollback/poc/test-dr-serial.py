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
