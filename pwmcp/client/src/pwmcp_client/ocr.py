"""Client for the pwmcp OCR sidecar (PWMCP-11, phase 1).

``recognize`` posts image bytes to the sidecar and returns the text, per-line and per-word
confidence (0-100) and boxes (pixels of the ORIGINAL image). It returns TEXT AND CONFIDENCE,
never a decision: callers decide what a screen means. The result is COMPLETE: every recognised
word is present whatever its confidence; ``min_conf`` only FLAGS words below it (``low_conf``)
unless the caller explicitly asks for ``drop_low_conf``. Standard library only (http.client).

READ THIS BEFORE ACTING ON A RESULT. Tesseract confidence is NOT calibrated: a high confidence does
not mean the text is right (a garbled TrueNAS dialog was read at a mean confidence of 91%, and
digits were misread: 10.0.2.15 as 16.0.2.15, 9) as 3)). ``low_conf`` is a hint, never a safety
check. Without a ``min_conf`` nothing was assessed: ``low_conf`` / ``low_conf_words`` /
``min_conf_applied`` are ``None`` (NOT False/0, which would read as "confident"). Missing or
fuzzy-matched text means UNKNOWN. Anything that will trigger a keystroke later needs an EXACT match
of a known screen signature (or the glyph-grid engine), never merely "unflagged".
"""

from __future__ import annotations

import http.client
import json
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any

from .session import PwmcpError

DEFAULT_ENGINE = "tesseract"
OCR_URL_ENV = "PWMCP_OCR_URL"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
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
    conf: float | None  # None: returned by the engine without a confidence ("unscored")
    box: tuple[int, int, int, int]  # x, y, w, h
    line: int
    low_conf: bool | None = None  # below the requested min_conf; None = no threshold was given (not assessed)


@dataclass(frozen=True)
class OcrLine:
    text: str
    conf: float | None  # alias of conf_mean (kept for compatibility)
    box: tuple[int, int, int, int]
    conf_mean: float | None = None
    conf_min: float | None = None  # the weakest word on the line: a high mean can hide a doubtful token
    low_conf_words: int | None = None  # None = no threshold was given


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
    low_conf_words: int | None = None  # words below min_conf over the whole frame; None = no threshold given
    min_conf_applied: float | None = None
    drop_low_conf: bool = False
    dropped_words: int = 0
    unscored_words: int = 0  # words the engine returned without any confidence (kept, counted)
    # True when the sidecar predates the completeness contract (its answer lacks ``min_conf_applied``):
    # it may have silently DROPPED words below a min_conf, so the result cannot be called complete.
    completeness_unknown: bool = False

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
                lines=tuple(_line(row) for row in value["lines"]),
                words=tuple(
                    OcrWord(str(row["text"]), _opt(row["conf"], float), _box(row["box"]), int(row["line"]),
                            _opt(row.get("low_conf"), bool))
                    for row in value["words"]
                ),
                low_conf_words=_opt(value.get("low_conf_words"), int),
                min_conf_applied=_opt(value.get("min_conf_applied"), float),
                drop_low_conf=bool(value.get("drop_low_conf", False)),
                dropped_words=int(value.get("dropped_words", 0)),
                unscored_words=int(value.get("unscored_words", 0)),
                completeness_unknown="min_conf_applied" not in value,
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
        """Tab-separated words: ``line x y w h conf low_conf text`` with a header row.

        ``low_conf`` is 1/0, or EMPTY when no min_conf threshold was given (not assessed, not "0");
        ``conf`` is empty for an unscored word.
        """

        rows = ["line\tx\ty\tw\th\tconf\tlow_conf\ttext"]
        for word in self.words:
            text = " ".join(word.text.split())
            conf = "" if word.conf is None else f"{word.conf:g}"
            flag = "" if word.low_conf is None else str(int(word.low_conf))  # empty = not assessed
            rows.append("\t".join(map(str, (word.line, *word.box, conf, flag, text))))
        return "\n".join(rows)


