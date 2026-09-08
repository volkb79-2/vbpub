"""Shared fixtures for scripts/telegram's pytest suite."""
from __future__ import annotations

import io
import urllib.error


class FakeHTTPResponse:
    """Minimal stand-in for urllib.request's response context manager."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_http_error(status: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.telegram.org/x", status, "error", None, io.BytesIO(body))
