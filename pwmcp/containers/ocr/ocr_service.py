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

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

DEFAULT_ENGINE = "tesseract"
ALLOWED_PSM = (3, 4, 6, 7, 11, 13)
MAX_SCALE = 8
MAX_SCALED_DIM = 8192
MAX_WHITELIST = 128
# Tesseract language codes: three letters, optional script/variant suffix, joined by '+'.
_LANG_RE = re.compile(r"^[a-z]{3}(_[A-Za-z]{1,12})?(\+[a-z]{3}(_[A-Za-z]{1,12})?)*$")
_KNOWN_PARAMS = frozenset({"engine", "lang", "psm", "scale", "whitelist", "min_conf"})


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"  # noqa: S104 - the container network is the boundary; nothing is published
    port: int = 8941
    max_bytes: int = 8 * 1024 * 1024
    max_dim: int = 4096
    timeout_s: float = 20.0
    tesseract: str = "tesseract"
    socket_timeout_s: float = 30.0

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
    conf: float
    box: tuple[int, int, int, int]  # x, y, w, h in the pixels of the image the engine was given
    line: tuple[int, ...]  # engine-defined line identity; words sharing it are one line


@dataclass(frozen=True)
class Params:
    engine: str = DEFAULT_ENGINE
    lang: str = "eng"
    psm: int = 6
    scale: int = 1
    whitelist: str | None = None
    min_conf: float = 0.0


