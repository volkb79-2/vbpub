"""INS-18: `resolve_latest` picks the primary asset by type and never fails open on its sidecar."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import resolve
from cmru.hosts.github import GitHubReleaseHost

DIGEST = "ab12" * 16


def _host(assets):
    host = GitHubReleaseHost("o", "r", "t")
    host._gh = SimpleNamespace(list_releases=lambda: [
        {"tag_name": "demo-v1.0.0", "assets": [
            {"name": n, "browser_download_url": f"https://dl/{n}"} for n in assets]}])
    return host


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_asset_suffix_selects_the_primary_asset_not_api_order(monkeypatch):
    seen = []
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, **kw: seen.append(url) or _Resp(DIGEST.encode() + b"  x\n"))
    host = _host(["notes.txt", "demo-v1.0.0.tar.xz", "demo-v1.0.0.tar.xz.sha256"])
    out = host.resolve_latest("demo-v", asset_suffix=".tar.xz")
    assert out["asset"] == "demo-v1.0.0.tar.xz" and out["sha256"] == DIGEST
    assert seen == ["https://dl/demo-v1.0.0.tar.xz.sha256"]
    assert host.resolve_latest("demo-v", asset_suffix=".whl") is None


def test_sidecar_fetch_failure_is_an_error_not_a_missing_digest(monkeypatch):
    def boom(url, **kw):
        raise OSError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError, match="cannot read the checksum sidecar"):
        _host(["a.tar.xz", "a.tar.xz.sha256"]).resolve_latest("demo-v")


@pytest.mark.parametrize("body", [b"", b"not-a-digest  a.tar.xz\n", b"abc123  a.tar.xz\n"])
def test_malformed_sidecar_is_an_error(monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen", lambda url, **kw: _Resp(body))
    with pytest.raises(RuntimeError, match="SHA-256 hex digest"):
        _host(["a.tar.xz", "a.tar.xz.sha256"]).resolve_latest("demo-v")


def test_resolve_passes_the_suffix_only_when_given():
    calls = []

    class Host:
        def resolve_latest(self, prefix, **kw):
            calls.append(kw)
            return {"tag": "t"}

    resolve.resolve(Host(), "demo-v", use_latest_json=False)
    resolve.resolve(Host(), "demo-v", use_latest_json=False, asset_suffix=".tar.xz")
    assert calls == [{}, {"asset_suffix": ".tar.xz"}]


def _cli(monkeypatch, resolver, installer):
    project = SimpleNamespace(prefix="demo-v", github_token="", installer=installer)
    loaded = (None, {"demo": project}, ["demo"], None, None, None, None, None,
              SimpleNamespace(owner="o", repo="r", token=None), None)
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", lambda **kw: object())
    monkeypatch.setattr(resolve, "resolve", resolver)


def test_cli_reports_a_sidecar_failure_as_exit_1(monkeypatch, capsys):
    def resolver(host, prefix, **kw):
        raise RuntimeError("cannot read the checksum sidecar a.tar.xz.sha256 of t: boom")

    _cli(monkeypatch, resolver, installer=None)
    assert resolve.resolve_main(["demo"]) == 1
    assert "cannot resolve project 'demo'" in capsys.readouterr().err


def test_cli_passes_the_installer_asset_suffix(monkeypatch):
    got = {}

    def resolver(host, prefix, **kw):
        got.update(kw)
        return None

    _cli(monkeypatch, resolver, installer=SimpleNamespace(asset_suffix=".tar.xz"))
    assert resolve.resolve_main(["demo"]) == 1
    assert got["asset_suffix"] == ".tar.xz"
