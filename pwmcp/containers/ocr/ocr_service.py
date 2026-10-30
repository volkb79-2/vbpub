#!/usr/bin/env python3
"""pwmcp OCR sidecar: a stateless HTTP service that turns an image into text, confidences and boxes.

Standard library only (plus Pillow, from the distro archive, for decoding/upscaling and the
tesseract CLI for recognition). It returns TEXT AND CONFIDENCE, never decisions: nothing here
watches, clicks or sends keys.

Endpoints
  GET  /health            200 {"status":"ok"} when the default engine is usable, else 503
  GET  /v1/engines        engine names, versions, languages, availability
  POST /v1/ocr            body = PNG/JPEG bytes; query: engine, lang, psm, scale, whitelist, min_conf

Every failure is JSON ``{"error": <message>, "code": <machine code>}`` with a 4xx/5xx status;
image bytes are never echoed.  One recognition runs at a time (a second concurrent request gets
429 immediately).  Configuration is by environment variable (see ``Config.from_env``).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

DEFAULT_ENGINE = "tesseract"
DEFAULT_LANG = "eng"
ALLOWED_PSM = (3, 4, 6, 7, 11, 13)
MAX_SCALE = 8
MAX_SCALED_DIM = 8192
MAX_WHITELIST = 128
# Tesseract language codes: three letters, optional script/variant suffix, joined by '+'.
_LANG_RE = re.compile(r"^[a-z]{3}(_[A-Za-z]{1,12})?(\+[a-z]{3}(_[A-Za-z]{1,12})?)*$")
_KNOWN_PARAMS = frozenset({"engine", "lang", "psm", "scale", "whitelist", "min_conf", "drop_low_conf"})


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"  # noqa: S104 - the container network is the boundary; nothing is published
    port: int = 8941
    max_bytes: int = 8 * 1024 * 1024
    max_dim: int = 4096
    timeout_s: float = 20.0
    tesseract: str = "tesseract"
    socket_timeout_s: float = 30.0
    body_deadline_s: float = 10.0  # TOTAL time allowed to receive a request body (slow-client guard)
    max_scaled_pixels: int = 24_000_000  # width*height AFTER --scale, checked before resizing
    header_deadline_s: float = 10.0  # TOTAL time allowed to receive the request line + headers
    max_connections: int = 16  # concurrent handler threads; further connections get a JSON 503
    drain_bytes: int = 1024 * 1024  # how much of a rejected upload is read (and discarded) after the reply
    drain_s: float = 1.0  # ... and for how long at most

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if env is None else env)
        return cls(
            host=env.get("OCR_BIND", cls.host),
            port=int(env.get("OCR_PORT", cls.port)),
            max_bytes=int(env.get("OCR_MAX_BYTES", cls.max_bytes)),
            max_dim=int(env.get("OCR_MAX_DIM", cls.max_dim)),
            timeout_s=float(env.get("OCR_TIMEOUT_S", cls.timeout_s)),
            tesseract=env.get("OCR_TESSERACT", cls.tesseract),
            body_deadline_s=float(env.get("OCR_BODY_DEADLINE_S", cls.body_deadline_s)),
            max_scaled_pixels=int(env.get("OCR_MAX_SCALED_PIXELS", cls.max_scaled_pixels)),
            header_deadline_s=float(env.get("OCR_HEADER_DEADLINE_S", cls.header_deadline_s)),
            max_connections=int(env.get("OCR_MAX_CONNECTIONS", cls.max_connections)),
        )


class ServiceError(Exception):
    """An error with an HTTP status and a stable machine code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Word:
    text: str
    conf: float | None  # None: the engine returned the word without a confidence ("unscored")
    box: tuple[int, int, int, int]  # x, y, w, h in the pixels of the image the engine was given
    line: tuple[int, ...]  # engine-defined line identity; words sharing it are one line


@dataclass(frozen=True)
class Params:
    engine: str = DEFAULT_ENGINE
    lang: str = "eng"
    psm: int = 6
    scale: int = 1
    whitelist: str | None = None
    min_conf: float | None = None  # None = no threshold given: nothing is flagged, and nothing is claimed
    drop_low_conf: bool = False  # opt-in: really remove the flagged words from the result