def _opt(value: Any, kind: Any) -> Any:
    return None if value is None else kind(value)


def _box(value: Any) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in value)
    return x, y, w, h


def _line(row: dict[str, Any]) -> OcrLine:
    conf = _opt(row["conf"], float)
    return OcrLine(
        str(row["text"]), conf, _box(row["box"]),
        conf_mean=_opt(row.get("conf_mean", conf), float),
        conf_min=_opt(row.get("conf_min", conf), float),
        low_conf_words=_opt(row.get("low_conf_words"), int),
    )


def resolve_sidecar_url(explicit: str | None = None, *, contract: object | None = None) -> str:
    """The sidecar base URL: explicit value, else ``PWMCP_OCR_URL``, else the contract's ``ocr_url``."""

    url = explicit or os.environ.get(OCR_URL_ENV) or str(getattr(contract, "ocr_url", "") or "")
    if not url:
        raise OcrNotConfigured()
    shown = _redact(url)
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        _ = parsed.port  # raises ValueError for a non-numeric or out-of-range port
        has_userinfo = bool(parsed.username or parsed.password or "@" in parsed.netloc)
    except ValueError:
        raise OcrError(f"OCR sidecar URL is malformed (bad host or port): {shown}", code="bad_url") from None
    if has_userinfo:
        raise OcrError(
            f"OCR sidecar URL must not contain credentials (userinfo): {shown}", code="bad_url"
        )
    if parsed.scheme not in {"http", "https"} or not host:
        raise OcrError(f"OCR sidecar URL must be an absolute http(s) URL, got {shown}", code="bad_url")
    if "?" in url or "#" in url:
        raise OcrError(f"OCR sidecar URL must be a base URL without a query string or fragment: {shown}", code="bad_url")
    if re.search(r"[\s\x00-\x1f\x7f]", url):
        raise OcrError(f"OCR sidecar URL must not contain spaces or control characters: {shown!r}", code="bad_url")
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


# A reset or broken pipe WHILE the image is being sent means the sidecar hung up early: busy, or it
# rejected the request (size limit, one recognition at a time). It says nothing after a full upload.
_RESET = (ConnectionResetError, BrokenPipeError)


