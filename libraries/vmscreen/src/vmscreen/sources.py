"""Exact-text sources, tried in order and always reported.

(a) vga-text   the text buffer at 0xb8000 read through QEMU (pmemsave, or HMP xp), only in a VGA text mode
(b) serial     the -serial log file: what the guest wrote to ttyS0 (a stream, not "the screen")
(c) vcs        /dev/vcsa* through a guest shell channel: NOT implemented (needs a channel; see README)
(d) none       a graphical framebuffer: exact text is unavailable and the result says so (NoExactText)

``exact`` values: True (decoded buffer agrees with the screendump on every comparable cell), "unverified" (decoded but the
cross-check failed or could not run: the reason is in ``exact_reason``), "stream" (serial: complete log text, not a screen),
False (nothing).
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pixels, serialtext, textmode
from .errors import NoExactText, QmpError, VmscreenError

SOURCES = ("auto", "vga", "serial")
STABLE_ATTEMPTS = 3

ASSUMPTIONS = {
    "font": "glyph codes are mapped with the default VGA cp437 set (256 glyphs); a 512-glyph or custom-font console may map codes differently",
    "blink_mode": "attribute bit 7 is read as blink (BIOS default) unless --no-blink-mode; the attribute-controller register is not readable through QMP",
}


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
    exact: Any = True
    exact_reason: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    assumptions: dict[str, str] = field(default_factory=dict)

    def as_dict(self, include_cells: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"exact": self.exact, "exact_reason": self.exact_reason, "source": self.source, "scope": self.scope,
                             "text": self.text, "geometry": self.geometry, "verification": self.verification,
                             "assumptions": self.assumptions, "tried": self.tried}
        if include_cells and self.screen is not None:
            d["cells"] = [[c.as_dict() for c in row] for row in self.screen.cells]
        return d


def _dump_dir(dump_dir: str | None) -> str:
    return dump_dir or tempfile.gettempdir()


def _scratch_path(dump_dir: str | None, suffix: str) -> str:
    """A unique NOT-yet-existing path: QEMU creates the file itself, so it may run as another user and
    must not meet a 0600 file we own.  The directory must be writable by QEMU's user."""
    d = _dump_dir(dump_dir)
    if not os.path.isdir(d) or not os.access(d, os.R_OK | os.W_OK | os.X_OK):
        raise VmscreenError(f"--dump-dir {d} is not a directory vmscreen can read and write (check existence and permissions)")
    return os.path.join(d, f"vmscreen-{os.getpid()}-{secrets.token_hex(6)}{suffix}")


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _perm_hint(exc: QmpError, d: str | None) -> QmpError:
    return QmpError(f"{exc} (the QEMU process must be able to write into --dump-dir {_dump_dir(d)}: check directory permissions "
                    "and that QEMU sees the same path, e.g. not running in another container)")


def screendump_png(client, dump_dir: str | None) -> bytes:
    """Ask QEMU for a PNG screendump; QEMU writes the file, so ``dump_dir`` must be a path BOTH sides see."""
    path = _scratch_path(dump_dir, ".png")
    try:
        try:
            client.execute("screendump", filename=path, format="png")
        except QmpError as exc:
            raise _perm_hint(exc, dump_dir) from exc
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise QmpError(f"screendump file {path} not readable by vmscreen ({exc}); permissions of --dump-dir, or QEMU in another container "
                           "that does not share the path") from exc
    finally:
        _unlink(path)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise QmpError(f"screendump produced {len(data)} bytes that are not a PNG (does QEMU see {path}? use --dump-dir on a shared path)")
    return data


def read_phys(client, addr: int, size: int, dump_dir: str | None) -> bytes:
    """Read guest physical memory: pmemsave to a shared file, else HMP ``xp`` (slower, needs no shared path)."""
    path = _scratch_path(dump_dir, ".bin")
    try:
        client.execute("pmemsave", val=addr, size=size, filename=path)
        data = Path(path).read_bytes()
        if len(data) == size:
            return data
    except (QmpError, OSError):
        pass
    finally:
        _unlink(path)
    data = textmode.parse_xp(client.hmp(f"xp /{size}bx {addr:#x}"))
    if len(data) != size:
        raise QmpError(f"reading {size} bytes at {addr:#x} gave {len(data)}")
    return data


def read_vga(client, dump_dir: str | None = None, blink_mode: bool = True, stable_check: bool = True) -> TextResult:
    """PNG, BIOS data area and text buffer are read as a set; with ``stable_check`` the screendump is taken again afterwards and
    the set is retried when it changed (pass False when the guest is paused).  The result is only ``exact: True`` when the decoded
    buffer also agrees with the screendump cell by cell (pixels.verify_ink)."""
    stable = True
    for _ in range(STABLE_ATTEMPTS if stable_check else 1):
        png = screendump_png(client, dump_dir)
        det = textmode.detect(read_phys(client, textmode.BDA_BASE, textmode.BDA_SIZE, dump_dir), textmode.png_size(png))
        if not det.ok:
            raise NoExactText(det.reason, [{"source": "vga-text", "ok": False, "reason": det.reason}])
        buf = read_phys(client, det.base, det.size, dump_dir)
        stable = not stable_check or screendump_png(client, dump_dir) == png
        if stable:
            break
    screen = textmode.decode_buffer(buf, det.cols, det.rows, blink_mode)
    ver = pixels.verify_ink(png, det.cols, det.rows, det.cell_w, det.cell_h, buf, det.cursor, blink_mode)
    if not stable:
        exact, reason = "unverified", f"the screen changed during all {STABLE_ATTEMPTS} reads (text, BDA and screendump are not from one instant)"
    elif ver["ok"] is True:
        exact, reason = True, ""
    else:
        exact, reason = "unverified", ver.get("reason", "cross-check against the screendump failed")
    geometry = det.as_dict() | {"blink_mode": blink_mode}
    return TextResult("vga-text", "screen", screen.text(), screen.lines, geometry, screen, buf, png,
                      [{"source": "vga-text", "ok": True, "reason": det.reason}], exact, reason, ver, dict(ASSUMPTIONS))


