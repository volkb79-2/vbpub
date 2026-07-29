"""Fail-closed public contracts for CIU's HTTP JSON boundary."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import http_util  # noqa: E402


def test_http_get_json_reports_os_transport_failure_with_the_requested_identity(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket-level failure stays red; CIU must not retry or invent a health result."""
    requests: list[tuple[str, str | None, float]] = []

    def unavailable(request: object, *, timeout: float) -> object:
        requests.append((request.full_url, request.get_header("User-agent"), timeout))
        raise OSError("network namespace unavailable")

    monkeypatch.setattr(http_util.urllib.request, "urlopen", unavailable)

    assert http_util.http_get_json(
        "https://health.internal/status", timeout=4.5, user_agent="CIU-test/1"
    ) == (False, "Error: network namespace unavailable")
    assert requests == [("https://health.internal/status", "CIU-test/1", 4.5)]


def test_http_get_json_rejects_valid_json_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A JSON array is not a health document and cannot turn a gate green."""

    class Response:
        def read(self) -> bytes:
            return b'["healthy"]'

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(http_util.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert http_util.http_get_json("https://health.internal/status") == (
        False,
        "Expected JSON object, got list",
    )
