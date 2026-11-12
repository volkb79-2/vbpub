"""Exact-text sources, tried in order and always reported.

(a) vga-text   the text buffer at 0xb8000 read through QEMU (pmemsave, or HMP xp), only in a VGA text mode
(b) serial     the -serial log file: what the guest wrote to ttyS0 (a stream, not "the screen")
(c) vcs        /dev/vcsa* through a guest shell channel: NOT implemented (needs a channel; see README)
(d) none       a graphical framebuffer: exact text is unavailable and the result says so (NoExactText)

``exact`` values: True (decoded buffer agrees with the screendump on every comparable cell), "unverified" (decoded but the
cross-check failed or could not run: the reason is in ``exact_reason``), "stream" (serial: complete log text, not a screen),
"partial" (every cell verified except the ``uncertain_cells``, which show U+FFFD in the text), False (nothing).
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import secrets
import sys
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


def _qemu_path(path: str, qemu_dump_dir: str | None) -> str:
    """The path QEMU is told to write: the same file name under ``qemu_dump_dir`` (how the QEMU process sees the directory
    vmscreen sees as ``--dump-dir``); without a mapping it is the local path itself."""
    return posixpath.join(qemu_dump_dir, os.path.basename(path)) if qemu_dump_dir else path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _perm_hint(exc: QmpError, d: str | None, qemu_dump_dir: str | None = None, local: str = "", remote: str = "") -> QmpError:
    if qemu_dump_dir:
        return QmpError(f"{exc} (QEMU writes to {remote}, vmscreen reads {local}: the QEMU process must be able to write into "
                        f"{qemu_dump_dir}, and --qemu-dump-dir {qemu_dump_dir} must be the directory vmscreen sees as --dump-dir {_dump_dir(d)})")
    return QmpError(f"{exc} (the QEMU process must be able to write into --dump-dir {_dump_dir(d)}: check directory permissions "
                    "and that QEMU sees the same path, e.g. not running in another container)")


def screendump_png(client, dump_dir: str | None, qemu_dump_dir: str | None = None) -> bytes:
    """Ask QEMU for a PNG screendump; QEMU writes the file, so ``dump_dir`` must be a path BOTH sides see, or ``qemu_dump_dir``
    must be the path under which QEMU sees ``dump_dir``."""
    path = _scratch_path(dump_dir, ".png")
    remote = _qemu_path(path, qemu_dump_dir)
    try:
        try:
            client.execute("screendump", filename=remote, format="png")
        except QmpError as exc:
            raise _perm_hint(exc, dump_dir, qemu_dump_dir, path, remote) from exc
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            if qemu_dump_dir:
                raise QmpError(f"QEMU reported success writing {remote}, but vmscreen cannot read {path} ({exc}): --dump-dir {_dump_dir(dump_dir)} "
                               f"and --qemu-dump-dir {qemu_dump_dir} probably do not point at the same directory") from exc
            raise QmpError(f"screendump file {path} not readable by vmscreen ({exc}); permissions of --dump-dir, or QEMU in another container "
                           "that does not share the path") from exc
    finally:
        _unlink(path)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        if qemu_dump_dir:
            raise QmpError(f"screendump produced {len(data)} bytes that are not a PNG (QEMU wrote {remote}, vmscreen read {path}: do "
                           "--dump-dir and --qemu-dump-dir point at the same directory?)")
        raise QmpError(f"screendump produced {len(data)} bytes that are not a PNG (does QEMU see {path}? use --dump-dir on a shared path)")
    return data


def read_phys(client, addr: int, size: int, dump_dir: str | None, qemu_dump_dir: str | None = None) -> bytes:
    """Read guest physical memory: pmemsave to a shared file, else HMP ``xp`` (slower, needs no shared path)."""
    path = _scratch_path(dump_dir, ".bin")
    try:
        remote = _qemu_path(path, qemu_dump_dir)
        client.execute("pmemsave", val=addr, size=size, filename=remote)
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            if qemu_dump_dir:  # QEMU said yes but the file is not where vmscreen looks: tell once, then use xp
                print(f"vmscreen: pmemsave reported success writing {remote}, but vmscreen cannot read {path} ({exc}): --dump-dir "
                      f"{_dump_dir(dump_dir)} and --qemu-dump-dir {qemu_dump_dir} probably do not point at the same directory; "
                      "falling back to HMP xp", file=sys.stderr)
            raise
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


def read_vga(client, dump_dir: str | None = None, blink_mode: bool = True, stable_check: bool = True,
             qemu_dump_dir: str | None = None) -> TextResult:
    """PNG, BIOS data area and text buffer are read as a set; with ``stable_check`` the screendump is taken again afterwards and
    the set is retried when it changed (pass False when the guest is paused).  The result is only ``exact: True`` when the decoded
    buffer also agrees with the screendump cell by cell (pixels.verify_ink)."""
    stable = True
    for _ in range(STABLE_ATTEMPTS if stable_check else 1):
        png = screendump_png(client, dump_dir, qemu_dump_dir)
        det = textmode.detect(read_phys(client, textmode.BDA_BASE, textmode.BDA_SIZE, dump_dir, qemu_dump_dir), textmode.png_size(png))
        if not det.ok:
            raise NoExactText(det.reason, [{"source": "vga-text", "ok": False, "reason": det.reason}])
        buf = read_phys(client, det.base, det.size, dump_dir, qemu_dump_dir)
        stable = not stable_check or screendump_png(client, dump_dir, qemu_dump_dir) == png
        if stable:
            break
    screen = textmode.decode_buffer(buf, det.cols, det.rows, blink_mode)
    ver = pixels.verify_ink(png, det.cols, det.rows, det.cell_w, det.cell_h, buf, det.cursor, blink_mode)
    uncertain = ver.get("uncertain_cells", []) if ver["ok"] is True else []
    for u in uncertain:  # a visible placeholder instead of a character we cannot vouch for
        old = screen.cells[u["row"]][u["col"]]
        screen.cells[u["row"]][u["col"]] = textmode.Cell("\ufffd", old.fg, old.bg, old.blink)
    if not stable:
        exact, reason = "unverified", f"the screen changed during all {STABLE_ATTEMPTS} reads (text, BDA and screendump are not from one instant)"
    elif ver["ok"] is True and uncertain:
        exact, reason = "partial", f"{len(uncertain)} cell(s) could not be verified and show U+FFFD (see uncertain_cells); all other cells agree with the screendump"
    elif ver["ok"] is True:
        exact, reason = True, ""
    else:
        exact, reason = "unverified", ver.get("reason", "cross-check against the screendump failed")
    geometry = det.as_dict() | {"blink_mode": blink_mode, "uncertain_cells": uncertain}
    return TextResult("vga-text", "screen", screen.text(), screen.lines, geometry, screen, buf, png,
                      [{"source": "vga-text", "ok": True, "reason": det.reason}], exact, reason, ver, dict(ASSUMPTIONS))


_ESC_BYTES = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()#][0-9A-Za-z]|\x1b[=>78DEHMNOc]")
HEAD = 64  # bytes of the log's start, and of the window ENDING at the offset, fingerprinted by a mark
_MARK_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)\.([0-9a-f]{16})\.(\d+)\.([0-9a-f]{16})", re.ASCII)


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def make_mark(path: str) -> str:
    """``OFFSET.INODE.HEADLEN.HEADHASH.TAILLEN.TAILHASH`` of a serial log now: the first 64 bytes AND the last 64 bytes before the
    offset are fingerprinted, so a log truncated and regrown by a restarted VM that prints the same banner is still caught (its
    content before the offset differs).  Reads two small windows only (seek), never the whole log."""
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        ino = os.fstat(f.fileno()).st_ino
        head = f.read(HEAD)
        tl = min(HEAD, size)
        f.seek(size - tl)
        tail = f.read(tl)
    return f"{size}.{ino}.{len(head)}.{_h(head)}.{len(tail)}.{_h(tail)}"


def parse_mark(token: str) -> tuple[int, int, int, str, int, str]:
    m = _MARK_RE.fullmatch(token)  # strict: ASCII digits only, no sign, underscore or whitespace
    if m is None:
        raise ValueError(f"malformed serial mark {token!r} (expected the output of `vmscreen mark`)")
    off, ino, hn, hh, tn, th = m.groups()
    return int(off), int(ino), int(hn), hh, int(tn), th


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
    ino = hn = hh = tn = th = None
    if mark is not None:
        from_offset, ino, hn, hh, tn, th = parse_mark(mark)
    try:
        raw = Path(path).read_bytes()
        st = os.stat(path)
    except OSError as exc:
        raise fail(f"serial log unreadable: {exc}") from exc
    if mark is not None and (st.st_ino != ino or tn > from_offset or len(raw) < max(hn, from_offset) or _h(raw[:hn]) != hh
                             or _h(raw[from_offset - tn:from_offset]) != th):
        raise fail("serial log was truncated or replaced since the mark (inode, first bytes or the bytes before the offset changed): "
                   "marks no longer apply")
    if from_offset > len(raw):
        raise fail(f"serial log is {len(raw)} bytes, shorter than --from-offset {from_offset} (truncated or rotated?)")
    start = _boundary(raw, from_offset)
    lines = serialtext.render(raw[start:]).split("\n")
    empty = not "".join(lines).strip()
    return TextResult("serial", "stream", "\n".join(lines), lines,
                      {"log": path, "bytes": len(raw), "from_offset": from_offset, "start": start, "empty": empty}, None, b"", None,
                      [{"source": "serial", "ok": True, "reason": f"{len(raw) - start} bytes of serial output"}], "stream",
                      "the serial log has no text (empty log, or nothing after the offset)" if empty else
                      "text the guest printed to the serial port (rendered log, not a screen)")


def read_text(client, *, source: str = "auto", serial_log: str | None = None, dump_dir: str | None = None,
              blink_mode: bool = True, from_offset: int = 0, mark: str | None = None, qemu_dump_dir: str | None = None) -> TextResult:
    """Try the sources in order (a, b); the winner's ``tried`` lists every attempt and why the others did not apply.

    In ``auto``, a VGA result that is only ``unverified``/``partial`` and an EMPTY serial result (empty log, nothing after the
    offset; typical when the console is on tty0) each count as a failed attempt.  Order of last resort: the flagged VGA text, else
    the empty serial result (exit 0, empty text, ``exact: "stream"``, reason says it is empty; ``wait-text`` simply keeps polling).
    ``--source vga`` / ``--source serial`` return their result as is."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    tried: list[dict[str, Any]] = []
    fallback: TextResult | None = None
    empty_serial: TextResult | None = None
    if source in ("auto", "vga"):
        try:
            res = read_vga(client, dump_dir, blink_mode, qemu_dump_dir=qemu_dump_dir)
            if res.exact is True or source == "vga":
                res.tried = tried + res.tried
                return res
            tried.append({"source": "vga-text", "ok": False, "reason": f"decoded but {res.exact}: {res.exact_reason}"})
            fallback = res
        except NoExactText as exc:
            tried += exc.tried
    if source in ("auto", "serial"):
        if serial_log:
            try:
                res = read_serial(serial_log, from_offset, mark)
                if source == "auto" and res.geometry["empty"]:
                    tried.append({"source": "serial", "ok": False, "reason": res.exact_reason})
                    empty_serial = res
                else:
                    res.tried = tried + res.tried
                    return res
            except NoExactText as exc:
                tried += exc.tried
        else:
            tried.append({"source": "serial", "ok": False, "reason": "no --serial-log given"})
    if fallback is not None:
        fallback.tried = tried + [{"source": "vga-text", "ok": True, "reason": f"returned {fallback.exact.upper()} because no other source applied"}]
        return fallback
    if empty_serial is not None:
        empty_serial.tried = tried
        return empty_serial
    if source == "auto":
        tried.append({"source": "vcs", "ok": False, "reason": "not implemented: needs a guest shell channel (see README)"})
    tried.append({"source": "none", "ok": False,
                  "reason": "graphical framebuffer or no applicable source: exact text is unavailable (use screen + OCR)"})
    raise NoExactText("no exact-text source applies to this screen: " + "; ".join(f"{t['source']}: {t['reason']}" for t in tried), tried)