def _post(base: str, query: str, body: bytes, content_type: str, timeout: float) -> tuple[int, bytes]:
    """POST ``body`` and return (status, response bytes), classifying failures by PHASE.

    http.client is used directly (not urllib): it never consults proxy environment variables for
    this internal service, never follows a redirect (a 3xx is returned as-is and treated as an
    error, so the image cannot be re-POSTed to another host), and shows whether a failure happened
    while connecting, while uploading or while waiting for the answer.
    """

    parts = urllib.parse.urlsplit(base)
    factory = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = factory(parts.hostname, parts.port, timeout=timeout)  # type: ignore[arg-type]
    shown = _redact(base)
    phase = "connect"
    try:
        conn.connect()
        phase = "upload"
        conn.putrequest("POST", f"{parts.path.rstrip('/')}/v1/ocr?{query}", skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", parts.netloc.rpartition("@")[2])
        conn.putheader("Content-Type", content_type)
        conn.putheader("Content-Length", str(len(body)))
        conn.putheader("User-Agent", "pwmcp-client-ocr/0.1")
        conn.endheaders(body)
        phase = "response"
        response = conn.getresponse()
        if response.length is not None and response.length > MAX_RESPONSE_BYTES:
            raise OcrError("the OCR sidecar returned an oversized response", code="bad_response")
        # A declared length is read in full (a cut-short answer raises IncompleteRead on every Python);
        # a close-delimited body is read up to the cap.
        data = response.read(MAX_RESPONSE_BYTES + 1) if response.length is None else response.read()
        if len(data) > MAX_RESPONSE_BYTES:
            raise OcrError("the OCR sidecar returned an oversized response", code="bad_response")
        status = response.status
    except TimeoutError as exc:
        raise OcrError(f"the OCR sidecar at {shown} did not answer in time ({phase} phase, {timeout:g} s)",
                       code="timeout") from exc
    except _RESET as exc:
        if phase == "upload":
            raise OcrError(
                f"the OCR sidecar at {shown} closed the connection while the image was being sent: it is "
                "busy or rejected the request (limits: body size, one recognition at a time); retry shortly",
                code="busy_or_rejected",
            ) from exc
        raise _sidecar_error(shown, phase, exc) from exc
    except http.client.HTTPException as exc:  # RemoteDisconnected, IncompleteRead, malformed reply
        raise _sidecar_error(shown, phase, exc) from exc
    except OSError as exc:
        raise OcrError(f"the OCR sidecar at {shown} is unreachable ({phase} phase): {exc}", code="unreachable") from exc
    finally:
        conn.close()
    return status, data


def _sidecar_error(shown: str, phase: str, exc: BaseException) -> OcrError:
    return OcrError(
        f"the OCR sidecar at {shown} broke the connection during the {phase} phase ({type(exc).__name__}); "
        "the image was received or being answered, so it may have crashed or restarted: check its logs",
        code="sidecar_error",
    )


def recognize(
    image_bytes: bytes,
    *,
    sidecar_url: str | None = None,
    engine: str = DEFAULT_ENGINE,
    lang: str = "eng",
    psm: int = 6,
    scale: int = 1,
    whitelist: str | None = None,
    min_conf: float | None = None,
    drop_low_conf: bool = False,
    timeout: float = 30.0,
) -> OcrResult:
    """Recognise text in a PNG/JPEG via the OCR sidecar.

    ``sidecar_url`` defaults to ``$PWMCP_OCR_URL``; resolve it from the contract with
    :func:`resolve_sidecar_url` when that is not set. The result is complete: with a ``min_conf``,
    words below it are flagged ``low_conf`` (and counted), not removed, unless ``drop_low_conf`` is
    set (which requires a ``min_conf``). Without one nothing is assessed and the flags are ``None``. Raises :class:`OcrNotConfigured` when no URL is known and :class:`OcrError` (with the
    sidecar's ``code`` and HTTP ``status``) on any failure; it never returns a partial result.
    """

    if not image_bytes:
        raise OcrError("the image is empty", code="empty_image")
    base = resolve_sidecar_url(sidecar_url)
    query: dict[str, str] = {
        "engine": engine,
        "lang": lang,
        "psm": str(psm),
        "scale": str(scale),
    }
    if min_conf is not None:
        query["min_conf"] = f"{min_conf:g}"
    if whitelist is not None:
        query["whitelist"] = whitelist
    if drop_low_conf:
        query["drop_low_conf"] = "1"
    status, data = _post(base, urllib.parse.urlencode(query), image_bytes, _content_type(image_bytes), timeout)
    if status != 200:
        raise _http_error(status, data)
    try:
        payload = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise OcrError("the OCR sidecar returned a non-JSON response", code="bad_response") from exc
    if not isinstance(payload, dict):
        raise OcrError("the OCR sidecar returned a non-object response", code="bad_response")
    return OcrResult.from_dict(payload)


def _http_error(status: int, data: bytes) -> OcrError:
    if 300 <= status < 400:
        return OcrError(
            f"the OCR sidecar answered with a redirect ({status}); redirects are never followed",
            code="redirect", status=status,
        )
    code, message = "http_error", f"HTTP {status}"
    try:
        body = json.loads(data.decode("utf-8", "replace"))
        if isinstance(body, dict):
            code = str(body.get("code") or code)
            message = str(body.get("error") or message)
    except ValueError:
        pass
    return OcrError(f"the OCR sidecar refused the request ({status} {code}): {message}", code=code, status=status)