class Engine(Protocol):
    """What the service needs from a recognition engine; ``glyphgrid`` will implement it later."""

    name: str

    def available(self) -> bool: ...
    def version(self) -> str | None: ...
    def languages(self) -> list[str]: ...
    def unavailable_reason(self) -> str | None: ...
    def recognize(self, png: bytes, params: Params, timeout_s: float) -> list[Word]: ...


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

    Only word rows (level 5) with non-empty text and a non-negative confidence are kept: the
    layout rows (levels 1-4) and ``-1`` confidences carry no recognised text. Empty output is
    simply an empty list.
    """

    words: list[Word] = []
    for raw in tsv.splitlines():
        cols = raw.split("\t", 11)
        if len(cols) < 12 or cols[0] != "5":
            continue  # header row, layout rows, or a malformed row
        try:
            block, par, line = int(cols[2]), int(cols[3]), int(cols[4])
            x, y, w, h = (int(v) for v in cols[6:10])
            conf = float(cols[10])
        except ValueError:
            continue
        text = cols[11].strip()
        if not text or conf < 0:
            continue
        words.append(Word(text, conf, (x, y, w, h), (block, par, line)))
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
            # stderr is the engine's own diagnostics, never image data; keep it short.
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise ServiceError(500, "engine_failed", f"tesseract exited {done.returncode}: {detail[0][:200]}")
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

    min_raw = got.get("min_conf", "0")
    try:
        min_conf = float(min_raw)
    except ValueError:
        min_conf = float("nan")
    if not (0 <= min_conf <= 100):
        raise ServiceError(400, "invalid_min_conf", "min_conf must be a number from 0 to 100")

    return Params(engine, lang, int(psm_raw), int(scale_f), whitelist, min_conf)


def prepare_image(body: bytes, params: Params, config: Config) -> tuple[bytes, int, int]:
    """Validate and decode the image, returning (PNG bytes for the engine, width, height).

    Width/height are those of the ORIGINAL image; the PNG handed to the engine is upscaled by
    ``params.scale``. Pillow reads the header lazily, so the dimension limit is enforced before
    any pixel data is decoded (a decompression-bomb guard).
    """

    from PIL import Image, UnidentifiedImageError  # distro package; imported lazily for the pure-logic tests

    Image.MAX_IMAGE_PIXELS = config.max_dim * config.max_dim
    try:
        image = Image.open(io.BytesIO(body))
        fmt, (width, height) = image.format, image.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ServiceError(415, "unsupported_image", "body is not a decodable PNG or JPEG image") from exc
    if fmt not in ("PNG", "JPEG"):
        raise ServiceError(415, "unsupported_image", "only PNG and JPEG images are accepted")
    if width > config.max_dim or height > config.max_dim:
        raise ServiceError(413, "image_too_large", f"image exceeds {config.max_dim}x{config.max_dim} pixels")
    if width * params.scale > MAX_SCALED_DIM or height * params.scale > MAX_SCALED_DIM:
        raise ServiceError(400, "invalid_scale", f"scaled image would exceed {MAX_SCALED_DIM} pixels")
    try:
        image.load()
        flat = image.convert("RGB") if image.mode not in ("L", "RGB") else image
        if params.scale > 1:
            flat = flat.resize((width * params.scale, height * params.scale), Image.Resampling.BICUBIC)
        out = io.BytesIO()
        flat.save(out, format="PNG")
    except (OSError, ValueError, SyntaxError) as exc:
        raise ServiceError(415, "unsupported_image", "image is truncated or corrupt") from exc
    return out.getvalue(), width, height


def build_response(
    words: list[Word], params: Params, width: int, height: int, engine: Engine, elapsed_ms: int
) -> dict[str, object]:
    """Assemble the JSON: boxes un-scaled to original pixels, min_conf applied, lines rebuilt."""

    def unscale(box: tuple[int, int, int, int]) -> list[int]:
        return [round(v / params.scale) for v in box]

    kept = [w for w in words if w.conf >= params.min_conf]
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
        lines.append({
            "text": " ".join(w.text for w in group),
            "conf": round(sum(w.conf for w in group) / len(group), 2),
            "box": unscale((x0, y0, x1 - x0, y1 - y0)),
        })
    out_words = [
        {"text": w.text, "conf": w.conf, "box": unscale(w.box), "line": order[w.line]} for w in kept
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
    }


@dataclass
class Service:
    config: Config
    registry: dict[str, Engine]
    busy: threading.Lock = field(default_factory=threading.Lock)

    def health(self) -> tuple[int, dict[str, object]]:
        if self.registry[DEFAULT_ENGINE].available():
            return 200, {"status": "ok"}
        return 503, {"status": "unavailable", "reason": self.registry[DEFAULT_ENGINE].unavailable_reason()}

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

        def _send(self, status: int, payload: dict[str, object], *, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _error(self, err: ServiceError) -> None:
            extra = {"Retry-After": "1"} if err.status == 429 else None
            self._send(err.status, {"error": err.message, "code": err.code}, extra=extra)

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
            if parts.path != "/v1/ocr":
                known = parts.path in ("/health", "/v1/engines")
                self._error(
                    ServiceError(405, "method_not_allowed", "use GET")
                    if known else ServiceError(404, "not_found", "no such endpoint")
                )
                return
            # One recognition at a time: take the slot BEFORE reading the body so at most one
            # (size-limited) body is ever held in memory; a concurrent caller is told to retry.
            if not service.busy.acquire(blocking=False):
                self._error(ServiceError(429, "busy", "another recognition is in progress; retry shortly"))
                return
            try:
                self._send_result(parts.query)
            finally:
                service.busy.release()

        def _send_result(self, query: str) -> None:
            try:
                body = self._read_body()
                self._send(200, service.recognize(body, query))
            except ServiceError as err:
                self._error(err)
            except (OSError, TimeoutError):
                self.close_connection = True  # client went away or stalled mid-upload
            except Exception:  # noqa: BLE001 - never leak internals or image data to the client
                self._error(ServiceError(500, "internal_error", "internal error"))

        def _read_body(self) -> bytes:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                raise ServiceError(411, "length_required", "send Content-Length; chunked bodies are not accepted")
            raw = self.headers.get("Content-Length")
            if raw is None or not raw.isdigit():
                raise ServiceError(411, "length_required", "Content-Length is required")
            length = int(raw)
            if length > service.config.max_bytes:
                raise ServiceError(413, "payload_too_large", f"body exceeds {service.config.max_bytes} bytes")
            if length == 0:
                raise ServiceError(400, "empty_body", "empty request body")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ServiceError(400, "short_body", "request body shorter than Content-Length")
            return data

        def log_message(self, fmt: str, *args: object) -> None:
            # Access log without the query string (a whitelist is operator data) or any body.
            sys.stderr.write(f"{self.address_string()} {self.command} {urlsplit(self.path).path} -> {args[1] if len(args) > 1 else ''}\n")

    return Handler


def make_server(config: Config, registry: dict[str, Engine] | None = None) -> ThreadingHTTPServer:
    service = Service(config, registry if registry is not None else build_registry(config))
    server = ThreadingHTTPServer((config.host, config.port), make_handler(service))
    server.daemon_threads = True
    return server


def main() -> int:
    config = Config.from_env()
    server = make_server(config)
    print(f"[pwmcp-ocr] listening on {config.host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