class Engine(Protocol):
    """What the service needs from a recognition engine; ``glyphgrid`` will implement it later."""

    name: str

    def available(self) -> bool:
        raise NotImplementedError(
            "Engine.available must be implemented by a concrete engine"
        )

    def version(self) -> str | None:
        raise NotImplementedError(
            "Engine.version must be implemented by a concrete engine"
        )

    def languages(self) -> list[str]:
        raise NotImplementedError(
            "Engine.languages must be implemented by a concrete engine"
        )

    def unavailable_reason(self) -> str | None:
        raise NotImplementedError(
            "Engine.unavailable_reason must be implemented by a concrete engine"
        )

    def recognize(self, png: bytes, params: Params, timeout_s: float) -> list[Word]:
        raise NotImplementedError(
            "Engine.recognize must be implemented by a concrete engine"
        )


class UnavailableEngine:
    """A registered engine that is explicitly not implemented: every use is a clear 501."""

    def __init__(self, name: str, reason: str = "engine not implemented") -> None:
        self.name = name
        self._reason = reason

    def available(self) -> bool:
        return False

    def version(self) -> str | None:
        return None

    def languages(self) -> list[str]:
        return []

    def unavailable_reason(self) -> str | None:
        return self._reason

    def recognize(self, png: bytes, params: Params, timeout_s: float) -> list[Word]:
        raise ServiceError(501, "engine_unavailable", self._reason)


def parse_tsv(tsv: str) -> list[Word]:
    """Parse Tesseract TSV (level, page, block, par, line, word, left, top, width, height, conf, text).

    Only word rows (level 5) with non-empty text are kept (the layout rows, levels 1-4, carry no
    text). A word row whose confidence is negative (``-1``) is kept as an UNSCORED word with
    ``conf=None`` rather than dropped: a recognised token must never vanish from the result.
    Empty output is simply an empty list.
    """

    words: list[Word] = []
    # Split on "\n" only (a trailing "\r" is stripped): str.splitlines() would also break at
    # \x0b \x0c \x1c-\x1e \x85 \u2028 \u2029 inside a recognised word.
    for raw in tsv.split("\n"):
        if raw.endswith("\r"):
            raw = raw[:-1]
        cols = raw.split("\t", 11)
        if len(cols) < 12 or cols[0] != "5":
            continue  # header row, layout rows, or a malformed row
        try:
            block, par, line = int(cols[2]), int(cols[3]), int(cols[4])
            x, y, w, h = (int(v) for v in cols[6:10])
            conf = float(cols[10])
        except ValueError:
            continue
        text = cols[11].strip(" \t\r")
        if not text:
            continue
        words.append(Word(text, conf if conf >= 0 else None, (x, y, w, h), (block, par, line)))
    return words


