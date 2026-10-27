"""A fake QMP server on a unix socket plus a tiny fake guest (BIOS data area, VGA text page, screendump)."""
from __future__ import annotations

import json
import socket
import struct
import threading
import zlib
from pathlib import Path
from typing import Any, Callable


def make_png(w: int, h: int) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeQmp:
    """Serves one connection at a time; ``handlers[name](args) -> return value`` or raises ValueError(desc) for an error reply."""

    def __init__(self, path: Path, handlers: dict[str, Callable[[dict], Any]] | None = None, *, greeting: bool = True,
                 events: bool = False, silent: set[str] | None = None, junk_on: str | None = None) -> None:
        self.path = str(path)
        self.handlers = handlers or {}
        self.greeting, self.events, self.silent, self.junk_on = greeting, events, silent or set(), junk_on
        self.log: list[tuple[str, dict]] = []
        self._srv = socket.socket(socket.AF_UNIX)
        self._srv.bind(self.path)
        self._srv.listen(8)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop = True
        try:
            socket.socket(socket.AF_UNIX).connect(self.path)
        except OSError:
            pass
        self._srv.close()

    def __enter__(self) -> "FakeQmp":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            if self._stop:
                conn.close()
                return
            with conn, conn.makefile("rw", encoding="utf-8") as f:
                self._conn(f)

    def _conn(self, f) -> None:
        def send(obj: Any) -> None:
            f.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
            f.flush()
        if self.greeting:
            send({"QMP": {"version": {}, "capabilities": []}})
        else:
            send({"nope": 1})
            return
        while True:
            line = f.readline()
            if not line:
                return
            msg = json.loads(line)
            name, args = msg["execute"], msg.get("arguments", {})
            self.log.append((name, args))
            if name in self.silent:
                continue
            if name == self.junk_on:
                send("this is not json")
                continue
            if self.events:
                send({"event": "RTC_CHANGE", "data": {}})
            if name == "qmp_capabilities":
                send({"return": {}})
            elif name in self.handlers:
                try:
                    send({"return": self.handlers[name](args)})
                except ValueError as exc:
                    send({"error": {"class": "GenericError", "desc": str(exc)}})
            else:
                send({"error": {"class": "CommandNotFound", "desc": f"no {name}"}})

    def names(self) -> list[str]:
        return [n for n, _ in self.log]


class FakeGuest:
    """Physical memory + display; ``handlers()`` plugs into FakeQmp."""

    def __init__(self, cols: int = 80, rows: int = 25, mode: int = 3, png_size: tuple[int, int] | None = (720, 400)) -> None:
        self.mem = bytearray(0x100000)
        self.png_size = png_size
        self.status = "running"
        self.xp_only = False  # pmemsave fails, forcing the HMP xp fallback
        self.mem[0x449] = mode
        struct.pack_into("<H", self.mem, 0x44A, cols)
        self.mem[0x484] = rows - 1
        struct.pack_into("<H", self.mem, 0x485, 16)
        self.cols, self.rows = cols, rows

    def put(self, row: int, col: int, text: str, attr: int = 0x07) -> None:
        for i, ch in enumerate(text):
            off = 0xB8000 + (row * self.cols + col + i) * 2
            self.mem[off], self.mem[off + 1] = ord(ch), attr

    def handlers(self) -> dict[str, Callable[[dict], Any]]:
        def screendump(a: dict) -> dict:
            Path(a["filename"]).write_bytes(make_png(*(self.png_size or (640, 480))))
            return {}

        def pmemsave(a: dict) -> dict:
            if self.xp_only:
                raise ValueError("pmemsave refused")
            Path(a["filename"]).write_bytes(bytes(self.mem[a["val"]:a["val"] + a["size"]]))
            return {}

        def hmp(a: dict) -> str:
            cmd = a["command-line"]  # "xp /<n>bx 0x<addr>"
            n = int(cmd.split()[1][1:-2])
            addr = int(cmd.split()[2], 16)
            data = self.mem[addr:addr + n]
            return "".join(f"{addr + i:016x}:" + "".join(f" 0x{b:02x}" for b in data[i:i + 8]) + "\r\n" for i in range(0, n, 8))

        def stop(_: dict) -> dict:
            self.status = "paused"
            return {}

        def cont(_: dict) -> dict:
            self.status = "running"
            return {}

        return {"screendump": screendump, "pmemsave": pmemsave, "human-monitor-command": hmp, "stop": stop, "cont": cont,
                "query-status": lambda _: {"status": self.status, "running": self.status == "running"}, "send-key": lambda _: {}}
