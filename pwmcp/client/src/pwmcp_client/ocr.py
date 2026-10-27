"""Client for the pwmcp OCR sidecar (PWMCP-11, phase 1).

``recognize`` posts image bytes to the sidecar and returns the text, per-line and per-word
confidence (0-100) and boxes (pixels of the ORIGINAL image). It returns TEXT AND CONFIDENCE,
never a decision: callers decide what a screen means. Standard library only (urllib).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .session import PwmcpError

DEFAULT_ENGINE = "tesseract"
OCR_URL_ENV = "PWMCP_OCR_URL"
# Named in the message so an operator who sees it knows which switch to flip.
NOT_CONFIGURED = (
    "no OCR sidecar URL is known: enable `ocr.enabled` (pwmcp.ocr.enabled in the ciu stack) so the "
    f"server advertises it in /contract, or pass --sidecar-url / set {OCR_URL_ENV}"
)


class OcrError(PwmcpError):
    """An OCR request failed. ``code`` is the sidecar's machine code (or a client-side one)."""

    def __init__(self, message: str, *, code: str = "ocr_error", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class OcrNotConfigured(OcrError):
    def __init__(self, message: str = NOT_CONFIGURED) -> None:
        super().__init__(message, code="not_configured")


@dataclass(frozen=True)
class OcrWord:
    text: str
    conf: float
    box: tuple[int, int, int, int]  # x, y, w, h
    line: int


@dataclass(frozen=True)
class OcrLine:
    text: str
    conf: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class OcrResult:
    engine: str
    engine_version: str | None
    lang: str
    psm: int
    width: int
    height: int
    elapsed_ms: int
    text: str
    lines: tuple[OcrLine, ...]
    words: tuple[OcrWord, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OcrResult":
        try:
            return cls(
                engine=str(value["engine"]),
                engine_version=None if value.get("engine_version") is None else str(value["engine_version"]),
                lang=str(value["lang"]),
                psm=int(value["psm"]),
                width=int(value["width"]),
                height=int(value["height"]),
                elapsed_ms=int(value["elapsed_ms"]),
                text=str(value["text"]),
                lines=tuple(
                    OcrLine(str(row["text"]), float(row["conf"]), _box(row["box"])) for row in value["lines"]
                ),
                words=tuple(
                    OcrWord(str(row["text"]), float(row["conf"]), _box(row["box"]), int(row["line"]))
                    for row in value["words"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OcrError(f"the OCR sidecar returned an unexpected response ({type(exc).__name__}: {exc})",
                           code="bad_response") from exc

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["lines"] = [asdict(line) for line in self.lines]
        data["words"] = [asdict(word) for word in self.words]
        for row in (*data["lines"], *data["words"]):
            row["box"] = list(row["box"])
        return data

    def to_tsv(self) -> str:
        """Tab-separated words: ``line x y w h conf text`` with a header row."""

        rows = ["line\tx\ty\tw\th\tconf\ttext"]
        for word in self.words:
            text = " ".join(word.text.split())
            rows.append("\t".join(map(str, (word.line, *word.box, f"{word.conf:g}", text))))
        return "\n".join(rows)


def _box(value: Any) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in value)
    return x, y, w, h


def resolve_sidecar_url(explicit: str | None = None, *, contract: object | None = None) -> str:
    """The sidecar base URL: explicit value, else ``PWMCP_OCR_URL``, else the contract's ``ocr_url``."""

    url = explicit or os.environ.get(OCR_URL_ENV) or str(getattr(contract, "ocr_url", "") or "")
    if not url:
        raise OcrNotConfigured()
    shown = _redact(url)
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        has_userinfo = bool(parsed.username or parsed.password or "@" in parsed.netloc)
    except ValueError:
        raise OcrError(f"OCR sidecar URL is malformed: {shown}", code="bad_url") from None
    if has_userinfo:
        raise OcrError(
            f"OCR sidecar URL must not contain credentials (userinfo): {shown}", code="bad_url"
        )
    if parsed.scheme not in {"http", "https"} or not host:
        raise OcrError(f"OCR sidecar URL must be an absolute http(s) URL, got {shown}", code="bad_url")
    return url.rstrip("/")


def _redact(url: str) -> str:
    """The URL without userinfo, query or fragment: safe to put in a message."""

    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}{parts.path}" if host else f"{parts.scheme}:<no host>"
    except ValueError:
        return "<malformed url>"


def _content_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "application/octet-stream"


# Internal sidecar traffic must never be routed through an HTTP(S)_PROXY from the environment.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an error: the image must never be re-POSTed to another host."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:  # type: ignore[override]
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def recognize(
    image_bytes: bytes,
    *,
    sidecar_url: str | None = None,
    engine: str = DEFAULT_ENGINE,
    lang: str = "eng",
    psm: int = 6,
    scale: int = 1,
    whitelist: str | None = None,
    min_conf: float = 0.0,
    timeout: float = 30.0,
) -> OcrResult:
    """Recognise text in a PNG/JPEG via the OCR sidecar.

    ``sidecar_url`` defaults to ``$PWMCP_OCR_URL``; resolve it from the contract with
    :func:`resolve_sidecar_url` when that is not set. Raises :class:`OcrNotConfigured` when
    no URL is known and :class:`OcrError` (with the sidecar's ``code`` and HTTP ``status``)
    on any failure; it never returns a partial result.
    """

    if not image_bytes:
        raise OcrError("the image is empty", code="empty_image")
    base = resolve_sidecar_url(sidecar_url)
    query: dict[str, str] = {
        "engine": engine,
        "lang": lang,
        "psm": str(psm),
        "scale": str(scale),
        "min_conf": f"{min_conf:g}",
    }
    if whitelist is not None:
        query["whitelist"] = whitelist
    request = urllib.request.Request(
        f"{base}/v1/ocr?{urllib.parse.urlencode(query)}",
        data=image_bytes,
        method="POST",
        headers={"Content-Type": _content_type(image_bytes), "User-Agent": "pwmcp-client-ocr/0.1"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, _RESET):
            raise _rejected(base) from exc
        raise OcrError(f"the OCR sidecar at {_redact(base)} is unreachable: {exc.reason}", code="unreachable") from exc
    except _RESET as exc:
        raise _rejected(base) from exc
    except OSError as exc:
        raise OcrError(f"the OCR sidecar at {_redact(base)} is unreachable: {exc}", code="unreachable") from exc
    except ValueError as exc:
        raise OcrError("the OCR sidecar returned a non-JSON response", code="bad_response") from exc
    if not isinstance(payload, dict):
        raise OcrError("the OCR sidecar returned a non-object response", code="bad_response")
    return OcrResult.from_dict(payload)


# A reset or broken pipe AFTER connecting means the sidecar hung up while the image was being
# sent (busy, or it rejected the request early); that is not "unreachable".
_RESET = (ConnectionResetError, BrokenPipeError)


def _rejected(base: str) -> OcrError:
    return OcrError(
        f"the OCR sidecar at {_redact(base)} closed the connection while the image was being sent: it is "
        "busy or rejected the request (limits: body size, one recognition at a time); retry shortly",
        code="busy_or_rejected",
    )


def _http_error(exc: urllib.error.HTTPError) -> OcrError:
    if 300 <= exc.code < 400:
        return OcrError(
            f"the OCR sidecar answered with a redirect ({exc.code}); redirects are never followed",
            code="redirect", status=exc.code,
        )
    code, message = "http_error", f"HTTP {exc.code}"
    try:
        body = json.loads(exc.read(65536).decode("utf-8", "replace"))
        if isinstance(body, dict):
            code = str(body.get("code") or code)
            message = str(body.get("error") or message)
    except ValueError:
        pass
    return OcrError(f"the OCR sidecar refused the request ({exc.code} {code}): {message}", code=code, status=exc.code)
