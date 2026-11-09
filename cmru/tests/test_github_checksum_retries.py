"""Bounded transient retries for GitHub release checksum sidecars."""
from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

from cmru.hosts.github import GitHubReleaseHost


SIDECAR_URL = "https://example.invalid/tool.whl.sha256"
ASSET_NAME = "tool.whl"
TAG = "tool-v1.0.0"
DIGEST = "a" * 64


class _Response:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._content


class _FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, *, timeout: int) -> _Response:
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Response)
        return outcome


def _host(opener: _FakeOpener, sleeps: list[float]) -> GitHubReleaseHost:
    host = GitHubReleaseHost(
        "owner", "repo", "token", _opener=opener, _sleep=sleeps.append,
    )
    host._gh.list_releases = lambda: [{
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": ASSET_NAME, "browser_download_url": "https://example.invalid/tool.whl"},
            {"name": f"{ASSET_NAME}.sha256", "browser_download_url": SIDECAR_URL},
        ],
    }]
    return host


def _http_error(status: int) -> HTTPError:
    reasons = {404: "Not Found", 429: "Too Many Requests", 504: "Gateway Timeout"}
    return HTTPError(SIDECAR_URL, status, reasons.get(status, "Server Error"), None, None)


def _read_error(host: GitHubReleaseHost) -> RuntimeError:
    with pytest.raises(RuntimeError) as caught:
        host.resolve_latest("tool-v", asset_suffix=".whl")
    return caught.value


def test_504_retries_then_reads_the_checksum_with_the_configured_backoff():
    opener = _FakeOpener([_http_error(504), _Response(f"{DIGEST}  {ASSET_NAME}\n".encode())])
    sleeps: list[float] = []

    resolved = _host(opener, sleeps).resolve_latest("tool-v", asset_suffix=".whl")

    assert resolved is not None
    assert resolved["sha256"] == DIGEST
    assert opener.calls == [(SIDECAR_URL, 10), (SIDECAR_URL, 10)]
    assert sleeps == [2]


def test_three_504s_raise_the_original_final_error_with_both_backoff_delays():
    errors = [_http_error(504), _http_error(504), _http_error(504)]
    opener = _FakeOpener(errors)
    sleeps: list[float] = []

    failure = _read_error(_host(opener, sleeps))

    expected = (
        f"cannot read the checksum sidecar {ASSET_NAME}.sha256 of {TAG}: {errors[-1]}"
    )
    assert str(failure) == expected
    assert failure.__cause__ is errors[-1]
    assert len(opener.calls) == 3
    assert sleeps == [2, 5]


def test_404_fails_without_retrying():
    error = _http_error(404)
    opener = _FakeOpener([error, _Response(f"{DIGEST}\n".encode())])
    sleeps: list[float] = []

    failure = _read_error(_host(opener, sleeps))

    assert str(failure) == f"cannot read the checksum sidecar {ASSET_NAME}.sha256 of {TAG}: {error}"
    assert failure.__cause__ is error
    assert len(opener.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("error", [
    _http_error(429),
    URLError("temporary connection failure"),
    TimeoutError("read timed out"),
])
def test_other_transient_failures_retry_once_then_succeed(error: Exception):
    opener = _FakeOpener([error, _Response(f"{DIGEST}\n".encode())])
    sleeps: list[float] = []

    resolved = _host(opener, sleeps).resolve_latest("tool-v", asset_suffix=".whl")

    assert resolved is not None
    assert resolved["sha256"] == DIGEST
    assert len(opener.calls) == 2
    assert sleeps == [2]


def test_invalid_digest_content_fails_without_retrying():
    opener = _FakeOpener([
        _Response(b"not-a-sha256-digest  tool.whl\n"),
        _Response(f"{DIGEST}\n".encode()),
    ])
    sleeps: list[float] = []

    failure = _read_error(_host(opener, sleeps))

    assert "does not start with a SHA-256 hex digest" in str(failure)
    assert len(opener.calls) == 1
    assert sleeps == []
