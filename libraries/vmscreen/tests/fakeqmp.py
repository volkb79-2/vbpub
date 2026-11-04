"""A fake QMP server on a unix socket plus a tiny fake guest (BIOS data area, VGA text page, screendump)."""
from __future__ import annotations

import json
import socket
import struct
import threading
import zlib
from pathlib import Path
from typing import Any, Callable


def make_png(w: int, h: int, ink: set[tuple[int, int]] | None = None, filter_type: int = 0) -> bytes:
    """A black RGB PNG with white pixels at ``ink`` (x, y); ``filter_type`` 0-4 applies that PNG row filter to every row."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    ink = ink or set()
    rows = [bytearray(3 * w) for _ in range(h)]
    for x, y in ink:
        rows[y][3 * x:3 * x + 3] = b"\xff\xff\xff"
    out, prev = bytearray(), bytearray(3 * w)
    for row in rows:
        out.append(filter_type)
        if filter_type == 0:
            out += row
            continue
        for i in range(3 * w):
            a = row[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            pred = (0, a, b, (a + b) >> 1, _paeth(a, b, c))[filter_type]
            out.append((row[i] - pred) & 255)
        prev = row
    raw = bytes(out)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


class FakeQmp:
    """Serves one connection at a time; ``handlers[name](args) -> return value`` or raises ValueError(desc) for an error reply."""

    def __init__(self, path: Path, handlers: dict[str, Callable[[dict], Any]] | None = None, *, greeting: bool = True,
                 events: bool = False, silent: set[str] | None = None, junk_on: str | None = None,
                 drop_reply: set[str] | None = None, bad_utf8_once: set[str] | None = None) -> None:
        self.path = str(path)
        self.handlers = handlers or {}
        self.greeting, self.events, self.silent, self.junk_on = greeting, events, silent or set(), junk_on
        self.bad_utf8_once = bad_utf8_once or set()  # answer the first such command with undecodable bytes
        self._cur = None
        self.drop_reply = drop_reply or set()  # run the handler, then never answer (a lost reply)
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
            self._cur = conn
            try:
                with conn, conn.makefile("rw", encoding="utf-8") as f:
                    self._conn(f)
            except OSError:
                pass  # the client went away (closed, timed out, killed): serve the next connection

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
            if name in self.bad_utf8_once:
                self.bad_utf8_once.discard(name)
                f.flush()
                self._cur.sendall(b"\xff\xfe\n")
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
                    value = self.handlers[name](args)
                    if name in self.drop_reply:
                        continue
                    send({"return": value})
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

    display_offset = 0       # the page the "CRTC" actually displays, in bytes from 0xb8000 (the BDA still says 0)
    extra_ink: set = frozenset()  # additional white pixels in the rendered screendump (e.g. a cursor block)
    graphic_noise = False    # paint ink everywhere: a graphical mode that happens to have a text-sized screendump

    def render(self) -> bytes:
        w, h = self.png_size or (640, 480)
        ink: set[tuple[int, int]] = set()
        if self.graphic_noise:
            ink = {(x, y) for y in range(0, h, 3) for x in range(0, w, 5)}
        elif (w, h) == (self.cols * 9, self.rows * 16):
            for r in range(self.rows):
                for c in range(self.cols):
                    off = 0xB8000 + self.display_offset + (r * self.cols + c) * 2
                    code, attr = self.mem[off], self.mem[off + 1]
                    if code not in (0, 0x20, 0xFF) and (attr & 0x0F) != (attr >> 4) & 7:
                        ink |= {(c * 9 + dx, r * 16 + dy) for dx in (1, 2) for dy in (4, 5)}
        ink = set(ink) | set(self.extra_ink)
        return make_png(w, h, ink, filter_type=getattr(self, "filter_type", 0))

    def handlers(self) -> dict[str, Callable[[dict], Any]]:
        def screendump(a: dict) -> dict:
            Path(a["filename"]).write_bytes(self.render())
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