_ESC_BYTES = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()#][0-9A-Za-z]|\x1b[=>78DEHMNOc]")
HEAD = 64  # bytes of the log's start fingerprinted by a mark


def make_mark(path: str) -> str:
    """``OFFSET.INODE.HEADLEN.HEADHASH`` of a serial log now.  Passed back as ``mark=``, it detects a log that was truncated or
    replaced and has regrown past the offset (a bare byte offset cannot)."""
    st = os.stat(path)
    head = Path(path).read_bytes()[:HEAD]
    return f"{st.st_size}.{st.st_ino}.{len(head)}.{hashlib.sha256(head).hexdigest()[:16]}"


def parse_mark(token: str) -> tuple[int, int, int, str]:
    try:
        off, ino, n, h = token.split(".")
        return int(off), int(ino), int(n), h
    except ValueError as exc:
        raise ValueError(f"malformed serial mark {token!r} (expected the output of `vmscreen mark`)") from exc


def _boundary(raw: bytes, offset: int) -> int:
    """Move ``offset`` forward out of the middle of an escape sequence or a UTF-8 character, so a mark taken while the guest was
    mid-write does not start the text with a torn sequence."""
    lo = max(0, offset - 64)
    for m in _ESC_BYTES.finditer(raw, lo, offset + 64):
        if m.start() < offset < m.end():
            offset = m.end()
    while offset < len(raw) and 0x80 <= raw[offset] < 0xC0:  # UTF-8 continuation byte
        offset += 1
    return offset


def read_serial(path: str, from_offset: int = 0, mark: str | None = None) -> TextResult:
    def fail(msg: str) -> NoExactText:
        return NoExactText(msg, [{"source": "serial", "ok": False, "reason": msg}])
    ino = hn = hh = None
    if mark is not None:
        from_offset, ino, hn, hh = parse_mark(mark)
    try:
        raw = Path(path).read_bytes()
        st = os.stat(path)
    except OSError as exc:
        raise fail(f"serial log unreadable: {exc}") from exc
    if mark is not None and (st.st_ino != ino or len(raw) < hn or hashlib.sha256(raw[:hn]).hexdigest()[:16] != hh):
        raise fail("serial log was truncated or replaced since the mark (inode or first bytes changed): marks no longer apply")
    if from_offset > len(raw):
        raise fail(f"serial log is {len(raw)} bytes, shorter than --from-offset {from_offset} (truncated or rotated?)")
    start = _boundary(raw, from_offset)
    lines = serialtext.render(raw[start:]).split("\n")
    return TextResult("serial", "stream", "\n".join(lines), lines,
                      {"log": path, "bytes": len(raw), "from_offset": from_offset, "start": start}, None, b"", None,
                      [{"source": "serial", "ok": True, "reason": f"{len(raw) - start} bytes of serial output"}], "stream",
                      "text the guest printed to the serial port (rendered log, not a screen)")


def read_text(client, *, source: str = "auto", serial_log: str | None = None, dump_dir: str | None = None,
              blink_mode: bool = True, from_offset: int = 0, mark: str | None = None) -> TextResult:
    """Try the sources in order (a, b); the winner's ``tried`` lists every attempt and why the others did not apply.  In ``auto`` a
    VGA result that is only ``unverified`` counts as a failed attempt: later sources are tried, and the unverified text (flagged)
    is returned only if nothing else applies.  ``--source vga`` returns it as is."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    tried: list[dict[str, Any]] = []
    fallback: TextResult | None = None
    if source in ("auto", "vga"):
        try:
            res = read_vga(client, dump_dir, blink_mode)
            if res.exact is True or source == "vga":
                res.tried = tried + res.tried
                return res
            tried.append({"source": "vga-text", "ok": False, "reason": f"decoded but unverified: {res.exact_reason}"})
            fallback = res
        except NoExactText as exc:
            tried += exc.tried
    if source in ("auto", "serial"):
        if serial_log:
            try:
                res = read_serial(serial_log, from_offset, mark)
                res.tried = tried + res.tried
                return res
            except NoExactText as exc:
                tried += exc.tried
        else:
            tried.append({"source": "serial", "ok": False, "reason": "no --serial-log given"})
    if fallback is not None:
        fallback.tried = tried + [{"source": "vga-text", "ok": True, "reason": "returned UNVERIFIED because no other source applied"}]
        return fallback
    if source == "auto":
        tried.append({"source": "vcs", "ok": False, "reason": "not implemented: needs a guest shell channel (see README)"})
    tried.append({"source": "none", "ok": False,
                  "reason": "graphical framebuffer or no applicable source: exact text is unavailable (use screen + OCR)"})
    raise NoExactText("no exact-text source applies to this screen: " + "; ".join(f"{t['source']}: {t['reason']}" for t in tried), tried)