class TesseractEngine:
    name = "tesseract"

    def __init__(self, binary: str = "tesseract") -> None:
        self._binary = binary
        self._lock = threading.Lock()
        self._version: str | None = None
        self._languages: list[str] | None = None

    def _run(self, argv: list[str], timeout_s: float, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_THREAD_LIMIT": "1", "LC_ALL": "C"}
        return subprocess.run(  # noqa: S603 - argv list, no shell, every element validated by the caller
            [self._binary, *argv],
            input=stdin,
            capture_output=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    def unavailable_reason(self) -> str | None:
        return None if self.available() else "tesseract binary not found"

    def version(self) -> str | None:
        with self._lock:
            if self._version is None and self.available():
                try:
                    done = self._run(["--version"], 10)
                    text = (done.stdout + done.stderr).decode("utf-8", "replace")
                    found = re.search(r"tesseract\s+v?(\S+)", text)
                    self._version = found.group(1) if found else "unknown"
                except (OSError, subprocess.SubprocessError):
                    self._version = "unknown"
            return self._version

    def languages(self) -> list[str]:
        with self._lock:
            if self._languages is None:
                if not self.available():
                    return []
                try:
                    done = self._run(["--list-langs"], 10)
                    text = (done.stdout + done.stderr).decode("utf-8", "replace")
                    # First line is "List of available languages (N):"; the rest one code per line.
                    self._languages = sorted(
                        line.strip() for line in text.splitlines()[1:] if re.fullmatch(r"[A-Za-z_]+", line.strip())
                    )
                except (OSError, subprocess.SubprocessError):
                    return []
            return list(self._languages)

    def recognize(self, png: bytes, params: Params, timeout_s: float) -> list[Word]:
        argv = ["stdin", "stdout", "-l", params.lang, "--psm", str(params.psm)]
        if params.whitelist is not None:
            argv += ["-c", f"tessedit_char_whitelist={params.whitelist}"]
        argv.append("tsv")
        try:
            done = self._run(argv, timeout_s, stdin=png)
        except subprocess.TimeoutExpired as exc:
            raise ServiceError(504, "timeout", f"recognition exceeded {timeout_s:g} s") from exc
        except OSError as exc:
            raise ServiceError(503, "engine_unavailable", "tesseract could not be started") from exc
        if done.returncode != 0:
            # The engine's own diagnostics stay server-side (log only); clients get a fixed message.
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            sys.stderr.write(f"[pwmcp-ocr] tesseract exited {done.returncode}: {detail[0][:300]!r}\n")
            raise ServiceError(500, "engine_failed", "the recognition engine failed")
        return parse_tsv(done.stdout.decode("utf-8", "replace"))


def build_registry(config: Config) -> dict[str, Engine]:
    return {
        "tesseract": TesseractEngine(config.tesseract),
        # Phase 2 plugs the exact glyph-grid matcher in here; until then it is a clear 501.
        "glyphgrid": UnavailableEngine("glyphgrid"),
    }


# ---------------------------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------------------------


def parse_params(query: str, registry: dict[str, Engine]) -> Params:
    """Validate the query string; every violation is a 400 with a specific code."""

    pairs = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    unknown = sorted(set(pairs) - _KNOWN_PARAMS)
    if unknown:
        raise ServiceError(400, "unknown_param", f"unknown query parameter: {unknown[0][:40]}")
    for key, values in pairs.items():
        if len(values) != 1:
            raise ServiceError(400, "duplicate_param", f"query parameter given more than once: {key}")
    got = {key: values[0] for key, values in pairs.items()}

    engine = got.get("engine", DEFAULT_ENGINE)
    if engine not in registry:
        raise ServiceError(400, "unknown_engine", f"unknown engine (known: {', '.join(sorted(registry))})")

    lang = got.get("lang", "eng")
    if not _LANG_RE.fullmatch(lang):
        raise ServiceError(400, "invalid_lang", "lang must be Tesseract language codes such as eng or eng+deu")

    psm_raw = got.get("psm", "6")
    if not re.fullmatch(r"[0-9]{1,2}", psm_raw) or int(psm_raw) not in ALLOWED_PSM:
        raise ServiceError(400, "invalid_psm", f"psm must be one of {', '.join(map(str, ALLOWED_PSM))}")

    scale_raw = got.get("scale", "1")
    try:
        scale_f = float(scale_raw)
    except ValueError:
        scale_f = float("nan")
    if not (1 <= scale_f <= MAX_SCALE) or scale_f != int(scale_f):
        raise ServiceError(400, "invalid_scale", f"scale must be an integer from 1 to {MAX_SCALE}")

    whitelist = got.get("whitelist")
    if whitelist is not None:
        if not whitelist or len(whitelist) > MAX_WHITELIST or not all(0x20 <= ord(c) <= 0x7E for c in whitelist):
            raise ServiceError(
                400, "invalid_whitelist", f"whitelist must be 1-{MAX_WHITELIST} printable ASCII characters"
            )

    min_conf: float | None = None
    if "min_conf" in got:
        try:
            min_conf = float(got["min_conf"])
        except ValueError:
            min_conf = float("nan")
        if not (0 <= min_conf <= 100):
            raise ServiceError(400, "invalid_min_conf", "min_conf must be a number from 0 to 100")

    drop_raw = got.get("drop_low_conf", "0")
    if drop_raw not in ("0", "1"):
        raise ServiceError(400, "invalid_drop_low_conf", "drop_low_conf must be 0 or 1")
    if drop_raw == "1" and min_conf is None:
        raise ServiceError(400, "drop_needs_min_conf", "drop_low_conf=1 needs a min_conf threshold to drop below")

    return Params(engine, lang, int(psm_raw), int(scale_f), whitelist, min_conf, drop_raw == "1")


def prepare_image(body: bytes, params: Params, config: Config) -> tuple[bytes, int, int]:
    """Validate and decode the image, returning (PNG bytes for the engine, width, height).

    Width/height are those of the ORIGINAL image; the PNG handed to the engine is upscaled by
    ``params.scale``. Pillow reads the header lazily, so the dimension limit is enforced before
    any pixel data is decoded (a decompression-bomb guard).
    """

    from PIL import Image, UnidentifiedImageError  # distro package; imported lazily for the pure-logic tests

    Image.MAX_IMAGE_PIXELS = config.max_dim * config.max_dim
    try:
        # Only the PNG and JPEG plugins ever see untrusted bytes.
        image = Image.open(io.BytesIO(body), formats=["PNG", "JPEG"])
        width, height = image.size
    except Image.DecompressionBombError as exc:  # more than 2x the pixel budget, found from the header alone
        raise ServiceError(413, "image_too_large", f"image exceeds {config.max_dim}x{config.max_dim} pixels") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ServiceError(415, "unsupported_image", "body is not a decodable PNG or JPEG image") from exc
    # A multi-picture JPEG (MPO, e.g. from some cameras) is a JPEG container: its first frame is used.
    # (formats=["PNG","JPEG"] above means the image is a PNG, a JPEG or its MPO variant: nothing else can get here)
    if width > config.max_dim or height > config.max_dim:
        raise ServiceError(413, "image_too_large", f"image exceeds {config.max_dim}x{config.max_dim} pixels")
    if width * params.scale > MAX_SCALED_DIM or height * params.scale > MAX_SCALED_DIM:
        raise ServiceError(400, "invalid_scale", f"scaled image would exceed {MAX_SCALED_DIM} pixels")
    if width * height * params.scale * params.scale > config.max_scaled_pixels:
        raise ServiceError(
            413, "scaled_image_too_large",
            f"scaled image would exceed {config.max_scaled_pixels} pixels; lower scale or send a smaller image",
        )
    try:
        image.load()
        flat = _flatten(image)
        if params.scale > 1:
            flat = flat.resize((width * params.scale, height * params.scale), Image.Resampling.BICUBIC)
        out = io.BytesIO()
        flat.save(out, format="PNG")
    except (OSError, ValueError, SyntaxError) as exc:
        raise ServiceError(415, "unsupported_image", "image is truncated or corrupt") from exc
    return out.getvalue(), width, height


def _flatten(image: "object") -> "object":
    """8-bit L or RGB for the engine: transparency composited onto WHITE, 16-bit scaled down."""

    from PIL import Image

    mode = image.mode  # type: ignore[attr-defined]
    if mode in ("RGBA", "LA", "PA") or (mode == "P" and "transparency" in image.info):  # type: ignore[attr-defined]
        rgba = image.convert("RGBA")  # type: ignore[attr-defined]
        backdrop = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(backdrop, rgba).convert("RGB")
    if mode in ("I;16", "I;16L", "I;16B", "I;16N", "I"):
        # 16-bit grayscale: map 0..65535 to 0..255 explicitly instead of clipping.
        wide = image.convert("I")  # type: ignore[attr-defined]
        return wide.point(lambda value: value * (1 / 256)).convert("L")
    if mode in ("L", "RGB"):
        return image
    return image.convert("RGB")  # type: ignore[attr-defined]


def build_response(
    words: list[Word], params: Params, width: int, height: int, engine: Engine, elapsed_ms: int
) -> dict[str, object]:
    """Assemble the JSON: boxes un-scaled to original pixels, lines rebuilt from the words.

    The result is COMPLETE: every recognised word is returned whatever its confidence.
    ``min_conf`` only FLAGS words below it (``low_conf``); a caller that really wants them
    removed must ask with ``drop_low_conf=1`` (and is told how many were dropped).
    WITHOUT a ``min_conf`` nothing was assessed: ``low_conf``, ``low_conf_words`` and
    ``min_conf_applied`` are null (not false/0, which would read as "confident").
    Tesseract confidence is NOT calibrated: a flag is a hint, an unflagged word is not verified.
    """

    def unscale(box: tuple[int, int, int, int]) -> list[int]:
        # Outward rounding: the original-pixel box always contains the scaled one, and a box
        # with width/height > 0 can never collapse to 0.
        s = params.scale
        x, y, w, h = box
        x0, y0 = x // s, y // s
        x1, y1 = -(-(x + w) // s), -(-(y + h) // s)
        return [x0, y0, x1 - x0 if w > 0 else 0, y1 - y0 if h > 0 else 0]

    threshold = params.min_conf

    def flagged(word: Word) -> bool | None:
        # No threshold: None ("not assessed"), never False, which would read as "confident".
        # An unscored word cannot be vouched for, so with a threshold it counts as flagged.
        if threshold is None:
            return None
        return word.conf is None or word.conf < threshold

    total_flagged = None if threshold is None else sum(1 for w in words if flagged(w))
    kept = [w for w in words if not flagged(w)] if params.drop_low_conf else list(words)
    order: dict[tuple[int, ...], int] = {}
    members: list[list[Word]] = []
    for word in kept:
        if word.line not in order:
            order[word.line] = len(members)
            members.append([])
        members[order[word.line]].append(word)

    lines = []
    for group in members:
        x0 = min(w.box[0] for w in group)
        y0 = min(w.box[1] for w in group)
        x1 = max(w.box[0] + w.box[2] for w in group)
        y1 = max(w.box[1] + w.box[3] for w in group)
        scored = [w.conf for w in group if w.conf is not None]
        mean = round(sum(scored) / len(scored), 2) if scored else None
        lines.append({
            "text": " ".join(w.text for w in group),
            "conf": mean,  # compatibility alias of conf_mean
            "conf_mean": mean,
            "conf_min": min(scored) if scored else None,
            "low_conf_words": None if threshold is None else sum(1 for w in group if flagged(w)),
            "box": unscale((x0, y0, x1 - x0, y1 - y0)),
        })
    out_words = [
        {"text": w.text, "conf": w.conf, "box": unscale(w.box), "line": order[w.line], "low_conf": flagged(w)}
        for w in kept
    ]
    return {
        "engine": params.engine,
        "engine_version": engine.version(),
        "lang": params.lang,
        "psm": params.psm,
        "width": width,
        "height": height,
        "elapsed_ms": elapsed_ms,
        "text": "\n".join(str(line["text"]) for line in lines),
        "lines": lines,
        "words": out_words,
        "min_conf_applied": threshold,
        "low_conf_words": total_flagged,
        "unscored_words": sum(1 for w in words if w.conf is None),
        "drop_low_conf": params.drop_low_conf,
        "dropped_words": len(words) - len(kept),
    }


@dataclass
class Service:
    config: Config
    registry: dict[str, Engine]
    busy: threading.Lock = field(default_factory=threading.Lock)

    def health(self) -> tuple[int, dict[str, object]]:
        engine = self.registry[DEFAULT_ENGINE]
        if not engine.available():
            return 503, {"status": "unavailable", "reason": engine.unavailable_reason()}
        # The binary alone is not enough: without the default language's traineddata every
        # recognition would fail. (The language list is cached after the first successful read.)
        if DEFAULT_LANG not in engine.languages():
            return 503, {"status": "unavailable",
                         "reason": f"no traineddata for the default language {DEFAULT_LANG!r} is installed"}
        return 200, {"status": "ok"}

    def engines(self) -> dict[str, object]:
        return {"engines": [
            {
                "name": name,
                "available": engine.available(),
                "version": engine.version(),
                "languages": engine.languages(),
                **({} if engine.available() else {"reason": engine.unavailable_reason()}),
            }
            for name, engine in sorted(self.registry.items())
        ]}

    def recognize(self, body: bytes, query: str) -> dict[str, object]:
        params = parse_params(query, self.registry)
        engine = self.registry[params.engine]
        if not engine.available():
            raise ServiceError(501, "engine_unavailable", engine.unavailable_reason() or "engine unavailable")
        installed = engine.languages()
        if installed and not set(params.lang.split("+")) <= set(installed):
            raise ServiceError(400, "unsupported_lang", f"language not installed (installed: {', '.join(installed)})")
        png, width, height = prepare_image(body, params, self.config)
        started = time.monotonic()
        words = engine.recognize(png, params, self.config.timeout_s)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return build_response(words, params, width, height, engine, elapsed_ms)


def make_handler(service: Service) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = service.config.socket_timeout_s  # a stalled upload cannot hold the slot forever
        server_version = "pwmcp-ocr"
        sys_version = ""
        _header_timer = threading.Timer(0, lambda: None)  # never started; replaced per request

        def handle_one_request(self) -> None:
            # A TOTAL deadline for the request line + headers (a per-recv timeout alone lets a
            # client trickle one byte at a time forever).
            timer = threading.Timer(service.config.header_deadline_s, self._header_deadline)
            timer.daemon = True
            self._header_timer = timer
            timer.start()
            try:
                super().handle_one_request()
            finally:
                timer.cancel()

        def parse_request(self) -> bool:
            ok = super().parse_request()
            self._header_timer.cancel()  # the headers are in; the body has its own deadline
            return ok

        def _header_deadline(self) -> None:
            body = json.dumps({"error": "request line and headers not received in time",
                               "code": "header_timeout"}).encode("utf-8")
            with contextlib.suppress(OSError):
                self.connection.sendall(
                    b"HTTP/1.0 408 Request Timeout\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
                )
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)  # unblocks the pending read

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            """Every error the stdlib generates (400/414/431/501...) uses the JSON error shape."""

            self.request_version = "HTTP/1.0"  # always answer in full, even for a request line that never parsed
            status = HTTPStatus(code)  # the stdlib only ever passes real status codes
            self.log_error("code %d", code)
            self._send(code, {"error": status.phrase.lower(), "code": status.name.lower()})

        def _send(self, status: int, payload: dict[str, object], *, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.close_connection = True
            with contextlib.suppress(OSError):  # the client may already be gone (no traceback noise)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                # Status line, headers and body leave in ONE write, so a client never sees a headers-only
                # packet (end_headers() would flush the headers first and write the body separately).
                self._headers_buffer.append(b"\r\n")
                if self.command != "HEAD":
                    self._headers_buffer.append(body)
                self.flush_headers()

        def _error(self, err: ServiceError) -> None:
            extra = {"Retry-After": "1"} if err.status == 429 else None
            self._send(err.status, {"error": err.message, "code": err.code}, extra=extra)
            if self._unread_body:
                self._linger()

        _unread_body = False

        def _linger(self) -> None:
            """Reading the rest of a rejected upload (bounded) keeps the client from seeing a reset.

            The reply is already sent; half-close, then swallow up to 1 MiB for at most about a
            second so a still-sending client reads the status instead of ECONNRESET.
            """

            with contextlib.suppress(OSError):  # the peer may already be gone
                self.connection.shutdown(socket.SHUT_WR)
                deadline = time.monotonic() + service.config.drain_s
                budget = service.config.drain_bytes
                while True:
                    remaining = deadline - time.monotonic()
                    if budget <= 0 or remaining <= 0:
                        break
                    self.connection.settimeout(remaining)
                    chunk = self.rfile.read1(min(65536, budget))
                    if not chunk:
                        break
                    budget -= len(chunk)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlsplit(self.path).path
            if path == "/health":
                status, payload = service.health()
                self._send(status, payload)
            elif path == "/v1/engines":
                self._send(200, service.engines())
            elif path == "/v1/ocr":
                self._error(ServiceError(405, "method_not_allowed", "use POST"))
            else:
                self._error(ServiceError(404, "not_found", "no such endpoint"))

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            parts = urlsplit(self.path)
            # Any rejection while a body is still on the wire gets the bounded drain, so the
            # client reads the status instead of a connection reset. Cleared once it is read.
            declared = self.headers.get("Content-Length", "")
            self._unread_body = bool(re.fullmatch(r"[0-9]+", declared)) and (len(declared) > 18 or int(declared) > 0)
            if parts.path != "/v1/ocr":
                known = parts.path in ("/health", "/v1/engines")
                self._error(
                    ServiceError(405, "method_not_allowed", "use GET")
                    if known else ServiceError(404, "not_found", "no such endpoint")
                )
                return
            # Parameters are validated BEFORE the slot is taken and the body is read: a request that
            # will be rejected must not hold the recognition slot while its body trickles in.
            try:
                parse_params(parts.query, service.registry)
            except ServiceError as err:
                self._error(err)
                return
            # One recognition at a time: take the slot BEFORE reading the body so at most one
            # (size-limited) body is ever held in memory; a concurrent caller is told to retry.
            if not service.busy.acquire(blocking=False):
                self._error(ServiceError(429, "busy", "another recognition is in progress; retry shortly"))
                return
            # The slot is released BEFORE the reply is written: a client that sends its next
            # request the instant it has read this reply must never see a spurious 429.
            outcome: dict[str, object] | ServiceError | None
            try:
                outcome = self._compute(parts.query)
            finally:
                service.busy.release()
            if isinstance(outcome, ServiceError):
                self._error(outcome)
            elif outcome is None:
                self.close_connection = True  # client went away or stalled mid-upload
            else:
                self._send(200, outcome)

        def _compute(self, query: str) -> "dict[str, object] | ServiceError | None":
            try:
                return service.recognize(self._read_body(), query)
            except ServiceError as err:
                return err
            except (OSError, TimeoutError):
                return None
            except Exception:  # noqa: BLE001 - never leak internals or image data to the client
                return ServiceError(500, "internal_error", "internal error")

        def _read_body(self) -> bytes:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                raise ServiceError(411, "length_required", "send Content-Length; chunked bodies are not accepted")
            raw = self.headers.get("Content-Length")
            # ASCII digits only: str.isdigit() accepts "\u00b2", which int() then rejects (a 500).
            if raw is None or not re.fullmatch(r"[0-9]+", raw):
                raise ServiceError(411, "length_required", "Content-Length is required")
            if len(raw) > 18 or int(raw) > service.config.max_bytes:
                raise ServiceError(413, "payload_too_large", f"body exceeds {service.config.max_bytes} bytes")
            length = int(raw)
            if length == 0:
                raise ServiceError(400, "empty_body", "empty request body")
            return self._read_exact(length)

        def _read_exact(self, length: int) -> bytes:
            """Read the body against a TOTAL deadline, not merely a per-recv timeout."""

            deadline = time.monotonic() + service.config.body_deadline_s
            chunks: list[bytes] = []
            got = 0
            try:
                while got < length:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    self.connection.settimeout(min(remaining, service.config.socket_timeout_s))
                    chunk = self.rfile.read1(min(65536, length - got))
                    if not chunk:
                        raise ServiceError(400, "short_body", "request body shorter than Content-Length")
                    chunks.append(chunk)
                    got += len(chunk)
            except TimeoutError as exc:
                raise ServiceError(
                    408, "request_timeout",
                    f"request body not received within {service.config.body_deadline_s:g} s",
                ) from exc
            finally:
                self.connection.settimeout(service.config.socket_timeout_s)
            self._unread_body = False
            return b"".join(chunks)

        def log_message(self, fmt: str, *args: object) -> None:
            # Access log without the query string (a whitelist is operator data) or any body.
            # (self.path / self.command do not exist yet for a request line that never parsed)
            path = urlsplit(getattr(self, "path", "")).path
            command = getattr(self, "command", None) or "-"
            sys.stderr.write(f"{self.address_string()} {command} {path} -> {args[1] if len(args) > 1 else ''}\n")

    return Handler


class BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a cap on concurrent handler threads: the rest get a JSON 503."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], max_connections: int) -> None:
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request: "socket.socket", client_address: object) -> None:  # type: ignore[override]
        if not self._slots.acquire(blocking=False):
            body = json.dumps({"error": "too many connections; retry shortly", "code": "overloaded"}).encode("utf-8")
            with contextlib.suppress(OSError):
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                    b"Retry-After: 1\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
                )
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: "socket.socket", client_address: object) -> None:  # type: ignore[override]
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(config: Config, registry: dict[str, Engine] | None = None) -> ThreadingHTTPServer:
    service = Service(config, registry if registry is not None else build_registry(config))
    return BoundedServer((config.host, config.port), make_handler(service), config.max_connections)


def main() -> int:
    config = Config.from_env()
    server = make_server(config)

    def stop(_signum: int, _frame: object) -> None:
        # shutdown() blocks until serve_forever() returns, so it must not run on the thread
        # serve_forever() occupies (this handler interrupts that very thread).
        threading.Thread(target=server.shutdown, daemon=True).start()

    # As PID 1 a default SIGTERM disposition is ignored by the kernel; install handlers.
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    # Announce readiness only AFTER the handlers are installed: whoever waits for this line may signal at once.
    print(f"[pwmcp-ocr] listening on {config.host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
