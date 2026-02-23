#!/usr/bin/env python3
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError, URLError


@dataclass
class GoogleMobileTranslateError(Exception):
    message: str
    kind: str = "service"
    retryable: bool = True
    status_code: int = 0
    body_preview: str = ""

    def __str__(self) -> str:
        if self.status_code:
            return f"{self.message} (http={self.status_code})"
        return self.message


class GoogleMobileTranslate:
    """Lightweight Google mobile translate wrapper.

    Uses https://translate.google.com/m with robust error handling and
    response parsing.
    """

    _RESULT_PATTERNS = [
        r'(?s)class="(?:t0|result-container)">(.*?)<',
        r'(?s)<div[^>]*class="result-container"[^>]*>(.*?)</div>',
    ]

    def __init__(
        self,
        source_language: str = "auto",
        target_language: str = "de",
        timeout: float = 10.0,
        user_agent: str = "Mozilla/5.0",
    ) -> None:
        self.source_language = source_language
        self.target_language = target_language
        self.timeout = float(timeout)
        self.user_agent = user_agent

    @staticmethod
    def _preview(text: str, max_len: int = 240) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:max_len]

    def _build_url(self, text: str, target_language: str, source_language: str) -> str:
        escaped_text = urllib.parse.quote(text.encode("utf-8"))
        return (
            "https://translate.google.com/m?"
            f"tl={target_language}&sl={source_language}&q={escaped_text}"
        )

    def _extract_translation(self, body: str) -> str:
        for pattern in self._RESULT_PATTERNS:
            results = re.findall(pattern, body)
            if results:
                return html.unescape(results[0])
        raise GoogleMobileTranslateError(
            f"google mobile parse failed (body_preview={self._preview(body)!r})",
            kind="service",
            retryable=True,
        )

    def translate(
        self,
        text: str,
        target_language: str = "",
        source_language: str = "",
        timeout: float | str | None = None,
    ) -> str:
        if not target_language:
            target_language = self.target_language
        if not source_language:
            source_language = self.source_language
        if timeout is None or timeout == "":
            timeout_value = self.timeout
        else:
            timeout_value = float(timeout)

        if len(text) > 5000:
            raise GoogleMobileTranslateError(
                f"google mobile supports max 5000 chars per request ({len(text)} given)",
                kind="request",
                retryable=False,
            )

        url = self._build_url(text, target_language, source_language)
        request = urllib.request.Request(url)
        request.add_header("User-Agent", self.user_agent)

        try:
            with urllib.request.urlopen(request, timeout=timeout_value) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            preview = self._preview(body)
            if exc.code == 429:
                raise GoogleMobileTranslateError(
                    f"google mobile rate limit (body_preview={preview!r})",
                    kind="rate_limit",
                    retryable=True,
                    status_code=exc.code,
                    body_preview=preview,
                ) from exc
            if exc.code >= 500:
                raise GoogleMobileTranslateError(
                    f"google mobile server error (body_preview={preview!r})",
                    kind="service",
                    retryable=True,
                    status_code=exc.code,
                    body_preview=preview,
                ) from exc
            raise GoogleMobileTranslateError(
                f"google mobile HTTP error (body_preview={preview!r})",
                kind="request",
                retryable=False,
                status_code=exc.code,
                body_preview=preview,
            ) from exc
        except URLError as exc:
            raise GoogleMobileTranslateError(
                f"google mobile request failed: {exc}",
                kind="service",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise GoogleMobileTranslateError(
                f"google mobile timeout: {exc}",
                kind="service",
                retryable=True,
            ) from exc

        return self._extract_translation(body)
