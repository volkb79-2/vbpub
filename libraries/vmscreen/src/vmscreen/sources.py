"""Exact-text sources, tried in order and always reported.

(a) vga-text   the text buffer at 0xb8000 read through QEMU (pmemsave, or HMP xp), only in a VGA text mode
(b) serial     the -serial log file: what the guest wrote to ttyS0 (a stream, not "the screen")
(c) vcs        /dev/vcsa* through a guest shell channel: NOT implemented (needs a channel; see README)
(d) none       a graphical framebuffer: exact text is unavailable and the result says so (NoExactText)
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import serialtext, textmode
from .errors import NoExactText, QmpError

SOURCES = ("auto", "vga", "serial")


@dataclass
class TextResult:
    source: str
    scope: str  # "screen" (what is displayed) or "stream" (what the guest printed)
    text: str
    lines: list[str] = field(default_factory=list)
    geometry: dict[str, Any] = field(default_factory=dict)
    screen: textmode.TextScreen | None = None
    buffer: bytes = b""
    png: bytes | None = None
    tried: list[dict[str, Any]] = field(default_factory=list)
    exact: bool = True

    def as_dict(self, include_cells: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"exact": self.exact, "source": self.source, "scope": self.scope, "text": self.text,
                             "geometry": self.geometry, "tried": self.tried}
        if include_cells and self.screen is not None:
            d["cells"] = [[c.as_dict() for c in row] for row in self.screen.cells]
        return d


def _dump_dir(dump_dir: str | None) -> str:
    return dump_dir or tempfile.gettempdir()


def screendump_png(client, dump_dir: str | None) -> bytes:
    """Ask QEMU for a PNG screendump; QEMU writes the file, so ``dump_dir`` must be a path BOTH sides see."""
    fd, path = tempfile.mkstemp(prefix="vmscreen-", suffix=".png", dir=_dump_dir(dump_dir))
    os.close(fd)
    try:
        client.execute("screendump", filename=path, format="png")
        data = Path(path).read_bytes()
    except OSError as exc:
        raise QmpError(f"screendump file {path} not readable by vmscreen (QEMU in another container? use --dump-dir on a shared path): {exc}") from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise QmpError(f"screendump produced {len(data)} bytes that are not a PNG (does QEMU see {path}? use --dump-dir on a shared path)")
    return data


def read_phys(client, addr: int, size: int, dump_dir: str | None) -> bytes:
    """Read guest physical memory: pmemsave to a shared file, else HMP ``xp`` (slower, needs no shared path)."""
    fd, path = tempfile.mkstemp(prefix="vmscreen-", suffix=".bin", dir=_dump_dir(dump_dir))
    os.close(fd)
    try:
        client.execute("pmemsave", val=addr, size=size, filename=path)
        data = Path(path).read_bytes()
        if len(data) == size:
            return data
    except (QmpError, OSError):
        pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    data = textmode.parse_xp(client.hmp(f"xp /{size}bx {addr:#x}"))
    if len(data) != size:
        raise QmpError(f"reading {size} bytes at {addr:#x} gave {len(data)}")
    return data


def read_vga(client, dump_dir: str | None = None, blink_mode: bool = True) -> TextResult:
    png = screendump_png(client, dump_dir)
    det = textmode.detect(read_phys(client, textmode.BDA_BASE, textmode.BDA_SIZE, dump_dir), textmode.png_size(png))
    if not det.ok:
        raise NoExactText(det.reason, [{"source": "vga-text", "ok": False, "reason": det.reason}])
    buf = read_phys(client, det.base, det.size, dump_dir)
    screen = textmode.decode_buffer(buf, det.cols, det.rows, blink_mode)
    geometry = det.as_dict() | {"blink_mode": blink_mode}
    return TextResult("vga-text", "screen", screen.text(), screen.lines, geometry, screen, buf, png,
                      [{"source": "vga-text", "ok": True, "reason": det.reason}])


def read_serial(path: str, tail: int | None = None) -> TextResult:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise NoExactText(f"serial log unreadable: {exc}", [{"source": "serial", "ok": False, "reason": f"serial log unreadable: {exc}"}]) from exc
    lines = serialtext.render(raw).split("\n")
    if tail is not None:
        lines = lines[-tail:]
    return TextResult("serial", "stream", "\n".join(lines), lines, {"log": path, "bytes": len(raw)}, None, b"", None,
                      [{"source": "serial", "ok": True, "reason": f"{len(raw)} bytes of serial output"}])


def read_text(client, *, source: str = "auto", serial_log: str | None = None, dump_dir: str | None = None,
              blink_mode: bool = True, tail: int | None = None) -> TextResult:
    """Try the sources in order (a, b); the winner's ``tried`` lists every attempt and why the others did not apply."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    tried: list[dict[str, Any]] = []
    if source in ("auto", "vga"):
        try:
            res = read_vga(client, dump_dir, blink_mode)
            res.tried = tried + res.tried
            return res
        except NoExactText as exc:
            tried += exc.tried
    if source in ("auto", "serial"):
        if serial_log:
            try:
                res = read_serial(serial_log, tail)
                res.tried = tried + res.tried
                return res
            except NoExactText as exc:
                tried += exc.tried
        else:
            tried.append({"source": "serial", "ok": False, "reason": "no --serial-log given"})
    if source == "auto":
        tried.append({"source": "vcs", "ok": False, "reason": "not implemented: needs a guest shell channel (see README)"})
    tried.append({"source": "none", "ok": False,
                  "reason": "graphical framebuffer or no applicable source: exact text is unavailable (use screen + OCR)"})
    raise NoExactText("no exact-text source applies to this screen: " + "; ".join(f"{t['source']}: {t['reason']}" for t in tried), tried)
